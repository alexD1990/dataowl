"""Time span and cadence of user-selected timestamp and date columns."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef, quote_column
from dataowl.model.analysis import DayCount, TimestampAnalysis
from dataowl.model.facts import Fact, derive
from dataowl.model.overview import ColumnInfo
from dataowl.runner import SqlRunner

SUPPORTED_TYPES = ("timestamp", "timestamp_ntz", "date")

NO_AGGREGATE_ROWS = "Timestamp aggregate query returned no rows"
NO_GAP_ROWS = "Gap query returned no rows"
FEWER_THAN_TWO_DATES = "Fewer than two distinct dates"
NO_NON_NULL_VALUES = "No non-null values"
NO_ROWS = "No rows"

_AGGREGATE_QUERY = """
SELECT
  COUNT(*) AS total_rows,
  COALESCE(SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END), 0) AS null_rows,
  MIN({column}) AS min_value,
  MAX({column}) AS max_value,
  COALESCE(SUM(CASE WHEN {column} > current_timestamp() THEN 1 ELSE 0 END), 0)
    AS future_values,
  COUNT(DISTINCT CAST({column} AS DATE)) AS distinct_dates,
  current_date() AS today
FROM {table}
"""

# Internal names have the __dataowl_ prefix, so that no user column name can collide with
# them. Only the last SELECT returns data, and it holds only aggregates.
_GAP_QUERY = """
WITH d AS (
  SELECT DISTINCT CAST({column} AS DATE) AS __dataowl_dt
  FROM {table}
  WHERE {column} IS NOT NULL
),
g AS (
  SELECT datediff(__dataowl_dt, LAG(__dataowl_dt) OVER (ORDER BY __dataowl_dt))
    AS __dataowl_gap_days
  FROM d
)
SELECT
  MIN(__dataowl_gap_days) AS min_gap_days,
  percentile(__dataowl_gap_days, 0.5) AS median_gap_days,
  AVG(__dataowl_gap_days) AS mean_gap_days,
  MAX(__dataowl_gap_days) AS max_gap_days
FROM g
WHERE __dataowl_gap_days IS NOT NULL
"""

# Groups on the expression, not on an alias, so that a user column named like the alias
# cannot be picked up. The window is the last :days whole days before :today.
_DAY_QUERY = """
SELECT
  CAST({column} AS DATE) AS __dataowl_day,
  COUNT(*) AS __dataowl_rows
FROM {table}
WHERE {column} >= date_sub(:today, :days)
  AND {column} < :today
GROUP BY CAST({column} AS DATE)
"""


def resolve_time_column(name: str, columns: tuple[ColumnInfo, ...]) -> ColumnInfo:
    """Find the column case insensitively and check that its type is supported.

    Raises ValueError for an unknown column and for a type other than timestamp,
    timestamp_ntz and date.
    """
    by_name = {column.name.lower(): column for column in columns}
    column = by_name.get(name.lower())
    if column is None:
        raise ValueError(f"timestamp column {name!r} does not exist in the table")
    data_type = column.data_type.strip().lower()
    if data_type not in SUPPORTED_TYPES:
        raise ValueError(
            f"timestamp column {column.name!r} has type {data_type}; "
            "supported types are timestamp, timestamp_ntz and date"
        )
    return column


def normalize_timestamp_columns(
    timestamp: str | list[str], columns: tuple[ColumnInfo, ...]
) -> tuple[ColumnInfo, ...]:
    """Validate timestamp and return the columns to analyze, in input order.

    A string is one column and a list holds several. Raises ValueError for an empty list,
    the same column twice (case insensitive), elements that are not strings, and every
    error from resolve_time_column.
    """
    elements: list[object] = list(timestamp) if isinstance(timestamp, list) else [timestamp]
    if not elements:
        raise ValueError("timestamp is an empty list")

    result: list[ColumnInfo] = []
    for element in elements:
        if not isinstance(element, str):
            raise ValueError(
                f"timestamp elements must be column names, got {type(element).__name__}"
            )
        column = resolve_time_column(element, columns)
        if any(existing.name.lower() == column.name.lower() for existing in result):
            raise ValueError(f"timestamp column {column.name!r} is given more than once")
        result.append(column)
    return tuple(result)


def validate_days(days: int) -> int:
    """Return days. Raises ValueError if it is not an int, is a bool or is less than 1."""
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError(f"days must be an integer, got {type(days).__name__}")
    if days < 1:
        raise ValueError(f"days must be at least 1, got {days}")
    return days


def fill_days(counts: dict[date, int], first_day: date, last_day: date) -> tuple[DayCount, ...]:
    """One DayCount per day from first_day to last_day inclusive, with 0 for missing days."""
    num_days = (last_day - first_day).days + 1
    return tuple(
        DayCount(day, counts.get(day, 0))
        for day in (first_day + timedelta(days=offset) for offset in range(num_days))
    )


def collect_timestamp_analysis(
    runner: SqlRunner, ref: TableRef, column: ColumnInfo, days: int
) -> TimestampAnalysis:
    """Run up to three queries for one column. Never raises.

    column must come from resolve_time_column and days from validate_days.

    Query 1 gives the aggregates and the session's current date. If it fails, its facts,
    the window and the per-day facts are unavailable, and query 3 is not run. Query 2 (gaps)
    is skipped when there are fewer than two distinct dates. The window is computed from the
    current date and does not depend on query 3. Query 3 counts rows per day in the window;
    if it fails, only rows_per_day and the per-day statistics are unavailable. A value of
    unexpected type makes only that fact unavailable, except in query 3, where any
    unexpected row makes rows_per_day and the per-day statistics unavailable.
    """
    quoted_column = quote_column(column.name)
    table = ref.quoted()

    aggregates = _aggregates(runner, quoted_column, table, column.data_type)

    distinct_dates = aggregates.distinct_dates
    if distinct_dates.available and distinct_dates.value is not None and distinct_dates.value < 2:
        gaps = _Gaps.unavailable(FEWER_THAN_TWO_DATES)
    else:
        gaps = _gaps(runner, quoted_column, table)

    today = aggregates.today
    if not today.available or today.value is None:
        assert today.reason is not None  # guaranteed by Fact.__post_init__
        window = _Window.unavailable(today.reason)
    else:
        window = _window(runner, quoted_column, table, today.value, days)

    rows_per_day = window.rows_per_day
    return TimestampAnalysis(
        column=column.name,
        data_type=column.data_type,
        total_rows=aggregates.total_rows,
        null_rows=aggregates.null_rows,
        null_share=_null_share(aggregates.null_rows, aggregates.total_rows),
        min_value=aggregates.min_value,
        max_value=aggregates.max_value,
        future_values=aggregates.future_values,
        distinct_dates=aggregates.distinct_dates,
        min_gap_days=gaps.min_gap_days,
        median_gap_days=gaps.median_gap_days,
        mean_gap_days=gaps.mean_gap_days,
        max_gap_days=gaps.max_gap_days,
        days=days,
        window_first_day=window.first_day,
        window_last_day=window.last_day,
        rows_per_day=rows_per_day,
        rows_per_day_min=derive(lambda counts: min(c.rows for c in counts), rows_per_day),
        rows_per_day_median=derive(
            lambda counts: float(statistics.median(c.rows for c in counts)), rows_per_day
        ),
        rows_per_day_mean=derive(
            lambda counts: sum(c.rows for c in counts) / len(counts), rows_per_day
        ),
        rows_per_day_max=derive(lambda counts: max(c.rows for c in counts), rows_per_day),
    )


def collect_timestamp_analyses(
    runner: SqlRunner, ref: TableRef, columns: tuple[ColumnInfo, ...], days: int
) -> tuple[TimestampAnalysis, ...]:
    """Analyze each column separately, in order."""
    return tuple(collect_timestamp_analysis(runner, ref, column, days) for column in columns)


@dataclass(frozen=True)
class _Aggregates:
    total_rows: Fact[int]
    null_rows: Fact[int]
    min_value: Fact[datetime | date]
    max_value: Fact[datetime | date]
    future_values: Fact[int]
    distinct_dates: Fact[int]
    today: Fact[date]


@dataclass(frozen=True)
class _Gaps:
    min_gap_days: Fact[int]
    median_gap_days: Fact[float]
    mean_gap_days: Fact[float]
    max_gap_days: Fact[int]

    @classmethod
    def unavailable(cls, reason: str) -> _Gaps:
        return cls(
            min_gap_days=Fact.unavailable("exact", reason),
            median_gap_days=Fact.unavailable("exact", reason),
            mean_gap_days=Fact.unavailable("exact", reason),
            max_gap_days=Fact.unavailable("exact", reason),
        )


@dataclass(frozen=True)
class _Window:
    first_day: Fact[date]
    last_day: Fact[date]
    rows_per_day: Fact[tuple[DayCount, ...]]

    @classmethod
    def unavailable(cls, reason: str) -> _Window:
        return cls(
            first_day=Fact.unavailable("derived", reason),
            last_day=Fact.unavailable("derived", reason),
            rows_per_day=Fact.unavailable("derived", reason),
        )


def _aggregates(runner: SqlRunner, column: str, table: str, data_type: str) -> _Aggregates:
    result = safe_query(runner, _AGGREGATE_QUERY.format(column=column, table=table))
    if isinstance(result, Exception):
        return _unavailable_aggregates(short_reason(result))
    if not result:
        return _unavailable_aggregates(NO_AGGREGATE_ROWS)

    row = result[0]
    is_date = data_type.strip().lower() == "date"
    return _Aggregates(
        total_rows=_int(row, "total_rows", "timestamp aggregate"),
        null_rows=_int(row, "null_rows", "timestamp aggregate"),
        min_value=_bound(row, "min_value", is_date),
        max_value=_bound(row, "max_value", is_date),
        future_values=_int(row, "future_values", "timestamp aggregate"),
        distinct_dates=_int(row, "distinct_dates", "timestamp aggregate"),
        today=_today(row),
    )


def _unavailable_aggregates(reason: str) -> _Aggregates:
    return _Aggregates(
        total_rows=Fact.unavailable("exact", reason),
        null_rows=Fact.unavailable("exact", reason),
        min_value=Fact.unavailable("exact", reason),
        max_value=Fact.unavailable("exact", reason),
        future_values=Fact.unavailable("exact", reason),
        distinct_dates=Fact.unavailable("exact", reason),
        today=Fact.unavailable("exact", reason),
    )


def _bound(row: dict[str, Any], name: str, is_date: bool) -> Fact[datetime | date]:
    value = row.get(name)
    if value is None:
        return Fact.unavailable("exact", NO_NON_NULL_VALUES)
    valid = (
        isinstance(value, date) and not isinstance(value, datetime)
        if is_date
        else isinstance(value, datetime)
    )
    if not valid:
        return Fact.unavailable(
            "exact",
            f"Unexpected {name} type in timestamp aggregate query result: {type(value).__name__}",
        )
    return Fact(value, source="exact")


def _today(row: dict[str, Any]) -> Fact[date]:
    value = row.get("today")
    if not isinstance(value, date) or isinstance(value, datetime):
        return Fact.unavailable(
            "exact",
            f"Unexpected today type in timestamp aggregate query result: {type(value).__name__}",
        )
    return Fact(value, source="exact")


def _gaps(runner: SqlRunner, column: str, table: str) -> _Gaps:
    result = safe_query(runner, _GAP_QUERY.format(column=column, table=table))
    if isinstance(result, Exception):
        return _Gaps.unavailable(short_reason(result))
    if not result:
        return _Gaps.unavailable(NO_GAP_ROWS)

    row = result[0]
    if row.get("min_gap_days") is None:
        return _Gaps.unavailable(FEWER_THAN_TWO_DATES)
    return _Gaps(
        min_gap_days=_int(row, "min_gap_days", "gap"),
        median_gap_days=_float(row, "median_gap_days", "gap"),
        mean_gap_days=_float(row, "mean_gap_days", "gap"),
        max_gap_days=_int(row, "max_gap_days", "gap"),
    )


def _window(runner: SqlRunner, column: str, table: str, today: date, days: int) -> _Window:
    first_day = today - timedelta(days=days)
    last_day = today - timedelta(days=1)
    return _Window(
        first_day=Fact(first_day, source="derived"),
        last_day=Fact(last_day, source="derived"),
        rows_per_day=_rows_per_day(runner, column, table, today, days, first_day, last_day),
    )


def _rows_per_day(
    runner: SqlRunner,
    column: str,
    table: str,
    today: date,
    days: int,
    first_day: date,
    last_day: date,
) -> Fact[tuple[DayCount, ...]]:
    result = safe_query(
        runner, _DAY_QUERY.format(column=column, table=table), {"today": today, "days": days}
    )
    if isinstance(result, Exception):
        return Fact.unavailable("derived", short_reason(result))

    counts: dict[date, int] = {}
    for row in result:
        day = row.get("__dataowl_day")
        rows = row.get("__dataowl_rows")
        if not isinstance(day, date) or isinstance(day, datetime):
            return Fact.unavailable(
                "derived",
                f"Unexpected __dataowl_day type in day query result: {type(day).__name__}",
            )
        if not isinstance(rows, int) or isinstance(rows, bool):
            return Fact.unavailable(
                "derived",
                f"Unexpected __dataowl_rows type in day query result: {type(rows).__name__}",
            )
        if not first_day <= day <= last_day:
            return Fact.unavailable("derived", f"Day query returned {day} outside the window")
        if day in counts:
            return Fact.unavailable("derived", f"Day query returned {day} more than once")
        counts[day] = rows

    return Fact(fill_days(counts, first_day, last_day), source="derived")


def _null_share(null_rows: Fact[int], total_rows: Fact[int]) -> Fact[float]:
    if null_rows.available and total_rows.available and total_rows.value == 0:
        return Fact.unavailable("derived", NO_ROWS)
    return derive(lambda nulls, total: nulls / total, null_rows, total_rows)


def _int(row: dict[str, Any], name: str, query: str) -> Fact[int]:
    value = row.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        return Fact.unavailable(
            "exact", f"Unexpected {name} type in {query} query result: {type(value).__name__}"
        )
    return Fact(value, source="exact")


def _float(row: dict[str, Any], name: str, query: str) -> Fact[float]:
    value = row.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Fact.unavailable(
            "exact", f"Unexpected {name} type in {query} query result: {type(value).__name__}"
        )
    return Fact(float(value), source="exact")
