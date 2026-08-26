import { css } from "@emotion/react";

import { ExternalLink, Icon, Icons, Text } from "@phoenix/components";
import { useOpenableMediaUrl } from "@phoenix/hooks/useOpenableMediaUrl";
import { mediaDisplayName } from "@phoenix/utils/mediaUtils";

/*
 * Sized and bordered like the image tile it stands in for, so a message whose media
 * cannot be drawn occupies the same space as one whose media can and the surrounding
 * layout does not shift.
 */
const unavailableCSS = css`
  display: flex;
  width: 200px;
  height: 200px;
  border: 1px solid var(--global-color-gray-500);
  border-radius: var(--global-rounding-small);
  background-color: var(--global-color-gray-200);
  color: var(--global-text-color-700);
`;

/* Fills whatever frame it is given, so the same body works in this component's own
   200px tile and inside an attachment tile of another size. */
const detailsCSS = css`
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--global-dimension-size-100);
  padding: var(--global-dimension-size-200);
  text-align: center;
  overflow-wrap: anywhere;
`;

export type MediaFileDetailsProps = {
  /** A URL the browser can load — a resolved REST path, or inline media. */
  url: string;
  /** The media type, when anything knows it. */
  mediaType?: string | null;
  /**
   * What to call the file. Defaults to a name derived from `url`, which is the best
   * a span can do; a caller still holding the `phoenix://media/<sha256>` reference
   * passes the digest-derived name instead, so two documents in one place read as
   * two documents rather than twice as `media.pdf`.
   */
  name?: string;
};

/**
 * What a piece of media *is*, for media that cannot be shown as a picture: its name,
 * its type, and a link to the file itself.
 *
 * Frameless, because every caller already has a frame — {@link MediaNotAnImage}'s own
 * tile on a span, an `<AttachmentPreview>`'s box on a dataset example. Giving it one
 * of its own would nest a bordered box inside a bordered box.
 *
 * The type is named when it is known. "Cannot be shown as an image" is the honest
 * answer for a stored reference, whose type nothing on the client can discover
 * without fetching the bytes; it reads as a failure for a PDF that arrived
 * declaring itself a PDF and is being shown exactly as intended.
 *
 * The link matters more here than anywhere else in the app: a document tile is a grey
 * icon whatever the document says, so opening it is the only way to check the media
 * is the media that was meant.
 */
export function MediaFileDetails({
  url,
  mediaType,
  name,
}: MediaFileDetailsProps) {
  const href = useOpenableMediaUrl(url);
  return (
    <div css={detailsCSS}>
      <Icon svg={<Icons.FileText />} />
      <Text size="XS" color="text-700">
        {mediaType == null
          ? "Cannot be shown as an image"
          : (name ?? mediaDisplayName(url, mediaType))}
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
 * Shown in place of a span's image when the browser cannot render it as one.
 *
 * Not everything recorded as image content is an image. A document recorded before
 * documents were named separately lands here, as does one recovered from a raw
 * request — OpenInference has no document content type, so a PDF can only ever
 * arrive as image content — as does an image whose host has stopped serving it.
 * Either way the browser's broken-image icon says nothing, so this offers the media
 * itself instead.
 */
export function MediaNotAnImage({ url, mediaType }: MediaFileDetailsProps) {
  return (
    <div css={unavailableCSS}>
      <MediaFileDetails url={url} mediaType={mediaType} />
    </div>
  );
}
