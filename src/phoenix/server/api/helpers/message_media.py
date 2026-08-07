"""
Media inside a playground message.

Held apart from `message_helpers` so the media feature reads as one module. That one
describes how a prompt template becomes the message dicts an LLM client takes, and
media touches nearly every stage of it: the block a content part becomes, the
substitution a named media variable goes through, the bytes a provider needs. Keeping
that here leaves the pipeline itself legible.

A message's content is either a plain string or an ordered list of blocks. Media
forces the list: a string cannot say where an image sits relative to the text.
"""

from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping, Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Required, TypeAlias, TypedDict

from phoenix.db.types.media import MediaContent
from phoenix.db.types.media_parts import MediaContentPart, media_source
from phoenix.server.api.exceptions import BadRequest
from phoenix.server.api.helpers.media import (
    MediaResolutionError,
    mark_media_referenced,
    resolve_media,
)
from phoenix.utilities.template_formatters import TemplateFormatter

if TYPE_CHECKING:
    from phoenix.server.api.helpers.message_helpers import PlaygroundMessage


class TextContentBlock(TypedDict):
    """A run of text within a message."""

    type: Literal["text"]
    text: str


class MediaContentBlock(TypedDict, total=False):
    """
    Binary media within a message, at one of three stages.

    ``variable`` is set when the prompt names the image rather than storing it; the
    run supplies the reference. :func:`formatted_messages` substitutes the value
    into ``url``, exactly as it substitutes text variables.

    ``url`` is the reference and is what gets recorded on the span, so that trace
    attributes stay small and stable.

    ``data`` and the authoritative ``media_type`` hold what the provider needs, and
    are populated by :func:`resolve_message_media`.
    """

    type: Required[Literal["media"]]
    kind: Required[Literal["image", "file"]]
    """Which content part this came from, which decides the provider's block shape."""
    variable: str
    url: str
    media_type: str
    data: bytes
    file_name: str
    """The stored name, for the providers that require one to carry a document."""


ContentBlock: TypeAlias = Union[TextContentBlock, MediaContentBlock]


def _media_noun(kind: str) -> str:
    """
    What to call a media kind in a message a user reads.

    A prompt carrying a PDF should not be told anything about images.
    """
    return "document" if kind == "file" else "image"


def _a_media_noun(kind: str) -> str:
    """:func:`_media_noun` with its indefinite article."""
    return "a document" if kind == "file" else "an image"


def message_text(message: "PlaygroundMessage") -> str:
    """
    The text of a message, with any media omitted.

    Lets a provider integration that cannot send media keep treating message
    content as a plain string.

    Args:
        message: The message to read.

    Returns:
        The message's text, with multiple text blocks joined by newlines.
    """
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content if block["type"] == "text")


def message_media(message: "PlaygroundMessage") -> list[MediaContentBlock]:
    """
    The media blocks of a message, in the order they appear.

    Args:
        message: The message to read.

    Returns:
        The message's media blocks, empty if it carries none.
    """
    content = message.get("content")
    if content is None or isinstance(content, str):
        return []
    return [block for block in content if block["type"] == "media"]


def content_blocks(message: "PlaygroundMessage") -> list[ContentBlock]:
    """
    The content of a message as an ordered block list.

    Args:
        message: The message to read.

    Returns:
        The message's blocks. String content becomes a single text block; empty
        string content yields no blocks.
    """
    content = message.get("content")
    if content is None:
        return []
    if isinstance(content, str):
        return [TextContentBlock(type="text", text=content)] if content else []
    return list(content)


def reject_media(messages: Iterable["PlaygroundMessage"], *, provider: str) -> None:
    """
    Refuse a message that carries media on a role that may not.

    Every supported provider takes media now, so reaching here means the media sat on
    something other than a user turn — a rule enforced when a prompt version is
    written (see `reject_media_on_non_user_role`), re-checked at the provider so that
    a message which arrived by another route fails loudly instead of being sent with
    its media silently dropped.

    Args:
        messages: The messages about to be sent.
        provider: Human-readable provider name, used in the error message.

    Raises:
        BadRequest: A message carries media on a role that may not.
    """
    for message in messages:
        if not (media := message_media(message)):
            continue
        role = message.get("role")
        role_name = role.value.lower() if role is not None else "non-user"
        noun = _media_noun(media[0]["kind"])
        raise BadRequest(
            f"{provider} cannot take {noun} content on a message with role "
            f"'{role_name}'. Media is only supported on 'user' messages."
        )


async def resolve_message_media(
    session: AsyncSession,
    messages: Iterable["PlaygroundMessage"],
) -> list["PlaygroundMessage"]:
    """
    Attach resolved bytes to every media block in the given messages.

    Call once the message list is final — after template formatting and after any
    per-example messages have been appended — so that a single batch resolves every
    reference across every message.

    Resolving is also what marks media as used, which is what keeps the sweeper from
    reclaiming it: this is the one place every path to a provider passes through, and
    the span each of those paths writes holds a reference the sweeper cannot see.

    Args:
        session: Session used to read Phoenix-hosted media.
        messages: Messages whose media should be resolved.

    Returns:
        New messages whose image blocks carry ``data`` and the authoritative
        ``media_type``. Messages without media are passed through unchanged.

    Raises:
        MediaResolutionError: A reference is malformed or names media that is not
            present.
    """
    message_list = list(messages)
    for message in message_list:
        for media_block in message_media(message):
            if "url" not in media_block:
                # formatted_messages fills a variable's reference in. Reaching here
                # means the message never went through it.
                noun = _media_noun(media_block["kind"])
                name = media_block.get("variable")
                target = f"'{name}'" if name else f"the {noun}"
                raise MediaResolutionError(f"No {noun} reference was substituted for {target}.")
    urls = [block["url"] for message in message_list for block in message_media(message)]
    if not urls:
        return message_list

    resolved = await resolve_media(session, urls)
    await mark_media_referenced(session, urls)
    output: list["PlaygroundMessage"] = []
    for message in message_list:
        content = message.get("content")
        if isinstance(content, str) or not content:
            output.append(message)
            continue
        blocks: list[ContentBlock] = []
        for block in content:
            if block["type"] != "media":
                blocks.append(block)
                continue
            media = resolved[block["url"]]
            resolved_block = MediaContentBlock(
                type="media",
                kind=block["kind"],
                url=block["url"],
                media_type=media.media_type,
                data=media.content,
            )
            if media.file_name is not None:
                resolved_block["file_name"] = media.file_name
            if (variable := block.get("variable")) is not None:
                resolved_block["variable"] = variable
            blocks.append(resolved_block)
        output.append({**message, "content": blocks})
    return output


def _media_variable_value(
    variable: str,
    template_variables: Mapping[str, Any],
    *,
    kind: str,
) -> Optional[str]:
    """
    The media reference supplied for a media variable.

    A media slot is optional. One prompt has to serve both the runs that fill it and
    the runs that do not — a dataset holding attachment-bearing and text-only rows is
    the ordinary case — so a slot left empty yields ``None`` and the block is dropped.
    The alternative is maintaining two near-identical prompts, or attaching a
    placeholder image to the text-only rows, which changes what the model sees.

    An empty slot arrives in three shapes and all three mean the same thing: the key
    is missing, the value is ``None``, or the value is blank. The last two are what a
    dataset column with empty cells becomes, so recognising only the missing key would
    reject exactly the rows this admits.

    What stays an error is a value that *was* supplied and cannot be used — a number,
    an object, anything that is not a reference. Media is still never silently
    dropped; "you gave me nothing" has simply stopped being read as "you gave me
    something broken".

    Args:
        variable: The media variable's name.
        template_variables: The values supplied for this run.
        kind: Which kind of media the part expects, so the error names it correctly.

    Returns:
        The reference to resolve, e.g. ``phoenix://media/<sha256>``, or ``None`` when
        this run supplied nothing for the slot.

    Raises:
        BadRequest: A value was supplied but is not a reference string.
    """
    value = template_variables.get(variable)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise BadRequest(
            f"The value supplied for '{variable}' is not {_a_media_noun(kind)} reference. "
            f"Upload one for it and try again."
        )
    return value


def media_content_block(part: MediaContentPart) -> MediaContentBlock:
    """
    The block an image or file content part becomes.

    Args:
        part: The content part to convert.

    Returns:
        A block holding the stored reference, or naming the variable that will supply
        one when the prompt runs.
    """
    source = media_source(part)
    if isinstance(source, MediaContent):
        return MediaContentBlock(
            type="media",
            kind=part.type,
            url=source.url,
            media_type=source.media_type,
        )
    return MediaContentBlock(type="media", kind=part.type, variable=source.variable)


def format_message_content(
    content: Union[str, list["ContentBlock"], None],
    *,
    template_formatter: TemplateFormatter,
    template_variables: Mapping[str, Any],
) -> Union[str, list["ContentBlock"]]:
    """
    A message's content with its variables substituted.

    Text is formatted. A media block naming a variable takes its reference from that
    variable — the same substitution, applied to a different kind of content. A media
    block already holding a stored reference is left alone.

    A media block whose variable this run did not supply is dropped, leaving the rest
    of the message intact. See :func:`_media_variable_value` for why an empty slot is
    a normal outcome rather than an error.

    Args:
        content: The message's content, string or blocks.
        template_formatter: The formatter for this template format.
        template_variables: The values supplied for this run.

    Returns:
        The content, in whichever shape it arrived.

    Raises:
        BadRequest: A media variable was given a value that is not a reference.
    """
    if isinstance(content, str) or content is None:
        return template_formatter.format(content or "", **template_variables)
    blocks: list[ContentBlock] = []
    for block in content:
        if block["type"] == "text":
            blocks.append(
                TextContentBlock(
                    type="text",
                    text=template_formatter.format(block["text"], **template_variables),
                )
            )
        elif (variable := block.get("variable")) is not None:
            url = _media_variable_value(variable, template_variables, kind=block["kind"])
            if url is None:
                continue
            blocks.append(
                MediaContentBlock(
                    type="media",
                    kind=block["kind"],
                    variable=variable,
                    url=url,
                )
            )
        else:
            blocks.append(block)
    return blocks


def content_was_emptied(
    original: Union[str, list["ContentBlock"], None],
    formatted: Union[str, list["ContentBlock"]],
) -> bool:
    """
    Whether substitution removed every block a message had.

    Only an unsupplied media block is ever dropped, so this is exactly the message that
    existed solely to carry media this run did not supply — a template whose user turn
    is nothing but ``{{question_image}}``.

    Such a message must not reach a provider. A user turn with no content at all is
    rejected outright, so passing it on would turn a row that should simply run without
    an attachment into a failed one, which is the outcome optional media exists to
    avoid.

    Args:
        original: The message's content before substitution.
        formatted: The same content afterwards.

    Returns:
        True when the message had blocks and now has none.
    """
    return (
        isinstance(original, list)
        and bool(original)
        and isinstance(formatted, list)
        and not formatted
    )
