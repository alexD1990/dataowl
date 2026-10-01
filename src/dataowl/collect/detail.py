"""Size, files and Delta metadata from DESCRIBE DETAIL."""

from __future__ import annotations

from typing import Any

from dataowl.collect import NOT_FOR_VIEWS, is_view, safe_query, short_reason
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact, derive
from dataowl.model.overview import DetailInfo, ObjectType
from dataowl.runner import SqlRunner

NOT_PRESENT = "Not present in DESCRIBE DETAIL output"
NO_ROWS = "DESCRIBE DETAIL returned no rows"


def collect_detail(runner: SqlRunner, ref: TableRef, object_type: Fact[ObjectType]) -> DetailInfo:
    """Run DESCRIBE DETAIL, except for views.

    It also runs when the object type is UNKNOWN or unavailable. Errors make every fact
    unavailable.
    """
    if is_view(object_type):
        return _unavailable(NOT_FOR_VIEWS)

    result = safe_query(runner, f"DESCRIBE DETAIL {ref.quoted()}")
    if isinstance(result, Exception):
        return _unavailable(short_reason(result))
    if not result:
        return _unavailable(NO_ROWS)

    row = result[0]
    size_bytes: Fact[int] = _field(row, "sizeInBytes")
    num_files: Fact[int] = _field(row, "numFiles")
    return DetailInfo(
        format=_field(row, "format"),
        size_bytes=size_bytes,
        num_files=num_files,
        avg_file_size_bytes=_avg_file_size(size_bytes, num_files),
        created=_field(row, "createdAt"),
        last_modified=_field(row, "lastModified"),
        partition_columns=_columns(row, "partitionColumns"),
        clustering_columns=_columns(row, "clusteringColumns"),
    )


def _field(row: dict[str, Any], key: str) -> Fact[Any]:
    if key not in row:
        return Fact.unavailable("metadata", NOT_PRESENT)
    return Fact(row[key], source="metadata")


def _columns(row: dict[str, Any], key: str) -> Fact[tuple[str, ...]]:
    fact: Fact[Any] = _field(row, key)
    if fact.available and fact.value is not None:
        return Fact(tuple(fact.value), source="metadata")
    return fact


def _avg_file_size(size_bytes: Fact[int], num_files: Fact[int]) -> Fact[float]:
    if num_files.available and num_files.value == 0:
        return Fact.unavailable("derived", "No files")
    if (size_bytes.available and size_bytes.value is None) or (
        num_files.available and num_files.value is None
    ):
        return Fact.unavailable("derived", "Input value is null")
    return derive(lambda size, files: size / files, size_bytes, num_files)


def _unavailable(reason: str) -> DetailInfo:
    return DetailInfo(
        format=Fact.unavailable("metadata", reason),
        size_bytes=Fact.unavailable("metadata", reason),
        num_files=Fact.unavailable("metadata", reason),
        avg_file_size_bytes=Fact.unavailable("derived", reason),
        created=Fact.unavailable("metadata", reason),
        last_modified=Fact.unavailable("metadata", reason),
        partition_columns=Fact.unavailable("metadata", reason),
        clustering_columns=Fact.unavailable("metadata", reason),
    )
