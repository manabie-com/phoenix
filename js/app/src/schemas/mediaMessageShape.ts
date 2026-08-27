import { z } from "zod";

import {
  contentLayoutPartSchema,
  filePartSchema,
  fileVariablePartSchema,
  imagePartSchema,
  imageVariablePartSchema,
} from "@phoenix/schemas/mediaPartSchemas";

/**
 * The media fields a playground chat message carries, spread into its schema.
 *
 * Four fields and their reasoning is a lot to sit inside upstream's
 * `chatMessageSchema`, so it lives here and is spread in with one line.
 *
 * Media is held separately from `content` rather than as one ordered part list
 * because the editor is a single text field plus an attachment strip. `contentLayout`
 * is how a message that *was* interleaved says so anyway: the editor still lays it out
 * flat, and the run sends it back in its recorded order. A prompt authored through the
 * API that interleaves them differently is normalized to the flat shape when saved from
 * the playground, which is why the layout is carried beside the media rather than
 * replacing it.
 *
 * Images and documents are kept apart from each other because they are presented
 * differently — an image has a thumbnail, a document has a name — and because
 * providers carry documents on their own wire format.
 *
 * The variable forms name media the prompt does not store; their values arrive with
 * the run's inputs, so one prompt can run against many files.
 */
export const mediaMessageShape = {
  images: z.array(imagePartSchema).optional(),
  imageVariables: z.array(imageVariablePartSchema).optional(),
  files: z.array(filePartSchema).optional(),
  fileVariables: z.array(fileVariablePartSchema).optional(),
  contentLayout: z.array(contentLayoutPartSchema).optional(),
};
