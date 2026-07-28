"""Batch model token-detail summaries by project and time range."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.sql.functions import coalesce
from strawberry.dataloader import DataLoader
from typing_extensions import TypeAlias

from phoenix.db import models
from phoenix.server.api.dataloaders.cost_summary_scope import (
    CostSummaryScope,
    filter_to_scope,
)
from phoenix.server.api.dataloaders.types import (
    CostBreakdown,
    SpanCostDetailSummaryEntry,
)
from phoenix.server.types import DbSessionFactory


@dataclass(frozen=True)
class GenerativeModelCostDetailSummaryKey:
    """Identifies one model's token details within a scope."""

    model_id: int
    scope: CostSummaryScope


CostDetailSummaryEntries: TypeAlias = list[SpanCostDetailSummaryEntry]


class SpanCostDetailSummaryEntriesByModelAndScopeDataLoader(
    DataLoader[GenerativeModelCostDetailSummaryKey, CostDetailSummaryEntries]
):
    """Loads model token details with one aggregate query per unique scope."""

    def __init__(self, db: DbSessionFactory) -> None:
        super().__init__(load_fn=self._load_fn)
        self._db = db

    async def _load_fn(
        self, keys: list[GenerativeModelCostDetailSummaryKey]
    ) -> list[CostDetailSummaryEntries]:
        model_ids_by_scope: defaultdict[CostSummaryScope, set[int]] = defaultdict(set)
        for key in keys:
            model_ids_by_scope[key.scope].add(key.model_id)

        summaries: defaultdict[tuple[CostSummaryScope, int], CostDetailSummaryEntries] = (
            defaultdict(list)
        )
        async with self._db.read() as session:
            for scope, model_ids in model_ids_by_scope.items():
                rows = await session.stream(
                    _build_cost_detail_summary_statement(model_ids=model_ids, scope=scope)
                )
                async for model_id, token_type, is_prompt, cost, tokens in rows:
                    summaries[(scope, model_id)].append(
                        SpanCostDetailSummaryEntry(
                            token_type=token_type,
                            is_prompt=is_prompt,
                            value=CostBreakdown(tokens=tokens, cost=cost),
                        )
                    )

        return [summaries[(key.scope, key.model_id)] for key in keys]


def _build_cost_detail_summary_statement(
    *,
    model_ids: set[int],
    scope: CostSummaryScope,
) -> Select[tuple[Optional[int], str, bool, Optional[float], Optional[float]]]:
    """Aggregate token counts and costs by model, token type, and prompt kind."""
    statement = (
        select(
            models.SpanCost.model_id,
            models.SpanCostDetail.token_type,
            models.SpanCostDetail.is_prompt,
            coalesce(func.sum(models.SpanCostDetail.cost), 0).label("cost"),
            coalesce(func.sum(models.SpanCostDetail.tokens), 0).label("tokens"),
        )
        .select_from(models.SpanCostDetail)
        .join(
            models.SpanCost,
            models.SpanCostDetail.span_cost_id == models.SpanCost.id,
        )
        .where(models.SpanCost.model_id.in_(model_ids))
        .group_by(
            models.SpanCost.model_id,
            models.SpanCostDetail.token_type,
            models.SpanCostDetail.is_prompt,
        )
    )
    return filter_to_scope(statement, scope)
