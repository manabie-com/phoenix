"""
Media must survive the hop out of a span into a dataset example, and back.

A media reference survives everywhere else in Phoenix. The one place it used to
vanish was here: the extractor that builds an example from a span read only text
out of a message's content blocks, so an image contributed nothing and the example
came back text-only — no error, no warning. The trace view showed the picture; the
example made from that very span did not have it.

That is the failure this feature exists to prevent, and the silent kind is the
worst kind, so both directions are pinned here:

* span → example: the image reference reaches the example;
* example → run: it is still there when the example is run again.

A new test file on purpose (see .claude/rules/fork-ownership.md).
"""

from typing import Any

import pytest

from phoenix.db.types.prompts import PromptTemplateFormat
from phoenix.server.api.helpers.dataset_example_media import (
    example_media_content_blocks,
    span_message_media_content,
)
from phoenix.server.api.helpers.message_helpers import (
    convert_openai_message_to_internal,
    extract_and_convert_example_messages,
    formatted_messages,
)

MEDIA_URL = f"phoenix://media/{'a' * 64}"


def span_text_block(text: str) -> dict[str, Any]:
    return {"message_content": {"type": "text", "text": text}}


def span_image_block(url: str = MEDIA_URL) -> dict[str, Any]:
    # Doubly nested on purpose: the semconv keys are `message_content.image` and
    # `image.url`, so an unflattened block really does read
    # `message_content.image.image.url`.
    return {"message_content": {"type": "image", "image": {"image": {"url": url}}}}


def span_message(*blocks: Any) -> dict[str, Any]:
    """A span message wrapping the given content blocks, as unflattened attributes."""
    return {"message": {"contents": list(blocks)}}


class TestSpanToExample:
    """What a span's content blocks become in a dataset example."""

    def test_an_image_block_reaches_the_example(self) -> None:
        parts = span_message_media_content(
            span_message(span_text_block("What is in this image?"), span_image_block())
        )
        assert parts == [
            {"type": "text", "text": "What is in this image?"},
            {"type": "image_url", "image_url": {"url": MEDIA_URL}},
        ]

    def test_the_authored_order_is_kept(self) -> None:
        parts = span_message_media_content(
            span_message(span_image_block(), span_text_block("describe it"))
        )
        assert parts is not None
        assert [part["type"] for part in parts] == ["image_url", "text"]

    def test_several_images_all_survive(self) -> None:
        other = f"phoenix://media/{'b' * 64}"
        parts = span_message_media_content(
            span_message(span_image_block(), span_text_block("and"), span_image_block(other))
        )
        assert parts is not None
        urls = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
        assert urls == [MEDIA_URL, other]

    def test_an_ordinary_image_url_survives_too(self) -> None:
        # Not every image a span records is Phoenix-hosted.
        parts = span_message_media_content(
            span_message(span_image_block("https://example.com/cat.png"))
        )
        assert parts == [{"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}]

    @pytest.mark.parametrize(
        "contents",
        [
            [span_text_block("just text")],
            [span_text_block("a"), span_text_block("b")],
            [],
            None,
            "a plain string",
            [{"message_content": {"type": "image"}}],  # an image block with no url
        ],
    )
    def test_no_media_leaves_the_existing_path_alone(self, contents: Any) -> None:
        # Returning None is what keeps every text-only example exactly as it was.
        # The overwhelming majority have nothing to do with media, and reshaping
        # them would rewrite datasets for no reason.
        assert span_message_media_content({"message": {"contents": contents}}) is None


class TestExampleToRun:
    """What an example's content becomes when the example is run again."""

    def test_an_image_part_becomes_a_media_block(self) -> None:
        blocks = example_media_content_blocks(
            [
                {"type": "text", "text": "grade this"},
                {"type": "image_url", "image_url": {"url": MEDIA_URL}},
            ]
        )
        assert blocks == [
            {"type": "text", "text": "grade this"},
            {"type": "media", "kind": "image", "url": MEDIA_URL},
        ]

    @pytest.mark.parametrize(
        "part",
        [
            {"type": "image_url", "image_url": {"url": MEDIA_URL}},  # what we write
            {"type": "image_url", "image_url": MEDIA_URL},  # string-valued variant
            {"type": "input_image", "image_url": MEDIA_URL},  # responses API
            {"type": "image", "image": {"url": MEDIA_URL}},  # copied from a span
        ],
    )
    def test_the_shapes_a_hand_written_example_may_use(self, part: dict[str, Any]) -> None:
        # An example's input is whatever someone put there, so this is deliberately
        # liberal about which spelling of "an image" it recognises.
        assert example_media_content_blocks([part]) == [
            {"type": "media", "kind": "image", "url": MEDIA_URL}
        ]

    @pytest.mark.parametrize(
        "content",
        [
            "a plain string",
            [{"type": "text", "text": "only text"}],
            [],
            None,
            [{"type": "image_url", "image_url": {}}],  # no url to use
        ],
    )
    def test_no_media_leaves_the_existing_flattening_alone(self, content: Any) -> None:
        assert example_media_content_blocks(content) is None


class TestResponsesApiPartNames:
    """The Responses API spells text parts `input_text`, not `text`.

    An example built from a Responses span carries that spelling. Recognising only
    `text` kept the image and dropped the words beside it — the model would be
    shown a picture with no question attached, and the run would succeed.
    """

    def test_input_text_survives_alongside_the_image(self) -> None:
        blocks = example_media_content_blocks(
            [
                {"type": "input_text", "text": "Describe this image."},
                {"type": "input_image", "detail": "auto", "image_url": MEDIA_URL},
            ]
        )
        assert blocks == [
            {"type": "text", "text": "Describe this image."},
            {"type": "media", "kind": "image", "url": MEDIA_URL},
        ]

    @pytest.mark.parametrize("part_type", ["text", "input_text", "output_text"])
    def test_every_spelling_of_a_text_part(self, part_type: str) -> None:
        blocks = example_media_content_blocks(
            [
                {"type": part_type, "text": "words"},
                {"type": "image_url", "image_url": {"url": MEDIA_URL}},
            ]
        )
        assert blocks is not None
        assert blocks[0] == {"type": "text", "text": "words"}


class TestTheRunPathActuallyUsesIt:
    """The delegation is wired, not merely available."""

    def test_appended_messages_keep_the_image(self) -> None:
        # Through the real entry point: upstream's converter still flattens, and
        # the fork puts the media back around it.
        (message,) = extract_and_convert_example_messages(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "grade this"},
                            {"type": "image_url", "image_url": {"url": MEDIA_URL}},
                        ],
                    }
                ]
            },
            "messages",
        )
        content = message["content"]
        assert not isinstance(content, str)
        assert [block["type"] for block in content] == ["text", "media"]

    def test_upstream_converter_is_left_flattening(self) -> None:
        # Pinned deliberately: the fork wraps this function, it does not change it.
        # If this ever returns blocks, the delegation moved inside upstream's file
        # and upstream's own tests will be conflicting on every sync.
        message = convert_openai_message_to_internal(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "grade this"},
                    {"type": "image_url", "image_url": {"url": MEDIA_URL}},
                ],
            }
        )
        assert message["content"] == "grade this"

    def test_convert_still_flattens_a_text_only_multimodal_message(self) -> None:
        message = convert_openai_message_to_internal(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "second"},
                ],
            }
        )
        assert message["content"] == "first\nsecond"

    def test_a_plain_string_message_is_untouched(self) -> None:
        message = convert_openai_message_to_internal({"role": "user", "content": "hello"})
        assert message["content"] == "hello"

    def test_the_image_survives_template_formatting(self) -> None:
        # The whole point: an example saved from a span, run again, still carries
        # the image when it reaches a provider.
        (message,) = extract_and_convert_example_messages(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "grade {{answer}}"},
                            {"type": "image_url", "image_url": {"url": MEDIA_URL}},
                        ],
                    }
                ]
            },
            "messages",
        )
        (formatted,) = formatted_messages(
            messages=[message],
            template_format=PromptTemplateFormat.MUSTACHE,
            template_variables={"answer": "4"},
        )
        content = formatted["content"]
        assert not isinstance(content, str)
        assert content[0]["text"] == "grade 4"  # type: ignore[typeddict-item]
        assert content[1]["url"] == MEDIA_URL  # type: ignore[typeddict-item]


class TestTheSpanPathActuallyUsesIt:
    """A span carrying an image produces an example that has it."""

    def test_get_dataset_example_input_keeps_the_image(self) -> None:
        from phoenix.server.api.helpers.dataset_helpers import _get_message

        message = _get_message(
            {
                "role": "user",
                "message": {
                    "contents": [
                        span_text_block("What is in this image?"),
                        span_image_block(),
                    ]
                },
            }
        )
        content = message["content"]
        assert not isinstance(content, str)
        assert [part["type"] for part in content] == ["text", "image_url"]
        assert content[1]["image_url"]["url"] == MEDIA_URL

    def test_a_text_only_span_message_still_yields_a_string(self) -> None:
        from phoenix.server.api.helpers.dataset_helpers import _get_message

        message = _get_message(
            {"role": "user", "message": {"contents": [span_text_block("just text")]}}
        )
        assert message["content"] == "just text"
