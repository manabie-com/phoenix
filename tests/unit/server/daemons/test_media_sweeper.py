from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from secrets import token_hex
from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phoenix.db import models
from phoenix.db.types.identifier import Identifier
from phoenix.db.types.media import MediaContent, hosted_media_url
from phoenix.db.types.media_parts import (
    ImageContentPart,
)
from phoenix.db.types.model_provider import ModelProvider
from phoenix.db.types.prompts import (
    PromptChatTemplate,
    PromptMessage,
    PromptOpenAIInvocationParameters,
    PromptOpenAIInvocationParametersContent,
    PromptTemplateFormat,
    PromptTemplateType,
    TextContentPart,
)
from phoenix.server.api.helpers.media_storage import media_store
from phoenix.server.daemons.media_sweeper import (
    MediaSweeper,
    digests_in_json,
    referenced_digests,
)
from phoenix.server.types import DbSessionFactory
from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def _store_media(
    db: DbSessionFactory,
    sha256: str,
    *,
    age: timedelta = timedelta(days=7),
    used: bool = False,
) -> None:
    await media_store().put(sha256, b"png", media_type="image/png")
    async with db() as session:
        session.add(
            models.MediaFile(
                sha256=sha256,
                media_type="image/png",
                size_bytes=3,
                created_at=datetime.now(timezone.utc) - age,
                # A run stamps this, standing in for the span reference the sweep
                # cannot see.
                referenced_at=datetime.now(timezone.utc) if used else None,
            )
        )


def _chat_template_with_image(image_url: str) -> PromptChatTemplate:
    return PromptChatTemplate(
        type="chat",
        messages=[
            PromptMessage(
                role="user",
                content=[
                    TextContentPart(type="text", text="describe this"),
                    ImageContentPart(
                        type="image",
                        image=MediaContent(url=image_url, media_type="image/png"),
                    ),
                ],
            )
        ],
    )


async def _dataset_and_version(session: AsyncSession) -> tuple[int, int]:
    """
    A dataset and one version of it, for the artifacts that need both.

    The name is randomised because a single test may need more than one dataset and
    `datasets.name` is unique.
    """
    dataset = models.Dataset(name=f"media-{token_hex(8)}", metadata_={})
    session.add(dataset)
    await session.flush()
    version = models.DatasetVersion(dataset_id=dataset.id, description=None, metadata_={})
    session.add(version)
    await session.flush()
    return dataset.id, version.id


async def _store_experiment_task_with_image(db: DbSessionFactory, image_url: str) -> None:
    """
    An experiment whose prompt was never saved as a prompt version.

    This is what the playground writes when an experiment runs against an unsaved
    prompt: the template, media parts and all, lands in `experiment_prompt_tasks`
    and `prompt_version_id` stays NULL.
    """
    async with db() as session:
        dataset_id, version_id = await _dataset_and_version(session)
        experiment = models.Experiment(
            dataset_id=dataset_id,
            dataset_version_id=version_id,
            name="media-experiment",
            repetitions=1,
            metadata_={},
        )
        session.add(experiment)
        await session.flush()
        session.add(
            models.ExperimentPromptTask(
                id=experiment.id,
                model_provider=ModelProvider.OPENAI,
                model_name="gpt-4o",
                template_type=PromptTemplateType.CHAT,
                template_format=PromptTemplateFormat.MUSTACHE,
                template=_chat_template_with_image(image_url),
                invocation_parameters=PromptOpenAIInvocationParameters(
                    type="openai",
                    openai=PromptOpenAIInvocationParametersContent(),
                ),
            )
        )


async def _store_dataset_example_with_media(db: DbSessionFactory, image_url: str) -> None:
    """
    A dataset example supplying a media variable's value.

    A prompt with a media *variable* takes its reference from the run's template
    variables, which for an experiment come from the dataset example's input.
    """
    async with db() as session:
        dataset_id, version_id = await _dataset_and_version(session)
        example = models.DatasetExample(dataset_id=dataset_id)
        session.add(example)
        await session.flush()
        session.add(
            models.DatasetExampleRevision(
                dataset_example_id=example.id,
                dataset_version_id=version_id,
                input={"question": "what is this?", "picture": image_url},
                output={},
                metadata_={},
                revision_kind="CREATE",
            )
        )


async def _store_prompt_with_image(
    db: DbSessionFactory,
    name: str,
    image_url: Optional[str],
) -> None:
    content: list[TextContentPart | ImageContentPart] = [
        TextContentPart(type="text", text="describe this")
    ]
    if image_url is not None:
        content.append(
            ImageContentPart(
                type="image",
                image=MediaContent(url=image_url, media_type="image/png"),
            )
        )
    async with db() as session:
        prompt = models.Prompt(name=Identifier(root=name), metadata_={})
        session.add(prompt)
        await session.flush()
        session.add(
            models.PromptVersion(
                prompt_id=prompt.id,
                template_type=PromptTemplateType.CHAT,
                template_format=PromptTemplateFormat.MUSTACHE,
                template=PromptChatTemplate(
                    type="chat",
                    messages=[PromptMessage(role="user", content=content)],
                ),
                invocation_parameters=PromptOpenAIInvocationParameters(
                    type="openai",
                    openai=PromptOpenAIInvocationParametersContent(),
                ),
                model_provider=ModelProvider.OPENAI,
                model_name="gpt-4o",
                metadata_={},
            )
        )


async def _stored_digests(db: DbSessionFactory) -> set[str]:
    async with db() as session:
        return set((await session.scalars(select(models.MediaFile.sha256))).all())


class TestReferencedDigests:
    def test_collects_hosted_references(self) -> None:
        digest = _digest("a")
        template = PromptChatTemplate(
            type="chat",
            messages=[
                PromptMessage(
                    role="user",
                    content=[
                        TextContentPart(type="text", text="hi"),
                        ImageContentPart(
                            type="image",
                            image=MediaContent(
                                url=hosted_media_url(digest), media_type="image/png"
                            ),
                        ),
                    ],
                )
            ],
        )
        assert referenced_digests([template]) == {digest}

    def test_ignores_inline_media(self) -> None:
        template = PromptChatTemplate(
            type="chat",
            messages=[
                PromptMessage(
                    role="user",
                    content=[
                        ImageContentPart(
                            type="image",
                            image=MediaContent(
                                url="data:image/png;base64,aGk=", media_type="image/png"
                            ),
                        )
                    ],
                )
            ],
        )
        assert referenced_digests([template]) == set()

    def test_handles_text_only_and_string_content(self) -> None:
        templates = [
            PromptChatTemplate(
                type="chat",
                messages=[
                    PromptMessage(role="user", content=[TextContentPart(type="text", text="hi")]),
                    PromptMessage(role="system", content="you are helpful"),
                ],
            )
        ]
        assert referenced_digests(templates) == set()


class TestDigestsInJson:
    def test_finds_a_reference_anywhere_in_the_value(self) -> None:
        digest = _digest("nested")
        value = {
            "question": "what is this?",
            "attachments": [{"picture": hosted_media_url(digest)}],
        }
        assert digests_in_json([value]) == {digest}

    def test_ignores_values_carrying_no_reference(self) -> None:
        assert digests_in_json([{"question": "hi"}, None, {}]) == set()

    def test_ignores_a_prefix_without_a_valid_digest(self) -> None:
        assert digests_in_json([{"picture": "phoenix://media/not-a-digest"}]) == set()

    def test_survives_a_value_json_cannot_serialize(self) -> None:
        """One odd value must not fail the whole sweep."""
        digest = _digest("alongside")
        assert digests_in_json([{"when": object(), "picture": hosted_media_url(digest)}]) == {
            digest
        }


class TestMediaSweeper:
    async def test_deletes_unreferenced_media(self, db: DbSessionFactory) -> None:
        orphan = _digest("orphan")
        await _store_media(db, orphan)

        deleted = await MediaSweeper(db)._delete_orphaned_media()

        assert deleted == 1
        assert await _stored_digests(db) == set()

    async def test_keeps_referenced_media(self, db: DbSessionFactory) -> None:
        referenced = _digest("referenced")
        await _store_media(db, referenced)
        await _store_prompt_with_image(db, "keeper", hosted_media_url(referenced))

        deleted = await MediaSweeper(db)._delete_orphaned_media()

        assert deleted == 0
        assert await _stored_digests(db) == {referenced}

    async def test_keeps_media_inside_the_grace_period(self, db: DbSessionFactory) -> None:
        """The playground uploads before the prompt is saved, so fresh media is safe."""
        fresh = _digest("fresh")
        await _store_media(db, fresh, age=timedelta(minutes=5))

        deleted = await MediaSweeper(db)._delete_orphaned_media()

        assert deleted == 0
        assert await _stored_digests(db) == {fresh}

    async def test_respects_a_configured_grace_period(
        self,
        db: DbSessionFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        aged = _digest("aged")
        await _store_media(db, aged, age=timedelta(hours=48))
        monkeypatch.setenv("PHOENIX_MEDIA_ORPHAN_GRACE_PERIOD_HOURS", "72")

        assert await MediaSweeper(db)._delete_orphaned_media() == 0
        assert await _stored_digests(db) == {aged}

        monkeypatch.setenv("PHOENIX_MEDIA_ORPHAN_GRACE_PERIOD_HOURS", "24")

        assert await MediaSweeper(db)._delete_orphaned_media() == 1
        assert await _stored_digests(db) == set()

    async def test_sweeps_only_the_unreferenced_rows(self, db: DbSessionFactory) -> None:
        keep, drop = _digest("keep"), _digest("drop")
        await _store_media(db, keep)
        await _store_media(db, drop)
        await _store_prompt_with_image(db, "keeper", hosted_media_url(keep))

        deleted = await MediaSweeper(db)._delete_orphaned_media()

        assert deleted == 1
        assert await _stored_digests(db) == {keep}

    async def test_deletes_media_once_its_last_prompt_is_gone(
        self,
        db: DbSessionFactory,
    ) -> None:
        digest = _digest("abandoned")
        await _store_media(db, digest)
        await _store_prompt_with_image(db, "doomed", hosted_media_url(digest))

        assert await MediaSweeper(db)._delete_orphaned_media() == 0

        async with db() as session:
            prompt = await session.scalar(select(models.Prompt))
            assert prompt is not None
            await session.delete(prompt)

        assert await MediaSweeper(db)._delete_orphaned_media() == 1
        assert await _stored_digests(db) == set()

    async def test_is_a_noop_with_no_media(self, db: DbSessionFactory) -> None:
        assert await MediaSweeper(db)._delete_orphaned_media() == 0

    async def test_ignores_prompts_that_carry_no_media(self, db: DbSessionFactory) -> None:
        orphan = _digest("orphan")
        await _store_media(db, orphan)
        await _store_prompt_with_image(db, "text-only", None)

        assert await MediaSweeper(db)._delete_orphaned_media() == 1

    async def test_deletes_more_rows_than_one_batch(self, db: DbSessionFactory) -> None:
        digests = {_digest(f"bulk-{index}") for index in range(150)}
        for digest in digests:
            await _store_media(db, digest)

        assert await MediaSweeper(db)._delete_orphaned_media() == 150
        assert await _stored_digests(db) == set()

    async def test_deletes_the_bytes_not_only_the_row(self, db: DbSessionFactory) -> None:
        """
        Leaving the bytes behind would move the orphan problem into a bucket nobody
        watches, where it would be invisible — the database would look clean.
        """
        orphan = _digest("orphan-with-bytes")
        await _store_media(db, orphan)
        assert await media_store().get(orphan) is not None

        assert await MediaSweeper(db)._delete_orphaned_media() == 1

        assert await _stored_digests(db) == set()
        assert await media_store().get(orphan) is None

    async def test_keeps_the_bytes_of_media_it_keeps(self, db: DbSessionFactory) -> None:
        keep, drop = _digest("keep-bytes"), _digest("drop-bytes")
        await _store_media(db, keep)
        await _store_media(db, drop)
        await _store_prompt_with_image(db, "keeper", hosted_media_url(keep))

        assert await MediaSweeper(db)._delete_orphaned_media() == 1

        assert await media_store().get(keep) is not None
        assert await media_store().get(drop) is None

    async def test_keeps_media_a_run_has_used(self, db: DbSessionFactory) -> None:
        """
        The regression this column exists for.

        A playground run records `phoenix://media/<sha256>` on a span and the span is
        never scanned, so without the stamp this row looks abandoned and the trace
        loses its image permanently.
        """
        used = _digest("used-by-a-run")
        await _store_media(db, used, used=True)

        assert await MediaSweeper(db)._delete_orphaned_media() == 0
        assert await _stored_digests(db) == {used}

    async def test_keeps_media_referenced_by_an_experiment_task(
        self,
        db: DbSessionFactory,
    ) -> None:
        """An experiment from an unsaved prompt keeps its template outside prompt_versions."""
        referenced = _digest("experiment-task")
        await _store_media(db, referenced)
        await _store_experiment_task_with_image(db, hosted_media_url(referenced))

        assert await MediaSweeper(db)._delete_orphaned_media() == 0
        assert await _stored_digests(db) == {referenced}

    async def test_keeps_media_referenced_by_a_dataset_example(
        self,
        db: DbSessionFactory,
    ) -> None:
        """A media variable's value lives in the dataset example, not in the template."""
        referenced = _digest("dataset-example")
        await _store_media(db, referenced)
        await _store_dataset_example_with_media(db, hosted_media_url(referenced))

        assert await MediaSweeper(db)._delete_orphaned_media() == 0
        assert await _stored_digests(db) == {referenced}

    async def test_still_reclaims_media_that_was_never_used(
        self,
        db: DbSessionFactory,
    ) -> None:
        """
        The sweeper must not become a no-op.

        Media attached in the playground and then abandoned is the case it was built
        for, and widening the live set must not cost that.
        """
        abandoned = _digest("never-used")
        kept_by_run = _digest("used")
        kept_by_task = _digest("in-a-task")
        kept_by_example = _digest("in-an-example")
        await _store_media(db, abandoned)
        await _store_media(db, kept_by_run, used=True)
        await _store_media(db, kept_by_task)
        await _store_media(db, kept_by_example)
        await _store_experiment_task_with_image(db, hosted_media_url(kept_by_task))
        await _store_dataset_example_with_media(db, hosted_media_url(kept_by_example))

        assert await MediaSweeper(db)._delete_orphaned_media() == 1
        assert await _stored_digests(db) == {kept_by_run, kept_by_task, kept_by_example}
