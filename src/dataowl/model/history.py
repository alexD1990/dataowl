"""History model: commits and write behaviour from DESCRIBE HISTORY."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact, to_jsonable


@dataclass(frozen=True)
class CommitMetrics:
    """Known keys from operationMetrics of one commit, parsed to int.

    Each field corresponds to the operationMetrics key in camel case, for example
    num_output_rows to numOutputRows. A key that is not present is None, not 0.
    """

    num_output_rows: int | None = None
    num_target_rows_inserted: int | None = None
    num_target_rows_updated: int | None = None
    num_target_rows_deleted: int | None = None
    num_target_rows_copied: int | None = None
    num_deleted_rows: int | None = None
    num_updated_rows: int | None = None
    num_copied_rows: int | None = None
    num_added_files: int | None = None
    num_removed_files: int | None = None


@dataclass(frozen=True)
class CommitInfo:
    """One commit from DESCRIBE HISTORY.

    mode is operationParameters["mode"], or None when it is not present. No other part of
    operationParameters is kept: it contains predicates with row values (principle 3).
    metrics is empty (every field None) unless the commit was parsed with metrics.
    """

    version: int
    timestamp: datetime
    operation: str
    mode: str | None
    metrics: CommitMetrics = field(default_factory=CommitMetrics)


RETENTION_NOTE = "history is limited by delta.logRetentionDuration; counts cover the window above"
OVERWRITE_NOTE = "rows replaced by WRITE (overwrite) are not counted as deleted"
HISTORY_NOTES = (RETENTION_NOTE, OVERWRITE_NOTE)


@dataclass(frozen=True)
class HourCount:
    """Number of commits in one hour of the day (0–23)."""

    hour: int
    commits: int


@dataclass(frozen=True)
class RowStats:
    """Rows written by one operation category, for one kind: inserted, updated or deleted.

    commits is the number of commits in the category, and commits_with_metric the number of
    those where the metric is present. sum, median and max cover only commits with the
    metric, and are None when no commit has it.
    """

    operation: str
    kind: str
    commits: int
    commits_with_metric: int
    sum: int | None
    median: float | None
    max: int | None


@dataclass(frozen=True)
class HistoryAnalysis:
    """Write behaviour of a table, from DESCRIBE HISTORY.

    Commit timestamps are used as DESCRIBE HISTORY returns them, in the session time zone
    (time_zone); days and hours are counted in that time zone. With limit, only the newest
    `limit` commits are read.

    The window runs from first_commit to last_commit. num_days counts calendar days from
    the date of the first commit to the date of the last, both included. The per-day
    statistics cover every day in the window; days without commits count as 0.
    commits_per_hour holds 24 values, hours 0–23, with 0 for hours without commits.

    operations counts commits per operation category, with WRITE (overwrite) as its own
    category. row_stats is ordered as operations, then inserted, updated, deleted, and is
    based on these operationMetrics keys:
    - inserted: numTargetRowsInserted for MERGE; numOutputRows for WRITE, WRITE (overwrite)
      and operations whose name ends with AS SELECT
    - updated: numTargetRowsUpdated for MERGE, numUpdatedRows for UPDATE
    - deleted: numTargetRowsDeleted for MERGE, numDeletedRows for DELETE
    Other combinations of operation and kind are not included.
    """

    table: TableRef
    limit: int | None
    time_zone: Fact[str]
    first_commit: Fact[datetime]
    last_commit: Fact[datetime]
    num_commits: Fact[int]
    num_days: Fact[int]
    commits_per_day_median: Fact[float]
    commits_per_day_min: Fact[int]
    commits_per_day_max: Fact[int]
    commits_per_hour: Fact[tuple[HourCount, ...]]
    operations: Fact[dict[str, int]]
    row_stats: Fact[tuple[RowStats, ...]]
    notes: tuple[str, ...] = HISTORY_NOTES

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = to_jsonable(self)
        return result
