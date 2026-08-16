import {
  canonicalDataUrl,
  decodePythonBytesRepr,
  inlineMedia,
} from "../inlineMediaPayload";

/** The first bytes of a real PNG, as the Google GenAI SDK's `bytes` would be dumped. */
const PNG_BYTES_REPR = String.raw`b'\x89PNG\r\n\x1a\n\x00'`;
const PNG_BYTES = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00];

describe("decodePythonBytesRepr", () => {
  it("decodes hex escapes, named escapes and literal characters", () => {
    expect(Array.from(decodePythonBytesRepr(PNG_BYTES_REPR)!)).toEqual(
      PNG_BYTES
    );
  });

  it("accepts a double-quoted repr", () => {
    expect(Array.from(decodePythonBytesRepr(String.raw`b"ab\x01"`)!)).toEqual([
      0x61, 0x62, 0x01,
    ]);
  });

  it("decodes the escapes JSON does not have", () => {
    expect(Array.from(decodePythonBytesRepr(String.raw`b'\v\a\0'`)!)).toEqual([
      0x0b, 0x07, 0x00,
    ]);
  });

  it("rejects anything that is not a bytes repr", () => {
    expect(decodePythonBytesRepr("iVBORw0KGgo=")).toBeNull();
    expect(decodePythonBytesRepr("b'unterminated")).toBeNull();
    expect(decodePythonBytesRepr(String.raw`b'\q'`)).toBeNull();
    expect(decodePythonBytesRepr(String.raw`b'\xZZ'`)).toBeNull();
  });

  it("rejects a repr whose bytes were decoded away", () => {
    expect(decodePythonBytesRepr("b'é中'")).toBeNull();
  });
});

describe("inlineMedia", () => {
  it("turns a bytes repr into a canonical pair", () => {
    expect(inlineMedia("image/png", PNG_BYTES_REPR)).toEqual({
      url: `data:image/png;base64,${btoa(String.fromCharCode(...PNG_BYTES))}`,
      mediaType: "image/png",
    });
  });

  it("keeps a base64 payload, dropping the line breaks SDKs wrap it with", () => {
    expect(inlineMedia("image/png", "iVBO\nRw0K")).toEqual({
      url: "data:image/png;base64,iVBORw0K",
      mediaType: "image/png",
    });
  });

  it("normalizes an aliased declared type into both halves", () => {
    // The URL header and the media type must agree exactly or `MediaContent` rejects
    // the pair, which aborts the whole run rather than the one attachment.
    expect(inlineMedia("image/jpg", "iVBORw0K")).toEqual({
      url: "data:image/jpeg;base64,iVBORw0K",
      mediaType: "image/jpeg",
    });
  });

  it("refuses a payload it cannot place", () => {
    expect(inlineMedia("", "iVBORw0K")).toBeNull();
    expect(inlineMedia("image/png", "not base64!!")).toBeNull();
    expect(inlineMedia("image/png", "b''")).toBeNull();
    // Length that cannot decode.
    expect(inlineMedia("image/png", "QQQ")).toBeNull();
  });
});

describe("canonicalDataUrl", () => {
  it("rewrites the header so it states the normalized type", () => {
    expect(canonicalDataUrl("data:image/JPG;base64,QQ==")).toEqual({
      url: "data:image/jpeg;base64,QQ==",
      mediaType: "image/jpeg",
    });
  });

  it("passes a already-canonical URL through unchanged", () => {
    expect(canonicalDataUrl("data:application/pdf;base64,JVBERi0=")).toEqual({
      url: "data:application/pdf;base64,JVBERi0=",
      mediaType: "application/pdf",
    });
  });

  it("strips whitespace out of the payload", () => {
    expect(canonicalDataUrl("data:image/png;base64,iVBO\nRw0K")?.url).toBe(
      "data:image/png;base64,iVBORw0K"
    );
  });

  it("refuses a data URL the server would refuse", () => {
    // parse_media_url requires the base64 parameter.
    expect(canonicalDataUrl("data:image/png,AA")).toBeNull();
    expect(canonicalDataUrl("data:image/png;charset=utf-8,AA")).toBeNull();
    // InlineMedia.decode uses strict base64.
    expect(canonicalDataUrl("data:image/png;base64,not!!")).toBeNull();
    expect(canonicalDataUrl("data:image/png;base64,QQQ")).toBeNull();
    expect(canonicalDataUrl("data:image/png;base64,")).toBeNull();
  });

  it("returns null for anything that is not a data URL", () => {
    expect(canonicalDataUrl("https://example.com/a.png")).toBeNull();
    expect(canonicalDataUrl("phoenix://media/abc")).toBeNull();
  });
});
