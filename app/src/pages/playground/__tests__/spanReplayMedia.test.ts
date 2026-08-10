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
