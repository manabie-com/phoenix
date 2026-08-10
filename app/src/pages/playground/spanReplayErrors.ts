/**
 * Which span-parsing errors are worth putting in front of someone replaying a span.
 *
 * `getOutputFromAttributes` records that it could not read `llm.output_messages`
 * *before* it tries `output.value`, and keeps that error even when the second read
 * succeeds. So a span whose output panel is correctly populated still opens under a
 * warning saying the output could not be parsed. Reporting a failure for a path that
 * recovered is worse than saying nothing: it teaches the reader to dismiss the
 * banner, including the times it is real.
 *
 * A span that records `output.value` and no structured messages is the normal shape
 * for anything not instrumented by OpenInference, so this is the common case rather
 * than an edge one.
 *
 * Applied where the banner's list is assembled rather than inside
 * `getOutputFromAttributes`, deliberately. That function's own contract — it reports
 * what it could not read, and a test asserts exactly that — is left alone; what
 * changes is only which of those reports is worth showing. Written as a filter over
 * the list so upstream's lines stay where they are and any error it adds later passes
 * through untouched.
 */
import {
  INPUT_MESSAGES_PARSING_ERROR,
  OUTPUT_MESSAGES_PARSING_ERROR,
  OUTPUT_VALUE_PARSING_ERROR,
} from "./constants";

/**
 * The input errors worth reporting, given what the template ended up holding.
 *
 * `getTemplateMessagesFromAttributes` reports that it could not read
 * `llm.input_messages`, which is true and is not the whole story: the fork then reads
 * the messages out of the raw request the span recorded, and when that works the
 * template is complete and the error describes a detour rather than a failure.
 *
 * Filtering here rather than returning a different error from that function keeps the
 * recovery out of upstream's parser entirely — it goes on reporting what it could not
 * read, and this decides whether the reader needs to hear it.
 *
 * @param errors The errors gathered while reading the input messages.
 * @param messages The messages the template ended up with, if any.
 */
export const withoutInputMessagesError = (
  errors: string[],
  messages: readonly unknown[] | null | undefined
): string[] =>
  messages?.length
    ? errors.filter((error) => error !== INPUT_MESSAGES_PARSING_ERROR)
    : errors;

/**
 * The output errors worth reporting, given whether the raw output stood in.
 *
 * The missing-messages error is dropped only when `output.value` supplied the output
 * in its place. When that failed too the list is returned whole, because then nothing
 * produced an output and both reasons are worth reading.
 *
 * @param errors The errors gathered while reading the span's output.
 */
export const withoutOutputMessagesError = (errors: string[]): string[] =>
  errors.includes(OUTPUT_VALUE_PARSING_ERROR)
    ? errors
    : errors.filter((error) => error !== OUTPUT_MESSAGES_PARSING_ERROR);
