from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import assert_no_judgement

from dataowl.identifiers import TableRef
from dataowl.model.analysis import (
    ColumnAnalysis,
    ComparisonAnalysis,
    DayCount,
    KeyAnalysis,
    TimestampAnalysis,
)
from dataowl.model.facts import Fact
from dataowl.render.terminal import render_column_analysis

FIXTURES = Path(__file__).parent.parent / "fixtures"

ORDERS = TableRef("main", "sales", "orders")

# total, nulls, distinct, duplicate, in duplicate, max, median, 1, 2–10, 11–100, >100 rows
CUSTOMER_KEY = (1284991, 0, 52004, 51870, 1284857, 4210, 18.0, 134, 9902, 41731, 237)
COMPOSITE_KEY = (1284991, 0, 1284991, 0, 0, 1, 1.0, 1284991, 0, 0, 0)


def _e(value: Any) -> Fact[Any]:
    return Fact(value, source="exact")


def _d(value: Any) -> Fact[Any]:
    return Fact(value, source="derived")


def _key(columns: tuple[str, ...], *values: float) -> KeyAnalysis:
    total, nulls, distinct, dup, in_dup, max_rows, median, k1, k2, k3, k4 = values
    return KeyAnalysis(
        columns=columns,
        total_rows=_e(total),
        rows_with_null_key=_e(nulls),
        distinct_keys=_e(distinct),
        duplicate_keys=_e(dup),
        rows_in_duplicate_keys=_e(in_dup),
        max_rows_per_key=_e(max_rows),
        median_rows_per_key=_e(median),
        keys_with_1_row=_e(k1),
        keys_with_2_10_rows=_e(k2),
        keys_with_11_100_rows=_e(k3),
        keys_with_over_100_rows=_e(k4),
    )


def _unavailable_key(columns: tuple[str, ...], reason: str) -> KeyAnalysis:
    fields = {f.name for f in dataclasses.fields(KeyAnalysis)} - {"columns"}
    return KeyAnalysis(
        columns=columns, **{name: Fact.unavailable("exact", reason) for name in fields}
    )


def _timestamp(column: str, data_type: str, **overrides: Any) -> TimestampAnalysis:
    days = overrides.get("days", 30)
    first = date(2026, 10, 8) - timedelta(days=days)
    values: dict[str, Any] = {
        "column": column,
        "data_type": data_type,
        "total_rows": _e(1000),
        "null_rows": _e(0),
        "null_share": _d(0.0),
        "min_value": _e(datetime(2021, 1, 4, 6, 30)),
        "max_value": _e(datetime(2026, 10, 7, 23, 58)),
        "future_values": _e(0),
        "distinct_dates": _e(300),
        "min_gap_days": _e(1),
        "median_gap_days": _e(1.0),
        "mean_gap_days": _e(1.0),
        "max_gap_days": _e(1),
        "days": days,
        "window_first_day": _d(first),
        "window_last_day": _d(date(2026, 10, 7)),
        "rows_per_day": _d(tuple(DayCount(first + timedelta(days=i), 0) for i in range(days))),
        "rows_per_day_min": _d(0),
        "rows_per_day_median": _d(0.0),
        "rows_per_day_mean": _d(0.0),
        "rows_per_day_max": _d(0),
    }
    values.update(overrides)
    return TimestampAnalysis(**values)


def _comparison(
    first: str, second: str, first_type: str, second_type: str, *values: int
) -> ComparisonAnalysis:
    after, equal, before, either = values
    return ComparisonAnalysis(
        first_column=first,
        second_column=second,
        first_data_type=first_type,
        second_data_type=second_type,
        second_after_first=_e(after),
        second_equal_first=_e(equal),
        second_before_first=_e(before),
        either_null=_e(either),
    )


def full_analysis() -> ColumnAnalysis:
    return ColumnAnalysis(
        table=ORDERS,
        primary_key=Fact(("customer_id", "order_id"), source="metadata"),
        keys=(
            _key(("customer_id",), *CUSTOMER_KEY),
            _key(("customer_id", "order_id"), *COMPOSITE_KEY),
        ),
        timestamps=(
            _timestamp(
                "created_at",
                "timestamp",
                total_rows=_e(1284991),
                null_rows=_e(1284),
                null_share=_d(1284 / 1284991),
                distinct_dates=_e(2103),
                max_gap_days=_e(3),
                rows_per_day_min=_d(38912),
                rows_per_day_median=_d(42800.0),
                rows_per_day_mean=_d(42833.33),
                rows_per_day_max=_d(45001),
            ),
            _timestamp(
                "ingested_date",
                "date",
                min_value=_e(date(2021, 1, 4)),
                max_value=_e(date(2026, 9, 28)),
                median_gap_days=_e(7.0),
                mean_gap_days=_e(7.06),
                max_gap_days=_e(21),
                rows_per_day_mean=_d(4283.33),
                rows_per_day_max=_d(42800),
            ),
        ),
        comparison=_comparison(
            "created_at", "ingested_date", "timestamp", "DATE", 12004, 1272987, 0, 0
        ),
    )


def unavailable_analysis() -> ColumnAnalysis:
    today_reason = "Unexpected today type in timestamp aggregate query result: datetime"
    gaps_reason = "Fewer than two distinct dates"
    return ColumnAnalysis(
        table=TableRef("my-catalog", "raw", "events"),
        primary_key=Fact.unavailable(
            "metadata",
            "[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG on Catalog 'my-catalog'.",
        ),
        keys=(_unavailable_key(("event_id",), "Key query returned no rows"),),
        timestamps=(
            _timestamp(
                "event_time",
                "timestamp",
                total_rows=_e(500),
                min_value=_e(datetime(2026, 8, 29)),
                max_value=Fact.unavailable(
                    "exact",
                    "Unexpected max_value type in timestamp aggregate query result: str",
                ),
                future_values=_e(2),
                distinct_dates=_e(40),
                median_gap_days=Fact.unavailable(
                    "exact", "Unexpected median_gap_days type in gap query result: Decimal"
                ),
                mean_gap_days=_e(1.3),
                max_gap_days=_e(4),
                window_first_day=Fact.unavailable("derived", today_reason),
                window_last_day=Fact.unavailable("derived", today_reason),
                rows_per_day=Fact.unavailable("derived", today_reason),
                rows_per_day_min=Fact.unavailable("derived", today_reason),
                rows_per_day_median=Fact.unavailable("derived", today_reason),
                rows_per_day_mean=Fact.unavailable("derived", today_reason),
                rows_per_day_max=Fact.unavailable("derived", today_reason),
            ),
            _timestamp(
                "loaded_on",
                "date",
                total_rows=_e(10),
                null_rows=_e(10),
                null_share=_d(1.0),
                min_value=Fact.unavailable("exact", "No non-null values"),
                max_value=Fact.unavailable("exact", "No non-null values"),
                distinct_dates=_e(0),
                min_gap_days=Fact.unavailable("exact", gaps_reason),
                median_gap_days=Fact.unavailable("exact", gaps_reason),
                mean_gap_days=Fact.unavailable("exact", gaps_reason),
                max_gap_days=Fact.unavailable("exact", gaps_reason),
                rows_per_day=Fact.unavailable("derived", "timeout"),
                rows_per_day_min=Fact.unavailable("derived", "timeout"),
                rows_per_day_median=Fact.unavailable("derived", "timeout"),
                rows_per_day_mean=Fact.unavailable("derived", "timeout"),
                rows_per_day_max=Fact.unavailable("derived", "timeout"),
            ),
        ),
        comparison=ComparisonAnalysis(
            first_column="event_time",
            second_column="loaded_on",
            first_data_type="timestamp",
            second_data_type="date",
            second_after_first=Fact.unavailable("exact", "Comparison query returned no rows"),
            second_equal_first=Fact.unavailable("exact", "Comparison query returned no rows"),
            second_before_first=Fact.unavailable("exact", "Comparison query returned no rows"),
            either_null=Fact.unavailable("exact", "Comparison query returned no rows"),
        ),
    )


def days_analysis() -> ColumnAnalysis:
    counts = (
        DayCount(date(2026, 10, 5), 0),
        DayCount(date(2026, 10, 6), 12004),
        DayCount(date(2026, 10, 7), 980),
    )
    return ColumnAnalysis(
        table=ORDERS,
        primary_key=None,
        keys=(),
        timestamps=(
            _timestamp(
                "created_at",
                "timestamp",
                days=3,
                total_rows=_e(12984),
                min_value=_e(datetime(2026, 10, 6, 0, 12)),
                max_value=_e(datetime(2026, 10, 7, 23, 40)),
                distinct_dates=_e(2),
                rows_per_day=_d(counts),
                rows_per_day_min=_d(0),
                rows_per_day_median=_d(980.0),
                rows_per_day_mean=_d(12984 / 3),
                rows_per_day_max=_d(12004),
            ),
        ),
        comparison=None,
    )


def minimal_analysis() -> ColumnAnalysis:
    return ColumnAnalysis(
        table=ORDERS,
        primary_key=None,
        keys=(),
        timestamps=(),
        comparison=_comparison(
            "created_at", "Updated_At", "timestamp", "TIMESTAMP", 700, 300, 0, 12
        ),
    )


@pytest.mark.parametrize(
    ("build", "fixture", "show_days"),
    [
        (full_analysis, "analysis_full.txt", False),
        (unavailable_analysis, "analysis_unavailable.txt", False),
        (days_analysis, "analysis_days.txt", True),
        (minimal_analysis, "analysis_minimal.txt", False),
    ],
)
def test_snapshot(build: Any, fixture: str, show_days: bool) -> None:
    text = render_column_analysis(build(), show_days=show_days)

    assert text + "\n" == (FIXTURES / fixture).read_text(encoding="utf-8")
    assert_no_judgement(text)


def test_day_table_only_with_show_days() -> None:
    text = render_column_analysis(days_analysis())

    assert "2026-10-06  12 004" not in text
    assert "    median 980 · mean 4 328 · min 0 · max 12 004" in text
    assert_no_judgement(text)


def test_window_label_for_one_day() -> None:
    timestamp = _timestamp("created_at", "timestamp", days=1)
    reason = "Unexpected today type in timestamp aggregate query result: datetime"
    unavailable = dataclasses.replace(
        timestamp,
        window_first_day=Fact.unavailable("derived", reason),
        window_last_day=Fact.unavailable("derived", reason),
    )

    available_text = render_column_analysis(
        dataclasses.replace(minimal_analysis(), timestamps=(timestamp,))
    )
    unavailable_text = render_column_analysis(
        dataclasses.replace(minimal_analysis(), timestamps=(unavailable,))
    )

    assert "  Rows per day, last 1 complete day (2026-10-07 – 2026-10-07):" in available_text
    assert "  Rows per day, last 1 complete day:\n" in unavailable_text
    assert "complete days" not in available_text + unavailable_text


def test_no_primary_key_declared() -> None:
    analysis = dataclasses.replace(minimal_analysis(), primary_key=Fact(None, source="metadata"))

    text = render_column_analysis(analysis)

    assert text.splitlines()[:3] == ["main.sales.orders", "", "PRIMARY KEY  none declared"]
    assert_no_judgement(text)


def test_no_primary_key_line_when_not_collected() -> None:
    text = render_column_analysis(minimal_analysis())

    assert "PRIMARY KEY" not in text


@pytest.mark.parametrize(
    ("first_type", "second_type", "has_note"),
    [
        ("timestamp", "date", True),
        (" DATE ", "timestamp_ntz", True),
        ("date", "DATE", False),
        ("timestamp", "timestamp_ntz", False),
    ],
)
def test_date_comparison_note(first_type: str, second_type: str, has_note: bool) -> None:
    analysis = dataclasses.replace(
        minimal_analysis(),
        comparison=_comparison("a", "b", first_type, second_type, 1, 2, 3, 4),
    )

    text = render_column_analysis(analysis)

    assert ("Note: date values are compared as 00:00:00." in text) is has_note


def test_show_prints_render(capsys: pytest.CaptureFixture[str]) -> None:
    analysis = days_analysis()

    analysis.show()
    analysis.show(show_days=True)

    expected = (
        render_column_analysis(analysis) + "\n" + render_column_analysis(analysis, show_days=True)
    )
    assert capsys.readouterr().out == expected + "\n"
