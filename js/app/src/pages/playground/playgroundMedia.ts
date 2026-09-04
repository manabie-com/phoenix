import type {
  ContentLayoutPart,
  ImagePart,
  MediaKind,
} from "@phoenix/schemas/mediaPartSchemas";
import type { SpanMessageContentPart } from "@phoenix/schemas/spanMessageContentSchema";
import type {
  PlaygroundInput,
  PlaygroundInstance,
} from "@phoenix/store/playground";
import { canonicalDataUrl } from "@phoenix/utils/inlineMediaPayload";
import { makeImagePart } from "@phoenix/utils/mediaParts";
import { isHostedMediaUrl } from "@phoenix/utils/mediaUtils";
import { isSupportedImageMediaType } from "@phoenix/utils/supportedMediaTypes";

/**
 * Which media a playground template declares, and of what kind.
 *
 * Separate from `playgroundUtils` on purpose: that module is large and changes
 * often for reasons unrelated to media, so keeping this here lets the two evolve
 * without touching each other.
 */

/** The variable-name-to-value map upstream derives, whose values may be unset. */
type VariablesMap = Record<string, string | undefined>;

/** A media variable a template declares, and which kind of media fills it. */
export type MediaVariableDeclaration = {
  variable: string;
  kind: MediaKind;
};

/**
 * The media variables an instance expects, in the order they appear.
 *
 * A media variable is declared by a message part rather than by template syntax,
 * so it does not depend on the template format — a prompt with format `NONE` still
 * has to be given its images.
 *
 * The kind travels with the name because the Inputs panel picks a different
 * control for each: an image is chosen and previewed, a document is chosen and
 * named.
 */
export const extractMediaVariableDeclarationsFromInstance = ({
  instance,
}: {
  instance: PlaygroundInstance;
}): MediaVariableDeclaration[] => {
  if (instance.template.__type !== "chat") {
    return [];
  }
  const declarations: MediaVariableDeclaration[] = [];
  const declare = (variable: string, kind: MediaKind) => {
    // First declaration wins, so a name used for both kinds stays one input
    // rather than rendering two controls that fight over the same value.
    if (!declarations.some((existing) => existing.variable === variable)) {
      declarations.push({ variable, kind });
    }
  };
  instance.template.messages.forEach((message) => {
    (message.imageVariables ?? []).forEach((part) =>
      declare(part.image.variable, "image")
    );
    (message.fileVariables ?? []).forEach((part) =>
      declare(part.file.variable, "file")
    );
  });
  return declarations;
};

export const extractMediaVariableDeclarationsFromInstances = ({
  instances,
}: {
  instances: PlaygroundInstance[];
}): MediaVariableDeclaration[] => {
  const declarations: MediaVariableDeclaration[] = [];
  instances.forEach((instance) => {
    extractMediaVariableDeclarationsFromInstance({ instance }).forEach(
      (declaration) => {
        if (
          !declarations.some(
            (existing) => existing.variable === declaration.variable
          )
        ) {
          declarations.push(declaration);
        }
      }
    );
  });
  return declarations;
};

export const extractMediaVariablesFromInstances = ({
  instances,
}: {
  instances: PlaygroundInstance[];
}): string[] =>
  extractMediaVariableDeclarationsFromInstances({ instances }).map(
    (declaration) => declaration.variable
  );

/**
 * Media variables layered onto whatever `getVariablesMapFromInstances` returned.
 *
 * The fork adds media variables to a derivation that upstream owns, and doing that
 * by editing `getVariablesMapFromInstances` meant rewriting lines inside one of the
 * busiest files in the app. Layering on the outside keeps upstream's function
 * untouched and puts the media logic here, where it can only conflict with itself.
 *
 * A media variable is declared by a message part, not by template syntax, so it
 * survives a `NONE` template format even though text variables do not — which is
 * why the keys are unioned in rather than filtered by format.
 *
 * @param base What upstream derived: the text variables and their values.
 * @param instances The instances to read media declarations from.
 * @param input The playground input, for cached values.
 */
export const withMediaVariables = (
  base: { variablesMap: VariablesMap; variableKeys: string[] },
  {
    instances,
    input,
  }: {
    instances: PlaygroundInstance[];
    input: Pick<PlaygroundInput, "variablesValueCache">;
  }
): {
  variablesMap: VariablesMap;
  variableKeys: string[];
  mediaVariableKeys: string[];
  mediaVariableKinds: Record<string, MediaKind>;
} => {
  const declarations = extractMediaVariableDeclarationsFromInstances({
    instances,
  });
  const cache = input.variablesValueCache ?? {};
  const variablesMap: VariablesMap = { ...base.variablesMap };
  const mediaVariableKinds: Record<string, MediaKind> = {};
  for (const { variable, kind } of declarations) {
    variablesMap[variable] = cache[variable] || "";
    mediaVariableKinds[variable] = kind;
  }
  return {
    variablesMap,
    variableKeys: Array.from(
      new Set([...base.variableKeys, ...declarations.map((d) => d.variable)])
    ),
    mediaVariableKeys: declarations.map((d) => d.variable),
    mediaVariableKinds,
  };
};

/**
 * The media variable values a run has to carry.
 *
 * The server substitutes a media reference out of the run's template variables like
 * any other value, so a media variable missing from that map means the model is sent
 * a prompt with the media slot unfilled and nothing reports a problem.
 */
export const withMediaVariableValues = (
  variablesMap: VariablesMap,
  {
    instances,
    input,
  }: {
    instances: PlaygroundInstance[];
    input: Pick<PlaygroundInput, "variablesValueCache">;
  }
): VariablesMap =>
  withMediaVariables({ variablesMap, variableKeys: [] }, { instances, input })
    .variablesMap;

/** A text content part as the chat-completion wire format names it. */
type TextContentPartInput = { text: { text: string } };

/** A media content part as the chat-completion wire format names it. */
export type MediaContentPartInput =
  | { image: { url: string; mediaType: string } }
  | { imageVariable: { variable: string } }
  | { file: { url: string; mediaType: string } }
  | { fileVariable: { variable: string } };

/**
 * A message's media as content parts, in the order the editor lays them out:
 * text first (added by the caller), then pictures, then papers.
 *
 * The wire format names the variable variants separately — `imageVariable` rather
 * than `image` — so the one-of input stays unambiguous between a stored reference
 * and a named one.
 */
export const mediaContentPartInputs = (message: {
  images?: { image: { url: string; mediaType: string } }[];
  imageVariables?: { image: { variable: string } }[];
  files?: { file: { url: string; mediaType: string } }[];
  fileVariables?: { file: { variable: string } }[];
}): MediaContentPartInput[] => [
  ...(message.images ?? []).map(({ image }) => ({
    image: { url: image.url, mediaType: image.mediaType },
  })),
  ...(message.imageVariables ?? []).map(({ image }) => ({
    imageVariable: { variable: image.variable },
  })),
  ...(message.files ?? []).map(({ file }) => ({
    file: { url: file.url, mediaType: file.mediaType },
  })),
  ...(message.fileVariables ?? []).map(({ file }) => ({
    fileVariable: { variable: file.variable },
  })),
];

/**
 * The media type a stored reference is replayed with.
 *
 * A span records an image as a reference and never a media type beside it, because
 * the type is derived from the bytes rather than declared — which is also why
 * `useIsRenderableImage` has to probe. A playground image part must still name one,
 * so a stored reference is replayed with a supported placeholder.
 *
 * Sound because the declared type is advisory for a stored reference: the server
 * replaces it with the type held against the stored bytes (see
 * `resolve_media_in_messages`) before any provider sees the request, and an image
 * tile renders no media type, so the placeholder is neither sent nor shown. Its only
 * job is to satisfy `ImageContentPart`, which rejects a type outside the supported
 * set. The one place it survives is a prompt saved out of a replayed span, where it
 * is recorded — and still overridden on every run.
 */
export const REPLAYED_STORED_IMAGE_MEDIA_TYPE = "image/png";

/**
 * How a message's text parts are rejoined into one editor's worth of text.
 *
 * A blank line between them, because a part boundary is a paragraph boundary in every
 * payload that uses more than one — a heading per section is the common shape. Shared
 * with the raw-request reader so a span replayed down either path reads the same.
 */
export const joinTextParts = (texts: readonly string[]): string | undefined =>
  texts.filter(Boolean).join("\n\n") || undefined;

/** What a message needs to carry for its recorded layout to be replayable. */
type LayoutMessage = {
  content?: string;
  contentLayout?: ContentLayoutPart[];
  images?: { image: { url: string; mediaType: string } }[];
  imageVariables?: { image: { variable: string } }[];
  files?: { file: { url: string; mediaType: string } }[];
  fileVariables?: { file: { variable: string } }[];
};

/**
 * The message's content parts in their recorded order, or null when that order no
 * longer describes the message.
 *
 * Null is the ordinary answer, not a failure: a message assembled in the playground or
 * loaded from a prompt has no recorded layout at all, and the flat form is then the
 * only form there is.
 *
 * Three things have to hold for a layout to still be usable, and each guards a way the
 * message can move on from what was recorded:
 *
 * * every attachment placed exactly once and in the order the message holds them, so a
 *   layout can never duplicate an image, drop one, or point past the end;
 * * no media *variables*, which name media arriving with the run rather than media the
 *   message holds — there is nothing for an index to point at;
 * * the layout's text, rejoined, still equal to the message's text. This is the one
 *   that matters in practice: the editor shows a message as a single field, so any edit
 *   to it invalidates positions recorded against what it used to say. Falling back then
 *   sends the edited text followed by the attachments, which is what the editor shows.
 */
/**
 * Walks the recorded layout once, placing each part in order.
 *
 * Split out of {@link placedContentParts} purely to keep each function's
 * branching below the linter's complexity ceiling — the two halves (walk the
 * layout; decide whether the walk's result is still usable) don't share any
 * control flow, so splitting them changes nothing about behavior.
 */
const layOutContentParts = (
  layout: ContentLayoutPart[],
  message: LayoutMessage
): {
  parts: (TextContentPartInput | MediaContentPartInput)[];
  texts: string[];
  placedImages: number;
  placedFiles: number;
} | null => {
  const parts: (TextContentPartInput | MediaContentPartInput)[] = [];
  const texts: string[] = [];
  let nextImage = 0;
  let nextFile = 0;
  for (const part of layout) {
    if ("text" in part) {
      texts.push(part.text);
      parts.push({ text: { text: part.text } });
      continue;
    }
    if ("image" in part) {
      const held = message.images?.[part.image]?.image;
      if (held == null || part.image !== nextImage++) {
        return null;
      }
      parts.push({ image: { url: held.url, mediaType: held.mediaType } });
      continue;
    }
    const held = message.files?.[part.file]?.file;
    if (held == null || part.file !== nextFile++) {
      return null;
    }
    parts.push({ file: { url: held.url, mediaType: held.mediaType } });
  }
  return { parts, texts, placedImages: nextImage, placedFiles: nextFile };
};

const placedContentParts = (
  message: LayoutMessage
): (TextContentPartInput | MediaContentPartInput)[] | null => {
  const layout = message.contentLayout;
  if (layout == null || layout.length === 0) {
    return null;
  }
  if (message.imageVariables?.length || message.fileVariables?.length) {
    return null;
  }
  const laidOut = layOutContentParts(layout, message);
  if (laidOut == null) {
    return null;
  }
  const { parts, texts, placedImages, placedFiles } = laidOut;
  if (
    placedImages !== (message.images?.length ?? 0) ||
    placedFiles !== (message.files?.length ?? 0)
  ) {
    return null;
  }
  return (joinTextParts(texts) ?? "") === (message.content ?? "")
    ? parts
    : null;
};

/**
 * A message's content parts as they should be sent.
 *
 * The editor lays a message out as one text field and a strip of attachments, and for a
 * message authored there that is also the order it goes out in — text, then pictures,
 * then papers. A message read back from a span is different: the request it records
 * interleaved them, captioning each attachment with the line above it, and sending it
 * flattened asks the model to re-pair four labels with four pictures it now meets in a
 * block. Where the recorded order survives, it is the order used.
 *
 * The already-built text parts are passed in rather than rebuilt so that the flat case
 * comes out exactly as the caller made it, and only the interleaved case is this
 * module's to construct.
 *
 * @param textContent The message's text parts as the caller built them.
 * @param message The message being sent.
 */
export const orderedMessageContent = <T extends object>(
  textContent: readonly T[],
  message: LayoutMessage
): (T | TextContentPartInput | MediaContentPartInput)[] =>
  placedContentParts(message) ?? [
    ...textContent,
    ...mediaContentPartInputs(message),
  ];

/**
 * The text and images a recorded message carried, ready to spread onto its replay.
 *
 * Images and, when a turn holds more than one, its text.
 *
 * Upstream takes `contents.find(type === "text")` — the *first* text part, the rest
 * discarded, with no warning and a TODO admitting it. That is fine until a payload puts
 * each section in its own part, which is what an AI-marking prompt does: question in
 * one, student answer in the next. The answer being graded was silently dropped and the
 * replay looked complete. Returning `content` here overrides upstream's value by spread
 * order — this object is spread immediately after its `content:` property — so the fix
 * needs no edit to upstream's expression.
 *
 * Left alone when a turn has one text part or none: one part joins to itself, and none
 * must stay `undefined` rather than becoming an empty string. So single-part messages
 * come out byte-identical to before.
 *
 * Documents are not read here. OpenInference has no content-part convention for them,
 * so a span never records one.
 *
 * An external `http(s)` image URL is skipped rather than carried. A chat completion
 * takes only stored references and inline data URLs — an external one is refused with
 * a BadRequest, on the grounds that resolving it would have Phoenix fetch a
 * user-supplied URL server-side — so carrying it would trade a missing image for a
 * run that cannot start.
 *
 * Returns an object to spread rather than an array so that a message with no usable
 * image is left exactly as it was, with no empty `images` on it.
 *
 * The order the parts arrived in is recorded alongside them. The editor cannot show it
 * — a message there is one text field and a strip of attachments — but the run can send
 * it, which is the half that decides what the model sees. See {@link
 * orderedMessageContent}.
 *
 * @param contents The message's `contents` from the span attributes, if it had any.
 */
export const spanMessageParts = (
  contents: SpanMessageContentPart[] | undefined
): {
  content?: string;
  images?: ImagePart[];
  contentLayout?: ContentLayoutPart[];
} => {
  const images: ImagePart[] = [];
  const texts: string[] = [];
  const contentLayout: ContentLayoutPart[] = [];
  for (const part of contents ?? []) {
    const text = part.message_content.text;
    if (part.message_content.type === "text" && typeof text === "string") {
      texts.push(text);
      contentLayout.push({ text });
    }
    const url = part.message_content.image?.image?.url;
    if (!url) {
      continue;
    }
    // Inline media is canonicalized rather than read: `canonicalDataUrl` rejects what
    // the server rejects — a data URL with no `base64` parameter, or a payload that will
    // not decode — and returns a URL whose header states the same type as the field.
    // Gated on the shared list for the same reason the raw-payload reader is: a type the
    // server refuses aborts the whole template conversion, so one unusable attachment
    // must cost only itself, not the run.
    const resolved = isHostedMediaUrl(url)
      ? { url, mediaType: REPLAYED_STORED_IMAGE_MEDIA_TYPE }
      : canonicalDataUrl(url);
    if (resolved == null || !isSupportedImageMediaType(resolved.mediaType)) {
      continue;
    }
    const image = makeImagePart(resolved.url, resolved.mediaType);
    if (image) {
      contentLayout.push({ image: images.length });
      images.push(image);
    }
  }
  return {
    // Only when upstream would have thrown text away.
    ...(texts.length > 1 ? { content: joinTextParts(texts) } : {}),
    // The layout is only ever consulted to place media, so a message with none has
    // nothing to say with it — and an empty one would only be another thing to keep
    // consistent with the text as it is edited.
    ...(images.length > 0 ? { images, contentLayout } : {}),
  };
};
