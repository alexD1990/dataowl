"""Schema facts from information_schema.columns and the Spark schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo, ColumnsInfo
from dataowl.runner import SqlRunner

if TYPE_CHECKING:
    from pyspark.sql.types import DataType, StructType

NO_COLUMNS = "information_schema.columns returned no rows"

_QUERY = """
SELECT
  column_name AS column_name,
  ordinal_position AS ordinal_position,
  full_data_type AS full_data_type,
  data_type AS data_type,
  is_nullable AS is_nullable,
  comment AS comment
FROM {catalog}.information_schema.columns
WHERE table_schema = :schema
  AND table_name = :table
ORDER BY ordinal_position
"""


def collect_columns(runner: SqlRunner, ref: TableRef) -> ColumnsInfo:
    """Read top-level columns from information_schema and count nested fields from the schema.

    The two sources fail independently.
    """
    columns, num_columns = _top_level_columns(runner, ref)
    return ColumnsInfo(
        columns=columns,
        num_columns=num_columns,
        num_fields_nested=_nested_field_count(runner, ref),
    )


def count_fields(schema: StructType) -> int:
    """Count all fields in a schema, including nested fields.

    Every StructField counts 1. Fields inside a struct, inside the element type of an
    array and inside the key and value types of a map are added recursively.

    Example: ``a INT, b STRUCT<x INT, y ARRAY<STRUCT<z INT>>>, m MAP<STRING, STRUCT<v INT>>``
    counts a, b, x, y, z, m and v, which gives 7.
    """
    from pyspark.sql.types import ArrayType, MapType, StructType

    def nested(data_type: DataType) -> int:
        if isinstance(data_type, StructType):
            return sum(1 + nested(field.dataType) for field in data_type.fields)
        if isinstance(data_type, ArrayType):
            return nested(data_type.elementType)
        if isinstance(data_type, MapType):
            return nested(data_type.keyType) + nested(data_type.valueType)
        return 0

    return nested(schema)


def _top_level_columns(
    runner: SqlRunner, ref: TableRef
) -> tuple[Fact[tuple[ColumnInfo, ...]], Fact[int]]:
    result = safe_query(
        runner,
        _QUERY.format(catalog=ref.quoted_catalog()),
        # Unity Catalog stores names in lower case. Lowering the parameters instead of
        # the columns keeps the WHERE clause free of functions on information_schema columns.
        {"schema": ref.schema.lower(), "table": ref.table.lower()},
    )
    if isinstance(result, Exception):
        reason = short_reason(result)
        return Fact.unavailable("metadata", reason), Fact.unavailable("metadata", reason)
    if not result:
        return Fact.unavailable("metadata", NO_COLUMNS), Fact.unavailable("metadata", NO_COLUMNS)

    columns = tuple(_column(row) for row in result)
    return Fact(columns, source="metadata"), Fact(len(columns), source="metadata")


def _column(row: dict[str, Any]) -> ColumnInfo:
    full_data_type = row.get("full_data_type")
    return ColumnInfo(
        name=row["column_name"],
        position=row["ordinal_position"],
        data_type=full_data_type if full_data_type is not None else row["data_type"],
        nullable=row["is_nullable"] == "YES",
        comment=row.get("comment"),
    )


def _nested_field_count(runner: SqlRunner, ref: TableRef) -> Fact[int]:
    try:
        schema = runner.schema(ref)
    except Exception as exc:
        return Fact.unavailable("metadata", short_reason(exc))
    return Fact(count_fields(schema), source="metadata")
