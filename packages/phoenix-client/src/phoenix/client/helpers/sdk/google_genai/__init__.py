"""Convert a Phoenix prompt into `google.genai` inputs, with image support.

Fork-owned module. Nothing here lives in a file upstream also has, so it can
never conflict on a sync.

## Why this exists rather than reusing `helpers/sdk/google_generativeai`

That helper targets `google.generativeai`, whose upstream support has ended
("Please switch to the `google.genai` package"). Agent frameworks built on
Gemini — Google's ADK among them — speak `google.genai` types, so a prompt
converted by the legacy helper cannot be handed to them directly.

It also has two gaps this module does not:

* `role == "system"` raises `NotImplementedError`, so any prompt with a system
  message is unusable. `google.genai` models a system message as
  `GenerateContentConfig.system_instruction`, which is what this module emits.
* `ImageContentPart` matches none of its dispatch branches, so images are
  silently dropped — the model is asked about an image it never received.

## Images

Gemini accepts inline bytes or a GCS/YouTube URI; it will NOT fetch an
arbitrary HTTP URL. Every image therefore lands as inline bytes
(`Part.from_bytes`), which means a reference has to be resolved to bytes first.
Two forms exist in a prompt template:

* `MediaContent{url, media_type}` — a fixed image baked into the template
  (few-shot examples). Resolved by fetching `url`; pass `client` for URLs served
  by Phoenix itself, which require the caller's auth.
* `MediaVariable{variable}` — an image supplied per run, which is what the UI
  shows as `{{image}}` under "Image Input". Resolved from `variables`.

A media variable's value may be raw `bytes`, base64 (as `str` or `bytes`), a
filesystem path (`str`/`Path`), a `data:` URI, an `http(s)` URL, or a
`MediaContent` mapping. Text variables are
still plain strings — the content part's type decides how a variable is read, so
the two never collide.

## Usage

    from phoenix.client import Client
    from phoenix.client.helpers.sdk.google_genai import to_genai

    prompt = Client().prompts.get(prompt_identifier="prompt_with_image_examples")
    p = to_genai(prompt, variables={"image": Path("cat.png")})

    # Direct google.genai call
    genai.Client().models.generate_content(
        model=p.model, contents=p.contents, config=p.config
    )

    # ADK — the system message becomes the agent instruction
    LlmAgent(model=p.model, instruction=p.system_instruction, ...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence, cast

import httpx

from phoenix.client.__generated__ import v1
from phoenix.client.helpers.prompt_media import (
    SUPPORTED_FILE_MEDIA_TYPES,
    MediaResolutionError,
    resolve_media_source,
)
from phoenix.client.utils.template_formatters import (
    BaseTemplateFormatter,
    TemplateFormatterError,
    to_formatter,
)

if TYPE_CHECKING:
    from google.genai import types as genai_types

    from phoenix.client.types.prompts import PromptVersion

# MediaResolutionError is re-exported: callers catch it from the converter they
# import, without needing to know media resolution is shared across providers.
__all__ = ["GenaiPrompt", "to_genai", "MediaResolutionError"]

# Roles that google.genai understands inside `Content`. Everything else is
# either hoisted into system_instruction or mapped onto one of these.
_MODEL_ROLES = frozenset({"assistant", "model", "ai"})
_SYSTEM_ROLES = frozenset({"system", "developer"})


@dataclass(frozen=True)
class GenaiPrompt:
    """A prompt rendered into `google.genai` inputs.

    Attributes:
        model: The model name recorded on the prompt version.
        contents: Conversation turns, ready for `models.generate_content`.
        config: Invocation parameters plus `system_instruction`.
        system_instruction: The system text, also exposed on its own because
            agent frameworks take it as a separate constructor argument (ADK's
            `LlmAgent(instruction=...)`) rather than inside a config object.
    """

    model: str
    contents: list["genai_types.Content"]
    config: "genai_types.GenerateContentConfig"
    system_instruction: Optional[str] = None
    _unsupported: tuple[str, ...] = field(default=(), repr=False)

    @property
    def unsupported_parts(self) -> tuple[str, ...]:
        """Content-part types that were present but could not be converted.

        Empty for every prompt this module fully understands. Non-empty is a
        signal to look, not a silent drop — the whole failure mode this module
        exists to avoid.
        """
        return self._unsupported


def _genai_types() -> Any:
    """Import `google.genai.types` lazily.

    Keeps `google-genai` an optional dependency: importing this module, which
    the package's own test collection does, must not require it.
    """
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "google.genai is required to convert a prompt for ADK or the Gemini "
            "SDK. Install it with `pip install google-genai`."
        ) from exc
    return types


def _text_part(
    part: v1.TextContentPart,
    variables: Mapping[str, Any],
    formatter: BaseTemplateFormatter,
) -> Any:
    types = _genai_types()
    template = part["text"]
    try:
        # Only string-valued variables can take part in text substitution; media
        # values are looked up by image parts instead.
        text = formatter.format(
            template,
            variables={k: v for k, v in variables.items() if isinstance(v, str)},
        )
    except TemplateFormatterError:
        text = template
    return types.Part.from_text(text=text)


def _media_part(
    source: Mapping[str, Any],
    variables: Mapping[str, Any],
    client: Optional[httpx.Client],
    *,
    kind: str,
) -> Any:
    """Inline an image or a document as a Gemini part.

    Gemini carries a document exactly as it carries an image — same `inline_data`
    channel, only the media type differs — so one function covers both. That
    mirrors the server's own `playground_media/_google.py`.

    Gemini will not fetch an arbitrary HTTP URL, so every reference is inlined.
    """
    types = _genai_types()
    resolved = resolve_media_source(
        source,
        variables,
        client,
        kind=kind,
        # Images are validated by the provider; documents have exactly one
        # accepted type, so an early, named failure beats a provider 400.
        supported_media_types=SUPPORTED_FILE_MEDIA_TYPES if kind == "file" else None,
    )
    return types.Part.from_bytes(data=resolved.data, mime_type=resolved.media_type)


def _config(
    obj: v1.PromptVersionData,
    system_instruction: Optional[str],
) -> Any:
    """Map Phoenix invocation parameters onto GenerateContentConfig."""
    types = _genai_types()
    raw = obj.get("invocation_parameters") or {}
    params: Mapping[str, Any] = {}
    if isinstance(raw, Mapping):
        # Shape is {"type": "<provider>", "<provider>": {...}}.
        kind = raw.get("type")
        inner = raw.get(kind) if isinstance(kind, str) else None
        params = inner if isinstance(inner, Mapping) else {}

    kwargs: dict[str, Any] = {}
    for phoenix_key, genai_key in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_completion_tokens", "max_output_tokens"),
        ("max_output_tokens", "max_output_tokens"),
        ("stop_sequences", "stop_sequences"),
        ("presence_penalty", "presence_penalty"),
        ("frequency_penalty", "frequency_penalty"),
        ("seed", "seed"),
    ):
        if phoenix_key in params and params[phoenix_key] is not None:
            kwargs[genai_key] = params[phoenix_key]
    if system_instruction:
        kwargs["system_instruction"] = system_instruction
    return types.GenerateContentConfig(**kwargs)


def to_genai(
    prompt: "PromptVersion",
    *,
    variables: Mapping[str, Any] = MappingProxyType({}),
    client: Optional[httpx.Client] = None,
) -> GenaiPrompt:
    """Render a Phoenix prompt version into `google.genai` inputs.

    Args:
        prompt: A prompt version, e.g. from `Client().prompts.get(...)`.
        variables: Values for the prompt's template variables. String values
            substitute into text; an image variable (`{{image}}` in the UI) takes
            raw bytes, base64 (str or bytes), a filesystem path, a `data:` URI,
            an `http(s)` URL, or a `MediaContent` mapping.
        client: The httpx client to fetch image URLs with. Required for images
            stored in Phoenix, whose URLs need the caller's auth — pass
            `Client()._client`.

    Returns:
        A `GenaiPrompt` carrying `contents`, `config`, `system_instruction`, and
        `model`. Check `unsupported_parts` if you want to assert nothing was
        skipped.

    Raises:
        MediaResolutionError: An image reference could not be turned into bytes,
            including when a required image variable was not supplied.
        ImportError: `google-genai` is not installed.
    """
    types = _genai_types()
    obj = prompt._dumps()  # noqa: SLF001 - no public accessor for the raw data
    formatter = to_formatter(obj)

    # `template` is a union of template shapes, so `.get` widens to object; only
    # the chat shape carries messages, and a non-chat template yields none.
    template = obj.get("template") or {}
    messages: Sequence[v1.PromptMessage] = (
        cast(Sequence[v1.PromptMessage], template.get("messages") or [])
        if isinstance(template, Mapping)
        else []
    )

    system_chunks: list[str] = []
    contents: list[Any] = []
    unsupported: list[str] = []

    for message in messages:
        role = message["role"]
        content = message["content"]

        # A bare string is shorthand for a single text part.
        parts_in: Sequence[Any] = (
            [v1.TextContentPart(type="text", text=content)] if isinstance(content, str) else content
        )

        if role in _SYSTEM_ROLES:
            # google.genai has no system turn; it becomes system_instruction.
            for part in parts_in:
                if part["type"] == "text":
                    system_chunks.append(_text_part(part, variables, formatter).text or "")
                else:
                    unsupported.append(f"{part['type']} in {role} message")
            continue

        parts_out: list[Any] = []
        for part in parts_in:
            kind = part["type"]
            if kind == "text":
                parts_out.append(_text_part(part, variables, formatter))
            elif kind == "image":
                parts_out.append(
                    _media_part(
                        cast(Mapping[str, Any], part["image"]), variables, client, kind="image"
                    )
                )
            elif kind == "file":
                parts_out.append(
                    _media_part(
                        cast(Mapping[str, Any], part["file"]), variables, client, kind="file"
                    )
                )
            elif kind == "tool_call":
                call = part["tool_call"]
                parts_out.append(
                    types.Part.from_function_call(
                        name=call.get("tool_call", {}).get("name", ""),
                        args=call.get("tool_call", {}).get("arguments", {}) or {},
                    )
                )
            elif kind == "tool_result":
                result = part["tool_result"]
                parts_out.append(
                    types.Part.from_function_response(
                        name=str(result.get("tool_call_id", "")),
                        response={"result": result.get("result")},
                    )
                )
            else:
                unsupported.append(str(kind))

        if parts_out:
            contents.append(
                types.Content(
                    role="model" if role in _MODEL_ROLES else "user",
                    parts=parts_out,
                )
            )

    system_instruction = "\n\n".join(c for c in system_chunks if c) or None
    return GenaiPrompt(
        model=obj.get("model_name", ""),
        contents=contents,
        config=_config(obj, system_instruction),
        system_instruction=system_instruction,
        _unsupported=tuple(unsupported),
    )
