from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only

from dataowl.collect.constraints import collect_primary_key
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact

REF = TableRef("Main", "Sales", "Orders")
PATTERN = r"information_schema\.table_constraints"


def _rows(*columns: str, constraint: str = "orders_pk") -> list[dict[str, Any]]:
    return [{"constraint_name": constraint, "column_name": column} for column in columns]


def test_single_column_primary_key(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, _rows("order_id"))

    fact = collect_primary_key(fake_runner, REF)

    assert fact.available is True
    assert fact == Fact(("order_id",), source="metadata")
    assert len(fake_runner.queries) == 1
    assert_read_only(fake_runner)


def test_composite_primary_key_keeps_query_order(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, _rows("region", "order_id", "line_no"))

    fact = collect_primary_key(fake_runner, REF)

    assert fact.available is True
    assert fact == Fact(("region", "order_id", "line_no"), source="metadata")
    assert_read_only(fake_runner)


def test_no_primary_key_is_available_with_none(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [])

    fact = collect_primary_key(fake_runner, REF)

    assert fact.available is True
    assert fact.value is None
    assert fact.source == "metadata"
    assert_read_only(fake_runner)


def test_access_error_is_unavailable(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(PATTERN, RuntimeError("PERMISSION_DENIED: no USE CATALOG\ndetails"))

    fact = collect_primary_key(fake_runner, REF)

    assert fact == Fact.unavailable("metadata", "PERMISSION_DENIED: no USE CATALOG")
    assert_read_only(fake_runner)


@pytest.mark.parametrize(
    "row",
    [
        {"constraint_name": "orders_pk"},
        {"column_name": "order_id"},
        {"constraint_name": "orders_pk", "column_name": None},
        {"constraint_name": "orders_pk", "column_name": 1},
        {"constraint_name": None, "column_name": "order_id"},
    ],
)
def test_unexpected_row_format_is_unavailable(fake_runner: FakeRunner, row: dict[str, Any]) -> None:
    fake_runner.on(PATTERN, [row])

    fact = collect_primary_key(fake_runner, REF)

    assert fact.available is False
    assert fact.source == "metadata"
    assert fact.reason is not None
    assert fact.reason.startswith("Unexpected row format in ")


def test_more_than_one_constraint_is_unavailable(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, _rows("order_id") + _rows("region", constraint="other_pk"))

    fact = collect_primary_key(fake_runner, REF)

    assert fact == Fact.unavailable(
        "metadata", "Primary key query returned columns from 2 constraints"
    )


def test_table_name_is_a_parameter(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [])

    collect_primary_key(fake_runner, REF)

    [(sql, params)] = fake_runner.queries
    assert params == {"schema": "sales", "table": "orders"}
    assert "`Main`.information_schema.table_constraints" in sql
    assert "`Main`.information_schema.key_column_usage" in sql
    assert "orders" not in sql.lower()
    assert "sales" not in sql.lower()
    assert_read_only(fake_runner)


def test_sql_filters_primary_key_and_orders_by_position(fake_runner: FakeRunner) -> None:
    fake_runner.on(PATTERN, [])

    collect_primary_key(fake_runner, REF)

    [(sql, _)] = fake_runner.queries
    assert "constraint_type = 'PRIMARY KEY'" in sql
    assert "ORDER BY kcu.ordinal_position" in sql
