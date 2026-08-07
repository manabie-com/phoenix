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
from typing import Any

import pytest

from phoenix.db import models
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
