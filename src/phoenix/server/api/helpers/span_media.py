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

What it recognises is what the walker does: Gemini inline parts (``inline_data`` and
``inlineData``) and ``data:`` URLs, which covers Gemini and OpenAI requests. Anthropic
and Bedrock base64 sources are not rewritten and stay inline.

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
own views and replay resolve it; an outside reader fetches ``GET /v1/media/<sha256>``.
A rewritten value is re-serialised, so it is no longer byte-identical to what the app
sent: whitespace is compacted, a duplicated key keeps its last value, and numbers are
re-printed (``1e400`` becomes ``Infinity``). A value with nothing stored keeps its exact
text.

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

_UPLOAD_WORKERS = 8
"""How many spans upload at once. Spans beyond that wait their turn in the queue."""

_HASH_IN_THREAD_BYTES = 1 << 20
"""Files at least this large are hashed in a thread: ``hashlib`` releases the GIL."""

_RECENT_CAPACITY = 1024
"""
How many recently recorded files are remembered, so the same file is not uploaded again.

An agent sends the same pages on every model call, and the ``invocation`` span repeats
them, so one paper arrives many times in a row. A remembered file is safe to reuse: it
was recorded and stamped as used, and the sweeper never deletes a used file.
"""


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
    before they answer their client. A span carrying media goes on a queue of its own,
    which :data:`_UPLOAD_WORKERS` workers drain, and the receiver returns at once. Spans
    waiting there count toward :attr:`is_full`, so the receivers' backpressure still
    sees them. A span without media is queued for insertion directly.

    Uploads are workers reading a queue rather than a task per span, so a span not yet
    started is data that shutdown can still queue, not a task cancelled before it ran.

    Overrides upstream's private ``_enqueue_span``, the callable ``__aenter__`` hands
    every receiver. ``TestWiring`` fails if a rename upstream leaves this unused.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._offloader = SpanMediaOffloader(self._db)
        self._to_offload: "Optional[asyncio.Queue[tuple[Span, str, dict[str, str]]]]" = None
        self._offload_workers: "list[asyncio.Task[None]]" = []
        self._offloading = 0
        """Spans waiting to upload or uploading, which are in neither of upstream's queues."""

    @property
    def is_full(self) -> bool:
        queued = len(self._spans) + len(self._span_batch) + self._offloading
        return super().is_full or bool(
            self._max_spans_queue_size and self._max_spans_queue_size <= queued
        )

    async def __aenter__(self) -> Any:
        handles = await super().__aenter__()
        self._to_offload = asyncio.Queue()
        self._offload_workers = [
            asyncio.create_task(self._offload_worker(self._to_offload))
            for _ in range(_UPLOAD_WORKERS)
        ]
        return handles

    async def _enqueue_span(self, span: Span, project_name: str) -> None:
        candidates = self._offloader.candidates(span)
        if candidates and self._to_offload is not None:
            self._offloading += 1
            self._to_offload.put_nowait((span, project_name, candidates))
        else:
            await super()._enqueue_span(span, project_name)

    async def _offload_worker(
        self, to_offload: "asyncio.Queue[tuple[Span, str, dict[str, str]]]"
    ) -> None:
        while True:
            span, project_name, candidates = await to_offload.get()
            queued = span
            try:
                queued = await self._offloader.offload(span, candidates)
            except Exception:
                logger.exception(
                    f"Failed to move inline media on span {span.context.span_id}; keeping it inline"
                )
            finally:
                # Also when cancelled at shutdown, with its media inline: a span is never
                # dropped for want of an upload.
                await super()._enqueue_span(queued, project_name)
                self._offloading -= 1
                to_offload.task_done()

    async def __aexit__(self, *args: Any) -> None:
        # One deadline for both phases, so shutdown takes no longer than configured: up to
        # half for uploads in progress, the rest for inserting what they produced.
        deadline = monotonic() + self._shutdown_drain_sec
        await self._finish_offloads(self._shutdown_drain_sec / 2)
        self._shutdown_drain_sec = max(0.0, deadline - monotonic())
        await super().__aexit__(*args)

    async def _finish_offloads(self, timeout: float) -> None:
        """Let the workers finish within ``timeout``, then queue the rest with media inline."""
        to_offload, self._to_offload = self._to_offload, None
        if to_offload is None:
            return
        try:
            await asyncio.wait_for(to_offload.join(), timeout)
        except asyncio.TimeoutError:
            pass
        for worker in self._offload_workers:
            worker.cancel()
        await asyncio.gather(*self._offload_workers, return_exceptions=True)
        self._offload_workers = []
        while not to_offload.empty():
            span, project_name, _ = to_offload.get_nowait()
            await super()._enqueue_span(span, project_name)
            self._offloading -= 1


class SpanMediaOffloader:
    """
    Moves a span's inline media to the media store, for one app and its database.

    The pause and the recently recorded files live here rather than in the module: a
    process can hold more than one app (tests, an embedded server), and a file recorded
    in one database says nothing about another.
    """

    def __init__(self, db: DbSessionFactory) -> None:
        self._db = db
        self._paused_until = 0.0
        self._recent: "OrderedDict[str, StoredMedia]" = OrderedDict()

    def candidates(self, span: Span) -> dict[str, str]:
        """The span's values worth rewriting, or none when offloading is off or paused."""
        if not get_env_span_media_offload() or self._store_paused():
            return {}
        candidates: dict[str, str] = {}
        for key in _ATTRIBUTES:
            value = get_attribute_value(span.attributes, key)
            if _worth_parsing(value):
                candidates[key] = value
        return candidates

    async def offload(self, span: Span, candidates: Mapping[str, str]) -> Span:
        """
        The span with its inline media moved to the media store, or the span unchanged.

        The bytes are stored first, then the ``media_files`` rows of files stored just
        now are written and stamped as used in one short transaction of their own; a file
        already recorded needs neither. If that transaction fails, the span keeps its
        media inline: nothing references the stored bytes, and the span is not lost. If
        the span itself later fails to insert, the rows stay, marked used: a few kept
        files, never a missing one.

        Args:
            span: The span as received.
            candidates: Its values worth rewriting, from :meth:`candidates`.

        Returns:
            The span to queue.
        """
        offloaded, stored, uploaded = await self._offloaded(span, candidates)
        if not stored:
            return span
        if uploaded:
            # One row per file, in a fixed order, so concurrent spans carrying the same
            # files take their row locks in the same order and cannot deadlock.
            unique = sorted({media.sha256: media for media in uploaded}.values(), key=_by_sha256)
            try:
                async with self._db() as session:
                    for media in unique:
                        await record_media(session, media)
                    await mark_media_referenced(session, [media.url for media in unique])
            except Exception:
                logger.exception(
                    f"Failed to record stored media for span {span.context.span_id}; "
                    "keeping it inline"
                )
                return span
            for media in unique:
                self._remember(media)
        return offloaded

    async def _offloaded(
        self, span: Span, candidates: Mapping[str, str]
    ) -> tuple[Span, list[StoredMedia], list[StoredMedia]]:
        """
        The span with its inline media replaced, the media that replaced it, and which of
        that was uploaded just now rather than recalled.
        """
        span_id = span.context.span_id
        stored: list[StoredMedia] = []
        uploaded: list[StoredMedia] = []

        async def store(content: bytes) -> Optional[StoredMedia]:
            # When the store fails, that file stays inline and so does every file after it
            # until the pause ends, in this span and the next: it is the store that
            # failed. Files this span already stored stay stored.
            sha256 = await _sha256(content)
            if (media := self._recall(sha256)) is not None:
                stored.append(media)
                return media
            if self._store_paused():
                return None
            try:
                media = await asyncio.wait_for(
                    put_media(content, sha256=sha256), _STORE_TIMEOUT_SECONDS
                )
            except Exception:
                self._paused_until = monotonic() + _PAUSE_SECONDS
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
                # The value itself, not the store, e.g. JSON nested past the recursion
                # limit. It stays as it arrived. Bytes already uploaded for it are left
                # without a row, where the sweeper, which walks rows, will not see them:
                # some wasted space, never lost media.
                logger.warning(
                    f"Could not rewrite {key} of span {span_id}; keeping it inline",
                    exc_info=True,
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

    def _store_paused(self) -> bool:
        return monotonic() < self._paused_until

    def _remember(self, media: StoredMedia) -> None:
        self._recent[media.sha256] = media
        self._recent.move_to_end(media.sha256)
        while len(self._recent) > _RECENT_CAPACITY:
            self._recent.popitem(last=False)

    def _recall(self, sha256: str) -> Optional[StoredMedia]:
        media = self._recent.get(sha256)
        if media is not None:
            self._recent.move_to_end(sha256)
        return media


def _by_sha256(media: StoredMedia) -> str:
    return media.sha256


async def _sha256(content: bytes) -> str:
    if len(content) < _HASH_IN_THREAD_BYTES:
        return hashlib.sha256(content).hexdigest()
    return await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())


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
