import { getDefaultInvocationConfig } from "@phoenix/pages/playground/providerAdapters";
import type { ModelConfig } from "@phoenix/store/playground/types";

import { getProviderKeyForGenerativeModelSDK } from "../generative/modelProviderUtils";
import type {
  AvailableBuiltinModel,
  CustomProviderInfo,
} from "../generative/useModelMenuData";

export type PersistedAgentModel =
  | {
      readonly __typename: "AgentBuiltinProviderModelSelection";
      readonly provider: ModelProvider;
      readonly modelName: string;
      readonly openaiApiType: OpenAIApiType;
    }
  | {
      readonly __typename: "AgentCustomProviderModelSelection";
      readonly providerId: string;
      readonly modelName: string;
    };

export function resolvePersistedAgentModel({
  model,
  availableBuiltinModels,
  customProviders,
  fallback,
}: {
  model: PersistedAgentModel;
  availableBuiltinModels: readonly AvailableBuiltinModel[];
  customProviders: readonly CustomProviderInfo[];
  fallback: ModelConfig;
}): ModelConfig {
  if (model.__typename === "AgentCustomProviderModelSelection") {
    const customProvider = customProviders.find(
      (provider) =>
        provider.id === model.providerId &&
        (provider.modelNames.length === 0 ||
          provider.modelNames.includes(model.modelName))
    );
    if (!customProvider) {
      return fallback;
    }
    const provider = getProviderKeyForGenerativeModelSDK(customProvider.sdk);
    return {
      provider,
      modelName: model.modelName,
      customProvider: {
        id: customProvider.id,
        name: customProvider.name,
      },
      invocationParameters: getDefaultInvocationConfig(provider),
    };
  }
  const isAvailable = availableBuiltinModels.some(
    (candidate) =>
      candidate.provider === model.provider &&
      candidate.modelName === model.modelName
  );
  if (!isAvailable) {
    return fallback;
  }
  return {
    provider: model.provider,
    modelName: model.modelName,
    openaiApiType: model.openaiApiType,
    invocationParameters: getDefaultInvocationConfig(model.provider),
  };
}
