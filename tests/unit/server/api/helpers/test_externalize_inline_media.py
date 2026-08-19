"""
Media carried inline on a span must not be copied verbatim into a dataset example.

A provider that will not fetch a URL is sent the bytes, and the span records what
was actually sent — so one Gemini call with a screenshot leaves ``input.value``
holding 315 KB of escaped bytes for a 109 KB image. Copied into a dataset that is
315 KB *per row*, in a column every list view reads.

Storing the bytes and leaving a reference keeps the image while making the row
small. The image must never be dropped to achieve that: this whole feature exists
because media that disappears quietly is the worst failure mode available.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

import base64
import json
from typing import Any

import pytest

from phoenix.db import models
from phoenix.db.insertion.dataset import insert_dataset_example_revision
from phoenix.server.api.helpers.dataset_example_media import (
    MEDIA_URL_KEY,
    externalize_inline_media,
)
from phoenix.server.api.helpers.media import resolve_media
from phoenix.server.types import DbSessionFactory
from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
PNG_DATA_URL = f"data:image/png;base64,{PNG_B64}"
HOSTED_PREFIX = "phoenix://media/"


def gemini_part(payload: str = PNG_B64) -> dict[str, Any]:
    """The inline shape a Gemini request records, which is the one that hurts."""
    return {"inline_data": {"mime_type": "image/png", "data": payload}}


class TestDataUrlsBecomeReferences:
    async def test_a_bare_data_url_is_replaced(self, db: DbSessionFactory) -> None:
        async with db() as session:
            result = await externalize_inline_media(session, PNG_DATA_URL)
        assert isinstance(result, str)
        assert result.startswith(HOSTED_PREFIX)

    async def test_it_is_replaced_wherever_it_is_nested(self, db: DbSessionFactory) -> None:
        example = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Grade this:"},
                        {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                    ],
                }
            ]
        }
        async with db() as session:
            result = await externalize_inline_media(session, example)
        url = result["messages"][0]["content"][1]["image_url"]["url"]
        assert url.startswith(HOSTED_PREFIX)
        # Everything around it is untouched.
        assert result["messages"][0]["content"][0] == {"type": "text", "text": "Grade this:"}

    async def test_the_row_gets_dramatically_smaller(self, db: DbSessionFactory) -> None:
        import json

        big = base64.b64encode(PNG_BYTES + b"\x00" * 200_000).decode()
        example = {"image": f"data:image/png;base64,{big}"}
        before = len(json.dumps(example))
        async with db() as session:
            after = len(json.dumps(await externalize_inline_media(session, example)))
        assert before > 100_000
        assert after < 200


class TestProviderInlinePartsBecomeReferences:
    async def test_the_payload_is_replaced(self, db: DbSessionFactory) -> None:
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part())
        inline = result["inline_data"]
        assert inline[MEDIA_URL_KEY].startswith(HOSTED_PREFIX)

    async def test_no_field_is_left_claiming_to_be_base64(self, db: DbSessionFactory) -> None:
        # Leaving a reference under `data` would make the field lie, and the next
        # reader to decode it would get a confusing failure instead of a clear one.
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part())
        assert "data" not in result["inline_data"]

    async def test_the_declared_type_is_kept(self, db: DbSessionFactory) -> None:
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part())
        assert result["inline_data"]["mime_type"] == "image/png"

    async def test_it_works_inside_a_real_gemini_payload(self, db: DbSessionFactory) -> None:
        payload = {
            "model": "gemini-2.5-flash",
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Which of these applies?"}, gemini_part()],
                }
            ],
        }
        async with db() as session:
            result = await externalize_inline_media(session, payload)
        parts = result["contents"][0]["parts"]
        assert parts[0] == {"text": "Which of these applies?"}
        assert parts[1]["inline_data"][MEDIA_URL_KEY].startswith(HOSTED_PREFIX)


class TestBytesReprPayloads:
    """The shape real Gemini spans actually carry.

    An SDK holds the image as ``bytes`` and something reached for ``str()`` instead
    of an encoder, so the payload arrives as ``b'\\x89PNG\\r\\n...'``. It is not
    base64, and a decoder that only tries base64 silently leaves it alone — which
    would have made this whole fix a no-op on the spans that motivated it. It is
    also worse than base64: escaping every non-printable byte inflates a 109 KB
    screenshot to 315 KB of text.
    """

    async def test_a_bytes_repr_payload_is_stored(self, db: DbSessionFactory) -> None:
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part(repr(PNG_BYTES)))
        assert result["inline_data"][MEDIA_URL_KEY].startswith(HOSTED_PREFIX)

    async def test_the_original_bytes_come_back(self, db: DbSessionFactory) -> None:
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part(repr(PNG_BYTES)))
            reference = result["inline_data"][MEDIA_URL_KEY]
        async with db() as session:
            resolved = await resolve_media(session, [reference])
        assert resolved[reference].content == PNG_BYTES

    async def test_it_agrees_with_the_base64_spelling(self, db: DbSessionFactory) -> None:
        # Same image, two serialisations, one stored object.
        async with db() as session:
            from_repr = await externalize_inline_media(session, gemini_part(repr(PNG_BYTES)))
            from_b64 = await externalize_inline_media(session, gemini_part(PNG_B64))
        assert from_repr["inline_data"][MEDIA_URL_KEY] == from_b64["inline_data"][MEDIA_URL_KEY]

    async def test_a_double_quoted_repr_works_too(self, db: DbSessionFactory) -> None:
        payload = 'b"' + repr(PNG_BYTES)[2:-1].replace("'", "\\'") + '"'
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part(payload))
        assert result["inline_data"][MEDIA_URL_KEY].startswith(HOSTED_PREFIX)

    async def test_a_repr_of_something_that_is_not_media_is_left_alone(
        self, db: DbSessionFactory
    ) -> None:
        async with db() as session:
            part = gemini_part(repr(b"just some bytes, not an image"))
            assert await externalize_inline_media(session, part) == part

    async def test_a_string_literal_repr_is_not_mistaken_for_bytes(
        self, db: DbSessionFactory
    ) -> None:
        # `literal_eval` happily returns a str for "'hello'"; only bytes qualify.
        part = gemini_part("'hello'")
        async with db() as session:
            assert await externalize_inline_media(session, part) == part


class TestUrlSafeBase64Payloads:
    """The alphabet the Google GenAI SDK actually writes.

    ``inline_data.data`` comes back in the URL-safe alphabet (RFC 4648 §5), where
    ``-`` and ``_`` stand for ``+`` and ``/``. Strict ``b64decode`` refuses it, so a
    real ``google-adk`` span's PDF was left inline and unstored — and, on the client,
    dropped from the playground entirely.
    """

    async def test_a_url_safe_payload_is_stored(self, db: DbSessionFactory) -> None:
        payload = base64.urlsafe_b64encode(PNG_BYTES).decode()
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part(payload))
        assert result["inline_data"][MEDIA_URL_KEY].startswith(HOSTED_PREFIX)

    async def test_the_original_bytes_come_back(self, db: DbSessionFactory) -> None:
        payload = base64.urlsafe_b64encode(PNG_BYTES).decode()
        async with db() as session:
            result = await externalize_inline_media(session, gemini_part(payload))
            reference = result["inline_data"][MEDIA_URL_KEY]
        async with db() as session:
            resolved = await resolve_media(session, [reference])
        assert resolved[reference].content == PNG_BYTES

    async def test_it_agrees_with_the_standard_spelling(self, db: DbSessionFactory) -> None:
        # Same image, two alphabets, one stored object.
        async with db() as session:
            url_safe = await externalize_inline_media(
                session, gemini_part(base64.urlsafe_b64encode(PNG_BYTES).decode())
            )
            standard = await externalize_inline_media(session, gemini_part(PNG_B64))
        assert url_safe["inline_data"][MEDIA_URL_KEY] == standard["inline_data"][MEDIA_URL_KEY]


class TestTheWalkDoesNotStopAtAnInlinePart:
    """An inline part is a mapping like any other; its siblings still get walked.

    Returning early once `inline_data` was recognised left a `data:` URL sitting
    beside it untouched, and — worse — skipped the whole mapping whenever the
    payload could not be decoded, so one corrupt blob preserved every other inline
    image in the same part.
    """

    async def test_a_sibling_data_url_is_externalized_too(self, db: DbSessionFactory) -> None:
        part = {**gemini_part(), "thumbnail": PNG_DATA_URL}
        async with db() as session:
            result = await externalize_inline_media(session, part)
        assert result["inline_data"][MEDIA_URL_KEY].startswith(HOSTED_PREFIX)
        assert result["thumbnail"].startswith(HOSTED_PREFIX)

    async def test_a_corrupt_payload_does_not_shield_its_siblings(
        self, db: DbSessionFactory
    ) -> None:
        part = {**gemini_part("!!!! not base64 !!!!"), "thumbnail": PNG_DATA_URL}
        async with db() as session:
            result = await externalize_inline_media(session, part)
        # The undecodable payload is left exactly as it was...
        assert result["inline_data"]["data"] == "!!!! not base64 !!!!"
        # ...but that must not stop the sibling from being stored.
        assert result["thumbnail"].startswith(HOSTED_PREFIX)

    async def test_nested_parts_inside_an_inline_part_are_reached(
        self, db: DbSessionFactory
    ) -> None:
        part = {**gemini_part(), "alternatives": [{"image_url": {"url": PNG_DATA_URL}}]}
        async with db() as session:
            result = await externalize_inline_media(session, part)
        assert result["alternatives"][0]["image_url"]["url"].startswith(HOSTED_PREFIX)


class TestDepthGuard:
    """The walk is bounded, exactly as its TypeScript twin is.

    An example's input is arbitrary JSON from somebody else's instrumentation, and
    every level costs a stack frame. Unbounded, a pathological span becomes a
    `RecursionError` inside the dataset insert — a 500 on the one path whose whole
    premise is that failing to shrink a row beats failing to save the span.
    """

    async def test_a_pathological_structure_does_not_raise(self, db: DbSessionFactory) -> None:
        # 700 is chosen against the measured window, not picked for roundness:
        # `json.loads` parses to roughly 800 levels, while the unguarded walk
        # raises `RecursionError` from about 600. Anything in between is a span
        # Phoenix ingests happily and then crashes on. 400 sat below that window
        # and passed either way, which is no test at all.
        deep: Any = PNG_DATA_URL
        for _ in range(700):
            deep = {"next": deep}
        async with db() as session:
            result = await externalize_inline_media(session, {"root": deep})
        assert result is not None  # returned rather than blowing the stack

    async def test_media_within_the_cap_is_still_externalized(self, db: DbSessionFactory) -> None:
        nested: Any = {"image": PNG_DATA_URL}
        for _ in range(8):
            nested = {"next": nested}
        async with db() as session:
            result = await externalize_inline_media(session, nested)
        found = json.dumps(result)
        assert HOSTED_PREFIX in found and "data:image" not in found

    async def test_media_past_the_cap_is_left_alone_rather_than_crashing(
        self, db: DbSessionFactory
    ) -> None:
        nested: Any = {"image": PNG_DATA_URL}
        for _ in range(30):
            nested = {"next": nested}
        async with db() as session:
            result = await externalize_inline_media(session, nested)
        # Not shrunk, but the row still saves — the trade this module always makes.
        assert "data:image" in json.dumps(result)


class TestTheImageIsNotLost:
    """Shrinking the row must never mean dropping the picture."""

    async def test_the_bytes_are_recoverable_from_the_reference(self, db: DbSessionFactory) -> None:
        async with db() as session:
            reference = await externalize_inline_media(session, PNG_DATA_URL)
        async with db() as session:
            resolved = await resolve_media(session, [reference])
        assert resolved[reference].content == PNG_BYTES
        assert resolved[reference].media_type == "image/png"

    async def test_a_media_row_is_recorded(self, db: DbSessionFactory) -> None:
        async with db() as session:
            reference = await externalize_inline_media(session, PNG_DATA_URL)
        sha256 = reference[len(HOSTED_PREFIX) :]
        async with db() as session:
            row = await session.get(models.MediaFile, sha256)
        # The row is what the sweeper checks and what makes the media servable.
        assert row is not None
        assert row.media_type == "image/png"

    async def test_the_same_image_twice_stores_once(self, db: DbSessionFactory) -> None:
        # Content-addressed, so a dataset where every row carries the same logo
        # costs one row and one object.
        async with db() as session:
            first = await externalize_inline_media(session, PNG_DATA_URL)
            second = await externalize_inline_media(session, gemini_part())
        assert first == second["inline_data"][MEDIA_URL_KEY]


class TestNothingElseIsTouched:
    @pytest.mark.parametrize(
        "value",
        [
            "an ordinary string",
            "phoenix://media/" + "a" * 64,  # already a reference
            "https://example.com/cat.png",
            {"question": "text only", "score": 1},
            [1, 2, 3],
            None,
            42,
        ],
    )
    async def test_values_without_inline_media_are_unchanged(
        self, db: DbSessionFactory, value: Any
    ) -> None:
        async with db() as session:
            assert await externalize_inline_media(session, value) == value

    async def test_an_unrelated_data_url_is_left_alone(self, db: DbSessionFactory) -> None:
        # Not every data URL is media Phoenix stores.
        csv = "data:text/csv;base64,YSxiCjEsMg=="
        async with db() as session:
            assert await externalize_inline_media(session, csv) == csv

    async def test_a_corrupt_payload_is_left_alone_rather_than_raising(
        self, db: DbSessionFactory
    ) -> None:
        # Failing to shrink a row is a far better outcome than failing to save
        # somebody's span.
        broken = gemini_part("!!!! not base64 !!!!")
        async with db() as session:
            assert await externalize_inline_media(session, broken) == broken

    async def test_a_data_url_of_unsupported_media_is_left_alone(
        self, db: DbSessionFactory
    ) -> None:
        # Decodes fine, but the bytes are not a format Phoenix serves.
        zipped = "data:application/zip;base64," + base64.b64encode(b"PK\x03\x04junk").decode()
        async with db() as session:
            assert await externalize_inline_media(session, zipped) == zipped


class TestTheInsertPathActuallyUsesIt:
    """The delegation is wired, not merely available.

    Every other test here calls `externalize_inline_media` directly, which proves
    the helper works and nothing about whether anything calls it. The helper is
    wired into four write paths by eight separate `await`s; a sync that drops one
    of them would leave a row carrying hundreds of kilobytes of base64 again, with
    the whole suite still green. This goes through the insert instead.
    """

    async def _example(self, session: Any) -> tuple[int, int]:
        """A dataset, a version and an example to hang a revision off."""
        dataset = models.Dataset(name="wiring-check", metadata_={})
        session.add(dataset)
        await session.flush()
        version = models.DatasetVersion(dataset_id=dataset.id, metadata_={})
        session.add(version)
        example = models.DatasetExample(dataset_id=dataset.id)
        session.add(example)
        await session.flush()
        return version.id, example.id

    async def test_insert_dataset_example_revision_externalizes(self, db: DbSessionFactory) -> None:
        async with db() as session:
            version_id, example_id = await self._example(session)
            revision_id = await insert_dataset_example_revision(
                session=session,
                dataset_version_id=version_id,
                dataset_example_id=example_id,
                input={"question": "what is this?", "question_image": PNG_DATA_URL},
                output={},
                metadata={},
            )
        async with db() as session:
            revision = await session.get(models.DatasetExampleRevision, revision_id)
        assert revision is not None
        stored = revision.input["question_image"]
        assert stored.startswith(HOSTED_PREFIX), "the insert did not externalize"
        assert "data:image" not in json.dumps(revision.input)
        # The rest of the row is untouched.
        assert revision.input["question"] == "what is this?"

    async def test_output_is_externalized_too(self, db: DbSessionFactory) -> None:
        async with db() as session:
            version_id, example_id = await self._example(session)
            revision_id = await insert_dataset_example_revision(
                session=session,
                dataset_version_id=version_id,
                dataset_example_id=example_id,
                input={},
                output={"rendered": PNG_DATA_URL},
                metadata={},
            )
        async with db() as session:
            revision = await session.get(models.DatasetExampleRevision, revision_id)
        assert revision is not None
        assert revision.output["rendered"].startswith(HOSTED_PREFIX)

    async def test_a_text_only_example_is_stored_verbatim(self, db: DbSessionFactory) -> None:
        # The delegation must not reshape rows that have nothing to do with media.
        row = {"question": "no media here", "note": "data is not a URI"}
        async with db() as session:
            version_id, example_id = await self._example(session)
            revision_id = await insert_dataset_example_revision(
                session=session,
                dataset_version_id=version_id,
                dataset_example_id=example_id,
                input=row,
                output={},
                metadata={},
            )
        async with db() as session:
            revision = await session.get(models.DatasetExampleRevision, revision_id)
        assert revision is not None
        assert revision.input == row
