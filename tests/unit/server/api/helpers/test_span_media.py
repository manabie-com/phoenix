"""
Inline media on an incoming span goes to the media store, and the span keeps a reference.

A Gemini call grading a PDF records the whole paper, base64-encoded, in ``input.value``
— on the ``invocation`` span and again on every ``call_llm`` span under it. On staging
that made the ``spans`` table 44 GB. These tests drive spans through the bulk inserter,
the path every ingested span takes, so they also prove the hook is wired in.

The one thing that must never happen is a span losing its media: whatever cannot be
stored stays inline, byte for byte.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from sqlalchemy import select

from phoenix.db import models
from phoenix.db.bulk_inserter import BulkInserter
from phoenix.server.api.helpers import span_media
from phoenix.server.api.helpers.dataset_example_media import MEDIA_URL_KEY
from phoenix.server.api.helpers.media import StoredMedia, put_media
from phoenix.server.api.helpers.media_storage import media_store
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


async def ingest(db: DbSessionFactory, *spans: Span) -> None:
    """Insert spans the way the server does, through the bulk inserter's own loop."""
    inserter = BulkInserter(
        db,
        event_queue=_Events(),
        span_cost_calculator=None,  # type: ignore[arg-type]  # no span here is priced
        initial_batch_of_spans=[(s, "project") for s in spans],
    )
    await inserter._insert_spans(len(spans))


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
    async def test_one_file_the_store_refuses_does_not_hold_back_the_others(
        self, db: DbSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refuses_png(content: bytes) -> Optional[StoredMedia]:
            if content.startswith(b"\x89PNG"):
                raise RuntimeError("bucket unavailable")
            return await put_media(content)

        monkeypatch.setattr(span_media, "put_media", refuses_png)
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

        async def failing(content: bytes) -> None:
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
        async def slow(content: bytes) -> None:
            await asyncio.sleep(5)

        monkeypatch.setattr(span_media, "put_media", slow)
        monkeypatch.setattr(span_media, "_STORE_TIMEOUT_SECONDS", 0.05)
        request = call_llm_request()
        await ingest(db, span("a", request))
        assert await stored_input(db, "a") == request
