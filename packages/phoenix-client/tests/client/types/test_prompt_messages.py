"""Tests for the fork-owned public accessor for a prompt version's messages."""

from typing import Any

import pytest

from phoenix.client.types.prompts import PromptVersion

TEXT_AND_IMAGE: list[Any] = [
    {"role": "system", "content": "You are a marker"},
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Mark this answer: {{answer}}"},
            {"type": "image", "image": {"variable": "question_image"}},
        ],
    },
]


def _version(messages: list[Any] = TEXT_AND_IMAGE) -> PromptVersion:
    return PromptVersion(
        messages,
        model_name="gemini-2.5-flash",
        model_provider="GOOGLE",
        template_format="MUSTACHE",
    )


class TestMessages:
    def test_returns_the_messages_as_authored(self) -> None:
        assert list(_version().messages) == TEXT_AND_IMAGE

    def test_is_public(self) -> None:
        """The whole point: consumers had to reach `_template["messages"]`."""
        version = _version()
        # The private read is the assertion: this pins the public accessor to the
        # exact data consumers were reaching for.
        assert version.messages == tuple(version._template["messages"])  # pyright: ignore[reportPrivateUsage]
        assert "messages" in dir(version)

    def test_round_trips_through_the_server_payload(self) -> None:
        # No public constructor from raw API data, and none from a dumped version.
        restored = PromptVersion._loads(_version()._dumps())  # pyright: ignore[reportPrivateUsage]
        assert list(restored.messages) == TEXT_AND_IMAGE

    def test_mutating_the_result_does_not_alter_the_version(self) -> None:
        version = _version()
        messages = list(version.messages)
        messages.append({"role": "user", "content": "injected"})
        assert len(version.messages) == 2

    def test_variables_are_left_unrendered(self) -> None:
        content = _version().messages[1]["content"]
        assert not isinstance(content, str)
        assert content[0]["text"] == "Mark this answer: {{answer}}"  # type: ignore[typeddict-item]

    def test_reads_a_media_bearing_prompt_without_resolving_media(self) -> None:
        """The reason `to_genai` is not a substitute: it resolves media, so it
        raises on a prompt that declares a media variable when the caller only
        wanted the text."""
        content = _version().messages[1]["content"]
        assert not isinstance(content, str)
        assert content[1] == {"type": "image", "image": {"variable": "question_image"}}

    def test_string_content_is_returned_verbatim(self) -> None:
        assert _version().messages[0]["content"] == "You are a marker"

    def test_empty_template(self) -> None:
        assert _version([]).messages == ()


class TestToGenaiComparison:
    def test_to_genai_raises_where_messages_does_not(self) -> None:
        """Documents the gap this accessor fills, so a change to either side is
        visible here."""
        genai = pytest.importorskip("google.genai")
        assert genai is not None
        from phoenix.client.helpers.prompt_media import MediaResolutionError
        from phoenix.client.helpers.sdk.google_genai import to_genai

        version = PromptVersion(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe it"},
                        {
                            "type": "image",
                            "image": {
                                "url": "phoenix://media/" + "a" * 64,
                                "media_type": "image/png",
                            },
                        },
                    ],
                }
            ],
            model_name="gemini-2.5-flash",
            model_provider="GOOGLE",
        )

        # Reading the template is fine...
        assert len(version.messages) == 1

        # ...while converting needs a client to resolve the stored media.
        with pytest.raises(MediaResolutionError):
            to_genai(version)
