import {
  MODEL_CONFIG_PARSING_ERROR,
  OUTPUT_MESSAGES_PARSING_ERROR,
  OUTPUT_VALUE_PARSING_ERROR,
} from "../constants";
import { withoutOutputMessagesError } from "../spanReplayErrors";

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
