"""
A response format Phoenix only passes through must not be opted into strict mode.

OpenAI's Responses API validates a ``json_schema`` format against strict
structured-output rules unless ``strict`` says otherwise, and those rules require
``additionalProperties: false`` on every object. A schema recorded from another
provider does not have it — Gemini has no such keyword — so replaying a Gemini span
against an OpenAI model failed the whole run rather than one field.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

from typing import Any

import pytest
from openai import AsyncOpenAI
from opentelemetry.trace import INVALID_SPAN

from phoenix.db.types.model_provider import LLMClientFactory
from phoenix.db.types.prompts import (
    PromptResponseFormatJSONSchema,
    PromptResponseFormatJSONSchemaDefinition,
)
from phoenix.server.api.helpers.message_helpers import PlaygroundMessage, create_playground_message
from phoenix.server.api.helpers.openai_response_format import default_openai_strict
from phoenix.server.api.helpers.playground_clients import OpenAIResponsesClient
from phoenix.server.api.types.ChatCompletionMessageRole import ChatCompletionMessageRole

#: The shape `google-adk` records and `googleAdapter.ts` promotes: no
#: `additionalProperties`, and one property left out of `required`.
GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Output",
    "properties": {
        "final_score": {"type": "number", "description": "The score"},
        "feedback": {"type": "string", "description": "The feedback"},
        "notes": {"type": "string", "description": "Optional notes"},
    },
    "required": ["final_score", "feedback"],
}


def _response_format(strict: Any = None) -> PromptResponseFormatJSONSchema:
    """The format the playground builds from a replayed Gemini span."""
    definition = (
        PromptResponseFormatJSONSchemaDefinition(name="response", schema=GEMINI_RESPONSE_SCHEMA)
        if strict is None
        else PromptResponseFormatJSONSchemaDefinition(
            name="response", schema=GEMINI_RESPONSE_SCHEMA, strict=strict
        )
    )
    return PromptResponseFormatJSONSchema(type="json_schema", json_schema=definition)


def _responses_text_format(response_format: PromptResponseFormatJSONSchema) -> Any:
    """The `text.format` an OpenAI Responses request would carry."""
    client = OpenAIResponsesClient(
        client_factory=LLMClientFactory(lambda: AsyncOpenAI(api_key="sk-test"), ("openai", "test")),
        model_name="gpt-5.6-luna",
        provider="openai",
    )
    messages: list[PlaygroundMessage] = [
        create_playground_message(ChatCompletionMessageRole.USER, "Grade this.")
    ]
    params, _ = client._openai_response_build_params(
        messages=messages,
        tools=None,
        response_format=response_format,
        span=INVALID_SPAN,
    )
    return params["text"]["format"]


class TestDefaultOpenAIStrict:
    def test_an_unspecified_strict_becomes_false(self) -> None:
        fmt: dict[str, Any] = {"type": "json_schema", "name": "response", "schema": {}}
        default_openai_strict(fmt)  # type: ignore[arg-type]
        assert fmt["strict"] is False

    @pytest.mark.parametrize("strict", [True, False], ids=["true", "false"])
    def test_an_explicit_strict_is_left_alone(self, strict: bool) -> None:
        # The caller copied this off the recording, so it is the user's decision.
        fmt: dict[str, Any] = {
            "type": "json_schema",
            "name": "response",
            "schema": {},
            "strict": strict,
        }
        default_openai_strict(fmt)  # type: ignore[arg-type]
        assert fmt["strict"] is strict


class TestReplayingAForeignSchemaAgainstOpenAI:
    def test_a_schema_without_additional_properties_is_not_sent_as_strict(self) -> None:
        # Sending this as strict is what produced "'additionalProperties' is required
        # to be supplied and to be false" and failed the run.
        assert _responses_text_format(_response_format())["strict"] is False

    def test_the_recorded_schema_is_sent_exactly_as_recorded(self) -> None:
        # Not opting into strict is the whole fix; rewriting the schema to satisfy
        # strict would promote `notes` to required and change the task.
        fmt = _responses_text_format(_response_format())
        assert fmt["schema"] == GEMINI_RESPONSE_SCHEMA
        assert "additionalProperties" not in fmt["schema"]
        assert fmt["schema"]["required"] == ["final_score", "feedback"]

    def test_the_format_still_names_and_types_itself(self) -> None:
        fmt = _responses_text_format(_response_format())
        assert fmt["type"] == "json_schema"
        assert fmt["name"] == "response"

    def test_a_format_that_asks_for_strict_still_gets_it(self) -> None:
        # A schema authored for OpenAI keeps its guarantee.
        assert _responses_text_format(_response_format(strict=True))["strict"] is True
