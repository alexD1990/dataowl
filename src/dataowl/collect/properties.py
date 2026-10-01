"""Selected Delta table properties from SHOW TBLPROPERTIES."""

from __future__ import annotations

from typing import Any

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType, PropertiesInfo
from dataowl.runner import SqlRunner

NOT_FOR_VIEWS = "Not available for views"

_CHANGE_DATA_FEED = "delta.enablechangedatafeed"
_LOG_RETENTION = "delta.logretentionduration"
_DELETED_FILE_RETENTION = "delta.deletedfileretentionduration"
_KEYS = frozenset({_CHANGE_DATA_FEED, _LOG_RETENTION, _DELETED_FILE_RETENTION})


def collect_properties(
    runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType]
) -> PropertiesInfo:
    """Run SHOW TBLPROPERTIES, except for views.

    It also runs when the object type is UNKNOWN or unavailable. Keys are matched case
    insensitively. A property that is not set gives Fact(None, source="metadata").
    """
    if object_type.available and object_type.value is ObjectType.VIEW:
        return _unavailable(NOT_FOR_VIEWS)

    result = safe_query(runner, f"SHOW TBLPROPERTIES {ref.quoted()}")
    if isinstance(result, Exception):
        return _unavailable(short_reason(result))

    try:
        values = _relevant_values(result)
    except ValueError as exc:
        return _unavailable(f"Unexpected row format in SHOW TBLPROPERTIES: {exc}")

    return PropertiesInfo(
        change_data_feed=Fact(values.get(_CHANGE_DATA_FEED), source="metadata"),
        log_retention=Fact(values.get(_LOG_RETENTION), source="metadata"),
        deleted_file_retention=Fact(values.get(_DELETED_FILE_RETENTION), source="metadata"),
    )


def _relevant_values(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Return the relevant properties keyed by lower-case key. Other rows are discarded.

    Raises ValueError if a row has no string key or value.
    """
    values: dict[str, str] = {}
    for row in rows:
        key = row.get("key")
        value = row.get("value")
        if not isinstance(key, str):
            raise ValueError(f"key has type {type(key).__name__}")
        if not isinstance(value, str):
            raise ValueError(f"value has type {type(value).__name__}")
        normalized = key.lower()
        if normalized in _KEYS:
            values.setdefault(normalized, value)
    return values


def _unavailable(reason: str) -> PropertiesInfo:
    return PropertiesInfo(
        change_data_feed=Fact.unavailable("metadata", reason),
        log_retention=Fact.unavailable("metadata", reason),
        deleted_file_retention=Fact.unavailable("metadata", reason),
    )
