import { MediaNotAnImage } from "@phoenix/components/media/MediaNotAnImage";
import { useIsRenderableImage } from "@phoenix/hooks/useIsRenderableImage";
import { SpanImage } from "@phoenix/pages/trace/span/SpanImage";
import {
  declaredInlineMediaType,
  resolveMediaUrl,
} from "@phoenix/utils/mediaUtils";
import { isSupportedImageMediaType } from "@phoenix/utils/supportedMediaTypes";

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
  // A `data:` URL states its own type, so probing one only asks the browser to
  // decode a payload to learn what its header already said — and a recovered
  // document's payload is the whole file. The hook still runs unconditionally,
  // since a stored reference carries no type and is the case it exists for.
  const declaredMediaType = declaredInlineMediaType(resolvedUrl);
  const probedIsImage = useIsRenderableImage(resolvedUrl);
  const isImage =
    declaredMediaType == null
      ? probedIsImage
      : isSupportedImageMediaType(declaredMediaType);

  if (!isImage) {
    return <MediaNotAnImage url={resolvedUrl} mediaType={declaredMediaType} />;
  }
  return <SpanImage url={resolvedUrl} />;
}
