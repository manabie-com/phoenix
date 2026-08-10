"""Client access to the content-addressed media store prompts reference.

Fork-owned. Media is a fork feature — ``POST /v1/media``, ``POST /v1/media/import``
and ``GET /v1/media/{sha256}`` are served by ``server/api/routers/v1/media.py``,
which upstream does not have — so the whole resource lives outside upstream's tree
and the only thing left in ``client.py`` is a mixin on the two client classes.

Reading bytes out of a reference is not reimplemented here.
:func:`phoenix.client.helpers.prompt_media.resolve_media` already turns every form
a prompt's image variable accepts into ``(bytes, media_type)``, so ``upload``
accepts exactly the same values a template variable does. Two implementations of
"what counts as an image" would drift, and the one users hit at run time is the
one they never tested against.
"""

from __future__ import annotations

from typing import Any, Optional, cast

import httpx

from phoenix.client.__generated__ import v1
from phoenix.client.helpers.prompt_media import (
    DEFAULT_MEDIA_TYPE,
    media_file_name,
    reject_non_media,
    resolve_media,
    sniff_media_type,
)
from phoenix.client.utils.encode_path_param import encode_path_param
from phoenix.client.utils.server_requirements import (
    AsyncServerVersionGuard,
    ServerVersionGuard,
)

__all__ = [
    "AsyncMedia",
    "AsyncMediaClientMixin",
    "FetchedMedia",
    "Media",
    "MediaClientMixin",
]


class FetchedMedia(tuple[bytes, str]):
    """The bytes of stored media and the type the server serves them as.

    A digest names content, not a format, so a caller that fetched by digest has
    no other way to learn what it got — and every onward use (writing a file,
    building a ``data:`` URI, handing it to a provider) needs the type. Returning
    both is what makes the result usable on its own.

    A tuple subclass rather than a dataclass so that it unpacks the way
    :data:`phoenix.client.helpers.prompt_media.ResolvedMedia` — the same pair,
    produced by the resolver — already does::

        content, media_type = client.media.get(sha256)
        content = client.media.get(sha256).content
    """

    __slots__ = ()

    def __new__(cls, content: bytes, media_type: str) -> FetchedMedia:
        return super().__new__(cls, (content, media_type))

    def __getnewargs__(self) -> tuple[bytes, str]:
        """Arguments to rebuild this from, for ``copy`` and ``pickle``.

        ``tuple`` supplies its own, which hands the whole pair back as a *single*
        argument — and this ``__new__`` takes two, so copying or pickling a result
        would fail with a ``TypeError`` naming the missing ``media_type``. Media
        bytes crossing a process boundary or a disk cache is ordinary, so the
        result has to survive the round trip.
        """
        return self[0], self[1]

    @property
    def content(self) -> bytes:
        """The raw media bytes."""
        return self[0]

    @property
    def media_type(self) -> str:
        """The IANA media type the server served the bytes as."""
        return self[1]


def _upload_files(content: bytes, file_name: str) -> dict[str, tuple[str, bytes, str]]:
    """Build the multipart payload for ``POST /v1/media``.

    The declared content type is deliberately not the one detection produced. The
    server types media from the bytes and ignores what the upload claims, so
    sending a guess here would only create a second, unread source of truth that a
    reader could mistake for authoritative.
    """
    return {"file": (file_name, content, "application/octet-stream")}


class Media:
    """Provides methods for storing and retrieving media used by prompts.

    Media is content-addressed: uploading the same bytes twice returns the same
    digest and stores one copy. A prompt template references stored media as
    ``phoenix://media/<sha256>``, which is what
    :attr:`~phoenix.client.resources.media.FetchedMedia` and the SDK converters
    resolve against.

    Examples:
        Storing an image and referencing it from a prompt::

            from pathlib import Path
            from phoenix.client import Client

            client = Client()

            stored = client.media.upload(Path("cat.png"))
            part = {
                "type": "image",
                "image": {"url": stored["url"], "media_type": stored["media_type"]},
            }

            # Or let the server fetch it once, so the prompt never depends on
            # the third-party host again.
            stored = client.media.import_from_url("https://example.com/cat.png")

            # And read it back.
            content, media_type = client.media.get(stored["sha256"])
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        _guard: ServerVersionGuard | None = None,
    ) -> None:
        self._client = client
        self._guard = _guard or ServerVersionGuard(client)

    def upload(
        self,
        media: Any,
        /,
        *,
        file_name: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> v1.MediaFileData:
        """
        Stores media and returns the digest and URL that reference it.

        Args:
            media (Any): The media to store. Accepts every form a prompt's image
                variable accepts: raw bytes, a ``bytearray`` or ``memoryview``, a
                base64 string, a ``data:`` URI, a :class:`~pathlib.Path`, a path
                string, an ``http(s)`` URL, or a ``MediaContent`` mapping.
            file_name (Optional[str]): The name to remember the media by. Derived
                from the reference when not given.
            media_type (Optional[str]): A declared media type, which wins over
                detection when deriving a file name. The stored type always comes
                from the server, which reads it from the bytes.

        Returns:
            v1.MediaFileData: The stored media's ``sha256``, ``media_type``,
            ``size_bytes``, and the ``phoenix://media/<sha256>`` ``url`` a prompt
            template references it by.

        Raises:
            MediaResolutionError: If the media could not be turned into bytes.
            httpx.HTTPStatusError: If the server rejected the media — 413 if it
                exceeds the configured size limit, 415 if it is not a supported
                format.

        Note:
            An ``http(s)`` URL is fetched **by this process** and the bytes are
            uploaded. Use :meth:`import_from_url` to have the server fetch it
            instead, which is what you want when only the server can reach the
            host, or when the download should not cross your network twice.

            A URL on another host is fetched without this client, so your Phoenix
            credentials are never sent to it — and neither is the client's proxy
            or timeout configuration. Pass the bytes yourself if you need those.

        Example::

            from pathlib import Path
            from phoenix.client import Client

            client = Client()

            stored = client.media.upload(Path("cat.png"))
            print(stored["url"])  # phoenix://media/<sha256>

            # Bytes, with a name to remember them by
            stored = client.media.upload(png_bytes, file_name="cat.png")
        """
        content, detected_type = resolve_media(media, media_type=media_type, client=self._client)
        name = file_name or media_file_name(media, detected_type)
        response = self._client.post("v1/media", files=_upload_files(content, name))
        response.raise_for_status()
        return cast(v1.UploadMediaResponseBody, response.json())["data"]

    def import_from_url(self, url: str, /) -> v1.MediaFileData:
        """
        Has the server fetch an image once and store it.

        Args:
            url (str): A public ``http(s)`` URL. The server resolves it and
                refuses anything that is not on the public internet.

        Returns:
            v1.MediaFileData: The same body :meth:`upload` returns.

        Raises:
            httpx.HTTPStatusError: If the server could not import the image — 422
                if the URL is unusable, does not resolve publicly, or redirects;
                413 if the image is too large; 415 if it is not a supported format.

        Note:
            The URL is not kept. Prompts always reference stored media, so a run
            never depends on the third-party host still serving the image.

        Example::

            from phoenix.client import Client
            client = Client()

            stored = client.media.import_from_url("https://example.com/cat.png")
            print(stored["sha256"])
        """
        body = v1.ImportMediaRequestBodyWrapper(data=v1.ImportMediaRequestBody(url=url))
        response = self._client.post("v1/media/import", json=body)
        response.raise_for_status()
        return cast(v1.UploadMediaResponseBody, response.json())["data"]

    def get(self, sha256: str, /) -> FetchedMedia:
        """
        Retrieves stored media by its digest.

        Args:
            sha256 (str): The SHA-256 digest of the media, in lowercase
                hexadecimal — the ``sha256`` an upload returned, or the tail of a
                ``phoenix://media/<sha256>`` reference.

        Returns:
            FetchedMedia: The raw bytes and the media type they were served as.
            Unpacks as ``(content, media_type)``.

        Raises:
            MediaResolutionError: If the response was plainly not media, which is
                how a wrong digest shows up on a Phoenix instance that serves its
                web app for unmatched paths.
            httpx.HTTPStatusError: If no media with that digest is stored (404).

        Example::

            from pathlib import Path
            from phoenix.client import Client

            client = Client()

            content, media_type = client.media.get(sha256)
            Path("cat.png").write_bytes(content)
        """
        url = _media_url(sha256)
        response = self._client.get(url)
        response.raise_for_status()
        return _fetched(url, response)


class AsyncMedia:
    """
    Provides asynchronous methods for storing and retrieving media used by prompts.

    The asynchronous counterpart to :class:`Media`; see it for what the methods do.

    Examples:
        Storing an image and reading it back::

            from pathlib import Path
            from phoenix.client import AsyncClient

            async_client = AsyncClient()

            stored = await async_client.media.upload(Path("cat.png"))
            content, media_type = await async_client.media.get(stored["sha256"])
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        _guard: AsyncServerVersionGuard | None = None,
    ) -> None:
        self._client = client
        self._guard = _guard or AsyncServerVersionGuard(client)

    async def upload(
        self,
        media: Any,
        /,
        *,
        file_name: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> v1.MediaFileData:
        """
        Asynchronously stores media and returns the digest and URL that reference it.

        Args:
            media (Any): The media to store. Accepts every form a prompt's image
                variable accepts: raw bytes, a ``bytearray`` or ``memoryview``, a
                base64 string, a ``data:`` URI, a :class:`~pathlib.Path`, a path
                string, an ``http(s)`` URL, or a ``MediaContent`` mapping.
            file_name (Optional[str]): The name to remember the media by. Derived
                from the reference when not given.
            media_type (Optional[str]): A declared media type, which wins over
                detection when deriving a file name. The stored type always comes
                from the server, which reads it from the bytes.

        Returns:
            v1.MediaFileData: The stored media's ``sha256``, ``media_type``,
            ``size_bytes``, and the ``phoenix://media/<sha256>`` ``url`` a prompt
            template references it by.

        Raises:
            MediaResolutionError: If the media could not be turned into bytes.
            httpx.HTTPStatusError: If the server rejected the media.

        Note:
            Resolution is synchronous — reading a file or fetching an ``http(s)``
            URL blocks this coroutine. Pass bytes you already hold to keep the
            event loop free, or use :meth:`import_from_url` so the fetch happens
            on the server.

            For the same reason this cannot resolve a reference that needs the
            Phoenix client itself — ``phoenix://media/<sha256>`` or a
            Phoenix-relative path — because the shared resolver takes a
            synchronous client. Both name media that is *already stored*, so
            re-uploading one is a round trip to the digest you started with;
            :meth:`get` reads it back instead. Passing one raises
            ``MediaResolutionError``.

        Example::

            from pathlib import Path
            from phoenix.client import AsyncClient

            async_client = AsyncClient()
            stored = await async_client.media.upload(Path("cat.png"))
        """
        content, detected_type = resolve_media(media, media_type=media_type)
        name = file_name or media_file_name(media, detected_type)
        response = await self._client.post("v1/media", files=_upload_files(content, name))
        response.raise_for_status()
        return cast(v1.UploadMediaResponseBody, response.json())["data"]

    async def import_from_url(self, url: str, /) -> v1.MediaFileData:
        """
        Asynchronously has the server fetch an image once and store it.

        Args:
            url (str): A public ``http(s)`` URL. The server resolves it and
                refuses anything that is not on the public internet.

        Returns:
            v1.MediaFileData: The same body :meth:`upload` returns.

        Raises:
            httpx.HTTPStatusError: If the server could not import the image.

        Example::

            from phoenix.client import AsyncClient
            async_client = AsyncClient()

            stored = await async_client.media.import_from_url("https://example.com/cat.png")
        """
        body = v1.ImportMediaRequestBodyWrapper(data=v1.ImportMediaRequestBody(url=url))
        response = await self._client.post("v1/media/import", json=body)
        response.raise_for_status()
        return cast(v1.UploadMediaResponseBody, response.json())["data"]

    async def get(self, sha256: str, /) -> FetchedMedia:
        """
        Asynchronously retrieves stored media by its digest.

        Args:
            sha256 (str): The SHA-256 digest of the media, in lowercase
                hexadecimal.

        Returns:
            FetchedMedia: The raw bytes and the media type they were served as.
            Unpacks as ``(content, media_type)``.

        Raises:
            MediaResolutionError: If the response was plainly not media.
            httpx.HTTPStatusError: If no media with that digest is stored (404).

        Example::

            from phoenix.client import AsyncClient
            async_client = AsyncClient()

            content, media_type = await async_client.media.get(sha256)
        """
        url = _media_url(sha256)
        response = await self._client.get(url)
        response.raise_for_status()
        return _fetched(url, response)


class MediaClientMixin:
    """Adds :attr:`media` to :class:`~phoenix.client.client.Client`.

    A mixin so that wiring the resource costs upstream's ``client.py`` one import
    and one base class, instead of a property and a line in the ``_client``
    setter — the setter being exactly where upstream adds each new resource of its
    own, and so the worst place for the fork to also be adding lines.

    Constructing on access rather than caching in the setter is what keeps the
    setter untouched, and matches ``Prompts.tags``, which does the same.
    """

    @property
    def _client(self) -> httpx.Client:
        """The HTTP client ``Client`` was configured with.

        Declared as a property rather than as ``_client: httpx.Client``, because
        ``Client`` declares ``_client`` as a property too, and a property
        overriding a plain variable is an error under pyright strict — which CI
        runs. The prompt mixins keep the variable form for the mirror-image
        reason: ``Prompts`` assigns ``self._client`` in ``__init__``, and a
        read-only property here would refuse that assignment.

        Never reached: every class using this mixin supplies its own.
        """
        raise NotImplementedError

    @property
    def media(self) -> Media:
        """Returns an instance of the Media class for interacting with media-related API endpoints.

        Returns:
            Media: An instance of the Media class.
        """  # noqa: E501
        return Media(self._client)


class AsyncMediaClientMixin:
    """Adds :attr:`media` to :class:`~phoenix.client.client.AsyncClient`.

    See :class:`MediaClientMixin`.
    """

    @property
    def _client(self) -> httpx.AsyncClient:
        """The HTTP client ``AsyncClient`` was configured with.

        A property for the reason given on :class:`MediaClientMixin`. Never
        reached: every class using this mixin supplies its own.
        """
        raise NotImplementedError

    @property
    def media(self) -> AsyncMedia:
        """Returns an instance of the AsyncMedia class for interacting with media-related API endpoints.

        Returns:
            AsyncMedia: An instance of the AsyncMedia class.
        """  # noqa: E501
        return AsyncMedia(self._client)


def _media_url(sha256: str) -> str:
    return f"v1/media/{encode_path_param(sha256)}"


def _fetched(url: str, response: httpx.Response) -> FetchedMedia:
    """Turn a media response into bytes and a type, refusing what is not media.

    The digest check belongs on this side of the call. Phoenix serves its web app
    for any unmatched path, so a digest that is merely *wrong* rather than
    malformed can come back as HTML with a healthy 200 — and bytes accepted here
    go on to be written to a file or handed to a model as an image.
    """
    header_type = response.headers.get("content-type")
    reject_non_media(url, response.content, header_type)
    media_type = (header_type or "").split(";")[0].strip()
    return FetchedMedia(
        response.content,
        media_type or sniff_media_type(response.content) or DEFAULT_MEDIA_TYPE,
    )
