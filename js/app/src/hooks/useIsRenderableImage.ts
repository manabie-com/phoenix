import { useEffect, useState } from "react";

/**
 * Whether the browser can draw what a URL serves as an image.
 *
 * Phoenix stores media by content digest and keeps no type alongside the
 * reference, so `phoenix://media/<sha256>` says nothing about whether it holds a
 * PNG or a PDF. Anywhere a stored reference is displayed, that has to be settled
 * before choosing between a thumbnail and a document chip — and probing is the
 * only way to settle it without a round trip of its own.
 *
 * Optimistic: `true` until a probe fails, so a working image never flashes a
 * placeholder first. The browser serves the probe and the real `<img>` from one
 * cache entry, so this costs no extra request.
 */
export function useIsRenderableImage(url: string): boolean {
  const [isImage, setIsImage] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const probe = new Image();
    probe.onload = () => {
      if (!cancelled) {
        setIsImage(true);
      }
    };
    probe.onerror = () => {
      if (!cancelled) {
        setIsImage(false);
      }
    };
    probe.src = url;
    return () => {
      cancelled = true;
    };
  }, [url]);

  return isImage;
}
