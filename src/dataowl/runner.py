"""SqlRunner protocol and SparkRunner.

PySpark is imported lazily, so this module can be imported without PySpark installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from dataowl.identifiers import TableRef

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from pyspark.sql.types import StructType


class SqlRunner(Protocol):
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def schema(self, table: TableRef) -> StructType: ...


class SparkRunner:
    def __init__(self, spark: SparkSession) -> None:
        self._spark = spark

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows = self._spark.sql(sql, args=params).collect()
        return [row.asDict(recursive=True) for row in rows]

    def schema(self, table: TableRef) -> StructType:
        # spark.table() is lazy; reading .schema does not scan data.
        return self._spark.table(table.quoted()).schema


def get_runner(spark: SparkSession | None = None) -> SparkRunner:
    """Return a SparkRunner for the given session, or for the active session."""
    if spark is None:
        from pyspark.sql import SparkSession

        spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError(
            "No active SparkSession found. Run dataowl in a Databricks notebook or job, "
            "or pass a session explicitly with spark=."
        )
    return SparkRunner(spark)
