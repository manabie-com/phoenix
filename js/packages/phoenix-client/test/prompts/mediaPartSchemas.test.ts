import { describe, expect, it } from "vitest";

import {
  asFilePart,
  asImagePart,
  filePartSchema,
  imagePartSchema,
} from "../../src/schemas/llm/phoenixPrompt/mediaPartSchemas";
import { phoenixContentPartSchema } from "../../src/schemas/llm/phoenixPrompt/messagePartSchemas";

/**
 * A new test file on purpose: assertions added to an upstream test file conflict
 * on every upstream edit to it, for no benefit (see
 * .claude/rules/fork-ownership.md).
 *
 * These schemas are what keep `tsc --build` compiling. `phoenixContentPartSchema`
 * is wrapped in `schemaMatches<PromptChatMessagePart>()`, which fails at compile
 * time when the Zod union and the generated OpenAPI type diverge — so a
 * regression here breaks the package build, not just these tests.
 */

const STORED_IMAGE = {
  type: "image" as const,
  image: { url: `phoenix://media/${"a".repeat(64)}`, media_type: "image/png" },
};
const IMAGE_VARIABLE = {
  type: "image" as const,
  image: { variable: "image" },
};
const STORED_FILE = {
  type: "file" as const,
  file: {
    url: `phoenix://media/${"b".repeat(64)}`,
    media_type: "application/pdf",
  },
};
const FILE_VARIABLE = {
  type: "file" as const,
  file: { variable: "contract_pdf" },
};

describe("imagePartSchema", () => {
  it("accepts a stored image reference", () => {
    expect(imagePartSchema.parse(STORED_IMAGE)).toEqual(STORED_IMAGE);
  });

  it("accepts an image variable, which carries no media_type", () => {
    // A variable's type is only known once a value is supplied, so the server
    // validates it at run time rather than on write.
    expect(imagePartSchema.parse(IMAGE_VARIABLE)).toEqual(IMAGE_VARIABLE);
  });

  it("rejects a media source that is neither a url nor a variable", () => {
    expect(
      imagePartSchema.safeParse({ type: "image", image: {} }).success
    ).toBe(false);
  });

  it("rejects a stored reference missing media_type", () => {
    expect(
      imagePartSchema.safeParse({ type: "image", image: { url: "x" } }).success
    ).toBe(false);
  });

  it("rejects a file part", () => {
    expect(imagePartSchema.safeParse(STORED_FILE).success).toBe(false);
  });
});

describe("filePartSchema", () => {
  it("accepts a stored file reference", () => {
    expect(filePartSchema.parse(STORED_FILE)).toEqual(STORED_FILE);
  });

  it("accepts a file variable", () => {
    expect(filePartSchema.parse(FILE_VARIABLE)).toEqual(FILE_VARIABLE);
  });

  it("rejects an image part", () => {
    expect(filePartSchema.safeParse(STORED_IMAGE).success).toBe(false);
  });
});

describe("phoenixContentPartSchema", () => {
  it.each([
    ["stored image", STORED_IMAGE],
    ["image variable", IMAGE_VARIABLE],
    ["stored file", STORED_FILE],
    ["file variable", FILE_VARIABLE],
  ])("discriminates %s by type", (_label, part) => {
    expect(phoenixContentPartSchema.parse(part)).toEqual(part);
  });

  it("still accepts the parts that predate media support", () => {
    const text = { type: "text" as const, text: "hello" };
    expect(phoenixContentPartSchema.parse(text)).toEqual(text);
  });

  it("rejects an unknown part type", () => {
    expect(
      phoenixContentPartSchema.safeParse({ type: "audio", audio: {} }).success
    ).toBe(false);
  });
});

describe("creation helpers", () => {
  it("asImagePart narrows an image and rejects anything else", () => {
    expect(asImagePart(STORED_IMAGE)).toEqual(STORED_IMAGE);
    expect(asImagePart(STORED_FILE)).toBeNull();
    expect(asImagePart({ nonsense: true })).toBeNull();
  });

  it("asFilePart narrows a file and rejects anything else", () => {
    expect(asFilePart(FILE_VARIABLE)).toEqual(FILE_VARIABLE);
    expect(asFilePart(STORED_IMAGE)).toBeNull();
    expect(asFilePart(null)).toBeNull();
  });
});
