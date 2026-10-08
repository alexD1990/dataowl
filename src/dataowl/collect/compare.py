"""Comparison of two user-selected time columns."""

from __future__ import annotations

from typing import Any

from dataowl.collect import safe_query, short_reason
from dataowl.collect.timestamps import resolve_time_column
from dataowl.identifiers import TableRef, quote_column
from dataowl.model.analysis import ComparisonAnalysis
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo
from dataowl.runner import SqlRunner

NO_ROWS = "Comparison query returned no rows"

# A comparison with null is never true, so rows with null in either column fall only in
# either_null.
_QUERY = """
SELECT
  COALESCE(SUM(CASE WHEN {second} > {first} THEN 1 ELSE 0 END), 0) AS second_after_first,
  COALESCE(SUM(CASE WHEN {second} = {first} THEN 1 ELSE 0 END), 0) AS second_equal_first,
  COALESCE(SUM(CASE WHEN {second} < {first} THEN 1 ELSE 0 END), 0) AS second_before_first,
  COALESCE(SUM(CASE WHEN {first} IS NULL OR {second} IS NULL THEN 1 ELSE 0 END), 0)
    AS either_null
FROM {table}
"""


def normalize_compare(
    compare: tuple[str, str], columns: tuple[ColumnInfo, ...]
) -> tuple[ColumnInfo, ColumnInfo]:
    """Validate compare and return (first, second) in input order.

    Raises ValueError if compare is not a tuple, does not have exactly two elements, has
    elements that are not strings or names the same column twice (case insensitive), and
    for every error from resolve_time_column.
    """
    if not isinstance(compare, tuple):
        raise ValueError(
            f"compare must be a tuple of two column names, got {type(compare).__name__}"
        )
    if len(compare) != 2:
        raise ValueError(f"compare must have exactly two columns, got {len(compare)}")
    for element in compare:
        if not isinstance(element, str):
            raise ValueError(f"compare elements must be column names, got {type(element).__name__}")
    first = resolve_time_column(compare[0], columns)
    second = resolve_time_column(compare[1], columns)
    if first.name.lower() == second.name.lower():
        raise ValueError(f"compare has column {first.name!r} twice")
    return first, second


def collect_comparison(
    runner: SqlRunner, ref: TableRef, first: ColumnInfo, second: ColumnInfo
) -> ComparisonAnalysis:
    """Run one query that compares second with first. Never raises.

    first and second must come from normalize_compare. The query has no casts: columns of
    different types are compared after Spark's implicit type coercion to their least common
    type, following the precedence DATE -> TIMESTAMP_NTZ -> TIMESTAMP. A DATE is compared
    as that date at 00:00:00. A TIMESTAMP_NTZ compared with a TIMESTAMP keeps its year,
    month, day and time fields and is read in the session time zone.

    An error or no rows make every fact unavailable. A value of unexpected type makes only
    that fact unavailable.
    """
    sql = _QUERY.format(
        first=quote_column(first.name), second=quote_column(second.name), table=ref.quoted()
    )
    result = safe_query(runner, sql)
    if isinstance(result, Exception):
        return _unavailable(first, second, short_reason(result))
    if not result:
        return _unavailable(first, second, NO_ROWS)

    row = result[0]
    return ComparisonAnalysis(
        first_column=first.name,
        second_column=second.name,
        first_data_type=first.data_type,
        second_data_type=second.data_type,
        second_after_first=_count(row, "second_after_first"),
        second_equal_first=_count(row, "second_equal_first"),
        second_before_first=_count(row, "second_before_first"),
        either_null=_count(row, "either_null"),
    )


def _count(row: dict[str, Any], name: str) -> Fact[int]:
    value = row.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        return Fact.unavailable(
            "exact", f"Unexpected {name} type in comparison query result: {type(value).__name__}"
        )
    return Fact(value, source="exact")


def _unavailable(first: ColumnInfo, second: ColumnInfo, reason: str) -> ComparisonAnalysis:
    return ComparisonAnalysis(
        first_column=first.name,
        second_column=second.name,
        first_data_type=first.data_type,
        second_data_type=second.data_type,
        second_after_first=Fact.unavailable("exact", reason),
        second_equal_first=Fact.unavailable("exact", reason),
        second_before_first=Fact.unavailable("exact", reason),
        either_null=Fact.unavailable("exact", reason),
    )
