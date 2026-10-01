"""Collection layer: queries to raw data to model objects. The only layer that knows SQL."""

from __future__ import annotations

from typing import Any

from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType
from dataowl.runner import SqlRunner

_MAX_REASON_LENGTH = 200

NOT_FOR_VIEWS = "Not available for views"


def is_view(object_type: Fact[ObjectType]) -> bool:
    """True only when the object type is known to be VIEW."""
    return object_type.available and object_type.value is ObjectType.VIEW


def safe_query(
    runner: SqlRunner, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]] | Exception:
    """Run a query and return the rows, or the exception instead of raising it."""
    try:
        return runner.query(sql, params)
    except Exception as exc:
        return exc


def short_reason(exc: Exception) -> str:
    """First line of the error message, at most 200 characters.

    Falls back to the exception type name when the message is empty.
    """
    lines = str(exc).strip().splitlines()
    reason = lines[0].strip() if lines else type(exc).__name__
    if len(reason) > _MAX_REASON_LENGTH:
        reason = reason[: _MAX_REASON_LENGTH - 1] + "…"
    return reason
