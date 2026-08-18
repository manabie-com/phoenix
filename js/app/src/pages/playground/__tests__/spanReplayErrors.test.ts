import {
  INPUT_MESSAGES_PARSING_ERROR,
  MODEL_CONFIG_PARSING_ERROR,
  OUTPUT_MESSAGES_PARSING_ERROR,
  OUTPUT_VALUE_PARSING_ERROR,
} from "../constants";
import {
  SPAN_INPUT_RECOVERED_FROM_RAW_REQUEST,
  spanInputParsingErrors,
  withoutOutputMessagesError,
} from "../spanReplayErrors";

describe("spanInputParsingErrors", () => {
  const recovered = [{ id: 1, role: "user" as const, content: "hi" }];

  it("downgrades the failure to a caveat once the raw request supplied messages", () => {
    expect(
      spanInputParsingErrors(
        [INPUT_MESSAGES_PARSING_ERROR, MODEL_CONFIG_PARSING_ERROR],
        recovered
      )
    ).toEqual([
      SPAN_INPUT_RECOVERED_FROM_RAW_REQUEST,
      MODEL_CONFIG_PARSING_ERROR,
    ]);
  });

  it("says what is missing rather than going quiet", () => {
    // The raw reader drops tool calls and tool results; a clean banner would claim a
    // faithful replay.
    expect(SPAN_INPUT_RECOVERED_FROM_RAW_REQUEST).toMatch(/tool calls/i);
    expect(SPAN_INPUT_RECOVERED_FROM_RAW_REQUEST).not.toBe(
      INPUT_MESSAGES_PARSING_ERROR
    );
  });

  it("keeps the original failure when nothing was recovered", () => {
    const errors = [INPUT_MESSAGES_PARSING_ERROR];
    expect(spanInputParsingErrors(errors, undefined)).toEqual(errors);
    expect(spanInputParsingErrors(errors, null)).toEqual(errors);
    expect(spanInputParsingErrors(errors, [])).toEqual(errors);
  });

  it("leaves a clean list alone", () => {
    expect(spanInputParsingErrors([], recovered)).toEqual([]);
    expect(
      spanInputParsingErrors([MODEL_CONFIG_PARSING_ERROR], recovered)
    ).toEqual([MODEL_CONFIG_PARSING_ERROR]);
  });
});

describe("withoutOutputMessagesError", () => {
  it("drops the missing-messages error when the raw output stood in for them", () => {
    expect(
      withoutOutputMessagesError([
        OUTPUT_MESSAGES_PARSING_ERROR,
        MODEL_CONFIG_PARSING_ERROR,
      ])
    ).toEqual([MODEL_CONFIG_PARSING_ERROR]);
  });

  it("keeps both when there was no raw output either", () => {
    const errors = [OUTPUT_MESSAGES_PARSING_ERROR, OUTPUT_VALUE_PARSING_ERROR];
    expect(withoutOutputMessagesError(errors)).toEqual(errors);
  });

  it("passes through a list that never mentioned the output", () => {
    expect(withoutOutputMessagesError([MODEL_CONFIG_PARSING_ERROR])).toEqual([
      MODEL_CONFIG_PARSING_ERROR,
    ]);
    expect(withoutOutputMessagesError([])).toEqual([]);
  });
});
