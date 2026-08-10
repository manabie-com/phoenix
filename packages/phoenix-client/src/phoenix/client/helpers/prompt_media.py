"""Resolve image references in a prompt template to bytes.

Fork-owned module, shared by every provider converter. Extracted rather than
copied per provider: duplicated fork logic is the failure mode where a sync fixes
one copy and leaves the other stale with no test failing.

An `ImageContentPart` carries one of two things:

* `MediaContent{url, media_type}` — a fixed image baked into the template.
* `MediaVariable{variable}` — an image supplied per run, shown in the UI as
  `{{image}}` under "Image Input".

Either way a provider ultimately needs bytes (or a URL it can fetch itself), and
the caller may hand the value over in any of the shapes below.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union, cast
from urllib.parse import urlsplit

import httpx

__all__ = [
    "MediaResolutionError",
    "PHOENIX_MEDIA_SCHEME",
    "SUPPORTED_FILE_MEDIA_TYPES",
    "MediaReference",
    "ResolvedSource",
    "addresses_phoenix",
    "media_file_name",
    "media_reference",
    "media_value_is_absent",
    "reject_non_media",
    "note_omitted_media",
    "resolve_media_source",
    "ResolvedMedia",
    "resolve_media",
    "sniff_media_type",
    "DEFAULT_MEDIA_TYPE",
]

DEFAULT_MEDIA_TYPE = "application/octet-stream"

# Stored prompt media is written as `phoenix://media/<sha256>`. The server's
# validator accepts only this or a base64 `data:` URL, so it is the form a
# template actually contains — not the REST path the digest resolves to.
PHOENIX_MEDIA_SCHEME = "phoenix://media/"

# Every provider Phoenix supports accepts exactly one document type, so this is
# provider-agnostic. Mirrors the server's per-provider file allowlists in
# server/api/helpers/playground_media/_allowlists.py.
SUPPORTED_FILE_MEDIA_TYPES = frozenset(("application/pdf",))

# (bytes, media_type)
ResolvedMedia = tuple[bytes, str]


class MediaResolutionError(Exception):
    """Raised when an image reference in a prompt cannot be turned into bytes."""


def sniff_media_type(data: bytes) -> Optional[str]:
    """Identify common media types from their leading magic bytes.

    Raw `bytes` carry no filename or header to infer a type from, and providers
    reject `application/octet-stream` for image input — so guessing from the
    signature is the difference between a working call and a confusing 400.
    """
    for signature, media_type in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"%PDF-", "application/pdf"),
    ):
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _decode_base64_image(value: Union[str, bytes]) -> Optional[ResolvedMedia]:
    """Decode base64-encoded image data, if that is what `value` holds.

    Returns a result only when the decode succeeds AND the output carries a
    signature we recognise. That double condition is what makes this safe to
    attempt speculatively: arbitrary text either fails to decode or decodes to
    something unrecognisable, so it is never mistaken for an image.

    Needed in two places. A caller may pass a bare base64 *string* (the common
    shape when an image arrived as JSON). More dangerously, a caller may pass
    base64 as *bytes* — without this, that text would reach the model verbatim as
    image data, which fails in the least debuggable way possible.
    """
    try:
        # Binary image data is not ASCII, so this doubles as the cheap rejection
        # path for bytes that are already the image itself.
        text = value.decode("ascii") if isinstance(value, bytes) else value
        text = "".join(text.split())  # tolerate wrapped/padded base64
        if len(text) < 16:
            return None
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, UnicodeDecodeError):
        return None
    sniffed = sniff_media_type(decoded)
    return (decoded, sniffed) if sniffed else None


def _guess_media_type(
    source: Union[str, Path],
    fallback: Optional[str],
    data: Optional[bytes] = None,
) -> str:
    if fallback:
        return fallback
    guessed, _ = mimetypes.guess_type(str(source))
    if guessed:
        return guessed
    return (sniff_media_type(data) if data else None) or DEFAULT_MEDIA_TYPE


def _looks_like_file(value: str) -> Optional[Path]:
    """Return `value` as a Path if it names an existing file.

    Checked before any URL interpretation: an absolute filesystem path also
    starts with "/", so treating a leading slash as a relative URL would send
    local paths to httpx and produce an opaque protocol error.
    """
    try:
        candidate = Path(value)
        return candidate if candidate.is_file() else None
    except (OSError, ValueError):  # embedded nulls, name too long
        return None


# Content types a media reference must never resolve to. A fetch that lands on a
# web page means the URL was wrong, not that the page is an image.
_NON_MEDIA_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
)


def reject_non_media(url: str, data: bytes, header_type: Optional[str]) -> None:
    """Raise when a fetch came back as something that is plainly not media.

    A 200 is not proof the URL was right. Phoenix serves its single-page app for
    any unmatched path, so a mistyped filesystem path — which reaches the fetch
    branch as a Phoenix-relative URL as soon as a client is present — comes back
    as HTML with a perfectly healthy status. Without this the model is handed that
    page as an image and the call *succeeds*, which is the silent corruption this
    module exists to prevent and the hardest kind of failure to trace back.

    Deliberately narrow. Bytes carrying a media signature are accepted whatever
    the server claims, since a misconfigured host serving a real PNG as
    `text/plain` is a working image; only bytes that are unrecognisable *and*
    declared as text, JSON, or XML are rejected.
    """
    if sniff_media_type(data) is not None:
        return
    declared = (header_type or "").split(";")[0].strip().lower()
    if declared.startswith(_NON_MEDIA_PREFIXES):
        raise MediaResolutionError(
            f"fetching {url!r} returned {declared!r}, which is not media — the "
            "reference is probably wrong. A path that does not exist locally is "
            "fetched from Phoenix, where any unknown path serves the web app."
        )


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    scheme = url.scheme.lower()
    return scheme, url.host.lower(), url.port or (443 if scheme == "https" else 80)


def addresses_phoenix(url: str, client: httpx.Client) -> bool:
    """Whether `url` names the Phoenix instance `client` is configured for.

    A URL that is not absolute is Phoenix's by construction — it is resolved
    against the client's `base_url`. An absolute one is Phoenix's only if it names
    the same origin.
    """
    if not url.startswith(("http://", "https://")):
        return True
    return _origin(httpx.URL(url)) == _origin(client.base_url)


def fetch_url(url: str, client: Optional[httpx.Client]) -> tuple[bytes, Optional[str]]:
    """GET `url`, using the caller's Phoenix client only for Phoenix's own URLs.

    Phoenix-hosted media needs that client: the URL is relative to its `base_url`
    and the request has to carry its auth. A third-party URL needs the exact
    opposite. The client's headers *are* the caller's Phoenix credentials, and
    httpx applies client-level headers to every host it is pointed at — so
    fetching `https://example.com/cat.png` through it would hand example.com a
    working Phoenix API key. Third-party URLs are therefore fetched the same way
    they are when no client was passed at all.

    The cost is that a client's proxy, CA bundle and timeout do not apply to a
    third-party fetch. A caller who needs them should read the bytes itself and
    pass those, or let the server fetch the URL via `client.media.import_from_url`.

    Raises:
        MediaResolutionError: The response was not media. See `reject_non_media`.
    """
    if client is not None and addresses_phoenix(url, client):
        response = client.get(url)
    else:
        response = httpx.get(url, follow_redirects=True)
    response.raise_for_status()
    header_type = response.headers.get("content-type")
    reject_non_media(url, response.content, header_type)
    return response.content, header_type


def resolve_media(
    value: Any,
    *,
    media_type: Optional[str] = None,
    client: Optional[httpx.Client] = None,
) -> ResolvedMedia:
    """Turn any supported image reference into `(bytes, media_type)`.

    Accepts raw bytes, base64 (str or bytes), a filesystem path, a `data:` URI,
    an `http(s)` URL, a Phoenix-relative URL, or a `MediaContent`-shaped mapping.

    Args:
        value: The reference to resolve.
        media_type: A declared type, which always wins over detection.
        client: Client used to fetch Phoenix-hosted media, whose relative URLs
            need a base_url and auth headers. Third-party URLs are fetched
            without it, so its credentials never reach another host — see
            `fetch_url`.

    Raises:
        MediaResolutionError: The reference could not be turned into bytes.
    """
    # MediaContent mapping: recurse on its url, preferring its declared type.
    if isinstance(value, Mapping):
        # `isinstance` narrows only to Mapping[Unknown, Unknown], so every read
        # off it is partially unknown under pyright strict. The cast states what
        # a MediaContent actually is.
        mapping = cast(Mapping[str, Any], value)
        url = mapping.get("url")
        if not url:
            raise MediaResolutionError(f"media mapping has no 'url': {mapping!r}")
        return resolve_media(url, media_type=mapping.get("media_type") or media_type, client=client)

    if isinstance(value, (bytes, bytearray, memoryview)):
        # `memoryview` is generic, and an unparameterized one makes `bytes(value)`
        # a partially-unknown call under pyright strict. `tobytes()` is precisely
        # typed, so branch rather than cast.
        raw = value.tobytes() if isinstance(value, memoryview) else bytes(value)
        if (sniffed := sniff_media_type(raw)) is not None:
            return raw, media_type or sniffed
        # Unrecognisable as image data — it may be base64 *text* handed over as
        # bytes. Forwarding that verbatim would send the model the ASCII of the
        # encoding instead of the image.
        if (decoded := _decode_base64_image(raw)) is not None:
            return decoded[0], media_type or decoded[1]
        return raw, media_type or DEFAULT_MEDIA_TYPE

    if isinstance(value, Path):
        if not value.is_file():
            raise MediaResolutionError(f"no such file: {value}")
        data = value.read_bytes()
        return data, _guess_media_type(value, media_type, data)

    if isinstance(value, str):
        if value.startswith("data:"):
            # data:[<media type>][;base64],<data>
            header, _, payload = value.partition(",")
            if not payload and not _:
                raise MediaResolutionError(f"malformed data URI: {value[:40]}...")
            declared = header[5:].split(";")[0] or None
            raw = base64.b64decode(payload) if ";base64" in header else payload.encode("utf-8")
            return raw, media_type or declared or sniff_media_type(raw) or DEFAULT_MEDIA_TYPE

        if value.startswith(PHOENIX_MEDIA_SCHEME):
            # The scheme the server stores prompt media under, and the only form
            # its validator accepts besides a data URL. It is not fetchable on its
            # own — the digest maps onto GET /v1/media/{sha256} on the Phoenix
            # instance the caller is authenticated against.
            sha256 = value[len(PHOENIX_MEDIA_SCHEME) :]
            if client is None:
                raise MediaResolutionError(
                    f"{value!r} is Phoenix-hosted media, which needs a client to fetch: "
                    "pass client=Client()._client"
                )
            data, header_type = fetch_url(f"v1/media/{sha256}", client)
            return data, media_type or header_type or sniff_media_type(data) or DEFAULT_MEDIA_TYPE

        if not value.startswith(("http://", "https://")):
            if (candidate := _looks_like_file(value)) is not None:
                data = candidate.read_bytes()
                return data, _guess_media_type(candidate, media_type, data)

        if value.startswith(("http://", "https://")) or (
            value.startswith("/") and client is not None
        ):
            data, header_type = fetch_url(value, client)
            return data, media_type or header_type or sniff_media_type(data) or DEFAULT_MEDIA_TYPE

        # A bare base64 payload, e.g. straight out of a JSON API response.
        if (decoded := _decode_base64_image(value)) is not None:
            return decoded[0], media_type or decoded[1]

        hint = (
            " — it looks like a Phoenix media URL, which needs client=Client()._client to fetch"
            if value.startswith("/")
            else ""
        )
        raise MediaResolutionError(
            f"cannot resolve image reference {value[:60]!r}{hint}. Expected bytes, an "
            "existing file path, base64-encoded image data, a data: URI, or an http(s) URL"
        )

    raise MediaResolutionError(f"unsupported media value of type {type(value).__name__}")


def media_file_name(reference: Any, media_type: str) -> str:
    """Best-effort filename for a resolved media reference.

    OpenAI requires a filename alongside file data — it has no other way to hint
    the document's type — so one has to be produced even when the reference is
    raw bytes. Mirrors what the server's `media_file_name` does for the
    playground.
    """
    candidate: Optional[str] = None
    if isinstance(reference, Path):
        candidate = reference.name
    elif isinstance(reference, str) and not reference.startswith("data:"):
        if reference.startswith(("http://", "https://")):
            # A URL's query and fragment are not part of its file name, and a
            # signed URL carries its credential there — which would otherwise
            # become the name Phoenix stores and the name sent on to a provider
            # alongside a document part.
            reference = urlsplit(reference).path
        tail = reference.rstrip("/").rsplit("/", 1)[-1]
        # A digest is not a filename, and a bare base64 blob certainly is not.
        if "." in tail and len(tail) <= 128:
            candidate = tail
    if candidate:
        return candidate
    return f"document{mimetypes.guess_extension(media_type) or ''}"


@dataclass(frozen=True)
class ResolvedSource:
    """Media resolved from a content part, with everything a provider may need."""

    data: bytes
    media_type: str
    filename: str


def media_value_is_absent(value: Any) -> bool:
    """Whether a media variable's value means "nothing was supplied".

    A prompt declaring a media slot has to serve both the runs that fill it and
    the runs that do not — one dataset holding attachment-bearing and text-only
    rows is the ordinary case, not the exotic one. So absence is a supported
    input, and stays distinct from a value that *was* supplied and turned out to
    be unusable, which still fails loudly.

    A row with no attachment arrives in one of three shapes and all three mean
    the same thing: the key is missing, the value is `None`, or the value is
    blank. The latter two are what a spreadsheet column with empty cells becomes
    once it is JSON, so keying on the missing key alone would reject exactly the
    rows this exists to admit.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (bytes, bytearray)):
        return not value
    if isinstance(value, memoryview):
        # `memoryview` is generic, so `len()` on an unparameterized one is a
        # partially-unknown call under pyright strict. `nbytes` is an int.
        return value.nbytes == 0
    return False


@dataclass(frozen=True)
class MediaReference:
    """What a media part points at, before anything is fetched or decoded."""

    value: Any
    media_type: Optional[str]


def media_reference(
    source: Mapping[str, Any],
    variables: Mapping[str, Any],
) -> Optional[MediaReference]:
    """The reference a content part carries, or `None` if its slot is empty.

    `None` means the part names a variable this run did not supply — the caller's
    signal to leave the media out rather than to fail.

    Separate from `resolve_media_source` because the OpenAI image path needs the
    reference rather than bytes: a public URL is handed to OpenAI to fetch, not
    downloaded and re-encoded. Both paths have to agree on what counts as absent,
    and one implementation is the only way to keep that true.
    """
    if "variable" in source:
        value = variables.get(source["variable"])
        if media_value_is_absent(value):
            return None
        return MediaReference(value=value, media_type=None)
    return MediaReference(value=source.get("url"), media_type=source.get("media_type"))


def note_omitted_media(omitted: list[str], source: Mapping[str, Any]) -> None:
    """Record that a media slot was left empty, so the omission stays visible.

    Skipping an unsupplied slot is the point, but it should never be *invisible*:
    a caller who meant to pass an image and misspelled the key would otherwise
    see a silently text-only prompt. Appended in template order and deduplicated,
    since one variable may fill several parts.
    """
    name = source.get("variable")
    if isinstance(name, str) and name not in omitted:
        omitted.append(name)


def resolve_media_source(
    source: Mapping[str, Any],
    variables: Mapping[str, Any],
    client: Optional[httpx.Client],
    *,
    kind: str = "image",
    supported_media_types: Optional[frozenset[str]] = None,
) -> Optional[ResolvedSource]:
    """Resolve a content part's media field to bytes, media type, and filename.

    Shared by `ImageContentPart.image` and `FileContentPart.file`, which are the
    same `MediaContent | MediaVariable` union. It arrives as a plain Mapping
    because probing a TypedDict union by key is what a Mapping view is for —
    indexing the union directly is a typeddict-item error, since neither member
    carries all the keys.

    Args:
        source: The part's `image` or `file` value.
        variables: Run-time values, for a `MediaVariable`.
        client: Client used to fetch Phoenix-hosted media.
        kind: `"image"` or `"file"`, used only in error messages.
        supported_media_types: If given, the resolved type must be a member.
            A variable carries no declared type, so this is the only point at
            which its value can be checked at all.

    Returns:
        The resolved media, or `None` when the part names a variable this run did
        not supply — an optional slot, which the caller leaves out.

    Raises:
        MediaResolutionError: Supplied but unresolvable, or an unsupported media
            type. An empty slot is not a failure; a bad value still is.
    """
    if (reference := media_reference(source, variables)) is None:
        return None
    data, media_type = resolve_media(
        reference.value, media_type=reference.media_type, client=client
    )

    if supported_media_types is not None and media_type.lower() not in supported_media_types:
        raise MediaResolutionError(
            f"unsupported {kind} media type {media_type!r}; expected one of "
            f"{', '.join(sorted(supported_media_types))}"
        )

    return ResolvedSource(
        data=data, media_type=media_type, filename=media_file_name(reference.value, media_type)
    )


def to_data_uri(data: bytes, media_type: str) -> str:
    """Encode resolved media as a `data:` URI.

    The form providers accept inline when they will not fetch a URL themselves,
    and the only way to pass Phoenix-hosted media, which needs auth the provider
    does not have.
    """
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
