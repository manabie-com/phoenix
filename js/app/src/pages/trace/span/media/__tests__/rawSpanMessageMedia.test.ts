/**
 * A new test file on purpose (see .claude/rules/fork-ownership.md).
 */
import type { AttributeMessage } from "@phoenix/openInference/tracing/types";
import type { LLMSpanAttributes } from "@phoenix/pages/trace/span/utils";

import { withRawSpanMessageMedia } from "../rawSpanMessageMedia";

const PDF_B64 = "JVBERi0xLjQK";
const PNG_B64 = "iVBORw0K";

const SYSTEM_PROMPT = "You are a teacher.";
const QUESTION = "# Question";
const ANSWER = "Provided in the attached file(s).";

/** The attribute bundle upstream reads, with only the fields under test filled in. */
const llmAttributes = (
  inputMessages: AttributeMessage[]
): LLMSpanAttributes => ({
  modelName: "gemini-2.5-flash",
  provider: "google",
  inputMessages,
  outputMessages: [],
  tools: [],
  prompts: [],
  promptTemplate: null,
  invocationParameters: "{}",
});

/** Text content, the only shape `google-adk` records for a message with a file. */
const textContent = (text: string) => ({
  message_content: { type: "text", text },
});

const imageContent = (url: string) => ({
  message_content: { type: "image", image: { image: { url } } },
});

/** What `google-adk` records: system prompt and user text, and no media at all. */
const RECORDED_MESSAGES: AttributeMessage[] = [
  { role: "system", content: SYSTEM_PROMPT },
  { role: "user", contents: [textContent(QUESTION), textContent(ANSWER)] },
];

/**
 * The request the app actually sent, as `input.value` holds it.
 *
 * The system prompt sits in `config.system_instruction`, outside `contents` — the
 * shape that makes the payload one message shorter than the recording until the
 * reader accounts for it.
 */
const rawAttributes = (parts: unknown[]) => ({
  input: {
    value: JSON.stringify({
      model: "gemini-2.5-flash",
      contents: [{ role: "user", parts }],
      config: { system_instruction: SYSTEM_PROMPT },
    }),
  },
});

const GEMINI_PARTS = [
  { text: QUESTION },
  { text: ANSWER },
  { inline_data: { mime_type: "application/pdf", data: PDF_B64 } },
];

/** The contents of the message at `index`, as URLs where they are media. */
const mediaUrls = (attributes: LLMSpanAttributes, index: number) =>
  (attributes.inputMessages[index]?.contents ?? [])
    .map((content) => content.message_content?.image?.image?.url)
    .filter(Boolean);

describe("withRawSpanMessageMedia", () => {
  it("shows a document the recording left in the raw request", () => {
    const result = withRawSpanMessageMedia(
      llmAttributes(RECORDED_MESSAGES),
      rawAttributes(GEMINI_PARTS)
    );
    expect(mediaUrls(result, 1)).toEqual([
      `data:application/pdf;base64,${PDF_B64}`,
    ]);
  });

  it("keeps the text the message already showed", () => {
    const result = withRawSpanMessageMedia(
      llmAttributes(RECORDED_MESSAGES),
      rawAttributes(GEMINI_PARTS)
    );
    const contents = result.inputMessages[1].contents ?? [];
    expect(contents.slice(0, 2)).toEqual([
      textContent(QUESTION),
      textContent(ANSWER),
    ]);
    expect(contents).toHaveLength(3);
  });

  it("attaches nothing to the message that had none", () => {
    const result = withRawSpanMessageMedia(
      llmAttributes(RECORDED_MESSAGES),
      rawAttributes(GEMINI_PARTS)
    );
    expect(result.inputMessages[0]).toEqual(RECORDED_MESSAGES[0]);
  });

  it("recovers an image the same way", () => {
    const result = withRawSpanMessageMedia(
      llmAttributes(RECORDED_MESSAGES),
      rawAttributes([
        { text: QUESTION },
        { text: ANSWER },
        { inline_data: { mime_type: "image/png", data: PNG_B64 } },
      ])
    );
    expect(mediaUrls(result, 1)).toEqual([`data:image/png;base64,${PNG_B64}`]);
  });

  it("reads the URL-safe alphabet the Google SDK writes", () => {
    const result = withRawSpanMessageMedia(
      llmAttributes(RECORDED_MESSAGES),
      rawAttributes([
        { text: QUESTION },
        { text: ANSWER },
        { inline_data: { mime_type: "application/pdf", data: "-_-_" } },
      ])
    );
    expect(mediaUrls(result, 1)).toEqual(["data:application/pdf;base64,+/+/"]);
  });

  it("leaves images alone when the recording carries its own", () => {
    // Instrumentation that records images properly should not be second-guessed,
    // and a second copy of one image is worse than none.
    const recorded: AttributeMessage[] = [
      { role: "system", content: SYSTEM_PROMPT },
      {
        role: "user",
        contents: [textContent(QUESTION), imageContent("https://host/a.png")],
      },
    ];
    const attributes = llmAttributes(recorded);
    const result = withRawSpanMessageMedia(
      attributes,
      rawAttributes([
        { text: QUESTION },
        { inline_data: { mime_type: "image/png", data: PNG_B64 } },
      ])
    );
    expect(result).toBe(attributes);
    expect(mediaUrls(result, 1)).toEqual(["https://host/a.png"]);
  });

  it("still recovers a document when the recording carries an image", () => {
    // OpenInference has no document content type, so a recorded image is no
    // evidence at all that the documents were handled too.
    const recorded: AttributeMessage[] = [
      { role: "system", content: SYSTEM_PROMPT },
      {
        role: "user",
        contents: [textContent(QUESTION), imageContent("https://host/a.png")],
      },
    ];
    const result = withRawSpanMessageMedia(
      llmAttributes(recorded),
      rawAttributes([
        { text: QUESTION },
        { inline_data: { mime_type: "image/png", data: PNG_B64 } },
        { inline_data: { mime_type: "application/pdf", data: PDF_B64 } },
      ])
    );
    expect(mediaUrls(result, 1)).toEqual([
      "https://host/a.png",
      `data:application/pdf;base64,${PDF_B64}`,
    ]);
  });

  it("leaves a payload describing a different conversation alone", () => {
    // Matching by position is only sound when the two are two recordings of one
    // request. A missing attachment beats one shown against the wrong turn.
    const attributes = llmAttributes(RECORDED_MESSAGES);
    const misaligned = {
      input: {
        value: JSON.stringify({
          contents: [
            { role: "user", parts: [{ text: QUESTION }] },
            { role: "user", parts: GEMINI_PARTS },
          ],
        }),
      },
    };
    expect(withRawSpanMessageMedia(attributes, misaligned)).toBe(attributes);
  });

  it("leaves a span with no raw request alone", () => {
    const attributes = llmAttributes(RECORDED_MESSAGES);
    expect(withRawSpanMessageMedia(attributes, {})).toBe(attributes);
  });

  it("leaves a span with no recorded messages alone", () => {
    const attributes = llmAttributes([]);
    expect(
      withRawSpanMessageMedia(attributes, rawAttributes(GEMINI_PARTS))
    ).toBe(attributes);
  });

  it("leaves a raw request carrying no media alone", () => {
    const attributes = llmAttributes(RECORDED_MESSAGES);
    expect(
      withRawSpanMessageMedia(
        attributes,
        rawAttributes([{ text: QUESTION }, { text: ANSWER }])
      )
    ).toBe(attributes);
  });

  describe("when OpenInference redacted the image", () => {
    // `OPENINFERENCE_BASE64_IMAGE_MAX_LENGTH` defaults to 32,000 characters, so a
    // photograph is recorded as a content part holding `__REDACTED__` and drawn as
    // the grey redacted tile. The bytes are still in the raw request.
    const REDACTED = "__REDACTED__";
    const PNG2_B64 = "iVBORw0A";
    const LABEL_1 = "Attachment {sample_answer_1.jpg}:";
    const LABEL_2 = "Attachment {sample_answer_2.jpg}:";

    it("fills the placeholder with the image the raw request carried", () => {
      const recorded: AttributeMessage[] = [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          contents: [textContent(LABEL_1), imageContent(REDACTED)],
        },
      ];
      const result = withRawSpanMessageMedia(
        llmAttributes(recorded),
        rawAttributes([
          { text: LABEL_1 },
          { inline_data: { mime_type: "image/png", data: PNG_B64 } },
        ])
      );
      expect(mediaUrls(result, 1)).toEqual([
        `data:image/png;base64,${PNG_B64}`,
      ]);
    });

    it("fills each placeholder where it stood, under its own label", () => {
      // The label names the tile that follows it, so appending the recovered images
      // after the last line of text would caption every one of them wrongly — and
      // leave the grey placeholders sitting above them.
      const recorded: AttributeMessage[] = [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          contents: [
            textContent(LABEL_1),
            imageContent(REDACTED),
            textContent(LABEL_2),
            imageContent(REDACTED),
          ],
        },
      ];
      const result = withRawSpanMessageMedia(
        llmAttributes(recorded),
        rawAttributes([
          { text: LABEL_1 },
          { inline_data: { mime_type: "image/png", data: PNG_B64 } },
          { text: LABEL_2 },
          { inline_data: { mime_type: "image/png", data: PNG2_B64 } },
        ])
      );
      expect(result.inputMessages[1].contents).toEqual([
        textContent(LABEL_1),
        imageContent(`data:image/png;base64,${PNG_B64}`),
        textContent(LABEL_2),
        imageContent(`data:image/png;base64,${PNG2_B64}`),
      ]);
    });

    it("fills only the placeholder when one image survived the cap", () => {
      // A small picture is recorded and a large one is not, so the placeholder has to
      // be matched to the payload image that stood in the same position.
      const recorded: AttributeMessage[] = [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          contents: [
            imageContent("https://host/a.png"),
            imageContent(REDACTED),
          ],
        },
      ];
      const result = withRawSpanMessageMedia(
        llmAttributes(recorded),
        rawAttributes([
          { inline_data: { mime_type: "image/png", data: PNG_B64 } },
          { inline_data: { mime_type: "image/png", data: PNG2_B64 } },
        ])
      );
      expect(mediaUrls(result, 1)).toEqual([
        "https://host/a.png",
        `data:image/png;base64,${PNG2_B64}`,
      ]);
    });

    it("leaves the placeholder alone when the raw request has no image for it", () => {
      const attributes = llmAttributes([
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          contents: [textContent(LABEL_1), imageContent(REDACTED)],
        },
      ]);
      expect(
        withRawSpanMessageMedia(attributes, rawAttributes([{ text: LABEL_1 }]))
      ).toBe(attributes);
    });
  });
});
