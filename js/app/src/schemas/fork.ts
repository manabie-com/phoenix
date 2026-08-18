/**
 * Everything the fork adds to upstream's `playground/schemas.ts`, behind one name.
 *
 * The fork's rule for an upstream file is one import statement, no matter how many
 * things it needs from fork-owned code. Two imports are two places a sync can
 * conflict, and upstream adds imports to the top of that file often enough that the
 * difference is real. Re-exporting here keeps each fork module cohesive and still
 * costs upstream a single line.
 *
 * Named after `mk/fork.mk`, which does the same job for the Makefile.
 */
export { mediaMessageShape } from "./mediaMessageShape";
export {
  spanMessageContentPartSchema,
  spanMessageContentSchema,
} from "./spanMessageContentSchema";
export type { SpanMessageContentPart } from "./spanMessageContentSchema";
