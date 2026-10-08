"""dataowl: facts about Databricks data products for building dbt staging models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dataowl.collect.columns import collect_column_infos, collect_columns
from dataowl.collect.compare import collect_comparison, normalize_compare
from dataowl.collect.constraints import collect_primary_key
from dataowl.collect.counts import collect_row_count
from dataowl.collect.detail import collect_detail
from dataowl.collect.history import collect_history_excerpt
from dataowl.collect.keys import KeyInput, collect_key_analyses, normalize_keys
from dataowl.collect.properties import collect_properties
from dataowl.collect.tables import collect_table_info
from dataowl.collect.timestamps import (
    collect_timestamp_analyses,
    normalize_timestamp_columns,
    validate_days,
)
from dataowl.errors import TableNotFoundError
from dataowl.identifiers import parse_table
from dataowl.model.analysis import ColumnAnalysis
from dataowl.model.overview import Overview
from dataowl.runner import get_runner

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

__all__ = ["ColumnAnalysis", "Overview", "TableNotFoundError", "analyze", "inspect"]


def inspect(
    table: str, *, spark: SparkSession | None = None, count_views: bool = False
) -> Overview:
    """Collect overview facts for a table.

    Raises ValueError for an invalid table name, RuntimeError when no SparkSession is
    available, and TableNotFoundError when the table does not exist or is not accessible.
    Every other failure makes the affected facts unavailable.
    """
    ref = parse_table(table)
    runner = get_runner(spark)

    info = collect_table_info(runner, ref)
    detail = collect_detail(runner, ref, info.object_type)
    columns = collect_columns(runner, ref)
    properties = collect_properties(runner, ref, info.object_type)
    history = collect_history_excerpt(runner, ref, info.object_type)
    num_rows = collect_row_count(runner, ref, info.object_type, count_views=count_views)

    return Overview(
        table=ref,
        object_type=info.object_type,
        object_type_raw=info.table_type_raw,
        format=info.format,
        owner=info.owner,
        comment=info.comment,
        created=info.created,
        last_modified=detail.last_modified,
        size_bytes=detail.size_bytes,
        num_files=detail.num_files,
        avg_file_size_bytes=detail.avg_file_size_bytes,
        num_rows=num_rows,
        num_columns=columns.num_columns,
        num_fields_nested=columns.num_fields_nested,
        partition_columns=detail.partition_columns,
        clustering_columns=detail.clustering_columns,
        change_data_feed=properties.change_data_feed,
        log_retention=properties.log_retention,
        deleted_file_retention=properties.deleted_file_retention,
        history_first_commit=history.first_commit,
        history_last_commit=history.last_commit,
        history_num_commits=history.num_commits,
        history_operations=history.operations,
        columns=columns.columns,
    )


def analyze(
    table: str,
    *,
    key: KeyInput | None = None,
    timestamp: str | list[str] | None = None,
    compare: tuple[str, str] | None = None,
    days: int = 30,
    spark: SparkSession | None = None,
) -> ColumnAnalysis:
    """Collect facts about the columns and column combinations given by the user.

    key is a column name, a tuple of column names (one composite key) or a list of either.
    timestamp is a column name or a list of names; each column is analyzed separately, and
    rows per day cover the last `days` whole days. compare is a tuple (first, second) of two
    time columns. Only the given parameters are analyzed. The declared primary key is
    collected only when key is given.

    Raises ValueError for an invalid table name, when key, timestamp and compare are all
    None, for invalid days, and for an unknown or unsupported column. All input is validated
    before any query against the table's data. Raises RuntimeError when no SparkSession is
    available and when the schema is not available from information_schema.columns. Raises
    TableNotFoundError when the table does not exist or is not accessible. Every other
    failure makes the affected facts unavailable.
    """
    ref = parse_table(table)
    if key is None and timestamp is None and compare is None:
        raise ValueError("At least one of key, timestamp and compare must be given")
    validate_days(days)
    runner = get_runner(spark)

    collect_table_info(runner, ref)
    columns = collect_column_infos(runner, ref)
    if not columns.available or columns.value is None:
        raise RuntimeError(f"Cannot validate columns: schema not available ({columns.reason})")

    keys = normalize_keys(key, columns.value) if key is not None else ()
    time_columns = (
        normalize_timestamp_columns(timestamp, columns.value) if timestamp is not None else ()
    )
    pair = normalize_compare(compare, columns.value) if compare is not None else None

    return ColumnAnalysis(
        table=ref,
        primary_key=collect_primary_key(runner, ref) if key is not None else None,
        keys=collect_key_analyses(runner, ref, keys),
        timestamps=collect_timestamp_analyses(runner, ref, time_columns, days),
        comparison=collect_comparison(runner, ref, *pair) if pair is not None else None,
    )
