"""
Media in the ``applyChatTemplate`` preview.

A fork-owned file rather than assertions inside upstream's `test_queries.py`, which
would conflict on every upstream edit to it.

The regression under test: the query walked content parts in an ``if``/``elif`` chain
with no ``else`` and a branch only for ``image``, so a document or a media variable was
dropped from the preview without a word.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from phoenix.server.api.exceptions import BadRequest
from phoenix.server.api.helpers.apply_template_media import media_template_content_part
from phoenix.server.api.input_types.MediaContentInput import (
    ImageContentValueInput,
    ImageVariableValueInput,
)
from phoenix.server.api.types.PromptVersionTemplate import (
    FileContentPart,
    ImageContentPart,
    ImageContentValue,
    ImageVariableValue,
)
from phoenix.utilities.template_formatters import (
    FStringTemplateFormatter,
    MustacheTemplateFormatter,
    NoOpFormatter,
)
from tests.unit.graphql import AsyncGraphQLClient

_DIGEST = "a" * 64
_URL = f"phoenix://media/{_DIGEST}"


class _Part:
    """
    A stand-in for `ContentPartInput`.

    The conversion is written against a structural protocol, so a test does not need
    Strawberry to build one — and `strawberry.UNSET` is falsy, which a plain None
    matches for these purposes.
    """

    def __init__(
        self,
        *,
        image: Any = None,
        image_variable: Any = None,
        file: Any = None,
        file_variable: Any = None,
    ) -> None:
        self.image = image
        self.image_variable = image_variable
        self.file = file
        self.file_variable = file_variable


def _convert(part: _Part, variables: Optional[dict[str, Any]] = None) -> Any:
    return media_template_content_part(part, NoOpFormatter(), variables or {})


class TestStoredMedia:
    def test_converts_an_image(self) -> None:
        result = _convert(_Part(image=ImageContentValueInput(url=_URL, media_type="image/png")))
        assert isinstance(result, ImageContentPart)
        assert isinstance(result.image, ImageContentValue)
        assert result.image.url == _URL
        assert result.image.media_type == "image/png"

    def test_converts_a_file(self) -> None:
        """The branch that did not exist: a PDF used to vanish from the preview."""
        result = _convert(
            _Part(file=ImageContentValueInput(url=_URL, media_type="application/pdf"))
        )
        assert isinstance(result, FileContentPart)
        assert isinstance(result.file, ImageContentValue)
        assert result.file.url == _URL
        assert result.file.media_type == "application/pdf"

    def test_returns_none_for_a_part_carrying_no_media(self) -> None:
        """So the caller falls through to its text and tool branches."""
        assert _convert(_Part()) is None


class TestMediaVariables:
    def test_an_image_variable_without_a_value_stays_a_variable(self) -> None:
        result = _convert(_Part(image_variable=ImageVariableValueInput(variable="picture")))
        assert isinstance(result, ImageContentPart)
        assert isinstance(result.image, ImageVariableValue)
        assert result.image.variable == "picture"

    def test_a_file_variable_without_a_value_stays_a_variable(self) -> None:
        result = _convert(_Part(file_variable=ImageVariableValueInput(variable="statement")))
        assert isinstance(result, FileContentPart)
        assert isinstance(result.file, ImageVariableValue)
        assert result.file.variable == "statement"

    def test_an_image_variable_is_substituted_when_a_value_is_supplied(self) -> None:
        """A media variable behaves like a text one: the preview shows the real value."""
        result = _convert(
            _Part(image_variable=ImageVariableValueInput(variable="picture")),
            {"picture": _URL},
        )
        assert isinstance(result, ImageContentPart)
        assert isinstance(result.image, ImageContentValue)
        assert result.image.url == _URL

    def test_a_file_variable_is_substituted_when_a_value_is_supplied(self) -> None:
        result = _convert(
            _Part(file_variable=ImageVariableValueInput(variable="statement")),
            {"statement": _URL},
        )
        assert isinstance(result, FileContentPart)
        assert isinstance(result.file, ImageContentValue)
        assert result.file.url == _URL

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("   ", id="blank"),
            pytest.param("", id="empty"),
            pytest.param(None, id="null"),
            pytest.param(42, id="not-a-string"),
        ],
    )
    def test_a_useless_value_leaves_the_variable_visible(self, value: Any) -> None:
        result = _convert(
            _Part(image_variable=ImageVariableValueInput(variable="picture")),
            {"picture": value},
        )
        assert isinstance(result, ImageContentPart)
        assert isinstance(result.image, ImageVariableValue)


class TestUrlFormatting:
    def test_formats_variables_inside_a_stored_url(self) -> None:
        """A variable can select which stored media a prompt runs against."""
        part = _Part(
            image=ImageContentValueInput(url="phoenix://media/{{digest}}", media_type="image/png")
        )
        result = media_template_content_part(part, MustacheTemplateFormatter(), {"digest": _DIGEST})
        assert isinstance(result, ImageContentPart)
        assert isinstance(result.image, ImageContentValue)
        assert result.image.url == _URL

    def test_reports_a_url_that_cannot_be_formatted(self) -> None:
        part = _Part(
            image=ImageContentValueInput(url="phoenix://media/{digest}", media_type="image/png")
        )
        with pytest.raises(BadRequest):
            media_template_content_part(part, FStringTemplateFormatter(), {})


class TestPrecedence:
    def test_file_wins_over_image_when_both_are_somehow_set(self) -> None:
        """
        The input is `@oneOf`, so only one field can arrive set.

        Pinned anyway so the ordering is a decision rather than an accident, and
        matching `MediaContentInput.media_content_part` keeps the preview and the ORM
        conversion from disagreeing about the same input.
        """
        result = _convert(
            _Part(
                image=ImageContentValueInput(url=_URL, media_type="image/png"),
                file=ImageContentValueInput(url=_URL, media_type="application/pdf"),
            )
        )
        assert isinstance(result, FileContentPart)


class TestApplyChatTemplateQuery:
    """The wiring: the delegation left behind in `queries.py` has to actually fire."""

    _QUERY = """
      query ($template: PromptChatTemplateInput!, $templateOptions: PromptTemplateOptions!) {
        applyChatTemplate(template: $template, templateOptions: $templateOptions) {
          messages {
            role
            content {
              __typename
              ... on TextContentPart { text { text } }
              ... on ImageContentPart {
                image {
                  __typename
                  ... on ImageContentValue { url mediaType }
                  ... on ImageVariableValue { variable }
                }
              }
              ... on FileContentPart {
                file {
                  __typename
                  ... on ImageContentValue { url mediaType }
                  ... on ImageVariableValue { variable }
                }
              }
            }
          }
        }
      }
    """

    async def _apply(
        self,
        gql_client: AsyncGraphQLClient,
        content: list[dict[str, Any]],
        variables: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        response = await gql_client.execute(
            query=self._QUERY,
            variables={
                "template": {"messages": [{"role": "USER", "content": content}]},
                "templateOptions": {"format": "MUSTACHE", "variables": variables or {}},
            },
        )
        assert not response.errors
        assert response.data is not None
        messages = response.data["applyChatTemplate"]["messages"]
        assert len(messages) == 1
        return list(messages[0]["content"])

    async def test_keeps_a_document_beside_its_text(
        self,
        gql_client: AsyncGraphQLClient,
    ) -> None:
        content = await self._apply(
            gql_client,
            [
                {"text": {"text": "summarise {{ aspect }}"}},
                {"file": {"url": _URL, "mediaType": "application/pdf"}},
            ],
            {"aspect": "the risks"},
        )
        assert [part["__typename"] for part in content] == ["TextContentPart", "FileContentPart"]
        assert content[0]["text"]["text"] == "summarise the risks"
        assert content[1]["file"]["url"] == _URL
        assert content[1]["file"]["mediaType"] == "application/pdf"

    async def test_keeps_an_image(self, gql_client: AsyncGraphQLClient) -> None:
        content = await self._apply(
            gql_client, [{"image": {"url": _URL, "mediaType": "image/png"}}]
        )
        assert [part["__typename"] for part in content] == ["ImageContentPart"]
        assert content[0]["image"]["url"] == _URL

    async def test_keeps_an_unsupplied_media_variable(
        self,
        gql_client: AsyncGraphQLClient,
    ) -> None:
        content = await self._apply(gql_client, [{"imageVariable": {"variable": "picture"}}])
        assert [part["__typename"] for part in content] == ["ImageContentPart"]
        assert content[0]["image"]["__typename"] == "ImageVariableValue"
        assert content[0]["image"]["variable"] == "picture"

    async def test_substitutes_a_supplied_media_variable(
        self,
        gql_client: AsyncGraphQLClient,
    ) -> None:
        content = await self._apply(
            gql_client,
            [{"fileVariable": {"variable": "statement"}}],
            {"statement": _URL},
        )
        assert [part["__typename"] for part in content] == ["FileContentPart"]
        assert content[0]["file"]["__typename"] == "ImageContentValue"
        assert content[0]["file"]["url"] == _URL

    async def test_text_parts_are_untouched(self, gql_client: AsyncGraphQLClient) -> None:
        """The delegation must not disturb the branches that were already there."""
        content = await self._apply(
            gql_client, [{"text": {"text": "hello {{ name }}"}}], {"name": "world"}
        )
        assert [part["__typename"] for part in content] == ["TextContentPart"]
        assert content[0]["text"]["text"] == "hello world"
