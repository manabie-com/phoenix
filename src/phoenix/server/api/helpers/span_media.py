"""
Inline media on an incoming span, moved to the media store before the span is written.

A provider that will not fetch a URL is sent the bytes, and instrumentation records the
request verbatim in ``input.value``. A Gemini call grading a PDF therefore carries the
whole paper, base64-encoded, on the ``invocation`` span and again on every ``call_llm``
span beneath it. On the staging deployment that grew the ``spans`` table to 44 GB, 42 GB
of it TOAST, and one attribute filter over a week of a 689-span project took 21 seconds:
Postgres decompresses a span's entire ``attributes`` value to read any key of it.

So once a span is received, and before it is queued for insertion, inline media in its
``input.value`` and ``output.value`` goes to the media store (GCS in a deployment) and is
replaced by a ``phoenix://media/<sha256>`` reference — the same rewrite, through the
same walker, as copying a span into a dataset (see ``dataset_example_media``). The span
view and playground replay draw the reference wherever they would have drawn the bytes.

Wired in by :class:`MediaOffloadingBulkInserter`, which ``create_app`` builds in place
of upstream's ``BulkInserter``: upstream's insert loop, which upstream keeps reworking,
is not edited at all. Spans handed to the inserter's constructor as
``initial_batch_of_spans`` bypass the queue, and so keep their media inline.

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

_MARKERS = ('"inline_data"', '"inlineData"', "data:image/", "data:application/pdf")
"""
A value is parsed only if it holds one of the shapes the walker replaces: a Gemini
inline part (snake_case from the Python SDK, camelCase from the JS SDK and REST API), or
a ``data:`` URL of a type Phoenix stores. A bare ``"data:"`` would match every
``metadata:`` in a large log or prompt and parse it for nothing.
"""

_STORE_TIMEOUT_SECONDS = 10.0
"""
How long one file may wait on the store. Per file rather than per span, so a span
carrying many images is not timed out for their total.
"""

_PAUSE_SECONDS = 60.0
"""
After the store fails, spans go in inline for this long rather than each waiting out the
timeout against a store that is down. Only a store failure pauses: a span whose own
value cannot be rewritten says nothing about the next one.
"""

_paused_until = 0.0

_MAX_SPANS_UPLOADING = 8
"""
How many spans may be uploading at once before queueing another waits for a slot.

Each holds its whole decoded request in memory until it is queued, which is what bounds
the extra memory to a few large requests rather than a whole burst of them.
"""

_HASH_IN_THREAD_BYTES = 1 << 20
"""Files at least this large are hashed in a thread: ``hashlib`` releases the GIL."""

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
    The bulk inserter, with each span's inline media moved to the media store before
    the span is queued.

    Not inside the insert loop: it holds one transaction across a whole batch and
    inserts its spans one after another, so uploading there would hold the connection —
    and on SQLite the write lock — through every upload in the batch, in turn.

    Not in the receiver's path either: gRPC and ``POST /v1/spans`` wait for the queue
    before they answer their client, so a span carrying media is handed to a task of its
    own and the receiver answers at once. A span without media is queued directly.

    Overrides upstream's private ``_enqueue_span``, the callable ``__aenter__`` hands
    every receiver. ``TestWiring`` fails if a rename upstream leaves this unused.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._uploading: "set[asyncio.Task[None]]" = set()
        self._upload_slots = asyncio.Semaphore(_MAX_SPANS_UPLOADING)

    async def _enqueue_span(self, span: Span, project_name: str) -> None:
        if not _candidates(span):
            await super()._enqueue_span(span, project_name)
            return
        await self._upload_slots.acquire()
        task = asyncio.create_task(self._offload_then_enqueue(span, project_name))
        self._uploading.add(task)
        task.add_done_callback(self._uploading.discard)

    async def _offload_then_enqueue(self, span: Span, project_name: str) -> None:
        queued = span
        try:
            queued = await offload_span_media(self._db, span)
        finally:
            # Queued even when cancelled at shutdown, with its media inline: a span is
            # never dropped for want of an upload.
            self._upload_slots.release()
            await super()._enqueue_span(queued, project_name)

    async def __aexit__(self, *args: Any) -> None:
        await self._finish_uploads(self._shutdown_drain_sec)
        await super().__aexit__(*args)

    async def _finish_uploads(self, timeout: float) -> None:
        """Wait for spans still uploading, then queue the stragglers with media inline."""
        if not self._uploading:
            return
        _, pending = await asyncio.wait(set(self._uploading), timeout=timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def offload_span_media(db: DbSessionFactory, span: Span) -> Span:
    """
    The span with its inline media moved to the media store, or the span unchanged.

    The bytes are stored first, then the ``media_files`` rows of files stored just now
    are written and stamped as used in one short transaction of their own; a file this
    process already recorded needs neither. If that transaction fails, the span keeps
    its media inline: nothing references the stored bytes, and the span is not lost. If
    the span itself later fails to insert, the rows stay, marked used: a few kept files,
    never a missing one.

    Args:
        db: Session factory for the ``media_files`` rows.
        span: The span as received.

    Returns:
        The span to queue.
    """
    offloaded, stored, uploaded = await _offloaded(span)
    if not stored:
        return span
    if uploaded:
        # One row per file, in a fixed order, so concurrent spans carrying the same files
        # take their row locks in the same order and cannot deadlock.
        unique = sorted({media.sha256: media for media in uploaded}.values(), key=_by_sha256)
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


def _recall(sha256: str) -> Optional[StoredMedia]:
    media = _recent.get(sha256)
    if media is not None:
        _recent.move_to_end(sha256)
    return media


async def _sha256(content: bytes) -> str:
    if len(content) < _HASH_IN_THREAD_BYTES:
        return hashlib.sha256(content).hexdigest()
    return await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())


def _candidates(span: Span) -> dict[str, str]:
    """The span's values worth rewriting, or none when offloading is off or paused."""
    if not get_env_span_media_offload() or _store_paused():
        return {}
    candidates: dict[str, str] = {}
    for key in _ATTRIBUTES:
        value = get_attribute_value(span.attributes, key)
        if _worth_parsing(value):
            candidates[key] = value
    return candidates


async def _offloaded(span: Span) -> tuple[Span, list[StoredMedia], list[StoredMedia]]:
    """
    The span with its inline media replaced, the media that replaced it, and which of
    that was uploaded just now rather than recalled.
    """
    candidates = _candidates(span)
    if not candidates:
        return span, [], []

    span_id = span.context.span_id
    stored: list[StoredMedia] = []
    uploaded: list[StoredMedia] = []

    async def store(content: bytes) -> Optional[StoredMedia]:
        # When the store fails, that file stays inline and so does every file after it
        # until the pause ends, in this span and the next: it is the store that failed.
        # Files this span already stored stay stored.
        global _paused_until
        sha256 = await _sha256(content)
        if (media := _recall(sha256)) is not None:
            stored.append(media)
            return media
        if _store_paused():
            return None
        try:
            media = await asyncio.wait_for(
                put_media(content, sha256=sha256), _STORE_TIMEOUT_SECONDS
            )
        except Exception:
            _paused_until = monotonic() + _PAUSE_SECONDS
            logger.exception(
                f"Failed to store inline media from span {span_id}; keeping it inline, "
                f"as will spans for the next {_PAUSE_SECONDS:.0f}s"
            )
            return None
        if media is not None:
            stored.append(media)
            uploaded.append(media)
        return media

    attributes = span.attributes
    for key, value in candidates.items():
        stored_before, uploaded_before = len(stored), len(uploaded)
        try:
            replaced = await _replace_in_value(store, value)
        except Exception:
            # The value itself, not the store, e.g. JSON nested past the recursion limit.
            # It stays as it arrived. Bytes already uploaded for it are left without a
            # row, where the sweeper, which walks rows, will not see them: some wasted
            # space, never lost media.
            logger.warning(
                f"Could not rewrite {key} of span {span_id}; keeping it inline", exc_info=True
            )
            del stored[stored_before:]
            del uploaded[uploaded_before:]
            continue
        # Rewritten only if something was stored, so a value with nothing to replace
        # keeps its exact original text rather than a re-serialisation of it.
        if len(stored) > stored_before:
            attributes = _with_attribute(attributes, key, replaced)
    if not stored:
        return span, [], []
    return replace(span, attributes=attributes), stored, uploaded


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

    The attribute dict belongs to the span as the receiver decoded it, so it is copied
    rather than mutated. Pricing reads only token counts, which this never touches.
    """
    head, _, rest = key.partition(".")
    updated = dict(attributes)
    updated[head] = _with_attribute(attributes.get(head) or {}, rest, value) if rest else value
    return updated
