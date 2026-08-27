/**
 * Showing a span's media when the instrumentation recorded it only in the raw request.
 *
 * The span view draws its messages from `llm.input_messages`, and plenty of
 * instrumentation puts no media there at all. `google-adk` is the case that prompted
 * this: it records the text of every part in `message.contents` and leaves the bytes
 * behind, so a message reading "Provided in the attached file(s)" is shown with no
 * file anywhere near it. The attachment is not lost — it is in `input.value`, the
 * provider request the app actually sent — but the reader has to know to open the Raw
 * view and scroll past a megabyte of base64 to find out it exists.
 *
 * The same hole opens a second way, and this one looks like the media *was* recorded.
 * OpenInference caps a recorded image at `OPENINFERENCE_BASE64_IMAGE_MAX_LENGTH`
 * characters — 32,000 by default, roughly a 24KB picture — and writes `__REDACTED__`
 * in place of anything longer, which is every photograph. The content part is there,
 * the span view draws a tile for it, and the tile is the grey redacted placeholder.
 * The bytes are still in `input.value`, so the same recovery applies.
 *
 * OpenInference makes half of this unfixable upstream: it has no document content
 * type, so a PDF has nowhere in `message.contents` to live even when instrumentation
 * wants to record one. The bytes only ever appear in the raw payload.
 *
 * `spanRawInputMessages` already reads that payload — it is how replay recovers the
 * same media for the playground — so this grafts its finding onto the attribute
 * messages the span view renders, and nothing here re-parses a provider request.
 *
 * The recovered media is written as *image* content because that is the only media
 * the attribute shape has a place for. A PDF is not an image, and saying so in the
 * data would mean inventing a content type the rest of the app does not read;
 * `MessageContentsList` sends it to `SpanMedia` instead, which offers the document
 * rather than a broken thumbnail. That is the same compromise the fork already makes
 * for stored media whose type it cannot know without fetching the bytes.
 */
import {
  MessageAttributePostfixes,
  MessageContentsAttributePostfixes,
  SemanticAttributePrefixes,
} from "@arizeai/openinference-semantic-conventions";

import type {
  AttributeMessage,
  AttributeMessageContent,
} from "@phoenix/openInference/tracing/types";
import {
  chatRole,
  rawSpanInputMessages,
} from "@phoenix/pages/playground/spanRawInputMessages";

import type { LLMSpanAttributes } from "../utils";

/**
 * What OpenInference writes in place of an image it declined to record.
 *
 * Kept here rather than imported from `SpanImage`, which does not export it: this is
 * upstream's file, and a fork-owned copy of a five-character constant costs less than
 * an export added to it.
 */
const REDACTED_URL = "__REDACTED__";

/** One recovered attachment, in the only content shape the span view renders. */
const mediaContent = (url: string): AttributeMessageContent => ({
  [SemanticAttributePrefixes.message_content]: {
    [MessageContentsAttributePostfixes.type]: "image",
    [MessageContentsAttributePostfixes.image]: {
      [MessageContentsAttributePostfixes.image]: { url },
    },
  },
});

/** The URL a recorded content part holds as an image, or undefined for text. */
const contentImageUrl = (
  content: AttributeMessageContent
): string | undefined =>
  content[SemanticAttributePrefixes.message_content]?.[
    MessageContentsAttributePostfixes.image
  ]?.[MessageContentsAttributePostfixes.image]?.url;

/**
 * Whether a recorded message already shows media of its own.
 *
 * A redacted placeholder does not count. It is the shape of a recording without the
 * substance of one — the reader is shown a grey tile and told nothing — so treating it
 * as media properly recorded was what kept the recovery from ever running on the spans
 * that need it most.
 */
const carriesImage = (message: AttributeMessage): boolean =>
  (message[MessageAttributePostfixes.contents] ?? []).some((content) => {
    const url = contentImageUrl(content);
    return url != null && url !== REDACTED_URL;
  });

/**
 * The raw payload's messages when they describe the same conversation, else null.
 *
 * The same alignment rule replay uses, and for the same reason: two recordings of one
 * request can be matched by position, and anything else cannot. A payload that keeps
 * its system prompt outside the message list, a truncated recording, or a different
 * call entirely all fail this and are left alone — a missing attachment being a far
 * better outcome than one shown against the wrong turn.
 */
const alignedPayloadMessages = (
  messages: AttributeMessage[],
  parsedAttributes: unknown
) => {
  const fromPayload = rawSpanInputMessages(parsedAttributes);
  if (fromPayload == null || fromPayload.length !== messages.length) {
    return null;
  }
  const aligned = fromPayload.every(
    (message, index) =>
      message.role ===
      chatRole(messages[index]?.[MessageAttributePostfixes.role])
  );
  return aligned ? fromPayload : null;
};

/**
 * The same LLM attributes, with media the raw request carried added to the messages.
 *
 * Applied to the whole attribute bundle rather than to the message list alone so that
 * the span view needs a single call at the point it reads the attributes, rather than
 * threading a second value down to the component that draws a message.
 *
 * A redacted placeholder is filled **in place**, by position among the message's own
 * image parts. Appending instead would caption every picture with the wrong name: a
 * message like `Attachment {sample_answer_1.jpg}:` labels the tile that follows it, so
 * the recovered images have to land where the placeholders stood, not in a block after
 * the last line of text — and the four grey tiles would still be sitting above them.
 *
 * Images are otherwise grafted only when the recording shows none of its own, because
 * instrumentation that records images properly should not be second-guessed.
 * Documents are grafted regardless: OpenInference cannot express one, so their absence
 * from `message.contents` says nothing about whether the call had any.
 *
 * @param attributes The LLM attributes as upstream read them.
 * @param parsedAttributes The JSON-parsed span attributes.
 */
export function withRawSpanMessageMedia(
  attributes: LLMSpanAttributes,
  parsedAttributes: unknown
): LLMSpanAttributes {
  const messages = attributes.inputMessages;
  if (messages.length === 0) {
    return attributes;
  }
  const fromPayload = alignedPayloadMessages(messages, parsedAttributes);
  if (fromPayload == null) {
    return attributes;
  }
  const graftImages = !messages.some(carriesImage);
  let grafted = false;
  const inputMessages = messages.map((message, index) => {
    const { images = [], files = [] } = fromPayload[index];
    const recovered = images.map((part) => part.image.url);
    const documents = files.map((part) => part.file.url);
    const contents = message[MessageAttributePostfixes.contents] ?? [];
    // Counts every image part, not only the redacted ones, so that a message whose
    // small picture survived the cap and whose large one did not still matches each
    // placeholder to the payload image that stood in the same position.
    let imageIndex = 0;
    let filled = false;
    const withPlaceholdersFilled = contents.map((content) => {
      const url = contentImageUrl(content);
      if (url == null) {
        return content;
      }
      const replacement = recovered[imageIndex++];
      if (url !== REDACTED_URL || replacement == null) {
        return content;
      }
      filled = true;
      return mediaContent(replacement);
    });
    // What is left over after the placeholders: the whole recovered set when the
    // message recorded no image part at all, and the documents, which never have a
    // part of their own to fill.
    const appended = [
      ...(graftImages ? recovered.slice(imageIndex) : []),
      ...documents,
    ];
    if (!filled && appended.length === 0) {
      return message;
    }
    grafted = true;
    return {
      ...message,
      [MessageAttributePostfixes.contents]: [
        ...withPlaceholdersFilled,
        ...appended.map(mediaContent),
      ],
    };
  });
  return grafted ? { ...attributes, inputMessages } : attributes;
}
