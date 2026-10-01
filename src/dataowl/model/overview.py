"""Overview model: facts from step 1 (inspect)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact, to_jsonable


class ObjectType(Enum):
    MANAGED = "MANAGED"
    EXTERNAL = "EXTERNAL"
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    STREAMING_TABLE = "STREAMING_TABLE"
    FOREIGN = "FOREIGN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TableInfo:
    """Catalog metadata from information_schema.tables.

    table_type_raw holds the original table_type value, also when object_type is UNKNOWN.
    """

    object_type: Fact[ObjectType]
    table_type_raw: Fact[str]
    format: Fact[str]
    owner: Fact[str]
    comment: Fact[str]
    created: Fact[datetime]
    last_altered: Fact[datetime]


@dataclass(frozen=True)
class DetailInfo:
    """Size, files and Delta metadata from DESCRIBE DETAIL."""

    format: Fact[str]
    size_bytes: Fact[int]
    num_files: Fact[int]
    avg_file_size_bytes: Fact[float]
    created: Fact[datetime]
    last_modified: Fact[datetime]
    partition_columns: Fact[tuple[str, ...]]
    clustering_columns: Fact[tuple[str, ...]]


@dataclass(frozen=True)
class PropertiesInfo:
    """Selected Delta table properties from SHOW TBLPROPERTIES, as raw strings.

    A property that is not set is an available fact with value None, e.g.
    Fact(None, source="metadata"). The model holds no display text for this; rendering
    decides how to show it.
    """

    change_data_feed: Fact[str]
    log_retention: Fact[str]
    deleted_file_retention: Fact[str]


@dataclass(frozen=True)
class ColumnInfo:
    """One top-level column from information_schema.columns.

    position is ordinal_position as returned by information_schema.columns. The Databricks
    documentation describes it as "The position (numbered from 1) of the column within
    the relation."
    """

    name: str
    position: int
    data_type: str
    nullable: bool
    comment: str | None


@dataclass(frozen=True)
class ColumnsInfo:
    """Schema facts: top-level columns and the field count including nested fields."""

    columns: Fact[tuple[ColumnInfo, ...]]
    num_columns: Fact[int]
    num_fields_nested: Fact[int]


@dataclass(frozen=True)
class Overview:
    """All facts from inspect().

    format and created come from information_schema; last_modified is the last data change
    from DESCRIBE DETAIL. object_type_raw is the original table_type, also when object_type
    is UNKNOWN.
    """

    table: TableRef
    object_type: Fact[ObjectType]
    object_type_raw: Fact[str]
    format: Fact[str]
    owner: Fact[str]
    comment: Fact[str]
    created: Fact[datetime]
    last_modified: Fact[datetime]
    size_bytes: Fact[int]
    num_files: Fact[int]
    avg_file_size_bytes: Fact[float]
    num_rows: Fact[int]
    num_columns: Fact[int]
    num_fields_nested: Fact[int]
    partition_columns: Fact[tuple[str, ...]]
    clustering_columns: Fact[tuple[str, ...]]
    change_data_feed: Fact[str]
    log_retention: Fact[str]
    deleted_file_retention: Fact[str]
    columns: Fact[tuple[ColumnInfo, ...]]

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = to_jsonable(self)
        return result

    def show(self) -> None:
        from dataowl.render.terminal import render_overview

        print(render_overview(self))
