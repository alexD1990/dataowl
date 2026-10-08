"""Collection layer: queries to raw data to model objects. The only layer that knows SQL."""

from __future__ import annotations

import re
from typing import Any

from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType
from dataowl.runner import SqlRunner

_MAX_REASON_LENGTH = 200

NOT_FOR_VIEWS = "Not available for views"

_CATALOG_NOT_FOUND_CLASSES = frozenset({"NO_SUCH_CATALOG_EXCEPTION", "CATALOG_NOT_FOUND"})
_TABLE_OR_VIEW_NOT_FOUND = "TABLE_OR_VIEW_NOT_FOUND"
_LEADING_ERROR_CLASS = re.compile(r"\[([A-Za-z0-9_.]+)\]")
_INFORMATION_SCHEMA = re.compile(r"(?<!\w)information_schema(?!\w)", re.IGNORECASE)


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


def is_catalog_not_found(exc: Exception) -> bool:
    """True when the error says that the catalog does not exist.

    The error class is read from getCondition(), then getErrorClass(), and otherwise from a
    leading "[CLASS]" in the first line of the message. PySpark is not imported. True for
    NO_SUCH_CATALOG_EXCEPTION and CATALOG_NOT_FOUND, and for TABLE_OR_VIEW_NOT_FOUND when the
    first line names information_schema, quoted or not. False for everything else, including
    access errors.
    """
    lines = str(exc).strip().splitlines()
    first_line = lines[0].strip() if lines else ""
    error_class = _error_class(exc, first_line)
    if error_class in _CATALOG_NOT_FOUND_CLASSES:
        return True
    if error_class == _TABLE_OR_VIEW_NOT_FOUND:
        return _INFORMATION_SCHEMA.search(first_line) is not None
    return False


def _error_class(exc: Exception, first_line: str) -> str | None:
    for method_name in ("getCondition", "getErrorClass"):
        method = getattr(exc, method_name, None)
        if not callable(method):
            continue
        try:
            value = method()
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    match = _LEADING_ERROR_CLASS.match(first_line)
    return match.group(1).upper() if match else None


def short_reason(exc: Exception) -> str:
    """First line of the error message, at most 200 characters.

    Falls back to the exception type name when the message is empty.
    """
    lines = str(exc).strip().splitlines()
    reason = lines[0].strip() if lines else type(exc).__name__
    if len(reason) > _MAX_REASON_LENGTH:
        reason = reason[: _MAX_REASON_LENGTH - 1] + "…"
    return reason
