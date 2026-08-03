from __future__ import annotations

import base64
import hashlib
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

import httpx
import pytest
from sqlalchemy import func, select

from phoenix.db import models
from phoenix.server.api.helpers.media_storage import media_store
from phoenix.server.api.routers.v1.media import _is_public_address
from phoenix.server.types import DbSessionFactory
from tests.unit.media_store_fixtures import isolated_media_store  # noqa: F401

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
_PNG_DIGEST = hashlib.sha256(_PNG_BYTES).hexdigest()
_GIF_BYTES = b"GIF89a" + b"\x00" * 10
_WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
_SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer<<>>\n%%EOF\n"
_PDF_DIGEST = hashlib.sha256(_PDF_BYTES).hexdigest()


def _stub_stream(response: httpx.Response) -> Callable[..., Any]:
    """
    Stand in for `httpx.AsyncClient.stream` with a canned response.

    The import endpoint streams rather than buffering, so a stub has to hand back an
    async context manager rather than an awaited response.
    """

    @asynccontextmanager
    async def stream(
        self: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> AsyncIterator[httpx.Response]:
        yield response

    return stream


class _CountingStream(httpx.AsyncByteStream):
    """
    A body delivered in chunks, counting how many were actually pulled.

    Lets a test prove the size cap stops the read rather than merely rejecting an
    oversized body once all of it is already in memory.
    """

    def __init__(self, chunk: bytes, chunks: int) -> None:
        self._chunk = chunk
        self._chunks = chunks
        self.chunks_read = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self._chunks):
            self.chunks_read += 1
            yield self._chunk


class _FakeNetworkStream:
    """Reports the address a response was supposedly served from."""

    def __init__(self, server_addr: Optional[tuple[str, int]]) -> None:
        self._server_addr = server_addr

    def get_extra_info(self, name: str) -> Any:
        return self._server_addr if name == "server_addr" else None


def _allow_any_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Neutralise the name-based egress guard.

    It rejects every locally reachable address, so the happy path cannot be exercised
    against a test server. The guard has its own tests; here the fetch is stubbed and
    everything after it is real.
    """
    monkeypatch.setattr(
        "phoenix.server.api.routers.v1.media._reject_unsafe_host",
        lambda host: None,
    )


class TestUploadMedia:
    async def test_stores_png_and_returns_reference(
        self,
        httpx_client: httpx.AsyncClient,
        db: DbSessionFactory,
    ) -> None:
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("cat.png", _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["sha256"] == _PNG_DIGEST
        assert data["media_type"] == "image/png"
        assert data["size_bytes"] == len(_PNG_BYTES)
        assert data["url"] == f"phoenix://media/{_PNG_DIGEST}"

        async with db() as session:
            media_file = await session.get(models.MediaFile, _PNG_DIGEST)
            assert media_file is not None
            assert media_file.media_type == "image/png"
        # The row is metadata only; the bytes are in the store. Media would be the one
        # large binary in the schema, and bytes in the database inflate every backup.
        assert await media_store().get(_PNG_DIGEST) == _PNG_BYTES

    @pytest.mark.parametrize(
        "content,expected_media_type",
        [
            pytest.param(_PNG_BYTES, "image/png", id="png"),
            pytest.param(b"\xff\xd8\xff\xe0 jpeg", "image/jpeg", id="jpeg"),
            pytest.param(_GIF_BYTES, "image/gif", id="gif"),
            pytest.param(_WEBP_BYTES, "image/webp", id="webp"),
        ],
    )
    async def test_detects_media_type_from_content(
        self,
        httpx_client: httpx.AsyncClient,
        content: bytes,
        expected_media_type: str,
    ) -> None:
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("upload.bin", content, "application/octet-stream")},
        )
        assert response.status_code == 200
        assert response.json()["data"]["media_type"] == expected_media_type

    async def test_ignores_declared_content_type(
        self,
        httpx_client: httpx.AsyncClient,
    ) -> None:
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("cat.jpg", _PNG_BYTES, "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.json()["data"]["media_type"] == "image/png"

    async def test_deduplicates_identical_uploads(
        self,
        httpx_client: httpx.AsyncClient,
        db: DbSessionFactory,
    ) -> None:
        first = await httpx_client.post(
            "v1/media",
            files={"file": ("a.png", _PNG_BYTES, "image/png")},
        )
        second = await httpx_client.post(
            "v1/media",
            files={"file": ("b.png", _PNG_BYTES, "image/png")},
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["data"]["sha256"] == second.json()["data"]["sha256"]

        async with db() as session:
            count = await session.scalar(select(func.count(models.MediaFile.sha256)))
        assert count == 1

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(_SVG_BYTES, id="svg"),
            pytest.param(b"just some text", id="text"),
            pytest.param(b"", id="empty"),
        ],
    )
    async def test_rejects_unsupported_content(
        self,
        httpx_client: httpx.AsyncClient,
        content: bytes,
    ) -> None:
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("upload.png", content, "image/png")},
        )
        assert response.status_code == 415

    async def test_stores_a_pdf(
        self,
        httpx_client: httpx.AsyncClient,
        db: DbSessionFactory,
    ) -> None:
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("statement.pdf", _PDF_BYTES, "application/pdf")},
        )
        assert response.status_code == 200
        assert response.json()["data"]["media_type"] == "application/pdf"

        async with db() as session:
            stored = await session.get(models.MediaFile, _PDF_DIGEST)
        assert stored is not None
        assert stored.media_type == "application/pdf"

    async def test_remembers_the_uploaded_name(
        self,
        httpx_client: httpx.AsyncClient,
        db: DbSessionFactory,
    ) -> None:
        """Some providers require a name to carry a document."""
        await httpx_client.post(
            "v1/media",
            files={"file": ("statement.pdf", _PDF_BYTES, "application/pdf")},
        )
        async with db() as session:
            stored = await session.get(models.MediaFile, _PDF_DIGEST)
        assert stored is not None
        assert stored.file_name == "statement.pdf"

    async def test_still_rejects_a_pdf_claiming_to_be_one(
        self,
        httpx_client: httpx.AsyncClient,
    ) -> None:
        """The declared type is ignored; only the leading bytes decide."""
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("fake.pdf", b"not really a pdf", "application/pdf")},
        )
        assert response.status_code == 415

    async def test_rejects_media_over_size_limit(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PHOENIX_MAX_MEDIA_BYTES", str(len(_PNG_BYTES) - 1))
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("cat.png", _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 413

    async def test_accepts_media_at_size_limit(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PHOENIX_MAX_MEDIA_BYTES", str(len(_PNG_BYTES)))
        response = await httpx_client.post(
            "v1/media",
            files={"file": ("cat.png", _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 200


class TestGetMedia:
    @pytest.fixture
    async def stored_png(self, db: DbSessionFactory) -> None:
        """Metadata in the database, bytes in the store — how media is stored now."""
        await media_store().put(_PNG_DIGEST, _PNG_BYTES, media_type="image/png")
        async with db() as session:
            session.add(
                models.MediaFile(
                    sha256=_PNG_DIGEST,
                    media_type="image/png",
                    size_bytes=len(_PNG_BYTES),
                )
            )

    async def test_serves_stored_bytes(
        self,
        httpx_client: httpx.AsyncClient,
        stored_png: None,
    ) -> None:
        response = await httpx_client.get(f"v1/media/{_PNG_DIGEST}")
        assert response.status_code == 200
        assert response.content == _PNG_BYTES
        assert response.headers["content-type"] == "image/png"

    async def test_sets_hardening_and_cache_headers(
        self,
        httpx_client: httpx.AsyncClient,
        stored_png: None,
    ) -> None:
        response = await httpx_client.get(f"v1/media/{_PNG_DIGEST}")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert "immutable" in response.headers["cache-control"]
        assert response.headers["content-disposition"].startswith("inline")

    async def test_returns_404_for_unknown_digest(
        self,
        httpx_client: httpx.AsyncClient,
    ) -> None:
        response = await httpx_client.get(f"v1/media/{'d' * 64}")
        assert response.status_code == 404

    async def test_returns_404_when_the_row_outlives_its_bytes(
        self,
        httpx_client: httpx.AsyncClient,
        db: DbSessionFactory,
    ) -> None:
        """
        The row records that media exists; the store holds it. A row whose bytes the
        sweeper already took is a 404, not an empty 200.
        """
        async with db() as session:
            session.add(
                models.MediaFile(
                    sha256=_PNG_DIGEST,
                    media_type="image/png",
                    size_bytes=len(_PNG_BYTES),
                )
            )

        response = await httpx_client.get(f"v1/media/{_PNG_DIGEST}")

        assert response.status_code == 404
        assert "no longer stored" in response.text

    @pytest.mark.parametrize(
        "digest",
        [
            pytest.param("tooshort", id="short"),
            pytest.param("A" * 64, id="uppercase"),
            pytest.param("z" * 64, id="non-hex"),
        ],
    )
    async def test_rejects_malformed_digest(
        self,
        httpx_client: httpx.AsyncClient,
        digest: str,
    ) -> None:
        response = await httpx_client.get(f"v1/media/{digest}")
        assert response.status_code == 422

    async def test_round_trips_an_upload(
        self,
        httpx_client: httpx.AsyncClient,
    ) -> None:
        upload = await httpx_client.post(
            "v1/media",
            files={"file": ("cat.png", _PNG_BYTES, "image/png")},
        )
        sha256 = upload.json()["data"]["sha256"]
        download = await httpx_client.get(f"v1/media/{sha256}")
        assert download.status_code == 200
        assert download.content == _PNG_BYTES


class TestImportMediaFromUrl:
    async def test_rejects_a_non_http_url(self, httpx_client: httpx.AsyncClient) -> None:
        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "file:///etc/passwd"}}
        )
        assert response.status_code == 422
        assert "http or https" in response.text

    @pytest.mark.parametrize(
        "url",
        [
            pytest.param("http://localhost/cat.png", id="loopback-name"),
            pytest.param("http://127.0.0.1/cat.png", id="loopback-ip"),
            pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
            pytest.param("http://10.0.0.5/cat.png", id="private-10"),
            pytest.param("http://192.168.1.10/cat.png", id="private-192"),
            pytest.param("http://[::1]/cat.png", id="ipv6-loopback"),
        ],
    )
    async def test_rejects_hosts_off_the_public_internet(
        self,
        httpx_client: httpx.AsyncClient,
        url: str,
    ) -> None:
        """
        Fetching a caller-supplied URL server-side would otherwise reach cloud
        metadata, private networks, and loopback services.
        """
        response = await httpx_client.post("v1/media/import", json={"data": {"url": url}})
        assert response.status_code == 422
        assert "non-public address" in response.text

    async def test_rejects_a_host_that_does_not_resolve(
        self,
        httpx_client: httpx.AsyncClient,
    ) -> None:
        response = await httpx_client.post(
            "v1/media/import",
            json={"data": {"url": "http://this-host-does-not-exist.invalid/cat.png"}},
        )
        assert response.status_code == 422
        assert "Could not resolve" in response.text

    async def test_stores_a_fetched_image_and_dedupes_with_uploads(
        self,
        httpx_client: httpx.AsyncClient,
        db: DbSessionFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _allow_any_host(monkeypatch)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(
                    200,
                    content=_PNG_BYTES,
                    request=httpx.Request("GET", "https://example.com/cat.png"),
                )
            ),
        )

        response = await httpx_client.post(
            "v1/media/import",
            json={"data": {"url": "https://example.com/cat.png"}},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["sha256"] == _PNG_DIGEST
        assert data["media_type"] == "image/png"
        assert data["url"] == f"phoenix://media/{_PNG_DIGEST}"

        # An upload of the same bytes lands on the same row.
        upload = await httpx_client.post(
            "v1/media", files={"file": ("cat.png", _PNG_BYTES, "image/png")}
        )
        assert upload.json()["data"]["sha256"] == _PNG_DIGEST
        async with db() as session:
            assert await session.scalar(select(func.count(models.MediaFile.sha256))) == 1

    async def test_rejects_a_fetched_file_that_is_not_an_image(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _allow_any_host(monkeypatch)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(
                    200,
                    content=b"<html>not an image</html>",
                    request=httpx.Request("GET", "https://example.com/page.html"),
                )
            ),
        )
        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "https://example.com/page.html"}}
        )
        assert response.status_code == 415

    async def test_reports_a_failing_url(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _allow_any_host(monkeypatch)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(404, request=httpx.Request("GET", "https://example.com/missing.png"))
            ),
        )
        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "https://example.com/missing.png"}}
        )
        assert response.status_code == 422
        assert "returned 404" in response.text

    async def test_refuses_to_follow_a_redirect(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A redirect could land on a host the guard already rejected."""
        _allow_any_host(monkeypatch)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(
                    302,
                    headers={"location": "http://169.254.169.254/latest/meta-data/"},
                    request=httpx.Request("GET", "https://example.com/redirect.png"),
                )
            ),
        )
        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "https://example.com/redirect.png"}}
        )
        assert response.status_code == 422
        assert "redirects" in response.text

    async def test_stops_reading_once_the_body_exceeds_the_limit(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        The cap has to govern how much is read, not just what is accepted.

        httpx imposes no maximum response size, so buffering first would let a
        caller-named URL decide how much memory Phoenix allocates.
        """
        _allow_any_host(monkeypatch)
        monkeypatch.setenv("PHOENIX_MAX_MEDIA_BYTES", "1024")
        body = _CountingStream(chunk=b"\x00" * 256, chunks=4096)  # 1 MiB available
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(
                    200,
                    stream=body,
                    request=httpx.Request("GET", "https://example.com/huge.png"),
                )
            ),
        )

        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "https://example.com/huge.png"}}
        )

        assert response.status_code == 413
        # Five 256-byte chunks is the first total above 1024; anything near 4096 means
        # the whole body was pulled into memory before being rejected.
        assert body.chunks_read <= 8

    async def test_rejects_a_response_served_from_a_non_public_address(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        DNS rebinding: the name checked out, the connection did not.

        A record with a very short TTL can answer the guard's lookup with a public
        address and httpx's with a link-local one, so the peer is re-checked against
        the socket before any of the body is read.
        """
        _allow_any_host(monkeypatch)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(
                    200,
                    content=_PNG_BYTES,
                    extensions={
                        "network_stream": _FakeNetworkStream(("169.254.169.254", 80)),
                    },
                    request=httpx.Request("GET", "https://rebind.example.com/cat.png"),
                )
            ),
        )

        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "https://rebind.example.com/cat.png"}}
        )

        assert response.status_code == 422
        assert "non-public address" in response.text

    async def test_accepts_a_response_served_from_a_public_address(
        self,
        httpx_client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _allow_any_host(monkeypatch)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "stream",
            _stub_stream(
                httpx.Response(
                    200,
                    content=_PNG_BYTES,
                    extensions={"network_stream": _FakeNetworkStream(("93.184.216.34", 443))},
                    request=httpx.Request("GET", "https://example.com/cat.png"),
                )
            ),
        )

        response = await httpx_client.post(
            "v1/media/import", json={"data": {"url": "https://example.com/cat.png"}}
        )

        assert response.status_code == 200
        assert response.json()["data"]["sha256"] == _PNG_DIGEST


class TestIsPublicAddress:
    @pytest.mark.parametrize(
        "address",
        [
            pytest.param("127.0.0.1", id="loopback"),
            pytest.param("169.254.169.254", id="cloud-metadata"),
            pytest.param("10.0.0.5", id="private-10"),
            pytest.param("192.168.1.10", id="private-192"),
            pytest.param("172.16.0.1", id="private-172"),
            pytest.param("0.0.0.0", id="unspecified"),
            pytest.param("224.0.0.1", id="multicast"),
            pytest.param("::1", id="ipv6-loopback"),
            pytest.param("fe80::1", id="ipv6-link-local"),
            pytest.param("fd00::1", id="ipv6-unique-local"),
            pytest.param("not-an-address", id="unparseable"),
            pytest.param("fe80::1%en0", id="scoped-ipv6"),
        ],
    )
    def test_rejects_addresses_off_the_public_internet(self, address: str) -> None:
        assert _is_public_address(address) is False

    @pytest.mark.parametrize(
        "address",
        [
            pytest.param("93.184.216.34", id="ipv4"),
            pytest.param("8.8.8.8", id="public-resolver"),
            pytest.param("2606:2800:220:1:248:1893:25c8:1946", id="ipv6"),
        ],
    )
    def test_accepts_public_addresses(self, address: str) -> None:
        assert _is_public_address(address) is True
