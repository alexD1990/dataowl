"""Overview model: facts from step 1 (inspect)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from dataowl.model.facts import Fact


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
