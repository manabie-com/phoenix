from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import strawberry
from openinference.semconv.trace import OpenInferenceLLMProviderValues
from strawberry.relay import Node, NodeID
from strawberry.relay.types import GlobalID
from strawberry.types import Info
from typing_extensions import TypeAlias, assert_never

from phoenix.db import models
from phoenix.server.api.context import Context
from phoenix.server.api.dataloaders.cost_summary_scope import CostSummaryScope
from phoenix.server.api.dataloaders.span_cost_detail_summary_entries_by_model_and_scope import (
    GenerativeModelCostDetailSummaryKey,
)
from phoenix.server.api.dataloaders.span_cost_summary_by_model_and_scope import (
    GenerativeModelCostSummaryKey,
)
from phoenix.server.api.exceptions import BadRequest
from phoenix.server.api.input_types.TimeRange import TimeRange
from phoenix.server.api.types.CostBreakdown import CostBreakdown
from phoenix.server.api.types.GenerativeProvider import GenerativeProviderKey
from phoenix.server.api.types.ModelInterface import ModelInterface
from phoenix.server.api.types.SpanCostDetailSummaryEntry import SpanCostDetailSummaryEntry
from phoenix.server.api.types.SpanCostSummary import SpanCostSummary
from phoenix.server.api.types.TokenPrice import TokenKind, TokenPrice


@strawberry.enum
class GenerativeModelKind(Enum):
    CUSTOM = "CUSTOM"
    BUILT_IN = "BUILT_IN"


ProjectId: TypeAlias = int
TimeRangeKey: TypeAlias = tuple[Optional[datetime], Optional[datetime]]
# The same scope costSummary resolves against, flattened into a hashable key.
# A project is always part of it; a single time bound may still be open.
CachedCostSummaryKey: TypeAlias = tuple[ProjectId, TimeRangeKey]


@strawberry.type
class GenerativeModel(Node, ModelInterface):
    id: NodeID[int]
    db_record: strawberry.Private[Optional[models.GenerativeModel]] = None
    cached_cost_summary: strawberry.Private[
        Optional[dict[CachedCostSummaryKey, SpanCostSummary]]
    ] = None

    def __post_init__(self) -> None:
        if self.db_record and self.id != self.db_record.id:
            raise ValueError("GenerativeModel ID mismatch")

    @strawberry.field
    async def name(self, info: Info[Context, None]) -> str:
        if self.db_record:
            val = self.db_record.name
        else:
            val = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.name),
            )
        return val

    @strawberry.field
    async def provider(self, info: Info[Context, None]) -> Optional[str]:
        if self.db_record:
            provider = self.db_record.provider
        else:
            provider = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.provider),
            )
        return provider or None

    @strawberry.field
    async def name_pattern(self, info: Info[Context, None]) -> str:
        if self.db_record:
            pattern = self.db_record.name_pattern.pattern
        else:
            name_pattern_obj = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.name_pattern),
            )
            pattern = name_pattern_obj.pattern
        assert isinstance(pattern, str)
        return pattern

    @strawberry.field
    async def kind(self, info: Info[Context, None]) -> GenerativeModelKind:
        if self.db_record:
            is_built_in = self.db_record.is_built_in
        else:
            is_built_in = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.is_built_in),
            )
        return GenerativeModelKind.BUILT_IN if is_built_in else GenerativeModelKind.CUSTOM

    @strawberry.field
    async def created_at(self, info: Info[Context, None]) -> datetime:
        if self.db_record:
            val = self.db_record.created_at
        else:
            val = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.created_at),
            )
        return val

    @strawberry.field
    async def updated_at(self, info: Info[Context, None]) -> datetime:
        if self.db_record:
            val = self.db_record.updated_at
        else:
            val = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.updated_at),
            )
        return val

    @strawberry.field
    async def provider_key(self, info: Info[Context, None]) -> Optional[GenerativeProviderKey]:
        if self.db_record:
            provider = self.db_record.provider
        else:
            provider = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.provider),
            )
        return _semconv_provider_to_gql_generative_provider_key(provider) if provider else None

    @strawberry.field
    async def start_time(self, info: Info[Context, None]) -> Optional[datetime]:
        if self.db_record:
            val = self.db_record.start_time
        else:
            val = await info.context.data_loaders.generative_model_fields.load(
                (self.id, models.GenerativeModel.start_time),
            )
        return val

    def add_cached_cost_summary(
        self, project_id: int, time_range: TimeRange, cost_summary: SpanCostSummary
    ) -> None:
        """Records a summary the caller already aggregated, under the scope it covers.

        The top-models resolvers compute every model's summary in the query that
        ranks them, so costSummary answers from here rather than asking its
        loader to aggregate the same rows again.
        """
        if self.cached_cost_summary is None:
            self.cached_cost_summary = {}
        cache_key = (project_id, (time_range.start, time_range.end))
        self.cached_cost_summary[cache_key] = cost_summary

    @strawberry.field
    async def token_prices(self, info: Info[Context, None]) -> list[TokenPrice]:
        costs = await info.context.data_loaders.token_prices_by_model.load(self.id)
        token_prices: list[TokenPrice] = []
        for cost in costs:
            token_prices.append(
                TokenPrice(
                    token_type=cost.token_type,
                    kind=TokenKind.PROMPT if cost.is_prompt else TokenKind.COMPLETION,
                    cost_per_million_tokens=cost.base_rate * 1_000_000,
                    cost_per_token=cost.base_rate,
                )
            )
        return token_prices

    @strawberry.field(
        description=(
            "This model's total cost within one project and time range. Both "
            "arguments are required: a summary is always measured over a stated "
            "scope rather than the model's whole history. The time range may "
            "leave one bound open, but not both."
        )
    )  # type: ignore
    async def cost_summary(
        self,
        info: Info[Context, None],
        project_id: GlobalID,
        time_range: TimeRange,
    ) -> SpanCostSummary:
        scope = _to_cost_summary_scope(project_id, time_range)

        if self.cached_cost_summary is not None:
            cache_key = (scope.project_id, (scope.start_time, scope.end_time))
            if cache_key in self.cached_cost_summary:
                return self.cached_cost_summary[cache_key]

        loader = info.context.data_loaders.span_cost_summary_by_model_and_scope
        summary = await loader.load(GenerativeModelCostSummaryKey(model_id=self.id, scope=scope))
        return SpanCostSummary(
            prompt=CostBreakdown(
                tokens=summary.prompt.tokens,
                cost=summary.prompt.cost,
            ),
            completion=CostBreakdown(
                tokens=summary.completion.tokens,
                cost=summary.completion.cost,
            ),
            total=CostBreakdown(
                tokens=summary.total.tokens,
                cost=summary.total.cost,
            ),
        )

    @strawberry.field(
        description=(
            "This model's cost and token counts within one project and time "
            "range, broken down by token type. Both arguments are required and "
            "carry the same meaning as on costSummary, so the two fields always "
            "describe the same scope."
        )
    )  # type: ignore
    async def cost_detail_summary_entries(
        self,
        info: Info[Context, None],
        project_id: GlobalID,
        time_range: TimeRange,
    ) -> list[SpanCostDetailSummaryEntry]:
        loader = info.context.data_loaders.span_cost_detail_summary_entries_by_model_and_scope
        summary = await loader.load(
            GenerativeModelCostDetailSummaryKey(
                model_id=self.id,
                scope=_to_cost_summary_scope(project_id, time_range),
            )
        )
        return [
            SpanCostDetailSummaryEntry(
                token_type=entry.token_type,
                is_prompt=entry.is_prompt,
                value=CostBreakdown(
                    tokens=entry.value.tokens,
                    cost=entry.value.cost,
                ),
            )
            for entry in summary
        ]

    @strawberry.field
    async def last_used_at(self, info: Info[Context, None]) -> Optional[datetime]:
        return await info.context.data_loaders.last_used_times_by_generative_model_id.load(self.id)


def _to_cost_summary_scope(project_id: GlobalID, time_range: TimeRange) -> CostSummaryScope:
    """Validates a field's scope arguments into the scope its loader batches on.

    Both cost fields resolve their arguments here so they cannot drift into
    disagreeing about what a scope means or which inputs it rejects.
    """
    if time_range.start is None and time_range.end is None:
        raise BadRequest(
            "A time range needs a start, an end, or both. "
            "Leaving both open would summarize the project's whole history."
        )
    return CostSummaryScope(
        project_id=_to_project_rowid(project_id),
        start_time=time_range.start,
        end_time=time_range.end,
    )


def _to_project_rowid(project_id: GlobalID) -> int:
    """Resolves a project's row ID.

    The type name is checked before the row ID is parsed, so an ID carrying a
    non-numeric payload is still reported as an invalid project rather than
    surfacing as an internal error.
    """
    if project_id.type_name != models.Project.__name__:
        raise BadRequest("Invalid Project ID")
    try:
        return int(project_id.node_id)
    except ValueError:
        raise BadRequest("Invalid Project ID")


def _semconv_provider_to_gql_generative_provider_key(
    semconv_provider_str: str,
) -> Optional[GenerativeProviderKey]:
    """
    Translates a semconv provider string to a GQL GenerativeProviderKey.
    """

    try:
        semconv_provider = OpenInferenceLLMProviderValues(semconv_provider_str)
    except Exception:
        return None
    if semconv_provider == OpenInferenceLLMProviderValues.OPENAI:
        return GenerativeProviderKey.OPENAI
    if semconv_provider == OpenInferenceLLMProviderValues.ANTHROPIC:
        return GenerativeProviderKey.ANTHROPIC
    if semconv_provider == OpenInferenceLLMProviderValues.AZURE:
        return GenerativeProviderKey.AZURE_OPENAI
    if semconv_provider == OpenInferenceLLMProviderValues.GOOGLE:
        return GenerativeProviderKey.GOOGLE
    if semconv_provider == OpenInferenceLLMProviderValues.DEEPSEEK:
        return GenerativeProviderKey.DEEPSEEK
    if semconv_provider == OpenInferenceLLMProviderValues.XAI:
        return GenerativeProviderKey.XAI
    if semconv_provider == OpenInferenceLLMProviderValues.AWS:
        return GenerativeProviderKey.AWS
    if semconv_provider == OpenInferenceLLMProviderValues.COHERE:
        return None  # TODO
    if semconv_provider == OpenInferenceLLMProviderValues.MISTRALAI:
        return None  # TODO
    if semconv_provider == OpenInferenceLLMProviderValues.GROQ:
        return GenerativeProviderKey.GROQ
    if semconv_provider == OpenInferenceLLMProviderValues.FIREWORKS:
        return GenerativeProviderKey.FIREWORKS
    if semconv_provider == OpenInferenceLLMProviderValues.MOONSHOT:
        return GenerativeProviderKey.MOONSHOT
    if semconv_provider == OpenInferenceLLMProviderValues.CEREBRAS:
        return GenerativeProviderKey.CEREBRAS
    if semconv_provider == OpenInferenceLLMProviderValues.PERPLEXITY:
        return GenerativeProviderKey.PERPLEXITY
    if semconv_provider == OpenInferenceLLMProviderValues.TOGETHER:
        return GenerativeProviderKey.TOGETHER
    if TYPE_CHECKING:
        assert_never(semconv_provider)
    else:
        return None
