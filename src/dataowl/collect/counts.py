"""Exact row count."""

from __future__ import annotations

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType
from dataowl.runner import SqlRunner

_SKIPPED_TYPES = frozenset({ObjectType.VIEW, ObjectType.MATERIALIZED_VIEW, ObjectType.FOREIGN})

NO_ROWS = "COUNT(*) returned no rows"


def collect_row_count(
    runner: SqlRunner,
    ref: TableRef,
    object_type: Fact[ObjectType],
    *,
    count_views: bool = False,
) -> Fact[int]:
    """Count rows with COUNT(*).

    Unless count_views is True, the count is skipped for views, materialized views and
    foreign tables, and when the object type is unavailable. UNKNOWN is counted.
    """
    if not count_views:
        if not object_type.available:
            return Fact.unavailable("exact", "Skipped: object type unknown; use count_views=True")
        if object_type.value in _SKIPPED_TYPES:
            assert object_type.value is not None
            return Fact.unavailable(
                "exact", f"Skipped for {object_type.value.value}; use count_views=True"
            )

    result = safe_query(runner, f"SELECT COUNT(*) AS n FROM {ref.quoted()}")
    if isinstance(result, Exception):
        return Fact.unavailable("exact", short_reason(result))
    if not result:
        return Fact.unavailable("exact", NO_ROWS)

    n = result[0].get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        return Fact.unavailable("exact", f"Unexpected COUNT(*) result type: {type(n).__name__}")
    return Fact(n, source="exact")
