"""
Media in the ``applyChatTemplate`` preview.

`Query.apply_chat_template` substitutes a run's variables into a chat template and
hands back the result, which the evaluator prompt preview renders. It walks the
content-part inputs in an ``if``/``elif`` chain with no ``else``, so a part it has no
branch for is silently dropped rather than reported — and it only ever had a branch
for ``image``, which meant a document or a media variable disappeared from the
preview.

The conversion lives here rather than as four more branches in that chain for the
usual fork reason: `queries.py` belongs to upstream, and an interleaved block inside
one of its functions is the shape a merge handles worst. What is left at the call site
is a single ``elif`` delegating here, which git can carry through almost any edit
upstream makes around it.
"""

from typing import Any, Optional, Protocol

from phoenix.server.api.exceptions import BadRequest
from phoenix.server.api.types.PromptVersionTemplate import (
    FileContentPart,
    ImageContentPart,
    ImageContentValue,
    ImageSource,
    ImageVariableValue,
)
from phoenix.utilities.template_formatters import TemplateFormatter, TemplateFormatterError


class HasMediaFields(Protocol):
    """
    The media fields of a content-part input.

    Structural, so nothing here has to import `PromptVersionInput` — the same shape
    `MediaContentInput.media_content_part` matches for the ORM conversion.
    """

    image: Any
    image_variable: Any
    file: Any
    file_variable: Any


def _formatted_url(url: str, formatter: TemplateFormatter, variables: dict[str, Any]) -> str:
    """
    A stored media URL with the run's variables substituted into it.

    A media URL is formatted like any other template value, so a variable can select
    which media a prompt runs against without the part itself being a media variable.

    Args:
        url: The reference recorded on the part.
        formatter: The formatter for this template format.
        variables: The values supplied for this run.

    Returns:
        The formatted reference.

    Raises:
        BadRequest: The reference does not format against the supplied variables.
    """
    try:
        return formatter.format(url, **variables)
    except TemplateFormatterError as error:
        raise BadRequest(str(error))


def _media_source(
    variable: str,
    variables: dict[str, Any],
) -> ImageSource:
    """
    What a media variable resolves to in the preview.

    Substituted when the run supplies a value, so the preview shows the media the run
    would actually use — the same thing that happens to a text variable. Left as the
    variable when nothing was supplied, which the ``ImageSource`` union already models
    and which is more useful than an empty slot.

    Args:
        variable: The media variable's name.
        variables: The values supplied for this run.

    Returns:
        The stored reference, or the variable that still names it.
    """
    value = variables.get(variable)
    if isinstance(value, str) and value.strip():
        # The preview has no way to know the media type of a value supplied at run
        # time; the reference is what identifies the bytes.
        return ImageContentValue(url=value, media_type="")
    return ImageVariableValue(variable=variable)


def media_template_content_part(
    part: HasMediaFields,
    formatter: TemplateFormatter,
    variables: dict[str, Any],
) -> Optional[Any]:
    """
    The preview content part for whichever media field is set, if any.

    Args:
        part: A content-part input.
        formatter: The formatter for this template format.
        variables: The values supplied for this run.

    Returns:
        An image or file content part, or None when the input carries no media and the
        caller should go on to its text, tool-call and tool-result branches.

    Raises:
        BadRequest: A stored reference does not format against the supplied variables.
    """
    if part.file_variable:
        return FileContentPart(file=_media_source(part.file_variable.variable, variables))
    if part.file:
        return FileContentPart(
            file=ImageContentValue(
                url=_formatted_url(part.file.url, formatter, variables),
                media_type=part.file.media_type,
            )
        )
    if part.image_variable:
        return ImageContentPart(image=_media_source(part.image_variable.variable, variables))
    if part.image:
        return ImageContentPart(
            image=ImageContentValue(
                url=_formatted_url(part.image.url, formatter, variables),
                media_type=part.image.media_type,
            )
        )
    return None
