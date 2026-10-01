"""Object type and basic catalog metadata from information_schema.tables."""

from __future__ import annotations

from typing import Any

from dataowl.collect import safe_query, short_reason
from dataowl.errors import TableNotFoundError
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType, TableInfo
from dataowl.runner import SqlRunner

_QUERY = """
SELECT
  table_type AS table_type,
  data_source_format AS data_source_format,
  table_owner AS table_owner,
  comment AS comment,
  created AS created,
  last_altered AS last_altered
FROM {catalog}.information_schema.tables
WHERE table_schema = :schema
  AND table_name = :table
"""


def collect_table_info(runner: SqlRunner, ref: TableRef) -> TableInfo:
    """Read catalog metadata for the table.

    Raises TableNotFoundError if the query succeeds but returns no rows. Any other
    failure makes every fact unavailable.
    """
    result = safe_query(
        runner,
        _QUERY.format(catalog=ref.quoted_catalog()),
        # Unity Catalog stores names in lower case. Lowering the parameters instead of
        # the columns keeps the WHERE clause free of functions on information_schema columns.
        {"schema": ref.schema.lower(), "table": ref.table.lower()},
    )

    if isinstance(result, Exception):
        reason = short_reason(result)
        return TableInfo(
            object_type=Fact.unavailable("metadata", reason),
            table_type_raw=Fact.unavailable("metadata", reason),
            format=Fact.unavailable("metadata", reason),
            owner=Fact.unavailable("metadata", reason),
            comment=Fact.unavailable("metadata", reason),
            created=Fact.unavailable("metadata", reason),
            last_altered=Fact.unavailable("metadata", reason),
        )

    if not result:
        raise TableNotFoundError(f"Table {ref.quoted()} not found or no access")

    row = result[0]
    raw_type = row.get("table_type")
    return TableInfo(
        object_type=Fact(_object_type(raw_type), source="metadata"),
        table_type_raw=_metadata(raw_type),
        format=_metadata(row.get("data_source_format")),
        owner=_metadata(row.get("table_owner")),
        comment=_metadata(row.get("comment")),
        created=_metadata(row.get("created")),
        last_altered=_metadata(row.get("last_altered")),
    )


def _object_type(raw: str | None) -> ObjectType:
    if raw is None:
        return ObjectType.UNKNOWN
    try:
        return ObjectType(raw.strip().upper())
    except ValueError:
        return ObjectType.UNKNOWN


def _metadata(value: Any) -> Fact[Any]:
    return Fact(value, source="metadata")
