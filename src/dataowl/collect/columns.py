"""Schema facts from information_schema.columns and the Spark schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from dataowl.collect import safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo, ColumnsInfo
from dataowl.runner import SqlRunner

if TYPE_CHECKING:
    from pyspark.sql.types import DataType, StructType

T = TypeVar("T")

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


def collect_column_infos(runner: SqlRunner, ref: TableRef) -> Fact[tuple[ColumnInfo, ...]]:
    """Read top-level columns from information_schema.columns, in ordinal order.

    Does not read the Spark schema. An error, no rows and an unexpected row format make the
    fact unavailable.
    """
    result = safe_query(
        runner,
        _QUERY.format(catalog=ref.quoted_catalog()),
        # Unity Catalog stores names in lower case. Lowering the parameters instead of
        # the columns keeps the WHERE clause free of functions on information_schema columns.
        {"schema": ref.schema.lower(), "table": ref.table.lower()},
    )
    if isinstance(result, Exception):
        return Fact.unavailable("metadata", short_reason(result))
    if not result:
        return Fact.unavailable("metadata", NO_COLUMNS)

    try:
        columns = tuple(_column(row) for row in result)
    except ValueError as exc:
        return Fact.unavailable(
            "metadata", f"Unexpected row format in information_schema.columns: {exc}"
        )
    return Fact(columns, source="metadata")


def _top_level_columns(
    runner: SqlRunner, ref: TableRef
) -> tuple[Fact[tuple[ColumnInfo, ...]], Fact[int]]:
    columns = collect_column_infos(runner, ref)
    if not columns.available or columns.value is None:
        assert columns.reason is not None  # guaranteed by Fact.__post_init__
        return columns, Fact.unavailable("metadata", columns.reason)
    return columns, Fact(len(columns.value), source="metadata")


def _column(row: dict[str, Any]) -> ColumnInfo:
    """Convert one row. Raises ValueError if a key is missing or has an unexpected type."""
    name = _get(row, "column_name", str)
    position = _get(row, "ordinal_position", int)
    full_data_type = row.get("full_data_type")
    if full_data_type is None:
        data_type = _get(row, "data_type", str)
    elif isinstance(full_data_type, str):
        data_type = full_data_type
    else:
        raise ValueError(f"full_data_type has type {type(full_data_type).__name__}")
    is_nullable = _get(row, "is_nullable", str)
    if is_nullable not in ("YES", "NO"):
        raise ValueError(f"is_nullable has value {is_nullable!r}")
    comment = row.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise ValueError(f"comment has type {type(comment).__name__}")
    return ColumnInfo(
        name=name,
        position=position,
        data_type=data_type,
        nullable=is_nullable == "YES",
        comment=comment,
    )


def _get(row: dict[str, Any], key: str, expected: type[T]) -> T:
    if key not in row:
        raise ValueError(f"missing {key}")
    value = row[key]
    if not isinstance(value, expected) or isinstance(value, bool):
        raise ValueError(f"{key} has type {type(value).__name__}")
    return value


def _nested_field_count(runner: SqlRunner, ref: TableRef) -> Fact[int]:
    try:
        schema = runner.schema(ref)
    except Exception as exc:
        return Fact.unavailable("metadata", short_reason(exc))
    return Fact(count_fields(schema), source="metadata")
