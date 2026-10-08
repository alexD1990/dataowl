"""History model: commits from DESCRIBE HISTORY."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
