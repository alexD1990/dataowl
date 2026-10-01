"""Selected Delta table properties from SHOW TBLPROPERTIES."""

from __future__ import annotations

from typing import Any

from dataowl.collect import NOT_FOR_VIEWS, is_view, safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType, PropertiesInfo
from dataowl.runner import SqlRunner

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
    if is_view(object_type):
        return _unavailable(NOT_FOR_VIEWS)

    result = safe_query(runner, f"SHOW TBLPROPERTIES {ref.quoted()}")
    if isinstance(result, Exception):
        return _unavailable(short_reason(result))

    facts = _property_facts(result)
    return PropertiesInfo(
        change_data_feed=facts[_CHANGE_DATA_FEED],
        log_retention=facts[_LOG_RETENTION],
        deleted_file_retention=facts[_DELETED_FILE_RETENTION],
    )


def _property_facts(rows: list[dict[str, Any]]) -> dict[str, Fact[str]]:
    """Return one fact per relevant property, keyed by lower-case key.

    Rows whose key is not a string, and rows for other properties, are skipped. A relevant
    row whose value is not a string makes only that property unavailable.
    """
    facts: dict[str, Fact[str]] = {key: Fact(None, source="metadata") for key in _KEYS}
    seen: set[str] = set()
    for row in rows:
        key = row.get("key")
        if not isinstance(key, str):
            continue
        normalized = key.lower()
        if normalized not in _KEYS or normalized in seen:
            continue
        seen.add(normalized)
        value = row.get("value")
        if isinstance(value, str):
            facts[normalized] = Fact(value, source="metadata")
        else:
            facts[normalized] = Fact.unavailable(
                "metadata",
                f"Unexpected row format in SHOW TBLPROPERTIES: value of {key} has type "
                f"{type(value).__name__}",
            )
    return facts


def _unavailable(reason: str) -> PropertiesInfo:
    return PropertiesInfo(
        change_data_feed=Fact.unavailable("metadata", reason),
        log_retention=Fact.unavailable("metadata", reason),
        deleted_file_retention=Fact.unavailable("metadata", reason),
    )
