# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportUnknownLambdaType=false
# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false
#
# The converted messages are OpenAI SDK TypedDicts indexed dynamically in these
# assertions, which pyright strict reads as unknown. `reportPrivateUsage` covers
# `PromptVersion._loads`, which has no public equivalent for building a version
# from raw API data.
"""Tests for the fork-owned OpenAI prompt converter.

A new test file on purpose: assertions added to an upstream test file conflict on
every upstream edit to it, for no benefit (see .claude/rules/fork-ownership.md).

The behaviour under test is what `format(sdk="openai")` gets wrong — an
`ImageContentPart` is skipped by `_ContentPartsConversion.to_openai`, so the call
succeeds with the image missing.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Sequence

import httpx
import pytest

from phoenix.client.__generated__ import v1
from phoenix.client.helpers.prompt_media import MediaResolutionError
from phoenix.client.helpers.sdk.openai_media import to_openai
from phoenix.client.types.prompts import PromptVersion

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()
EXPECTED_DATA_URI = PNG_DATA_URI  # what the converter should inline

IMAGE_VAR: Sequence[Any] = [
    {"type": "text", "text": "Describe {{subject}}:"},
    {"type": "image", "image": {"variable": "image"}},
]


def make_prompt(
    messages: Sequence[Any],
    *,
    invocation_parameters: Any = None,
    model_name: str = "gpt-4o",
) -> PromptVersion:
    # Pinned to Any: `invocation_parameters or {...}` widens to
    # `Any | dict[Any, Any]`, which the TypedDict item rejects under --strict.
    params: Any = invocation_parameters or {"type": "openai", "openai": {}}
    return PromptVersion._loads(  # noqa: SLF001 - no public constructor from raw data
        v1.PromptVersionData(
            model_provider="OPENAI",
            model_name=model_name,
            template={"type": "chat", "messages": list(messages)},
            template_type="CHAT",
            template_format="MUSTACHE",
            invocation_parameters=params,
        )
    )


def image_urls(result: Any) -> list[str]:
    urls: list[str] = []
    for message in result["messages"]:
        content = message["content"]
        if isinstance(content, str):
            continue
        for part in content:
            if part["type"] == "image_url":
                urls.append(part["image_url"]["url"])
    return urls


def texts(result: Any) -> list[str]:
    out: list[str] = []
    for message in result["messages"]:
        content = message["content"]
        if isinstance(content, str):
            out.append(content)
            continue
        out.extend(part["text"] for part in content if part["type"] == "text")
    return out


@pytest.fixture
def png_file(tmp_path: Path) -> Path:
    path = tmp_path / "cat.png"
    path.write_bytes(PNG_BYTES)
    return path


def phoenix_client(expected_path: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_path, request.url
        return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://phoenix.local")


class TestImagesSurvive:
    @pytest.mark.parametrize(
        "make_value",
        [
            pytest.param(lambda p: p, id="path"),
            pytest.param(lambda p: str(p), id="path-str"),
            pytest.param(lambda p: PNG_BYTES, id="bytes"),
            pytest.param(lambda p: base64.b64encode(PNG_BYTES).decode(), id="base64"),
            pytest.param(lambda p: PNG_DATA_URI, id="data-uri"),
        ],
    )
    def test_image_variable_becomes_a_data_uri(self, png_file: Path, make_value: Any) -> None:
        prompt = make_prompt([{"role": "user", "content": IMAGE_VAR}])
        result = to_openai(prompt, variables={"subject": "a cat", "image": make_value(png_file)})
        assert image_urls(result) == [EXPECTED_DATA_URI]
        assert texts(result) == ["Describe a cat:"]

    def test_stored_phoenix_media_is_fetched_and_inlined(self) -> None:
        sha = "c" * 64
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
        result = to_openai(prompt, client=phoenix_client(f"/v1/media/{sha}"))
        assert image_urls(result) == [EXPECTED_DATA_URI]

    def test_public_url_is_passed_through_not_downloaded(self) -> None:
        # OpenAI fetches public URLs itself, so re-encoding wastes a round trip.
        url = "https://example.com/cat.png"
        prompt = make_prompt(
            [{"role": "user", "content": [{"type": "image", "image": {"url": url}}]}]
        )
        assert image_urls(to_openai(prompt)) == [url]

    def test_inline_urls_forces_public_urls_to_be_inlined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `httpx.get` rather than the client's transport, because a URL on another
        # host is deliberately not fetched through the caller's Phoenix client —
        # that client's headers are Phoenix credentials. See `fetch_url`.
        url = "https://example.com/cat.png"
        prompt = make_prompt(
            [{"role": "user", "content": [{"type": "image", "image": {"url": url}}]}]
        )

        def fake_get(requested: str, **kwargs: Any) -> httpx.Response:
            assert requested == url
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-type": "image/png"},
                request=httpx.Request("GET", requested),
            )

        monkeypatch.setattr(httpx, "get", fake_get)
        client = httpx.Client(base_url="http://phoenix.local")
        result = to_openai(prompt, client=client, inline_urls=True)
        assert image_urls(result) == [EXPECTED_DATA_URI]

    def test_runtime_and_stored_images_coexist(self, png_file: Path) -> None:
        sha = "d" * 64
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Like this:"},
                        {"type": "image", "image": {"url": f"phoenix://media/{sha}"}},
                        {"type": "image", "image": {"variable": "image"}},
                    ],
                }
            ]
        )
        result = to_openai(
            prompt, variables={"image": png_file}, client=phoenix_client(f"/v1/media/{sha}")
        )
        assert image_urls(result) == [EXPECTED_DATA_URI, EXPECTED_DATA_URI]
        assert texts(result) == ["Like this:"]


class TestUpstreamBehaviourPreserved:
    """Messages without images must convert exactly as upstream does."""

    def test_system_message_is_untouched(self) -> None:
        prompt = make_prompt(
            [
                {"role": "system", "content": [{"type": "text", "text": "You are a chatbot"}]},
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ]
        )
        messages = to_openai(prompt)["messages"]
        assert messages[0] == {"role": "system", "content": "You are a chatbot"}

    def test_assistant_turn_is_preserved(self) -> None:
        prompt = make_prompt(
            [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a"},
            ]
        )
        roles = [m["role"] for m in to_openai(prompt)["messages"]]
        assert roles == ["user", "assistant"]

    def test_result_splats_into_the_sdk_call(self) -> None:
        prompt = make_prompt(
            [{"role": "user", "content": "hi"}],
            invocation_parameters={
                "type": "openai",
                "openai": {"temperature": 0.3, "max_tokens": 256},
            },
        )
        result = to_openai(prompt)
        assert set(result.keys()) == {"messages", "model", "temperature", "max_tokens"}
        assert result["model"] == "gpt-4o"
        assert result["temperature"] == pytest.approx(0.3)
        assert result["max_tokens"] == 256

    def test_text_variables_substitute_and_media_does_not_leak(self, png_file: Path) -> None:
        prompt = make_prompt([{"role": "user", "content": IMAGE_VAR}])
        result = to_openai(prompt, variables={"subject": "a cat", "image": png_file})
        assert texts(result) == ["Describe a cat:"]


class TestErrors:
    """A supplied image that cannot be used must raise.

    A variable left unsupplied is a different situation and is not an error; see
    `test_optional_media_variables.py`.
    """

    def test_phoenix_url_without_client_explains_the_fix(self) -> None:
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [{"type": "image", "image": {"url": "phoenix://media/abc"}}],
                }
            ]
        )
        with pytest.raises(MediaResolutionError, match="client="):
            to_openai(prompt)

    def test_non_image_text_is_rejected(self) -> None:
        prompt = make_prompt([{"role": "user", "content": IMAGE_VAR}])
        with pytest.raises(MediaResolutionError):
            to_openai(prompt, variables={"subject": "x", "image": "not an image at all"})


class TestFileParts:
    """A PDF becomes a Chat Completions `file` part, with a filename."""

    PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 60
    FILE_VAR: Sequence[Any] = [
        {"type": "text", "text": "Check document:"},
        {"type": "file", "file": {"variable": "contract_pdf"}},
    ]

    def file_parts(self, result: Any) -> list[dict[str, Any]]:
        return [
            part["file"]
            for message in result["messages"]
            if not isinstance(message["content"], str)
            for part in message["content"]
            if part["type"] == "file"
        ]

    def test_file_variable_becomes_a_file_part(self) -> None:
        prompt = make_prompt(
            [
                {"role": "system", "content": [{"type": "text", "text": "You are a chatbot"}]},
                {"role": "user", "content": self.FILE_VAR},
            ]
        )
        result = to_openai(prompt, variables={"contract_pdf": self.PDF_BYTES})
        assert result.unsupported_parts == ()
        assert texts(result) == ["You are a chatbot", "Check document:"]
        (file_part,) = self.file_parts(result)
        assert file_part["filename"] == "document.pdf"
        assert file_part["file_data"].startswith("data:application/pdf;base64,")

    @pytest.mark.parametrize(
        ("make_value", "expected_name"),
        [
            pytest.param(lambda p: p, "contract.pdf", id="path-keeps-basename"),
            pytest.param(lambda p: str(p), "contract.pdf", id="path-str-keeps-basename"),
            pytest.param(lambda p: TestFileParts.PDF_BYTES, "document.pdf", id="bytes-get-default"),
        ],
    )
    def test_filename_is_derived_from_the_reference(
        self, tmp_path: Path, make_value: Any, expected_name: str
    ) -> None:
        # OpenAI has no other way to hint a document's type, so a filename is
        # always produced — even for raw bytes.
        pdf = tmp_path / "contract.pdf"
        pdf.write_bytes(self.PDF_BYTES)
        prompt = make_prompt([{"role": "user", "content": self.FILE_VAR}])
        result = to_openai(prompt, variables={"contract_pdf": make_value(pdf)})
        assert self.file_parts(result)[0]["filename"] == expected_name

    def test_file_data_round_trips_the_bytes(self) -> None:
        prompt = make_prompt([{"role": "user", "content": self.FILE_VAR}])
        result = to_openai(prompt, variables={"contract_pdf": self.PDF_BYTES})
        payload = self.file_parts(result)[0]["file_data"].split(",", 1)[1]
        assert base64.b64decode(payload) == self.PDF_BYTES

    def test_non_pdf_file_value_is_rejected(self) -> None:
        prompt = make_prompt([{"role": "user", "content": self.FILE_VAR}])
        with pytest.raises(MediaResolutionError, match="unsupported file media type"):
            to_openai(prompt, variables={"contract_pdf": PNG_BYTES})


class TestUnsupportedReporting:
    """Anything unconvertible must be visible, never silently dropped."""

    def test_tool_part_alongside_media_is_reported(self) -> None:
        prompt = make_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "x"},
                        {"type": "image", "image": {"variable": "image"}},
                        {
                            "type": "tool_result",
                            "tool_result": {"tool_call_id": "1", "result": "r"},
                        },
                    ],
                }
            ]
        )
        result = to_openai(prompt, variables={"image": PNG_BYTES})
        assert result.unsupported_parts == ("tool_result alongside media",)

    def test_clean_prompt_reports_nothing(self) -> None:
        prompt = make_prompt([{"role": "user", "content": IMAGE_VAR}])
        result = to_openai(prompt, variables={"subject": "x", "image": PNG_BYTES})
        assert result.unsupported_parts == ()

    def test_unsupported_field_does_not_leak_into_the_splat(self) -> None:
        # The result must still be exactly the SDK's kwargs.
        prompt = make_prompt([{"role": "user", "content": "hi"}])
        assert set(to_openai(prompt).keys()) == {"messages", "model"}
