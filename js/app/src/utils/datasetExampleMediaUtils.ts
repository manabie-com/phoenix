import { isRenderableMediaUrl } from "./mediaUtils";

/**
 * Media references living inside a dataset example's input.
 *
 * An example's input is the map of template variables a run is given, so a media
 * variable is filled the same way a text one is: a top-level key whose name matches
 * the variable, holding a `phoenix://media/<sha256>` reference. There is no separate
 * attachment channel, and there does not need to be — the server substitutes the
 * reference into the media block exactly as it substitutes text, and the media
 * sweeper already scans example inputs so a referenced file is never reclaimed.
 *
 * What was missing was any way to *put* a reference there: the example editors are
 * raw JSON, and a digest is not something anyone can type. These helpers are the
 * bridge between a file the user picked and the JSON text the editor holds.
 */

/** A media reference found in an example's input, and the variable it fills. */
export type ExampleMediaEntry = {
  /**
   * Where the reference was found: a top-level key for an example authored as
   * template variables, or a path for one saved from a span. Unique within an
   * example, so it doubles as a stable identity.
   */
  key: string;
  /** The short form to show a reader. Equal to `key` for a top-level variable. */
  label: string;
  /** The `phoenix://media/<sha256>` reference. */
  url: string;
};

/**
 * Trailing path segments that say how to reach the URL rather than what it is.
 *
 * `messages[0].content[1].image_url.url` names one thing — the image on the second
 * content part — and three quarters of it is plumbing. Dropping the accessor tail
 * leaves the part a reader is actually locating.
 */
const ACCESSOR_SEGMENTS = new Set([
  "url",
  "image_url",
  "image",
  "file",
  "file_url",
  // Written by the server when it lifts inline bytes out of a span into the media
  // store, and the wrapper they were carried in.
  "phoenix_media_url",
  "inline_data",
]);

function displayLabel(path: string): string {
  const segments = path.split(".");
  while (
    segments.length > 1 &&
    ACCESSOR_SEGMENTS.has(segments[segments.length - 1])
  ) {
    segments.pop();
  }
  return segments.join(".") || path;
}

/** How the example JSON is re-serialized after an edit; matches the editors' style. */
const JSON_INDENT = 2;

/** Narrows parsed JSON to something that can hold named variables. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Parses example JSON, returning `null` unless it is an object.
 *
 * An array or a bare scalar is valid JSON but cannot hold named variables, so it is
 * rejected alongside malformed text rather than being half-supported.
 */
function parseExampleObject(json: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(json);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Parses the input if it arrived as text, or takes the object as given. */
function asExampleObject(input: unknown): Record<string, unknown> | null {
  if (typeof input === "string") {
    return parseExampleObject(input);
  }
  return isRecord(input) ? input : null;
}

/**
 * The media references an example's input holds as **top-level** keys, in key order.
 *
 * Takes the input either as the JSON text an editor holds or as the already-parsed
 * object a table cell is handed. Both surfaces need the same answer and neither
 * should have to convert first — a second implementation of "which values are
 * media" is exactly the thing that drifts.
 *
 * Deliberately shallow: this is what the **editor** uses, and a top-level key is
 * exactly what fills a template variable. Surfacing a nested digest here would
 * offer a remove button for something no run reads, and removing it would edit a
 * structure the user did not mean to touch. Use {@link findExampleMediaAnywhere}
 * for read-only views, which have the opposite problem.
 */
export function findExampleMedia(input: unknown): ExampleMediaEntry[] {
  const parsed = asExampleObject(input);
  if (parsed === null) {
    return [];
  }
  return Object.entries(parsed).flatMap(([key, value]) =>
    typeof value === "string" && isRenderableMediaUrl(value)
      ? [{ key, label: key, url: value }]
      : []
  );
}

/** Guards against a pathological structure; far deeper than any real example. */
const MAX_SCAN_DEPTH = 12;

/**
 * Every media reference in an example's input, however deeply nested.
 *
 * A row saved from a span nests its media inside `messages[i].content[j]`, because
 * that is the shape a conversation has — the reference is never a top-level key.
 * A read-only view that scanned only the top level would show nothing for exactly
 * the rows the span-to-dataset path produces, which is most of the multimodal ones.
 *
 * The trade the editor cannot make, this view can: a reader is asking "does this
 * row carry the right picture", and where in the JSON it sits is a detail. So the
 * answer is every reference, labelled by the path it was found at.
 */
export function findExampleMediaAnywhere(input: unknown): ExampleMediaEntry[] {
  const found: ExampleMediaEntry[] = [];
  const seen = new Set<string>();

  const walk = (value: unknown, path: string, depth: number): void => {
    if (depth > MAX_SCAN_DEPTH) {
      return;
    }
    if (typeof value === "string") {
      // Keyed by path rather than url: the same image under two variables is two
      // attachments, because which slot it fills is the thing being checked.
      if (isRenderableMediaUrl(value) && !seen.has(path)) {
        seen.add(path);
        found.push({ key: path, label: displayLabel(path), url: value });
      }
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) =>
        walk(item, `${path}[${index}]`, depth + 1)
      );
      return;
    }
    if (isRecord(value)) {
      for (const [key, child] of Object.entries(value)) {
        walk(child, path ? `${path}.${key}` : key, depth + 1);
      }
    }
  };

  walk(asExampleObject(input), "", 0);
  return found;
}

/**
 * The example JSON with `key` set to a media reference.
 *
 * Returns `null` when the text is not a JSON object, which is the caller's signal
 * to say so rather than to overwrite whatever the user was in the middle of typing.
 *
 * Re-serializing discards the user's own whitespace. That is a real cost, but the
 * alternative — splicing text into a document that may be mid-edit — trades a
 * predictable reformat for the chance of producing invalid JSON.
 */
export function setExampleMedia(
  json: string,
  key: string,
  url: string
): string | null {
  const parsed = parseExampleObject(json);
  if (parsed === null) {
    return null;
  }
  return JSON.stringify({ ...parsed, [key]: url }, null, JSON_INDENT);
}

/** The example JSON with `key` removed, or `null` if the text is not an object. */
export function removeExampleMedia(json: string, key: string): string | null {
  const parsed = parseExampleObject(json);
  if (parsed === null) {
    return null;
  }
  const { [key]: _removed, ...rest } = parsed;
  return JSON.stringify(rest, null, JSON_INDENT);
}

/**
 * Whether `key` can name a media variable on this example.
 *
 * A name is required because it is what binds the file to the prompt's media
 * variable — an attachment under the wrong key fills no slot, and now that an
 * unsupplied slot is skipped rather than raised, that mistake would run silently
 * without an image. Blank is rejected here so the user learns before uploading.
 */
export function isValidExampleMediaKey(key: string): boolean {
  return key.trim().length > 0;
}
