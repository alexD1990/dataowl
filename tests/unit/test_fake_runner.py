from __future__ import annotations

import pytest
from conftest import FakeRunner
from pyspark.sql.types import StringType, StructField, StructType

from dataowl.identifiers import TableRef

REF = TableRef("c", "s", "t")


def test_returns_registered_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE DETAIL", [{"numFiles": 3}])

    assert fake_runner.query("DESCRIBE DETAIL `c`.`s`.`t`") == [{"numFiles": 3}]


def test_pattern_is_case_insensitive_and_spans_lines(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"select.*from information_schema", [{"x": 1}])

    assert fake_runner.query("SELECT x\nFROM INFORMATION_SCHEMA.tables") == [{"x": 1}]


def test_first_registered_match_wins(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"COUNT", [{"n": 1}])
    fake_runner.on(r"SELECT", [{"n": 2}])

    assert fake_runner.query("SELECT COUNT(*) AS n FROM t") == [{"n": 1}]


def test_raises_registered_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(r"SHOW TBLPROPERTIES", PermissionError("denied"))

    with pytest.raises(PermissionError, match="denied"):
        fake_runner.query("SHOW TBLPROPERTIES t")


def test_unmatched_sql_raises_assertion_error(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"DESCRIBE", [])

    with pytest.raises(AssertionError, match="no response registered"):
        fake_runner.query("SELECT 1")


def test_records_queries_and_params(fake_runner: FakeRunner) -> None:
    fake_runner.on(r".", [])

    fake_runner.query("SELECT 1")
    fake_runner.query("SELECT :a", {"a": "x"})

    assert fake_runner.queries == [("SELECT 1", None), ("SELECT :a", {"a": "x"})]


def test_records_query_that_raises(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(r".", RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        fake_runner.query("SELECT 1")

    assert fake_runner.queries == [("SELECT 1", None)]


def test_schema_returns_registered_struct(fake_runner: FakeRunner) -> None:
    struct = StructType([StructField("a", StringType())])
    fake_runner.on_schema(struct)

    assert fake_runner.schema(REF) is struct
    assert fake_runner.schema_calls == [REF]


def test_schema_raises_registered_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_schema_error(PermissionError("denied"))

    with pytest.raises(PermissionError, match="denied"):
        fake_runner.schema(REF)
    assert fake_runner.schema_calls == [REF]


def test_schema_without_registration_raises_assertion_error(fake_runner: FakeRunner) -> None:
    with pytest.raises(AssertionError, match="no schema registered"):
        fake_runner.schema(REF)
