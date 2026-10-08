"""Declared primary key from information_schema.table_constraints and key_column_usage."""

from __future__ import annotations

from typing import Any, TypeVar

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.runner import SqlRunner

T = TypeVar("T")

_QUERY = """
SELECT
  kcu.constraint_name AS constraint_name,
  kcu.column_name AS column_name
FROM {catalog}.information_schema.table_constraints AS tc
JOIN {catalog}.information_schema.key_column_usage AS kcu
  ON kcu.constraint_catalog = tc.constraint_catalog
  AND kcu.constraint_schema = tc.constraint_schema
  AND kcu.constraint_name = tc.constraint_name
WHERE tc.table_schema = :schema
  AND tc.table_name = :table
  AND tc.constraint_type = 'PRIMARY KEY'
ORDER BY kcu.ordinal_position
"""


def collect_primary_key(runner: SqlRunner, ref: TableRef) -> Fact[tuple[str, ...] | None]:
    """Read the declared primary key columns, in key order.

    The constraint is informational: Databricks does not enforce it. Column names are
    returned as information_schema stores them.

    No rows give an available fact with value None, since a table without a primary key is
    a fact and not an error. This differs on purpose from the NO_ROWS handling in other
    collect modules. Errors, an unexpected row format and rows from more than one
    constraint make the fact unavailable.
    """
    result = safe_query(
        runner,
        _QUERY.format(catalog=ref.quoted_catalog()),
        # Unity Catalog stores names in lower case. Lowering the parameters instead of
        # the columns keeps the WHERE clause free of functions on information_schema columns.
        {"schema": ref.schema.lower(), "table": ref.table.lower()},
    )
    if isinstance(result, Exception):
        return Fact.unavailable("metadata", short_reason(result))
    if not result:
        return Fact(None, source="metadata")

    try:
        rows = [
            (_get(row, "constraint_name", str), _get(row, "column_name", str)) for row in result
        ]
    except ValueError as exc:
        return Fact.unavailable(
            "metadata",
            f"Unexpected row format in information_schema.key_column_usage: {exc}",
        )

    constraint_names = {name for name, _ in rows}
    if len(constraint_names) > 1:
        return Fact.unavailable(
            "metadata",
            f"Primary key query returned columns from {len(constraint_names)} constraints",
        )
    return Fact(tuple(column for _, column in rows), source="metadata")


def _get(row: dict[str, Any], key: str, expected: type[T]) -> T:
    if key not in row:
        raise ValueError(f"missing {key}")
    value = row[key]
    if not isinstance(value, expected) or isinstance(value, bool):
        raise ValueError(f"{key} has type {type(value).__name__}")
    return value
