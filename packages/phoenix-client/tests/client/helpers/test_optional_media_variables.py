# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportPrivateUsage=false
# pyright: reportAttributeAccessIssue=false
#
# Same third-party typing gaps as the two converter test files: `google.genai`
# part members and dynamically-indexed OpenAI TypedDicts read as unknown under
# strict mode, and `PromptVersion._loads` has no public equivalent.
"""A media variable a run does not supply is an empty slot, not a failure.

One prompt has to serve both the rows that carry an attachment and the rows that
do not. A dataset built from real data is mostly the latter, so a prompt that
declares `question_image` so attachments *can* be tested was, until this
behaviour existed, unusable against that dataset: the alternatives were two
near-identical prompts, or a placeholder image on the text-only rows, which
changes what the model sees and so distorts what a reviewer is judging.

Two situations that look alike are kept apart throughout:

* the variable was **not supplied** — skip the part, the run proceeds;
* the variable **was supplied** and cannot be resolved — raise, as before.

Both converters are exercised together because they share `prompt_media` and the
whole point of that sharing is that they cannot drift.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

from __future__ import annotations

import base64
from typing import Any, Sequence

import pytest

from phoenix.client.__generated__ import v1
from phoenix.client.helpers.prompt_media import MediaResolutionError
from phoenix.client.helpers.sdk.openai_media import to_openai
from phoenix.client.types.prompts import PromptVersion

genai_types = pytest.importorskip("google.genai.types", reason="google-genai not installed")

from phoenix.client.helpers.sdk.google_genai import to_genai  # noqa: E402

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 60

TEXT_AND_IMAGE: Sequence[Any] = [
    {"type": "text", "text": "Grade {{answer}}:"},
    {"type": "image", "image": {"variable": "question_image"}},
]
IMAGE_ONLY: Sequence[Any] = [{"type": "image", "image": {"variable": "question_image"}}]
TEXT_AND_FILE: Sequence[Any] = [
    {"type": "text", "text": "Grade {{answer}}:"},
    {"type": "file", "file": {"variable": "question_file"}},
]
# The shape the ticket describes: one prompt declaring several media slots so any
# of them *can* be filled, run against rows that fill some, none, or all.
TWO_SLOTS: Sequence[Any] = [
    {"type": "text", "text": "Grade {{answer}}:"},
    {"type": "image", "image": {"variable": "question_image"}},
    {"type": "image", "image": {"variable": "answer_image"}},
]

# Every value a text-only row can carry for a slot it does not fill. The last two
# matter most: a dataset column with empty cells becomes null or "", never a
# missing key, so recognising only the missing key would reject the real rows.
ABSENT_VALUES: Sequence[Any] = [None, "", "   "]


def openai_prompt(messages: Sequence[Any]) -> PromptVersion:
    params: Any = {"type": "openai", "openai": {}}
    return PromptVersion._loads(  # noqa: SLF001 - no public constructor from raw data
        v1.PromptVersionData(
            model_provider="OPENAI",
            model_name="gpt-4o",
            template={"type": "chat", "messages": list(messages)},
            template_type="CHAT",
            template_format="MUSTACHE",
            invocation_parameters=params,
        )
    )


def genai_prompt(messages: Sequence[Any]) -> PromptVersion:
    params: Any = {"type": "google", "google": {}}
    return PromptVersion._loads(  # noqa: SLF001 - no public constructor from raw data
        v1.PromptVersionData(
            model_provider="GOOGLE",
            model_name="gemini-2.5-flash",
            template={"type": "chat", "messages": list(messages)},
            template_type="CHAT",
            template_format="MUSTACHE",
            invocation_parameters=params,
        )
    )


def openai_part_types(result: Any) -> list[str]:
    return [
        part["type"]
        for message in result["messages"]
        if not isinstance(message["content"], str)
        for part in message["content"]
    ]


def openai_texts(result: Any) -> list[str]:
    return [
        part["text"]
        for message in result["messages"]
        if not isinstance(message["content"], str)
        for part in message["content"]
        if part["type"] == "text"
    ]


def genai_part_kinds(result: Any) -> list[str]:
    """Flatten every content part to "text" or "media", in template order."""
    kinds: list[str] = []
    for content in result.contents:
        for part in content.parts:
            if part.text is not None:
                kinds.append("text")
            elif part.inline_data is not None:
                kinds.append("media")
    return kinds


class TestAbsentVariableIsSkipped:
    """The slot disappears; the rest of the message is untouched."""

    @pytest.mark.parametrize("value", ABSENT_VALUES)
    def test_openai_image_slot_is_dropped(self, value: Any) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4", "question_image": value},
        )
        assert openai_part_types(result) == ["text"]
        assert openai_texts(result) == ["Grade 4:"]

    @pytest.mark.parametrize("value", ABSENT_VALUES)
    def test_genai_image_slot_is_dropped(self, value: Any) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4", "question_image": value},
        )
        assert genai_part_kinds(result) == ["text"]

    def test_openai_omits_the_slot_when_the_key_is_missing_entirely(self) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4"},
        )
        assert openai_part_types(result) == ["text"]

    def test_genai_omits_the_slot_when_the_key_is_missing_entirely(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4"},
        )
        assert genai_part_kinds(result) == ["text"]

    def test_openai_file_slot_is_dropped(self) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TEXT_AND_FILE}]),
            variables={"answer": "4"},
        )
        assert openai_part_types(result) == ["text"]

    def test_genai_file_slot_is_dropped(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TEXT_AND_FILE}]),
            variables={"answer": "4"},
        )
        assert genai_part_kinds(result) == ["text"]


class TestSuppliedButUnresolvableStillRaises:
    """The documented property survives: media is never silently dropped.

    Only "you gave me no image" has stopped being read as "you gave me a broken
    image". A broken one still fails loudly, at the same place as before.
    """

    def test_openai_nonexistent_path_raises(self) -> None:
        with pytest.raises(MediaResolutionError):
            to_openai(
                openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
                variables={"answer": "4", "question_image": "/no/such/file.png"},
            )

    def test_genai_nonexistent_path_raises(self) -> None:
        with pytest.raises(MediaResolutionError):
            to_genai(
                genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
                variables={"answer": "4", "question_image": "/no/such/file.png"},
            )

    def test_openai_arbitrary_text_raises(self) -> None:
        with pytest.raises(MediaResolutionError):
            to_openai(
                openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
                variables={"answer": "4", "question_image": "not an image at all"},
            )

    def test_genai_wrong_value_type_raises(self) -> None:
        with pytest.raises(MediaResolutionError, match="int"):
            to_genai(
                genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
                variables={"answer": "4", "question_image": 123},
            )

    def test_genai_phoenix_url_without_client_still_raises(self) -> None:
        # A reference that was supplied and simply needs a client to fetch must
        # not be mistaken for an empty slot.
        with pytest.raises(MediaResolutionError, match="client="):
            to_genai(
                genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
                variables={"answer": "4", "question_image": "phoenix://media/abc123"},
            )

    def test_openai_non_pdf_for_a_file_slot_still_raises(self) -> None:
        with pytest.raises(MediaResolutionError, match="unsupported file media type"):
            to_openai(
                openai_prompt([{"role": "user", "content": TEXT_AND_FILE}]),
                variables={"answer": "4", "question_file": PNG_BYTES},
            )


class TestMixedMessage:
    """One slot filled and one empty, in the same message."""

    def test_openai_keeps_the_supplied_image_and_drops_the_other(self) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TWO_SLOTS}]),
            variables={"answer": "4", "question_image": PNG_BYTES},
        )
        assert openai_part_types(result) == ["text", "image_url"]
        assert result.omitted_media == ("answer_image",)

    def test_genai_keeps_the_supplied_image_and_drops_the_other(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TWO_SLOTS}]),
            variables={"answer": "4", "question_image": PNG_BYTES},
        )
        assert genai_part_kinds(result) == ["text", "media"]
        assert result.omitted_media == ("answer_image",)

    def test_openai_keeps_both_when_both_are_supplied(self) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TWO_SLOTS}]),
            variables={"answer": "4", "question_image": PNG_BYTES, "answer_image": PNG_BYTES},
        )
        assert openai_part_types(result) == ["text", "image_url", "image_url"]
        assert result.omitted_media == ()

    def test_genai_keeps_both_when_both_are_supplied(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TWO_SLOTS}]),
            variables={"answer": "4", "question_image": PNG_BYTES, "answer_image": PNG_BYTES},
        )
        assert genai_part_kinds(result) == ["text", "media", "media"]
        assert result.omitted_media == ()


class TestMediaOnlyMessage:
    """A message that was nothing but an empty slot must not survive.

    A user turn with no content at all is rejected by the provider, so leaving an
    emptied message in place would fail exactly the text-only rows this exists to
    let through — the bug, moved rather than fixed.
    """

    def test_openai_drops_the_message_entirely(self) -> None:
        result = to_openai(
            openai_prompt(
                [
                    {"role": "system", "content": [{"type": "text", "text": "You grade work."}]},
                    {"role": "user", "content": IMAGE_ONLY},
                ]
            ),
            variables={},
        )
        assert [m["role"] for m in result["messages"]] == ["system"]

    def test_genai_drops_the_message_entirely(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": IMAGE_ONLY}]),
            variables={},
        )
        assert result.contents == []

    def test_openai_keeps_a_message_that_still_has_text(self) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4"},
        )
        assert [m["role"] for m in result["messages"]] == ["user"]


class TestOmittedMediaReporting:
    """Skipping is deliberate, but it is never invisible."""

    def test_openai_reports_the_variable_it_skipped(self) -> None:
        result = to_openai(
            openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4"},
        )
        assert result.omitted_media == ("question_image",)

    def test_genai_reports_the_variable_it_skipped(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4"},
        )
        assert result.omitted_media == ("question_image",)

    def test_a_supplied_slot_is_not_reported(self) -> None:
        result = to_genai(
            genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4", "question_image": PNG_BYTES},
        )
        assert result.omitted_media == ()

    def test_omission_is_not_conflated_with_an_unconvertible_part(self) -> None:
        # `unsupported_parts` means "present but could not be converted". An empty
        # optional slot is neither, and reporting it there would make a run that
        # was meant to be text-only look broken.
        result = to_genai(
            genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}]),
            variables={"answer": "4"},
        )
        assert result.unsupported_parts == ()

    def test_one_variable_filling_two_parts_is_reported_once(self) -> None:
        repeated: Sequence[Any] = [
            {"type": "image", "image": {"variable": "question_image"}},
            {"type": "text", "text": "and again:"},
            {"type": "image", "image": {"variable": "question_image"}},
        ]
        result = to_genai(genai_prompt([{"role": "user", "content": repeated}]), variables={})
        assert result.omitted_media == ("question_image",)


class TestOneDatasetAgainstOnePrompt:
    """The acceptance criterion, as a test.

    Rows built from real data are mostly text-only, with a minority carrying an
    attachment. Every row has to run against the same prompt.
    """

    ROWS: Sequence[dict[str, Any]] = [
        {"answer": "text-only row", "question_image": None},
        {"answer": "blank-cell row", "question_image": ""},
        {"answer": "key-absent row"},
        {"answer": "attachment row", "question_image": PNG_BYTES},
    ]

    def test_every_row_converts_for_openai(self) -> None:
        prompt = openai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}])
        images = [openai_part_types(to_openai(prompt, variables=row)) for row in self.ROWS]
        assert images == [["text"], ["text"], ["text"], ["text", "image_url"]]

    def test_every_row_converts_for_genai(self) -> None:
        prompt = genai_prompt([{"role": "user", "content": TEXT_AND_IMAGE}])
        kinds = [genai_part_kinds(to_genai(prompt, variables=row)) for row in self.ROWS]
        assert kinds == [["text"], ["text"], ["text"], ["text", "media"]]

    def test_a_file_slot_behaves_the_same_way(self) -> None:
        prompt = openai_prompt([{"role": "user", "content": TEXT_AND_FILE}])
        rows: Sequence[dict[str, Any]] = [
            {"answer": "no attachment"},
            {"answer": "with attachment", "question_file": PDF_BYTES},
        ]
        assert [openai_part_types(to_openai(prompt, variables=row)) for row in rows] == [
            ["text"],
            ["text", "file"],
        ]
