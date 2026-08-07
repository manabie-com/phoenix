"""REST endpoints for the content-addressed media referenced by prompt templates."""

import hashlib
import logging
import socket
from ipaddress import ip_address
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Response, UploadFile
from sqlalchemy import select
from starlette.requests import Request
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_422_UNPROCESSABLE_ENTITY,
)

from phoenix.config import get_env_max_media_bytes
from phoenix.db import models
from phoenix.db.helpers import SupportedSQLDialect
from phoenix.db.insertion.helpers import OnConflict, insert_on_conflict
from phoenix.db.types.media import (
    SUPPORTED_MEDIA_TYPES,
    detect_media_type,
    hosted_media_url,
)
from phoenix.server.api.helpers.media_storage import media_store
from phoenix.server.api.routers.v1.models import V1RoutesBaseModel
from phoenix.server.api.routers.v1.utils import (
    RequestBody,
    ResponseBody,
    add_errors_to_responses,
)
from phoenix.server.authorization import is_not_locked

logger = logging.getLogger(__name__)

router = APIRouter(tags=["media"])

_SHA256_PATH_PATTERN = r"^[0-9a-f]{64}$"

_IMPORT_TIMEOUT_SECONDS = 10.0
"""How long to wait on a third-party host when importing an image by URL."""

_IMMUTABLE_MEDIA_HEADERS = {
    # Safe to cache forever: the URL is the digest of the content it serves.
    "Cache-Control": "public, max-age=31536000, immutable",
    # Phoenix serves user-uploaded bytes from its own origin. Pin the declared
    # type and strip the response of any ambient authority so that a payload
    # which slips past the allowlist still cannot execute as a document.
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


class MediaFileData(V1RoutesBaseModel):
    sha256: str
    media_type: str
    size_bytes: int
    url: str


class UploadMediaResponseBody(ResponseBody[MediaFileData]):
    pass


class ImportMediaRequestBody(V1RoutesBaseModel):
    url: str


class ImportMediaRequestBodyWrapper(RequestBody[ImportMediaRequestBody]):
    pass


def _is_public_address(address: str) -> bool:
    """
    Whether an address belongs to the public internet.

    Args:
        address: An IPv4 or IPv6 address in string form.

    Returns:
        False for anything Phoenix should not be made to fetch from — loopback,
        link-local (where cloud metadata endpoints live), private ranges, and
        multicast. Also False for an address that cannot be parsed: a check that
        cannot be performed has not passed.
    """
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return parsed.is_global and not parsed.is_multicast


def _reject_unsafe_host(host: str) -> None:
    """
    Reject a host that resolves anywhere other than the public internet.

    Fetching a caller-supplied URL server-side would otherwise reach anything the
    Phoenix process can reach — cloud metadata endpoints, databases on the private
    network, services on loopback. Every resolved address is checked, not just the
    first, since a hostname can return a mix.

    This is a check on the *name*. See :func:`_reject_unsafe_peer` for the check on
    the connection that name actually produced.

    Args:
        host: The hostname or IP from the URL.

    Raises:
        HTTPException: 422 if the host cannot be resolved or resolves to a
            non-public address.
    """
    try:
        # `getaddrinfo` types its sockaddr loosely, and every check downstream wants
        # the address as a string.
        addresses = {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not resolve {host}.",
        )
    if not addresses:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not resolve {host}.",
        )
    for address in addresses:
        if not _is_public_address(address):
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{host} resolves to a non-public address ({address}). "
                    f"Only images on the public internet can be imported by URL."
                ),
            )


def _reject_unsafe_peer(response: httpx.Response) -> None:
    """
    Reject a response that arrived from a non-public address.

    :func:`_reject_unsafe_host` resolves the hostname, and then httpx resolves it
    again in order to connect. A record with a very short TTL can answer those two
    lookups differently — public for the check, ``169.254.169.254`` for the
    connection — so the name-based check can be walked past on its own. This asks the
    socket where the bytes are actually coming from.

    Called before the body is read, so nothing internal is ever stored or served
    back. It does not stop the request from being sent; that would need the
    connection pinned to the address already validated, which httpx does not expose
    without a custom transport.

    Args:
        response: A streamed response whose body has not been read yet.

    Raises:
        HTTPException: 422 if the peer is not a public address.
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        # No socket to interrogate — a mock transport, say. The name-based check
        # has already run regardless.
        return
    if not (server_addr := stream.get_extra_info("server_addr")):
        return
    address = str(server_addr[0])
    if not _is_public_address(address):
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"The URL was served from a non-public address ({address}). "
                f"Only images on the public internet can be imported by URL."
            ),
        )


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """
    Read a streamed response, giving up as soon as it exceeds the limit.

    The cap has to be applied while reading rather than afterwards. httpx enforces no
    maximum response size, so materializing the body first would let a caller-named
    URL decide how much memory Phoenix allocates — a 10 GB file would be entirely
    resident before being rejected for exceeding 20 MiB. The upload path applies the
    same discipline by reading one byte past the limit and no further.

    Args:
        response: A streamed response whose body has not been read yet.
        max_bytes: The most that may be read.

    Returns:
        The response body.

    Raises:
        HTTPException: 413 as soon as the body is known to exceed ``max_bytes``.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Media exceeds the maximum supported size of {max_bytes} bytes.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/media",
    dependencies=[Depends(is_not_locked)],
    operation_id="uploadMedia",
    summary="Upload media for use in prompts",
    description=(
        "Store a media file and return the URL that references it from a prompt "
        "template. Media is addressed by the SHA-256 digest of its content, so "
        "uploading the same file twice returns the same URL and stores one copy. "
        "The media type is determined from the file's content, not from the "
        "declared content type."
    ),
    response_description="The stored media and the URL that references it",
    responses=add_errors_to_responses([413, 415]),
    response_model_by_alias=True,
    response_model_exclude_defaults=True,
    response_model_exclude_unset=True,
)
async def upload_media(request: Request, file: UploadFile) -> UploadMediaResponseBody:
    """
    Store a media file for use in prompt templates.

    Args:
        request: The FastAPI request object.
        file: The uploaded media file.

    Returns:
        The stored media's digest, type, size, and prompt-template URL.

    Raises:
        HTTPException: 413 if the file exceeds the configured size limit, or 415
            if its content is not a supported image format.
    """
    max_bytes = get_env_max_media_bytes()
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Media exceeds the maximum supported size of {max_bytes} bytes.",
        )
    if not content:
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Media is empty.",
        )
    return await _store_media(
        request,
        content,
        source=file.filename or "the file",
        file_name=file.filename,
    )


async def _store_media(
    request: Request,
    content: bytes,
    *,
    source: str,
    file_name: Optional[str] = None,
) -> UploadMediaResponseBody:
    """
    Validate media and store it, returning the reference prompts use.

    Shared by every way media arrives so that the type is always determined from
    the bytes, never from what the caller claimed.

    Args:
        request: The FastAPI request object, for the database.
        content: The media bytes.
        source: Where the bytes came from, used only in error messages.
        file_name: The name to remember the media by, when one is known.

    Returns:
        The stored media's digest, type, size, and prompt-template URL.

    Raises:
        HTTPException: 415 if the content is empty or not a supported image.
    """
    if not content:
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"{source} is empty.",
        )
    media_type = detect_media_type(content)
    if media_type is None or media_type not in SUPPORTED_MEDIA_TYPES:
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported media. Expected one of: {', '.join(sorted(SUPPORTED_MEDIA_TYPES))}."
            ),
        )

    sha256 = hashlib.sha256(content).hexdigest()
    # Bytes first, metadata second. The other order would leave a row promising media
    # that is not there yet, which every reader would report as missing. This order can
    # leave stored bytes with no row, which the sweeper reclaims as unreferenced.
    await media_store().put(sha256, content, media_type=media_type)
    async with request.app.state.db() as session:
        dialect = SupportedSQLDialect(session.bind.dialect.name)
        await session.execute(
            insert_on_conflict(
                dict(
                    sha256=sha256,
                    media_type=media_type,
                    size_bytes=len(content),
                    file_name=file_name,
                ),
                dialect=dialect,
                table=models.MediaFile,
                unique_by=("sha256",),
                on_conflict=OnConflict.DO_NOTHING,
                constraint_name="pk_media_files",
            )
        )

    return UploadMediaResponseBody(
        data=MediaFileData(
            sha256=sha256,
            media_type=media_type,
            size_bytes=len(content),
            url=hosted_media_url(sha256),
        )
    )


@router.post(
    "/media/import",
    dependencies=[Depends(is_not_locked)],
    operation_id="importMediaFromUrl",
    summary="Import an image from a URL for use in prompts",
    description=(
        "Fetch an image from a public URL once and store it, returning the same "
        "reference an upload would. The URL is not kept: prompts always reference "
        "stored media, so a run never depends on a third-party host still serving "
        "the image, and never fetches a caller-supplied URL."
    ),
    response_description="The stored media and the URL that references it",
    responses=add_errors_to_responses([413, 415, 422]),
    response_model_by_alias=True,
    response_model_exclude_defaults=True,
    response_model_exclude_unset=True,
)
async def import_media_from_url(
    request: Request,
    request_body: ImportMediaRequestBodyWrapper,
) -> UploadMediaResponseBody:
    """
    Store an image fetched from a public URL.

    Args:
        request: The FastAPI request object.
        request_body: The URL to fetch.

    Returns:
        The stored media's digest, type, size, and prompt-template URL.

    Raises:
        HTTPException: 422 if the URL is not an http(s) URL, does not resolve to a
            public address, or cannot be fetched; 413 if the image exceeds the
            configured size limit; 415 if it is not a supported image.
    """
    parsed_url = urlparse(request_body.data.url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide an http or https URL for the image.",
        )
    _reject_unsafe_host(parsed_url.hostname)

    max_bytes = get_env_max_media_bytes()
    try:
        async with httpx.AsyncClient(
            timeout=_IMPORT_TIMEOUT_SECONDS,
            # A redirect could land somewhere the host check already rejected.
            follow_redirects=False,
        ) as client:
            # Streamed so that the size limit governs how much is ever read, and so
            # that the address actually connected to can be checked before any of the
            # body is.
            async with client.stream("GET", request_body.data.url) as response:
                if response.is_redirect:
                    raise HTTPException(
                        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="The URL redirects. Use the URL the image is served from.",
                    )
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"The image URL returned {response.status_code}.",
                    )
                _reject_unsafe_peer(response)
                content = await _read_capped(response, max_bytes)
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not fetch the image: {error}.",
        )
    return await _store_media(
        request,
        content,
        source=request_body.data.url,
        file_name=PurePosixPath(parsed_url.path).name or None,
    )


@router.get(
    "/media/{sha256}",
    operation_id="getMedia",
    summary="Get media by digest",
    description=(
        "Return the raw bytes of stored media. The response is immutable and "
        "safe to cache indefinitely, since the digest in the path identifies the "
        "content being served."
    ),
    response_description="The raw media bytes",
    responses=add_errors_to_responses([404]),
    response_class=Response,
)
async def get_media(
    request: Request,
    sha256: str = Path(
        ...,
        pattern=_SHA256_PATH_PATTERN,
        description="The SHA-256 digest of the media, in lowercase hexadecimal.",
    ),
) -> Response:
    """
    Serve stored media by its digest.

    Args:
        request: The FastAPI request object.
        sha256: The SHA-256 digest of the media, in lowercase hexadecimal.

    Returns:
        The raw media bytes with their stored media type.

    Raises:
        HTTPException: 404 if no media with that digest is stored.
    """
    async with request.app.state.db() as session:
        media_type = await session.scalar(
            select(models.MediaFile.media_type).where(models.MediaFile.sha256 == sha256)
        )
    if media_type is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"No media found with digest {sha256}.",
        )
    # The row records that this media exists and what type it is; the bytes come from
    # the store, which the sweeper could have emptied since.
    if (content := await media_store().get(sha256)) is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Media {sha256} is recorded but its content is no longer stored.",
        )
    return Response(
        content=content,
        media_type=media_type,
        headers={
            **_IMMUTABLE_MEDIA_HEADERS,
            "Content-Disposition": f'inline; filename="{sha256}"',
        },
    )
