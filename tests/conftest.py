"""Shared test fixtures."""

from __future__ import annotations

import re
from typing import Any

import pytest


class FakeRunner:
    """SqlRunner for tests. Responses are registered per regex pattern on the SQL text.

    The first registered matching pattern wins. SQL without a match raises AssertionError.
    """

    def __init__(self) -> None:
        self._responses: list[tuple[re.Pattern[str], list[dict[str, Any]] | Exception]] = []
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    def on(self, pattern: str, rows: list[dict[str, Any]]) -> None:
        self._responses.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), rows))

    def on_error(self, pattern: str, exc: Exception) -> None:
        self._responses.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), exc))

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        for pattern, response in self._responses:
            if pattern.search(sql):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"FakeRunner has no response registered for SQL: {sql}")


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()
