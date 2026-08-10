/**
 * The media types a run accepts, and what each one is.
 *
 * One list, because three had already drifted apart. `spanRawInputMessages` kept its
 * own image and file sets, `spanMessageImages` kept none at all and so let anything
 * through, and `mediaUtils` kept a third copy as an extension map. A media type that
 * only one of them knows about is a media type the paths disagree on — and the
 * disagreement is not symmetric, because a type the server refuses does not merely
 * drop an attachment: `ImageContentPart` and `FileContentPart` raise, and
 * `PromptChatTemplateInput.to_orm` converts the whole template in one pass, so a
 * single bad part aborts the entire run before any provider is contacted.
 *
 * Mirrors `SUPPORTED_IMAGE_MEDIA_TYPES` and `SUPPORTED_FILE_MEDIA_TYPES` in
 * `phoenix/db/types/media.py`, which is the authority. `image/svg+xml` is absent
 * there deliberately — SVG can carry script and Phoenix serves stored media from its
 * own origin — so it is absent here too.
 */

/** What a media type is, or null when a run would refuse it. */
export type MediaKindForType = "image" | "file";

const IMAGE_MEDIA_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "image/heic",
  "image/heif",
]);

const FILE_MEDIA_TYPES = new Set(["application/pdf"]);

/**
 * Spellings that mean a supported type without being it.
 *
 * `image/jpg` is the one that matters. It is not a registered media type and the
 * server refuses it, yet it is what hand-written `data:` URLs say, so it arrived on
 * one path as a skipped attachment and on the other as a dead run.
 */
const MEDIA_TYPE_ALIASES: Record<string, string> = {
  "image/jpg": "image/jpeg",
  "image/pjpeg": "image/jpeg",
  "image/x-png": "image/png",
  "application/x-pdf": "application/pdf",
};

/**
 * A media type in the spelling the server expects.
 *
 * Lower-cased and de-aliased. Parameters are dropped, so the `charset` or `name` a
 * recorded `data:` URL may carry does not turn a supported type into an unknown one.
 *
 * @param mediaType The media type as recorded, in any spelling.
 */
export function normalizeMediaType(mediaType: string): string {
  const bare = mediaType.split(";")[0].trim().toLowerCase();
  return MEDIA_TYPE_ALIASES[bare] ?? bare;
}

/**
 * What a run would treat the media type as, or null when it would refuse it.
 *
 * Every path that builds a media part from a span goes through this, so an
 * unsupported type costs one attachment rather than the whole replay.
 *
 * @param mediaType The media type as recorded, in any spelling.
 */
export function mediaKindForType(mediaType: string): MediaKindForType | null {
  const normalized = normalizeMediaType(mediaType);
  if (IMAGE_MEDIA_TYPES.has(normalized)) {
    return "image";
  }
  if (FILE_MEDIA_TYPES.has(normalized)) {
    return "file";
  }
  return null;
}

/** Whether a run would accept the media type as an image. */
export function isSupportedImageMediaType(mediaType: string): boolean {
  return mediaKindForType(mediaType) === "image";
}

/** File extensions for the supported types, for naming a stored file. */
export const MEDIA_TYPE_EXTENSIONS: Record<string, string> = {
  "application/pdf": "pdf",
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/heic": "heic",
  "image/heif": "heif",
};
