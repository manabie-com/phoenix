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
 *
 * Every URL leaves here **canonical**: `data:<normalized-type>;base64,<payload>`, paired
 * with the same type in the `mediaType` field. Both halves are returned together for a
 * reason. An earlier revision passed an already-`data:` URL through untouched and
 * normalized only the field, on the grounds that the URL's own declared type was
 * authoritative — which produced `{url: "data:image/jpg;…", mediaType: "image/jpeg"}`.
 * `MediaContent` requires those two to be equal (`db/types/media.py`), so the pair the
 * alias was meant to rescue was rejected, and rejection is not local: it aborts the
 * whole template conversion and with it the run. Canonicalizing the header and the
 * field from one value makes disagreement unrepresentable.
 */
import { normalizeMediaType } from "./supportedMediaTypes";

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
 * The same payload in the standard base64 alphabet, or null when it is not base64.
 *
 * Length is checked as well as the alphabet: base64 encodes three bytes to four
 * characters, so a length that is not a multiple of four cannot decode. Without that
 * check a stray token beside a declared media type became a malformed `data:` URL that
 * failed server-side, instead of being skipped here.
 *
 * `-` and `_` are translated to `+` and `/` first, because the Google GenAI SDK writes
 * `inline_data.data` in the URL-safe alphabet (RFC 4648 §5) — which is what a real
 * `google-adk` span carries, and what both `atob` and Python's strict `b64decode`
 * reject. The translation is lossless and unambiguous: neither alphabet uses the
 * other's two characters, so a payload written in either survives it unchanged in
 * meaning. Rejecting one instead cost the whole attachment silently — the messages
 * replayed and the PDF did not.
 *
 * Returning the translated string rather than a boolean is what makes that fix stick:
 * the payload is embedded in a `data:` URL, and the server decodes that URL strictly,
 * so the URL has to carry the standard alphabet no matter which one was recorded.
 *
 * Padding is required rather than added. An unpadded payload is left to fail, because
 * re-padding to the next multiple of four is exactly what would let a stray token
 * (`foo-bar`) through the length check that is here to stop it.
 */
function standardBase64(value: string): string | null {
  const translated = value.replace(/-/g, "+").replace(/_/g, "/");
  return translated.length > 0 &&
    translated.length % 4 === 0 &&
    /^[A-Za-z0-9+/]+={0,2}$/.test(translated)
    ? translated
    : null;
}

/** Inline media as the playground carries it: a canonical URL and its media type. */
export type CanonicalInlineMedia = { url: string; mediaType: string };

/** `data:<type>[;params],<payload>`, split into the three parts that matter. */
const DATA_URL_PATTERN = /^data:([-\w.+]+\/[-\w.+]+)((?:;[^,]*)?),([\s\S]*)$/;

/**
 * A recorded `data:` URL in the form the server accepts, or null when it is not usable.
 *
 * Checks what the server checks, so that a URL it would reject never becomes a content
 * part: the `base64` parameter has to be present (`parse_media_url` refuses a data URL
 * without it) and the payload has to actually decode (`InlineMedia.decode` uses strict
 * base64). Skipping one attachment here costs the attachment; letting it through costs
 * the whole run.
 *
 * The returned URL is rebuilt rather than echoed, so its header always states the same
 * normalized type as `mediaType`.
 *
 * @param url The `data:` URL as recorded.
 */
export function canonicalDataUrl(url: string): CanonicalInlineMedia | null {
  const match = DATA_URL_PATTERN.exec(url);
  if (match == null) {
    return null;
  }
  const [, declaredType, parameters, payload] = match;
  if (!parameters.slice(1).split(";").includes("base64")) {
    return null;
  }
  const compact = standardBase64(payload.replace(/\s/g, ""));
  if (compact == null) {
    return null;
  }
  const mediaType = normalizeMediaType(declaredType);
  return { url: `data:${mediaType};base64,${compact}`, mediaType };
}

/**
 * Media a span recorded, as a canonical inline pair, or null when it is unusable.
 *
 * @param declaredMediaType The media type recorded beside the payload, if any. Ignored
 *   when the payload is a `data:` URL, which declares its own.
 * @param payload The payload as recorded: a `data:` URL, base64, or a bytes repr.
 */
export function inlineMedia(
  declaredMediaType: string,
  payload: string
): CanonicalInlineMedia | null {
  if (payload.startsWith("data:")) {
    return canonicalDataUrl(payload);
  }
  if (!declaredMediaType) {
    return null;
  }
  const bytes = decodePythonBytesRepr(payload);
  let base64: string | null;
  if (bytes) {
    base64 = bytes.length > 0 ? toBase64(bytes) : null;
  } else {
    base64 = standardBase64(payload.replace(/\s/g, ""));
  }
  if (base64 == null) {
    return null;
  }
  const mediaType = normalizeMediaType(declaredMediaType);
  return { url: `data:${mediaType};base64,${base64}`, mediaType };
}
