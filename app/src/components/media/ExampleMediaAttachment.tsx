import { css } from "@emotion/react";

import { Flex, Text } from "@phoenix/components";
import {
  Attachment,
  AttachmentPreview,
  AttachmentRemove,
} from "@phoenix/components/ai/attachment";
import { useIsRenderableImage } from "@phoenix/hooks/useIsRenderableImage";
import { mediaDisplayName, resolveMediaUrl } from "@phoenix/utils/mediaUtils";

/**
 * One media reference on a dataset example, drawn as a captioned tile.
 *
 * Shared by the editor and the read-only view so both describe an attachment the
 * same way, and both name the variable it fills — which slot a file lands in is
 * the thing a reader is actually checking, and a digest answers nothing on its
 * own.
 *
 * The caption is written here rather than left to `AttachmentInfo`, which renders
 * nothing in the grid variant by design: a grid tile is image-only. A bare grid of
 * thumbnails is exactly the wrong trade for this use — two scans look alike, and
 * the question is never "is there an image" but "is it under `question_image` or
 * `answer_image`".
 *
 * The example stores only the reference, so the media type has to be probed rather
 * than read. Without that a PDF would be handed to an `<img>` and show the
 * browser's broken-image icon, which reads as "this attachment is broken" when it
 * is merely not a picture.
 */

/** Types that put the attachment in the right category for its preview. */
const IMAGE_TYPE = "image/*";
const DOCUMENT_TYPE = "application/pdf";

/* Matches the grid tile's width so a long variable name wraps rather than
   widening the column and pushing its neighbours out of alignment. Reads the
   same custom property the tile does, so a container that shrinks the tiles
   shrinks the captions with them and the two never disagree. */
const captionCSS = css`
  width: var(--example-media-tile-size, var(--global-dimension-size-1200));
  word-break: break-word;
`;

export type ExampleMediaAttachmentProps = {
  /** Where the reference lives — a variable name, or a path within the input. */
  mediaKey: string;
  /**
   * The short form to caption it with. Defaults to `mediaKey`, which is right for
   * a top-level variable; a nested path passes its trimmed form instead.
   */
  label?: string;
  /** The `phoenix://media/<sha256>` reference. */
  url: string;
  /** Omitted in read-only views, where there is no revision to save a removal to. */
  onRemove?: () => void;
};

export function ExampleMediaAttachment({
  mediaKey,
  label,
  url,
  onRemove,
}: ExampleMediaAttachmentProps) {
  const caption = label ?? mediaKey;
  const resolvedUrl = resolveMediaUrl(url);
  const isImage = useIsRenderableImage(resolvedUrl);
  const mediaType = isImage ? IMAGE_TYPE : DOCUMENT_TYPE;
  const name = mediaDisplayName(url, mediaType);

  return (
    <Flex direction="column" gap="size-50" alignItems="start">
      <Attachment
        data={{
          // The location, not the reference: inline media carries its bytes in
          // the URL, and a few hundred kilobytes of base64 makes a poor identity.
          id: mediaKey,
          type: "file",
          mediaType,
          // Becomes the image's alt text, so it says which slot this fills
          // rather than repeating a digest to a screen reader.
          filename: `${mediaKey}: ${name}`,
          url: resolvedUrl,
        }}
        onRemove={onRemove}
      >
        <AttachmentPreview />
        {onRemove ? (
          <AttachmentRemove label={`Remove media for ${mediaKey}`} />
        ) : null}
      </Attachment>
      {/* The full location stays reachable on hover, since the caption is the
          trimmed form and a nested path is where the ambiguity lives. */}
      <div css={captionCSS} className="example-media__caption" title={mediaKey}>
        <Text size="XS" weight="heavy">
          {caption}
        </Text>
      </div>
    </Flex>
  );
}
