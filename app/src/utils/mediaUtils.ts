import { z } from "zod";

import { authFetch } from "@phoenix/authFetch";
import { BASE_URL } from "@phoenix/config";

import { prependBasename } from "./routingUtils";

const PHOENIX_MEDIA_URL_PREFIX = "phoenix://media/";

/**
 * Whether a URL points at media Phoenix stores.
 *
 * Lets a caller send only the fork's own references down the fork's rendering path
 * and leave ordinary image URLs on the one that was already there.
 */
export function isHostedMediaUrl(url: string): boolean {
  return url.startsWith(PHOENIX_MEDIA_URL_PREFIX);
}

/**
 * Whether a URL carries its media inline as a `data:` URI.
 *
 * Phoenix's own playground stores media and records a reference, but a span from
 * an instrumented app records whatever that app passed its provider — which is
 * very often base64 inline, because that is what the SDKs take. Media saved out of
 * such a span is inline too.
 *
 * Narrowed to the media types that can actually be shown, so an arbitrary `data:`
 * blob of some unrelated type is not mistaken for a picture.
 */
export function isInlineMediaUrl(url: string): boolean {
  return /^data:(image\/|application\/pdf)/i.test(url);
}

/**
 * Whether a value is media the browser can render, however it is carried.
 *
 * The distinction between stored and inline matters when *resolving* a reference;
 * it does not matter at all when deciding whether something is a picture worth
 * showing. Views that only asked `isHostedMediaUrl` silently showed nothing for
 * inline media, which is the common shape outside the playground.
 */
export function isRenderableMediaUrl(url: string): boolean {
  return isHostedMediaUrl(url) || isInlineMediaUrl(url);
}

/**
 * Resolves a prompt media reference into a URL the browser can load.
 *
 * Media stored in Phoenix is referenced as `phoenix://media/<sha256>` so that
 * span attributes and prompt templates stay small; the bytes are served by the
 * REST API. Data URLs and ordinary http(s) URLs are returned unchanged.
 */
export function resolveMediaUrl(url: string): string {
  if (!isHostedMediaUrl(url)) {
    return url;
  }
  const sha256 = url.slice(PHOENIX_MEDIA_URL_PREFIX.length);
  return prependBasename(`/v1/media/${encodeURIComponent(sha256)}`);
}

/** Extensions for the media types Phoenix stores, for naming a stored file. */
const MEDIA_TYPE_EXTENSIONS: Record<string, string> = {
  "application/pdf": "pdf",
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/heic": "heic",
  "image/heif": "heif",
};

/**
 * A display name for media a prompt references.
 *
 * A prompt stores media by content digest and keeps no original filename, so the
 * name is derived from the digest. That makes it stable: the same document reads
 * the same before and after a reload, which the uploaded file's own name would
 * not, since identical bytes uploaded twice keep whichever name arrived first.
 */
export function mediaDisplayName(url: string, mediaType: string): string {
  const extension =
    MEDIA_TYPE_EXTENSIONS[mediaType] ?? mediaType.split("/").at(-1) ?? "bin";
  if (!url.startsWith(PHOENIX_MEDIA_URL_PREFIX)) {
    return `media.${extension}`;
  }
  const digest = url.slice(PHOENIX_MEDIA_URL_PREFIX.length);
  return `${digest.slice(0, 8)}.${extension}`;
}

const uploadMediaResponseSchema = z.object({
  data: z.object({
    sha256: z.string(),
    media_type: z.string(),
    size_bytes: z.number(),
    url: z.string(),
  }),
});

const uploadErrorSchema = z.object({ detail: z.string() });

export type UploadedMedia = {
  /** The `phoenix://media/<sha256>` reference to store on a prompt. */
  url: string;
  mediaType: string;
};

/**
 * Stores a media file in Phoenix and returns the reference to put on a prompt.
 *
 * Uploading the same bytes twice yields the same reference, so re-adding an image
 * costs nothing. Sent as multipart rather than through the typed API client
 * because the generated schema models the file field as a string.
 *
 * @throws Error with the server's message when the upload is rejected — most
 * often because the file is too large or is not a supported image.
 */
export async function uploadMedia(file: File): Promise<UploadedMedia> {
  const body = new FormData();
  body.append("file", file);
  const response = await authFetch(`${BASE_URL}/v1/media`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const parsedError = uploadErrorSchema.safeParse(payload);
    throw new Error(
      parsedError.success
        ? parsedError.data.detail
        : `Could not upload ${file.name}.`
    );
  }
  const parsed = uploadMediaResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new Error(`Unexpected response while uploading ${file.name}.`);
  }
  return {
    url: parsed.data.data.url,
    mediaType: parsed.data.data.media_type,
  };
}

/**
 * Imports an image from a public URL and returns the reference to put on a prompt.
 *
 * Phoenix fetches the image once and stores it, so the prompt references stored
 * media rather than the third-party host — a run never depends on that host still
 * serving the image.
 *
 * @throws Error with the server's message when the URL cannot be imported.
 */
export async function importMediaFromUrl(url: string): Promise<UploadedMedia> {
  const response = await authFetch(`${BASE_URL}/v1/media/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data: { url } }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const parsedError = uploadErrorSchema.safeParse(payload);
    throw new Error(
      parsedError.success
        ? parsedError.data.detail
        : "Could not import that image URL."
    );
  }
  const parsed = uploadMediaResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new Error("Unexpected response while importing the image.");
  }
  return {
    url: parsed.data.data.url,
    mediaType: parsed.data.data.media_type,
  };
}
