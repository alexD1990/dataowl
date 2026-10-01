from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner

from dataowl.collect.detail import collect_detail
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import DetailInfo, ObjectType

FIXTURES = Path(__file__).parent.parent / "fixtures"
REF = TableRef("main", "sales", "orders")
MANAGED: Fact[ObjectType] = Fact(ObjectType.MANAGED, source="metadata")


def _detail_row(**overrides: Any) -> dict[str, Any]:
    """Load the fixture and convert timestamps to datetime, as Spark returns them."""
    [row] = json.loads((FIXTURES / "describe_detail_table.json").read_text())
    for key in ("createdAt", "lastModified"):
        row[key] = datetime.fromisoformat(row[key])
    row.update(overrides)
    return row


def _all_facts(info: DetailInfo) -> list[Fact[Any]]:
    return [
        info.format,
        info.size_bytes,
        info.num_files,
        info.avg_file_size_bytes,
        info.created,
        info.last_modified,
        info.partition_columns,
        info.clustering_columns,
    ]


def test_regular_table(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [_detail_row()])

    info = collect_detail(fake_runner, REF, MANAGED)

    assert info.format == Fact("delta", source="metadata")
    assert info.size_bytes == Fact(88290099, source="metadata")
    assert info.num_files == Fact(12, source="metadata")
    assert info.avg_file_size_bytes == Fact(88290099 / 12, source="derived")
    assert info.created == Fact(datetime(2024, 3, 12, 8, 14), source="metadata")
    assert info.last_modified == Fact(datetime(2026, 9, 30, 3, 12), source="metadata")
    assert info.partition_columns == Fact(("year",), source="metadata")
    assert info.clustering_columns == Fact((), source="metadata")
    assert fake_runner.queries == [("DESCRIBE DETAIL `main`.`sales`.`orders`", None)]


def test_table_without_partitioning(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [_detail_row(partitionColumns=[])])

    info = collect_detail(fake_runner, REF, MANAGED)

    assert info.partition_columns == Fact((), source="metadata")


def test_view_runs_no_query(fake_runner: FakeRunner) -> None:
    view: Fact[ObjectType] = Fact(ObjectType.VIEW, source="metadata")

    info = collect_detail(fake_runner, REF, view)

    assert fake_runner.queries == []
    assert info.size_bytes == Fact.unavailable("metadata", "Not available for views")
    assert info.avg_file_size_bytes == Fact.unavailable("derived", "Not available for views")
    assert all(fact.reason == "Not available for views" for fact in _all_facts(info))


@pytest.mark.parametrize(
    "object_type",
    [
        Fact(ObjectType.UNKNOWN, source="metadata"),
        Fact.unavailable("metadata", "Permission denied"),
    ],
)
def test_runs_for_unknown_or_unavailable_object_type(
    fake_runner: FakeRunner, object_type: Fact[ObjectType]
) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [_detail_row()])

    info = collect_detail(fake_runner, REF, object_type)

    assert len(fake_runner.queries) == 1
    assert info.num_files == Fact(12, source="metadata")


def test_query_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(
        r"DESCRIBE DETAIL",
        RuntimeError("[DELTA_TABLE_ONLY_OPERATION] main.sales.orders is not a Delta table.\nmore"),
    )

    info = collect_detail(fake_runner, REF, MANAGED)

    reason = "[DELTA_TABLE_ONLY_OPERATION] main.sales.orders is not a Delta table."
    assert info.size_bytes == Fact.unavailable("metadata", reason)
    assert info.avg_file_size_bytes == Fact.unavailable("derived", reason)
    assert all(not fact.available and fact.reason == reason for fact in _all_facts(info))


def test_missing_fields(fake_runner: FakeRunner) -> None:
    row = _detail_row()
    del row["clusteringColumns"]
    del row["sizeInBytes"]
    fake_runner.on(r"DESCRIBE DETAIL", [row])

    info = collect_detail(fake_runner, REF, MANAGED)

    missing = "Not present in DESCRIBE DETAIL output"
    assert info.clustering_columns == Fact.unavailable("metadata", missing)
    assert info.size_bytes == Fact.unavailable("metadata", missing)
    assert info.avg_file_size_bytes == Fact.unavailable("derived", missing)
    assert info.num_files == Fact(12, source="metadata")


def test_zero_files(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [_detail_row(numFiles=0, sizeInBytes=0)])

    info = collect_detail(fake_runner, REF, MANAGED)

    assert info.num_files == Fact(0, source="metadata")
    assert info.avg_file_size_bytes == Fact.unavailable("derived", "No files")


def test_null_input_to_average(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [_detail_row(sizeInBytes=None)])

    info = collect_detail(fake_runner, REF, MANAGED)

    assert info.size_bytes == Fact(None, source="metadata")
    assert info.avg_file_size_bytes == Fact.unavailable("derived", "Input value is null")


def test_no_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [])

    info = collect_detail(fake_runner, REF, MANAGED)

    assert all(fact.reason == "DESCRIBE DETAIL returned no rows" for fact in _all_facts(info))
