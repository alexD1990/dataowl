"""dataowl: facts about Databricks data products for building dbt staging models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dataowl.collect.columns import collect_columns
from dataowl.collect.counts import collect_row_count
from dataowl.collect.detail import collect_detail
from dataowl.collect.properties import collect_properties
from dataowl.collect.tables import collect_table_info
from dataowl.errors import TableNotFoundError
from dataowl.identifiers import parse_table
from dataowl.model.overview import Overview
from dataowl.runner import get_runner

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

__all__ = ["Overview", "TableNotFoundError", "inspect"]


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
        columns=columns.columns,
    )
