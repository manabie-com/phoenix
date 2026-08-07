"""
A media variable a run does not supply is an empty slot, not a failure.

This is the server-side half of the behaviour `phoenix-client`'s
`test_optional_media_variables.py` covers. It is the half that matters for the
Playground and for experiments driven from the UI: a dataset row reaches a prompt
through `formatted_messages`, so a prompt declaring `question_image` was, until
this behaviour existed, unusable against a dataset whose rows are mostly
text-only.

Two situations that look alike are kept apart:

* the variable was **not supplied** — drop the block, the run proceeds;
* the variable **was supplied** and cannot be used — raise `BadRequest`.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

from typing import Any

import pytest

from phoenix.db.types.prompts import PromptTemplateFormat
from phoenix.server.api.exceptions import BadRequest
from phoenix.server.api.helpers.message_helpers import PlaygroundMessage, formatted_messages
from phoenix.server.api.helpers.message_media import (
    ContentBlock,
    MediaContentBlock,
    TextContentBlock,
)
from phoenix.server.api.types.ChatCompletionMessageRole import ChatCompletionMessageRole

MEDIA_URL = f"phoenix://media/{'a' * 64}"

# Every value a text-only row can carry for a slot it does not fill. The blank
# cases matter most: a dataset column with empty cells becomes null or "", never a
# missing key, so recognising only the missing key would reject the real rows.
ABSENT_VALUES: list[Any] = [None, "", "   "]


def text(value: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=value)


def media_variable(name: str, kind: str = "image") -> MediaContentBlock:
    return MediaContentBlock(type="media", kind=kind, variable=name)  # type: ignore[typeddict-item]


def user_message(*blocks: ContentBlock) -> PlaygroundMessage:
    return PlaygroundMessage(role=ChatCompletionMessageRole.USER, content=list(blocks))


def run(
    messages: list[PlaygroundMessage],
    variables: dict[str, Any],
) -> list[PlaygroundMessage]:
    return formatted_messages(
        messages=messages,
        template_format=PromptTemplateFormat.MUSTACHE,
        template_variables=variables,
    )


def block_types(message: PlaygroundMessage) -> list[str]:
    content = message["content"]
    assert not isinstance(content, str)
    return [block["type"] for block in content]


class TestAbsentVariableIsDropped:
    @pytest.mark.parametrize("value", ABSENT_VALUES)
    def test_a_blank_value_drops_the_block(self, value: Any) -> None:
        (message,) = run(
            [user_message(text("Grade {{answer}}:"), media_variable("question_image"))],
            {"answer": "4", "question_image": value},
        )
        assert block_types(message) == ["text"]

    def test_a_missing_key_drops_the_block(self) -> None:
        (message,) = run(
            [user_message(text("Grade {{answer}}:"), media_variable("question_image"))],
            {"answer": "4"},
        )
        assert block_types(message) == ["text"]

    def test_the_surrounding_text_is_still_formatted(self) -> None:
        (message,) = run(
            [user_message(text("Grade {{answer}}:"), media_variable("question_image"))],
            {"answer": "4"},
        )
        content = message["content"]
        assert not isinstance(content, str)
        assert content[0]["text"] == "Grade 4:"

    def test_a_file_slot_behaves_the_same_way(self) -> None:
        (message,) = run(
            [user_message(text("Grade this:"), media_variable("question_file", kind="file"))],
            {},
        )
        assert block_types(message) == ["text"]


class TestSuppliedVariableIsSubstituted:
    def test_a_supplied_reference_fills_the_block(self) -> None:
        (message,) = run(
            [user_message(text("Grade this:"), media_variable("question_image"))],
            {"question_image": MEDIA_URL},
        )
        content = message["content"]
        assert not isinstance(content, str)
        assert content[1]["url"] == MEDIA_URL  # type: ignore[typeddict-item]

    def test_a_stored_reference_is_left_alone(self) -> None:
        # No variable to substitute — the template already holds the reference.
        stored = MediaContentBlock(type="media", kind="image", url=MEDIA_URL)
        (message,) = run([user_message(text("Grade this:"), stored)], {})
        assert block_types(message) == ["text", "media"]


class TestSuppliedButUnusableStillRaises:
    """Media is still never silently dropped.

    Only "you gave me nothing" has stopped being read as "you gave me something
    broken". A value that was supplied and is not a reference still fails loudly.
    """

    @pytest.mark.parametrize("value", [123, 4.5, True, {"url": MEDIA_URL}, [MEDIA_URL]])
    def test_a_non_string_value_is_rejected(self, value: Any) -> None:
        with pytest.raises(BadRequest):
            run(
                [user_message(text("Grade this:"), media_variable("question_image"))],
                {"question_image": value},
            )

    def test_the_error_names_the_right_noun_for_a_document(self) -> None:
        with pytest.raises(BadRequest, match="document"):
            run(
                [user_message(media_variable("question_file", kind="file"))],
                {"question_file": 123},
            )


class TestMixedMessage:
    def test_one_slot_filled_and_one_empty(self) -> None:
        (message,) = run(
            [
                user_message(
                    text("Grade {{answer}}:"),
                    media_variable("question_image"),
                    media_variable("answer_image"),
                )
            ],
            {"answer": "4", "question_image": MEDIA_URL},
        )
        assert block_types(message) == ["text", "media"]

    def test_both_slots_filled(self) -> None:
        (message,) = run(
            [
                user_message(
                    text("Grade {{answer}}:"),
                    media_variable("question_image"),
                    media_variable("answer_image"),
                )
            ],
            {"answer": "4", "question_image": MEDIA_URL, "answer_image": MEDIA_URL},
        )
        assert block_types(message) == ["text", "media", "media"]


class TestEmptiedMessageIsRemoved:
    """A message that was nothing but an empty slot must not survive.

    A user turn with no content at all is rejected by every provider, so leaving
    the emptied message in place would fail exactly the text-only rows this
    exists to let through — the bug moved rather than fixed.
    """

    def test_a_media_only_message_is_dropped(self) -> None:
        result = run(
            [
                PlaygroundMessage(
                    role=ChatCompletionMessageRole.SYSTEM, content=[text("You grade work.")]
                ),
                user_message(media_variable("question_image")),
            ],
            {},
        )
        assert [m["role"] for m in result] == [ChatCompletionMessageRole.SYSTEM]

    def test_a_message_keeping_its_text_survives(self) -> None:
        result = run(
            [user_message(text("Grade this:"), media_variable("question_image"))],
            {},
        )
        assert len(result) == 1

    def test_a_media_only_message_survives_when_the_slot_is_filled(self) -> None:
        result = run(
            [user_message(media_variable("question_image"))],
            {"question_image": MEDIA_URL},
        )
        assert len(result) == 1

    def test_an_empty_string_message_is_untouched(self) -> None:
        # Only a message that *had* blocks and lost them all is removed. A plain
        # string message, empty or not, is upstream's business.
        result = run([PlaygroundMessage(role=ChatCompletionMessageRole.USER, content="")], {})
        assert len(result) == 1


class TestOneDatasetAgainstOnePrompt:
    """The acceptance criterion, as a test.

    Rows built from real data are mostly text-only, with a minority carrying an
    attachment. Every row has to run against the same prompt.
    """

    ROWS: list[dict[str, Any]] = [
        {"answer": "text-only row", "question_image": None},
        {"answer": "blank-cell row", "question_image": ""},
        {"answer": "key-absent row"},
        {"answer": "attachment row", "question_image": MEDIA_URL},
    ]

    def test_every_row_formats(self) -> None:
        template = [user_message(text("Grade {{answer}}:"), media_variable("question_image"))]
        shapes = [block_types(run(template, row)[0]) for row in self.ROWS]
        assert shapes == [["text"], ["text"], ["text"], ["text", "media"]]
