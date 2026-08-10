"""A fetch that lands on a web page must not be accepted as media.

A 200 is not proof the URL was right. Phoenix serves its single-page app for any
unmatched path, and a string like `/scans/marking.png` reaches the fetch branch as
a Phoenix-relative URL the moment a client is passed — so a typo in a path comes
back as HTML, with a healthy status, and used to be handed to the model as an
image. The call succeeded, which is what made it dangerous.

This matters more now that a media variable is optional: "supplied but
unresolvable must raise" is the only thing left standing between a mistyped
reference and a corrupted run.

The absolute URLs below are on the mock client's own origin deliberately.
`fetch_url` hands a URL to the passed client only when it addresses that client's
Phoenix instance, so a third-party URL would bypass the mock transport and reach
the network. What happens to a third-party URL is `test_third_party_media_fetch.py`.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

from __future__ import annotations

import base64

import httpx
import pytest

from phoenix.client.helpers.prompt_media import MediaResolutionError, resolve_media

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
SPA_HTML = b"<!doctype html><html><head><title>Phoenix</title></head><body></body></html>"


def client_returning(content: bytes, content_type: str) -> httpx.Client:
    """A client whose every response carries the given body and type."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://phoenix.local")


class TestAWebPageIsNotMedia:
    def test_the_spa_fallback_is_rejected(self) -> None:
        # The exact shape of the bug: a path that does not exist locally is fetched
        # from Phoenix, which answers 200 with the app shell.
        with client_returning(SPA_HTML, "text/html; charset=utf-8") as client:
            with pytest.raises(MediaResolutionError, match="not media"):
                resolve_media("/no/such/marking.png", client=client)

    def test_the_error_names_what_came_back(self) -> None:
        with client_returning(SPA_HTML, "text/html; charset=utf-8") as client:
            with pytest.raises(MediaResolutionError, match="text/html"):
                resolve_media("/no/such/marking.png", client=client)

    def test_a_json_error_body_is_rejected(self) -> None:
        with client_returning(b'{"detail":"not found"}', "application/json") as client:
            with pytest.raises(MediaResolutionError, match="not media"):
                resolve_media("http://phoenix.local/cat.png", client=client)

    def test_an_http_url_serving_a_page_is_rejected(self) -> None:
        with client_returning(SPA_HTML, "text/html") as client:
            with pytest.raises(MediaResolutionError, match="not media"):
                resolve_media("http://phoenix.local/gone.png", client=client)

    def test_a_declared_media_type_does_not_launder_a_web_page(self) -> None:
        # A template claiming the reference is a PNG does not make the HTML one.
        with client_returning(SPA_HTML, "text/html") as client:
            with pytest.raises(MediaResolutionError, match="not media"):
                resolve_media("http://phoenix.local/cat.png", media_type="image/png", client=client)


class TestRealMediaStillResolves:
    """The check is narrow: only unrecognisable bytes declared as text are refused."""

    def test_an_image_resolves(self) -> None:
        with client_returning(PNG_BYTES, "image/png") as client:
            data, media_type = resolve_media("http://phoenix.local/cat.png", client=client)
        assert data == PNG_BYTES
        assert media_type == "image/png"

    def test_an_image_served_as_text_is_still_an_image(self) -> None:
        # A misconfigured host serving a real PNG as text/plain is a working image,
        # and the signature is better evidence than the header.
        with client_returning(PNG_BYTES, "text/plain") as client:
            data, _ = resolve_media("http://phoenix.local/cat.png", client=client)
        assert data == PNG_BYTES

    def test_a_pdf_resolves(self) -> None:
        pdf = b"%PDF-1.4\n" + b"\x00" * 60
        with client_returning(pdf, "application/pdf") as client:
            data, media_type = resolve_media("http://phoenix.local/doc.pdf", client=client)
        assert data == pdf
        assert media_type == "application/pdf"

    def test_unrecognisable_bytes_from_a_binary_host_are_kept(self) -> None:
        # No signature and no text claim — an exotic format, not an error page.
        blob = b"\x01\x02\x03\x04" * 8
        with client_returning(blob, "application/octet-stream") as client:
            data, _ = resolve_media("http://phoenix.local/thing.bin", client=client)
        assert data == blob

    def test_phoenix_hosted_media_resolves(self) -> None:
        with client_returning(PNG_BYTES, "image/png") as client:
            data, media_type = resolve_media(f"phoenix://media/{'a' * 64}", client=client)
        assert data == PNG_BYTES
        assert media_type == "image/png"
