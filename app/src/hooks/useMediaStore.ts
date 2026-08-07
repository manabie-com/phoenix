import { useCallback, useState } from "react";

import {
  importMediaFromUrl,
  uploadMedia,
  type UploadedMedia,
} from "@phoenix/utils/mediaUtils";

/**
 * Storing media in Phoenix, with the busy and error state a picker needs.
 *
 * Every surface that attaches media does the same three things — store the bytes,
 * disable itself while that is in flight, and show what went wrong inline rather
 * than in a toast. Extracted so the playground's media input and the dataset
 * example editor share one implementation: they are the same operation, and two
 * copies would be two places to fix when the upload contract changes.
 *
 * Media is always stored, never merely referenced. A pasted third-party URL is
 * imported and kept, so a run does not depend on that host still serving the file.
 */
export function useMediaStore() {
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Runs a store operation, returning what was stored or `null` on failure.
   *
   * The failure is reported through `error` rather than thrown: every caller would
   * otherwise wrap this in the same try/catch to put the message on screen.
   */
  const store = useCallback(
    async (
      fetchMedia: () => Promise<UploadedMedia>,
      fallbackMessage: string
    ): Promise<UploadedMedia | null> => {
      setIsBusy(true);
      setError(null);
      try {
        return await fetchMedia();
      } catch (storeError) {
        setError(
          storeError instanceof Error ? storeError.message : fallbackMessage
        );
        return null;
      } finally {
        setIsBusy(false);
      }
    },
    []
  );

  const upload = useCallback(
    (file: File, fallbackMessage: string) =>
      store(() => uploadMedia(file), fallbackMessage),
    [store]
  );

  const importUrl = useCallback(
    (url: string, fallbackMessage: string) =>
      store(() => importMediaFromUrl(url), fallbackMessage),
    [store]
  );

  return { isBusy, error, setError, upload, importUrl };
}
