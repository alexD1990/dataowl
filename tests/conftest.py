"""Shared test fixtures."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest

from dataowl.identifiers import TableRef

if TYPE_CHECKING:
    from pyspark.sql.types import StructType


class FakeRunner:
    """SqlRunner for tests. Responses are registered per regex pattern on the SQL text.

    The first registered matching pattern wins. SQL without a match raises AssertionError.
    schema() returns the struct registered with on_schema, or raises the exception
    registered with on_schema_error.
    """

    def __init__(self) -> None:
        self._responses: list[tuple[re.Pattern[str], list[dict[str, Any]] | Exception]] = []
        self.queries: list[tuple[str, dict[str, Any] | None]] = []
        self._schema: StructType | Exception | None = None
        self.schema_calls: list[TableRef] = []

    def on(self, pattern: str, rows: list[dict[str, Any]]) -> None:
        self._responses.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), rows))

    def on_error(self, pattern: str, exc: Exception) -> None:
        self._responses.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), exc))

    def on_schema(self, struct: StructType) -> None:
        self._schema = struct

    def on_schema_error(self, exc: Exception) -> None:
        self._schema = exc

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        for pattern, response in self._responses:
            if pattern.search(sql):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"FakeRunner has no response registered for SQL: {sql}")

    def schema(self, table: TableRef) -> StructType:
        self.schema_calls.append(table)
        if self._schema is None:
            raise AssertionError(f"FakeRunner has no schema registered for {table.quoted()}")
        if isinstance(self._schema, Exception):
            raise self._schema
        return self._schema


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


_READ_ONLY_START = re.compile(r"(SELECT|WITH|DESCRIBE|SHOW)\b", re.IGNORECASE)
_QUOTED_IDENTIFIER = re.compile(r"`(?:[^`]|``)*`")
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|OPTIMIZE|VACUUM)\b|\bANALYZE\s+TABLE\b",
    re.IGNORECASE,
)


def _strip_leading_comments(sql: str) -> str:
    while True:
        sql = sql.lstrip()
        if sql.startswith("--"):
            newline = sql.find("\n")
            sql = "" if newline == -1 else sql[newline + 1 :]
        elif sql.startswith("/*"):
            end = sql.find("*/")
            sql = "" if end == -1 else sql[end + 2 :]
        else:
            return sql


def assert_read_only(fake: FakeRunner) -> None:
    """Assert that every executed query is read-only (principle 2).

    Each query must start with SELECT, WITH, DESCRIBE or SHOW after leading whitespace and
    comments, and must not contain a forbidden keyword as a whole word. Backtick-quoted
    identifiers are removed before the keyword search, so a table named `update` passes.
    """
    for sql, _ in fake.queries:
        assert _READ_ONLY_START.match(_strip_leading_comments(sql)), (
            f"Query does not start with a read-only keyword: {sql}"
        )
        match = _FORBIDDEN.search(_QUOTED_IDENTIFIER.sub(" ", sql))
        assert match is None, f"Query contains forbidden keyword {match.group(0)!r}: {sql}"


_JUDGEMENT = re.compile(r"\b(recommend|should|safe|suitable)\b|[✓⚠✗]", re.IGNORECASE)


def assert_no_judgement(text: str) -> None:
    """Assert that text contains no judgemental words or symbols (principle 1)."""
    match = _JUDGEMENT.search(text)
    assert match is None, f"Output contains judgemental term {match.group(0)!r}"
