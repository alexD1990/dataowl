"""History model: commits from DESCRIBE HISTORY."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CommitInfo:
    """One commit from DESCRIBE HISTORY.

    mode is operationParameters["mode"], or None when it is not present.
    """

    version: int
    timestamp: datetime
    operation: str
    mode: str | None
