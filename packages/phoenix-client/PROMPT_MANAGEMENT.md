# Prompt management with the Python client

How to store, version, and run prompts from `arize-phoenix-client`, including
prompts that carry images.

This is a fork-owned document. Everything in the "Images" sections describes
behaviour added in this fork; everything else is upstream behaviour.

---

## Mental model

A **prompt** is a named container. A **prompt version** is an immutable snapshot
holding the whole request payload:

- the chat template (messages, each with a role and content parts)
- the model name and provider
- invocation parameters (temperature, max tokens, tools, …)
- the template format (`MUSTACHE`, `F_STRING`, or `NONE`)

There is no in-place update. Changing a prompt means **creating a new version** —
`POST /v1/prompts` with the same name. Versions accumulate, and **tags** are the
moving pointers you deploy against.

```
prompt "my-prompt"
├── version 1
├── version 2  ← tag "staging"
└── version 3  ← tag "production"
```

---

## Reading a prompt

```python
from phoenix.client import Client

client = Client()

# Latest version
prompt = client.prompts.get(prompt_identifier="my-prompt")

# A tagged version — this is what production code should use
prompt = client.prompts.get(prompt_identifier="my-prompt", tag="production")

# An exact version, fully pinned
prompt = client.prompts.get(prompt_version_id="UHJvbXB0VmVyc2lvbjox")
```

`AsyncClient` exposes the same methods with `await`.

Pin by `tag` in application code, not by version id. Promoting a new version
becomes a tag move with no deploy.

---

## Running a text prompt

`format()` returns a `Mapping`, so it splats straight into the provider SDK:

```python
import openai

prompt = client.prompts.get(prompt_identifier="my-prompt", tag="production")
formatted = prompt.format(variables={"topic": "penguins"})

response = openai.OpenAI().chat.completions.create(**formatted)
```

The mapping carries `messages` plus every invocation parameter stored on the
version, so the model, temperature, and tools all come from Phoenix. Pass
`sdk="openai" | "anthropic" | "google_generativeai"` to pick a dialect; the
default follows the version's provider.

> **`format()` does not support images.** See the limitations table below. For
> image-bearing prompts use `to_openai()` or `to_genai()`.

---

## Running a prompt with images or files

Two fork-owned converters, one per provider family. Both handle image **and**
file (PDF) parts, take the same `variables` and `client` arguments, and raise the
same `MediaResolutionError`.

| provider | import | returns |
|---|---|---|
| OpenAI | `helpers.sdk.openai_media.to_openai` | `OpenAIPrompt` — a Mapping, `**`-splattable |
| Gemini / ADK | `helpers.sdk.google_genai.to_genai` | `GenaiPrompt` — `contents` + `config` |

### OpenAI

Drop-in for `format(sdk="openai")` — the result is a Mapping, so it splats the
same way:

```python
from pathlib import Path
import openai
from phoenix.client import Client
from phoenix.client.helpers.sdk.openai_media import to_openai

client = Client()
prompt = client.prompts.get(prompt_identifier="prompt_with_image_examples")

formatted = to_openai(
    prompt,
    variables={"image": Path("cat.png"), "subject": "a cat"},
    client=client._client,  # only needed for images stored in Phoenix
)

openai.OpenAI().chat.completions.create(**formatted)
```

Messages containing no image go through upstream's own conversion, so tool
calls, system messages, and role handling behave exactly as before. Only
image-bearing messages take the fork path, and model kwargs come from upstream's
parameter mapping.

**URL handling differs from Gemini.** OpenAI fetches public URLs itself, so a
public `http(s)` image is passed through untouched rather than downloaded and
re-encoded. Everything else — bytes, base64, paths, `data:` URIs, and
Phoenix-hosted media — is inlined as a `data:` URI, which is the only way to
pass media OpenAI has no credentials for. Force inlining with
`inline_urls=True` if the model must not make outbound requests.

### Gemini and ADK

The `google.genai` converter also handles images and system messages, and its
output is what Gemini-based agent frameworks (including Google ADK) consume.

```python
from pathlib import Path
from google import genai
from phoenix.client import Client
from phoenix.client.helpers.sdk.google_genai import to_genai

client = Client()
prompt = client.prompts.get(prompt_identifier="prompt_with_image_examples")

p = to_genai(
    prompt,
    variables={"image": Path("cat.png"), "subject": "a cat"},
    client=client._client,  # only needed for images stored in Phoenix
)

genai.Client().models.generate_content(
    model=p.model,
    contents=p.contents,
    config=p.config,
)
```

`to_genai` returns a `GenaiPrompt`:

| field | meaning |
|---|---|
| `model` | model name from the version |
| `contents` | `list[genai.types.Content]`, ready to send |
| `config` | `GenerateContentConfig`, including `system_instruction` |
| `system_instruction` | the system text on its own, for agent constructors |
| `unsupported_parts` | content-part types present but not converted — `()` normally |

`to_openai` returns an `OpenAIMediaPrompt`, which is upstream's `OpenAIPrompt`
plus the same `unsupported_parts` signal. It remains a Mapping, so the extra
field never reaches the SDK call.

### Files (PDF)

A document input — `{{contract_pdf}}` in the UI — works exactly like an image
variable:

```python
p = to_genai(prompt, variables={"contract_pdf": Path("contract.pdf")})
formatted = to_openai(prompt, variables={"contract_pdf": pdf_bytes})
```

The two providers carry it differently, though both are handled for you:

| provider | wire format |
|---|---|
| Gemini | `inline_data` — the same channel as an image, only the media type differs |
| OpenAI | a `file` part with `filename` + `file_data` (a data URL) |

OpenAI requires a **filename** — it has no other way to hint the document's type
— so one is always produced: the reference's basename when there is one
(`Path("contract.pdf")` → `contract.pdf`), otherwise `document.pdf`.

Only `application/pdf` is accepted, matching every provider's server-side
allowlist. A non-PDF value raises rather than reaching the model:

```python
to_openai(prompt, variables={"contract_pdf": png_bytes})
# MediaResolutionError: unsupported file media type 'image/png'; expected one of application/pdf
```

Unlike images, a file is always inlined — `file_data` takes a data URL, not a
fetchable URL, so there is no public-URL pass-through.

### Two kinds of image in a template

**A runtime image** — shown in the UI as `{{image}}` under "Image Input",
described as *"Supplied when the prompt runs"*. Resolved from `variables`:

```python
p = to_genai(prompt, variables={"image": Path("cat.png")})
```

**A stored image** — a fixed example baked into the template, e.g. for few-shot
prompting. Stored as a `phoenix://media/<sha256>` URL, which needs your client to
resolve, since the digest maps onto an endpoint only you are authenticated for:

```python
p = to_genai(prompt, client=client._client)
```

Both can appear in the same message; pass `variables` and `client` together.

### Accepted values for an image variable

```python
variables = {"image": b"\x89PNG\r\n\x1a\n..."}  # raw bytes
variables = {"image": bytearray(...)}  # bytearray / memoryview
variables = {"image": "iVBORw0KGgoAAAANS..."}  # bare base64 string
variables = {"image": b"iVBORw0KGgoAAAANS..."}  # base64 as bytes
variables = {"image": "data:image/png;base64,iVBOR..."}  # data: URI
variables = {"image": Path("cat.png")}  # Path
variables = {"image": "/abs/path/cat.png"}  # path string
variables = {"image": "https://example.com/cat.png"}  # http(s) URL
variables = {"image": "phoenix://media/<sha256>"}  # Phoenix media (needs client=)
variables = {"image": {"url": ..., "media_type": ...}}  # MediaContent mapping
```

Line-wrapped base64 is fine — whitespace is stripped before decoding.

Text variables stay plain strings. The content part's type decides how a
variable is read, so text and media variables never collide, and non-string
values are never stringified into prompt text.

**Media type detection.** Explicit `media_type` wins; then the filename
extension; then magic bytes (PNG, JPEG, GIF, WebP, PDF). Bytes with no
recognizable signature fall back to `application/octet-stream`, which Gemini
rejects for image input — for formats outside that list, pass a `MediaContent`
mapping with an explicit `media_type`.

**Failures are loud.** An unresolvable image raises `MediaResolutionError` naming
the variable and what was expected. Images are never silently dropped.

```python
from phoenix.client.helpers.sdk.google_genai import MediaResolutionError

try:
    p = to_genai(prompt, variables={})
except MediaResolutionError as e:
    ...  # "prompt expects an image for variable 'image'; pass it as ..."
```

### With Google ADK

ADK takes the instruction as a constructor argument rather than inside a config
object, which is why `system_instruction` is exposed separately:

```python
p = to_genai(prompt, variables={"image": Path("cat.png")})

agent = LlmAgent(
    model=p.model,
    instruction=p.system_instruction,
    ...
)
```

Confirm the constructor against your ADK version — the converter's `google.genai`
output is verified, but the ADK call shape is not covered by tests here.

---

## Authoring a prompt from code

```python
from phoenix.client.types.prompts import PromptVersion

version = PromptVersion(
    [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Write about {{topic}}"},
    ],
    model_name="gpt-4o",
    model_provider="OPENAI",
    template_format="MUSTACHE",
)

client.prompts.create(
    name="my-prompt",
    version=version,
    prompt_description="Topic writer",
)
```

`create()` on an existing name adds a version rather than replacing anything.
`prompt_description` and `prompt_metadata` are ignored by the server if the
prompt already exists.

### Round-tripping from an SDK payload

If you already have a working provider request, convert it directly:

```python
PromptVersion.from_openai({...})  # a chat.completions payload
PromptVersion.from_anthropic({...})
PromptVersion.from_google_generativeai({...})
PromptVersion.from_aws({...})
```

Anything you can send, you can store.

> Media content parts are **not** covered by these constructors — they predate
> media support. Build the message dicts by hand, as below.

---

## Creating a template with image or file inputs

Media parts are plain dicts inside a message's `content`, so `PromptVersion`
takes them directly. Two forms exist, matching the two kinds of media in a
template.

### A runtime variable — the common case

This is what the UI shows as `{{image}}` under "Image Input", labelled *"Supplied
when the prompt runs"*. The template holds only the variable name; the value is
passed at run time.

```python
from phoenix.client import Client
from phoenix.client.types.prompts import PromptVersion

client = Client()

version = PromptVersion(
    [
        {"role": "system", "content": "You are a chatbot"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image", "image": {"variable": "image"}},
            ],
        },
    ],
    model_name="gemini-2.5-flash",
    model_provider="GOOGLE",
    template_format="MUSTACHE",
)

client.prompts.create(name="prompt_with_image_examples", version=version)
```

A **file** variable is identical, with `file` in place of `image`:

```python
{"type": "file", "file": {"variable": "document"}}
```

Then fill it at run time — see "Running a prompt with images" above:

```python
p = to_genai(prompt, variables={"image": Path("cat.png")})
```

### A stored image — baked into the template

Use this for fixed few-shot examples. The URL **must** be
`phoenix://media/<sha256>` or a base64 `data:` URL — the server rejects anything
else, including the `/v1/media/<sha256>` REST path:

```python
{"type": "image", "image": {"url": f"phoenix://media/{sha256}", "media_type": "image/png"}}
```

To get a `sha256` you upload the bytes first. There is still no client method for
`POST /v1/media`, so go through the underlying httpx client:

```python
from pathlib import Path

data = Path("cat.png").read_bytes()
response = client._client.post("v1/media", files={"file": ("cat.png", data, "image/png")})
response.raise_for_status()
sha256 = response.json()["data"]["sha256"]
```

`POST /v1/media/import` takes `{"data": {"url": ...}}` to import from a URL
instead. Both return the same `{"data": {"sha256", "media_type", "size_bytes",
"url"}}` body.

Alternatively skip the upload and inline the bytes in the template itself:

```python
import base64

b64 = base64.b64encode(Path("cat.png").read_bytes()).decode()
{"type": "image", "image": {"url": f"data:image/png;base64,{b64}", "media_type": "image/png"}}
```

That keeps the prompt self-contained, at the cost of storing the image in every
version that references it.

### Media types the server accepts

A declared `media_type` is validated on write and rejected if unsupported:

| kind | accepted |
|---|---|
| `image` | `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/heic`, `image/heif` |
| `file` | `application/pdf` |

A **variable** part carries no `media_type`, so nothing is validated at write
time — the type is determined when a run resolves the value. That is why an
invalid image only fails at run time, not at `create()`.

### Two caveats when authoring media prompts

**The generated client types lag the server.** `FileContentPart` exists in
`schemas/openapi.json` and in the server models, but not in
`src/phoenix/client/__generated__/v1/__init__.py`. A `{"type": "file", ...}` part
works at run time and your type checker will still reject it. The fix is
regenerating the client, never hand-editing the generated file.

**Both converters run file parts.** A `{{contract_pdf}}` document input is
resolved and inlined the same way an image is. Only `application/pdf` is accepted
— every provider Phoenix supports takes exactly that one document type — and a
non-PDF value raises `MediaResolutionError` naming the type it got, rather than
letting the provider return an opaque 400.

---

## Tags

```python
client.prompts.tags.create(prompt_version_id=version_id, name="production")
client.prompts.tags.list(prompt_version_id=version_id)
```

Combined with `get(tag="production")`, moving a tag promotes a version with no
code change.

---

## Current limitations

Measured, not assumed — each row was executed against the client.

### Media through the SDK converters

| how you run the prompt | text | images | files (PDF) |
|---|---|---|---|
| `to_openai(...)` | works | **works** | **works** |
| `to_genai(...)` | works | **works** | **works** |
| `format(sdk="openai")` | works | silently dropped | silently dropped |
| `format(sdk="anthropic")` | works | raises `AssertionError` | silently dropped |
| `format(sdk="google_generativeai")` | fails on a `system` message (`NotImplementedError`) | silently dropped | silently dropped |

The two fork converters handle media; `format()` does not. If existing code
calls `format()` on a media-bearing version it fails as above — the OpenAI case
is the dangerous one, because the call succeeds with the media missing.

Anthropic has no fork converter yet, so a media-bearing prompt cannot be run
against Anthropic models at all, even though the server supports images and PDF
for Anthropic in the playground.

### Missing client methods

| capability | server endpoint | client |
|---|---|---|
| list prompts | `GET /v1/prompts` | missing |
| list a prompt's versions | `GET /v1/prompts/{id}/versions` | missing |
| delete a prompt | `DELETE /v1/prompts/{id}` | missing |
| delete a version tag | `DELETE /v1/prompt_versions/{id}/tags/{tag}` | missing |
| upload media | `POST /v1/media` | missing |
| import media from a URL | `POST /v1/media/import` | missing |
| fetch media | `GET /v1/media/{sha256}` | missing |

Media can be *referenced* (`to_genai` fetches it) but not *created* from the
SDK — upload images through the UI for now.

### Schema

Prompt content parts are `text`, `tool_call`, `tool_result`, `image`, and
`file` — the server supports files, contrary to what the absence of
`FileContentPart` in the generated client types suggests. See "Two caveats when
authoring media prompts" above.

---

## Notes

`google-genai` is an optional dependency and is not declared as an extra.
Install it explicitly:

```bash
pip install google-genai
```

`helpers/sdk/` is excluded from mypy and pyright in `pyproject.toml`, and
`tox.ini`'s per-SDK type-check list names neither fork converter, so CI does not
type-check them. Both pass `mypy --strict` when run directly:

```bash
cd src && mypy --strict --follow-untyped-imports \
  phoenix/client/helpers/prompt_media.py \
  phoenix/client/helpers/sdk/openai_media/__init__.py \
  phoenix/client/helpers/sdk/google_genai/__init__.py
```

Media resolution is shared by both converters, in
`helpers/prompt_media.py`, so provider behaviour cannot drift on the parts that
should be identical.

Tests:

* `tests/client/helpers/test_openai_media_prompts.py` — 16 tests
* `tests/client/helpers/test_google_genai_prompts.py` — 32 tests, skipped when
  `google-genai` is absent
