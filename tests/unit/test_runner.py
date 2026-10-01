from __future__ import annotations

from typing import Any

import pytest
from pyspark.sql import SparkSession

from dataowl.runner import SparkRunner, get_runner


class StubRow:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.recursive: bool | None = None

    def asDict(self, recursive: bool = False) -> dict[str, Any]:
        self.recursive = recursive
        return self._data


class StubDataFrame:
    def __init__(self, rows: list[StubRow]) -> None:
        self._rows = rows

    def collect(self) -> list[StubRow]:
        return self._rows


class StubSession:
    def __init__(self, rows: list[StubRow]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def sql(self, sql: str, args: dict[str, Any] | None = None) -> StubDataFrame:
        self.calls.append((sql, args))
        return StubDataFrame(self.rows)


def test_spark_runner_query_converts_rows_recursively() -> None:
    rows = [StubRow({"n": 1, "s": {"a": 2}}), StubRow({"n": 3, "s": {"a": 4}})]
    session = StubSession(rows)

    result = SparkRunner(session).query("SELECT :x", {"x": 1})  # type: ignore[arg-type]

    assert result == [{"n": 1, "s": {"a": 2}}, {"n": 3, "s": {"a": 4}}]
    assert all(row.recursive is True for row in rows)
    assert session.calls == [("SELECT :x", {"x": 1})]


def test_spark_runner_query_without_params() -> None:
    session = StubSession([])

    assert SparkRunner(session).query("SELECT 1") == []  # type: ignore[arg-type]
    assert session.calls == [("SELECT 1", None)]


def test_get_runner_uses_given_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise AssertionError("getActiveSession must not be called")

    monkeypatch.setattr(SparkSession, "getActiveSession", fail)
    session = StubSession([])

    runner = get_runner(session)  # type: ignore[arg-type]

    assert isinstance(runner, SparkRunner)
    assert runner._spark is session


def test_get_runner_uses_active_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = StubSession([])
    monkeypatch.setattr(SparkSession, "getActiveSession", lambda: session)

    runner = get_runner()

    assert runner._spark is session


def test_get_runner_without_session_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SparkSession, "getActiveSession", lambda: None)

    with pytest.raises(RuntimeError, match="spark="):
        get_runner()
