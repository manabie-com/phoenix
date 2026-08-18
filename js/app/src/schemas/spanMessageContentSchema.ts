/**
 * The zod schema for one content part of an LLM message recorded on a span.
 *
 * Upstream typed a part as `z.record(z.string(), z.string())`, which holds only while
 * every part is flat text. An image part is not flat: OpenInference records the
 * reference under `message_content.image.image.url`, so the `image` key holds an
 * object. Because a part list is validated as a whole, one image part failed the
 * entire `llm.input_messages` array — span replay lost every message, including the
 * text-only ones, and fell back to the default template.
 *
 * Declared here rather than inline so the shape the fork's media reader depends on
 * has one definition, and upstream's schema keeps a single-token reference to it.
 *
 * Loose on purpose. OpenInference gives `message_content` a growing set of keys
 * (`id`, `signature`, `data` and `encrypted_content` at the time of writing), and a
 * part carrying one nothing here has modelled must still parse rather than take its
 * whole message down with it. Only `type`, `text` and `image` are named, because
 * those are the three that anything reads.
 */
import {
  ImageAttributesPostfixes,
  MessageContentsAttributePostfixes,
  SemanticAttributePrefixes,
} from "@arizeai/openinference-semantic-conventions";
import { z } from "zod";

/**
 * The image on an image content part.
 *
 * Nested twice because the attribute key is `message_content.image` followed by
 * `image.url`: unflattening that leaves an `image` holding an `image` holding a
 * `url`. It reads oddly and is nonetheless what the convention specifies.
 */
const spanImageSchema = z.looseObject({
  [MessageContentsAttributePostfixes.image]: z
    .looseObject({
      [ImageAttributesPostfixes.url]: z.string().optional(),
    })
    .optional(),
});

/** The content of one part: text, an image, or something not yet modelled. */
export const spanMessageContentSchema = z.looseObject({
  [MessageContentsAttributePostfixes.type]: z.string().optional(),
  [MessageContentsAttributePostfixes.text]: z.string().optional(),
  [MessageContentsAttributePostfixes.image]: spanImageSchema.optional(),
});

/** One entry of a message's `contents`, wrapping its content in the part prefix. */
export const spanMessageContentPartSchema = z.object({
  [SemanticAttributePrefixes.message_content]: spanMessageContentSchema,
});

export type SpanMessageContentPart = z.infer<
  typeof spanMessageContentPartSchema
>;
