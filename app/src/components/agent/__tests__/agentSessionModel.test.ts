import { describe, expect, it } from "vitest";

import { getDefaultInvocationConfig } from "@phoenix/pages/playground/providerAdapters";

import { resolvePersistedAgentModel } from "../agentSessionModel";

const fallback = {
  provider: "ANTHROPIC" as const,
  modelName: "claude-opus-4-6",
  invocationParameters: getDefaultInvocationConfig("ANTHROPIC"),
};

describe("resolvePersistedAgentModel", () => {
  it("restores a built-in selection from the live catalog", () => {
    expect(
      resolvePersistedAgentModel({
        model: {
          __typename: "AgentBuiltinProviderModelSelection",
          provider: "OPENAI",
          modelName: "gpt-5.5",
          openaiApiType: "RESPONSES",
        },
        availableBuiltinModels: [{ provider: "OPENAI", modelName: "gpt-5.5" }],
        customProviders: [],
        fallback,
      })
    ).toMatchObject({
      provider: "OPENAI",
      modelName: "gpt-5.5",
      openaiApiType: "RESPONSES",
    });
  });

  it("restores a custom selection with its current provider identity", () => {
    expect(
      resolvePersistedAgentModel({
        model: {
          __typename: "AgentCustomProviderModelSelection",
          providerId: "provider-1",
          modelName: "custom-model",
        },
        availableBuiltinModels: [],
        customProviders: [
          {
            id: "provider-1",
            name: "Custom OpenAI",
            sdk: "OPENAI",
            modelNames: ["custom-model"],
          },
        ],
        fallback,
      })
    ).toMatchObject({
      provider: "OPENAI",
      modelName: "custom-model",
      customProvider: {
        id: "provider-1",
        name: "Custom OpenAI",
      },
    });
  });

  it("allows a custom provider without an advertised model catalog", () => {
    expect(
      resolvePersistedAgentModel({
        model: {
          __typename: "AgentCustomProviderModelSelection",
          providerId: "provider-1",
          modelName: "user-supplied-model",
        },
        availableBuiltinModels: [],
        customProviders: [
          {
            id: "provider-1",
            name: "Custom OpenAI",
            sdk: "OPENAI",
            modelNames: [],
          },
        ],
        fallback,
      })
    ).toMatchObject({
      modelName: "user-supplied-model",
      customProvider: { id: "provider-1" },
    });
  });

  it("falls back when the persisted selection is no longer in the catalog", () => {
    expect(
      resolvePersistedAgentModel({
        model: {
          __typename: "AgentBuiltinProviderModelSelection",
          provider: "OPENAI",
          modelName: "removed-model",
          openaiApiType: "RESPONSES",
        },
        availableBuiltinModels: [],
        customProviders: [],
        fallback,
      })
    ).toBe(fallback);
  });
});
