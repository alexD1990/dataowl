from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only

import dataowl
from dataowl import ColumnAnalysis, TableNotFoundError, analyze
from dataowl.collect.columns import NO_COLUMNS
from dataowl.identifiers import TableRef
from dataowl.model.analysis import ComparisonAnalysis, KeyAnalysis, TimestampAnalysis
from dataowl.model.facts import Fact

TODAY = date(2026, 10, 8)

PATTERNS = {
    "tables": r"information_schema\.tables",
    "columns": r"information_schema\.columns",
    "constraints": r"information_schema\.table_constraints",
    "key": r"__dataowl_has_null",
    "ts_aggregate": r"current_date\(\) AS today",
    "ts_gaps": r"__dataowl_gap_days",
    "ts_days": r"__dataowl_day\b",
    "compare": r"AS second_after_first",
}

COLUMN_ROWS = [
    ("customer_id", "string"),
    ("order_id", "bigint"),
    ("created_at", "timestamp"),
    ("Updated_At", "timestamp"),
    ("status", "string"),
]


def _column_row(position: int, name: str, data_type: str) -> dict[str, Any]:
    return {
        "column_name": name,
        "ordinal_position": position,
        "full_data_type": data_type,
        "data_type": data_type.upper(),
        "is_nullable": "YES",
        "comment": None,
    }


TABLE_ROW: dict[str, Any] = {
    "table_type": "MANAGED",
    "data_source_format": "DELTA",
    "table_owner": "team-x",
    "comment": None,
    "created": datetime(2024, 3, 12, 8, 14),
    "last_altered": datetime(2026, 9, 30, 3, 12),
}

KEY_ROW: dict[str, Any] = {
    "total_rows": 1000,
    "rows_with_null_key": 0,
    "distinct_keys": 1000,
    "duplicate_keys": 0,
    "rows_in_duplicate_keys": 0,
    "max_rows_per_key": 1,
    "median_rows_per_key": 1.0,
    "keys_with_1_row": 1000,
    "keys_with_2_10_rows": 0,
    "keys_with_11_100_rows": 0,
    "keys_with_over_100_rows": 0,
}

TS_AGGREGATE_ROW: dict[str, Any] = {
    "total_rows": 1000,
    "null_rows": 0,
    "min_value": datetime(2024, 1, 1, 0, 5),
    "max_value": datetime(2026, 10, 7, 23, 50),
    "future_values": 0,
    "distinct_dates": 1011,
    "today": TODAY,
}

TS_GAP_ROW: dict[str, Any] = {
    "min_gap_days": 1,
    "median_gap_days": 1.0,
    "mean_gap_days": 1.0,
    "max_gap_days": 1,
}

COMPARE_ROW: dict[str, Any] = {
    "second_after_first": 700,
    "second_equal_first": 300,
    "second_before_first": 0,
    "either_null": 0,
}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> FakeRunner:
    def get_runner(spark: object) -> FakeRunner:
        assert spark is None
        return fake_runner

    monkeypatch.setattr(dataowl, "get_runner", get_runner)
    return fake_runner


@pytest.fixture
def no_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_runner(spark: object) -> FakeRunner:
        raise AssertionError("get_runner must not be called")

    monkeypatch.setattr(dataowl, "get_runner", get_runner)


def _register_all(fake: FakeRunner, primary_key: list[dict[str, Any]] | None = None) -> None:
    fake.on(PATTERNS["tables"], [TABLE_ROW])
    fake.on(
        PATTERNS["columns"],
        [_column_row(i, name, data_type) for i, (name, data_type) in enumerate(COLUMN_ROWS)],
    )
    fake.on(PATTERNS["constraints"], [] if primary_key is None else primary_key)
    fake.on(PATTERNS["key"], [KEY_ROW])
    fake.on(PATTERNS["ts_aggregate"], [TS_AGGREGATE_ROW])
    fake.on(PATTERNS["ts_gaps"], [TS_GAP_ROW])
    fake.on(PATTERNS["ts_days"], [])
    fake.on(PATTERNS["compare"], [COMPARE_ROW])


def _kinds(fake: FakeRunner) -> list[str]:
    """Name of the pattern each executed query matches. Each query must match exactly one."""
    kinds = []
    for sql, _ in fake.queries:
        matches = [name for name, pattern in PATTERNS.items() if re.search(pattern, sql)]
        assert len(matches) == 1, (matches, sql)
        kinds.append(matches[0])
    return kinds


def _assert_facts_available(obj: Any) -> None:
    for name in type(obj).__dataclass_fields__:
        value = getattr(obj, name)
        if isinstance(value, Fact):
            assert value.available is True, f"{type(obj).__name__}.{name}: {value.reason}"


def test_only_key(fake: FakeRunner) -> None:
    _register_all(fake)

    analysis = analyze("main.sales.orders", key="customer_id")

    assert isinstance(analysis, ColumnAnalysis)
    assert analysis.table == TableRef("main", "sales", "orders")
    assert analysis.primary_key == Fact(None, source="metadata")
    assert [k.columns for k in analysis.keys] == [("customer_id",)]
    _assert_facts_available(analysis.keys[0])
    assert analysis.timestamps == ()
    assert analysis.comparison is None
    assert _kinds(fake) == ["tables", "columns", "constraints", "key"]
    assert_read_only(fake)


def test_only_timestamp(fake: FakeRunner) -> None:
    _register_all(fake)

    analysis = analyze("main.sales.orders", timestamp="CREATED_AT")

    assert analysis.primary_key is None
    assert analysis.keys == ()
    assert [t.column for t in analysis.timestamps] == ["created_at"]
    _assert_facts_available(analysis.timestamps[0])
    assert analysis.timestamps[0].days == 30
    assert analysis.comparison is None
    assert _kinds(fake) == ["tables", "columns", "ts_aggregate", "ts_gaps", "ts_days"]
    assert_read_only(fake)


def test_only_compare(fake: FakeRunner) -> None:
    _register_all(fake)

    analysis = analyze("main.sales.orders", compare=("created_at", "updated_at"))

    assert analysis.primary_key is None
    assert analysis.keys == ()
    assert analysis.timestamps == ()
    assert isinstance(analysis.comparison, ComparisonAnalysis)
    assert analysis.comparison.first_column == "created_at"
    assert analysis.comparison.second_column == "Updated_At"
    _assert_facts_available(analysis.comparison)
    assert analysis.comparison.second_after_first == Fact(700, source="exact")
    assert _kinds(fake) == ["tables", "columns", "compare"]
    assert_read_only(fake)


def test_all_three(fake: FakeRunner) -> None:
    _register_all(fake, primary_key=[{"constraint_name": "pk", "column_name": "order_id"}])

    analysis = analyze(
        "main.sales.orders",
        key=["customer_id", ("customer_id", "order_id")],
        timestamp=["created_at", "Updated_At"],
        compare=("created_at", "Updated_At"),
        days=7,
    )

    assert analysis.primary_key == Fact(("order_id",), source="metadata")
    assert [k.columns for k in analysis.keys] == [("customer_id",), ("customer_id", "order_id")]
    assert [t.column for t in analysis.timestamps] == ["created_at", "Updated_At"]
    for item in (*analysis.keys, *analysis.timestamps, analysis.comparison):
        _assert_facts_available(item)
    assert all(isinstance(k, KeyAnalysis) for k in analysis.keys)
    assert all(isinstance(t, TimestampAnalysis) for t in analysis.timestamps)
    assert [t.days for t in analysis.timestamps] == [7, 7]
    assert analysis.timestamps[0].rows_per_day.value is not None
    assert len(analysis.timestamps[0].rows_per_day.value) == 7
    assert _kinds(fake) == [
        "tables",
        "columns",
        "constraints",
        "key",
        "key",
        "ts_aggregate",
        "ts_gaps",
        "ts_days",
        "ts_aggregate",
        "ts_gaps",
        "ts_days",
        "compare",
    ]
    assert_read_only(fake)


def test_no_parameters_raises_without_runner(no_runner: None) -> None:
    with pytest.raises(ValueError, match="At least one of key, timestamp and compare"):
        analyze("main.sales.orders")


@pytest.mark.parametrize("days", [0, -1, True, 1.5])
def test_invalid_days_raises_without_runner(no_runner: None, days: Any) -> None:
    with pytest.raises(ValueError, match="days"):
        analyze("main.sales.orders", timestamp="created_at", days=days)


def test_invalid_table_name_raises_without_runner(no_runner: None) -> None:
    with pytest.raises(ValueError):
        analyze("sales.orders", key="customer_id")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"key": "missing"}, "does not exist"),
        ({"key": ["customer_id", ("order_id",)]}, "at least two columns"),
        ({"timestamp": "status"}, "has type string"),
        ({"timestamp": ["created_at", "missing"]}, "does not exist"),
        ({"compare": ("created_at", "missing")}, "does not exist"),
        ({"compare": ["created_at", "Updated_At"]}, "must be a tuple"),
        ({"key": "customer_id", "timestamp": "created_at", "compare": ("a", "b")}, "'a'"),
        ({"key": "customer_id", "timestamp": "status"}, "has type string"),
    ],
)
def test_invalid_column_raises_before_data_queries(
    fake: FakeRunner, kwargs: dict[str, Any], message: str
) -> None:
    _register_all(fake)

    with pytest.raises(ValueError, match=message):
        analyze("main.sales.orders", **kwargs)

    assert _kinds(fake) == ["tables", "columns"]


def test_table_not_found(fake: FakeRunner) -> None:
    fake.on(PATTERNS["tables"], [])
    _register_all(fake)

    with pytest.raises(TableNotFoundError):
        analyze("main.sales.missing", key="customer_id")

    assert _kinds(fake) == ["tables"]


def test_columns_error_raises_runtime_error(fake: FakeRunner) -> None:
    fake.on_error(PATTERNS["columns"], RuntimeError("PERMISSION_DENIED: no access\ntrace"))
    _register_all(fake)

    with pytest.raises(RuntimeError) as excinfo:
        analyze("main.sales.orders", key="customer_id")

    assert str(excinfo.value) == (
        "Cannot validate columns: schema not available (PERMISSION_DENIED: no access)"
    )
    assert _kinds(fake) == ["tables", "columns"]
    assert fake.schema_calls == []


def test_columns_without_rows_raises_runtime_error(fake: FakeRunner) -> None:
    fake.on(PATTERNS["columns"], [])
    _register_all(fake)

    with pytest.raises(RuntimeError, match=re.escape(f"({NO_COLUMNS})")):
        analyze("main.sales.orders", timestamp="created_at")


def test_primary_key_not_collected_without_key(fake: FakeRunner) -> None:
    _register_all(fake, primary_key=[{"constraint_name": "pk", "column_name": "order_id"}])

    analysis = analyze(
        "main.sales.orders", timestamp="created_at", compare=("created_at", "Updated_At")
    )

    assert analysis.primary_key is None
    assert "constraints" not in _kinds(fake)


def test_no_primary_key_declared(fake: FakeRunner) -> None:
    _register_all(fake, primary_key=[])

    analysis = analyze("main.sales.orders", key="order_id")

    assert analysis.primary_key is not None
    assert analysis.primary_key.available is True
    assert analysis.primary_key.value is None


def test_declared_composite_primary_key(fake: FakeRunner) -> None:
    rows = [
        {"constraint_name": "orders_pk", "column_name": "order_id"},
        {"constraint_name": "orders_pk", "column_name": "customer_id"},
    ]
    _register_all(fake, primary_key=rows)

    analysis = analyze("main.sales.orders", key=("customer_id", "order_id"))

    assert analysis.primary_key == Fact(("order_id", "customer_id"), source="metadata")


def test_primary_key_error_does_not_stop_analysis(fake: FakeRunner) -> None:
    fake.on_error(PATTERNS["constraints"], RuntimeError("no access"))
    _register_all(fake)

    analysis = analyze("main.sales.orders", key="order_id")

    assert analysis.primary_key == Fact.unavailable("metadata", "no access")
    _assert_facts_available(analysis.keys[0])


def test_to_dict_is_json_serializable(fake: FakeRunner) -> None:
    _register_all(fake, primary_key=[{"constraint_name": "pk", "column_name": "order_id"}])

    analysis = analyze(
        "main.sales.orders",
        key="customer_id",
        timestamp="created_at",
        compare=("created_at", "Updated_At"),
        days=3,
    )

    data = json.loads(json.dumps(analysis.to_dict()))
    assert data["table"] == {"catalog": "main", "schema": "sales", "table": "orders"}
    assert data["primary_key"]["value"] == ["order_id"]
    assert data["keys"][0]["columns"] == ["customer_id"]
    assert data["timestamps"][0]["rows_per_day"]["value"][0] == {"day": "2026-10-05", "rows": 0}
    assert data["comparison"]["second_after_first"]["value"] == 700


def test_to_dict_without_primary_key_and_comparison(fake: FakeRunner) -> None:
    _register_all(fake)

    data = json.loads(json.dumps(analyze("main.sales.orders", timestamp="created_at").to_dict()))

    assert data["primary_key"] is None
    assert data["keys"] == []
    assert data["comparison"] is None
