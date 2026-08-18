import {
  isSupportedImageMediaType,
  mediaKindForType,
  normalizeMediaType,
} from "../supportedMediaTypes";

describe("normalizeMediaType", () => {
  it("lower-cases and drops parameters", () => {
    expect(normalizeMediaType("IMAGE/PNG")).toBe("image/png");
    expect(normalizeMediaType("image/png; charset=binary")).toBe("image/png");
  });

  it("resolves the spellings that mean a supported type", () => {
    expect(normalizeMediaType("image/jpg")).toBe("image/jpeg");
    expect(normalizeMediaType("IMAGE/JPG")).toBe("image/jpeg");
    expect(normalizeMediaType("application/x-pdf")).toBe("application/pdf");
  });

  it("leaves an unknown type alone rather than guessing", () => {
    expect(normalizeMediaType("image/bmp")).toBe("image/bmp");
  });
});

describe("mediaKindForType", () => {
  it("classifies every type the server accepts", () => {
    for (const type of [
      "image/png",
      "image/jpeg",
      "image/gif",
      "image/webp",
      "image/heic",
      "image/heif",
    ]) {
      expect(mediaKindForType(type)).toBe("image");
    }
    expect(mediaKindForType("application/pdf")).toBe("file");
  });

  it("accepts image/jpg by way of its alias", () => {
    expect(mediaKindForType("image/jpg")).toBe("image");
  });

  it("refuses what the server refuses", () => {
    // Each of these reaches ImageContentPart/FileContentPart and raises, which aborts
    // the whole template conversion — so it must never leave the client as a part.
    expect(mediaKindForType("image/bmp")).toBeNull();
    // Excluded on purpose: SVG can carry script and Phoenix serves media from its
    // own origin.
    expect(mediaKindForType("image/svg+xml")).toBeNull();
    expect(mediaKindForType("text/plain")).toBeNull();
    expect(mediaKindForType("application/json")).toBeNull();
    expect(mediaKindForType("")).toBeNull();
  });
});

describe("isSupportedImageMediaType", () => {
  it("is true only for images", () => {
    expect(isSupportedImageMediaType("image/webp")).toBe(true);
    expect(isSupportedImageMediaType("image/jpg")).toBe(true);
    expect(isSupportedImageMediaType("application/pdf")).toBe(false);
    expect(isSupportedImageMediaType("image/bmp")).toBe(false);
  });
});
