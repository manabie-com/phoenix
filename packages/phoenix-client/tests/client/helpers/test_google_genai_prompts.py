# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportUnknownLambdaType=false
# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false
#
# `google.genai` ships types whose members pyright reads as partially unknown
# (`part.text`, `inline_data.mime_type`, …), so asserting on a converted part is
# unavoidably "unknown" under strict mode — a third-party typing gap, not a
# defect here. `reportPrivateUsage` covers `PromptVersion._loads`, which has no
# public equivalent for building a version from raw API data.
"""Tests for the fork-owned `google.genai` prompt converter.

A new test file on purpose: assertions added to an upstream test file conflict on
every upstream edit to it, for no benefit (see .claude/rules/fork-ownership.md).

The behaviour under test is specifically what the legacy `google_generativeai`
helper gets wrong — a system message raising `NotImplementedError`, and an
`ImageContentPart` being silently dropped so the model never sees the image.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Sequence

import httpx
import pytest

from phoenix.client.__generated__ import v1
from phoenix.client.types.prompts import PromptVersion

genai_types = pytest.importorskip("google.genai.types", reason="google-genai not installed")

from phoenix.client.helpers.sdk.google_genai import (  # noqa: E402
    MediaResolutionError,
    to_genai,
)

# Smallest valid PNG: 1x1, so the magic-byte sniffer has something real to read.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()

TEXT_AND_IMAGE_VARIABLE: Sequence[Any] = [
    {"type": "text", "text": "Describe {{subject}}:"},
    {"type": "image", "image": {"variable": "image"}},
]


def make_prompt(
    messages: Sequence[Any],
    *,
    model_name: str = "gemini-2.5-flash",
    invocation_parameters: Any = None,
) -> PromptVersion:
    # Pinned to Any: `invocation_parameters or {...}` widens to
    # `Any | dict[Any, Any]`, which the TypedDict item rejects under --strict.
    params: Any = invocation_parameters or {"type": "google", "google": {}}
    return PromptVersion._loads(  # noqa: SLF001 - no public constructor from raw data
        v1.PromptVersionData(
            model_provider="GOOGLE",
            model_name=model_name,
            template={"type": "chat", "messages": list(messages)},
            template_type="CHAT",
            template_format="MUSTACHE",
            invocation_parameters=params,
        )
    )


def parts_of(prompt: Any) -> list[tuple[str, Any]]:
    """Flatten contents into (kind, value) pairs for readable assertions."""
    out: list[tuple[str, Any]] = []
    for content in prompt.contents:
        for part in content.parts:
            if part.text is not None:
                out.append(("text", part.text))
            elif part.inline_data is not None:
                out.append(("image", part.inline_data.mime_type))
    return out


@pytest.fixture
def png_file(tmp_path: Path) -> Path:
    path = tmp_path / "cat.png"
    path.write_bytes(PNG_BYTES)
    return path


class TestSystemMessage:
    """The legacy google_generativeai helper raises NotImplementedError here."""

    def test_system_message_becomes_system_instruction(self) -> None:
        prompt = make_prompt(
            [
                {"role": "system", "content": [{"type": "text", "text": "You are a chatbot"}]},
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ]
        )
        result = to_genai(prompt)
        assert result.system_instruction == "You are a chatbot"
        assert result.config.system_instruction == "You are a chatbot"
        # The system turn must not also appear as conversation content.
        assert parts_of(result) == [("text", "hi")]

    def test_multiple_system_messages_are_joined(self) -> None:
        prompt = make_prompt(
            [
                {"role": "system", "content": [{"type": "text", "text": "first"}]},
                {"role": "developer", "content": [{"type": "text", "text": "second"}]},
                {"role": "user", "content": "go"},
            ]
        )
        assert to_genai(prompt).system_instruction == "first\n\nsecond"

    def test_no_system_message_leaves_instruction_unset(self) -> None:
        result = to_genai(make_prompt([{"role": "user", "content": "hi"}]))
        assert result.system_instruction is None


class TestImageParts:
    """An image must survive conversion; silent loss is the bug being prevented."""

    @pytest.mark.parametrize("declared_type", [None, "image/png"])
    def test_literal_image_in_template(self, declared_type: str | None) -> None:
        image: dict[str, Any] = {"url": PNG_DATA_URI}
        if declared_type:
            image["media_type"] = declared_type
        prompt = make_prompt([{"role": "user", "content": [{"type": "image", "image": image}]}])
        assert parts_of(to_genai(prompt)) == [("image", "image/png")]

    def test_image_variable_from_path(self, png_file: Path) -> None:
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        result = to_genai(prompt, variables={"subject": "a cat", "image": png_file})
        assert parts_of(result) == [("text", "Describe a cat:"), ("image", "image/png")]

    def test_image_variable_from_path_string(self, png_file: Path) -> None:
        # An absolute path also starts with "/", so it must not be read as a URL.
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        result = to_genai(prompt, variables={"subject": "x", "image": str(png_file)})
        assert parts_of(result)[1] == ("image", "image/png")

    def test_image_variable_from_raw_bytes_sniffs_type(self) -> None:
        # Raw bytes carry no filename; without sniffing this would be
        # application/octet-stream, which Gemini rejects for image input.
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        result = to_genai(prompt, variables={"subject": "x", "image": PNG_BYTES})
        assert parts_of(result)[1] == ("image", "image/png")

    def test_image_variable_from_data_uri(self) -> None:
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        result = to_genai(prompt, variables={"subject": "x", "image": PNG_DATA_URI})
        assert parts_of(result)[1] == ("image", "image/png")

    def test_image_variable_from_media_content_mapping(self) -> None:
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        result = to_genai(
            prompt,
            variables={
                "subject": "x",
                "image": {"url": PNG_DATA_URI, "media_type": "image/png"},
            },
        )
        assert parts_of(result)[1] == ("image", "image/png")

    def test_image_bytes_are_preserved_exactly(self, png_file: Path) -> None:
        prompt = make_prompt(
            [{"role": "user", "content": [{"type": "image", "image": {"variable": "i"}}]}]
        )
        result = to_genai(prompt, variables={"i": png_file})
        assert result.contents[0].parts[0].inline_data.data == PNG_BYTES

    def test_nothing_is_reported_unsupported(self, png_file: Path) -> None:
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        result = to_genai(prompt, variables={"subject": "x", "image": png_file})
        assert result.unsupported_parts == ()


class TestBase64Media:
    """base64 is accepted in every shape callers actually hand it over in.

    The dangerous case is base64 as *bytes*: without decoding, the ASCII of the
    encoding gets forwarded to the model as if it were the image.
    """

    IMAGE_VAR_ONLY: Sequence[Any] = [{"type": "image", "image": {"variable": "image"}}]

    @pytest.mark.parametrize(
        "make_value",
        [
            pytest.param(lambda b64: b64, id="bare-str"),
            pytest.param(lambda b64: b64.encode(), id="ascii-bytes"),
            pytest.param(
                lambda b64: "\n".join(b64[i : i + 40] for i in range(0, len(b64), 40)),
                id="line-wrapped",
            ),
        ],
    )
    def test_base64_is_decoded_to_the_original_bytes(self, make_value: Any) -> None:
        b64 = base64.b64encode(PNG_BYTES).decode()
        prompt = make_prompt([{"role": "user", "content": self.IMAGE_VAR_ONLY}])
        result = to_genai(prompt, variables={"image": make_value(b64)})
        inline = result.contents[0].parts[0].inline_data
        assert inline.mime_type == "image/png"
        assert inline.data == PNG_BYTES

    def test_raw_bytes_are_not_mistaken_for_base64(self) -> None:
        prompt = make_prompt([{"role": "user", "content": self.IMAGE_VAR_ONLY}])
        result = to_genai(prompt, variables={"image": PNG_BYTES})
        assert result.contents[0].parts[0].inline_data.data == PNG_BYTES

    def test_bytearray_is_accepted(self) -> None:
        prompt = make_prompt([{"role": "user", "content": self.IMAGE_VAR_ONLY}])
        result = to_genai(prompt, variables={"image": bytearray(PNG_BYTES)})
        assert result.contents[0].parts[0].inline_data.data == PNG_BYTES

    def test_jpeg_signature_is_detected(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x00" * 40
        prompt = make_prompt([{"role": "user", "content": self.IMAGE_VAR_ONLY}])
        result = to_genai(prompt, variables={"image": jpeg})
        assert result.contents[0].parts[0].inline_data.mime_type == "image/jpeg"

    @pytest.mark.parametrize(
        "not_an_image",
        [
            pytest.param("just some text", id="plain-text"),
            pytest.param(
                base64.b64encode(b"hello world, not an image at all").decode(),
                id="valid-base64-of-text",
            ),
        ],
    )
    def test_non_image_text_is_rejected_not_guessed(self, not_an_image: str) -> None:
        # Speculative base64 decoding must never turn arbitrary text into an image.
        prompt = make_prompt([{"role": "user", "content": self.IMAGE_VAR_ONLY}])
        with pytest.raises(MediaResolutionError):
            to_genai(prompt, variables={"image": not_an_image})


class TestMediaResolutionErrors:
    """Media that was supplied and cannot be used must raise, never silently drop.

    A variable left unsupplied is a different situation and is not an error; see
    `test_optional_media_variables.py`.
    """

    def test_nonexistent_path_raises(self) -> None:
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        with pytest.raises(MediaResolutionError):
            to_genai(prompt, variables={"subject": "x", "image": "/no/such/file.png"})

    def test_phoenix_url_without_client_explains_the_fix(self) -> None:
        # The scheme stored templates actually use.
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        with pytest.raises(MediaResolutionError, match="client="):
            to_genai(prompt, variables={"subject": "x", "image": "phoenix://media/abc123"})

    def test_unsupported_value_type_raises(self) -> None:
        prompt = make_prompt([{"role": "user", "content": TEXT_AND_IMAGE_VARIABLE}])
        with pytest.raises(MediaResolutionError, match="int"):
            to_genai(prompt, variables={"subject": "x", "image": 123})


class TestPhoenixHostedMedia:
    """Images stored in Phoenix resolve through the caller's client.

    A stored image is a relative URL (`/v1/media/<sha256>`) that only the
    caller's client can reach — it needs the base_url and the auth headers.
    """

    @staticmethod
    def _client(expected_path: str) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == expected_path, request.url
            return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://phoenix.local")

    def test_stored_media_url_is_fetched(self) -> None:
        sha = "a" * 64
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "image": {"url": f"phoenix://media/{sha}", "media_type": "image/png"},
                        }
                    ],
                }
            ]
        )
        result = to_genai(prompt, client=self._client(f"/v1/media/{sha}"))
        assert parts_of(result) == [("image", "image/png")]
        assert result.contents[0].parts[0].inline_data.data == PNG_BYTES

    def test_stored_media_and_runtime_variable_coexist(self) -> None:
        # The shape a few-shot prompt takes: a fixed example image plus the
        # image supplied for this particular run.
        sha = "b" * 64
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Like this example:"},
                        {"type": "image", "image": {"url": f"phoenix://media/{sha}"}},
                        {"type": "image", "image": {"variable": "image"}},
                    ],
                }
            ]
        )
        result = to_genai(
            prompt, variables={"image": PNG_BYTES}, client=self._client(f"/v1/media/{sha}")
        )
        assert parts_of(result) == [
            ("text", "Like this example:"),
            ("image", "image/png"),
            ("image", "image/png"),
        ]
        assert result.unsupported_parts == ()


class TestRolesAndConfig:
    def test_assistant_roles_map_to_model(self) -> None:
        prompt = make_prompt(
            [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a"},
                {"role": "ai", "content": "b"},
            ]
        )
        assert [c.role for c in to_genai(prompt).contents] == ["user", "model", "model"]

    def test_string_content_is_treated_as_text(self) -> None:
        result = to_genai(make_prompt([{"role": "user", "content": "plain"}]))
        assert parts_of(result) == [("text", "plain")]

    def test_model_name_is_carried_through(self) -> None:
        prompt = make_prompt([{"role": "user", "content": "x"}], model_name="gemini-2.0-pro")
        assert to_genai(prompt).model == "gemini-2.0-pro"

    def test_invocation_parameters_map_to_config(self) -> None:
        prompt = make_prompt(
            [{"role": "user", "content": "x"}],
            invocation_parameters={
                "type": "google",
                "google": {"temperature": 0.25, "max_output_tokens": 512, "top_p": 0.9},
            },
        )
        config = to_genai(prompt).config
        assert config.temperature == pytest.approx(0.25)
        assert config.max_output_tokens == 512
        assert config.top_p == pytest.approx(0.9)

    def test_text_variables_are_substituted(self) -> None:
        prompt = make_prompt([{"role": "user", "content": "hello {{name}}"}])
        assert parts_of(to_genai(prompt, variables={"name": "world"})) == [("text", "hello world")]

    def test_media_values_do_not_leak_into_text_substitution(self, png_file: Path) -> None:
        # A non-str variable must not be stringified into the prompt text.
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look at {{subject}}"},
                        {"type": "image", "image": {"variable": "image"}},
                    ],
                }
            ]
        )
        result = to_genai(prompt, variables={"subject": "this", "image": png_file})
        assert parts_of(result)[0] == ("text", "look at this")


class TestFileParts:
    """A PDF must reach the model, not land in unsupported_parts.

    Gemini carries a document on the same `inline_data` channel as an image, so
    this is the cheapest provider to support documents on.
    """

    PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 60
    FILE_VAR: Sequence[Any] = [
        {"type": "text", "text": "Check document:"},
        {"type": "file", "file": {"variable": "contract_pdf"}},
    ]

    def test_file_variable_is_inlined(self) -> None:
        prompt = make_prompt(
            [
                {"role": "system", "content": [{"type": "text", "text": "You are a chatbot"}]},
                {"role": "user", "content": self.FILE_VAR},
            ]
        )
        result = to_genai(prompt, variables={"contract_pdf": self.PDF_BYTES})
        assert result.system_instruction == "You are a chatbot"
        assert result.unsupported_parts == ()
        assert parts_of(result) == [("text", "Check document:"), ("image", "application/pdf")]

    def test_file_bytes_are_preserved_exactly(self) -> None:
        prompt = make_prompt([{"role": "user", "content": self.FILE_VAR}])
        result = to_genai(prompt, variables={"contract_pdf": self.PDF_BYTES})
        assert result.contents[0].parts[1].inline_data.data == self.PDF_BYTES

    def test_file_from_path(self, tmp_path: Path) -> None:
        pdf = tmp_path / "contract.pdf"
        pdf.write_bytes(self.PDF_BYTES)
        prompt = make_prompt([{"role": "user", "content": self.FILE_VAR}])
        result = to_genai(prompt, variables={"contract_pdf": pdf})
        assert result.contents[0].parts[1].inline_data.mime_type == "application/pdf"

    def test_stored_file_literal(self) -> None:
        uri = "data:application/pdf;base64," + base64.b64encode(self.PDF_BYTES).decode()
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {"url": uri, "media_type": "application/pdf"},
                        }
                    ],
                }
            ]
        )
        assert parts_of(to_genai(prompt)) == [("image", "application/pdf")]

    def test_non_pdf_file_value_is_rejected(self) -> None:
        # Every provider accepts only PDF, so failing here beats a provider 400.
        prompt = make_prompt([{"role": "user", "content": self.FILE_VAR}])
        with pytest.raises(MediaResolutionError, match="unsupported file media type"):
            to_genai(prompt, variables={"contract_pdf": PNG_BYTES})
