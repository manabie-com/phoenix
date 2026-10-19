"""
Inline media on an incoming span, moved to the media store before the span is written.

A provider that will not fetch a URL is sent the bytes, and instrumentation records the
request verbatim in ``input.value``. A Gemini call grading a PDF therefore carries the
whole paper, base64-encoded, on the ``invocation`` span and again on every ``call_llm``
span beneath it. On the staging deployment that grew the ``spans`` table to 44 GB, 42 GB
of it TOAST, and one attribute filter over a week of a 689-span project took 21 seconds:
Postgres decompresses a span's entire ``attributes`` value to read any key of it.

So as a span is queued for insertion, inline media in its ``input.value`` and
``output.value`` goes to the media store (GCS in a deployment) and is replaced by a
``phoenix://media/<sha256>`` reference — the same rewrite, through the same walker, as
copying a span into a dataset (see ``dataset_example_media``). The span view and
playground replay draw the reference wherever they would have drawn the bytes.

Wired in by :class:`MediaOffloadingBulkInserter`, which ``create_app`` builds in place
of upstream's ``BulkInserter``: upstream's insert loop, which upstream keeps reworking,
is not edited at all.

Off unless ``PHOENIX_SPAN_MEDIA_OFFLOAD=true``. Without a bucket the store is the local
filesystem, which in a container does not outlive the pod, so turning this on is a
deployment decision, not a default.

It never costs a span its media. Anything that cannot be stored — a type Phoenix does not
store, a payload that does not decode, a store that errors or is slow — stays inline,
exactly as it arrived.

Whatever reads ``input.value`` / ``output.value`` back — the span view, REST and client
span exports, evals over exported traces — gets the reference, not the bytes. Phoenix's
own views and replay resolve it; an outside reader fetches ``GET /v1/media/<sha256>``. A
rewritten value is also re-serialised compactly, so it is no longer byte-identical to
what the app sent; a value with nothing stored keeps its exact text.

Media a span references is stamped as used, so the sweeper keeps it, and keeps it for
good: the sweeper cannot afford to scan spans (see ``mark_media_referenced``), so tying a
file's lifetime to its traces' retention is a feature of its own.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections import OrderedDict
from dataclasses import replace
from time import monotonic
from typing import Any, Mapping, Optional

from openinference.semconv.trace import SpanAttributes
from typing_extensions import TypeGuard

from phoenix.db.bulk_inserter import BulkInserter
from phoenix.server.api.helpers.dataset_example_media import MediaStorer, replace_inline_media
from phoenix.server.api.helpers.media import (
    StoredMedia,
    mark_media_referenced,
    put_media,
    record_media,
)
from phoenix.server.types import DbSessionFactory
from phoenix.trace.attributes import get_attribute_value
from phoenix.trace.schemas import Span

logger = logging.getLogger(__name__)

ENV_PHOENIX_SPAN_MEDIA_OFFLOAD = "PHOENIX_SPAN_MEDIA_OFFLOAD"
"""
Set to ``true`` to move inline media on incoming spans to the media store.

Read here rather than in ``phoenix.config`` for the reason ``media_storage._config``
gives: that module is upstream's, and nothing else needs this value.
"""

_ATTRIBUTES = (SpanAttributes.INPUT_VALUE, SpanAttributes.OUTPUT_VALUE)
"""Where instrumentation records the raw request and response."""

_MIN_CHARS = 32_768
"""
Shorter values are not parsed at all. Nothing that small is worth a store round trip,
and it keeps the cost of this module at one length check for almost every span.
"""

_MARKERS = ('"inline_data"', "data:image/", "data:application/pdf")
"""
A value is parsed only if it holds one of the shapes the walker replaces: a Gemini
inline part, or a ``data:`` URL of a type Phoenix stores. A bare ``"data:"`` would match
every ``metadata:`` in a large log or prompt and parse it for nothing.
"""

_STORE_TIMEOUT_SECONDS = 10.0
"""
How long one file may wait on the store. Ingestion inserts spans one after another, so a
store that hangs would otherwise stall every span queued behind this one. Per file rather
than per span, so a span carrying many images is not timed out for their total.
"""

_PAUSE_SECONDS = 60.0
"""
After the store fails, spans go in inline for this long rather than each waiting out the
timeout against a store that is down. Only a store failure pauses: a span whose own
value cannot be rewritten says nothing about the next one.
"""

_paused_until = 0.0

_RECENT_CAPACITY = 1024
"""
How many recently recorded files are remembered, so the same file is not uploaded again.

An agent sends the same pages on every model call, and the ``invocation`` span repeats
them, so one paper arrives many times in a row. A remembered file is safe to reuse: it
was recorded and stamped as used, and the sweeper never deletes a used file.
"""

_recent: "OrderedDict[str, StoredMedia]" = OrderedDict()


def get_env_span_media_offload() -> bool:
    """Whether inline media on incoming spans is moved to the media store."""
    return os.environ.get(ENV_PHOENIX_SPAN_MEDIA_OFFLOAD, "").strip().lower() == "true"


class MediaOffloadingBulkInserter(BulkInserter):
    """
    The bulk inserter, with each span's inline media moved to the media store as it is
    queued.

    Queued rather than inserted: the insert loop holds one transaction across a whole
    batch and inserts its spans one after another, so uploading there would hold the
    connection — and on SQLite the write lock — through every upload in the batch, in
    turn. A span being queued has no transaction yet, and the receivers queue from their
    own tasks, so uploads for different requests also run concurrently. OTLP over HTTP
    queues after it has responded; a gRPC export waits for its uploads.

    ``create_app`` builds this by default, so upstream's insert loop is untouched.
    """

    async def _enqueue_span(self, span: Span, project_name: str) -> None:
        await super()._enqueue_span(await offload_span_media(self._db, span), project_name)


async def offload_span_media(db: DbSessionFactory, span: Span) -> Span:
    """
    The span with its inline media moved to the media store, or the span unchanged.

    The bytes are stored first, then their ``media_files`` rows are written and stamped
    as used in one short transaction of their own. If that fails the span keeps its
    media inline: nothing references the stored bytes, and the span is not lost. If the
    span itself later fails to insert, the rows stay, marked used: a few kept files, never
    a missing one.

    Args:
        db: Session factory for the ``media_files`` rows.
        span: The span as received.

    Returns:
        The span to queue.
    """
    offloaded, stored = await _offloaded(span)
    if not stored:
        return span
    # One row per file, in a fixed order, so concurrent spans carrying the same files
    # take their row locks in the same order and cannot deadlock.
    unique = sorted({media.sha256: media for media in stored}.values(), key=_by_sha256)
    try:
        async with db() as session:
            for media in unique:
                await record_media(session, media)
            await mark_media_referenced(session, [media.url for media in unique])
    except Exception:
        logger.exception(
            f"Failed to record stored media for span {span.context.span_id}; keeping it inline"
        )
        return span
    for media in unique:
        _remember(media)
    return offloaded


def _by_sha256(media: StoredMedia) -> str:
    return media.sha256


def _remember(media: StoredMedia) -> None:
    _recent[media.sha256] = media
    _recent.move_to_end(media.sha256)
    while len(_recent) > _RECENT_CAPACITY:
        _recent.popitem(last=False)


def _recalled(content: bytes) -> Optional[StoredMedia]:
    media = _recent.get(hashlib.sha256(content).hexdigest())
    if media is not None:
        _recent.move_to_end(media.sha256)
    return media


async def _offloaded(span: Span) -> tuple[Span, list[StoredMedia]]:
    """The span with its inline media replaced, and the media that replaced it."""
    if not get_env_span_media_offload() or _store_paused():
        return span, []
    candidates: dict[str, str] = {}
    for key in _ATTRIBUTES:
        value = get_attribute_value(span.attributes, key)
        if _worth_parsing(value):
            candidates[key] = value
    if not candidates:
        return span, []

    span_id = span.context.span_id
    stored: list[StoredMedia] = []

    async def store(content: bytes) -> Optional[StoredMedia]:
        # When the store fails, that file stays inline and so does every file after it
        # until the pause ends, in this span and the next: it is the store that failed.
        # Files this span already stored stay stored.
        global _paused_until
        if (media := _recalled(content)) is not None:
            stored.append(media)
            return media
        if _store_paused():
            return None
        try:
            media = await asyncio.wait_for(put_media(content), _STORE_TIMEOUT_SECONDS)
        except Exception:
            _paused_until = monotonic() + _PAUSE_SECONDS
            logger.exception(
                f"Failed to store inline media from span {span_id}; keeping it inline, "
                f"as will spans for the next {_PAUSE_SECONDS:.0f}s"
            )
            return None
        if media is not None:
            stored.append(media)
        return media

    attributes = span.attributes
    for key, value in candidates.items():
        before = len(stored)
        try:
            replaced = await _replace_in_value(store, value)
        except Exception:
            # The value itself, not the store, e.g. JSON nested past the recursion limit.
            # It stays as it arrived; anything already stored for it is left unrecorded.
            logger.warning(f"Could not rewrite {key} of span {span_id}; keeping it inline")
            del stored[before:]
            continue
        # Rewritten only if something was stored, so a value with nothing to replace
        # keeps its exact original text rather than a re-serialisation of it.
        if len(stored) > before:
            attributes = _with_attribute(attributes, key, replaced)
    if not stored:
        return span, []
    return replace(span, attributes=attributes), stored


def _store_paused() -> bool:
    return monotonic() < _paused_until


def _worth_parsing(value: Any) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) >= _MIN_CHARS
        and any(marker in value for marker in _MARKERS)
    )


async def _replace_in_value(store: MediaStorer, value: str) -> str:
    """A recorded value with its inline media replaced, whether JSON or a bare URL."""
    try:
        parsed = json.loads(value)
    except ValueError:
        replaced = await replace_inline_media(store, value)
        return replaced if isinstance(replaced, str) else value
    replaced = await replace_inline_media(store, parsed)
    text = json.dumps(replaced, ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate, which the original could only have held as a \u escape (a
        # truncated emoji, say). Written out raw it is not UTF-8 and the span could not
        # be stored at all, so it stays escaped.
        text = json.dumps(replaced, separators=(",", ":"))
    return text


def _with_attribute(attributes: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """
    ``attributes`` with the nested attribute ``key`` set, copying only along its path.

    The span's attribute dict is shared with whatever else holds the span, and the
    bulk inserter goes on to price the span from the original, so it is not mutated.
    """
    head, _, rest = key.partition(".")
    updated = dict(attributes)
    updated[head] = _with_attribute(attributes.get(head) or {}, rest, value) if rest else value
    return updated
