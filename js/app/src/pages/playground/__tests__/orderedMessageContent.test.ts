/**
 * A new test file on purpose (see .claude/rules/fork-ownership.md).
 *
 * What is under test is the *order a message is sent in*, which is why almost every
 * assertion is on a whole array rather than on membership: the parts have always all
 * been there, and only their arrangement was wrong.
 */
import type { ChatMessage } from "@phoenix/store/playground";

import { orderedMessageContent } from "../playgroundMedia";
import { withRawSpanInputMedia } from "../spanRawInputMessages";

const PNG_A = "iVBORw0K";
const PNG_B = "iVBORw0A";
const PDF = "JVBERi0xLjQK";
const URL_A = `data:image/png;base64,${PNG_A}`;
const URL_B = `data:image/png;base64,${PNG_B}`;
const PDF_URL = `data:application/pdf;base64,${PDF}`;

const image = (url: string) => ({ image: { url, mediaType: "image/png" } });
const file = (url: string) => ({
  file: { url, mediaType: "application/pdf" },
});

/** The text parts upstream has already pushed by the time the fork is called. */
const textContent = (message: { content?: string }) =>
  message.content ? [{ text: { text: message.content } }] : [];

/** What upstream's caller ends up sending for a message. */
const sent = (message: Parameters<typeof orderedMessageContent>[1]) =>
  orderedMessageContent(textContent(message), message);

const INTRO = "Grade these answers.";
const LABEL_1 = "Attachment {sample_answer_1.jpg}:";
const LABEL_2 = "Attachment {sample_answer_2.jpg}:";
const TAIL = "Return your marking as JSON.";
const JOINED = [INTRO, LABEL_1, LABEL_2, TAIL].join("\n\n");

describe("orderedMessageContent", () => {
  it("sends a recorded message in the order it was recorded", () => {
    expect(
      sent({
        content: `${INTRO}\n\n${LABEL_1}`,
        images: [image(URL_A)],
        contentLayout: [{ text: INTRO }, { text: LABEL_1 }, { image: 0 }],
      })
    ).toEqual([
      { text: { text: INTRO } },
      { text: { text: LABEL_1 } },
      { image: { url: URL_A, mediaType: "image/png" } },
    ]);
  });

  it("puts each attachment under the line that names it", () => {
    // The whole point: two labels and two pictures, paired by position rather than
    // left to the model to re-pair from a block of four.
    expect(
      sent({
        content: JOINED,
        images: [image(URL_A), image(URL_B)],
        contentLayout: [
          { text: INTRO },
          { text: LABEL_1 },
          { image: 0 },
          { text: LABEL_2 },
          { image: 1 },
          { text: TAIL },
        ],
      })
    ).toEqual([
      { text: { text: INTRO } },
      { text: { text: LABEL_1 } },
      { image: { url: URL_A, mediaType: "image/png" } },
      { text: { text: LABEL_2 } },
      { image: { url: URL_B, mediaType: "image/png" } },
      { text: { text: TAIL } },
    ]);
  });

  it("places a document the same way", () => {
    expect(
      sent({
        content: INTRO,
        files: [file(PDF_URL)],
        contentLayout: [{ file: 0 }, { text: INTRO }],
      })
    ).toEqual([
      { file: { url: PDF_URL, mediaType: "application/pdf" } },
      { text: { text: INTRO } },
    ]);
  });

  it("sends text then media when nothing recorded an order", () => {
    // A message authored in the playground, or loaded from a prompt. This is the
    // shape the editor shows, and it stays the shape that is sent.
    expect(sent({ content: INTRO, images: [image(URL_A)] })).toEqual([
      { text: { text: INTRO } },
      { image: { url: URL_A, mediaType: "image/png" } },
    ]);
  });

  it("falls back once the message has been edited", () => {
    // The editor shows one text field, so an edit invalidates positions recorded
    // against what the message used to say. Sending the edited text with the
    // attachments after it is what the editor is showing.
    expect(
      sent({
        content: `${INTRO} Be strict.`,
        images: [image(URL_A)],
        contentLayout: [{ text: INTRO }, { image: 0 }],
      })
    ).toEqual([
      { text: { text: `${INTRO} Be strict.` } },
      { image: { url: URL_A, mediaType: "image/png" } },
    ]);
  });

  it("falls back when the layout does not account for every attachment", () => {
    expect(
      sent({
        content: INTRO,
        images: [image(URL_A), image(URL_B)],
        contentLayout: [{ text: INTRO }, { image: 0 }],
      })
    ).toEqual([
      { text: { text: INTRO } },
      { image: { url: URL_A, mediaType: "image/png" } },
      { image: { url: URL_B, mediaType: "image/png" } },
    ]);
  });

  it("falls back when the layout points past the attachments it has", () => {
    expect(
      sent({
        content: INTRO,
        images: [image(URL_A)],
        contentLayout: [{ text: INTRO }, { image: 1 }],
      })
    ).toEqual([
      { text: { text: INTRO } },
      { image: { url: URL_A, mediaType: "image/png" } },
    ]);
  });

  it("falls back when the message names media it does not hold", () => {
    // A media variable's value arrives with the run, so a recorded index has nothing
    // to point at.
    expect(
      sent({
        content: INTRO,
        images: [image(URL_A)],
        imageVariables: [{ image: { variable: "answer" } }],
        contentLayout: [{ text: INTRO }, { image: 0 }],
      })
    ).toEqual([
      { text: { text: INTRO } },
      { image: { url: URL_A, mediaType: "image/png" } },
      { imageVariable: { variable: "answer" } },
    ]);
  });

  it("sends a message with no media exactly as upstream built it", () => {
    const built = textContent({ content: INTRO });
    expect(sent({ content: INTRO })).toEqual(built);
  });
});

describe("orderedMessageContent, on media recovered from a raw request", () => {
  // End to end for the case that prompted this: OpenInference redacted the images, so
  // the recorded message is text only, and the bytes come back from `input.value`.
  const SYSTEM = "You are a marking assistant.";
  const recorded: ChatMessage[] = [
    { id: 1, role: "system", content: SYSTEM },
    { id: 2, role: "user", content: JOINED },
  ];
  const parsedAttributes = {
    input: {
      value: JSON.stringify({
        contents: [
          {
            role: "user",
            parts: [
              { text: INTRO },
              { text: LABEL_1 },
              { inline_data: { mime_type: "image/png", data: PNG_A } },
              { text: LABEL_2 },
              { inline_data: { mime_type: "image/png", data: PNG_B } },
              { text: TAIL },
            ],
          },
        ],
        config: { system_instruction: SYSTEM },
      }),
    },
  };

  it("replays the request in the order the provider was sent it", () => {
    const messages = withRawSpanInputMedia(recorded, parsedAttributes);
    expect(sent(messages![1])).toEqual([
      { text: { text: INTRO } },
      { text: { text: LABEL_1 } },
      { image: { url: URL_A, mediaType: "image/png" } },
      { text: { text: LABEL_2 } },
      { image: { url: URL_B, mediaType: "image/png" } },
      { text: { text: TAIL } },
    ]);
  });

  it("leaves the turn that carried nothing alone", () => {
    const messages = withRawSpanInputMedia(recorded, parsedAttributes);
    expect(sent(messages![0])).toEqual([{ text: { text: SYSTEM } }]);
  });
});
