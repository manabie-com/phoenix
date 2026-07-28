"""Batch model cost summaries by project and time range."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.sql.functions import coalesce
from strawberry.dataloader import DataLoader

from phoenix.db import models
from phoenix.server.api.dataloaders.cost_summary_scope import (
    CostSummaryScope,
    filter_to_scope,
)
from phoenix.server.api.dataloaders.types import CostBreakdown, SpanCostSummary
from phoenix.server.types import DbSessionFactory


@dataclass(frozen=True)
class GenerativeModelCostSummaryKey:
    """Identifies one model's cost summary within a scope."""

    model_id: int
    scope: CostSummaryScope


class SpanCostSummaryByModelAndScopeDataLoader(
    DataLoader[GenerativeModelCostSummaryKey, SpanCostSummary]
):
    """Loads model cost summaries with one aggregate query per unique scope."""

    def __init__(self, db: DbSessionFactory) -> None:
        super().__init__(load_fn=self._load_fn)
        self._db = db

    async def _load_fn(self, keys: list[GenerativeModelCostSummaryKey]) -> list[SpanCostSummary]:
        model_ids_by_scope: defaultdict[CostSummaryScope, set[int]] = defaultdict(set)
        for key in keys:
            model_ids_by_scope[key.scope].add(key.model_id)

        summaries: dict[tuple[CostSummaryScope, int], SpanCostSummary] = {}
        async with self._db.read() as session:
            for scope, model_ids in model_ids_by_scope.items():
                rows = await session.stream(
                    _build_cost_summary_statement(model_ids=model_ids, scope=scope)
                )
                async for (
                    model_id,
                    prompt_cost,
                    completion_cost,
                    total_cost,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                ) in rows:
                    summaries[(scope, model_id)] = SpanCostSummary(
                        prompt=CostBreakdown(tokens=prompt_tokens, cost=prompt_cost),
                        completion=CostBreakdown(tokens=completion_tokens, cost=completion_cost),
                        total=CostBreakdown(tokens=total_tokens, cost=total_cost),
                    )

        # A model with no spans in the scope is a zeroed summary, not an error:
        # the caller asked what it cost there, and the answer is nothing.
        return [summaries.get((key.scope, key.model_id), SpanCostSummary()) for key in keys]


def _build_cost_summary_statement(
    *,
    model_ids: set[int],
    scope: CostSummaryScope,
) -> Select[
    tuple[
        Optional[int],
        Optional[float],
        Optional[float],
        Optional[float],
        Optional[float],
        Optional[float],
        Optional[float],
    ]
]:
    """Aggregate cost and token totals by model within one scope."""
    statement = (
        select(
            models.SpanCost.model_id,
            coalesce(func.sum(models.SpanCost.prompt_cost), 0).label("prompt_cost"),
            coalesce(func.sum(models.SpanCost.completion_cost), 0).label("completion_cost"),
            coalesce(func.sum(models.SpanCost.total_cost), 0).label("total_cost"),
            coalesce(func.sum(models.SpanCost.prompt_tokens), 0).label("prompt_tokens"),
            coalesce(func.sum(models.SpanCost.completion_tokens), 0).label("completion_tokens"),
            coalesce(func.sum(models.SpanCost.total_tokens), 0).label("total_tokens"),
        )
        .where(models.SpanCost.model_id.in_(model_ids))
        .group_by(models.SpanCost.model_id)
    )
    return filter_to_scope(statement, scope)
