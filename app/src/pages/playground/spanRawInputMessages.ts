/**
 * Reading a span's messages, and their media, out of the raw request it recorded.
 *
 * Replay reads `llm.input_messages`. Plenty of spans do not have it: an app that
 * records its call with `input.value` alone — a JSON dump of the provider request —
 * is instrumented enough for the trace UI and invisible to replay, which opens on the
 * default template and reports that it could not parse any messages. There is nothing
 * wrong with the parse; there is nothing there to parse.
 *
 * This reads the request payload instead. Its shape is the provider's own rather than
 * OpenInference's, so there is a branch per family — but only at the leaves, because
 * every provider agrees on the outline: a list of messages, each with a role and a
 * list of parts, each part either text or media.
 *
 * Two jobs, and the second is the one that matters more often:
 *
 * * {@link rawSpanInputMessages} — the messages, for a span that recorded none;
 * * {@link withRawSpanInputMedia} — the media only, for a span that recorded its
 *   messages as text and left its images behind. Instrumentation that drops media
 *   from `message.contents` while keeping the full payload in `input.value` is common,
 *   and the failure is silent: the messages replay, the picture does not.
 *
 * What is deliberately not read: tool calls and tool results, which replay already
 * takes from `message.tool_calls` when they are recorded, and which have as many
 * shapes again as media does. A raw-payload message comes back as its text and its
 * media.
 */
import {
  ChatRoleMap,
  DEFAULT_CHAT_ROLE,
} from "@phoenix/constants/generativeConstants";
import type { FilePart, ImagePart } from "@phoenix/schemas/mediaPartSchemas";
import type { ChatMessage } from "@phoenix/store/playground";
import { generateMessageId } from "@phoenix/store/playground";
import { isStringKeyedObject } from "@phoenix/typeUtils";
import { inlineMedia } from "@phoenix/utils/inlineMediaPayload";
import { makeFilePart, makeImagePart } from "@phoenix/utils/mediaParts";
import { isHostedMediaUrl } from "@phoenix/utils/mediaUtils";
import {
  mediaKindForType,
  normalizeMediaType,
} from "@phoenix/utils/supportedMediaTypes";

import { REPLAYED_STORED_IMAGE_MEDIA_TYPE } from "./playgroundMedia";
import { chatMessageRolesSchema } from "./schemas";

/** Media found on a request part, in the form a playground message holds it. */
type FoundMedia = { image: ImagePart } | { file: FilePart };

/** The media a message carried, in the order it was recorded. */
type MessageMediaLists = { images: ImagePart[]; files: FilePart[] };

const asRecord = (value: unknown): Record<string, unknown> | null =>
  isStringKeyedObject(value) ? value : null;

const asString = (value: unknown): string | null =>
  typeof value === "string" && value ? value : null;

/** The first present key, for payloads that differ only in snake vs camel case. */
const pick = (record: Record<string, unknown>, ...keys: string[]): unknown => {
  for (const key of keys) {
    if (record[key] !== undefined) {
      return record[key];
    }
  }
  return undefined;
};

/**
 * A media part built from a reference or payload, or null when it cannot be carried.
 *
 * Rejects what a run would reject anyway: an external `http(s)` URL, which a chat
 * completion refuses rather than fetch server-side, and a media type outside the
 * supported sets.
 *
 * A stored `phoenix://media/<sha256>` reference passes through and, when the payload
 * declared no type beside it, takes the same placeholder replay gives one — the type
 * held against the stored bytes is authoritative and is filled in when the run
 * resolves the reference. The cost is that a stored *document* recorded without a
 * declared type is replayed as an image; nothing on the client can tell them apart
 * without fetching the bytes.
 *
 * Inline media goes through `inlineMedia`, which returns the URL and its type as one
 * canonical pair; nothing here recombines them, because a pair assembled from two
 * sources is how the two came to disagree.
 */
const mediaPart = (
  declaredMediaType: string | null,
  payload: string
): FoundMedia | null => {
  const resolved = isHostedMediaUrl(payload)
    ? {
        url: payload,
        mediaType: normalizeMediaType(
          declaredMediaType ?? REPLAYED_STORED_IMAGE_MEDIA_TYPE
        ),
      }
    : inlineMedia(declaredMediaType ?? "", payload);
  if (resolved == null) {
    return null;
  }
  const { url, mediaType } = resolved;
  switch (mediaKindForType(mediaType)) {
    case "image": {
      const image = makeImagePart(url, mediaType);
      return image ? { image } : null;
    }
    case "file": {
      const file = makeFilePart(url, mediaType);
      return file ? { file } : null;
    }
    default:
      return null;
  }
};

/**
 * A media type built from a provider's bare format name, e.g. Bedrock's `"png"`.
 *
 * No aliasing here — `normalizeMediaType` turns `image/jpg` into `image/jpeg` for
 * every path, so this only has to join the two halves.
 */
const mediaTypeFromFormat = (
  format: unknown,
  prefix: string
): string | null => {
  const name = asString(format);
  return name == null ? null : `${prefix}/${name}`;
};

/**
 * The media on one request part, whichever provider wrote it.
 *
 * Ordered from the most specific shape to the most general so that a part matching
 * two readings is taken by the one that knows more about it.
 */
const readPartMedia = (part: Record<string, unknown>): FoundMedia | null => {
  // Google: `inline_data: {mime_type, data}`. The media type is declared, and the
  // payload is base64 or the repr of the SDK's `bytes`.
  const inlineData = asRecord(pick(part, "inline_data", "inlineData"));
  if (inlineData) {
    const payload = asString(pick(inlineData, "data"));
    if (payload) {
      return mediaPart(
        asString(pick(inlineData, "mime_type", "mimeType")),
        payload
      );
    }
  }

  // Anthropic: `source: {type: "base64", media_type, data}` or `{type: "url", url}`,
  // under a part typed `image` or `document`.
  const source = asRecord(pick(part, "source"));
  if (source) {
    const payload =
      asString(pick(source, "data")) ?? asString(pick(source, "url"));
    if (payload) {
      return mediaPart(
        asString(pick(source, "media_type", "mediaType")),
        payload
      );
    }
    // No `bytes` branch here: Bedrock keeps its bytes beside a bare `format` name one
    // level up, handled below. Reading them here would mean a payload with no declared
    // type, which `mediaPart` can do nothing with.
  }

  // OpenAI: `image_url: {url}`, or the same key holding the URL directly.
  const imageUrl = pick(part, "image_url", "imageUrl");
  const imageUrlValue =
    asString(imageUrl) ?? asString(pick(asRecord(imageUrl) ?? {}, "url"));
  if (imageUrlValue) {
    return mediaPart(null, imageUrlValue);
  }

  // OpenAI documents, in both of the shapes the SDKs use: the completions API nests it
  // as `file: {file_data, filename}`, while the responses API puts it on the part —
  // `{type: "input_file", filename, file_data}`, which is what this fork's own responses
  // builder emits (`playground_media/_openai.py`). Reading only the nested one dropped
  // every PDF from a responses request, including the ones Phoenix itself recorded.
  const fileData =
    asString(pick(part, "file_data", "fileData")) ??
    asString(pick(asRecord(pick(part, "file")) ?? {}, "file_data", "fileData"));
  if (fileData) {
    return mediaPart(null, fileData);
  }

  // Bedrock: `image: {format, source: {bytes}}` and `document: {format, source}`.
  for (const [key, prefix] of [
    ["image", "image"],
    ["document", "application"],
  ] as const) {
    const block = asRecord(pick(part, key));
    const blockSource = asRecord(pick(block ?? {}, "source"));
    const bytes = asString(pick(blockSource ?? {}, "bytes"));
    if (block && bytes) {
      return mediaPart(
        mediaTypeFromFormat(pick(block, "format"), prefix),
        bytes
      );
    }
    // OpenInference's own shape, in case the payload mirrors it: `image.image.url`.
    const nested = asRecord(pick(block ?? {}, "image"));
    const nestedUrl = asString(pick(nested ?? {}, "url"));
    if (nestedUrl) {
      return mediaPart(null, nestedUrl);
    }
  }

  return null;
};

/** Every string of text on a value, however the provider nested it. */
const readText = (value: unknown): string[] => {
  const text = asString(value);
  if (text) {
    return [text];
  }
  if (Array.isArray(value)) {
    return value.flatMap(readText);
  }
  const record = asRecord(value);
  if (record == null) {
    return [];
  }
  // `parts` is Google's wrapper for a content list; `text` is every provider's leaf.
  const parts = pick(record, "parts");
  if (parts !== undefined) {
    return readText(parts);
  }
  const leaf = asString(pick(record, "text"));
  return leaf ? [leaf] : [];
};

/** The text and media of one message's content, read part by part. */
const readContent = (
  content: unknown
): { text: string | undefined } & MessageMediaLists => {
  const parts = Array.isArray(content) ? content : [content];
  const texts: string[] = [];
  const media: MessageMediaLists = { images: [], files: [] };
  for (const part of parts) {
    const record = asRecord(part);
    if (record == null) {
      texts.push(...readText(part));
      continue;
    }
    const found = readPartMedia(record);
    if (found == null) {
      texts.push(...readText(record));
      continue;
    }
    if ("image" in found) {
      media.images.push(found.image);
    } else {
      media.files.push(found.file);
    }
  }
  const text = texts.filter(Boolean).join("\n\n");
  return { text: text || undefined, ...media };
};

/**
 * A role name from any provider, mapped the way replay maps a recorded one.
 *
 * Goes through `ChatRoleMap` so that the aliases stay in one place — it is what turns
 * Gemini's `model` and OpenAI's `developer` into roles the playground has — and
 * validates the result rather than asserting it, since `Object.entries` cannot know
 * the map's keys are roles.
 */
const chatRole = (role: unknown): ChatMessageRole => {
  const name = asString(role)?.toLowerCase();
  if (name == null) {
    return DEFAULT_CHAT_ROLE;
  }
  for (const [candidate, aliases] of Object.entries(ChatRoleMap)) {
    if (!aliases.includes(name)) {
      continue;
    }
    const parsed = chatMessageRolesSchema.safeParse(candidate);
    if (parsed.success) {
      return parsed.data;
    }
  }
  return DEFAULT_CHAT_ROLE;
};

/**
 * A playground message, with the fields it has nothing for left off.
 *
 * `toolCallId` is carried when the payload names one, so a recovered tool result still
 * says which call it answers. Without it the turn replays as an orphan, and the
 * playground sends a tool message the provider cannot match.
 */
const chatMessage = (
  role: ChatMessageRole,
  { text, images, files }: { text: string | undefined } & MessageMediaLists,
  toolCallId?: string | null
): ChatMessage => ({
  id: generateMessageId(),
  role,
  content: text,
  ...(images.length > 0 ? { images } : {}),
  ...(files.length > 0 ? { files } : {}),
  ...(toolCallId ? { toolCallId } : {}),
});

/**
 * The system prompt a payload carries outside its message list.
 *
 * Anthropic and Bedrock take it as `system`, Google as `system_instruction`. Only
 * OpenAI puts it in the list, where it needs no special handling.
 *
 * Looked for inside the request config as well as beside the messages, because the
 * Google SDK puts it there — `config.system_instruction`, from the
 * `GenerateContentConfig` the caller passes — and never at the top level. Missing
 * that cost more than the prompt: a payload one message short of the recording it is
 * compared against fails the alignment check in {@link withRawSpanInputMedia}, so the
 * media was refused too.
 */
const systemMessage = (
  payload: Record<string, unknown>
): ChatMessage | null => {
  const containers = [
    payload,
    asRecord(pick(payload, "config", "generation_config", "generationConfig")),
  ];
  for (const container of containers) {
    if (container == null) {
      continue;
    }
    const instruction = pick(
      container,
      "system",
      "system_instruction",
      "systemInstruction",
      // The responses API's name for it, alongside the `input` list.
      "instructions"
    );
    if (instruction === undefined) {
      continue;
    }
    const content = readContent(instruction);
    if (content.text) {
      return chatMessage("system", content);
    }
  }
  return null;
};

/** The message list a payload holds, whatever the provider calls it. */
const messageList = (
  payload: Record<string, unknown>
): Record<string, unknown>[] | null => {
  // `messages` for OpenAI's completions API, Anthropic and Bedrock; `contents` for
  // Google; `input` for OpenAI's responses API, which is what the playground itself
  // calls by default.
  const messages = pick(payload, "messages", "contents", "input");
  if (Array.isArray(messages)) {
    return messages.map((message) => asRecord(message) ?? {});
  }
  const single = asRecord(messages);
  if (single) {
    return [single];
  }
  // The responses API also takes a bare string as the whole prompt.
  const bare = asString(messages);
  return bare ? [{ role: "user", content: bare }] : null;
};

/** The request payload a span recorded under `input.value`, parsed. */
const rawInputPayload = (
  parsedAttributes: unknown
): Record<string, unknown> | null => {
  const input = asRecord(pick(asRecord(parsedAttributes) ?? {}, "input"));
  const value = asString(pick(input ?? {}, "value"));
  if (value == null) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return null;
  }
  // Some instrumentation records the message list alone rather than the whole request.
  // Wrapping it lets the same reader handle both without a second code path.
  return Array.isArray(parsed) ? { messages: parsed } : asRecord(parsed);
};

/** The messages a payload describes, system prompt first. */
const payloadMessages = (
  payload: Record<string, unknown>
): ChatMessage[] | null => {
  const messages = messageList(payload);
  if (messages == null) {
    return null;
  }
  const built = messages
    .map((message) => ({
      role: chatRole(pick(message, "role")),
      content: readContent(pick(message, "content", "parts")),
      // OpenAI and Bedrock say `tool_call_id`; Anthropic says `tool_use_id`.
      toolCallId: asString(
        pick(message, "tool_call_id", "toolCallId", "tool_use_id")
      ),
    }))
    // A responses-API list holds items that are not messages at all — a
    // `function_call` or its output — and they read as a message with nothing in it.
    // Dropping those keeps replay from opening on a column of blank turns.
    .filter(
      ({ content }) =>
        content.text != null ||
        content.images.length > 0 ||
        content.files.length > 0
    )
    .map(({ role, content, toolCallId }) =>
      chatMessage(role, content, toolCallId)
    );
  const system = systemMessage(payload);
  return system ? [system, ...built] : built;
};

/**
 * The messages a span recorded as a raw provider request, or null when it recorded
 * none — in which case replay's own parsing error is the right thing to report.
 *
 * @param parsedAttributes The JSON-parsed span attributes.
 */
export function rawSpanInputMessages(
  parsedAttributes: unknown
): ChatMessage[] | null {
  const payload = rawInputPayload(parsedAttributes);
  if (payload == null) {
    return null;
  }
  const messages = payloadMessages(payload);
  return messages && messages.length > 0 ? messages : null;
}

/**
 * The same messages, with media taken from the raw request when they carry none.
 *
 * Applied only when the raw payload describes exactly the same conversation — same
 * number of messages, same roles in the same order — because the two lists are then
 * two recordings of one request and matching them by position is sound. Anything else
 * (a payload with its system prompt outside the list, a truncated recording, a
 * different call entirely) fails that check and is left alone, which is the safe
 * outcome: a missing image, not one attached to the wrong message.
 *
 * Pictures and documents are decided separately, and for opposite reasons. An image
 * that the recording already carries is left alone, because instrumentation that
 * records images properly should not be second-guessed. A document is grafted even
 * then: OpenInference has no document content type, so a PDF is *never* in
 * `message.contents` — the playground writes it as a line of descriptive text (see
 * `playground_media/_tracing.py`) — and treating one recorded image as proof the
 * documents are handled too would lose every one of them.
 *
 * When there are no messages at all — upstream found no `llm.input_messages` — the
 * whole conversation is read from the raw request instead. Both recoveries live behind
 * one call so upstream's parser needs no branch of its own and keeps reporting exactly
 * what it could not read; {@link spanInputParsingErrors} decides whether that report
 * still deserves to be shown.
 *
 * @param messages The messages replay parsed from `llm.input_messages`, if any.
 * @param parsedAttributes The JSON-parsed span attributes.
 */
export function withRawSpanInputMedia(
  messages: ChatMessage[] | null | undefined,
  parsedAttributes: unknown
): ChatMessage[] | undefined {
  if (messages == null) {
    return rawSpanInputMessages(parsedAttributes) ?? undefined;
  }
  const carriesImages = messages.some(
    (message) =>
      (message.images?.length ?? 0) > 0 ||
      (message.imageVariables?.length ?? 0) > 0
  );
  const carriesFiles = messages.some(
    (message) =>
      (message.files?.length ?? 0) > 0 ||
      (message.fileVariables?.length ?? 0) > 0
  );
  if (carriesImages && carriesFiles) {
    return messages;
  }
  const fromPayload = rawSpanInputMessages(parsedAttributes);
  if (
    fromPayload == null ||
    fromPayload.length !== messages.length ||
    fromPayload.some((message, index) => message.role !== messages[index].role)
  ) {
    return messages;
  }
  const graftImages =
    !carriesImages &&
    fromPayload.some((message) => (message.images?.length ?? 0) > 0);
  const graftFiles =
    !carriesFiles &&
    fromPayload.some((message) => (message.files?.length ?? 0) > 0);
  if (!graftImages && !graftFiles) {
    return messages;
  }
  return messages.map((message, index) => {
    const { images, files } = fromPayload[index];
    return {
      ...message,
      ...(graftImages && images?.length ? { images } : {}),
      ...(graftFiles && files?.length ? { files } : {}),
    };
  });
}
