"""Convert a Phoenix prompt into OpenAI chat-completion inputs, with images.

Fork-owned module. Nothing here lives in a file upstream also has, so it can
never conflict on a sync.

## Why this exists rather than fixing `format(sdk="openai")`

`helpers/sdk/openai/chat.py` dispatches content parts in one place,
`_ContentPartsConversion.to_openai`, which handles `text` and skips everything
else — so an `ImageContentPart` is silently dropped and the model is asked about
an image it never received. The call succeeds, which is what makes it dangerous.

A one-line `elif` there would not be enough. That function receives neither

* a `variables` mapping that can hold non-string values — it is typed
  `Mapping[str, str]`, and an image variable's value is bytes, a path, or a URL;
* nor an httpx client, which Phoenix-hosted media needs for base_url and auth.

Threading both down from `PromptVersion.format()` means changing upstream
signatures across the whole call chain, which is a large edit to files upstream
owns. A separate entry point costs one import at the call site instead.

## What it reuses

Everything except image handling. Messages with no image parts go through
upstream's own `_MessageConversion.to_openai`, so tool calls, refusals, and role
quirks behave exactly as before. Model kwargs come from upstream's
`_to_model_kwargs`, so invocation parameters stay correct as upstream evolves.
Only image-bearing messages take the fork path.

## Images

Unlike Gemini, OpenAI will fetch a public URL itself, so a public `http(s)`
reference is passed through untouched — no download, no re-encoding. Everything
else is inlined as a `data:` URI, which is also the only way to pass
Phoenix-hosted media, since OpenAI has no credentials for your Phoenix instance.
Pass `inline_urls=True` to inline public URLs too, e.g. if the model must not
make outbound requests.

## Usage

    from phoenix.client import Client
    from phoenix.client.helpers.sdk.openai_media import to_openai

    client = Client()
    prompt = client.prompts.get(prompt_identifier="prompt_with_image_examples")

    formatted = to_openai(
        prompt,
        variables={"image": Path("cat.png")},
        client=client._client,   # only needed for Phoenix-hosted images
    )

    openai.OpenAI().chat.completions.create(**formatted)

The return value is upstream's `OpenAIPrompt`, a Mapping, so it splats into the
SDK call exactly like `format()` does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Optional, Sequence, cast

import httpx

from phoenix.client.__generated__ import v1
from phoenix.client.helpers.prompt_media import (
    SUPPORTED_FILE_MEDIA_TYPES,
    MediaResolutionError,
    media_reference,
    note_omitted_media,
    resolve_media,
    resolve_media_source,
    to_data_uri,
)

# Upstream internals, reused deliberately so this module does not reimplement —
# and then drift from — parameter mapping and non-image message conversion. These
# are private names; if upstream renames them this fails loudly at import rather
# than silently producing different output.
from phoenix.client.helpers.sdk.openai.chat import (
    _MessageConversion,
    _to_model_kwargs,
)
from phoenix.client.types.prompts import OpenAIPrompt
from phoenix.client.utils.template_formatters import (
    BaseTemplateFormatter,
    TemplateFormatterError,
    to_formatter,
)

if TYPE_CHECKING:
    from phoenix.client.types.prompts import PromptVersion

# MediaResolutionError is re-exported so callers catch it from the converter they
# import, without needing to know media resolution is shared across providers.
__all__ = ["to_openai", "OpenAIMediaPrompt", "MediaResolutionError"]


@dataclass(frozen=True)
class OpenAIMediaPrompt(OpenAIPrompt):
    """An `OpenAIPrompt` that also reports parts it could not convert.

    Subclassed rather than replaced so the result stays a Mapping over
    `messages` + kwargs and still splats into `chat.completions.create(**result)`.
    The extra field is not a Mapping key, so it never reaches the SDK.

    `to_genai` exposes the same signal. Without it here, an unconvertible part
    would vanish exactly the way upstream's `format(sdk="openai")` loses an
    image — the failure this module exists to prevent.
    """

    _unsupported: tuple[str, ...] = field(default=(), repr=False)
    _omitted_media: tuple[str, ...] = field(default=(), repr=False)

    @property
    def unsupported_parts(self) -> tuple[str, ...]:
        """Content-part types that were present but could not be converted."""
        return self._unsupported

    @property
    def omitted_media(self) -> tuple[str, ...]:
        """Media variables the template declares that this run did not supply.

        Kept apart from `unsupported_parts`: an empty optional slot is a normal
        outcome, not a conversion failure, and conflating the two would make a
        deliberately text-only run look broken. Reading it is how a caller who
        expected to pass an image catches a misspelled key.
        """
        return self._omitted_media


def _string_variables(variables: Mapping[str, Any]) -> Mapping[str, str]:
    """Narrow to the variables text substitution can use.

    Media values are looked up by image parts instead, and must never be
    stringified into prompt text.
    """
    return {k: v for k, v in variables.items() if isinstance(v, str)}


def _format_text(
    template: str,
    variables: Mapping[str, Any],
    formatter: BaseTemplateFormatter,
) -> str:
    try:
        return formatter.format(template, variables=_string_variables(variables))
    except TemplateFormatterError:
        return template


_MEDIA_KINDS = frozenset({"image", "file"})


def _has_media(message: v1.PromptMessage) -> bool:
    """Whether a message needs the fork's conversion path.

    Both image and file parts qualify: upstream's `_ContentPartsConversion`
    matches neither, so leaving either to it drops the part with no error.
    """
    content = message["content"]
    if isinstance(content, str):
        return False
    return any(part["type"] in _MEDIA_KINDS for part in content)


def _image_url(
    image: Mapping[str, Any],
    variables: Mapping[str, Any],
    client: Optional[httpx.Client],
    inline_urls: bool,
) -> Optional[str]:
    """Produce the value for an OpenAI `image_url.url` field.

    A public http(s) reference is returned as-is so OpenAI fetches it directly;
    anything else is resolved to bytes and inlined as a data URI. `None` means the
    image's variable was not supplied and the slot stays empty.
    """
    if (reference := media_reference(image, variables)) is None:
        return None

    if (
        not inline_urls
        and isinstance(reference.value, str)
        and reference.value.startswith(("http://", "https://"))
    ):
        return reference.value

    data, media_type = resolve_media(
        reference.value, media_type=reference.media_type, client=client
    )
    return to_data_uri(data, media_type)


def _file_part(
    source: Mapping[str, Any],
    variables: Mapping[str, Any],
    client: Optional[httpx.Client],
) -> Optional[dict[str, Any]]:
    """Build a Chat Completions `file` part.

    Unlike an image, a document must carry a filename — OpenAI has no other way
    to hint its type — so the reference's basename is used, falling back to
    `document.pdf`. Mirrors the server's `playground_media/_openai.py`.

    A document is always inlined: `file_data` takes a data URL, not a fetchable
    URL, so there is no pass-through equivalent to the image path.

    `None` means the document's variable was not supplied and the slot stays empty.
    """
    resolved = resolve_media_source(
        source,
        variables,
        client,
        kind="file",
        # Every provider Phoenix supports accepts only PDF, so failing here with a
        # named media type beats an opaque provider 400.
        supported_media_types=SUPPORTED_FILE_MEDIA_TYPES,
    )
    if resolved is None:
        return None
    return {
        "type": "file",
        "file": {
            "filename": resolved.filename,
            "file_data": to_data_uri(resolved.data, resolved.media_type),
        },
    }


def _media_message(
    message: v1.PromptMessage,
    variables: Mapping[str, Any],
    formatter: BaseTemplateFormatter,
    client: Optional[httpx.Client],
    inline_urls: bool,
    unsupported: list[str],
    omitted: list[str],
) -> Iterator[Any]:
    """Convert a message containing at least one image or file part.

    Media is only meaningful on a user turn — that is what the prompt UI produces
    under "Image Input" — so the result is always a user message. Anything that
    cannot be represented there is recorded in `unsupported` rather than dropped
    without a trace, and a slot this run left empty is recorded in `omitted`.
    """
    content: list[dict[str, Any]] = []
    for part in cast(Sequence[Any], message["content"]):
        kind = part["type"]
        if kind == "text":
            content.append(
                {"type": "text", "text": _format_text(part["text"], variables, formatter)}
            )
        elif kind in _MEDIA_KINDS:
            source = cast(Mapping[str, Any], part[kind])
            converted = (
                _image_url(source, variables, client, inline_urls)
                if kind == "image"
                else _file_part(source, variables, client)
            )
            if converted is None:
                note_omitted_media(omitted, source)
            elif kind == "image":
                content.append({"type": "image_url", "image_url": {"url": converted}})
            else:
                content.append(cast(dict[str, Any], converted))
        else:
            # Tool parts are not representable on a user turn. Upstream skips
            # them silently here; recording them keeps the omission visible.
            unsupported.append(f"{kind} alongside media")
    if not content:
        # Every part was a media slot this run left empty. A user turn with no
        # content is rejected outright by the API, so the message goes too — the
        # message only ever existed to carry that media.
        return
    yield {"role": "user", "content": content}


def to_openai(
    prompt: "PromptVersion",
    *,
    variables: Mapping[str, Any] = MappingProxyType({}),
    client: Optional[httpx.Client] = None,
    inline_urls: bool = False,
) -> OpenAIMediaPrompt:
    """Render a Phoenix prompt version into OpenAI chat-completion inputs.

    Args:
        prompt: A prompt version, e.g. from `Client().prompts.get(...)`.
        variables: Values for the prompt's template variables. String values
            substitute into text; an image variable (`{{image}}` in the UI) takes
            raw bytes, base64 (str or bytes), a filesystem path, a `data:` URI, an
            `http(s)` URL, or a `MediaContent` mapping. A media variable left out
            is an empty slot, not an error — see `omitted_media`.
        client: The httpx client used to fetch image URLs. Required for images
            stored in Phoenix — pass `Client()._client`.
        inline_urls: Inline public `http(s)` images as data URIs instead of
            letting OpenAI fetch them.

    Returns:
        An `OpenAIPrompt`, which is a Mapping — splat it into
        `chat.completions.create(**result)`. Check `omitted_media` for media
        slots this run left empty.

    Raises:
        MediaResolutionError: An image reference was supplied but could not be
            turned into bytes. A variable left unsupplied is not an error — its
            part is skipped and its name reported in `omitted_media`.
    """
    obj = prompt._dumps()  # noqa: SLF001 - no public accessor for the raw data
    formatter = to_formatter(obj)
    string_variables = _string_variables(variables)

    template = obj.get("template") or {}
    messages: Sequence[v1.PromptMessage] = (
        cast(Sequence[v1.PromptMessage], template.get("messages") or [])
        if isinstance(template, Mapping)
        else []
    )

    out: list[Any] = []
    unsupported: list[str] = []
    omitted: list[str] = []
    for message in messages:
        if _has_media(message):
            out.extend(
                _media_message(
                    message, variables, formatter, client, inline_urls, unsupported, omitted
                )
            )
        else:
            # No media: upstream's conversion is authoritative.
            out.extend(_MessageConversion.to_openai(message, string_variables, formatter))

    return OpenAIMediaPrompt(out, _to_model_kwargs(obj), tuple(unsupported), tuple(omitted))
