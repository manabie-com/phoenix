import { useIsRenderableImage } from "@phoenix/hooks/useIsRenderableImage";
import { SpanImage } from "@phoenix/pages/trace/span/SpanImage";
import { resolveMediaUrl } from "@phoenix/utils/mediaUtils";

import { MediaNotAnImage } from "./MediaNotAnImage";

/**
 * Media that Phoenix itself stores, as recorded on a span.
 *
 * Two things `SpanImage` cannot do for such media, both belonging to the media
 * feature rather than to the image viewer:
 *
 * A `phoenix://media/<sha256>` reference is not a URL a browser can load — it has to
 * be resolved to the REST path first.
 *
 * And not everything recorded as image content is an image. A document recorded
 * before documents were named separately reads back as image content, so the
 * reference is probed and a document offered instead of a broken-image icon.
 *
 * Callers route only hosted references here — see `isHostedMediaUrl` — so an
 * ordinary image URL keeps rendering through `SpanImage` exactly as it did, with no
 * probe and no change in behaviour. Written as a wrapper for the same reason: the
 * expand affordance, the container and the redacted placeholder stay where upstream
 * put them.
 */
export function SpanMedia({ url }: { url: string }) {
  const resolvedUrl = resolveMediaUrl(url);
  const isImage = useIsRenderableImage(resolvedUrl);

  if (!isImage) {
    return <MediaNotAnImage url={resolvedUrl} />;
  }
  return <SpanImage url={resolvedUrl} />;
}
