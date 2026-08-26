import { css } from "@emotion/react";

import { ExternalLink, Text } from "@phoenix/components";
import {
  Attachment,
  AttachmentPreview,
  AttachmentRemove,
} from "@phoenix/components/ai/attachment";
import { useIsRenderableImage } from "@phoenix/hooks/useIsRenderableImage";
import { useOpenableMediaUrl } from "@phoenix/hooks/useOpenableMediaUrl";
import {
  declaredInlineMediaType,
  mediaDisplayName,
  resolveMediaUrl,
} from "@phoenix/utils/mediaUtils";

import { MediaFileDetails } from "./MediaNotAnImage";

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
 * A document fills its tile with the same body a span's document tile uses — name,
 * type, and a link to the file — since a grey page icon is all a PDF thumbnail will
 * ever be, and every one of them looks alike. An image needs none of that, having
 * shown itself, so it keeps the plain thumbnail and carries its link in the caption.
 *
 * The example stores only the reference, so a stored one's media type has to be
 * probed rather than read. Without that a PDF would be handed to an `<img>` and show
 * the browser's broken-image icon, which reads as "this attachment is broken" when it
 * is merely not a picture.
 */

/** Where the probe lands for media that is not an image; see `mediaType` below. */
const IMAGE_TYPE = "image/*";
const DOCUMENT_TYPE = "application/pdf";

/* The link is a tile caption, not body text, so it is sized with the name above it
   rather than inheriting the container's font size. */
const openLinkCSS = css`
  font-size: var(--global-font-size-xs);
`;

/*
 * The tile and its caption, stacked, and the one place a media tile's footprint is
 * decided. Every surface showing example media picks a density and nothing else; what
 * a tile of that density measures is settled here, so the panel, the editor and a
 * table cell cannot drift apart. Both halves read one custom property, which is what
 * keeps a caption the width of the tile it captions.
 *
 * A thumbnail only has to be recognisable. A tile carrying the details holds the same
 * three lines the span's document tile does and is given room for them — at either
 * density, since a name and a type nobody can read is the one thing worse than a
 * grey square.
 */
const tileCSS = css`
  --example-media-tile-size: var(--global-dimension-size-1200);

  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--global-dimension-size-50);

  [data-attachment][data-variant="grid"] {
    width: var(--example-media-tile-size);
    height: var(--example-media-tile-size);
  }
`;

/** A thumbnail in a table cell, where two full-size ones would swallow the column. */
const compactTileCSS = css`
  --example-media-tile-size: var(--global-dimension-size-800);
`;

const detailsTileCSS = css`
  --example-media-tile-size: var(--global-dimension-size-2500);
`;

/** The smallest the three lines stay legible at; a cell is narrow, not tiny. */
const compactDetailsTileCSS = css`
  --example-media-tile-size: var(--global-dimension-size-1800);
`;

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
  /** Set where the container is narrow — a table cell — to shrink the tile. */
  compact?: boolean;
};

export function ExampleMediaAttachment({
  mediaKey,
  label,
  url,
  onRemove,
  compact = false,
}: ExampleMediaAttachmentProps) {
  const caption = label ?? mediaKey;
  const resolvedUrl = resolveMediaUrl(url);
  const openableUrl = useOpenableMediaUrl(resolvedUrl);
  // Inline media states its own type in the header, which is both exact and free;
  // only a stored reference has to be probed, and a probe answers a narrower
  // question than the type does — image, or something to be opened rather than
  // drawn. PDF is what that something almost always is here, being the one
  // non-image an example can be given.
  const declaredMediaType = declaredInlineMediaType(resolvedUrl);
  const isImage = useIsRenderableImage(resolvedUrl);
  const mediaType = declaredMediaType ?? (isImage ? IMAGE_TYPE : DOCUMENT_TYPE);
  const isDrawable = declaredMediaType?.startsWith("image/") ?? isImage;
  const name = mediaDisplayName(url, mediaType);
  const showsDetails = !isDrawable;

  return (
    <div
      css={[
        tileCSS,
        showsDetails
          ? compact
            ? compactDetailsTileCSS
            : detailsTileCSS
          : compact
            ? compactTileCSS
            : undefined,
      ]}
    >
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
        // Lets the container give a tile carrying the details room for them,
        // without reaching for `:has()` to ask what a tile holds.
        data-example-media-details={showsDetails ? "" : undefined}
      >
        <AttachmentPreview
          fallback={
            showsDetails ? (
              <MediaFileDetails
                url={resolvedUrl}
                mediaType={mediaType}
                name={name}
              />
            ) : undefined
          }
        />
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
        {showsDetails ? null : (
          <div css={openLinkCSS}>
            <ExternalLink href={openableUrl}>Open</ExternalLink>
          </div>
        )}
      </div>
    </div>
  );
}
