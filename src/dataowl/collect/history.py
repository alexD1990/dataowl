"""Excerpt of the Delta history and commit metrics from DESCRIBE HISTORY."""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, TypeVar

from dataowl.collect import NOT_FOR_VIEWS, is_view, safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.history import CommitInfo, CommitMetrics
from dataowl.model.overview import HistoryInfo, ObjectType
from dataowl.runner import SqlRunner

T = TypeVar("T")

NO_ROWS = "DESCRIBE HISTORY returned no rows"

_OVERWRITE_CATEGORY = "WRITE (overwrite)"

# operationMetrics key -> CommitMetrics field
_METRIC_FIELDS = (
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
)
_DIGITS = re.compile(r"[0-9]+")


def collect_history_excerpt(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType]
) -> HistoryInfo:
    """Run DESCRIBE HISTORY, except for views, and summarize the commits.

    It also runs when the object type is UNKNOWN or unavailable. DESCRIBE HISTORY returns
    every history column; only version, timestamp, operation and operationParameters.mode
    are kept. Errors, no rows and an unexpected row format make every fact unavailable.
    """
    result = _history_rows(runner, ref, object_type)
    if isinstance(result, str):
        return _unavailable(result)

    try:
        commits = parse_commits(result)
    except ValueError as exc:
        return _unavailable(_unexpected_format(exc))

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


def collect_commits(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType]
) -> Fact[tuple[CommitInfo, ...]]:
    """Run DESCRIBE HISTORY, except for views, and return every commit with its metrics.

    Same query as collect_history_excerpt, without LIMIT. It also runs when the object type
    is UNKNOWN or unavailable. Commits keep the order of the query result. Errors, no rows
    and an unexpected row format, including an unexpected metric value, make the fact
    unavailable.
    """
    result = _history_rows(runner, ref, object_type)
    if isinstance(result, str):
        return Fact.unavailable("metadata", result)

    try:
        commits = parse_commits_with_metrics(result)
    except ValueError as exc:
        return Fact.unavailable("metadata", _unexpected_format(exc))
    return Fact(commits, source="metadata")


def parse_commits_with_metrics(rows: list[dict[str, Any]]) -> tuple[CommitInfo, ...]:
    """Convert DESCRIBE HISTORY rows to commits with metrics from operationMetrics.

    The plan (step 20) says to keep operationParameters as well. Only mode is kept, as in
    parse_commits: the rest contains predicates with row values, for example "(id#0 = 42)",
    which would break principle 3.

    Raises ValueError as parse_commits does, and as parse_metrics does for
    operationMetrics.
    """
    return tuple(
        dataclasses.replace(_commit(row), metrics=parse_metrics(row.get("operationMetrics")))
        for row in rows
    )


def parse_metrics(value: Any) -> CommitMetrics:
    """Parse the known keys of operationMetrics to int. Other keys are ignored.

    None gives CommitMetrics with every field None. A key that is missing or None gives
    None, not 0. A value must be a string of digits or a non-negative int (not bool).
    Raises ValueError, with the key name, for any other value, and when value is not a dict.
    """
    if value is None:
        return CommitMetrics()
    if not isinstance(value, dict):
        raise ValueError(f"operationMetrics has type {type(value).__name__}")
    return CommitMetrics(**{name: _metric(value, key) for key, name in _METRIC_FIELDS})


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


def _metric(metrics: dict[str, Any], key: str) -> int | None:
    value = metrics.get(key)
    if value is None:
        return None
    if isinstance(value, str) and _DIGITS.fullmatch(value):
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    # The value itself is left out of the message.
    raise ValueError(
        f"operationMetrics.{key} is not a non-negative integer ({type(value).__name__})"
    )


def _history_rows(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType]
) -> list[dict[str, Any]] | str:
    """Rows from DESCRIBE HISTORY, or the reason they are not available."""
    if is_view(object_type):
        return NOT_FOR_VIEWS
    result = safe_query(runner, f"DESCRIBE HISTORY {ref.quoted()}")
    if isinstance(result, Exception):
        return short_reason(result)
    if not result:
        return NO_ROWS
    return result


def _unexpected_format(exc: ValueError) -> str:
    return f"Unexpected row format in DESCRIBE HISTORY: {exc}"


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
