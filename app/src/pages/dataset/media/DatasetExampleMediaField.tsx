import { css } from "@emotion/react";
import { useCallback, useRef, useState } from "react";

import {
  Alert,
  Button,
  Flex,
  Icon,
  Icons,
  Input,
  Label,
  Text,
  TextField,
  View,
} from "@phoenix/components";
import { Attachments } from "@phoenix/components/ai/attachment";
import { useMediaStore } from "@phoenix/hooks/useMediaStore";
import {
  findExampleMedia,
  isValidExampleMediaKey,
  removeExampleMedia,
  setExampleMedia,
} from "@phoenix/utils/datasetExampleMediaUtils";

import { ExampleMediaAttachment } from "@phoenix/components/media/ExampleMediaAttachment";

/**
 * Attaches images and documents to a dataset example.
 *
 * A prompt fills a media slot from a top-level key in the example's input, exactly
 * as it fills a text variable — so an attachment is really just a key holding a
 * `phoenix://media/<sha256>` reference. The JSON editor above can express that
 * already; what it cannot do is produce a digest, which is why picking a file had
 * to happen somewhere. This is that somewhere, writing into the same JSON the
 * editor holds rather than into a channel of its own.
 *
 * The variable name is asked for first and deliberately: the name is what binds the
 * file to the prompt's slot. An attachment under the wrong key fills nothing, and
 * since an unfilled slot is now skipped rather than raised, that mistake would run
 * silently without the image.
 */

const hiddenFileInputCSS = css`
  display: none;
`;

/* Takes the slack on the row so the buttons keep their natural width. */
const grownFieldCSS = css`
  flex: 1;
  min-width: 0;
`;

/*
 * The attachment variants right-align themselves for a chat composer. Here they sit
 * under a form label, on the left.
 */
const previewCSS = css`
  & > [data-variant="grid"],
  & > [data-variant="inline"] {
    margin-left: 0;
  }
`;

/** Both kinds are offered at once: the server decides the type from the bytes. */
const ACCEPTED_TYPES = "image/*,application/pdf";

const UPLOAD_FAILED = "Could not upload that file.";
const IMPORT_FAILED = "Could not import that URL.";
const KEY_MISSING = "Name the variable this media fills first.";
const NOT_AN_OBJECT = "Fix the input JSON before attaching media.";

export type DatasetExampleMediaFieldProps = {
  /** The example's input, as the JSON editor holds it. */
  value: string;
  /** Receives the input JSON with the attachment added or removed. */
  onChange: (value: string) => void;
};

export function DatasetExampleMediaField({
  value,
  onChange,
}: DatasetExampleMediaFieldProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { isBusy, error, setError, upload, importUrl } = useMediaStore();
  const [variableName, setVariableName] = useState("");
  const [urlDraft, setUrlDraft] = useState("");

  const attached = findExampleMedia(value);

  /** Writes a stored reference into the example under the chosen name. */
  const attach = useCallback(
    (url: string) => {
      const updated = setExampleMedia(value, variableName.trim(), url);
      if (updated === null) {
        setError(NOT_AN_OBJECT);
        return;
      }
      onChange(updated);
      setVariableName("");
      setUrlDraft("");
    },
    [value, variableName, onChange, setError]
  );

  const guardName = useCallback(() => {
    if (!isValidExampleMediaKey(variableName)) {
      setError(KEY_MISSING);
      return false;
    }
    return true;
  }, [variableName, setError]);

  const onFilePicked = useCallback(
    async (file: File) => {
      const stored = await upload(file, UPLOAD_FAILED);
      if (stored) {
        attach(stored.url);
      }
    },
    [upload, attach]
  );

  const onImportUrl = useCallback(async () => {
    if (!guardName()) {
      return;
    }
    const trimmed = urlDraft.trim();
    if (!trimmed) {
      setError("Paste a URL to import.");
      return;
    }
    const stored = await importUrl(trimmed, IMPORT_FAILED);
    if (stored) {
      attach(stored.url);
    }
  }, [guardName, urlDraft, importUrl, attach, setError]);

  const detach = useCallback(
    (key: string) => {
      const updated = removeExampleMedia(value, key);
      if (updated === null) {
        setError(NOT_AN_OBJECT);
        return;
      }
      onChange(updated);
    },
    [value, onChange, setError]
  );

  return (
    <View paddingTop="size-100">
      <Flex direction="column" gap="size-100" width="100%">
        <Text weight="heavy" size="XS">
          Media
        </Text>
        <Text color="text-700" size="XS">
          Attach an image or PDF and name the prompt variable it fills. The
          reference is written into the input above.
        </Text>
        {error ? (
          <Alert
            variant="danger"
            banner
            dismissable
            onDismissClick={() => setError(null)}
          >
            {error}
          </Alert>
        ) : null}
        {attached.length > 0 ? (
          <View css={previewCSS}>
            <Attachments variant="grid" style={{ marginLeft: 0 }}>
              {attached.map(({ key, url }) => (
                <ExampleMediaAttachment
                  // Keyed by variable name: the value must follow its key if the
                  // list reorders, and a name is unique within one JSON object.
                  key={key}
                  mediaKey={key}
                  url={url}
                  onRemove={() => detach(key)}
                />
              ))}
            </Attachments>
          </View>
        ) : null}
        <Flex direction="row" gap="size-100" alignItems="end">
          <div css={grownFieldCSS}>
            <TextField
              value={variableName}
              onChange={setVariableName}
              isDisabled={isBusy}
            >
              <Label>Variable name</Label>
              <Input placeholder="e.g. question_image" />
            </TextField>
          </div>
          <Button
            size="S"
            leadingVisual={<Icon svg={<Icons.CloudUpload />} />}
            isDisabled={isBusy}
            onPress={() => {
              if (guardName()) {
                fileInputRef.current?.click();
              }
            }}
          >
            {isBusy ? "Working…" : "Upload"}
          </Button>
          <input
            ref={fileInputRef}
            css={hiddenFileInputCSS}
            type="file"
            accept={ACCEPTED_TYPES}
            aria-label="Choose media for this example"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void onFilePicked(file);
              }
              // Allow re-picking the same file after a removal.
              event.target.value = "";
            }}
          />
        </Flex>
        <Flex direction="row" gap="size-100" alignItems="center">
          <div css={grownFieldCSS}>
            <TextField
              value={urlDraft}
              onChange={setUrlDraft}
              isDisabled={isBusy}
              aria-label="Media URL to import"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void onImportUrl();
                }
              }}
            >
              <Input placeholder="…or paste an image or PDF URL" />
            </TextField>
          </div>
          <Button
            size="S"
            isDisabled={isBusy}
            onPress={() => void onImportUrl()}
          >
            Use URL
          </Button>
        </Flex>
      </Flex>
    </View>
  );
}
