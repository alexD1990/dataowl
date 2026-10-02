"""Excerpt of the Delta history from DESCRIBE HISTORY."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, TypeVar

from dataowl.collect import NOT_FOR_VIEWS, is_view, safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.history import CommitInfo
from dataowl.model.overview import HistoryInfo, ObjectType
from dataowl.runner import SqlRunner

T = TypeVar("T")

NO_ROWS = "DESCRIBE HISTORY returned no rows"

_OVERWRITE_CATEGORY = "WRITE (overwrite)"


def collect_history_excerpt(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType]
) -> HistoryInfo:
    """Run DESCRIBE HISTORY, except for views, and summarize the commits.

    It also runs when the object type is UNKNOWN or unavailable. DESCRIBE HISTORY returns
    every history column; only version, timestamp, operation and operationParameters.mode
    are kept. Errors, no rows and an unexpected row format make every fact unavailable.
    """
    if is_view(object_type):
        return _unavailable(NOT_FOR_VIEWS)

    result = safe_query(runner, f"DESCRIBE HISTORY {ref.quoted()}")
    if isinstance(result, Exception):
        return _unavailable(short_reason(result))
    if not result:
        return _unavailable(NO_ROWS)

    try:
        commits = parse_commits(result)
    except ValueError as exc:
        return _unavailable(f"Unexpected row format in DESCRIBE HISTORY: {exc}")

    first, last, num_commits = observation_window(commits)
    return HistoryInfo(
        first_commit=Fact(first, source="metadata"),
        last_commit=Fact(last, source="metadata"),
        num_commits=Fact(num_commits, source="metadata"),
        operations=Fact(count_operations(commits), source="metadata"),
    )


def parse_commits(rows: list[dict[str, Any]]) -> tuple[CommitInfo, ...]:
    """Convert DESCRIBE HISTORY rows to commits. Other columns are ignored.

    Raises ValueError if version, timestamp or operation is missing or has an unexpected
    type, or if operationParameters or its mode has an unexpected type. A missing or None
    operationParameters, or one without mode, gives mode None.
    """
    return tuple(_commit(row) for row in rows)


def operation_category(commit: CommitInfo) -> str:
    """The operation, except that WRITE with mode Overwrite (any case) is 'WRITE (overwrite)'."""
    if commit.operation == "WRITE" and (commit.mode or "").lower() == "overwrite":
        return _OVERWRITE_CATEGORY
    return commit.operation


def count_operations(commits: Iterable[CommitInfo]) -> dict[str, int]:
    """Number of commits per operation category.

    Ordered by count descending, then by category name.
    """
    counts = Counter(operation_category(commit) for commit in commits)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def observation_window(commits: Sequence[CommitInfo]) -> tuple[datetime, datetime, int]:
    """Oldest and newest commit timestamp, and the number of commits.

    The row order does not matter. Raises ValueError for an empty sequence.
    """
    if not commits:
        raise ValueError("no commits")
    timestamps = [commit.timestamp for commit in commits]
    return min(timestamps), max(timestamps), len(commits)


def _commit(row: dict[str, Any]) -> CommitInfo:
    version = _get(row, "version", int)
    timestamp = _get(row, "timestamp", datetime)
    operation = _get(row, "operation", str)
    parameters = row.get("operationParameters")
    if parameters is None:
        mode = None
    elif isinstance(parameters, dict):
        mode = parameters.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise ValueError(f"operationParameters.mode has type {type(mode).__name__}")
    else:
        raise ValueError(f"operationParameters has type {type(parameters).__name__}")
    return CommitInfo(version=version, timestamp=timestamp, operation=operation, mode=mode)


def _get(row: dict[str, Any], key: str, expected: type[T]) -> T:
    if key not in row:
        raise ValueError(f"missing {key}")
    value = row[key]
    if not isinstance(value, expected) or isinstance(value, bool):
        raise ValueError(f"{key} has type {type(value).__name__}")
    return value


def _unavailable(reason: str) -> HistoryInfo:
    return HistoryInfo(
        first_commit=Fact.unavailable("metadata", reason),
        last_commit=Fact.unavailable("metadata", reason),
        num_commits=Fact.unavailable("metadata", reason),
        operations=Fact.unavailable("metadata", reason),
    )
