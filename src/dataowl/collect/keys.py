"""Key analysis: grain and uniqueness of user-selected columns and column combinations."""

from __future__ import annotations

from typing import Any, TypeAlias

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef, quote_column
from dataowl.model.analysis import KeyAnalysis
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo
from dataowl.runner import SqlRunner

KeyInput: TypeAlias = str | tuple[str, ...] | list[str | tuple[str, ...]]

NO_ROWS = "Key query returned no rows"
NO_NON_NULL_KEYS = "No non-null keys"

# Key columns get positional aliases, so that no user column name can collide with the
# internal names. Only the last SELECT returns data, and it holds only aggregates.
_QUERY = """
WITH base AS (
  SELECT
    {aliased},
    CASE WHEN {any_null} THEN 1 ELSE 0 END AS __dataowl_has_null
  FROM {table}
),
grouped AS (
  SELECT {aliases}, COUNT(*) AS __dataowl_rows
  FROM base
  WHERE __dataowl_has_null = 0
  GROUP BY {aliases}
)
SELECT
  (SELECT COUNT(*) FROM base) AS total_rows,
  (SELECT COALESCE(SUM(__dataowl_has_null), 0) FROM base) AS rows_with_null_key,
  COUNT(*) AS distinct_keys,
  COALESCE(SUM(CASE WHEN __dataowl_rows > 1 THEN 1 ELSE 0 END), 0) AS duplicate_keys,
  COALESCE(SUM(CASE WHEN __dataowl_rows > 1 THEN __dataowl_rows ELSE 0 END), 0)
    AS rows_in_duplicate_keys,
  COALESCE(MAX(__dataowl_rows), 0) AS max_rows_per_key,
  percentile(__dataowl_rows, 0.5) AS median_rows_per_key,
  COALESCE(SUM(CASE WHEN __dataowl_rows = 1 THEN 1 ELSE 0 END), 0) AS keys_with_1_row,
  COALESCE(SUM(CASE WHEN __dataowl_rows BETWEEN 2 AND 10 THEN 1 ELSE 0 END), 0)
    AS keys_with_2_10_rows,
  COALESCE(SUM(CASE WHEN __dataowl_rows BETWEEN 11 AND 100 THEN 1 ELSE 0 END), 0)
    AS keys_with_11_100_rows,
  COALESCE(SUM(CASE WHEN __dataowl_rows > 100 THEN 1 ELSE 0 END), 0)
    AS keys_with_over_100_rows
FROM grouped
"""


def normalize_keys(key: KeyInput, columns: tuple[ColumnInfo, ...]) -> tuple[tuple[str, ...], ...]:
    """Validate key and return one tuple of column names per key to analyze.

    A string is one single-column key, a tuple one composite key, and a list holds several
    keys of either kind. Names are matched case insensitively, and the result holds the names
    as they appear in columns, in input order.

    Raises ValueError for an unknown column, a MAP column, a tuple with fewer than two
    columns or with the same column twice, an empty list, the same key twice in a list, and
    elements that are neither a string nor a tuple of strings.
    """
    by_name = {column.name.lower(): column for column in columns}
    elements = key if isinstance(key, list) else [key]
    if not elements:
        raise ValueError("key is an empty list")

    result: list[tuple[str, ...]] = []
    for element in elements:
        normalized = _normalize_element(element, by_name)
        if normalized in result:
            raise ValueError(f"key {_describe(normalized)} is given more than once")
        result.append(normalized)
    return tuple(result)


def collect_key_analysis(
    runner: SqlRunner, ref: TableRef, key_columns: tuple[str, ...]
) -> KeyAnalysis:
    """Run one key query. key_columns must be validated by normalize_keys.

    An error or no rows make every fact unavailable. A value of unexpected type makes only
    that fact unavailable. Without non-null keys the median is unavailable.
    """
    result = safe_query(runner, _key_query(ref, key_columns))
    if isinstance(result, Exception):
        return _unavailable(key_columns, short_reason(result))
    if not result:
        return _unavailable(key_columns, NO_ROWS)

    row = result[0]
    distinct_keys = _count(row, "distinct_keys")
    if distinct_keys.available and distinct_keys.value == 0:
        median: Fact[float] = Fact.unavailable("exact", NO_NON_NULL_KEYS)
    else:
        median = _median(row)
    return KeyAnalysis(
        columns=key_columns,
        total_rows=_count(row, "total_rows"),
        rows_with_null_key=_count(row, "rows_with_null_key"),
        distinct_keys=distinct_keys,
        duplicate_keys=_count(row, "duplicate_keys"),
        rows_in_duplicate_keys=_count(row, "rows_in_duplicate_keys"),
        max_rows_per_key=_count(row, "max_rows_per_key"),
        median_rows_per_key=median,
        keys_with_1_row=_count(row, "keys_with_1_row"),
        keys_with_2_10_rows=_count(row, "keys_with_2_10_rows"),
        keys_with_11_100_rows=_count(row, "keys_with_11_100_rows"),
        keys_with_over_100_rows=_count(row, "keys_with_over_100_rows"),
    )


def collect_key_analyses(
    runner: SqlRunner, ref: TableRef, keys: tuple[tuple[str, ...], ...]
) -> tuple[KeyAnalysis, ...]:
    """Run one key query per key, in order."""
    return tuple(collect_key_analysis(runner, ref, key_columns) for key_columns in keys)


def _key_query(ref: TableRef, key_columns: tuple[str, ...]) -> str:
    quoted = [quote_column(name) for name in key_columns]
    aliases = [f"__dataowl_k{i}" for i in range(len(quoted))]
    return _QUERY.format(
        aliased=",\n    ".join(f"{q} AS {a}" for q, a in zip(quoted, aliases, strict=True)),
        any_null=" OR ".join(f"{q} IS NULL" for q in quoted),
        table=ref.quoted(),
        aliases=", ".join(aliases),
    )


def _normalize_element(element: object, by_name: dict[str, ColumnInfo]) -> tuple[str, ...]:
    if isinstance(element, str):
        return (_resolve(element, by_name),)
    if not isinstance(element, tuple):
        raise ValueError(
            f"key elements must be a column name or a tuple of column names, "
            f"got {type(element).__name__}"
        )
    if not all(isinstance(name, str) for name in element):
        raise ValueError(f"composite key {element!r} must contain only column names")
    if len(element) < 2:
        raise ValueError(f"composite key {element!r} must have at least two columns")
    names = tuple(_resolve(name, by_name) for name in element)
    seen: set[str] = set()
    for name in names:
        if name.lower() in seen:
            raise ValueError(f"composite key {element!r} has column {name!r} more than once")
        seen.add(name.lower())
    return names


def _resolve(name: str, by_name: dict[str, ColumnInfo]) -> str:
    column = by_name.get(name.lower())
    if column is None:
        raise ValueError(f"key column {name!r} does not exist in the table")
    if _is_map(column.data_type):
        raise ValueError(f"key column {column.name!r} has type MAP, which cannot be grouped")
    return column.name


def _is_map(data_type: str) -> bool:
    normalized = data_type.strip().lower()
    return normalized == "map" or normalized.startswith("map<")


def _describe(key_columns: tuple[str, ...]) -> str:
    return repr(key_columns[0]) if len(key_columns) == 1 else repr(key_columns)


def _count(row: dict[str, Any], name: str) -> Fact[int]:
    value = row.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        return Fact.unavailable(
            "exact", f"Unexpected {name} type in key query result: {type(value).__name__}"
        )
    return Fact(value, source="exact")


def _median(row: dict[str, Any]) -> Fact[float]:
    value = row.get("median_rows_per_key")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Fact.unavailable(
            "exact",
            f"Unexpected median_rows_per_key type in key query result: {type(value).__name__}",
        )
    return Fact(float(value), source="exact")


def _unavailable(key_columns: tuple[str, ...], reason: str) -> KeyAnalysis:
    return KeyAnalysis(
        columns=key_columns,
        total_rows=Fact.unavailable("exact", reason),
        rows_with_null_key=Fact.unavailable("exact", reason),
        distinct_keys=Fact.unavailable("exact", reason),
        duplicate_keys=Fact.unavailable("exact", reason),
        rows_in_duplicate_keys=Fact.unavailable("exact", reason),
        max_rows_per_key=Fact.unavailable("exact", reason),
        median_rows_per_key=Fact.unavailable("exact", reason),
        keys_with_1_row=Fact.unavailable("exact", reason),
        keys_with_2_10_rows=Fact.unavailable("exact", reason),
        keys_with_11_100_rows=Fact.unavailable("exact", reason),
        keys_with_over_100_rows=Fact.unavailable("exact", reason),
    )
