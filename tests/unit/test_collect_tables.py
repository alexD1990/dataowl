from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from conftest import FakeRunner

import dataowl
from dataowl.collect.tables import collect_table_info
from dataowl.errors import TableNotFoundError
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType

REF = TableRef("Main", "Sales", "Orders")
CREATED = datetime(2024, 3, 12, 8, 14)
ALTERED = datetime(2026, 9, 30, 3, 12)


def _row(table_type: str | None = "MANAGED", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "table_type": table_type,
        "data_source_format": "DELTA",
        "table_owner": "team-x",
        "comment": "Orders",
        "created": CREATED,
        "last_altered": ALTERED,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MANAGED", ObjectType.MANAGED),
        ("EXTERNAL", ObjectType.EXTERNAL),
        ("VIEW", ObjectType.VIEW),
        ("MATERIALIZED_VIEW", ObjectType.MATERIALIZED_VIEW),
        ("STREAMING_TABLE", ObjectType.STREAMING_TABLE),
        ("FOREIGN", ObjectType.FOREIGN),
    ],
)
def test_object_types(fake_runner: FakeRunner, raw: str, expected: ObjectType) -> None:
    fake_runner.on(r"information_schema\.tables", [_row(raw)])

    info = collect_table_info(fake_runner, REF)

    assert info.object_type == Fact(expected, source="metadata")
    assert info.table_type_raw == Fact(raw, source="metadata")


@pytest.mark.parametrize("raw", ["MANAGED_SHALLOW_CLONE", "SOMETHING_NEW", None])
def test_unknown_object_type_keeps_raw_value(fake_runner: FakeRunner, raw: str | None) -> None:
    fake_runner.on(r"information_schema\.tables", [_row(raw)])

    info = collect_table_info(fake_runner, REF)

    assert info.object_type == Fact(ObjectType.UNKNOWN, source="metadata")
    assert info.table_type_raw == Fact(raw, source="metadata")


def test_metadata_facts(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"information_schema\.tables", [_row()])

    info = collect_table_info(fake_runner, REF)

    assert info.format == Fact("DELTA", source="metadata")
    assert info.owner == Fact("team-x", source="metadata")
    assert info.comment == Fact("Orders", source="metadata")
    assert info.created == Fact(CREATED, source="metadata")
    assert info.last_altered == Fact(ALTERED, source="metadata")


def test_null_comment_is_available_with_none(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"information_schema\.tables", [_row(comment=None)])

    info = collect_table_info(fake_runner, REF)

    assert info.comment == Fact(None, source="metadata")


def test_missing_table_raises(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"information_schema\.tables", [])

    with pytest.raises(TableNotFoundError, match="not found or no access"):
        collect_table_info(fake_runner, REF)


def test_table_not_found_error_is_exported() -> None:
    assert dataowl.TableNotFoundError is TableNotFoundError


def test_query_error_makes_all_facts_unavailable(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(
        r"information_schema\.tables",
        PermissionError("[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG.\ndetails"),
    )

    info = collect_table_info(fake_runner, REF)

    expected = Fact.unavailable(
        "metadata", "[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG."
    )
    assert info.object_type == expected
    assert info.table_type_raw == expected
    assert info.format == expected
    assert info.owner == expected
    assert info.comment == expected
    assert info.created == expected
    assert info.last_altered == expected


def test_schema_and_table_are_params_not_sql(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"information_schema\.tables", [_row()])

    collect_table_info(fake_runner, REF)

    [(sql, params)] = fake_runner.queries
    assert params == {"schema": "sales", "table": "orders"}
    assert "sales" not in sql.lower()
    assert "orders" not in sql.lower()
    assert ":schema" in sql
    assert ":table" in sql
    assert "lower(" not in sql.lower()


def test_catalog_is_quoted_in_sql(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"information_schema\.tables", [_row()])

    collect_table_info(fake_runner, TableRef("my`cat", "s", "t"))

    [(sql, _)] = fake_runner.queries
    assert "FROM `my``cat`.information_schema.tables" in sql


def test_query_selects_only_needed_columns(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"information_schema\.tables", [_row()])

    collect_table_info(fake_runner, REF)

    [(sql, _)] = fake_runner.queries
    assert "*" not in sql
