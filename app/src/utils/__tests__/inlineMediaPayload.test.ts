import {
  dataUrlMediaType,
  decodePythonBytesRepr,
  inlineMediaDataUrl,
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

describe("inlineMediaDataUrl", () => {
  it("turns a bytes repr into a data URL", () => {
    const url = inlineMediaDataUrl("image/png", PNG_BYTES_REPR);
    expect(url).toBe(
      `data:image/png;base64,${btoa(String.fromCharCode(...PNG_BYTES))}`
    );
  });

  it("keeps a base64 payload, dropping the line breaks SDKs wrap it with", () => {
    expect(inlineMediaDataUrl("image/png", "iVBO\nRw0K")).toBe(
      "data:image/png;base64,iVBORw0K"
    );
  });

  it("returns a data URL unchanged so its own declared type stays authoritative", () => {
    const url = "data:application/pdf;base64,JVBERi0=";
    expect(inlineMediaDataUrl("image/png", url)).toBe(url);
  });

  it("refuses a payload it cannot place", () => {
    expect(inlineMediaDataUrl("", "iVBORw0K")).toBeNull();
    expect(inlineMediaDataUrl("image/png", "not base64!!")).toBeNull();
    expect(inlineMediaDataUrl("image/png", "b''")).toBeNull();
  });
});

describe("dataUrlMediaType", () => {
  it("reads the type a data URL declares", () => {
    expect(dataUrlMediaType("data:image/PNG;base64,AA==")).toBe("image/png");
    expect(dataUrlMediaType("data:application/pdf,AA")).toBe("application/pdf");
  });

  it("returns null for anything else", () => {
    expect(dataUrlMediaType("https://example.com/a.png")).toBeNull();
    expect(dataUrlMediaType("phoenix://media/abc")).toBeNull();
  });
});
