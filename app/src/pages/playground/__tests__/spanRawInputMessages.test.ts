/**
 * Replaying spans that recorded a raw provider request instead of message attributes.
 *
 * One case per provider family, because the leaf shapes are the whole difficulty, plus
 * the guards that decide when media may be grafted onto messages that did parse.
 */
import {
  rawSpanInputMessages,
  withRawSpanInputMedia,
} from "../spanRawInputMessages";

const PNG_BYTES_REPR = String.raw`b'\x89PNG\r\n\x1a\n'`;
const PNG_BASE64 = btoa("\x89PNG\r\n\x1a\n");
const PNG_DATA_URL = `data:image/png;base64,${PNG_BASE64}`;
const PDF_BASE64 = "JVBERi0xLjQK";

/** Span attributes carrying a raw request under `input.value`, as a span records it. */
const attributes = (payload: unknown) => ({
  input: { value: JSON.stringify(payload), mime_type: "application/json" },
});

describe("rawSpanInputMessages", () => {
  it("reads a Google request whose image is a stringified bytes repr", () => {
    const messages = rawSpanInputMessages(
      attributes({
        model: "gemini-2.5-flash",
        system_instruction: "You classify screenshots.",
        contents: [
          {
            role: "user",
            parts: [
              { text: "Classify this screenshot" },
              {
                inline_data: { mime_type: "image/png", data: PNG_BYTES_REPR },
              },
            ],
          },
        ],
      })
    );

    expect(messages).toEqual([
      expect.objectContaining({
        role: "system",
        content: "You classify screenshots.",
      }),
      expect.objectContaining({
        role: "user",
        content: "Classify this screenshot",
        images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
      }),
    ]);
  });

  it("reads an OpenAI request's image_url and file parts", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          { role: "system", content: "You are terse." },
          {
            role: "user",
            content: [
              { type: "text", text: "What is this?" },
              { type: "image_url", image_url: { url: PNG_DATA_URL } },
              {
                type: "file",
                file: {
                  filename: "spec.pdf",
                  file_data: `data:application/pdf;base64,${PDF_BASE64}`,
                },
              },
            ],
          },
        ],
      })
    );

    expect(messages?.[0]).toMatchObject({
      role: "system",
      content: "You are terse.",
    });
    expect(messages?.[1]).toMatchObject({
      role: "user",
      content: "What is this?",
      images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
      files: [
        {
          file: {
            url: `data:application/pdf;base64,${PDF_BASE64}`,
            mediaType: "application/pdf",
          },
        },
      ],
    });
  });

  it("reads an Anthropic request's base64 source and top-level system prompt", () => {
    const messages = rawSpanInputMessages(
      attributes({
        system: [{ type: "text", text: "Answer briefly." }],
        messages: [
          {
            role: "user",
            content: [
              {
                type: "image",
                source: {
                  type: "base64",
                  media_type: "image/png",
                  data: PNG_BASE64,
                },
              },
              { type: "text", text: "Describe it." },
            ],
          },
        ],
      })
    );

    expect(messages).toEqual([
      expect.objectContaining({ role: "system", content: "Answer briefly." }),
      expect.objectContaining({
        role: "user",
        content: "Describe it.",
        images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
      }),
    ]);
  });

  it("reads a Bedrock request's bare format name and nested bytes", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          {
            role: "user",
            content: [
              { text: "And this one" },
              { image: { format: "png", source: { bytes: PNG_BASE64 } } },
              {
                document: {
                  format: "pdf",
                  name: "spec",
                  source: { bytes: PDF_BASE64 },
                },
              },
            ],
          },
        ],
      })
    );

    expect(messages?.[0]).toMatchObject({
      role: "user",
      content: "And this one",
      images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
      files: [
        {
          file: {
            url: `data:application/pdf;base64,${PDF_BASE64}`,
            mediaType: "application/pdf",
          },
        },
      ],
    });
  });

  it("reads an OpenAI responses request, whose list is `input`", () => {
    const messages = rawSpanInputMessages(
      attributes({
        model: "gpt-4o",
        input: [
          {
            role: "system",
            content: "Answer in one sentence.",
            type: "message",
          },
          {
            role: "user",
            type: "message",
            content: [
              { type: "input_text", text: "Describe this image." },
              { type: "input_image", detail: "auto", image_url: PNG_DATA_URL },
            ],
          },
        ],
      })
    );

    expect(messages).toEqual([
      expect.objectContaining({
        role: "system",
        content: "Answer in one sentence.",
      }),
      expect.objectContaining({
        role: "user",
        content: "Describe this image.",
        images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
      }),
    ]);
  });

  it("drops responses-API items that are not messages", () => {
    const messages = rawSpanInputMessages(
      attributes({
        input: [
          { role: "user", content: "call the tool", type: "message" },
          {
            type: "function_call",
            name: "lookup",
            arguments: "{}",
            call_id: "c1",
          },
          { type: "function_call_output", call_id: "c1", output: "" },
        ],
      })
    );
    expect(messages).toHaveLength(1);
    expect(messages?.[0]).toMatchObject({
      role: "user",
      content: "call the tool",
    });
  });

  it("reads a bare string prompt as a single user message", () => {
    const messages = rawSpanInputMessages(attributes({ input: "just ask" }));
    expect(messages?.[0]).toMatchObject({ role: "user", content: "just ask" });
  });

  it("finds the Google system instruction inside the request config", () => {
    const messages = rawSpanInputMessages(
      attributes({
        model: "gemini-2.5-flash",
        contents: [{ role: "user", parts: [{ text: "Review this." }] }],
        // Where the SDK actually puts it, via `GenerateContentConfig`.
        config: { system_instruction: "You review documents.", tools: [] },
      })
    );

    expect(messages).toEqual([
      expect.objectContaining({
        role: "system",
        content: "You review documents.",
      }),
      expect.objectContaining({ role: "user", content: "Review this." }),
    ]);
  });

  it("maps a Gemini model turn onto the ai role", () => {
    const messages = rawSpanInputMessages(
      attributes({ contents: [{ role: "model", parts: [{ text: "ok" }] }] })
    );
    expect(messages?.[0]).toMatchObject({ role: "ai", content: "ok" });
  });

  it("leaves out media a run would refuse, keeping the text", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "still here" },
              // Fetching an external URL server-side is refused by design.
              { type: "image_url", image_url: { url: "https://x.test/a.png" } },
              // Not a media type any provider Phoenix supports will take.
              {
                type: "image_url",
                image_url: { url: "data:image/bmp;base64,QQ==" },
              },
            ],
          },
        ],
      })
    );
    expect(messages?.[0]).toEqual(
      expect.objectContaining({ role: "user", content: "still here" })
    );
    expect(messages?.[0]).not.toHaveProperty("images");
  });

  it("carries a stored reference straight through", () => {
    const url = `phoenix://media/${"c".repeat(64)}`;
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          {
            role: "user",
            content: [{ type: "image_url", image_url: { url } }],
          },
        ],
      })
    );
    expect(messages?.[0]).toMatchObject({
      images: [{ image: { url, mediaType: "image/png" } }],
    });
  });

  it("returns null when there is no request to read", () => {
    expect(rawSpanInputMessages({})).toBeNull();
    expect(rawSpanInputMessages(attributes({ model: "gpt-4o" }))).toBeNull();
    expect(rawSpanInputMessages({ input: { value: "not json" } })).toBeNull();
    expect(rawSpanInputMessages({ input: { value: "{}" } })).toBeNull();
  });
});

describe("withRawSpanInputMedia", () => {
  const parsed = [
    { id: 1, role: "user" as const, content: "What is this?" },
    { id: 2, role: "ai" as const, content: "A login screen" },
  ];
  const payload = attributes({
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: "What is this?" },
          { type: "image_url", image_url: { url: PNG_DATA_URL } },
        ],
      },
      { role: "assistant", content: "A login screen" },
    ],
  });

  it("grafts media onto messages that parsed without it", () => {
    const [user, ai] = withRawSpanInputMedia(parsed, payload) ?? [];
    expect(user).toMatchObject({
      content: "What is this?",
      images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
    });
    expect(ai).not.toHaveProperty("images");
  });

  it("leaves messages that already carry media alone", () => {
    const stored = `phoenix://media/${"d".repeat(64)}`;
    const withMedia = [
      {
        ...parsed[0],
        images: [{ image: { url: stored, mediaType: "image/jpeg" } }],
      },
      parsed[1],
    ];
    expect(withRawSpanInputMedia(withMedia, payload)).toBe(withMedia);
  });

  it("refuses to graft when the two recordings describe different conversations", () => {
    expect(withRawSpanInputMedia([parsed[0]], payload)).toEqual([parsed[0]]);
    expect(withRawSpanInputMedia([parsed[1], parsed[0]], payload)).toEqual([
      parsed[1],
      parsed[0],
    ]);
  });

  it("reads the whole conversation when upstream parsed no messages at all", () => {
    const [system, user] = withRawSpanInputMedia(null, payload) ?? [];
    expect(system).toMatchObject({ role: "user", content: "What is this?" });
    expect(system).toMatchObject({
      images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
    });
    expect(user).toMatchObject({ role: "ai", content: "A login screen" });
  });

  it("stays empty when there are neither messages nor a raw request", () => {
    expect(withRawSpanInputMedia(null, {})).toBeUndefined();
    expect(withRawSpanInputMedia(undefined, {})).toBeUndefined();
  });

  it("grafts a document even onto messages that already carry an image", () => {
    // OpenInference has no document content type, so a recorded image is no evidence
    // that the documents came through — they cannot have.
    const stored = `phoenix://media/${"e".repeat(64)}`;
    const withImage = [
      {
        ...parsed[0],
        images: [{ image: { url: stored, mediaType: "image/png" } }],
      },
      parsed[1],
    ];
    const withPdf = attributes({
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "What is this?" },
            {
              type: "file",
              file: { file_data: `data:application/pdf;base64,${PDF_BASE64}` },
            },
          ],
        },
        { role: "assistant", content: "A login screen" },
      ],
    });

    const [user] = withRawSpanInputMedia(withImage, withPdf) ?? [];
    // The recorded image is kept as recorded; the document is added beside it.
    expect(user).toMatchObject({
      images: [{ image: { url: stored, mediaType: "image/png" } }],
      files: [
        {
          file: {
            url: `data:application/pdf;base64,${PDF_BASE64}`,
            mediaType: "application/pdf",
          },
        },
      ],
    });
  });

  it("is a no-op when the raw request has no media either", () => {
    const textOnly = attributes({
      messages: [
        { role: "user", content: "What is this?" },
        { role: "assistant", content: "A login screen" },
      ],
    });
    expect(withRawSpanInputMedia(parsed, textOnly)).toBe(parsed);
  });
});

describe("rawSpanInputMessages: shapes found during review", () => {
  it("reads the responses API's `instructions` as the system prompt", () => {
    const messages = rawSpanInputMessages(
      attributes({
        model: "gpt-4o",
        instructions: "Answer in one sentence.",
        input: [{ role: "user", content: "hi", type: "message" }],
      })
    );
    expect(messages).toEqual([
      expect.objectContaining({
        role: "system",
        content: "Answer in one sentence.",
      }),
      expect.objectContaining({ role: "user", content: "hi" }),
    ]);
  });

  it("reads a payload recorded as a bare list of messages", () => {
    const messages = rawSpanInputMessages(
      attributes([
        { role: "system", content: "Be terse." },
        {
          role: "user",
          content: [
            { type: "text", text: "What is this?" },
            { type: "image_url", image_url: { url: PNG_DATA_URL } },
          ],
        },
      ])
    );
    expect(messages?.[0]).toMatchObject({
      role: "system",
      content: "Be terse.",
    });
    expect(messages?.[1]).toMatchObject({
      role: "user",
      images: [{ image: { url: PNG_DATA_URL, mediaType: "image/png" } }],
    });
  });

  it("keeps the id a tool result answers, so the turn is not an orphan", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          { role: "user", content: "weather?" },
          { role: "tool", tool_call_id: "call_1", content: "72F" },
        ],
      })
    );
    expect(messages?.[1]).toMatchObject({
      role: "tool",
      content: "72F",
      toolCallId: "call_1",
    });
  });

  it("reads Anthropic's tool_use_id under the same field", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [{ role: "user", content: "ok", tool_use_id: "toolu_1" }],
      })
    );
    expect(messages?.[0]).toMatchObject({ toolCallId: "toolu_1" });
  });

  it("normalizes image/jpg on the raw path too", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          {
            role: "user",
            content: [
              {
                type: "image_url",
                image_url: { url: "data:image/jpg;base64,QQ==" },
              },
            ],
          },
        ],
      })
    );
    expect(messages?.[0]).toMatchObject({
      images: [
        {
          image: { url: "data:image/jpg;base64,QQ==", mediaType: "image/jpeg" },
        },
      ],
    });
  });

  it("skips a base64 payload whose length cannot decode", () => {
    const messages = rawSpanInputMessages(
      attributes({
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "kept" },
              { inline_data: { mime_type: "image/png", data: "QQQ" } },
            ],
          },
        ],
      })
    );
    expect(messages?.[0]).toMatchObject({ content: "kept" });
    expect(messages?.[0]).not.toHaveProperty("images");
  });
});
