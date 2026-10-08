from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only

import dataowl
from dataowl import HistoryAnalysis, TableNotFoundError, history
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.history import RowStats

FIXTURES = Path(__file__).parent.parent / "fixtures"

PATTERNS = {
    "tables": r"information_schema\.tables",
    "time_zone": r"current_timezone\(\)",
    "history": r"^DESCRIBE HISTORY",
}

TABLE_ROW: dict[str, Any] = {
    "table_type": "MANAGED",
    "data_source_format": "DELTA",
    "table_owner": "team-x",
    "comment": None,
    "created": datetime(2024, 3, 12, 8, 14),
    "last_altered": datetime(2026, 9, 30, 3, 12),
}


def _history_rows() -> list[dict[str, Any]]:
    """Load the fixture and convert timestamps to datetime, as Spark returns them."""
    rows: list[dict[str, Any]] = json.loads((FIXTURES / "describe_history_table.json").read_text())
    for row in rows:
        row["timestamp"] = datetime.fromisoformat(row["timestamp"])
    return rows


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


def _register_all(fake: FakeRunner, table_type: str = "MANAGED") -> None:
    fake.on(PATTERNS["tables"], [{**TABLE_ROW, "table_type": table_type}])
    fake.on(PATTERNS["time_zone"], [{"time_zone": "Europe/Oslo"}])
    fake.on(PATTERNS["history"], _history_rows())


def _kinds(fake: FakeRunner) -> list[str]:
    """Name of the pattern each executed query matches. Each query must match exactly one."""
    kinds = []
    for sql, _ in fake.queries:
        matches = [name for name, pattern in PATTERNS.items() if re.search(pattern, sql)]
        assert len(matches) == 1, (matches, sql)
        kinds.append(matches[0])
    return kinds


def _facts(analysis: HistoryAnalysis) -> dict[str, Fact[Any]]:
    return {name: value for name, value in vars(analysis).items() if isinstance(value, Fact)}


def test_table(fake: FakeRunner) -> None:
    _register_all(fake)

    analysis = history("main.sales.orders")

    assert isinstance(analysis, HistoryAnalysis)
    assert analysis.table == TableRef("main", "sales", "orders")
    assert analysis.limit is None
    assert _kinds(fake) == ["tables", "time_zone", "history"]
    assert fake.queries[2][0] == "DESCRIBE HISTORY `main`.`sales`.`orders`"
    for name, fact in _facts(analysis).items():
        assert fact.available is True, name
    assert analysis.time_zone == Fact("Europe/Oslo", source="metadata")
    assert analysis.first_commit == Fact(datetime(2026, 9, 2, 3, 10), source="metadata")
    assert analysis.last_commit == Fact(datetime(2026, 9, 30, 3, 12, 5), source="metadata")
    assert analysis.num_commits == Fact(8, source="metadata")
    assert analysis.num_days == Fact(29, source="derived")
    assert analysis.row_stats.value is not None
    assert analysis.row_stats.value[0] == RowStats("WRITE", "inserted", 2, 2, 8198, 4099.0, 4211)
    json.dumps(analysis.to_dict())
    assert_read_only(fake)


def test_view(fake: FakeRunner) -> None:
    _register_all(fake, table_type="VIEW")

    analysis = history("main.sales.orders_v")

    assert _kinds(fake) == ["tables", "time_zone"]
    assert analysis.time_zone.available is True
    for name, fact in _facts(analysis).items():
        if name != "time_zone":
            assert fact == Fact.unavailable(fact.source, "Not available for views"), name
    assert_read_only(fake)


def test_limit(fake: FakeRunner) -> None:
    fake.on(PATTERNS["tables"], [TABLE_ROW])
    fake.on(PATTERNS["time_zone"], [{"time_zone": "Etc/UTC"}])
    fake.on(PATTERNS["history"], _history_rows()[:2])

    analysis = history("main.sales.orders", limit=2)

    assert _kinds(fake) == ["tables", "time_zone", "history"]
    assert fake.queries[2][0] == "DESCRIBE HISTORY `main`.`sales`.`orders` LIMIT 2"
    assert analysis.limit == 2
    assert analysis.num_commits == Fact(2, source="metadata")
    assert analysis.first_commit == Fact(datetime(2026, 9, 25, 3, 11, 40), source="metadata")
    assert_read_only(fake)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10"])
def test_invalid_limit_raises_before_spark(no_runner: None, limit: Any) -> None:
    with pytest.raises(ValueError, match="limit must be"):
        history("main.sales.orders", limit=limit)


def test_invalid_table_name_raises_before_spark(no_runner: None) -> None:
    with pytest.raises(ValueError):
        history("sales.orders")


def test_table_not_found(fake: FakeRunner) -> None:
    fake.on(PATTERNS["tables"], [])
    _register_all(fake)

    with pytest.raises(TableNotFoundError, match="not found or no access"):
        history("main.sales.missing")

    assert _kinds(fake) == ["tables"]


def test_catalog_not_found(fake: FakeRunner) -> None:
    fake.on_error(
        PATTERNS["tables"],
        RuntimeError(
            "[TABLE_OR_VIEW_NOT_FOUND] The table or view `dev`.`information_schema`.`tables` "
            "cannot be found.\nSQLSTATE: 42P01"
        ),
    )
    _register_all(fake)

    with pytest.raises(TableNotFoundError, match="information_schema"):
        history("dev.sales.orders")

    assert _kinds(fake) == ["tables"]


def test_describe_history_error(fake: FakeRunner) -> None:
    fake.on_error(
        PATTERNS["history"],
        RuntimeError("[DELTA_MISSING_DELTA_TABLE] not a Delta table\ntrace"),
    )
    _register_all(fake)

    analysis = history("main.sales.orders")

    assert _kinds(fake) == ["tables", "time_zone", "history"]
    assert analysis.time_zone.available is True
    for name, fact in _facts(analysis).items():
        if name != "time_zone":
            assert fact.available is False, name
            assert fact.reason == "[DELTA_MISSING_DELTA_TABLE] not a Delta table", name
    json.dumps(analysis.to_dict())
    assert_read_only(fake)


def test_invalid_metrics_keep_other_facts(fake: FakeRunner) -> None:
    rows = _history_rows()
    rows[2]["operationMetrics"] = {"numUpdatedRows": "1.5"}
    fake.on(PATTERNS["history"], rows)
    _register_all(fake)

    analysis = history("main.sales.orders")

    assert analysis.row_stats == Fact.unavailable(
        "derived",
        "Unexpected row format in DESCRIBE HISTORY: "
        "operationMetrics.numUpdatedRows is not a non-negative integer (str)",
    )
    assert analysis.num_commits == Fact(8, source="metadata")
    assert analysis.commits_per_hour.available is True
    assert analysis.operations.available is True


def test_time_zone_error_keeps_other_facts(fake: FakeRunner) -> None:
    fake.on_error(PATTERNS["time_zone"], RuntimeError("[UNSUPPORTED] no current_timezone"))
    _register_all(fake)

    analysis = history("main.sales.orders")

    assert analysis.time_zone == Fact.unavailable("metadata", "[UNSUPPORTED] no current_timezone")
    assert analysis.num_commits == Fact(8, source="metadata")


def test_spark_is_passed_to_get_runner(
    monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner
) -> None:
    spark = object()
    received = []

    def get_runner(given: object) -> FakeRunner:
        received.append(given)
        return fake_runner

    monkeypatch.setattr(dataowl, "get_runner", get_runner)
    _register_all(fake_runner)

    history("main.sales.orders", spark=spark)  # type: ignore[arg-type]

    assert received == [spark]
