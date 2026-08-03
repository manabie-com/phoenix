from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Optional

import pytest
from sqlalchemy import select

from phoenix.db import models
from phoenix.db.types.media import hosted_media_url
from phoenix.server.api.helpers.media import (
    MediaResolutionError,
    hosted_digests,
    mark_media_referenced,
    resolve_media,
)
from phoenix.server.api.helpers.media_storage import media_store
from phoenix.server.types import DbSessionFactory
from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
_PNG_DIGEST = hashlib.sha256(_PNG_BYTES).hexdigest()
_JPEG_BYTES = b"\xff\xd8\xff\xe0 pretend jpeg"
_JPEG_DIGEST = hashlib.sha256(_JPEG_BYTES).hexdigest()


@pytest.fixture
async def stored_media(db: DbSessionFactory) -> None:
    """Metadata in the database, bytes in the store — how media is stored now."""
    store = media_store()
    await store.put(_PNG_DIGEST, _PNG_BYTES, media_type="image/png")
    await store.put(_JPEG_DIGEST, _JPEG_BYTES, media_type="image/jpeg")
    async with db() as session:
        session.add(
            models.MediaFile(
                sha256=_PNG_DIGEST,
                media_type="image/png",
                size_bytes=len(_PNG_BYTES),
            )
        )
        session.add(
            models.MediaFile(
                sha256=_JPEG_DIGEST,
                media_type="image/jpeg",
                size_bytes=len(_JPEG_BYTES),
            )
        )


class TestResolveMedia:
    async def test_returns_empty_for_no_input(self, db: DbSessionFactory) -> None:
        async with db() as session:
            assert await resolve_media(session, []) == {}

    async def test_resolves_hosted_media(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        url = hosted_media_url(_PNG_DIGEST)
        async with db() as session:
            resolved = await resolve_media(session, [url])
        assert resolved[url].content == _PNG_BYTES
        assert resolved[url].media_type == "image/png"

    async def test_resolves_inline_media(self, db: DbSessionFactory) -> None:
        url = f"data:image/png;base64,{base64.b64encode(_PNG_BYTES).decode()}"
        async with db() as session:
            resolved = await resolve_media(session, [url])
        assert resolved[url].content == _PNG_BYTES
        assert resolved[url].media_type == "image/png"

    async def test_resolves_hosted_and_inline_together(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        hosted = hosted_media_url(_PNG_DIGEST)
        inline = f"data:image/jpeg;base64,{base64.b64encode(_JPEG_BYTES).decode()}"
        async with db() as session:
            resolved = await resolve_media(session, [hosted, inline])
        assert resolved[hosted].content == _PNG_BYTES
        assert resolved[inline].content == _JPEG_BYTES

    async def test_resolves_repeated_reference_once(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        url = hosted_media_url(_PNG_DIGEST)
        async with db() as session:
            resolved = await resolve_media(session, [url, url, url])
        assert len(resolved) == 1
        assert resolved[url].content == _PNG_BYTES

    async def test_returns_stored_media_type(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        url = hosted_media_url(_JPEG_DIGEST)
        async with db() as session:
            resolved = await resolve_media(session, [url])
        assert resolved[url].media_type == "image/jpeg"

    async def test_raises_when_hosted_media_is_missing(self, db: DbSessionFactory) -> None:
        async with db() as session:
            with pytest.raises(MediaResolutionError, match="no longer stored in Phoenix"):
                await resolve_media(session, [hosted_media_url("b" * 64)])

    async def test_raises_when_some_hosted_media_is_missing(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        async with db() as session:
            with pytest.raises(MediaResolutionError, match="c" * 64):
                await resolve_media(
                    session,
                    [hosted_media_url(_PNG_DIGEST), hosted_media_url("c" * 64)],
                )

    async def test_raises_on_corrupt_inline_payload(self, db: DbSessionFactory) -> None:
        async with db() as session:
            with pytest.raises(MediaResolutionError, match="not valid base64"):
                await resolve_media(session, ["data:image/png;base64,!!!not-base64!!!"])

    async def test_raises_on_unsupported_scheme(self, db: DbSessionFactory) -> None:
        async with db() as session:
            with pytest.raises(MediaResolutionError, match="unsupported media URL scheme"):
                await resolve_media(session, ["https://example.com/cat.png"])


class TestRowAndStoreDisagreeing:
    """
    The row and the bytes can fall out of step, and the run must say so.

    Nothing keeps them in one transaction — an upload writes bytes then a row, the
    sweeper deletes bytes then a row — so each side can outlive the other briefly, or
    permanently if a delete half-failed.
    """

    async def test_reports_a_row_whose_bytes_are_gone(
        self,
        db: DbSessionFactory,
    ) -> None:
        """A clear error, not empty bytes handed to a model."""
        async with db() as session:
            session.add(
                models.MediaFile(
                    sha256=_PNG_DIGEST,
                    media_type="image/png",
                    size_bytes=len(_PNG_BYTES),
                )
            )
        async with db() as session:
            with pytest.raises(MediaResolutionError, match="no longer stored"):
                await resolve_media(session, [hosted_media_url(_PNG_DIGEST)])


class TestHostedDigests:
    def test_keeps_only_hosted_references(self) -> None:
        assert hosted_digests(
            [
                hosted_media_url(_PNG_DIGEST),
                "data:image/png;base64,aGk=",
                "https://example.com/cat.png",
                "not a url at all",
            ]
        ) == {_PNG_DIGEST}

    def test_deduplicates(self) -> None:
        url = hosted_media_url(_PNG_DIGEST)
        assert hosted_digests([url, url]) == {_PNG_DIGEST}


async def _referenced_at(db: DbSessionFactory, sha256: str) -> Optional[datetime]:
    async with db() as session:
        return await session.scalar(
            select(models.MediaFile.referenced_at).where(models.MediaFile.sha256 == sha256)
        )


class TestMarkMediaReferenced:
    async def test_stamps_hosted_media(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        assert await _referenced_at(db, _PNG_DIGEST) is None

        async with db() as session:
            await mark_media_referenced(session, [hosted_media_url(_PNG_DIGEST)])

        assert await _referenced_at(db, _PNG_DIGEST) is not None

    async def test_leaves_other_media_alone(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        async with db() as session:
            await mark_media_referenced(session, [hosted_media_url(_PNG_DIGEST)])

        assert await _referenced_at(db, _JPEG_DIGEST) is None

    async def test_keeps_the_first_stamp(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        """
        Re-running a prompt against the same image must not rewrite the row.

        The stamp answers "has this ever been used", so the first answer is the only
        one that matters and a hundred runs should cost one write.
        """
        url = hosted_media_url(_PNG_DIGEST)
        async with db() as session:
            await mark_media_referenced(session, [url])
        first = await _referenced_at(db, _PNG_DIGEST)

        async with db() as session:
            await mark_media_referenced(session, [url])

        assert await _referenced_at(db, _PNG_DIGEST) == first

    async def test_ignores_inline_and_unparseable_references(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        """Neither names a stored row, so neither should reach the database."""
        async with db() as session:
            await mark_media_referenced(
                session,
                ["data:image/png;base64,aGk=", "https://example.com/cat.png"],
            )

        assert await _referenced_at(db, _PNG_DIGEST) is None

    async def test_is_a_noop_for_no_urls(self, db: DbSessionFactory) -> None:
        async with db() as session:
            await mark_media_referenced(session, [])

    async def test_resolution_stamps_what_it_resolves(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        """
        The stamp has to happen on the resolution path, not just when called directly.

        `resolve_message_media` is the single chokepoint every run passes through, and
        it is the span each of those runs writes that the sweeper cannot see.
        """
        from phoenix.server.api.helpers.message_media import resolve_message_media

        messages = [
            {
                "role": None,
                "content": [
                    {
                        "type": "media",
                        "kind": "image",
                        "url": hosted_media_url(_PNG_DIGEST),
                        "media_type": "image/png",
                    }
                ],
            }
        ]
        async with db() as session:
            await resolve_message_media(session, messages)  # type: ignore[arg-type]

        assert await _referenced_at(db, _PNG_DIGEST) is not None
        assert await _referenced_at(db, _JPEG_DIGEST) is None

    async def test_datetime_is_timezone_aware(
        self,
        db: DbSessionFactory,
        stored_media: None,
    ) -> None:
        async with db() as session:
            await mark_media_referenced(session, [hosted_media_url(_PNG_DIGEST)])

        stamped = await _referenced_at(db, _PNG_DIGEST)
        assert stamped is not None
        assert stamped.tzinfo is not None
        assert stamped <= datetime.now(timezone.utc)
