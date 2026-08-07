"""
Media crossing the boundary between a span and a dataset example.

A media reference survives everywhere else in Phoenix — a prompt template holds it,
the playground substitutes it, a provider receives it, a span records it, and the
trace UI draws it. The one place it used to vanish is the hop out of a span into a
dataset: the extractor that builds an example from a span reads only text out of a
message's content blocks, so an image contributed nothing and the example came back
text-only. No error, no warning. The trace showed the picture and the example made
from that very span did not have it.

That is the same failure the media feature exists to prevent, in the one code path
nobody had checked, and it is worse than an outright error precisely because the
call succeeds.

Two functions, one for each direction across that boundary:

* :func:`span_message_media_content` — what a span's blocks become in an example;
* :func:`example_media_content_blocks` — what an example's content becomes at run
  time, so a saved example can actually be run again.

Both return ``None`` when there is no media, which leaves every text-only message
travelling the path it always did. That matters: the overwhelming majority of
examples have nothing to do with media, and reshaping them would rewrite datasets
for no reason.

**Documents are not recoverable here.** OpenInference has no document content type,
so the playground records a PDF as descriptive text when it writes the span (see
``playground_media/_tracing.py``). The reference is gone before this code is
reached. Only images round-trip.
"""

import ast
import base64
import binascii
from typing import Any, Mapping, Optional, Sequence

from openinference.semconv.trace import (
    ImageAttributes,
    MessageAttributes,
    MessageContentAttributes,
)
from sqlalchemy.ext.asyncio import AsyncSession

from phoenix.db.types.media import InlineMedia, parse_media_url
from phoenix.server.api.helpers.media import store_media
from phoenix.server.api.helpers.message_media import (
    ContentBlock,
    MediaContentBlock,
    TextContentBlock,
)
from phoenix.trace.attributes import get_attribute_value

MESSAGE_CONTENTS = MessageAttributes.MESSAGE_CONTENTS
MESSAGE_CONTENT_TEXT = MessageContentAttributes.MESSAGE_CONTENT_TEXT
MESSAGE_CONTENT_IMAGE = MessageContentAttributes.MESSAGE_CONTENT_IMAGE
IMAGE_URL = ImageAttributes.IMAGE_URL


def _is_block_sequence(value: Any) -> bool:
    """Whether a value is a list of blocks rather than a string."""
    return isinstance(value, Sequence) and not isinstance(value, str)


def _span_block_image_url(block: Mapping[str, Any]) -> Optional[str]:
    """The URL an image block on a span points at, if it is one."""
    image = get_attribute_value(block, MESSAGE_CONTENT_IMAGE)
    if not isinstance(image, Mapping):
        return None
    url = get_attribute_value(image, IMAGE_URL)
    return url if isinstance(url, str) and url else None


def span_message_media_content(
    message: Mapping[str, Any],
) -> Optional[list[dict[str, Any]]]:
    """
    A span message's content blocks, as OpenAI content parts, when media is present.

    Takes the whole message and reads ``contents`` itself, so the one line this
    replaces in upstream's extractor stays a same-line swap.

    Args:
        message: The span message, already unflattened.

    Returns:
        OpenAI-shaped content parts preserving both the text and the images, or
        ``None`` when the message carries no image — in which case the caller's own
        text extraction stays authoritative and the example keeps the plain-string
        content it has always had.

    The OpenAI shape is chosen because it is what
    ``convert_openai_message_to_internal`` documents itself as taking, so an example
    written here is one the run path can read back without a second dialect.
    """
    contents = get_attribute_value(message, MESSAGE_CONTENTS)
    if not isinstance(contents, Sequence) or isinstance(contents, str):
        return None
    blocks = [block for block in contents if isinstance(block, Mapping)]
    if not any(_span_block_image_url(block) is not None for block in blocks):
        return None

    parts: list[dict[str, Any]] = []
    for block in blocks:
        if (url := _span_block_image_url(block)) is not None:
            parts.append({"type": "image_url", "image_url": {"url": url}})
            continue
        text = get_attribute_value(block, MESSAGE_CONTENT_TEXT)
        if isinstance(text, str) and text:
            parts.append({"type": "text", "text": text})
    return parts or None


def _example_part_image_url(part: Mapping[str, Any]) -> Optional[str]:
    """
    The image URL a dataset example's content part names, if it names one.

    Liberal in what it accepts on purpose: an example's input is whatever someone
    put there. Recognised are the OpenAI part this module writes, its string-valued
    variant, and the OpenInference-flavoured shape someone gets by copying a span's
    own content.
    """
    if part.get("type") in ("image_url", "input_image"):
        image_url = part.get("image_url")
        if isinstance(image_url, str) and image_url:
            return image_url
        if isinstance(image_url, Mapping):
            url = image_url.get("url")
            return url if isinstance(url, str) and url else None
        return None
    if part.get("type") == "image":
        image = part.get("image")
        if isinstance(image, Mapping):
            url = image.get("url")
            return url if isinstance(url, str) and url else None
    return None


#: Every spelling of "this part is text". The Responses API says ``input_text``
#: where Chat Completions says ``text``, and an example built from a Responses
#: span carries the former. Recognising only ``text`` kept the image and dropped
#: the words next to it — the same silent loss this module exists to prevent,
#: just pointed at the other half of the message.
_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})


def example_media_content_blocks(
    content: Any,
) -> Optional[list[ContentBlock]]:
    """
    A dataset example's message content as playground blocks, when media is present.

    Args:
        content: The message's ``content`` from an example's input.

    Returns:
        The blocks a run needs, preserving images in the order they were authored,
        or ``None`` when the content carries no media — in which case the caller's
        existing text flattening applies unchanged.

    Without this, an example holding an image is flattened to its text on the way
    into a run: the model is asked about a picture it was never sent, and the run
    succeeds. Saving a multimodal span to a dataset and running it again has to
    send the same thing the span did, or the dataset is not a record of anything.
    """
    if not _is_block_sequence(content):
        return None
    parts = [part for part in content if isinstance(part, Mapping)]
    if not any(_example_part_image_url(part) is not None for part in parts):
        return None

    blocks: list[ContentBlock] = []
    for part in parts:
        if (url := _example_part_image_url(part)) is not None:
            blocks.append(MediaContentBlock(type="media", kind="image", url=url))
            continue
        if part.get("type") in _TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str) and text:
                blocks.append(TextContentBlock(type="text", text=text))
    return blocks or None


# Provider SDKs that cannot fetch a URL take the bytes inline, and the span records
# the request verbatim. Gemini's shape is `{"inline_data": {"mime_type", "data"}}`.
_INLINE_DATA_KEY = "inline_data"
_INLINE_PAYLOAD_KEY = "data"

#: Replaces ``data`` once the bytes are stored. A distinct name on purpose: leaving
#: a reference under ``data`` would make the field lie about holding the payload,
#: and the next reader to decode it would get a confusing failure, not a clear one.
MEDIA_URL_KEY = "phoenix_media_url"

#: A bytes literal that has been through ``str()`` rather than an encoder.
_BYTES_REPR_PREFIXES = ("b'", 'b"')


def _decode_inline_payload(payload: str) -> Optional[bytes]:
    """
    Recover the bytes from an inline payload, whatever it was serialised as.

    An SDK holds the image as ``bytes``, and what reaches ``input.value`` depends
    entirely on what serialised the request. Two shapes turn up in practice and
    they are not interchangeable:

    * **base64** — what the provider's own JSON wire format uses;
    * **a Python bytes repr** — ``b'\\x89PNG\\r\\n...'``, which is what happens
      when something reaches for ``str()`` on the bytes instead of encoding them.
      This is the shape a real Gemini span carries, and it is *worse* than base64:
      escaping every non-printable byte to ``\\xNN`` inflates the payload about
      three-fold, so a 109 KB screenshot lands as 315 KB of text.

    Only literals are evaluated, never expressions, so a hostile string can do no
    more than fail to parse.

    Args:
        payload: The recorded payload string.

    Returns:
        The bytes, or ``None`` if the string is neither shape.
    """
    if payload.startswith(_BYTES_REPR_PREFIXES):
        try:
            literal = ast.literal_eval(payload)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return None
        return literal if isinstance(literal, bytes) else None
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def _is_inline_media_part(value: Any) -> bool:
    """Whether a mapping is a provider's inline-bytes part."""
    if not isinstance(value, Mapping):
        return False
    inline = value.get(_INLINE_DATA_KEY)
    return isinstance(inline, Mapping) and isinstance(inline.get(_INLINE_PAYLOAD_KEY), str)


async def externalize_inline_media(session: AsyncSession, value: Any) -> Any:
    """
    Replace media carried inline in an example with stored references.

    A span records what was actually sent, and a provider that will not fetch a URL
    is sent the bytes — so ``input.value`` on one Gemini call runs to hundreds of
    kilobytes. Copied into a dataset, that is hundreds of kilobytes per row, in a
    column every list view reads.

    Storing the bytes and leaving a ``phoenix://media/<sha256>`` reference in their
    place keeps the image — this must never become a silent drop — while making the
    row small. It also makes the media *better* off than it was: content-addressed,
    so repeats across rows cost nothing; renderable in the UI, which now draws any
    reference it finds; and protected by the sweeper, which scans example inputs.

    Two inline shapes are recognised, and nothing else is touched:

    * a ``data:`` URL anywhere a string appears;
    * a provider inline-bytes part, whose ``data`` is swapped for
      :data:`MEDIA_URL_KEY` so that no field is left claiming to hold the payload.
      Both serialisations of that payload are handled — see
      :func:`_decode_inline_payload`, which is where the real spans differ from
      the documented wire format.

    Args:
        session: Session used to record media rows. The caller's transaction
            commits them.
        value: Any part of an example's input or output.

    Returns:
        The same structure with inline media replaced. Anything that cannot be
        stored — an unrecognised type, a corrupt payload — is returned untouched,
        because failing to shrink a row is a far better outcome than failing to
        save somebody's span.
    """
    if isinstance(value, str):
        if not value.startswith("data:"):
            return value
        try:
            reference = parse_media_url(value)
        except ValueError:
            return value
        if not isinstance(reference, InlineMedia):
            return value
        try:
            content = reference.decode()
        except ValueError:
            return value
        return await store_media(session, content) or value

    if _is_inline_media_part(value):
        inline = value[_INLINE_DATA_KEY]
        decoded = _decode_inline_payload(inline[_INLINE_PAYLOAD_KEY])
        if decoded is None:
            return value
        if (stored := await store_media(session, decoded)) is None:
            return value
        replacement = {k: v for k, v in inline.items() if k != _INLINE_PAYLOAD_KEY}
        replacement[MEDIA_URL_KEY] = stored
        return {**value, _INLINE_DATA_KEY: replacement}

    if isinstance(value, Mapping):
        return {k: await externalize_inline_media(session, v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [await externalize_inline_media(session, item) for item in value]
    return value


def with_example_media(message: Any, original: Mapping[str, Any]) -> Any:
    """
    Put media back into a message upstream's converter has already flattened.

    Applied *around* `convert_openai_message_to_internal` rather than inside it.
    That converter deliberately reduces multimodal content to text — a choice
    upstream pins with its own tests, and one that is right for a playground with
    no media support. Reaching inside it would mean rewriting those assertions and
    owning that conflict forever; wrapping its single caller costs a same-line swap
    and leaves upstream's file and tests exactly as upstream wrote them.

    Args:
        message: What the converter returned.
        original: The example message it was given, which still has the media.

    Returns:
        The message with its content restored to blocks, or unchanged when the
        original carried no media.
    """
    blocks = example_media_content_blocks(original.get("content"))
    if blocks is None:
        return message
    return {**message, "content": blocks}


__all__ = [
    "MEDIA_URL_KEY",
    "example_media_content_blocks",
    "externalize_inline_media",
    "with_example_media",
    "span_message_media_content",
]
