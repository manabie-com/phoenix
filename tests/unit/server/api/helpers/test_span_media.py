"""
Inline media on an incoming span goes to the media store, and the span keeps a reference.

A Gemini call grading a PDF records the whole paper, base64-encoded, in ``input.value``
— on the ``invocation`` span and again on every ``call_llm`` span under it. On staging
that made the ``spans`` table 44 GB. These tests queue spans through the callable the
bulk inserter hands the receivers and insert them with its own loop, the path every
ingested span takes; ``TestWiring`` checks the app builds this inserter.

The one thing that must never happen is a span losing its media: whatever cannot be
stored stays inline, byte for byte.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

import asyncio
import base64
import hashlib
import inspect
import json
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from sqlalchemy import select

from phoenix.db import bulk_inserter, models
from phoenix.db.insertion.span import insert_span as upstream_insert_span
from phoenix.server import app
from phoenix.server.api.helpers import span_media
from phoenix.server.api.helpers.dataset_example_media import MEDIA_URL_KEY
from phoenix.server.api.helpers.media import StoredMedia, put_media
from phoenix.server.api.helpers.media import record_media as upstream_record_media
from phoenix.server.api.helpers.media_storage import media_store
from phoenix.server.api.helpers.span_media import MediaOffloadingBulkInserter
from phoenix.server.types import DbSessionFactory
from phoenix.trace.schemas import Span, SpanContext, SpanKind, SpanStatusCode
from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401

PDF = b"%PDF-1.7\n" + bytes(range(256)) * 200  # 51,209 bytes: big enough to be offloaded
PDF_SHA = hashlib.sha256(PDF).hexdigest()
PDF_URL = f"phoenix://media/{PDF_SHA}"
# The Google GenAI SDK writes the URL-safe alphabet, so that is what a real span holds.
PDF_B64 = base64.urlsafe_b64encode(PDF).decode()
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
# A PNG padded past the size worth offloading, so a span can carry a second file.
PNG_B64 = base64.b64encode(PNG + b"\x00" * 40_000).decode()

_START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def call_llm_request(payload: str = PDF_B64, mime_type: str = "application/pdf") -> str:
    """``input.value`` as the ADK instrumentation writes it on a ``call_llm`` span."""
    return json.dumps(
        {
            "model": "gemini-3.5-flash-lite",
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Grade this submission."},
                        {"inline_data": {"data": payload, "mime_type": mime_type}},
                    ],
                }
            ],
        },
        separators=(",", ":"),
    )


def span(span_id: str, input_value: str, *, trace_id: str = "trace") -> Span:
    return Span(
        name="call_llm",
        context=SpanContext(trace_id=trace_id, span_id=span_id),
        span_kind=SpanKind.LLM,
        parent_id=None,
        start_time=_START,
        end_time=_START + timedelta(seconds=1),
        status_code=SpanStatusCode.OK,
        status_message="",
        attributes={"input": {"value": input_value, "mime_type": "application/json"}},
        events=[],
        conversation=None,
    )


class _Events:
    def put(self, item: Any) -> None:
        pass


async def _idle() -> None:
    """Stands in for the inserter's background loop, so each test inserts when it says."""


async def _refusing_png(content: bytes, **kwargs: Any) -> Optional[StoredMedia]:
    """A store that fails on PNGs and stores anything else."""
    if content.startswith(b"\x89PNG"):
        raise RuntimeError("bucket unavailable")
    return await put_media(content, **kwargs)


def inserter_for(db: DbSessionFactory, **kwargs: Any) -> MediaOffloadingBulkInserter:
    inserter = MediaOffloadingBulkInserter(
        db,
        event_queue=_Events(),
        span_cost_calculator=None,  # no span here is priced
        **kwargs,
    )
    inserter._bulk_insert = _idle  # type: ignore[method-assign]
    return inserter


async def ingest(db: DbSessionFactory, *spans: Span) -> None:
    """
    Insert spans the way the server does: queue each through the callable the inserter
    hands the receivers, then run the inserter's own loop over them.

    Each span's uploads finish before the next is queued, so tests see one order.
    """
    inserter = inserter_for(db)
    _, enqueue_span, _ = await inserter.__aenter__()
    try:
        for s in spans:
            await enqueue_span(s, "project")
            await inserter._finish_uploads(timeout=30)
        await inserter._insert_spans(len(spans))
    finally:
        await inserter.__aexit__()


async def stored_input(db: DbSessionFactory, span_id: str) -> str:
    async with db() as session:
        attributes = await session.scalar(
            select(models.Span.attributes).where(models.Span.span_id == span_id)
        )
    assert attributes is not None, f"span {span_id} was not inserted"
    value = attributes["input"]["value"]
    assert isinstance(value, str)
    return value


async def media_row(db: DbSessionFactory, sha256: str) -> Optional[models.MediaFile]:
    async with db() as session:
        return await session.scalar(
            select(models.MediaFile).where(models.MediaFile.sha256 == sha256)
        )


@pytest.fixture
def offload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(span_media.ENV_PHOENIX_SPAN_MEDIA_OFFLOAD, "true")
    monkeypatch.setattr(span_media, "_paused_until", 0.0)
    monkeypatch.setattr(span_media, "_recent", OrderedDict())


@pytest.mark.usefixtures("offload")
class TestThePaperLeavesTheSpan:
    async def test_the_span_keeps_a_reference_in_place_of_the_bytes(
        self, db: DbSessionFactory
    ) -> None:
        await ingest(db, span("a", call_llm_request()))
        value = await stored_input(db, "a")
        parts = json.loads(value)["contents"][0]["parts"]
        assert parts[1]["inline_data"] == {
            "mime_type": "application/pdf",
            MEDIA_URL_KEY: PDF_URL,
        }
        assert parts[0] == {"text": "Grade this submission."}
        assert len(value) < 400

    async def test_the_bytes_are_in_the_store(self, db: DbSessionFactory) -> None:
        await ingest(db, span("a", call_llm_request()))
        assert await media_store().get(PDF_SHA) == PDF

    async def test_the_media_is_recorded_as_used_so_the_sweeper_keeps_it(
        self, db: DbSessionFactory
    ) -> None:
        await ingest(db, span("a", call_llm_request()))
        row = await media_row(db, PDF_SHA)
        assert row is not None
        assert row.media_type == "application/pdf"
        assert row.size_bytes == len(PDF)
        assert row.referenced_at is not None

    async def test_the_invocation_span_shape_too(self, db: DbSessionFactory) -> None:
        """The root span records the runner's arguments, with the message nested."""
        root = json.dumps(
            {
                "user_id": "student",
                "new_message": {
                    "parts": [{"inline_data": {"data": PDF_B64, "mime_type": "application/pdf"}}],
                    "role": "user",
                },
            }
        )
        await ingest(db, span("root", root))
        value = json.loads(await stored_input(db, "root"))
        assert value["new_message"]["parts"][0]["inline_data"][MEDIA_URL_KEY] == PDF_URL
        assert value["user_id"] == "student"

    async def test_a_data_url_moves_too(self, db: DbSessionFactory) -> None:
        """The OpenAI shape, which is how ai-tutor's images arrive."""
        big_png = PNG + b"\x00" * 40_000
        data_url = f"data:image/png;base64,{base64.b64encode(big_png).decode()}"
        request = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": data_url}}],
                    }
                ]
            }
        )
        await ingest(db, span("a", request))
        url = json.loads(await stored_input(db, "a"))["messages"][0]["content"][0]["image_url"][
            "url"
        ]
        assert url == f"phoenix://media/{hashlib.sha256(big_png).hexdigest()}"

    @pytest.mark.parametrize(
        "part",
        [
            pytest.param(
                lambda url: {"type": "file", "file": {"filename": "p.pdf", "file_data": url}},
                id="chat-completions",
            ),
            pytest.param(
                lambda url: {"type": "input_file", "filename": "p.pdf", "file_data": url},
                id="responses",
            ),
        ],
    )
    async def test_an_openai_document_moves_too(self, db: DbSessionFactory, part: Any) -> None:
        """OpenAI sends a PDF as a data URL too, in either API's file part."""
        data_url = f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}"
        request = json.dumps({"messages": [{"role": "user", "content": [part(data_url)]}]})
        await ingest(db, span("a", request))
        content = json.loads(await stored_input(db, "a"))["messages"][0]["content"]
        assert content == [part(PDF_URL)]

    async def test_one_paper_on_many_spans_is_stored_once(self, db: DbSessionFactory) -> None:
        await ingest(db, span("a", call_llm_request()), span("b", call_llm_request()))
        async with db() as session:
            rows = list(await session.scalars(select(models.MediaFile.sha256)))
        assert rows == [PDF_SHA]
        for span_id in ("a", "b"):
            assert PDF_URL in await stored_input(db, span_id)

    async def test_one_paper_on_many_spans_is_uploaded_once(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent re-sends the same pages on every model call."""
        uploads = 0

        async def counting(content: bytes, **kwargs: Any) -> Optional[StoredMedia]:
            nonlocal uploads
            uploads += 1
            return await put_media(content, **kwargs)

        monkeypatch.setattr(span_media, "put_media", counting)
        spans = [span(span_id, call_llm_request()) for span_id in ("a", "b", "c")]
        await ingest(db, *spans)
        assert uploads == 1
        for span_id in ("a", "b", "c"):
            assert PDF_URL in await stored_input(db, span_id)


class TestNothingIsLost:
    async def test_off_unless_enabled(self, db: DbSessionFactory) -> None:
        request = call_llm_request()
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request
        assert await media_row(db, PDF_SHA) is None

    @pytest.mark.usefixtures("offload")
    async def test_a_small_value_is_left_alone(self, db: DbSessionFactory) -> None:
        request = call_llm_request(base64.b64encode(PNG).decode(), "image/png")
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request

    @pytest.mark.usefixtures("offload")
    async def test_a_value_with_nothing_to_store_keeps_its_exact_text(
        self, db: DbSessionFactory
    ) -> None:
        """Mentions a data URL, so it is parsed, but holds none, so it is not rewritten."""
        prose = "paste it as data:image/png;base64,... in the field. " + "words " * 10_000
        request = json.dumps({"prompt": prose}, indent=2)
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request

    @pytest.mark.usefixtures("offload")
    async def test_text_that_only_says_metadata_is_not_parsed(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parsed = 0

        async def counting(store: Any, value: str) -> str:
            nonlocal parsed
            parsed += 1
            return value

        monkeypatch.setattr(span_media, "_replace_in_value", counting)
        request = json.dumps({"log": "metadata: " + "words " * 10_000})
        await ingest(db, span("a", request))
        assert parsed == 0
        assert await stored_input(db, "a") == request

    @pytest.mark.usefixtures("offload")
    async def test_a_value_it_cannot_parse_stays_inline_without_pausing(
        self, db: DbSessionFactory
    ) -> None:
        """Nested past the recursion limit: that span's own problem, not the store's."""
        unparseable = "[" * 100_000 + '"inline_data"' + "]" * 100_000
        await ingest(db, span("a", unparseable), span("b", call_llm_request()))
        assert await stored_input(db, "a") == unparseable
        assert PDF_URL in await stored_input(db, "b")

    @pytest.mark.usefixtures("offload")
    async def test_a_lone_surrogate_stays_escaped(self, db: DbSessionFactory) -> None:
        """Written out raw it is not UTF-8, and the span could not be stored at all."""
        request = json.loads(call_llm_request())
        request["contents"][0]["parts"].insert(0, {"text": "cut off \ud83d"})
        await ingest(db, span("a", json.dumps(request)))
        value = await stored_input(db, "a")
        assert PDF_URL in value
        assert "cut off \\ud83d" in value

    @pytest.mark.usefixtures("offload")
    async def test_files_stored_before_a_store_failure_stay_stored(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(span_media, "put_media", _refusing_png)
        png = base64.b64encode(PNG).decode()
        request = json.loads(call_llm_request())
        request["contents"][0]["parts"].append(
            {"inline_data": {"data": png, "mime_type": "image/png"}}
        )
        await ingest(db, span("a", json.dumps(request)))
        parts = json.loads(await stored_input(db, "a"))["contents"][0]["parts"]
        assert parts[1]["inline_data"][MEDIA_URL_KEY] == PDF_URL
        assert parts[2]["inline_data"]["data"] == png
        row = await media_row(db, PDF_SHA)
        assert row is not None and row.referenced_at is not None

    @pytest.mark.usefixtures("offload")
    async def test_files_after_a_store_failure_stay_inline_too(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The store just failed, so the rest of the span does not wait on it."""
        monkeypatch.setattr(span_media, "put_media", _refusing_png)
        png = base64.b64encode(PNG).decode()
        request = json.loads(call_llm_request())
        request["contents"][0]["parts"].insert(
            1, {"inline_data": {"data": png, "mime_type": "image/png"}}
        )
        await ingest(db, span("a", json.dumps(request)))
        assert await stored_input(db, "a") == json.dumps(request)
        assert await media_row(db, PDF_SHA) is None

    @pytest.mark.usefixtures("offload")
    async def test_bytes_phoenix_does_not_store_stay_inline(self, db: DbSessionFactory) -> None:
        audio = base64.b64encode(b"RIFF" + b"\x01" * 60_000).decode()
        request = call_llm_request(audio, "audio/wav")
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request

    @pytest.mark.usefixtures("offload")
    async def test_a_failing_store_keeps_the_media_inline_and_pauses(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def failing(content: bytes, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("bucket unavailable")

        monkeypatch.setattr(span_media, "put_media", failing)
        request = call_llm_request()
        await ingest(db, span("a", request), span("b", request))
        assert await stored_input(db, "a") == request
        assert await stored_input(db, "b") == request
        # The second span did not wait on a store that had just failed.
        assert calls == 1

    @pytest.mark.usefixtures("offload")
    async def test_a_slow_store_times_out_and_keeps_the_media_inline(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def slow(content: bytes, **kwargs: Any) -> None:
            await asyncio.sleep(5)

        monkeypatch.setattr(span_media, "put_media", slow)
        monkeypatch.setattr(span_media, "_STORE_TIMEOUT_SECONDS", 0.05)
        request = call_llm_request()
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request

    @pytest.mark.usefixtures("offload")
    async def test_a_failure_recording_the_media_keeps_the_span_inline(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def failing(session: Any, media: StoredMedia, **kwargs: Any) -> None:
            raise RuntimeError("lock timeout")

        monkeypatch.setattr(span_media, "record_media", failing)
        request = call_llm_request()
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request
        assert await media_row(db, PDF_SHA) is None


@pytest.mark.usefixtures("offload")
class TestUploadsHappenOutsideTheInsertTransaction:
    async def test_every_upload_finishes_before_the_batch_inserts(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The insert loop holds one transaction across a batch. Uploading inside it would
        hold the connection (and SQLite's write lock) through each upload in turn.
        """
        events: list[str] = []

        async def recording_put(content: bytes, **kwargs: Any) -> Optional[StoredMedia]:
            events.append("upload")
            return await put_media(content, **kwargs)

        async def recording_insert(*args: Any, **kwargs: Any) -> Any:
            events.append("insert")
            return await upstream_insert_span(*args, **kwargs)

        monkeypatch.setattr(span_media, "put_media", recording_put)
        monkeypatch.setattr(bulk_inserter, "insert_span", recording_insert)
        await ingest(
            db, span("a", call_llm_request()), span("b", call_llm_request(PNG_B64, "image/png"))
        )
        assert events == ["upload", "upload", "insert", "insert"]


class TestWiring:
    async def test_the_receivers_are_handed_the_offloading_enqueue(
        self, db: DbSessionFactory
    ) -> None:
        """Were upstream to rename `_enqueue_span`, the override would silently do nothing."""
        inserter = MediaOffloadingBulkInserter(
            db,
            event_queue=_Events(),
            span_cost_calculator=None,
        )
        inserter._bulk_insert = _idle  # type: ignore[method-assign]
        _, enqueue_span, _ = await inserter.__aenter__()
        try:
            assert getattr(enqueue_span, "__func__", None) is (
                MediaOffloadingBulkInserter._enqueue_span
            )
        finally:
            await inserter.__aexit__()

    def test_the_app_builds_the_offloading_inserter_by_default(self) -> None:
        """`create_app` builds it unless a caller, such as the test suite, passes its own."""
        source = inspect.getsource(app.create_app)
        assert "bulk_inserter_factory or MediaOffloadingBulkInserter" in source


@pytest.mark.usefixtures("offload")
class TestTheReceiverDoesNotWaitForUploads:
    """gRPC and `POST /v1/spans` answer their client only once the span is queued."""

    async def test_queueing_returns_while_the_upload_is_still_running(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = asyncio.Event()

        async def held(content: bytes, **kwargs: Any) -> Optional[StoredMedia]:
            await release.wait()
            return await put_media(content, **kwargs)

        monkeypatch.setattr(span_media, "put_media", held)
        inserter = inserter_for(db)
        _, enqueue_span, _ = await inserter.__aenter__()
        try:
            await asyncio.wait_for(enqueue_span(span("a", call_llm_request()), "project"), 1)
            assert not inserter._spans  # still uploading, so not queued yet
            release.set()
            await inserter._finish_uploads(timeout=30)
            await inserter._insert_spans(1)
        finally:
            await inserter.__aexit__()
        assert PDF_URL in await stored_input(db, "a")

    async def test_a_span_still_uploading_at_shutdown_is_queued_inline(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def hangs(content: bytes, **kwargs: Any) -> Optional[StoredMedia]:
            await asyncio.sleep(30)
            return None

        monkeypatch.setattr(span_media, "put_media", hangs)
        inserter = inserter_for(db, shutdown_drain_sec=0.1)
        _, enqueue_span, _ = await inserter.__aenter__()
        request = call_llm_request()
        await enqueue_span(span("a", request), "project")
        await inserter.__aexit__()
        assert [s.attributes["input"]["value"] for s, _ in inserter._spans] == [request]

    async def test_a_span_without_media_is_queued_directly(self, db: DbSessionFactory) -> None:
        inserter = inserter_for(db)
        _, enqueue_span, _ = await inserter.__aenter__()
        try:
            await enqueue_span(span("a", "plain text"), "project")
            assert len(inserter._spans) == 1
            assert not inserter._uploading
        finally:
            await inserter.__aexit__()


@pytest.mark.usefixtures("offload")
class TestRepeatedFiles:
    async def test_a_file_already_recorded_needs_no_database_write(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = 0

        async def counting(session: Any, media: StoredMedia, **kwargs: Any) -> None:
            nonlocal recorded
            recorded += 1
            await upstream_record_media(session, media, **kwargs)

        monkeypatch.setattr(span_media, "record_media", counting)
        await ingest(db, *(span(span_id, call_llm_request()) for span_id in ("a", "b", "c")))
        assert recorded == 1
        row = await media_row(db, PDF_SHA)
        assert row is not None and row.referenced_at is not None


@pytest.mark.usefixtures("offload")
class TestCamelCase:
    async def test_the_js_sdk_shape_moves_too(self, db: DbSessionFactory) -> None:
        """`inlineData` / `mimeType`, as the JS SDK and the REST API write a Gemini part."""
        request = json.dumps(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"inlineData": {"mimeType": "application/pdf", "data": PDF_B64}}],
                    }
                ]
            }
        )
        await ingest(db, span("a", request))
        part = json.loads(await stored_input(db, "a"))["contents"][0]["parts"][0]
        assert part == {"inlineData": {"mimeType": "application/pdf", MEDIA_URL_KEY: PDF_URL}}
