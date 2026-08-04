import z from "zod";

import type { PromptChatMessagePart } from "../../../types/prompts";
import { schemaMatches } from "../../../utils/schemaMatches";

/**
 * Zod schemas for the fork's media content parts.
 *
 * Fork-owned file. It exists so `messagePartSchemas.ts` — which upstream owns —
 * needs only an import plus two entries in its `discriminatedUnion` array,
 * rather than carrying an interleaved block of fork logic.
 *
 * These are not optional extras. `phoenixContentPartSchema` is wrapped in
 * `schemaMatches<PromptChatMessagePart>()`, a compile-time guard that fails when
 * the Zod union and the generated OpenAPI type diverge. The fork widened
 * `PromptMessage.content` with `image` and `file` parts, so without the schemas
 * below that guard fails and `tsc --build` — which CI runs — cannot compile the
 * package at all.
 */

/**
 * A media reference, matching the server's `MediaSource` union.
 *
 * Either a concrete pointer or a template variable resolved when the prompt
 * runs, which is what the UI shows as `{{image}}` under "Image Input".
 *
 * `media_type` is absent on the variable form on purpose: a variable's type is
 * only known once a value is supplied, so the server validates it at run time
 * rather than on write.
 */
export const mediaSourceSchema = z.union([
  z.object({
    url: z.string(),
    media_type: z.string(),
  }),
  z.object({
    variable: z.string(),
  }),
]);

export type MediaSource = z.infer<typeof mediaSourceSchema>;

export const imagePartSchema = schemaMatches<
  Extract<PromptChatMessagePart, { type: "image" }>
>()(
  z.object({
    type: z.literal("image"),
    image: mediaSourceSchema,
  })
);

export type ImagePart = z.infer<typeof imagePartSchema>;

export const filePartSchema = schemaMatches<
  Extract<PromptChatMessagePart, { type: "file" }>
>()(
  z.object({
    type: z.literal("file"),
    file: mediaSourceSchema,
  })
);

export type FilePart = z.infer<typeof filePartSchema>;

/*
 *
 * Creation helpers, mirroring the `as*Part` helpers in messagePartSchemas.ts
 *
 */

export const asImagePart = (maybePart: unknown): ImagePart | null => {
  const parsed = imagePartSchema.safeParse(maybePart);
  return parsed.success ? parsed.data : null;
};

export const asFilePart = (maybePart: unknown): FilePart | null => {
  const parsed = filePartSchema.safeParse(maybePart);
  return parsed.success ? parsed.data : null;
};
