from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only

from dataowl.collect.compare import NO_ROWS, collect_comparison, normalize_compare
from dataowl.identifiers import TableRef
from dataowl.model.analysis import ComparisonAnalysis
from dataowl.model.facts import Fact, to_jsonable
from dataowl.model.overview import ColumnInfo

REF = TableRef("main", "sales", "orders")

CREATED_AT = ColumnInfo("created_at", 0, "timestamp", True, None)
UPDATED_AT = ColumnInfo("Updated_At", 1, "timestamp", True, None)
ORDER_DATE = ColumnInfo("order_date", 2, "date", True, None)
LOADED_AT = ColumnInfo("loaded`at", 3, "timestamp_ntz", True, None)
STATUS = ColumnInfo("status", 4, "string", True, None)
COLUMNS = (CREATED_AT, UPDATED_AT, ORDER_DATE, LOADED_AT, STATUS)

PATTERN = r"AS second_after_first"
FIELDS = ("second_after_first", "second_equal_first", "second_before_first", "either_null")


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "second_after_first": 700,
        "second_equal_first": 250,
        "second_before_first": 3,
        "either_null": 47,
    }
    row.update(overrides)
    return row


def _assert_all_available(analysis: ComparisonAnalysis) -> None:
    for name in FIELDS:
        assert getattr(analysis, name).available is True, name


# normalize_compare


def test_normalize_keeps_order() -> None:
    assert normalize_compare(("created_at", "Updated_At"), COLUMNS) == (CREATED_AT, UPDATED_AT)
    assert normalize_compare(("Updated_At", "created_at"), COLUMNS) == (UPDATED_AT, CREATED_AT)


def test_normalize_uses_schema_names() -> None:
    first, second = normalize_compare(("CREATED_AT", "updated_at"), COLUMNS)

    assert first.name == "created_at"
    assert second.name == "Updated_At"


def test_normalize_accepts_different_types() -> None:
    assert normalize_compare(("order_date", "created_at"), COLUMNS) == (ORDER_DATE, CREATED_AT)
    assert normalize_compare(("loaded`at", "created_at"), COLUMNS) == (LOADED_AT, CREATED_AT)


def test_normalize_rejects_list() -> None:
    with pytest.raises(ValueError, match="must be a tuple"):
        normalize_compare(["created_at", "Updated_At"], COLUMNS)  # type: ignore[arg-type]


@pytest.mark.parametrize("compare", [("created_at",), ("created_at", "Updated_At", "order_date")])
def test_normalize_rejects_wrong_length(compare: Any) -> None:
    with pytest.raises(ValueError, match="exactly two columns"):
        normalize_compare(compare, COLUMNS)


@pytest.mark.parametrize("compare", [("created_at", 1), (None, "created_at")])
def test_normalize_rejects_non_string_elements(compare: Any) -> None:
    with pytest.raises(ValueError, match="must be column names"):
        normalize_compare(compare, COLUMNS)


@pytest.mark.parametrize("compare", [("created_at", "created_at"), ("created_at", "Created_AT")])
def test_normalize_rejects_same_column_twice(compare: tuple[str, str]) -> None:
    with pytest.raises(ValueError, match="twice"):
        normalize_compare(compare, COLUMNS)


def test_normalize_rejects_unknown_column() -> None:
    with pytest.raises(ValueError, match="'missing' does not exist"):
        normalize_compare(("created_at", "missing"), COLUMNS)


def test_normalize_rejects_string_column() -> None:
    with pytest.raises(ValueError) as excinfo:
        normalize_compare(("status", "created_at"), COLUMNS)

    assert str(excinfo.value) == (
        "timestamp column 'status' has type string; "
        "supported types are timestamp, timestamp_ntz and date"
    )


# collect_comparison


def test_all_four_outcomes(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [_row()])

    analysis = collect_comparison(fake_runner, REF, CREATED_AT, UPDATED_AT)

    _assert_all_available(analysis)
    assert analysis.first_column == "created_at"
    assert analysis.second_column == "Updated_At"
    assert analysis.first_data_type == "timestamp"
    assert analysis.second_data_type == "timestamp"
    assert analysis.second_after_first == Fact(700, source="exact")
    assert analysis.second_equal_first == Fact(250, source="exact")
    assert analysis.second_before_first == Fact(3, source="exact")
    assert analysis.either_null == Fact(47, source="exact")
    assert len(fake_runner.queries) == 1
    assert_read_only(fake_runner)


def test_sql_compares_second_with_first(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [_row()])

    collect_comparison(fake_runner, REF, CREATED_AT, UPDATED_AT)

    [(sql, params)] = fake_runner.queries
    assert params is None
    assert "`Updated_At` > `created_at`" in sql
    assert "`Updated_At` = `created_at`" in sql
    assert "`Updated_At` < `created_at`" in sql
    assert "`created_at` IS NULL OR `Updated_At` IS NULL" in sql
    assert "FROM `main`.`sales`.`orders`" in sql
    assert sql.count("COALESCE(SUM(CASE WHEN") == 4


def test_different_types_without_cast(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [_row()])

    analysis = collect_comparison(fake_runner, REF, ORDER_DATE, CREATED_AT)

    _assert_all_available(analysis)
    assert analysis.first_data_type == "date"
    assert analysis.second_data_type == "timestamp"
    [(sql, _)] = fake_runner.queries
    assert "CAST" not in sql.upper()
    assert len(fake_runner.queries) == 1


def test_column_names_are_quoted(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [_row()])

    analysis = collect_comparison(fake_runner, REF, LOADED_AT, CREATED_AT)

    _assert_all_available(analysis)
    [(sql, _)] = fake_runner.queries
    assert "`created_at` > `loaded``at`" in sql
    assert_read_only(fake_runner)


def test_error_makes_every_fact_unavailable(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(PATTERN, RuntimeError("PERMISSION_DENIED: no SELECT\ntrace"))

    analysis = collect_comparison(fake_runner, REF, CREATED_AT, UPDATED_AT)

    for name in FIELDS:
        assert getattr(analysis, name) == Fact.unavailable("exact", "PERMISSION_DENIED: no SELECT")
    assert analysis.first_column == "created_at"
    assert analysis.second_data_type == "timestamp"
    assert len(fake_runner.queries) == 1


def test_no_rows_makes_every_fact_unavailable(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [])

    analysis = collect_comparison(fake_runner, REF, CREATED_AT, UPDATED_AT)

    for name in FIELDS:
        assert getattr(analysis, name) == Fact.unavailable("exact", NO_ROWS)


@pytest.mark.parametrize("name", FIELDS)
@pytest.mark.parametrize("value", ["5", 5.0, True, None])
def test_unexpected_type_affects_only_that_fact(
    fake_runner: FakeRunner, name: str, value: Any
) -> None:
    fake_runner.on(PATTERN, [_row(**{name: value})])

    analysis = collect_comparison(fake_runner, REF, CREATED_AT, UPDATED_AT)

    assert getattr(analysis, name) == Fact.unavailable(
        "exact", f"Unexpected {name} type in comparison query result: {type(value).__name__}"
    )
    for other in set(FIELDS) - {name}:
        assert getattr(analysis, other).available is True


def test_analysis_is_json_serializable(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [_row()])

    analysis = collect_comparison(fake_runner, REF, CREATED_AT, UPDATED_AT)

    data = json.loads(json.dumps(to_jsonable(analysis)))
    assert data["first_column"] == "created_at"
    assert data["second_after_first"] == {
        "value": 700,
        "source": "exact",
        "available": True,
        "reason": None,
    }
