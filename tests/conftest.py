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
