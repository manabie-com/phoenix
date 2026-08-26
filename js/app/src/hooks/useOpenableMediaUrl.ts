import { useEffect, useMemo } from "react";

import { dataUrlToBlob } from "@phoenix/utils/mediaUtils";

/**
 * A URL an "Open in a new tab" link will actually navigate to.
 *
 * Inline media is swapped for a `blob:` URL; see {@link dataUrlToBlob} for why a
 * `data:` one does nothing when clicked. Anything else — a stored reference already
 * resolved to a REST path, or an ordinary URL — is left exactly as it is.
 *
 * The object URL is revoked when the caller unmounts, so the bytes are released with
 * it instead of being held for the life of the tab. A tab opened from the link has
 * already started loading by then.
 *
 * Shared by every surface that offers the media itself — a span's document tile and
 * a dataset example's attachment — so "what does clicking this open" has one answer.
 */
export function useOpenableMediaUrl(url: string): string {
  const objectUrl = useMemo(() => {
    const blob = dataUrlToBlob(url);
    return blob == null ? null : URL.createObjectURL(blob);
  }, [url]);
  useEffect(
    () => () => {
      if (objectUrl != null) {
        URL.revokeObjectURL(objectUrl);
      }
    },
    [objectUrl]
  );
  return objectUrl ?? url;
}
