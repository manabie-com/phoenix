import { css } from "@emotion/react";
import { useEffect, useMemo } from "react";

import { ExternalLink, Icon, Icons, Text } from "@phoenix/components";
import { dataUrlToBlob, mediaDisplayName } from "@phoenix/utils/mediaUtils";

/*
 * Sized and bordered like the image tile it stands in for, so a message whose media
 * cannot be drawn occupies the same space as one whose media can and the surrounding
 * layout does not shift.
 */
const unavailableCSS = css`
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--global-dimension-size-100);
  width: 200px;
  height: 200px;
  padding: var(--global-dimension-size-200);
  border: 1px solid var(--global-color-gray-500);
  border-radius: var(--global-rounding-small);
  background-color: var(--global-color-gray-200);
  text-align: center;
  color: var(--global-text-color-700);
`;

/**
 * Shown in place of a span's image when the browser cannot render it as one.
 *
 * Not everything recorded as image content is an image. A document recorded before
 * documents were named separately lands here, as does one recovered from a raw
 * request — OpenInference has no document content type, so a PDF can only ever
 * arrive as image content — as does an image whose host has stopped serving it.
 * Either way the browser's broken-image icon says nothing, so this offers the media
 * itself instead.
 *
 * The type is named when it is known. "Cannot be shown as an image" is the honest
 * answer for a stored reference, whose type nothing on the client can discover
 * without fetching the bytes; it reads as a failure for a PDF that arrived
 * declaring itself a PDF and is being shown exactly as intended.
 */
export function MediaNotAnImage({
  url,
  mediaType,
}: {
  url: string;
  mediaType?: string | null;
}) {
  const href = useOpenableMediaUrl(url);
  return (
    <div css={unavailableCSS}>
      <Icon svg={<Icons.FileText />} />
      <Text size="XS" color="text-700">
        {mediaType == null
          ? "Cannot be shown as an image"
          : mediaDisplayName(url, mediaType)}
      </Text>
      {mediaType != null && (
        <Text size="XS" color="text-500">
          {mediaType}
        </Text>
      )}
      <ExternalLink href={href}>Open</ExternalLink>
    </div>
  );
}

/**
 * A URL for the "Open" link that the browser will actually navigate to.
 *
 * Inline media is swapped for a `blob:` URL; see {@link dataUrlToBlob} for why a
 * `data:` one does nothing when clicked. Anything else — a stored reference already
 * resolved to a REST path, or an ordinary URL — is left exactly as it is.
 *
 * The object URL is revoked when the tile goes away, so the bytes are released with
 * it instead of being held for the life of the tab. A tab opened from the link has
 * already started loading by then.
 */
function useOpenableMediaUrl(url: string): string {
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
