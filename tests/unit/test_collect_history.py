from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only

from dataowl.collect.history import (
    collect_history_excerpt,
    count_operations,
    observation_window,
    operation_category,
    parse_commits,
)
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact, to_jsonable
from dataowl.model.history import CommitInfo
from dataowl.model.overview import HistoryInfo, ObjectType

FIXTURES = Path(__file__).parent.parent / "fixtures"
REF = TableRef("main", "sales", "orders")
HISTORY_SQL = r"DESCRIBE HISTORY"
MANAGED: Fact[ObjectType] = Fact(ObjectType.MANAGED, source="metadata")


def _history_rows() -> list[dict[str, Any]]:
    """Load the fixture and convert timestamps to datetime, as Spark returns them."""
    rows: list[dict[str, Any]] = json.loads((FIXTURES / "describe_history_table.json").read_text())
    for row in rows:
        row["timestamp"] = datetime.fromisoformat(row["timestamp"])
    return rows


def _row(
    version: int, timestamp: datetime, operation: str, mode: str | None = "Append"
) -> dict[str, Any]:
    """A row with every DESCRIBE HISTORY column, based on the first fixture row."""
    row = _history_rows()[0]
    row.update(version=version, timestamp=timestamp, operation=operation)
    row["operationParameters"] = {"mode": mode} if mode is not None else {}
    return row


def _commit(operation: str, mode: str | None = None, day: int = 1) -> CommitInfo:
    return CommitInfo(version=day, timestamp=datetime(2026, 9, day), operation=operation, mode=mode)


def _all_facts(info: HistoryInfo) -> list[Fact[Any]]:
    return [info.first_commit, info.last_commit, info.num_commits, info.operations]


# collect_history_excerpt


def test_mixed_operations(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows())

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    assert info == HistoryInfo(
        first_commit=Fact(datetime(2026, 9, 2, 3, 10, 0), source="metadata"),
        last_commit=Fact(datetime(2026, 9, 30, 3, 12, 5), source="metadata"),
        num_commits=Fact(8, source="metadata"),
        operations=Fact(
            {
                "WRITE": 2,
                "CREATE TABLE": 1,
                "DELETE": 1,
                "MERGE": 1,
                "OPTIMIZE": 1,
                "UPDATE": 1,
                "WRITE (overwrite)": 1,
            },
            source="metadata",
        ),
    )
    assert info.operations.value is not None
    assert list(info.operations.value)[:2] == ["WRITE", "CREATE TABLE"]
    assert fake_runner.queries == [("DESCRIBE HISTORY `main`.`sales`.`orders`", None)]
    assert_read_only(fake_runner)


def test_append_only(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        HISTORY_SQL,
        [_row(v, datetime(2026, 9, 1 + v, 3, 10), "WRITE", "Append") for v in range(3)],
    )

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    assert info.operations == Fact({"WRITE": 3}, source="metadata")
    assert info.num_commits == Fact(3, source="metadata")
    assert_read_only(fake_runner)


def test_overwrite_is_own_category(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        HISTORY_SQL,
        [
            _row(2, datetime(2026, 9, 3), "WRITE", "Overwrite"),
            _row(1, datetime(2026, 9, 2), "WRITE", "Overwrite"),
            _row(0, datetime(2026, 9, 1), "WRITE", "Append"),
        ],
    )

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    assert info.operations == Fact({"WRITE (overwrite)": 2, "WRITE": 1}, source="metadata")
    assert_read_only(fake_runner)


def test_view_runs_no_query(fake_runner: FakeRunner) -> None:
    view: Fact[ObjectType] = Fact(ObjectType.VIEW, source="metadata")

    info = collect_history_excerpt(fake_runner, REF, view)

    assert fake_runner.queries == []
    assert all(
        fact == Fact.unavailable("metadata", "Not available for views") for fact in _all_facts(info)
    )


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
    fake_runner.on(HISTORY_SQL, _history_rows())

    info = collect_history_excerpt(fake_runner, REF, object_type)

    assert len(fake_runner.queries) == 1
    assert info.num_commits == Fact(8, source="metadata")


def test_access_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(
        HISTORY_SQL,
        PermissionError("[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table.\nmore"),
    )

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    reason = "[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table."
    assert all(fact == Fact.unavailable("metadata", reason) for fact in _all_facts(info))
    assert_read_only(fake_runner)


def test_no_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, [])

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    assert all(
        fact == Fact.unavailable("metadata", "DESCRIBE HISTORY returned no rows")
        for fact in _all_facts(info)
    )


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"version": None}, "version has type NoneType"),
        ({"version": "7"}, "version has type str"),
        ({"timestamp": "2026-09-30T03:12:05"}, "timestamp has type str"),
        ({"operation": None}, "operation has type NoneType"),
        ({"operationParameters": "mode=Append"}, "operationParameters has type str"),
        ({"operationParameters": {"mode": 1}}, "operationParameters.mode has type int"),
    ],
)
def test_unexpected_row_format(
    fake_runner: FakeRunner, change: dict[str, Any], detail: str
) -> None:
    rows = _history_rows()
    rows[3].update(change)
    fake_runner.on(HISTORY_SQL, rows)

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    reason = f"Unexpected row format in DESCRIBE HISTORY: {detail}"
    assert all(fact == Fact.unavailable("metadata", reason) for fact in _all_facts(info))


@pytest.mark.parametrize("key", ["version", "timestamp", "operation"])
def test_missing_column(fake_runner: FakeRunner, key: str) -> None:
    rows = _history_rows()
    del rows[0][key]
    fake_runner.on(HISTORY_SQL, rows)

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    assert info.num_commits == Fact.unavailable(
        "metadata", f"Unexpected row format in DESCRIBE HISTORY: missing {key}"
    )


def test_window_uses_min_and_max_regardless_of_row_order(fake_runner: FakeRunner) -> None:
    rows = _history_rows()
    shuffled = [rows[3], rows[7], rows[0], rows[5], rows[1], rows[6], rows[2], rows[4]]
    fake_runner.on(HISTORY_SQL, shuffled)

    info = collect_history_excerpt(fake_runner, REF, MANAGED)

    assert info.first_commit == Fact(datetime(2026, 9, 2, 3, 10, 0), source="metadata")
    assert info.last_commit == Fact(datetime(2026, 9, 30, 3, 12, 5), source="metadata")


def test_only_selected_fields_reach_the_model(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows())

    info = collect_history_excerpt(fake_runner, REF, MANAGED)
    text = json.dumps(to_jsonable(info))

    assert [field.name for field in dataclasses.fields(CommitInfo)] == [
        "version",
        "timestamp",
        "operation",
        "mode",
    ]
    for value in ("etl-service@example.com", "orders_load", "4211", "WriteSerializable", "Append"):
        assert value not in text


# parse_commits


def test_parse_commits_keeps_four_fields() -> None:
    commits = parse_commits(_history_rows())

    assert len(commits) == 8
    assert commits[0] == CommitInfo(7, datetime(2026, 9, 30, 3, 12, 5), "WRITE", "Append")
    assert commits[-1] == CommitInfo(0, datetime(2026, 9, 2, 3, 10, 0), "CREATE TABLE", None)


@pytest.mark.parametrize(
    "parameters", [{"absent": True}, {"value": None}, {"value": {}}, {"value": {"mode": None}}]
)
def test_parse_commits_without_mode(parameters: dict[str, Any]) -> None:
    row = _history_rows()[0]
    if "absent" in parameters:
        del row["operationParameters"]
    else:
        row["operationParameters"] = parameters["value"]

    [commit] = parse_commits([row])

    assert commit.mode is None
    assert operation_category(commit) == "WRITE"


def test_parse_commits_rejects_bool_version() -> None:
    row = _history_rows()[0]
    row["version"] = True

    with pytest.raises(ValueError, match="version has type bool"):
        parse_commits([row])


def test_parse_commits_empty() -> None:
    assert parse_commits([]) == ()


# operation_category


@pytest.mark.parametrize(
    ("operation", "mode", "category"),
    [
        ("WRITE", "Append", "WRITE"),
        ("WRITE", "Overwrite", "WRITE (overwrite)"),
        ("WRITE", "overwrite", "WRITE (overwrite)"),
        ("WRITE", "OVERWRITE", "WRITE (overwrite)"),
        ("WRITE", None, "WRITE"),
        ("MERGE", None, "MERGE"),
        ("CREATE OR REPLACE TABLE AS SELECT", "Overwrite", "CREATE OR REPLACE TABLE AS SELECT"),
        ("STREAMING UPDATE", "Append", "STREAMING UPDATE"),
    ],
)
def test_operation_category(operation: str, mode: str | None, category: str) -> None:
    assert operation_category(_commit(operation, mode)) == category


# count_operations


def test_count_operations_orders_by_count_then_name() -> None:
    commits = [
        _commit("MERGE"),
        _commit("DELETE"),
        _commit("WRITE", "Append"),
        _commit("WRITE", "Overwrite"),
        _commit("WRITE", "Append"),
        _commit("UPDATE"),
        _commit("DELETE"),
    ]

    counts = count_operations(commits)

    assert list(counts.items()) == [
        ("DELETE", 2),
        ("WRITE", 2),
        ("MERGE", 1),
        ("UPDATE", 1),
        ("WRITE (overwrite)", 1),
    ]


def test_count_operations_empty() -> None:
    assert count_operations([]) == {}


# observation_window


def test_observation_window() -> None:
    commits = [_commit("WRITE", day=5), _commit("WRITE", day=2), _commit("WRITE", day=9)]

    assert observation_window(commits) == (datetime(2026, 9, 2), datetime(2026, 9, 9), 3)


def test_observation_window_single_commit() -> None:
    commit = _commit("WRITE", day=4)

    assert observation_window([commit]) == (commit.timestamp, commit.timestamp, 1)


def test_observation_window_empty_raises() -> None:
    with pytest.raises(ValueError):
        observation_window([])
