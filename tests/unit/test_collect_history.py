from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner, assert_no_judgement, assert_read_only

from dataowl.collect.history import (
    CommitsResult,
    build_history_analysis,
    collect_commits,
    collect_history_excerpt,
    collect_session_time_zone,
    commits_per_day,
    commits_per_hour,
    count_operations,
    observation_window,
    operation_category,
    parse_commits,
    parse_commits_with_metrics,
    parse_metrics,
    row_stats,
    validate_limit,
    window_days,
)
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact, to_jsonable
from dataowl.model.history import (
    HISTORY_NOTES,
    CommitInfo,
    CommitMetrics,
    HistoryAnalysis,
    HourCount,
    RowStats,
)
from dataowl.model.overview import HistoryInfo, ObjectType

FIXTURES = Path(__file__).parent.parent / "fixtures"
REF = TableRef("main", "sales", "orders")
HISTORY_SQL = r"DESCRIBE HISTORY"
MANAGED: Fact[ObjectType] = Fact(ObjectType.MANAGED, source="metadata")


def _history_rows(name: str = "describe_history_table.json") -> list[dict[str, Any]]:
    """Load a fixture and convert timestamps to datetime, as Spark returns them."""
    rows: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text())
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
        "metrics",
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


# parse_metrics


METRIC_FIELDS = [
    ("numOutputRows", "num_output_rows"),
    ("numTargetRowsInserted", "num_target_rows_inserted"),
    ("numTargetRowsUpdated", "num_target_rows_updated"),
    ("numTargetRowsDeleted", "num_target_rows_deleted"),
    ("numTargetRowsCopied", "num_target_rows_copied"),
    ("numDeletedRows", "num_deleted_rows"),
    ("numUpdatedRows", "num_updated_rows"),
    ("numCopiedRows", "num_copied_rows"),
    ("numAddedFiles", "num_added_files"),
    ("numRemovedFiles", "num_removed_files"),
]


def test_commit_metrics_fields() -> None:
    assert [field.name for field in dataclasses.fields(CommitMetrics)] == [
        name for _, name in METRIC_FIELDS
    ]
    assert all(value is None for value in dataclasses.astuple(CommitMetrics()))


@pytest.mark.parametrize(("key", "name"), METRIC_FIELDS)
def test_parse_metrics_reads_each_key(key: str, name: str) -> None:
    metrics = parse_metrics({key: "42"})

    assert getattr(metrics, name) == 42
    assert sum(value is not None for value in dataclasses.astuple(metrics)) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0), ("4211", 4211), ("007", 7), (0, 0), (12, 12), (None, None)],
)
def test_parse_metrics_valid_values(value: Any, expected: int | None) -> None:
    assert parse_metrics({"numOutputRows": value}).num_output_rows == expected


@pytest.mark.parametrize("value", [None, {}, {"numSourceRows": "909", "executionTimeMs": "1"}])
def test_parse_metrics_without_known_keys(value: Any) -> None:
    assert parse_metrics(value) == CommitMetrics()


@pytest.mark.parametrize(
    "value", ["-1", -1, "1.5", 1.5, "", " 1", "12 rows", "abc", "²", True, False, ["1"]]
)
def test_parse_metrics_invalid_value_names_key(value: Any) -> None:
    with pytest.raises(ValueError, match=r"operationMetrics\.numDeletedRows "):
        parse_metrics({"numDeletedRows": value})


@pytest.mark.parametrize("value", ["{}", ["numOutputRows", "1"], 1])
def test_parse_metrics_rejects_non_dict(value: Any) -> None:
    with pytest.raises(ValueError, match="operationMetrics has type"):
        parse_metrics(value)


def test_parse_metrics_invalid_value_is_not_in_message() -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_metrics({"numOutputRows": "secret-value"})

    assert "secret-value" not in str(excinfo.value)


# parse_commits_with_metrics


def _metrics_by_version(rows: list[dict[str, Any]]) -> dict[int, CommitMetrics]:
    return {commit.version: commit.metrics for commit in parse_commits_with_metrics(rows)}


def test_metrics_per_operation() -> None:
    metrics = _metrics_by_version(_history_rows())

    # WRITE (append): numFiles and numOutputBytes are not among the known keys.
    assert metrics[7] == CommitMetrics(num_output_rows=4211)
    assert metrics[3] == CommitMetrics(num_output_rows=3987)
    # WRITE (overwrite)
    assert metrics[1] == CommitMetrics(num_output_rows=1270110)
    assert metrics[4] == CommitMetrics(
        num_output_rows=4931,
        num_target_rows_inserted=812,
        num_target_rows_updated=97,
        num_target_rows_deleted=0,
        num_target_rows_copied=4022,
    )
    assert metrics[5] == CommitMetrics(
        num_updated_rows=1, num_copied_rows=3110, num_added_files=1, num_removed_files=1
    )
    assert metrics[6] == CommitMetrics(
        num_deleted_rows=15321, num_copied_rows=0, num_added_files=0, num_removed_files=2
    )
    assert metrics[2] == CommitMetrics(num_added_files=1, num_removed_files=6)
    # CREATE TABLE: operationMetrics is an empty dict.
    assert metrics[0] == CommitMetrics()


def test_metrics_unknown_operation_and_missing_metrics() -> None:
    commits = parse_commits_with_metrics(_history_rows("describe_history_metrics.json"))

    assert [(c.version, c.operation) for c in commits] == [
        (12, "FUTURE OPERATION"),
        (11, "SET TBLPROPERTIES"),
        (10, "ADD COLUMNS"),
    ]
    # Unknown keys are ignored, including one whose value is not a number.
    assert commits[0].metrics == CommitMetrics(num_output_rows=250)
    # operationMetrics null
    assert commits[1].metrics == CommitMetrics()
    # operationMetrics missing
    assert commits[2].metrics == CommitMetrics()


def test_parse_commits_with_metrics_keeps_commit_fields() -> None:
    rows = _history_rows()

    with_metrics = parse_commits_with_metrics(rows)
    without_metrics = parse_commits(rows)

    assert [dataclasses.replace(c, metrics=CommitMetrics()) for c in with_metrics] == list(
        without_metrics
    )
    assert all(commit.metrics == CommitMetrics() for commit in without_metrics)


def test_parse_commits_with_metrics_raises_for_commit_fields() -> None:
    row = _history_rows()[0]
    row["version"] = "7"

    with pytest.raises(ValueError, match="version has type str"):
        parse_commits_with_metrics([row])


def test_commits_keep_only_mode_from_operation_parameters() -> None:
    commits = parse_commits_with_metrics(
        _history_rows() + _history_rows("describe_history_metrics.json")
    )
    text = json.dumps(to_jsonable(commits))

    assert [field.name for field in dataclasses.fields(CommitInfo)] == [
        "version",
        "timestamp",
        "operation",
        "mode",
        "metrics",
    ]
    for value in ("id#0", "year#1", "predicate", "partitionBy", "enableChangeDataFeed", "channel"):
        assert value not in text


# collect_commits


def test_collect_commits(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows())

    result = collect_commits(fake_runner, REF, MANAGED)

    commits = result.commits
    assert commits.available is True
    assert commits.source == "metadata"
    assert commits.value is not None
    assert [commit.version for commit in commits.value] == [7, 6, 5, 4, 3, 2, 1, 0]
    assert commits.value[0].metrics == CommitMetrics(num_output_rows=4211)
    assert result.metrics_reason is None
    assert fake_runner.queries == [("DESCRIBE HISTORY `main`.`sales`.`orders`", None)]
    assert_read_only(fake_runner)


def test_collect_commits_keeps_query_order(fake_runner: FakeRunner) -> None:
    rows = _history_rows()
    fake_runner.on(HISTORY_SQL, [rows[3], rows[0], rows[7]])

    commits = collect_commits(fake_runner, REF, MANAGED).commits

    assert commits.value is not None
    assert [commit.version for commit in commits.value] == [4, 7, 0]


def test_collect_commits_view_runs_no_query(fake_runner: FakeRunner) -> None:
    view: Fact[ObjectType] = Fact(ObjectType.VIEW, source="metadata")

    result = collect_commits(fake_runner, REF, view, limit=5)

    assert result == CommitsResult(Fact.unavailable("metadata", "Not available for views"))
    assert fake_runner.queries == []


@pytest.mark.parametrize(
    "object_type",
    [
        Fact(ObjectType.UNKNOWN, source="metadata"),
        Fact.unavailable("metadata", "Permission denied"),
    ],
)
def test_collect_commits_runs_for_unknown_or_unavailable_object_type(
    fake_runner: FakeRunner, object_type: Fact[ObjectType]
) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows())

    commits = collect_commits(fake_runner, REF, object_type).commits

    assert len(fake_runner.queries) == 1
    assert commits.value is not None
    assert len(commits.value) == 8


def test_collect_commits_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(
        HISTORY_SQL,
        PermissionError("[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table.\nmore"),
    )

    result = collect_commits(fake_runner, REF, MANAGED)

    assert result == CommitsResult(
        Fact.unavailable(
            "metadata", "[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table."
        )
    )
    assert_read_only(fake_runner)


def test_collect_commits_no_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, [])

    result = collect_commits(fake_runner, REF, MANAGED)

    assert result == CommitsResult(
        Fact.unavailable("metadata", "DESCRIBE HISTORY returned no rows")
    )


def test_collect_commits_unexpected_commit_format(fake_runner: FakeRunner) -> None:
    rows = _history_rows()
    rows[3]["version"] = None
    rows[5]["operationMetrics"] = {"numOutputRows": "-1"}
    fake_runner.on(HISTORY_SQL, rows)

    result = collect_commits(fake_runner, REF, MANAGED)

    assert result == CommitsResult(
        Fact.unavailable(
            "metadata", "Unexpected row format in DESCRIBE HISTORY: version has type NoneType"
        )
    )


@pytest.mark.parametrize(
    ("metrics", "detail"),
    [
        ("numOutputRows=1", "operationMetrics has type str"),
        (
            {"numOutputRows": "-1"},
            "operationMetrics.numOutputRows is not a non-negative integer (str)",
        ),
    ],
)
def test_collect_commits_unexpected_metrics_keep_commits(
    fake_runner: FakeRunner, metrics: Any, detail: str
) -> None:
    rows = _history_rows()
    rows[3]["operationMetrics"] = metrics
    fake_runner.on(HISTORY_SQL, rows)

    result = collect_commits(fake_runner, REF, MANAGED)

    assert result.commits == Fact(parse_commits(_history_rows()), source="metadata")
    assert result.commits.value is not None
    assert all(commit.metrics == CommitMetrics() for commit in result.commits.value)
    assert result.metrics_reason == f"Unexpected row format in DESCRIBE HISTORY: {detail}"


def test_invalid_metrics_do_not_affect_history_excerpt(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows())
    expected = collect_history_excerpt(fake_runner, REF, MANAGED)

    rows = _history_rows()
    rows[0]["operationMetrics"] = {"numOutputRows": "-1"}
    rows[3]["operationMetrics"] = "not a dict"
    broken = FakeRunner()
    broken.on(HISTORY_SQL, rows)

    assert collect_history_excerpt(broken, REF, MANAGED) == expected
    assert expected.num_commits == Fact(8, source="metadata")
    result = collect_commits(broken, REF, MANAGED)
    assert result.commits.available is True
    assert result.metrics_reason is not None
    assert_read_only(broken)


def test_collect_commits_with_limit(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows()[:3])

    result = collect_commits(fake_runner, REF, MANAGED, limit=3)

    assert fake_runner.queries == [("DESCRIBE HISTORY `main`.`sales`.`orders` LIMIT 3", None)]
    assert result.commits.value is not None
    assert len(result.commits.value) == 3
    assert_read_only(fake_runner)


def test_collect_commits_without_limit_has_no_limit(fake_runner: FakeRunner) -> None:
    fake_runner.on(HISTORY_SQL, _history_rows())

    collect_commits(fake_runner, REF, MANAGED)
    collect_history_excerpt(fake_runner, REF, MANAGED)

    assert all("LIMIT" not in sql.upper() for sql, _ in fake_runner.queries)


@pytest.mark.parametrize("limit", [0, -1, True, 2.0, "5"])
def test_collect_commits_invalid_limit_runs_no_query(fake_runner: FakeRunner, limit: Any) -> None:
    with pytest.raises(ValueError, match="limit must be"):
        collect_commits(fake_runner, REF, MANAGED, limit=limit)

    assert fake_runner.queries == []


# validate_limit


@pytest.mark.parametrize("limit", [None, 1, 500])
def test_validate_limit_valid(limit: int | None) -> None:
    assert validate_limit(limit) == limit


@pytest.mark.parametrize(
    ("limit", "message"),
    [
        (0, "limit must be at least 1, got 0"),
        (-3, "limit must be at least 1, got -3"),
        (True, "limit must be an integer, got bool"),
        (2.0, "limit must be an integer, got float"),
        ("5", "limit must be an integer, got str"),
    ],
)
def test_validate_limit_invalid(limit: Any, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        validate_limit(limit)


# collect_session_time_zone

TZ_SQL = r"current_timezone\(\)"


def test_session_time_zone(fake_runner: FakeRunner) -> None:
    fake_runner.on(TZ_SQL, [{"time_zone": "Europe/Oslo"}])

    assert collect_session_time_zone(fake_runner) == Fact("Europe/Oslo", source="metadata")
    assert fake_runner.queries == [("SELECT current_timezone() AS time_zone", None)]
    assert_read_only(fake_runner)


def test_session_time_zone_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(TZ_SQL, RuntimeError("[UNSUPPORTED] not here\ntrace"))

    assert collect_session_time_zone(fake_runner) == Fact.unavailable(
        "metadata", "[UNSUPPORTED] not here"
    )


def test_session_time_zone_no_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(TZ_SQL, [])

    assert collect_session_time_zone(fake_runner) == Fact.unavailable(
        "metadata", "current_timezone() returned no rows"
    )


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({"time_zone": 1}, "time_zone has type int"),
        ({"time_zone": None}, "time_zone has type NoneType"),
        ({"tz": "UTC"}, "missing time_zone"),
    ],
)
def test_session_time_zone_unexpected_format(
    fake_runner: FakeRunner, row: dict[str, Any], detail: str
) -> None:
    fake_runner.on(TZ_SQL, [row])

    assert collect_session_time_zone(fake_runner) == Fact.unavailable(
        "metadata", f"Unexpected row format in current_timezone(): {detail}"
    )


# window_days, commits_per_day, commits_per_hour


def _at(timestamp: datetime, operation: str = "WRITE", **metrics: int | None) -> CommitInfo:
    return CommitInfo(
        version=0,
        timestamp=timestamp,
        operation=operation,
        mode=None,
        metrics=CommitMetrics(**metrics),
    )


def test_window_days_same_day() -> None:
    assert window_days(datetime(2026, 9, 2, 0, 1), datetime(2026, 9, 2, 23, 59)) == 1


def test_window_days_counts_calendar_days() -> None:
    assert window_days(datetime(2026, 9, 2, 3, 10), datetime(2026, 9, 30, 3, 12)) == 29
    # 23:59 to 00:01 the next day is two calendar days.
    assert window_days(datetime(2026, 9, 1, 23, 59), datetime(2026, 9, 2, 0, 1)) == 2


def test_window_days_across_month_end() -> None:
    assert window_days(datetime(2026, 9, 28, 12), datetime(2026, 10, 3, 1)) == 6


def test_commits_per_day_with_empty_days_and_several_commits_per_day() -> None:
    commits = [
        _at(datetime(2026, 9, 30, 23, 0)),
        _at(datetime(2026, 9, 28, 3, 0)),
        _at(datetime(2026, 10, 2, 1, 0)),
        _at(datetime(2026, 9, 28, 15, 0)),
        _at(datetime(2026, 9, 28, 0, 0)),
    ]

    # 2026-09-28 to 2026-10-02
    assert commits_per_day(commits) == (3, 0, 1, 0, 1)


def test_commits_per_day_single_commit_and_empty() -> None:
    assert commits_per_day([_at(datetime(2026, 9, 28, 3))]) == (1,)
    assert commits_per_day([]) == ()


def test_commits_per_hour() -> None:
    commits = [
        _at(datetime(2026, 9, 28, 3, 10)),
        _at(datetime(2026, 9, 29, 3, 59)),
        _at(datetime(2026, 9, 29, 0, 0)),
        _at(datetime(2026, 9, 30, 23, 59)),
    ]

    hours = commits_per_hour(commits)

    assert len(hours) == 24
    assert [h.hour for h in hours] == list(range(24))
    assert {h.hour: h.commits for h in hours if h.commits} == {0: 1, 3: 2, 23: 1}
    assert sum(h.commits for h in hours) == 4


def test_commits_per_hour_empty() -> None:
    assert commits_per_hour([]) == tuple(HourCount(hour, 0) for hour in range(24))


# row_stats


def test_row_stats_from_fixture() -> None:
    stats = row_stats(parse_commits_with_metrics(_history_rows()))

    assert stats == (
        RowStats("WRITE", "inserted", 2, 2, 8198, 4099.0, 4211),
        RowStats("DELETE", "deleted", 1, 1, 15321, 15321.0, 15321),
        RowStats("MERGE", "inserted", 1, 1, 812, 812.0, 812),
        RowStats("MERGE", "updated", 1, 1, 97, 97.0, 97),
        RowStats("MERGE", "deleted", 1, 1, 0, 0.0, 0),
        RowStats("UPDATE", "updated", 1, 1, 1, 1.0, 1),
        RowStats("WRITE (overwrite)", "inserted", 1, 1, 1270110, 1270110.0, 1270110),
    )
    # CREATE TABLE and OPTIMIZE give no rows.
    assert {s.operation for s in stats}.isdisjoint({"CREATE TABLE", "OPTIMIZE"})


def test_row_stats_as_select_missing_metrics_and_category_without_metric() -> None:
    commits = [
        _at(datetime(2026, 9, 1), "CREATE OR REPLACE TABLE AS SELECT", num_output_rows=100),
        _at(datetime(2026, 9, 2), "CREATE OR REPLACE TABLE AS SELECT", num_output_rows=300),
        _at(datetime(2026, 9, 3), "CREATE OR REPLACE TABLE AS SELECT"),
        _at(datetime(2026, 9, 4), "create table as select", num_output_rows=5),
        _at(datetime(2026, 9, 5), "DELETE"),
        _at(datetime(2026, 9, 6), "DELETE", num_output_rows=9),
        _at(datetime(2026, 9, 7), "STREAMING UPDATE", num_output_rows=7),
    ]

    stats = row_stats(commits)

    assert stats == (
        RowStats("CREATE OR REPLACE TABLE AS SELECT", "inserted", 3, 2, 400, 200.0, 300),
        RowStats("DELETE", "deleted", 2, 0, None, None, None),
        RowStats("create table as select", "inserted", 1, 1, 5, 5.0, 5),
    )


def test_row_stats_odd_median_and_empty() -> None:
    commits = [
        _at(datetime(2026, 9, day), "UPDATE", num_updated_rows=rows)
        for day, rows in [(1, 5), (2, 1), (3, 9)]
    ]

    assert row_stats(commits) == (RowStats("UPDATE", "updated", 3, 3, 15, 5.0, 9),)
    assert row_stats([]) == ()


# build_history_analysis

UTC: Fact[str] = Fact("Etc/UTC", source="metadata")


def _analysis(
    commits: Fact[tuple[CommitInfo, ...]] | None = None,
    metrics_reason: str | None = None,
    time_zone: Fact[str] = UTC,
) -> HistoryAnalysis:
    if commits is None:
        commits = Fact(parse_commits_with_metrics(_history_rows()), source="metadata")
    return build_history_analysis(REF, None, time_zone, commits, metrics_reason)


def _analysis_facts(analysis: HistoryAnalysis) -> dict[str, Fact[Any]]:
    return {
        field.name: getattr(analysis, field.name)
        for field in dataclasses.fields(analysis)
        if isinstance(getattr(analysis, field.name), Fact) and field.name != "time_zone"
    }


def test_build_history_analysis() -> None:
    analysis = _analysis()

    hours = tuple(HourCount(hour, 8 if hour == 3 else 0) for hour in range(24))
    assert analysis == HistoryAnalysis(
        table=REF,
        limit=None,
        time_zone=UTC,
        first_commit=Fact(datetime(2026, 9, 2, 3, 10, 0), source="metadata"),
        last_commit=Fact(datetime(2026, 9, 30, 3, 12, 5), source="metadata"),
        num_commits=Fact(8, source="metadata"),
        num_days=Fact(29, source="derived"),
        # 8 days with one commit and 21 days without.
        commits_per_day_median=Fact(0.0, source="derived"),
        commits_per_day_min=Fact(0, source="derived"),
        commits_per_day_max=Fact(1, source="derived"),
        commits_per_hour=Fact(hours, source="derived"),
        operations=Fact(count_operations(parse_commits(_history_rows())), source="metadata"),
        row_stats=Fact(row_stats(parse_commits_with_metrics(_history_rows())), source="derived"),
        notes=HISTORY_NOTES,
    )
    assert all(fact.available for fact in _analysis_facts(analysis).values())


def test_build_history_analysis_per_day_statistics() -> None:
    commits = (
        _at(datetime(2026, 9, 1, 3)),
        _at(datetime(2026, 9, 1, 4)),
        _at(datetime(2026, 9, 1, 5)),
        _at(datetime(2026, 9, 2, 3)),
        _at(datetime(2026, 9, 4, 3)),
    )

    analysis = _analysis(Fact(commits, source="metadata"))

    # Per day: 3, 1, 0, 1
    assert analysis.num_days == Fact(4, source="derived")
    assert analysis.commits_per_day_median == Fact(1.0, source="derived")
    assert analysis.commits_per_day_min == Fact(0, source="derived")
    assert analysis.commits_per_day_max == Fact(3, source="derived")


def test_build_history_analysis_commits_unavailable() -> None:
    analysis = _analysis(Fact.unavailable("metadata", "Not available for views"))

    assert analysis.time_zone == UTC
    for name, fact in _analysis_facts(analysis).items():
        assert fact.available is False, name
        assert fact.reason == "Not available for views", name
    assert analysis.num_days.source == "derived"
    assert analysis.operations.source == "metadata"


def test_build_history_analysis_empty_commits() -> None:
    analysis = _analysis(Fact((), source="metadata"))

    assert analysis.num_commits == Fact.unavailable("metadata", "DESCRIBE HISTORY returned no rows")


def test_build_history_analysis_metrics_reason() -> None:
    reason = "Unexpected row format in DESCRIBE HISTORY: operationMetrics has type str"
    commits = Fact(parse_commits(_history_rows()), source="metadata")

    analysis = _analysis(commits, metrics_reason=reason)

    assert analysis.row_stats == Fact.unavailable("derived", reason)
    facts = _analysis_facts(analysis)
    del facts["row_stats"]
    assert all(fact.available for fact in facts.values())
    assert analysis.num_days == Fact(29, source="derived")
    assert analysis.operations.value is not None
    assert analysis.operations.value["MERGE"] == 1


def test_build_history_analysis_time_zone_unavailable() -> None:
    time_zone: Fact[str] = Fact.unavailable("metadata", "[UNSUPPORTED] not here")

    analysis = _analysis(time_zone=time_zone)

    assert analysis.time_zone == time_zone
    assert all(fact.available for fact in _analysis_facts(analysis).values())


def test_history_analysis_to_dict_is_json() -> None:
    analysis = _analysis()

    data = analysis.to_dict()
    text = json.dumps(to_jsonable(analysis))

    assert json.loads(text) == data
    assert data["first_commit"]["value"] == "2026-09-02T03:10:00"
    assert data["commits_per_hour"]["value"][3] == {"hour": 3, "commits": 8}
    assert data["row_stats"]["value"][0] == {
        "operation": "WRITE",
        "kind": "inserted",
        "commits": 2,
        "commits_with_metric": 2,
        "sum": 8198,
        "median": 4099.0,
        "max": 4211,
    }
    json.dumps(_analysis(Fact.unavailable("metadata", "x")).to_dict())


def test_history_notes_are_facts_only() -> None:
    assert HISTORY_NOTES == (
        "history is limited by delta.logRetentionDuration; counts cover the window above",
        "rows replaced by WRITE (overwrite) are not counted as deleted",
        "the oldest day in the window may be incomplete",
    )
    for note in _analysis().notes:
        assert_no_judgement(note)
