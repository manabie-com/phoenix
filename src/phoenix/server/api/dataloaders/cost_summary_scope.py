"""The project and time window a model's cost figures are measured over."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, TypeVar

from sqlalchemy import Select

from phoenix.db import models

_StatementT = TypeVar("_StatementT", bound=Select[Any])


@dataclass(frozen=True)
class CostSummaryScope:
    """The project and time window a model's cost figures are measured over.

    Scoping is mandatory rather than a convenience: a summary always belongs to
    one project, and at least one time bound is always set, so no caller can ask
    a loader to scan every project's whole history. A single bound may be left
    open -- a start alone reads as "since", an end alone as "until" -- which is
    why the two bounds are individually optional but not collectively so.

    Both cost loaders batch on this scope, answering every key that shares one
    with a single query, so it is frozen and compared by value.
    """

    project_id: int
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.start_time is None and self.end_time is None:
            raise ValueError("A cost summary scope needs at least one time bound")


def filter_to_scope(statement: _StatementT, scope: CostSummaryScope) -> _StatementT:
    """Narrows a `SpanCost` aggregate to one project and time window.

    The time bounds read against `SpanCost.span_start_time` -- the same column
    the top-models queries filter on -- so a scoped summary covers exactly the
    spans that put a model in the chart to begin with.
    """
    statement = statement.join(
        models.Trace,
        models.SpanCost.trace_rowid == models.Trace.id,
    ).where(models.Trace.project_rowid == scope.project_id)
    if scope.start_time is not None:
        statement = statement.where(models.SpanCost.span_start_time >= scope.start_time)
    if scope.end_time is not None:
        statement = statement.where(models.SpanCost.span_start_time < scope.end_time)
    return statement
