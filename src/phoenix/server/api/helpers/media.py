"""Resolution of prompt media references into the bytes a model provider needs."""

from datetime import datetime, timezone
from typing import Iterable, NamedTuple, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from phoenix.db import models
from phoenix.db.types.media import HostedMediaRef, InlineMedia, parse_media_url
from phoenix.server.api.helpers.media_storage import media_store


class MediaResolutionError(Exception):
    """Raised when prompt media cannot be resolved into bytes."""


class ResolvedMedia(NamedTuple):
    """Media bytes ready to hand to a model provider."""

    content: bytes
    media_type: str
    file_name: Optional[str] = None
    """The name the media was stored under, when one is known."""


async def resolve_media(
    session: AsyncSession,
    urls: Iterable[str],
) -> dict[str, ResolvedMedia]:
    """
    Resolve prompt media references into their bytes, keyed by reference URL.

    Phoenix-hosted references are read in a single query, so a message carrying
    several images costs one round trip rather than one per image. The media type
    returned here is authoritative — for hosted media it was determined from the
    bytes at upload time, and for inline media it is declared by the data URL —
    whereas the type recorded on a prompt part is advisory.

    Args:
        session: Session used to read Phoenix-hosted media.
        urls: The reference URLs to resolve. Repeated URLs are resolved once.

    Returns:
        A mapping from each reference URL to its resolved bytes and media type.

    Raises:
        MediaResolutionError: A reference is malformed, carries a corrupt inline
            payload, or names hosted media that is no longer present.
    """
    unique_urls = set(urls)
    if not unique_urls:
        return {}

    inline_media: dict[str, InlineMedia] = {}
    hosted_digests: dict[str, str] = {}
    for url in unique_urls:
        try:
            reference = parse_media_url(url)
        except ValueError as error:
            raise MediaResolutionError(str(error)) from error
        if isinstance(reference, InlineMedia):
            inline_media[url] = reference
        else:
            hosted_digests[url] = reference.sha256

    resolved: dict[str, ResolvedMedia] = {}
    for url, reference in inline_media.items():
        try:
            content = reference.decode()
        except ValueError as error:
            raise MediaResolutionError(str(error)) from error
        resolved[url] = ResolvedMedia(content=content, media_type=reference.media_type)

    if hosted_digests:
        digests = set(hosted_digests.values())
        rows = await session.execute(
            select(
                models.MediaFile.sha256,
                models.MediaFile.media_type,
                models.MediaFile.file_name,
            ).where(models.MediaFile.sha256.in_(digests))
        )
        stored = {sha256: (media_type, file_name) for sha256, media_type, file_name in rows}
        if missing := digests - stored.keys():
            raise MediaResolutionError(
                f"prompt references media that is no longer stored in Phoenix: "
                f"{', '.join(sorted(missing))}"
            )
        # The row records that the media exists and what type it is; the bytes come from
        # the store. Both are checked, because the two can disagree — a row whose bytes
        # were swept, or bytes whose row never landed.
        store = media_store()
        contents: dict[str, bytes] = {}
        for sha256 in stored:
            if (from_store := await store.get(sha256)) is None:
                raise MediaResolutionError(
                    f"prompt references media whose content is no longer stored: {sha256}"
                )
            contents[sha256] = from_store
        for url, sha256 in hosted_digests.items():
            media_type, file_name = stored[sha256]
            resolved[url] = ResolvedMedia(
                content=contents[sha256], media_type=media_type, file_name=file_name
            )

    return resolved


def hosted_digests(urls: Iterable[str]) -> set[str]:
    """
    The Phoenix-hosted digests among a set of media references.

    Args:
        urls: Reference URLs, of any scheme.

    Returns:
        The digests of the hosted references. Inline ``data:`` media names no stored
        row, and a reference that does not parse names none either, so both are
        skipped rather than raising — a caller marking media as used should not fail
        over a reference that resolution has already accepted or rejected on its own
        terms.
    """
    digests: set[str] = set()
    for url in set(urls):
        try:
            reference = parse_media_url(url)
        except ValueError:
            continue
        if isinstance(reference, HostedMediaRef):
            digests.add(reference.sha256)
    return digests


async def mark_media_referenced(session: AsyncSession, urls: Iterable[str]) -> None:
    """
    Record that media has been used, so the sweeper stops treating it as an orphan.

    A media digest is persisted in four places: a prompt version's template, a span's
    attributes, an experiment task's template, and a dataset example's input. The
    sweeper scans the first, third and fourth directly. It cannot scan the second —
    an hourly ``LIKE`` over a cast JSON column on the ``spans`` table is not
    affordable — and a span is written by every run that sends media to a provider.

    Marking here closes that gap from the other end. Every path that reaches a
    provider resolves its media first, so stamping at resolution time is equivalent
    to stamping every media reference a span could come to hold, at the cost of one
    UPDATE.

    The stamp is set once and never cleared, so media that was used is kept for good.
    Reclaiming it would mean tying media lifetime to trace retention, which is a
    feature rather than a fix.

    Args:
        session: Session used to update the media rows. The caller's transaction
            commits it.
        urls: The reference URLs that were resolved. Inline media is ignored.
    """
    if not (digests := hosted_digests(urls)):
        return
    await session.execute(
        update(models.MediaFile)
        # Only stamp rows that have never been stamped: a prompt run against the same
        # image a hundred times should cost one write, not a hundred.
        .where(models.MediaFile.sha256.in_(digests), models.MediaFile.referenced_at.is_(None))
        .values(referenced_at=datetime.now(timezone.utc))
    )
