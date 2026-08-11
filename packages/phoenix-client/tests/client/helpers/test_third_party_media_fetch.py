"""A third-party host must never see the caller's Phoenix credentials.

`resolve_media` is handed the caller's Phoenix client so that Phoenix-hosted media
resolves — a relative URL needs the `base_url`, and the request needs the auth
header. httpx applies client-level headers to *every* host, though, so reusing that
client for `https://example.com/cat.png` would hand example.com a working Phoenix
API key. `client.media.upload()` makes that reachable from a documented argument,
which is what these tests pin down.

The split is by origin, not by shape: an absolute URL on Phoenix's own origin still
goes through the client, because that is where the credential belongs.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest

from phoenix.client.helpers.prompt_media import (
    addresses_phoenix,
    media_file_name,
    resolve_media,
)

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
SHA = "a" * 64


def phoenix_client(handler: Any = None) -> httpx.Client:
    """A credentialed client whose transport fails the test if a fetch reaches it."""

    def refuse(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"the Phoenix client was used for {request.url}")

    return httpx.Client(
        transport=httpx.MockTransport(handler or refuse),
        base_url="http://phoenix.local",
        headers={"Authorization": "Bearer phoenix-api-key"},
    )


def serving_png(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})


@pytest.fixture
def recorded_get(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Record bare `httpx.get` calls instead of performing them."""
    calls: list[tuple[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            content=PNG_BYTES,
            headers={"content-type": "image/png"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


class TestThirdPartyUrls:
    def test_are_not_fetched_through_the_phoenix_client(
        self, recorded_get: list[tuple[str, Any]]
    ) -> None:
        # The client's transport raises if it is used at all.
        with phoenix_client() as client:
            data, media_type = resolve_media("https://example.com/cat.png", client=client)

        assert data == PNG_BYTES
        assert media_type == "image/png"
        assert [url for url, _ in recorded_get] == ["https://example.com/cat.png"]

    def test_carry_no_phoenix_authorization_header(
        self, recorded_get: list[tuple[str, Any]]
    ) -> None:
        with phoenix_client() as client:
            resolve_media("https://example.com/cat.png", client=client)

        _, kwargs = recorded_get[0]
        assert "headers" not in kwargs

    def test_are_still_fetched_when_no_client_was_passed(
        self, recorded_get: list[tuple[str, Any]]
    ) -> None:
        data, _ = resolve_media("https://example.com/cat.png")
        assert data == PNG_BYTES
        assert recorded_get[0][1] == {"follow_redirects": True}

    def test_a_client_with_no_base_url_is_not_an_origin_to_match(
        self, recorded_get: list[tuple[str, Any]]
    ) -> None:
        # A bare `httpx.Client()` names no Phoenix instance, so nothing is same-origin
        # with it and its headers stay put.
        with httpx.Client(
            transport=httpx.MockTransport(serving_png),
            headers={"Authorization": "Bearer phoenix-api-key"},
        ) as client:
            resolve_media("https://example.com/cat.png", client=client)

        assert [url for url, _ in recorded_get] == ["https://example.com/cat.png"]


class TestSignedUrlsDoNotLeakIntoTheFileName:
    """A signed URL carries its credential in the query string, and the derived
    name is persisted by Phoenix and sent to providers beside a document part."""

    @pytest.mark.parametrize(
        "reference,expected",
        [
            ("https://host/cat.png?token=SECRET", "cat.png"),
            ("https://host/doc.pdf?X-Amz-Signature=deadbeef", "doc.pdf"),
            ("https://host/cat.png#fragment", "cat.png"),
            ("https://host/a/b/cat.png?x=1&y=2", "cat.png"),
            ("https://host/cat.png", "cat.png"),  # unchanged when there is no query
            ("/local/path/cat.png", "cat.png"),  # a plain path is not a URL
        ],
    )
    def test_query_and_fragment_are_not_part_of_the_name(
        self, reference: str, expected: str
    ) -> None:
        assert media_file_name(reference, "image/png") == expected

    def test_the_upload_does_not_put_a_token_on_the_wire(
        self, recorded_get: list[tuple[str, Any]]
    ) -> None:
        from phoenix.client.resources.media import Media

        sent: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request.content)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "sha256": SHA,
                        "media_type": "image/png",
                        "size_bytes": len(PNG_BYTES),
                        "url": f"phoenix://media/{SHA}",
                    }
                },
            )

        with phoenix_client(handler) as client:
            Media(client).upload("https://host/cat.png?token=SECRET-abc123")

        assert b'filename="cat.png"' in sent[0]
        assert b"SECRET-abc123" not in sent[0]


class TestPhoenixUrlsStillUseTheClient:
    """The credential has to keep reaching Phoenix, or stored media stops resolving."""

    def test_a_phoenix_media_reference(self) -> None:
        seen: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers)
            return serving_png(request)

        with phoenix_client(handler) as client:
            data, _ = resolve_media(f"phoenix://media/{SHA}", client=client)

        assert data == PNG_BYTES
        assert seen[0]["authorization"] == "Bearer phoenix-api-key"

    def test_a_phoenix_relative_url(self) -> None:
        with phoenix_client(serving_png) as client:
            data, _ = resolve_media(f"/v1/media/{SHA}", client=client)
        assert data == PNG_BYTES

    def test_an_absolute_url_on_phoenixs_own_origin(self) -> None:
        with phoenix_client(serving_png) as client:
            data, _ = resolve_media(f"http://phoenix.local/v1/media/{SHA}", client=client)
        assert data == PNG_BYTES


class TestAddressesPhoenix:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("v1/media/abc", True),
            ("/v1/media/abc", True),
            ("http://phoenix.local/v1/media/abc", True),
            ("http://phoenix.local:80/v1/media/abc", True),  # the default port is implied
            ("http://PHOENIX.LOCAL/v1/media/abc", True),  # hosts are case-insensitive
            ("http://phoenix.local:6006/v1/media/abc", False),  # a different port
            ("https://phoenix.local/v1/media/abc", False),  # a different scheme
            ("http://phoenix.local.evil.test/v1/media/abc", False),  # a prefix is not a match
            ("https://example.com/cat.png", False),
        ],
    )
    def test_origin_matching(self, url: str, expected: bool) -> None:
        with httpx.Client(base_url="http://phoenix.local") as client:
            assert addresses_phoenix(url, client) is expected


class TestUploadDoesNotLeakTheKey:
    """`media.upload(<url>)` is the documented argument that reaches all of this."""

    def test_upload_of_a_third_party_url(self, recorded_get: list[tuple[str, Any]]) -> None:
        from phoenix.client.resources.media import Media

        stored = {
            "sha256": SHA,
            "media_type": "image/png",
            "size_bytes": len(PNG_BYTES),
            "url": f"phoenix://media/{SHA}",
        }
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": stored})

        with phoenix_client(handler) as client:
            assert Media(client).upload("https://example.com/cat.png") == stored

        # Fetched off-client, then uploaded on it.
        assert [url for url, _ in recorded_get] == ["https://example.com/cat.png"]
        assert [str(request.url) for request in seen] == ["http://phoenix.local/v1/media"]
        assert seen[0].headers["authorization"] == "Bearer phoenix-api-key"
