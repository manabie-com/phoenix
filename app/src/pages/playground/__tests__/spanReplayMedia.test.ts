/**
 * Replaying a span whose messages carried an image.
 *
 * The regression these guard is not the missing image but the missing *messages*:
 * one image content part used to fail the whole `llm.input_messages` array, so the
 * playground opened on its default template and reported a parsing error, losing the
 * text messages that parsed perfectly well.
 */
import { spanMessageImages } from "../playgroundMedia";
import { getTemplateMessagesFromAttributes } from "../playgroundUtils";

const DIGEST = "b".repeat(64);
const STORED_URL = `phoenix://media/${DIGEST}`;
const INLINE_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRg==";

/** A span's `contents` entry holding an image reference. */
const imagePart = (url: string) => ({
  message_content: { type: "image", image: { image: { url } } },
});

/** A span's `contents` entry holding text. */
const textPart = (text: string) => ({
  message_content: { type: "text", text },
});

const attributesWithImageMessage = {
  llm: {
    input_messages: [
      {
        message: {
          role: "system",
          content: "You are a precise data analyst. Answer in one sentence.",
        },
      },
      {
        message: {
          role: "user",
          contents: [
            textPart("Describe the pattern in this image."),
            imagePart(STORED_URL),
          ],
        },
      },
    ],
  },
};

describe("getTemplateMessagesFromAttributes", () => {
  it("parses a multimodal span's messages instead of failing the whole list", () => {
    const { messages, messageParsingErrors } =
      getTemplateMessagesFromAttributes({
        provider: "OPENAI",
        parsedAttributes: attributesWithImageMessage,
      });

    expect(messageParsingErrors).toEqual([]);
    expect(messages).toHaveLength(2);
    expect(messages?.[0]).toMatchObject({
      role: "system",
      content: "You are a precise data analyst. Answer in one sentence.",
    });
    expect(messages?.[1]).toMatchObject({
      role: "user",
      content: "Describe the pattern in this image.",
      images: [{ image: { url: STORED_URL, mediaType: "image/png" } }],
    });
  });

  it("keeps a message with an unmodelled content part rather than dropping the span", () => {
    const { messages, messageParsingErrors } =
      getTemplateMessagesFromAttributes({
        provider: "ANTHROPIC",
        parsedAttributes: {
          llm: {
            input_messages: [
              {
                message: {
                  role: "assistant",
                  contents: [
                    {
                      message_content: {
                        type: "thinking",
                        signature: "sig",
                        id: "1",
                      },
                    },
                    textPart("done"),
                  ],
                },
              },
            ],
          },
        },
      });

    expect(messageParsingErrors).toEqual([]);
    expect(messages?.[0]).toMatchObject({ role: "ai", content: "done" });
  });
});

describe("spanMessageImages", () => {
  it("carries a stored reference with a supported placeholder media type", () => {
    expect(spanMessageImages([imagePart(STORED_URL)])).toEqual({
      images: [{ image: { url: STORED_URL, mediaType: "image/png" } }],
    });
  });

  it("takes an inline image's media type from its data URL", () => {
    expect(spanMessageImages([imagePart(INLINE_URL)])).toEqual({
      images: [{ image: { url: INLINE_URL, mediaType: "image/jpeg" } }],
    });
  });

  it("skips an external URL, which a chat completion would refuse", () => {
    expect(
      spanMessageImages([imagePart("https://example.com/cat.png")])
    ).toEqual({});
  });

  it("adds no images field to a message that carried none", () => {
    expect(spanMessageImages([textPart("just words")])).toEqual({});
    expect(spanMessageImages(undefined)).toEqual({});
  });

  it("keeps every image on a message, in the order recorded", () => {
    expect(
      spanMessageImages([
        imagePart(STORED_URL),
        textPart("and"),
        imagePart(INLINE_URL),
      ])
    ).toEqual({
      images: [
        { image: { url: STORED_URL, mediaType: "image/png" } },
        { image: { url: INLINE_URL, mediaType: "image/jpeg" } },
      ],
    });
  });
});

describe("spanMessageImages media-type gate", () => {
  /** A span `contents` entry whose image is an inline payload of the given type. */
  const inlineImagePart = (mediaType: string) => ({
    message_content: {
      type: "image",
      image: { image: { url: `data:${mediaType};base64,QQ==` } },
    },
  });

  it("carries a supported inline image", () => {
    expect(spanMessageImages([inlineImagePart("image/webp")])).toEqual({
      images: [
        {
          image: {
            url: "data:image/webp;base64,QQ==",
            mediaType: "image/webp",
          },
        },
      ],
    });
  });

  it("canonicalizes image/jpg in the URL as well as the field", () => {
    // Normalizing only the field produced `{url: "data:image/jpg;…", mediaType:
    // "image/jpeg"}`, which `MediaContent` rejects because the two disagree — so the
    // alias that was supposed to rescue the attachment killed the run instead.
    expect(spanMessageImages([inlineImagePart("image/jpg")])).toEqual({
      images: [
        {
          image: {
            url: "data:image/jpeg;base64,QQ==",
            mediaType: "image/jpeg",
          },
        },
      ],
    });
  });

  it("skips a data URL the server would refuse", () => {
    for (const url of [
      // No base64 parameter: parse_media_url refuses it.
      "data:image/png,QQ==",
      // Payload that will not decode.
      "data:image/png;base64,not!!",
      "data:image/png;base64,QQQ",
    ]) {
      expect(
        spanMessageImages([
          { message_content: { type: "image", image: { image: { url } } } },
        ])
      ).toEqual({});
    }
  });

  it("skips a type the server refuses, rather than letting it abort the run", () => {
    // PromptChatTemplateInput.to_orm converts the whole template in one pass, so an
    // unsupported part is not a lost attachment — it is a dead run.
    for (const mediaType of ["image/bmp", "image/svg+xml", "text/plain"]) {
      expect(spanMessageImages([inlineImagePart(mediaType)])).toEqual({});
    }
  });

  it("still carries a stored reference, whose type the run resolves", () => {
    const url = `phoenix://media/${"f".repeat(64)}`;
    expect(
      spanMessageImages([
        { message_content: { type: "image", image: { image: { url } } } },
      ])
    ).toEqual({ images: [{ image: { url, mediaType: "image/png" } }] });
  });
});
