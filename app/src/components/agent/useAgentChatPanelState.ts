import { useCallback, useMemo } from "react";

import type { AgentModelSelection } from "@phoenix/agent/chat/buildAgentChatRequestBody";
import { useAgentContext } from "@phoenix/contexts/AgentContext";
import type { AgentState } from "@phoenix/store/agentStore";
import { DRAFT_SESSION_ID } from "@phoenix/store/agentStore";

import type { ModelMenuValue } from "../generative/ModelMenu";

export function buildAgentModel({
  model,
}: {
  model: ModelMenuValue;
}): AgentModelSelection {
  if (model.customProvider) {
    return {
      providerType: "custom",
      providerId: model.customProvider.id,
      modelName: model.modelName,
    };
  }

  const isOpenAIProvider =
    model.provider === "OPENAI" || model.provider === "AZURE_OPENAI";
  return {
    providerType: "builtin",
    provider: model.provider,
    modelName: model.modelName,
    ...(isOpenAIProvider && { openaiApiType: "responses" }),
  };
}

/**
 * Derives the chat request's model selection from the store's current default
 * model config. The chat transport reads this at request time so a model
 * change always applies to the next send, even when the runtime chat was
 * created by a since-unmounted surface (e.g. the draft that started the
 * session).
 */
export function selectAgentModel(
  state: Pick<AgentState, "defaultModelConfig"> &
    Partial<Pick<AgentState, "modelConfigBySessionId">>,
  sessionId?: string | null
): AgentModelSelection {
  const defaultModelConfig =
    sessionId && sessionId !== DRAFT_SESSION_ID
      ? (state.modelConfigBySessionId?.[sessionId] ?? state.defaultModelConfig)
      : state.defaultModelConfig;
  if (defaultModelConfig.customProvider) {
    return {
      providerType: "custom",
      providerId: defaultModelConfig.customProvider.id,
      modelName: defaultModelConfig.modelName ?? "",
    };
  }
  return {
    providerType: "builtin",
    provider: defaultModelConfig.provider,
    modelName: defaultModelConfig.modelName ?? "",
    ...((defaultModelConfig.provider === "OPENAI" ||
      defaultModelConfig.provider === "AZURE_OPENAI") && {
      openaiApiType:
        defaultModelConfig.openaiApiType === "CHAT_COMPLETIONS"
          ? "chat_completions"
          : "responses",
    }),
  };
}

/**
 * Encapsulates the non-visual state and side effects that drive
 * {@link AgentChatPanel}.
 *
 * Responsibilities:
 * - Derives the model menu value from the store
 */
export function useAgentChatPanelState(sessionId?: string | null) {
  const isOpen = useAgentContext((state) => state.isOpen);
  const setIsOpen = useAgentContext((state) => state.setIsOpen);
  const position = useAgentContext((state) => state.position);
  const setPosition = useAgentContext((state) => state.setPosition);
  const defaultModelConfig = useAgentContext(
    (state) => state.defaultModelConfig
  );
  const setDefaultModelConfig = useAgentContext(
    (state) => state.setDefaultModelConfig
  );
  const sessionModelConfig = useAgentContext((state) =>
    sessionId && sessionId !== DRAFT_SESSION_ID
      ? state.modelConfigBySessionId[sessionId]
      : undefined
  );
  const setSessionModelConfig = useAgentContext(
    (state) => state.setSessionModelConfig
  );
  const activeModelConfig = sessionModelConfig ?? defaultModelConfig;

  const menuValue: ModelMenuValue = useMemo(
    () => ({
      provider: activeModelConfig.provider,
      modelName: activeModelConfig.modelName ?? "",
      ...(activeModelConfig.customProvider && {
        customProvider: activeModelConfig.customProvider,
      }),
    }),
    [activeModelConfig]
  );

  const handleModelChange = useCallback(
    (model: ModelMenuValue) => {
      const nextConfig = {
        ...activeModelConfig,
        provider: model.provider,
        modelName: model.modelName,
        customProvider: model.customProvider ?? null,
      };
      if (sessionId && sessionId !== DRAFT_SESSION_ID) {
        setSessionModelConfig(sessionId, nextConfig);
      } else {
        setDefaultModelConfig(nextConfig);
      }
    },
    [activeModelConfig, sessionId, setDefaultModelConfig, setSessionModelConfig]
  );

  const closePanel = useCallback(() => {
    setIsOpen(false);
  }, [setIsOpen]);

  return {
    isOpen,
    position,
    menuValue,
    closePanel,
    setPosition,
    handleModelChange,
  };
}
