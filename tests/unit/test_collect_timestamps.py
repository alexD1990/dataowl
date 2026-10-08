from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from conftest import FakeRunner, assert_no_judgement, assert_read_only

from dataowl.collect.timestamps import (
    FEWER_THAN_TWO_DATES,
    NO_AGGREGATE_ROWS,
    NO_NON_NULL_VALUES,
    collect_timestamp_analyses,
    collect_timestamp_analysis,
    fill_days,
    normalize_timestamp_columns,
    resolve_time_column,
    validate_days,
)
from dataowl.identifiers import TableRef
from dataowl.model.analysis import TIMESTAMP_NOTES, DayCount, TimestampAnalysis
from dataowl.model.facts import Fact, to_jsonable
from dataowl.model.overview import ColumnInfo

REF = TableRef("main", "sales", "orders")
TODAY = date(2026, 10, 8)

CREATED_AT = ColumnInfo("created_at", 0, "timestamp", True, None)
ORDER_DATE = ColumnInfo("Order_Date", 1, "date", True, None)
LOADED_AT = ColumnInfo("loaded_at", 2, "timestamp_ntz", True, None)
STATUS = ColumnInfo("status", 3, "string", True, None)
LEGACY_TS = ColumnInfo("legacy_ts", 4, " TIMESTAMP ", True, None)
COLUMNS = (CREATED_AT, ORDER_DATE, LOADED_AT, STATUS, LEGACY_TS)

AGGREGATE = r"current_date\(\) AS today"
GAPS = r"__dataowl_gap_days"
DAYS = r"__dataowl_day\b"


def _aggregate_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "total_rows": 1000,
        "null_rows": 10,
        "min_value": datetime(2024, 1, 1, 0, 5),
        "max_value": datetime(2026, 10, 7, 23, 50),
        "future_values": 0,
        "distinct_dates": 1011,
        "today": TODAY,
    }
    row.update(overrides)
    return row


def _gap_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "min_gap_days": 1,
        "median_gap_days": 1.0,
        "mean_gap_days": 1.0,
        "max_gap_days": 1,
    }
    row.update(overrides)
    return row


def _day_rows(counts: dict[date, int]) -> list[dict[str, Any]]:
    return [{"__dataowl_day": day, "__dataowl_rows": rows} for day, rows in counts.items()]


def _every_day(days: int, rows: int) -> dict[date, int]:
    return {TODAY - timedelta(days=offset): rows for offset in range(1, days + 1)}


def _register(
    fake: FakeRunner,
    aggregate: list[dict[str, Any]] | None = None,
    gaps: list[dict[str, Any]] | None = None,
    day_rows: list[dict[str, Any]] | None = None,
    column: str = "",
) -> None:
    prefix = f"`{column}`.*" if column else ""
    fake.on(prefix + AGGREGATE, [_aggregate_row()] if aggregate is None else aggregate)
    fake.on(prefix + GAPS, [_gap_row()] if gaps is None else gaps)
    fake.on(prefix + DAYS, _day_rows(_every_day(30, 40)) if day_rows is None else day_rows)


def _sql(fake: FakeRunner, pattern: str) -> list[tuple[str, dict[str, Any] | None]]:
    return [(sql, params) for sql, params in fake.queries if re.search(pattern, sql)]


def _assert_all_available(analysis: TimestampAnalysis) -> None:
    for name in TimestampAnalysis.__dataclass_fields__:
        value = getattr(analysis, name)
        if isinstance(value, Fact):
            assert value.available is True, f"{name}: {value.reason}"


# Validation


def test_resolve_returns_schema_column_case_insensitively() -> None:
    assert resolve_time_column("ORDER_date", COLUMNS) is ORDER_DATE


@pytest.mark.parametrize("column", [CREATED_AT, ORDER_DATE, LOADED_AT, LEGACY_TS])
def test_resolve_accepts_supported_types(column: ColumnInfo) -> None:
    assert resolve_time_column(column.name, COLUMNS) is column


def test_resolve_rejects_string_with_factual_message() -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_time_column("status", COLUMNS)

    assert str(excinfo.value) == (
        "timestamp column 'status' has type string; "
        "supported types are timestamp, timestamp_ntz and date"
    )
    assert_no_judgement(str(excinfo.value))


@pytest.mark.parametrize("data_type", ["bigint", "timestamp_ltz", "array<timestamp>", "STRING"])
def test_resolve_rejects_other_types(data_type: str) -> None:
    columns = (ColumnInfo("ts", 0, data_type, True, None),)

    with pytest.raises(ValueError, match="supported types are"):
        resolve_time_column("ts", columns)


def test_resolve_rejects_unknown_column() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        resolve_time_column("missing", COLUMNS)


def test_normalize_single_string() -> None:
    assert normalize_timestamp_columns("created_at", COLUMNS) == (CREATED_AT,)


def test_normalize_list_keeps_input_order() -> None:
    result = normalize_timestamp_columns(["loaded_at", "order_date", "CREATED_AT"], COLUMNS)

    assert result == (LOADED_AT, ORDER_DATE, CREATED_AT)


def test_normalize_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="empty list"):
        normalize_timestamp_columns([], COLUMNS)


def test_normalize_rejects_duplicate_case_insensitively() -> None:
    with pytest.raises(ValueError, match="more than once"):
        normalize_timestamp_columns(["created_at", "Created_At"], COLUMNS)


@pytest.mark.parametrize("element", [1, None, ("created_at",)])
def test_normalize_rejects_non_string_elements(element: Any) -> None:
    with pytest.raises(ValueError, match="must be column names"):
        normalize_timestamp_columns(["created_at", element], COLUMNS)


def test_normalize_rejects_string_column_in_list() -> None:
    with pytest.raises(ValueError, match="has type string"):
        normalize_timestamp_columns(["created_at", "status"], COLUMNS)


@pytest.mark.parametrize("days", [1, 30, 365])
def test_validate_days_accepts_positive_int(days: int) -> None:
    assert validate_days(days) == days


@pytest.mark.parametrize("days", [0, -1, True, False, 1.5, "30", None])
def test_validate_days_rejects_invalid(days: Any) -> None:
    with pytest.raises(ValueError):
        validate_days(days)


# fill_days


def test_fill_days_fills_missing_days_with_zero() -> None:
    first = date(2026, 9, 28)
    counts = {date(2026, 9, 28): 5, date(2026, 9, 30): 2}

    result = fill_days(counts, first, date(2026, 10, 1))

    assert result == (
        DayCount(date(2026, 9, 28), 5),
        DayCount(date(2026, 9, 29), 0),
        DayCount(date(2026, 9, 30), 2),
        DayCount(date(2026, 10, 1), 0),
    )


def test_fill_days_window_without_rows() -> None:
    result = fill_days({}, date(2026, 10, 1), date(2026, 10, 3))

    assert result == tuple(DayCount(date(2026, 10, d), 0) for d in (1, 2, 3))


def test_fill_days_single_day() -> None:
    assert fill_days({TODAY: 3}, TODAY, TODAY) == (DayCount(TODAY, 3),)


# Collection


def test_timestamp_column(fake_runner: FakeRunner) -> None:
    _register(fake_runner)

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    _assert_all_available(analysis)
    assert analysis.column == "created_at"
    assert analysis.data_type == "timestamp"
    assert analysis.total_rows == Fact(1000, source="exact")
    assert analysis.null_rows == Fact(10, source="exact")
    assert analysis.min_value == Fact(datetime(2024, 1, 1, 0, 5), source="exact")
    assert analysis.max_value == Fact(datetime(2026, 10, 7, 23, 50), source="exact")
    assert analysis.future_values == Fact(0, source="exact")
    assert analysis.distinct_dates == Fact(1011, source="exact")
    assert analysis.days == 30
    assert analysis.notes == TIMESTAMP_NOTES
    assert len(fake_runner.queries) == 3
    assert_read_only(fake_runner)
    for sql, _ in fake_runner.queries:
        assert "`created_at`" in sql
        assert "`main`.`sales`.`orders`" in sql


def test_date_column(fake_runner: FakeRunner) -> None:
    _register(fake_runner, aggregate=[_aggregate_row(min_value=date(2024, 1, 1), max_value=TODAY)])

    analysis = collect_timestamp_analysis(fake_runner, REF, ORDER_DATE, 30)

    _assert_all_available(analysis)
    assert analysis.column == "Order_Date"
    assert analysis.min_value == Fact(date(2024, 1, 1), source="exact")
    assert analysis.max_value == Fact(TODAY, source="exact")
    assert len(fake_runner.queries) == 3
    assert all("`Order_Date`" in sql for sql, _ in fake_runner.queries)
    assert_read_only(fake_runner)


def test_timestamp_ntz_column(fake_runner: FakeRunner) -> None:
    _register(fake_runner)

    analysis = collect_timestamp_analysis(fake_runner, REF, LOADED_AT, 30)

    _assert_all_available(analysis)
    assert analysis.min_value == Fact(datetime(2024, 1, 1, 0, 5), source="exact")


@pytest.mark.parametrize(
    ("column", "value"),
    [(CREATED_AT, date(2024, 1, 1)), (ORDER_DATE, datetime(2024, 1, 1)), (CREATED_AT, "2024")],
)
def test_min_max_must_match_column_type(
    fake_runner: FakeRunner, column: ColumnInfo, value: Any
) -> None:
    _register(fake_runner, aggregate=[_aggregate_row(min_value=value, max_value=value)])

    analysis = collect_timestamp_analysis(fake_runner, REF, column, 30)

    for fact, name in ((analysis.min_value, "min_value"), (analysis.max_value, "max_value")):
        assert fact.available is False
        assert fact.reason == (
            f"Unexpected {name} type in timestamp aggregate query result: {type(value).__name__}"
        )
    assert analysis.total_rows.available is True
    assert analysis.rows_per_day.available is True


def test_multiple_columns(fake_runner: FakeRunner) -> None:
    _register(fake_runner, column="created_at")
    _register(
        fake_runner,
        aggregate=[_aggregate_row(min_value=date(2025, 1, 1), max_value=date(2026, 10, 1))],
        gaps=[_gap_row(min_gap_days=7, median_gap_days=7.0, mean_gap_days=7.0, max_gap_days=7)],
        column="Order_Date",
    )

    result = collect_timestamp_analyses(fake_runner, REF, (CREATED_AT, ORDER_DATE), 30)

    assert [a.column for a in result] == ["created_at", "Order_Date"]
    for analysis in result:
        _assert_all_available(analysis)
    assert result[0].min_value == Fact(datetime(2024, 1, 1, 0, 5), source="exact")
    assert result[0].min_gap_days == Fact(1, source="exact")
    assert result[1].min_value == Fact(date(2025, 1, 1), source="exact")
    assert result[1].min_gap_days == Fact(7, source="exact")
    assert len(fake_runner.queries) == 6
    assert_read_only(fake_runner)


def test_null_only_column(fake_runner: FakeRunner) -> None:
    _register(
        fake_runner,
        aggregate=[
            _aggregate_row(
                null_rows=1000, min_value=None, max_value=None, distinct_dates=0, future_values=0
            )
        ],
        day_rows=[],
    )

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 7)

    assert analysis.total_rows == Fact(1000, source="exact")
    assert analysis.null_rows == Fact(1000, source="exact")
    assert analysis.min_value == Fact.unavailable("exact", NO_NON_NULL_VALUES)
    assert analysis.max_value == Fact.unavailable("exact", NO_NON_NULL_VALUES)
    assert analysis.distinct_dates == Fact(0, source="exact")
    assert analysis.min_gap_days == Fact.unavailable("exact", FEWER_THAN_TWO_DATES)
    assert analysis.rows_per_day_max == Fact(0, source="derived")
    assert _sql(fake_runner, GAPS) == []
    assert len(fake_runner.queries) == 2


def test_single_distinct_date_skips_gap_query(fake_runner: FakeRunner) -> None:
    _register(fake_runner, aggregate=[_aggregate_row(distinct_dates=1)])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    for fact in (
        analysis.min_gap_days,
        analysis.median_gap_days,
        analysis.mean_gap_days,
        analysis.max_gap_days,
    ):
        assert fact == Fact.unavailable("exact", FEWER_THAN_TWO_DATES)
    assert analysis.distinct_dates == Fact(1, source="exact")
    assert _sql(fake_runner, GAPS) == []
    assert len(fake_runner.queries) == 2
    assert analysis.rows_per_day.available is True


def test_gap_query_without_gaps(fake_runner: FakeRunner) -> None:
    empty = {"min_gap_days": None, "median_gap_days": None, "mean_gap_days": None}
    _register(fake_runner, gaps=[{**empty, "max_gap_days": None}])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    assert analysis.min_gap_days == Fact.unavailable("exact", FEWER_THAN_TWO_DATES)
    assert analysis.max_gap_days == Fact.unavailable("exact", FEWER_THAN_TWO_DATES)


def test_even_cadence(fake_runner: FakeRunner) -> None:
    _register(fake_runner, day_rows=_day_rows(_every_day(30, 100)))

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    _assert_all_available(analysis)
    assert analysis.min_gap_days == Fact(1, source="exact")
    assert analysis.median_gap_days == Fact(1.0, source="exact")
    assert analysis.mean_gap_days == Fact(1.0, source="exact")
    assert analysis.max_gap_days == Fact(1, source="exact")
    assert analysis.rows_per_day_min == Fact(100, source="derived")
    assert analysis.rows_per_day_median == Fact(100.0, source="derived")
    assert analysis.rows_per_day_mean == Fact(100.0, source="derived")
    assert analysis.rows_per_day_max == Fact(100, source="derived")


def test_cadence_with_gaps(fake_runner: FakeRunner) -> None:
    _register(
        fake_runner,
        gaps=[_gap_row(min_gap_days=1, median_gap_days=2, mean_gap_days=3.25, max_gap_days=14)],
    )

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    _assert_all_available(analysis)
    assert analysis.min_gap_days == Fact(1, source="exact")
    assert analysis.median_gap_days == Fact(2.0, source="exact")
    assert isinstance(analysis.median_gap_days.value, float)
    assert analysis.mean_gap_days == Fact(3.25, source="exact")
    assert analysis.max_gap_days == Fact(14, source="exact")


def test_days_without_rows_are_zero(fake_runner: FakeRunner) -> None:
    counts = {TODAY - timedelta(days=5): 9, TODAY - timedelta(days=2): 3}
    _register(fake_runner, day_rows=_day_rows(counts))

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 5)

    _assert_all_available(analysis)
    assert analysis.rows_per_day == Fact(
        (
            DayCount(date(2026, 10, 3), 9),
            DayCount(date(2026, 10, 4), 0),
            DayCount(date(2026, 10, 5), 0),
            DayCount(date(2026, 10, 6), 3),
            DayCount(date(2026, 10, 7), 0),
        ),
        source="derived",
    )
    assert analysis.rows_per_day_min == Fact(0, source="derived")
    assert analysis.rows_per_day_median == Fact(0.0, source="derived")
    assert analysis.rows_per_day_mean == Fact(2.4, source="derived")
    assert analysis.rows_per_day_max == Fact(9, source="derived")


def test_no_day_rows_gives_zero_for_every_day(fake_runner: FakeRunner) -> None:
    _register(fake_runner, day_rows=[])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 3)

    _assert_all_available(analysis)
    assert analysis.rows_per_day.value == tuple(
        DayCount(TODAY - timedelta(days=offset), 0) for offset in (3, 2, 1)
    )
    assert analysis.rows_per_day_mean == Fact(0.0, source="derived")


def test_day_query_gets_today_and_days_as_parameters(fake_runner: FakeRunner) -> None:
    _register(fake_runner, day_rows=_day_rows(_every_day(7, 40)))

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 7)

    [(sql, params)] = _sql(fake_runner, DAYS)
    assert params == {"today": TODAY, "days": 7}
    assert "`created_at` >= date_sub(:today, :days)" in sql
    assert "`created_at` < :today" in sql
    assert "GROUP BY CAST(`created_at` AS DATE)" in sql
    assert "current_timestamp" not in sql
    assert analysis.window_first_day == Fact(date(2026, 10, 1), source="derived")
    assert analysis.window_last_day == Fact(date(2026, 10, 7), source="derived")
    assert analysis.rows_per_day.value is not None
    assert len(analysis.rows_per_day.value) == 7
    assert analysis.rows_per_day.value[0].day == TODAY - timedelta(days=7)
    assert analysis.rows_per_day.value[-1].day == TODAY - timedelta(days=1)


def test_aggregate_and_gap_queries_have_no_parameters(fake_runner: FakeRunner) -> None:
    _register(fake_runner)

    collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    [(_, aggregate_params)] = _sql(fake_runner, AGGREGATE)
    [(gap_sql, gap_params)] = _sql(fake_runner, GAPS)
    assert aggregate_params is None
    assert gap_params is None
    assert "WHERE `created_at` IS NOT NULL" in gap_sql


def test_aggregate_query_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(AGGREGATE, RuntimeError("PERMISSION_DENIED: no SELECT\ntrace"))
    _register(fake_runner)

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    reason = "PERMISSION_DENIED: no SELECT"
    for fact in (
        analysis.total_rows,
        analysis.null_rows,
        analysis.min_value,
        analysis.max_value,
        analysis.future_values,
        analysis.distinct_dates,
    ):
        assert fact == Fact.unavailable("exact", reason)
    for derived in (
        analysis.window_first_day,
        analysis.window_last_day,
        analysis.rows_per_day,
        analysis.rows_per_day_min,
        analysis.rows_per_day_median,
        analysis.rows_per_day_mean,
        analysis.rows_per_day_max,
    ):
        assert derived == Fact.unavailable("derived", reason)
    assert analysis.min_gap_days == Fact(1, source="exact")
    assert _sql(fake_runner, DAYS) == []
    assert len(fake_runner.queries) == 2


def test_aggregate_query_without_rows(fake_runner: FakeRunner) -> None:
    _register(fake_runner, aggregate=[])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    assert analysis.total_rows == Fact.unavailable("exact", NO_AGGREGATE_ROWS)
    assert analysis.rows_per_day == Fact.unavailable("derived", NO_AGGREGATE_ROWS)
    assert _sql(fake_runner, DAYS) == []


def test_gap_query_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(GAPS, RuntimeError("boom"))
    _register(fake_runner)

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    assert analysis.min_gap_days == Fact.unavailable("exact", "boom")
    assert analysis.max_gap_days == Fact.unavailable("exact", "boom")
    assert analysis.total_rows.available is True
    assert analysis.rows_per_day.available is True


def test_day_query_error_affects_only_day_facts(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(DAYS, RuntimeError("timeout"))
    _register(fake_runner)

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    for derived in (
        analysis.rows_per_day,
        analysis.rows_per_day_min,
        analysis.rows_per_day_median,
        analysis.rows_per_day_mean,
        analysis.rows_per_day_max,
    ):
        assert derived == Fact.unavailable("derived", "timeout")
    assert analysis.window_first_day == Fact(date(2026, 9, 8), source="derived")
    assert analysis.window_last_day == Fact(date(2026, 10, 7), source="derived")
    assert analysis.total_rows == Fact(1000, source="exact")
    assert analysis.min_value.available is True
    assert analysis.min_gap_days == Fact(1, source="exact")
    assert len(fake_runner.queries) == 3


@pytest.mark.parametrize("name", ["total_rows", "null_rows", "future_values", "distinct_dates"])
@pytest.mark.parametrize("value", ["12", 1.5, True, None])
def test_unexpected_aggregate_type(fake_runner: FakeRunner, name: str, value: Any) -> None:
    _register(fake_runner, aggregate=[_aggregate_row(**{name: value})])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    assert getattr(analysis, name) == Fact.unavailable(
        "exact",
        f"Unexpected {name} type in timestamp aggregate query result: {type(value).__name__}",
    )
    others = {"total_rows", "null_rows", "future_values", "distinct_dates"} - {name}
    for other in others:
        assert getattr(analysis, other).available is True
    assert analysis.rows_per_day.available is True


@pytest.mark.parametrize("value", [datetime(2026, 10, 8, 0, 0), "2026-10-08", None])
def test_unexpected_today_type_skips_day_query(fake_runner: FakeRunner, value: Any) -> None:
    _register(fake_runner, aggregate=[_aggregate_row(today=value)])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    reason = f"Unexpected today type in timestamp aggregate query result: {type(value).__name__}"
    assert analysis.window_first_day == Fact.unavailable("derived", reason)
    assert analysis.rows_per_day == Fact.unavailable("derived", reason)
    assert analysis.rows_per_day_max == Fact.unavailable("derived", reason)
    assert analysis.total_rows.available is True
    assert _sql(fake_runner, DAYS) == []


@pytest.mark.parametrize(
    "name", ["min_gap_days", "median_gap_days", "mean_gap_days", "max_gap_days"]
)
def test_unexpected_gap_type(fake_runner: FakeRunner, name: str) -> None:
    _register(fake_runner, gaps=[_gap_row(**{name: "1"})])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    assert getattr(analysis, name) == Fact.unavailable(
        "exact", f"Unexpected {name} type in gap query result: str"
    )
    others = {"min_gap_days", "median_gap_days", "mean_gap_days", "max_gap_days"} - {name}
    for other in others:
        assert getattr(analysis, other).available is True


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        (
            _day_rows({TODAY: 5}),
            "Day query returned 2026-10-08 outside the window",
        ),
        (
            _day_rows({TODAY - timedelta(days=31): 5}),
            "Day query returned 2026-09-07 outside the window",
        ),
        (
            _day_rows({TODAY - timedelta(days=1): 5}) + _day_rows({TODAY - timedelta(days=1): 2}),
            "Day query returned 2026-10-07 more than once",
        ),
        (
            [{"__dataowl_day": datetime(2026, 10, 7), "__dataowl_rows": 5}],
            "Unexpected __dataowl_day type in day query result: datetime",
        ),
        (
            [{"__dataowl_day": date(2026, 10, 7), "__dataowl_rows": "5"}],
            "Unexpected __dataowl_rows type in day query result: str",
        ),
        (
            [{"__dataowl_rows": 5}],
            "Unexpected __dataowl_day type in day query result: NoneType",
        ),
    ],
)
def test_unexpected_day_rows(
    fake_runner: FakeRunner, rows: list[dict[str, Any]], reason: str
) -> None:
    _register(fake_runner, day_rows=rows)

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 30)

    assert analysis.rows_per_day == Fact.unavailable("derived", reason)
    assert analysis.rows_per_day_min == Fact.unavailable("derived", reason)
    assert analysis.rows_per_day_median == Fact.unavailable("derived", reason)
    assert analysis.rows_per_day_mean == Fact.unavailable("derived", reason)
    assert analysis.rows_per_day_max == Fact.unavailable("derived", reason)
    assert analysis.window_first_day == Fact(date(2026, 9, 8), source="derived")
    assert analysis.window_last_day == Fact(date(2026, 10, 7), source="derived")
    assert analysis.total_rows.available is True


def test_internal_names_are_prefixed(fake_runner: FakeRunner) -> None:
    columns = (ColumnInfo("day", 0, "date", True, None),)
    [column] = normalize_timestamp_columns("day", columns)
    _register(fake_runner, aggregate=[_aggregate_row(min_value=TODAY, max_value=TODAY)])

    analysis = collect_timestamp_analysis(fake_runner, REF, column, 30)

    _assert_all_available(analysis)
    [(gap_sql, _)] = _sql(fake_runner, GAPS)
    [(day_sql, _)] = _sql(fake_runner, DAYS)
    assert "AS __dataowl_dt" in gap_sql
    assert "AS __dataowl_gap_days" in gap_sql
    assert "CAST(`day` AS DATE) AS __dataowl_day" in day_sql
    assert "COUNT(*) AS __dataowl_rows" in day_sql


def test_column_name_is_quoted(fake_runner: FakeRunner) -> None:
    columns = (ColumnInfo("event`time", 0, "timestamp", True, None),)
    _register(fake_runner)

    collect_timestamp_analysis(fake_runner, REF, columns[0], 30)

    assert len(fake_runner.queries) == 3
    for sql, _ in fake_runner.queries:
        assert "`event``time`" in sql
    assert_read_only(fake_runner)


def test_analysis_is_json_serializable(fake_runner: FakeRunner) -> None:
    _register(fake_runner, day_rows=[])

    analysis = collect_timestamp_analysis(fake_runner, REF, CREATED_AT, 3)

    data = json.loads(json.dumps(to_jsonable(analysis)))
    assert data["min_value"]["value"] == "2024-01-01T00:05:00"
    assert data["rows_per_day"]["value"][0] == {"day": "2026-10-05", "rows": 0}
    assert data["notes"] == list(TIMESTAMP_NOTES)


def test_notes_have_no_judgement() -> None:
    assert len(TIMESTAMP_NOTES) == 2
    for note in TIMESTAMP_NOTES:
        assert_no_judgement(note)
