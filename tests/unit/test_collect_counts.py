from __future__ import annotations

import pytest
from conftest import FakeRunner

from dataowl.collect.counts import collect_row_count
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType

REF = TableRef("main", "sales", "orders")
COUNT_SQL = r"SELECT COUNT\(\*\) AS n FROM"
UNAVAILABLE_TYPE: Fact[ObjectType] = Fact.unavailable("metadata", "Permission denied")


def _type(object_type: ObjectType) -> Fact[ObjectType]:
    return Fact(object_type, source="metadata")


@pytest.mark.parametrize("object_type", [ObjectType.MANAGED, ObjectType.EXTERNAL])
def test_table_is_counted(fake_runner: FakeRunner, object_type: ObjectType) -> None:
    fake_runner.on(COUNT_SQL, [{"n": 1284991}])

    result = collect_row_count(fake_runner, REF, _type(object_type))

    assert result == Fact(1284991, source="exact")
    assert fake_runner.queries == [("SELECT COUNT(*) AS n FROM `main`.`sales`.`orders`", None)]


@pytest.mark.parametrize(
    "object_type", [ObjectType.VIEW, ObjectType.MATERIALIZED_VIEW, ObjectType.FOREIGN]
)
def test_skipped_without_count_views(fake_runner: FakeRunner, object_type: ObjectType) -> None:
    result = collect_row_count(fake_runner, REF, _type(object_type))

    assert result == Fact.unavailable(
        "exact", f"Skipped for {object_type.value}; use count_views=True"
    )
    assert fake_runner.queries == []


@pytest.mark.parametrize(
    "object_type", [ObjectType.VIEW, ObjectType.MATERIALIZED_VIEW, ObjectType.FOREIGN]
)
def test_counted_with_count_views(fake_runner: FakeRunner, object_type: ObjectType) -> None:
    fake_runner.on(COUNT_SQL, [{"n": 42}])

    result = collect_row_count(fake_runner, REF, _type(object_type), count_views=True)

    assert result == Fact(42, source="exact")
    assert len(fake_runner.queries) == 1


def test_unavailable_object_type_skipped_without_count_views(fake_runner: FakeRunner) -> None:
    result = collect_row_count(fake_runner, REF, UNAVAILABLE_TYPE)

    assert result == Fact.unavailable("exact", "Skipped: object type unknown; use count_views=True")
    assert fake_runner.queries == []


def test_unavailable_object_type_counted_with_count_views(fake_runner: FakeRunner) -> None:
    fake_runner.on(COUNT_SQL, [{"n": 7}])

    result = collect_row_count(fake_runner, REF, UNAVAILABLE_TYPE, count_views=True)

    assert result == Fact(7, source="exact")


def test_unknown_object_type_is_counted(fake_runner: FakeRunner) -> None:
    fake_runner.on(COUNT_SQL, [{"n": 0}])

    result = collect_row_count(fake_runner, REF, _type(ObjectType.UNKNOWN))

    assert result == Fact(0, source="exact")
    assert len(fake_runner.queries) == 1


def test_query_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(COUNT_SQL, RuntimeError("[INSUFFICIENT_PERMISSIONS] no SELECT\nmore"))

    result = collect_row_count(fake_runner, REF, _type(ObjectType.MANAGED))

    assert result == Fact.unavailable("exact", "[INSUFFICIENT_PERMISSIONS] no SELECT")


def test_no_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(COUNT_SQL, [])

    result = collect_row_count(fake_runner, REF, _type(ObjectType.MANAGED))

    assert result == Fact.unavailable("exact", "COUNT(*) returned no rows")


@pytest.mark.parametrize("row", [{}, {"n": None}, {"n": "12"}])
def test_unexpected_result(fake_runner: FakeRunner, row: dict[str, object]) -> None:
    fake_runner.on(COUNT_SQL, [row])

    result = collect_row_count(fake_runner, REF, _type(ObjectType.MANAGED))

    assert not result.available
    assert result.reason is not None
    assert result.reason.startswith("Unexpected COUNT(*) result type")
