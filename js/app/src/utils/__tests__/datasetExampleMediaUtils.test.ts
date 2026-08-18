import { describe, expect, it } from "vitest";

import {
  findExampleMedia,
  findExampleMediaAnywhere,
  isValidExampleMediaKey,
  removeExampleMedia,
  setExampleMedia,
} from "../datasetExampleMediaUtils";

/** Unwraps a helper's `string | null` so a null shows up as a test failure, not a cast. */
function edited(value: string | null): string {
  if (value === null) {
    throw new Error("expected JSON text, but the helper refused the input");
  }
  return value;
}

const DIGEST = "a".repeat(64);
const MEDIA_URL = `phoenix://media/${DIGEST}`;
const OTHER_URL = `phoenix://media/${"b".repeat(64)}`;

describe("findExampleMedia", () => {
  it("finds a hosted reference at the top level", () => {
    const json = JSON.stringify({ answer: "4", question_image: MEDIA_URL });
    expect(findExampleMedia(json)).toEqual([
      { key: "question_image", label: "question_image", url: MEDIA_URL },
    ]);
  });

  it("finds several, in key order", () => {
    const json = JSON.stringify({
      question_image: MEDIA_URL,
      answer: "4",
      answer_image: OTHER_URL,
    });
    expect(findExampleMedia(json).map((entry) => entry.key)).toEqual([
      "question_image",
      "answer_image",
    ]);
  });

  it("ignores ordinary strings", () => {
    const json = JSON.stringify({
      answer: "4",
      note: "https://example.com/cat.png",
    });
    expect(findExampleMedia(json)).toEqual([]);
  });

  it("ignores a reference nested out of reach of a template variable", () => {
    // Only a top-level key can fill a media variable, so a digest buried inside
    // some other structure is not an attachment and must not be offered as one.
    const json = JSON.stringify({ meta: { question_image: MEDIA_URL } });
    expect(findExampleMedia(json)).toEqual([]);
  });

  it("returns nothing for malformed JSON rather than throwing", () => {
    // The editor's text is malformed while the user is mid-keystroke.
    expect(findExampleMedia("{ not json")).toEqual([]);
  });

  it("returns nothing for JSON that is not an object", () => {
    expect(findExampleMedia("[1, 2]")).toEqual([]);
    expect(findExampleMedia('"a string"')).toEqual([]);
  });
});

describe("setExampleMedia", () => {
  it("adds the reference under the given variable name", () => {
    const json = JSON.stringify({ answer: "4" }, null, 2);
    const updated = setExampleMedia(json, "question_image", MEDIA_URL);
    expect(updated).not.toBeNull();
    expect(JSON.parse(edited(updated))).toEqual({
      answer: "4",
      question_image: MEDIA_URL,
    });
  });

  it("keeps the other keys untouched", () => {
    const json = JSON.stringify({ answer: "4", rubric: { strict: true } });
    const updated = setExampleMedia(json, "question_image", MEDIA_URL);
    expect(JSON.parse(edited(updated)).rubric).toEqual({ strict: true });
  });

  it("replaces an existing attachment under the same name", () => {
    const json = JSON.stringify({ question_image: MEDIA_URL });
    const updated = setExampleMedia(json, "question_image", OTHER_URL);
    expect(findExampleMedia(edited(updated))).toEqual([
      { key: "question_image", label: "question_image", url: OTHER_URL },
    ]);
  });

  it("refuses to touch text that is not a JSON object", () => {
    // Returning null is what lets the caller say so, rather than overwriting
    // whatever the user was in the middle of typing.
    expect(
      setExampleMedia("{ not json", "question_image", MEDIA_URL)
    ).toBeNull();
    expect(setExampleMedia("[1, 2]", "question_image", MEDIA_URL)).toBeNull();
  });
});

describe("removeExampleMedia", () => {
  it("removes the key", () => {
    const json = JSON.stringify({ answer: "4", question_image: MEDIA_URL });
    const updated = removeExampleMedia(json, "question_image");
    expect(JSON.parse(edited(updated))).toEqual({ answer: "4" });
  });

  it("leaves the example alone when the key is not there", () => {
    const json = JSON.stringify({ answer: "4" });
    const updated = removeExampleMedia(json, "question_image");
    expect(JSON.parse(edited(updated))).toEqual({ answer: "4" });
  });

  it("refuses to touch text that is not a JSON object", () => {
    expect(removeExampleMedia("{ not json", "question_image")).toBeNull();
  });
});

describe("isValidExampleMediaKey", () => {
  it("accepts a name", () => {
    expect(isValidExampleMediaKey("question_image")).toBe(true);
  });

  it("rejects blank, which would attach the file to no variable at all", () => {
    expect(isValidExampleMediaKey("")).toBe(false);
    expect(isValidExampleMediaKey("   ")).toBe(false);
  });
});

describe("a text-only row and an attachment row in one dataset", () => {
  it("round-trips attaching and detaching", () => {
    const textOnly = JSON.stringify({ answer: "no attachment" }, null, 2);
    expect(findExampleMedia(textOnly)).toEqual([]);

    const withMedia = edited(
      setExampleMedia(textOnly, "question_image", MEDIA_URL)
    );
    expect(findExampleMedia(withMedia)).toHaveLength(1);

    const detached = edited(removeExampleMedia(withMedia, "question_image"));
    expect(findExampleMedia(detached)).toEqual([]);
    expect(JSON.parse(detached)).toEqual({ answer: "no attachment" });
  });
});

describe("findExampleMediaAnywhere", () => {
  it("finds a top-level reference, same as the shallow scan", () => {
    const text = JSON.stringify({ answer: "4", question_image: MEDIA_URL });
    expect(findExampleMediaAnywhere(text)).toEqual([
      { key: "question_image", label: "question_image", url: MEDIA_URL },
    ]);
  });

  it("finds media nested in a conversation, which is what a span produces", () => {
    // The exact shape span-to-dataset writes. A top-level scan sees nothing here,
    // and that is most of the multimodal rows anyone will have.
    const example = {
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "Grade this:" },
            { type: "image_url", image_url: { url: MEDIA_URL } },
          ],
        },
      ],
    };
    expect(findExampleMediaAnywhere(example)).toEqual([
      {
        key: "messages[0].content[1].image_url.url",
        // The accessor tail is dropped: what a reader is locating is the content
        // part, not the three keys it takes to reach the string.
        label: "messages[0].content[1]",
        url: MEDIA_URL,
      },
    ]);
  });

  it("finds several, in document order", () => {
    const example = {
      question_image: MEDIA_URL,
      nested: { deeper: [{ answer_image: OTHER_URL }] },
    };
    expect(findExampleMediaAnywhere(example).map((e) => e.url)).toEqual([
      MEDIA_URL,
      OTHER_URL,
    ]);
  });

  it("reports the same image under two keys twice", () => {
    // Which slot an image fills is the thing being checked, so a repeat is not
    // a duplicate.
    const example = { question_image: MEDIA_URL, answer_image: MEDIA_URL };
    expect(findExampleMediaAnywhere(example)).toHaveLength(2);
  });

  it("ignores ordinary strings and malformed input", () => {
    expect(
      findExampleMediaAnywhere({ note: "https://example.com/cat.png" })
    ).toEqual([]);
    expect(findExampleMediaAnywhere("{ not json")).toEqual([]);
    expect(findExampleMediaAnywhere(null)).toEqual([]);
  });

  it("survives a deeply nested structure without running away", () => {
    let deep: unknown = MEDIA_URL;
    for (let i = 0; i < 50; i++) {
      deep = { next: deep };
    }
    expect(() => findExampleMediaAnywhere({ root: deep })).not.toThrow();
  });
});

describe("inline media", () => {
  // A span from an instrumented app records whatever that app passed its
  // provider, which is very often base64 inline rather than a stored reference.
  const INLINE_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg";
  const INLINE_PDF = "data:application/pdf;base64,JVBERi0xLjQK";

  it("is found at the top level", () => {
    expect(findExampleMedia({ question_image: INLINE_PNG })).toEqual([
      { key: "question_image", label: "question_image", url: INLINE_PNG },
    ]);
  });

  it("is found nested, the way a span-saved row carries it", () => {
    const example = {
      messages: [
        {
          role: "user",
          content: [{ type: "image_url", image_url: { url: INLINE_PNG } }],
        },
      ],
    };
    expect(findExampleMediaAnywhere(example).map((e) => e.url)).toEqual([
      INLINE_PNG,
    ]);
  });

  it("recognises an inline document too", () => {
    expect(findExampleMediaAnywhere({ report: INLINE_PDF })).toHaveLength(1);
  });

  it("mixes with a stored reference in one example", () => {
    const found = findExampleMediaAnywhere({
      question_image: INLINE_PNG,
      answer_image: MEDIA_URL,
    });
    expect(found.map((e) => e.key)).toEqual(["question_image", "answer_image"]);
  });

  it("does not treat an unrelated data URI as a picture", () => {
    expect(
      findExampleMediaAnywhere({ blob: "data:text/csv;base64,YSxi" })
    ).toEqual([]);
    expect(
      findExampleMediaAnywhere({ blob: "data:application/zip;base64,UEsD" })
    ).toEqual([]);
  });
});

describe("labels for server-externalized inline media", () => {
  it("trims the wrapper the server leaves behind", () => {
    // What `externalize_inline_media` writes when it lifts a Gemini payload's
    // inline bytes into the media store. Untrimmed this reads
    // `contents[0].parts[1].inline_data.phoenix_media_url`, which is three lines
    // of plumbing to name one picture.
    const example = {
      contents: [
        {
          role: "user",
          parts: [
            { text: "Classify this" },
            {
              inline_data: {
                mime_type: "image/png",
                phoenix_media_url: MEDIA_URL,
              },
            },
          ],
        },
      ],
    };
    expect(findExampleMediaAnywhere(example)).toEqual([
      {
        key: "contents[0].parts[1].inline_data.phoenix_media_url",
        label: "contents[0].parts[1]",
        url: MEDIA_URL,
      },
    ]);
  });
});
