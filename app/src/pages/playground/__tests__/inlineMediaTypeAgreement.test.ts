/**
 * The invariant the server actually enforces: an inline media part declares its type
 * twice, and the two declarations must be equal.
 *
 * `MediaContent._validate_source` (`db/types/media.py`) compares the type in a `data:`
 * URL against the part's `media_type` and raises when they differ. That raise is not
 * local — `PromptChatTemplateInput.to_orm` converts the whole template in one pass, so
 * one disagreeing part aborts the entire run.
 *
 * A round of alias normalization corrected the part's type and left the URL's alone,
 * producing exactly that pair for every alias it was supposed to rescue. Both builders
 * had the bug and both had tests, because the tests asserted the two fields separately
 * and never compared them. This file compares them, for every shape either builder can
 * be handed, so the next correction applied to one half fails here rather than in a
 * replay.
 */
import { spanMessageImages } from "../playgroundMedia";
import { rawSpanInputMessages } from "../spanRawInputMessages";

/** The media type a `data:` URL declares in its header. */
const urlMediaType = (url: string): string | null =>
  /^data:([-\w.+]+\/[-\w.+]+)/.exec(url)?.[1] ?? null;

/** Every inline media part on a message, as `{ url, mediaType }`. */
type InlinePart = { url: string; mediaType: string };

const inlineParts = (
  message:
    | {
        images?: readonly { image: InlinePart }[];
        files?: readonly { file: InlinePart }[];
      }
    | undefined
): InlinePart[] =>
  [
    ...(message?.images ?? []).map((part) => part.image),
    ...(message?.files ?? []).map((part) => part.file),
  ].filter((part) => part.url.startsWith("data:"));

/** Types worth exercising: the supported spellings, and every alias of them. */
const MEDIA_TYPES = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "image/heic",
  "image/heif",
  "application/pdf",
  "image/jpg",
  "image/JPG",
  "image/pjpeg",
  "image/x-png",
  "application/x-pdf",
];

/** Every way a builder can be handed one media type, and what it should return. */
const shapes: {
  name: string;
  parts: (mediaType: string) => InlinePart[];
}[] = [
  {
    name: "contents: message_content.image.image.url",
    parts: (mediaType) =>
      inlineParts(
        spanMessageImages([
          {
            message_content: {
              type: "image",
              image: { image: { url: `data:${mediaType};base64,QQ==` } },
            },
          },
        ])
      ),
  },
  {
    name: "raw: OpenAI image_url",
    parts: (mediaType) =>
      inlineParts(
        rawFirst([
          {
            type: "image_url",
            image_url: { url: `data:${mediaType};base64,QQ==` },
          },
        ])
      ),
  },
  {
    name: "raw: OpenAI completions file.file_data",
    parts: (mediaType) =>
      inlineParts(
        rawFirst([
          {
            type: "file",
            file: { file_data: `data:${mediaType};base64,QQ==` },
          },
        ])
      ),
  },
  {
    name: "raw: OpenAI responses input_file.file_data",
    parts: (mediaType) =>
      inlineParts(
        rawFirst([
          { type: "input_file", file_data: `data:${mediaType};base64,QQ==` },
        ])
      ),
  },
  {
    name: "raw: Google inline_data (declared type, base64 payload)",
    parts: (mediaType) =>
      inlineParts(
        rawFirst([{ inline_data: { mime_type: mediaType, data: "QQ==" } }])
      ),
  },
  {
    name: "raw: Anthropic source (declared type, base64 payload)",
    parts: (mediaType) =>
      inlineParts(
        rawFirst([
          {
            type: "image",
            source: { type: "base64", media_type: mediaType, data: "QQ==" },
          },
        ])
      ),
  },
];

function rawFirst(parts: unknown[]) {
  const messages = rawSpanInputMessages({
    input: {
      value: JSON.stringify({ messages: [{ role: "user", content: parts }] }),
    },
  });
  return messages?.[0];
}

describe.each(shapes)("$name", ({ parts }) => {
  it.each(MEDIA_TYPES)(
    "declares the same type in the URL and the part for %s",
    (mediaType) => {
      for (const part of parts(mediaType)) {
        expect(urlMediaType(part.url)).toBe(part.mediaType);
      }
    }
  );
});

describe("Bedrock, whose format name has no type prefix of its own", () => {
  it.each(["png", "jpeg", "jpg", "JPG", "gif", "webp"])(
    "agrees for format %s",
    (format) => {
      const parts = inlineParts(
        rawFirst([{ image: { format, source: { bytes: "QQ==" } } }])
      );
      expect(parts).toHaveLength(1);
      for (const part of parts) {
        expect(urlMediaType(part.url)).toBe(part.mediaType);
      }
    }
  );

  it("agrees for a document", () => {
    const parts = inlineParts(
      rawFirst([{ document: { format: "pdf", source: { bytes: "JVBERi0=" } } }])
    );
    expect(parts).toHaveLength(1);
    expect(urlMediaType(parts[0].url)).toBe(parts[0].mediaType);
  });
});
