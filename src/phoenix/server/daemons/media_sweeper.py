from __future__ import annotations

import json
import logging
import random
import re
from asyncio import sleep
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import sqlalchemy as sa

from phoenix.config import get_env_media_orphan_grace_period_hours
from phoenix.db import models
from phoenix.db.types.media import (
    MEDIA_URL_PREFIX,
    HostedMediaRef,
    MediaContent,
    parse_media_url,
)
from phoenix.db.types.media_parts import (
    is_media_content_part,
    media_source,
)
from phoenix.db.types.prompts import (
    PromptChatTemplate,
    PromptTemplate,
)
from phoenix.server.api.helpers.media_storage import media_store
from phoenix.server.types import DaemonTask, DbSessionFactory

logger = logging.getLogger(__name__)

_SLEEP_SECONDS = 60 * 60  # 1 hour
_JITTER_SECONDS = 60  # plus or minus 1 minute
_DELETE_BATCH_SIZE = 100

_HOSTED_DIGEST_PATTERN = re.compile(re.escape(MEDIA_URL_PREFIX) + r"([0-9a-f]{64})")


def referenced_digests(templates: Iterable[PromptTemplate]) -> set[str]:
    """
    Collect the digests of Phoenix-hosted media referenced by prompt templates.

    Args:
        templates: The prompt templates to scan.

    Returns:
        The SHA-256 digests referenced by any media part. Inline ``data:`` media
        contributes nothing, since it is carried in the template itself.
    """
    digests: set[str] = set()
    for template in templates:
        if not isinstance(template, PromptChatTemplate):
            continue
        for message in template.messages:
            if isinstance(message.content, str):
                continue
            for part in message.content:
                if not is_media_content_part(part):
                    continue
                source = media_source(part)
                if not isinstance(source, MediaContent):
                    # Media supplied per run names no stored row, so it protects
                    # none by itself. A dataset example holding the value a run is
                    # given is scanned separately, and a run that has already
                    # happened has stamped the media it used.
                    continue
                try:
                    reference = parse_media_url(source.url)
                except ValueError:
                    # A reference Phoenix cannot parse names no stored row, so it
                    # cannot protect one either. Leave it out rather than fail the
                    # whole sweep.
                    continue
                if isinstance(reference, HostedMediaRef):
                    digests.add(reference.sha256)
    return digests


def digests_in_json(values: Iterable[Any]) -> set[str]:
    """
    Collect Phoenix-hosted media digests appearing anywhere in JSON values.

    Deliberately a regular expression over the serialized value rather than the typed
    walk :func:`referenced_digests` performs. A dataset example's shape is whatever
    the user uploaded, so there is no schema to walk, and the two failure directions
    are not symmetric: this set only ever *protects* media, so matching something
    that is not really a reference costs a row that could have been reclaimed, while
    missing a real one deletes media a run still needs.

    Args:
        values: JSON-serializable values to scan.

    Returns:
        Every SHA-256 digest named by a ``phoenix://media/`` reference within them.
    """
    digests: set[str] = set()
    for value in values:
        if value is None:
            continue
        # `default=str` so an unexpected value cannot fail the whole sweep.
        digests.update(_HOSTED_DIGEST_PATTERN.findall(json.dumps(value, default=str)))
    return digests


class MediaSweeper(DaemonTask):
    """
    Periodically deletes stored media that nothing references.

    Deletes the bytes from the media store as well as the ``media_files`` row. Leaving
    the bytes behind would move the orphan problem into a bucket nobody watches, where
    it would be invisible — the database would look clean.

    This is also why media deletion is not left to a bucket lifecycle rule: a rule
    cannot know what a prompt version references, so it would eventually delete media a
    live prompt still points at.

    Media is uploaded before whatever references it exists — the playground stores an
    image the moment it is attached, which may be long before the user runs or saves
    anything. Only media older than a grace period is therefore eligible, so that an
    image sitting in an unsaved editor is not swept out from under it.

    A digest is persisted in four places, and being alive in any one of them is
    enough:

    - a prompt version's template, scanned here;
    - an experiment task's template, scanned here, which is where a template lives
      when an experiment was run from a playground prompt that was never saved;
    - a dataset example's input, scanned here, which is how a media variable is
      supplied when an experiment drives the prompt;
    - a span's attributes, which is *not* scanned. Every run that sends media to a
      provider records a reference on a span, and an hourly ``LIKE`` over a cast JSON
      column on the largest table in the database is not affordable. Instead a run
      stamps ``referenced_at`` on the media it resolves (see `mark_media_referenced`)
      and this sweep only ever considers rows that were never stamped.

    The referenced set is recomputed inside the same transaction as the delete. A
    prompt version committed in the window between those two statements could still
    lose its media; the grace period makes that require an editor left open past it.
    The failure mode is a dangling reference, reported by ``resolve_media`` as a
    clear error rather than a corrupt prompt.
    """

    def __init__(self, db: DbSessionFactory) -> None:
        super().__init__()
        self._db = db

    async def _run(self) -> None:
        while self._running:
            try:
                await self._delete_orphaned_media()
            except Exception:
                logger.exception("Failed to sweep orphaned media")
            await sleep(_SLEEP_SECONDS + random.uniform(-_JITTER_SECONDS, _JITTER_SECONDS))

    async def _delete_orphaned_media(self) -> int:
        """
        Delete stored media that is past the grace period and unreferenced.

        Returns:
            The number of media rows deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=get_env_media_orphan_grace_period_hours()
        )
        async with self._db() as session:
            candidates = set(
                (
                    await session.scalars(
                        sa.select(models.MediaFile.sha256).where(
                            models.MediaFile.created_at < cutoff,
                            # Media a run has already used is kept for good. The span
                            # that run wrote holds a reference this sweep cannot see,
                            # so the stamp is the only evidence there is.
                            models.MediaFile.referenced_at.is_(None),
                        )
                    )
                ).all()
            )
            if not candidates:
                return 0

            # Only templates that mention the scheme can hold a reference, so each
            # scan skips the text-only majority instead of deserializing every row.
            prompt_templates = (
                await session.scalars(
                    sa.select(models.PromptVersion.template).where(
                        sa.cast(models.PromptVersion.template, sa.Text).contains(MEDIA_URL_PREFIX)
                    )
                )
            ).all()
            task_templates = (
                await session.scalars(
                    sa.select(models.ExperimentPromptTask.template).where(
                        sa.cast(models.ExperimentPromptTask.template, sa.Text).contains(
                            MEDIA_URL_PREFIX
                        )
                    )
                )
            ).all()
            example_values = (
                await session.execute(
                    sa.select(
                        models.DatasetExampleRevision.input,
                        models.DatasetExampleRevision.output,
                        models.DatasetExampleRevision.metadata_,
                    ).where(
                        sa.or_(
                            sa.cast(models.DatasetExampleRevision.input, sa.Text).contains(
                                MEDIA_URL_PREFIX
                            ),
                            sa.cast(models.DatasetExampleRevision.output, sa.Text).contains(
                                MEDIA_URL_PREFIX
                            ),
                            sa.cast(models.DatasetExampleRevision.metadata_, sa.Text).contains(
                                MEDIA_URL_PREFIX
                            ),
                        )
                    )
                )
            ).all()

            referenced = (
                referenced_digests(prompt_templates)
                | referenced_digests(task_templates)
                | digests_in_json(value for row in example_values for value in row)
            )
            orphans = sorted(candidates - referenced)
            if not orphans:
                return 0

            store = media_store()
            for start in range(0, len(orphans), _DELETE_BATCH_SIZE):
                batch = orphans[start : start + _DELETE_BATCH_SIZE]
                # Bytes first, then the row. The row is what makes the media findable,
                # so deleting it first would strand any bytes whose delete then failed
                # with nothing left pointing at them. This order can leave a row whose
                # bytes are gone, which the next sweep retries.
                await store.delete(batch)
                await session.execute(
                    sa.delete(models.MediaFile).where(models.MediaFile.sha256.in_(batch))
                )

        logger.info(f"Deleted {len(orphans)} orphaned media file(s).")
        return len(orphans)
