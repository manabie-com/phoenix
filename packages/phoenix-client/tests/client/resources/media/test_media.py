"""Tests for the fork-owned ``client.media`` resource."""

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from phoenix.client.helpers.prompt_media import MediaResolutionError
from phoenix.client.resources.media import AsyncMedia, FetchedMedia, Media

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SHA = "a" * 64

STORED = {
    "sha256": SHA,
    "media_type": "image/png",
    "size_bytes": len(PNG),
    "url": f"phoenix://media/{SHA}",
}


def _sync(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def _async(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


class TestUpload:
    def test_uploads_bytes_as_multipart(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["method"] = request.method
            seen["body"] = request.content
            return httpx.Response(200, json={"data": STORED})

        result = Media(_sync(handler)).upload(PNG, file_name="cat.png")

        assert result == STORED
        assert seen["path"] == "/v1/media"
        assert seen["method"] == "POST"
        # Multipart, under the field name the server's UploadFile parameter uses.
        assert b'name="file"' in seen["body"]
        assert b"cat.png" in seen["body"]
        assert PNG in seen["body"]

    def test_uploads_a_path_and_derives_the_file_name(self, tmp_path: Path) -> None:
        image = tmp_path / "kitten.png"
        image.write_bytes(PNG)
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(200, json={"data": STORED})

        assert Media(_sync(handler)).upload(image) == STORED
        assert b"kitten.png" in seen["body"]

    def test_uploads_a_data_uri(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(200, json={"data": STORED})

        uri = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"
        assert Media(_sync(handler)).upload(uri) == STORED
        # The bytes are sent decoded, not as the base64 text of the URI.
        assert PNG in seen["body"]

    def test_declares_octet_stream_so_the_server_types_from_content(self) -> None:
        """The server reads the type off the bytes; a client guess would be a
        second source of truth that nothing reads."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(200, json={"data": STORED})

        Media(_sync(handler)).upload(PNG, file_name="cat.png")
        assert b"Content-Type: application/octet-stream" in seen["body"]

    def test_unresolvable_media_raises_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("should not have sent a request")

        with pytest.raises(MediaResolutionError):
            Media(_sync(handler)).upload(object())

    def test_server_rejection_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(415, json={"detail": "Unsupported media."})

        with pytest.raises(httpx.HTTPStatusError):
            Media(_sync(handler)).upload(b"not an image", file_name="x.bin")


class TestImportFromUrl:
    def test_posts_the_url_in_the_wrapped_body(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = request.content
            return httpx.Response(200, json={"data": STORED})

        result = Media(_sync(handler)).import_from_url("https://example.com/cat.png")

        assert result == STORED
        assert seen["path"] == "/v1/media/import"
        assert json.loads(seen["json"]) == {"data": {"url": "https://example.com/cat.png"}}

    def test_does_not_fetch_the_url_itself(self) -> None:
        """The point of import is that the *server* fetches. A client-side fetch
        would defeat it for hosts only the server can reach."""
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"data": STORED})

        Media(_sync(handler)).import_from_url("https://example.com/cat.png")
        assert paths == ["/v1/media/import"]

    def test_unfetchable_url_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "Could not fetch the image."})

        with pytest.raises(httpx.HTTPStatusError):
            Media(_sync(handler)).import_from_url("https://example.com/gone.png")


class TestGet:
    def test_returns_bytes_and_media_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == f"/v1/media/{SHA}"
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

        result = Media(_sync(handler)).get(SHA)

        assert isinstance(result, FetchedMedia)
        assert result.content == PNG
        assert result.media_type == "image/png"

    def test_unpacks_as_a_pair(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

        content, media_type = Media(_sync(handler)).get(SHA)
        assert (content, media_type) == (PNG, "image/png")

    def test_strips_content_type_parameters(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=PNG, headers={"content-type": "image/png; charset=binary"}
            )

        assert Media(_sync(handler)).get(SHA).media_type == "image/png"

    def test_falls_back_to_sniffing_when_no_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=PNG, headers={"content-type": ""})

        assert Media(_sync(handler)).get(SHA).media_type == "image/png"

    def test_the_web_app_served_for_a_wrong_digest_is_rejected(self) -> None:
        """Phoenix serves its SPA for unmatched paths, so a wrong digest can come
        back as HTML with a healthy 200. Accepting it would hand a model a web
        page as an image."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"<!doctype html><html>", headers={"content-type": "text/html"}
            )

        with pytest.raises(MediaResolutionError):
            Media(_sync(handler)).get(SHA)

    def test_missing_media_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "No media found."})

        with pytest.raises(httpx.HTTPStatusError):
            Media(_sync(handler)).get(SHA)

    def test_a_digest_that_would_escape_the_path_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("should not have sent a request")

        with pytest.raises(ValueError, match="Cannot encode string"):
            Media(_sync(handler)).get("../../etc/passwd")


class TestAsyncMedia:
    @pytest.mark.anyio
    async def test_upload(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = request.content
            return httpx.Response(200, json={"data": STORED})

        result = await AsyncMedia(_async(handler)).upload(PNG, file_name="cat.png")

        assert result == STORED
        assert seen["path"] == "/v1/media"
        assert PNG in seen["body"]

    @pytest.mark.anyio
    async def test_import_from_url(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = request.content
            return httpx.Response(200, json={"data": STORED})

        result = await AsyncMedia(_async(handler)).import_from_url("https://example.com/cat.png")

        assert result == STORED
        assert seen["path"] == "/v1/media/import"
        assert json.loads(seen["json"]) == {"data": {"url": "https://example.com/cat.png"}}

    @pytest.mark.anyio
    async def test_get(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == f"/v1/media/{SHA}"
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

        content, media_type = await AsyncMedia(_async(handler)).get(SHA)
        assert (content, media_type) == (PNG, "image/png")

    @pytest.mark.anyio
    async def test_upload_cannot_resolve_phoenix_hosted_media(self) -> None:
        """The shared resolver takes a synchronous client, so the async upload
        cannot follow a `phoenix://` reference. Documented on the method; asserted
        here so the day it starts working is a test failure, not a surprise."""

        async def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("should not have sent a request")

        with pytest.raises(MediaResolutionError, match="needs a client to fetch"):
            await AsyncMedia(_async(handler)).upload(f"phoenix://media/{SHA}")

    @pytest.mark.anyio
    async def test_get_rejects_the_web_app(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"<!doctype html>", headers={"content-type": "text/html"}
            )

        with pytest.raises(MediaResolutionError):
            await AsyncMedia(_async(handler)).get(SHA)


class TestClientWiring:
    def test_client_exposes_media(self) -> None:
        from phoenix.client import AsyncClient, Client

        assert isinstance(Client(base_url="http://test").media, Media)
        assert isinstance(AsyncClient(base_url="http://test").media, AsyncMedia)

    def test_media_uses_the_clients_own_http_client(self) -> None:
        """Auth headers and base_url have to come from the client the caller
        configured, not a fresh one."""
        from phoenix.client import Client

        client = Client(base_url="http://test", api_key="secret")
        assert client.media._client is client._client
