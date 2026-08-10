/**
 * Turning the media payload a span recorded into a URL the playground can carry.
 *
 * A provider request holds its media as bytes, and a span records whatever survived
 * being serialized to JSON. Three shapes come out of that, and all three appear in
 * real traces:
 *
 * * a `data:` URL, already exactly what is wanted;
 * * a base64 string beside a declared media type, which is what most SDKs send;
 * * a Python `bytes` **repr** — `"b'\\x89PNG\\r\\n...'"` — written when a payload
 *   holding raw bytes is dumped with a `default=str` fallback. Common with the Google
 *   GenAI SDK, whose `inline_data.data` is `bytes`.
 *
 * The third is the reason this module exists rather than a one-line template string.
 * `dataset_example_media.py` already tolerates it on the server for the span-to-dataset
 * hop; this is the same tolerance on the client, for the span-to-playground hop.
 *
 * Everything ends up as a `data:` URL because that is one of the two forms a chat
 * completion accepts (the other being a `phoenix://media/<sha256>` reference), and the
 * only one reachable without an upload.
 */

/** Python string escapes that stand for a single byte, mapped to that byte. */
const PYTHON_BYTE_ESCAPES: Record<string, number> = {
  "\\": 0x5c,
  "'": 0x27,
  '"': 0x22,
  n: 0x0a,
  r: 0x0d,
  t: 0x09,
  b: 0x08,
  f: 0x0c,
  v: 0x0b,
  a: 0x07,
  "0": 0x00,
};

/**
 * The bytes a Python `bytes` repr denotes, or null if the string is not one.
 *
 * Written by hand rather than with a JSON or eval trick because the escape set is
 * Python's, not JavaScript's: `\v`, `\a` and a bare `\0` all appear, and a byte above
 * 0x7f is written `\xNN` rather than as a code point.
 */
export function decodePythonBytesRepr(value: string): Uint8Array | null {
  const quote = value[1];
  if (
    value[0] !== "b" ||
    (quote !== "'" && quote !== '"') ||
    value.length < 3 ||
    !value.endsWith(quote)
  ) {
    return null;
  }
  const body = value.slice(2, -1);
  const bytes: number[] = [];
  for (let index = 0; index < body.length; index++) {
    const char = body[index];
    if (char !== "\\") {
      const code = char.charCodeAt(0);
      // A repr holds one character per byte. Anything wider means the string was
      // decoded somewhere along the way and its bytes are no longer recoverable.
      if (code > 0xff) {
        return null;
      }
      bytes.push(code);
      continue;
    }
    const escape = body[++index];
    if (escape === "x") {
      const hex = body.slice(index + 1, index + 3);
      if (!/^[0-9a-fA-F]{2}$/.test(hex)) {
        return null;
      }
      bytes.push(parseInt(hex, 16));
      index += 2;
      continue;
    }
    const byte = PYTHON_BYTE_ESCAPES[escape];
    if (byte === undefined) {
      return null;
    }
    bytes.push(byte);
  }
  return new Uint8Array(bytes);
}

/**
 * Base64 for a byte array.
 *
 * Chunked because `String.fromCharCode(...bytes)` on a whole image overflows the
 * argument limit, and an image is exactly what this is given.
 */
function toBase64(bytes: Uint8Array): string {
  const CHUNK_SIZE = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += CHUNK_SIZE) {
    binary += String.fromCharCode(...bytes.subarray(index, index + CHUNK_SIZE));
  }
  return btoa(binary);
}

/**
 * Whether a string is base64 once its line breaks are taken out.
 *
 * Length is checked as well as the alphabet: base64 encodes three bytes to four
 * characters, so a length that is not a multiple of four cannot decode. Without that
 * check a stray token beside a declared media type became a malformed `data:` URL that
 * failed server-side, instead of being skipped here.
 */
function isBase64(value: string): boolean {
  return (
    value.length > 0 &&
    value.length % 4 === 0 &&
    /^[A-Za-z0-9+/]+={0,2}$/.test(value)
  );
}

/**
 * A `data:` URL for media a span recorded, or null when the payload is unusable.
 *
 * @param mediaType The media type declared alongside the payload.
 * @param payload The payload as recorded: a `data:` URL, base64, or a bytes repr.
 * @returns A `data:<mediaType>;base64,<payload>` URL. An input that is already a
 *   `data:` URL is returned unchanged, so its own declared type stays authoritative
 *   and keeps matching what the caller sends as the media type.
 */
export function inlineMediaDataUrl(
  mediaType: string,
  payload: string
): string | null {
  if (payload.startsWith("data:")) {
    return payload;
  }
  if (!mediaType) {
    return null;
  }
  const bytes = decodePythonBytesRepr(payload);
  if (bytes) {
    return bytes.length > 0
      ? `data:${mediaType};base64,${toBase64(bytes)}`
      : null;
  }
  const compact = payload.replace(/\s/g, "");
  return isBase64(compact) ? `data:${mediaType};base64,${compact}` : null;
}

/** The media type a `data:` URL declares, lowercased, or null when it declares none. */
export function dataUrlMediaType(url: string): string | null {
  const match = /^data:([-\w.+]+\/[-\w.+]+)[;,]/.exec(url);
  return match ? match[1].toLowerCase() : null;
}
