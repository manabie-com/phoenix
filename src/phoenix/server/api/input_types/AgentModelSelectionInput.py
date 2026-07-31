from typing import Optional

import strawberry
from strawberry import UNSET
from strawberry.relay import GlobalID

from phoenix.db.types.model_provider import ModelProvider
from phoenix.server.agents.model_selection import (
    AgentModelSelection,
    BuiltInProviderModelSelection,
    CustomProviderModelSelection,
)
from phoenix.server.api.input_types.ModelClientOptionsInput import OpenAIApiType


@strawberry.input
class AgentCustomProviderModelSelectionInput:
    provider_id: GlobalID
    model_name: str

    def to_model_selection(self) -> CustomProviderModelSelection:
        return CustomProviderModelSelection(
            provider_type="custom",
            provider_id=str(self.provider_id),
            model_name=self.model_name,
        )


@strawberry.input
class AgentBuiltinProviderModelSelectionInput:
    provider: ModelProvider
    model_name: str
    openai_api_type: OpenAIApiType = OpenAIApiType.RESPONSES

    def to_model_selection(self) -> BuiltInProviderModelSelection:
        return BuiltInProviderModelSelection(
            provider_type="builtin",
            provider=self.provider,
            model_name=self.model_name,
            openai_api_type=self.openai_api_type.value,
        )


@strawberry.input(one_of=True)
class AgentModelSelectionInput:
    custom: Optional[AgentCustomProviderModelSelectionInput] = UNSET
    builtin: Optional[AgentBuiltinProviderModelSelectionInput] = UNSET

    def to_model_selection(self) -> AgentModelSelection:
        if self.custom is not UNSET and self.custom is not None:
            return self.custom.to_model_selection()
        if self.builtin is not UNSET and self.builtin is not None:
            return self.builtin.to_model_selection()
        raise ValueError("Exactly one model selection must be provided.")
