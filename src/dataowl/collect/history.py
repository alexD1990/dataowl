"""Excerpt of the Delta history, commit metrics and write behaviour from DESCRIBE HISTORY."""

from __future__ import annotations

import dataclasses
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar

from dataowl.collect import NOT_FOR_VIEWS, is_view, safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.history import (
    CommitInfo,
    CommitMetrics,
    HistoryAnalysis,
    HourCount,
    RowStats,
)
from dataowl.model.overview import HistoryInfo, ObjectType
from dataowl.runner import SqlRunner

T = TypeVar("T")

NO_ROWS = "DESCRIBE HISTORY returned no rows"
NO_TIME_ZONE_ROWS = "current_timezone() returned no rows"

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

_TIME_ZONE_QUERY = "SELECT current_timezone() AS time_zone"

_ROW_KINDS = ("inserted", "updated", "deleted")
# (operation category, kind) -> CommitMetrics field. Categories ending with AS SELECT are
# handled in _row_metric.
_ROW_METRICS = {
    ("MERGE", "inserted"): "num_target_rows_inserted",
    ("MERGE", "updated"): "num_target_rows_updated",
    ("MERGE", "deleted"): "num_target_rows_deleted",
    ("WRITE", "inserted"): "num_output_rows",
    (_OVERWRITE_CATEGORY, "inserted"): "num_output_rows",
    ("UPDATE", "updated"): "num_updated_rows",
    ("DELETE", "deleted"): "num_deleted_rows",
}


@dataclass(frozen=True)
class CommitsResult:
    """Commits from collect_commits.

    metrics_reason is set when the commits could be read but their metrics could not. The
    commits then have empty metrics.
    """

    commits: Fact[tuple[CommitInfo, ...]]
    metrics_reason: str | None = None


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


def validate_limit(limit: int | None) -> int | None:
    """Return limit. Raises ValueError unless it is None or an int (not bool) of at least 1."""
    if limit is None:
        return None
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError(f"limit must be an integer, got {type(limit).__name__}")
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    return limit


def collect_commits(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType], limit: int | None = None
) -> CommitsResult:
    """Run DESCRIBE HISTORY, except for views, and return every commit with its metrics.

    Same query as collect_history_excerpt. With limit, LIMIT <limit> is added, which gives
    the newest commits; limit is validated before it is put in the SQL. It also runs when
    the object type is UNKNOWN or unavailable. Commits keep the order of the query result.

    Errors, no rows and an unexpected commit format make the commits unavailable. An
    unexpected metric value only sets metrics_reason; the commits are then returned
    without metrics.
    """
    validate_limit(limit)
    result = _history_rows(runner, ref, object_type, limit)
    if isinstance(result, str):
        return CommitsResult(Fact.unavailable("metadata", result))

    try:
        return CommitsResult(Fact(parse_commits_with_metrics(result), source="metadata"))
    except ValueError as metrics_exc:
        try:
            commits = parse_commits(result)
        except ValueError as exc:
            return CommitsResult(Fact.unavailable("metadata", _unexpected_format(exc)))
        return CommitsResult(Fact(commits, source="metadata"), _unexpected_format(metrics_exc))


def collect_session_time_zone(runner: SqlRunner) -> Fact[str]:
    """Name of the session time zone, in which DESCRIBE HISTORY returns timestamps.

    Errors, no rows and an unexpected row format make the fact unavailable.
    """
    result = safe_query(runner, _TIME_ZONE_QUERY)
    if isinstance(result, Exception):
        return Fact.unavailable("metadata", short_reason(result))
    if not result:
        return Fact.unavailable("metadata", NO_TIME_ZONE_ROWS)
    try:
        time_zone = _get(result[0], "time_zone", str)
    except ValueError as exc:
        return Fact.unavailable("metadata", f"Unexpected row format in current_timezone(): {exc}")
    return Fact(time_zone, source="metadata")


def build_history_analysis(
    ref: TableRef,
    limit: int | None,
    time_zone: Fact[str],
    commits: Fact[tuple[CommitInfo, ...]],
    metrics_reason: str | None,
) -> HistoryAnalysis:
    """Combine the time zone and the commits into a HistoryAnalysis. Never raises.

    Unavailable or empty commits make every fact except time_zone unavailable with the same
    reason. With metrics_reason, row_stats is unavailable with that reason.
    """
    if not commits.available or not commits.value:
        reason = commits.reason if not commits.available and commits.reason else NO_ROWS
        return _unavailable_analysis(ref, limit, time_zone, reason)

    values = commits.value
    first, last, num_commits = observation_window(values)
    per_day = commits_per_day(values)
    stats: Fact[tuple[RowStats, ...]] = (
        Fact.unavailable("derived", metrics_reason)
        if metrics_reason is not None
        else Fact(row_stats(values), source="derived")
    )
    return HistoryAnalysis(
        table=ref,
        limit=limit,
        time_zone=time_zone,
        first_commit=Fact(first, source="metadata"),
        last_commit=Fact(last, source="metadata"),
        num_commits=Fact(num_commits, source="metadata"),
        num_days=Fact(window_days(first, last), source="derived"),
        commits_per_day_median=Fact(float(statistics.median(per_day)), source="derived"),
        commits_per_day_min=Fact(min(per_day), source="derived"),
        commits_per_day_max=Fact(max(per_day), source="derived"),
        commits_per_hour=Fact(commits_per_hour(values), source="derived"),
        operations=Fact(count_operations(values), source="metadata"),
        row_stats=stats,
    )


def window_days(first: datetime, last: datetime) -> int:
    """Calendar days from the date of first to the date of last, both included."""
    return (last.date() - first.date()).days + 1


def commits_per_day(commits: Sequence[CommitInfo]) -> tuple[int, ...]:
    """Commits per calendar day, one value per day in the window, with 0 for days without.

    Empty for no commits.
    """
    if not commits:
        return ()
    counts = Counter(commit.timestamp.date() for commit in commits)
    first = min(counts)
    num_days = (max(counts) - first).days + 1
    return tuple(counts.get(first + timedelta(days=offset), 0) for offset in range(num_days))


def commits_per_hour(commits: Iterable[CommitInfo]) -> tuple[HourCount, ...]:
    """Commits per hour of the day, by timestamp.hour: 24 values, with 0 for hours without."""
    counts = Counter(commit.timestamp.hour for commit in commits)
    return tuple(HourCount(hour, counts.get(hour, 0)) for hour in range(24))


def row_stats(commits: Sequence[CommitInfo]) -> tuple[RowStats, ...]:
    """Inserted, updated and deleted rows per operation category, from the commit metrics.

    Ordered as count_operations, then inserted, updated, deleted. Only the combinations of
    category and kind described in HistoryAnalysis are included. sum, median and max cover
    the commits where the metric is present, and are None when no commit has it.
    """
    by_category: dict[str, list[CommitInfo]] = {}
    for commit in commits:
        by_category.setdefault(operation_category(commit), []).append(commit)

    result = []
    for category in count_operations(commits):
        category_commits = by_category[category]
        for kind in _ROW_KINDS:
            metric = _row_metric(category, kind)
            if metric is None:
                continue
            values = [
                value
                for value in (getattr(commit.metrics, metric) for commit in category_commits)
                if value is not None
            ]
            result.append(
                RowStats(
                    operation=category,
                    kind=kind,
                    commits=len(category_commits),
                    commits_with_metric=len(values),
                    sum=sum(values) if values else None,
                    median=float(statistics.median(values)) if values else None,
                    max=max(values) if values else None,
                )
            )
    return tuple(result)


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


def _row_metric(category: str, kind: str) -> str | None:
    if kind == "inserted" and category.upper().endswith("AS SELECT"):
        return "num_output_rows"
    return _ROW_METRICS.get((category, kind))


def _history_rows(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType], limit: int | None = None
) -> list[dict[str, Any]] | str:
    """Rows from DESCRIBE HISTORY, or the reason they are not available.

    limit must already be validated.
    """
    if is_view(object_type):
        return NOT_FOR_VIEWS
    sql = f"DESCRIBE HISTORY {ref.quoted()}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    result = safe_query(runner, sql)
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


def _unavailable_analysis(
    ref: TableRef, limit: int | None, time_zone: Fact[str], reason: str
) -> HistoryAnalysis:
    return HistoryAnalysis(
        table=ref,
        limit=limit,
        time_zone=time_zone,
        first_commit=Fact.unavailable("metadata", reason),
        last_commit=Fact.unavailable("metadata", reason),
        num_commits=Fact.unavailable("metadata", reason),
        num_days=Fact.unavailable("derived", reason),
        commits_per_day_median=Fact.unavailable("derived", reason),
        commits_per_day_min=Fact.unavailable("derived", reason),
        commits_per_day_max=Fact.unavailable("derived", reason),
        commits_per_hour=Fact.unavailable("derived", reason),
        operations=Fact.unavailable("metadata", reason),
        row_stats=Fact.unavailable("derived", reason),
    )
