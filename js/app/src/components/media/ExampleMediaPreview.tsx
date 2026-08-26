import { css } from "@emotion/react";
import type { ComponentProps } from "react";

import { Flex, Text, View } from "@phoenix/components";
import { Attachments } from "@phoenix/components/ai/attachment";
import { findExampleMediaAnywhere } from "@phoenix/utils/datasetExampleMediaUtils";

import { ExampleMediaAttachment } from "./ExampleMediaAttachment";

/**
 * The media a dataset example carries, shown as thumbnails.
 *
 * An attachment is stored as a `phoenix://media/<sha256>` reference under the
 * variable it fills, which is exactly right for a run and useless to a reader: the
 * JSON beside it shows a digest where the image is — and two 64-character digests
 * push the actual question out of view, so the reference costs more than it tells.
 * Rendering the bytes is what makes a row reviewable: whether the right image is
 * attached, and to the right variable, is not a question a digest can answer.
 *
 * Read-only on purpose. Editing happens in the example editor, where a change can
 * be saved as a revision; offering removal here would imply a mutation the views
 * that show this do not perform.
 */

/*
 * The attachment variants right-align themselves for a chat composer. Here they
 * follow the card's content, on the left.
 */
const previewCSS = css`
  & > [data-variant="grid"] {
    margin-left: 0;
  }
`;

/*
 * How much room a table cell can spare. The tiles size themselves from it — see
 * `ExampleMediaAttachment`, which owns every measurement — so all this adds is the
 * one thing a cell knows and a tile does not: that a variable name broken across two
 * lines mid-word costs the column more than a caption wider than its tile does.
 */
const compactCSS = css`
  .example-media__caption {
    width: auto;
    white-space: nowrap;
  }
`;

export type ExampleMediaPreviewProps = {
  /** The example's input, as JSON text or as an already-parsed object. */
  input: unknown;
  /**
   * Padding around the block. Pass `"size-0"` where the container already pads,
   * so the media lines up with the JSON beside it instead of sitting indented
   * from everything else in the cell.
   */
  padding?: ComponentProps<typeof View>["padding"];
  /** Shrinks the tiles for a narrow container, e.g. a table cell. */
  compact?: boolean;
};

export function ExampleMediaPreview({
  input,
  padding = "size-200",
  compact = false,
}: ExampleMediaPreviewProps) {
  // Deep, unlike the editor: a row saved from a span keeps its media inside
  // `messages[i].content[j]`, so a top-level scan would show nothing for exactly
  // the rows that path produces.
  const attached = findExampleMediaAnywhere(input);
  if (attached.length === 0) {
    return null;
  }
  return (
    <View padding={padding} paddingTop="size-100">
      <Flex direction="column" gap="size-100">
        <Text weight="heavy" size="XS" color="text-700">
          Media
        </Text>
        {/* A plain element, because the tile override has to reach the DOM as a
            class an ancestor selector can hang off. */}
        <div css={[previewCSS, compact ? compactCSS : undefined]}>
          <Attachments variant="grid" style={{ marginLeft: 0 }}>
            {attached.map(({ key, label, url }) => (
              <ExampleMediaAttachment
                key={key}
                mediaKey={key}
                label={label}
                url={url}
                compact={compact}
              />
            ))}
          </Attachments>
        </div>
      </Flex>
    </View>
  );
}
