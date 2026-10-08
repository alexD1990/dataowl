from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only
from pyspark.sql.types import ArrayType, IntegerType, StringType, StructField, StructType

import dataowl
from dataowl import Overview, TableNotFoundError, inspect
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo, ObjectType

TABLES_SQL = r"information_schema\.tables"
COLUMNS_SQL = r"information_schema\.columns"
DETAIL_SQL = r"^DESCRIBE DETAIL"
PROPS_SQL = r"^SHOW TBLPROPERTIES"
COUNT_SQL = r"^SELECT COUNT\(\*\)"
HISTORY_SQL = r"^DESCRIBE HISTORY"

CREATED = datetime(2024, 3, 12, 8, 14)
ALTERED = datetime(2025, 1, 2, 9, 0)
LAST_MODIFIED = datetime(2026, 9, 30, 3, 12)
FIRST_COMMIT = datetime(2026, 9, 2, 3, 10)

SCHEMA = StructType(
    [
        StructField("id", StringType(), False),
        StructField("year", IntegerType(), False),
        StructField("tags", ArrayType(StructType([StructField("k", StringType())]))),
    ]
)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> FakeRunner:
    def get_runner(spark: object) -> FakeRunner:
        assert spark is None
        return fake_runner

    monkeypatch.setattr(dataowl, "get_runner", get_runner)
    return fake_runner


def _table_row(table_type: str) -> dict[str, Any]:
    return {
        "table_type": table_type,
        "data_source_format": "DELTA",
        "table_owner": "team-x",
        "comment": None,
        "created": CREATED,
        "last_altered": ALTERED,
    }


def _register_all(fake: FakeRunner, table_type: str = "MANAGED") -> None:
    fake.on(TABLES_SQL, [_table_row(table_type)])
    fake.on(
        DETAIL_SQL,
        [
            {
                "format": "delta",
                "createdAt": datetime(2024, 3, 12, 8, 15),
                "lastModified": LAST_MODIFIED,
                "partitionColumns": ["year"],
                "clusteringColumns": [],
                "numFiles": 12,
                "sizeInBytes": 88290099,
            }
        ],
    )
    fake.on(
        COLUMNS_SQL,
        [
            {
                "column_name": "id",
                "ordinal_position": 1,
                "full_data_type": "string",
                "data_type": "STRING",
                "is_nullable": "NO",
                "comment": "Primary id",
            },
            {
                "column_name": "year",
                "ordinal_position": 2,
                "full_data_type": "int",
                "data_type": "INT",
                "is_nullable": "NO",
                "comment": None,
            },
            {
                "column_name": "tags",
                "ordinal_position": 3,
                "full_data_type": "array<struct<k:string>>",
                "data_type": "ARRAY",
                "is_nullable": "YES",
                "comment": None,
            },
        ],
    )
    fake.on(PROPS_SQL, [{"key": "delta.enableChangeDataFeed", "value": "true"}])
    fake.on(COUNT_SQL, [{"n": 1284991}])
    fake.on(
        HISTORY_SQL,
        [
            _history_row(2, LAST_MODIFIED, "MERGE", None),
            _history_row(1, datetime(2026, 9, 15, 3, 10), "WRITE", "Append"),
            _history_row(0, FIRST_COMMIT, "WRITE", "Overwrite"),
        ],
    )
    fake.on_schema(SCHEMA)


def _history_row(
    version: int, timestamp: datetime, operation: str, mode: str | None
) -> dict[str, Any]:
    return {
        "version": version,
        "timestamp": timestamp,
        "userName": "etl-service@example.com",
        "operation": operation,
        "operationParameters": {"mode": mode} if mode is not None else {},
        "operationMetrics": {"numOutputRows": "10"},
    }


def _queries_matching(fake: FakeRunner, prefix: str) -> list[str]:
    return [sql for sql, _ in fake.queries if sql.lstrip().upper().startswith(prefix)]


def test_table(fake: FakeRunner) -> None:
    _register_all(fake)

    overview = inspect("main.sales.orders")

    assert overview.table == TableRef("main", "sales", "orders")
    assert overview.object_type == Fact(ObjectType.MANAGED, source="metadata")
    assert overview.object_type_raw == Fact("MANAGED", source="metadata")
    assert overview.format == Fact("DELTA", source="metadata")
    assert overview.owner == Fact("team-x", source="metadata")
    assert overview.comment == Fact(None, source="metadata")
    assert overview.created == Fact(CREATED, source="metadata")
    assert overview.last_modified == Fact(LAST_MODIFIED, source="metadata")
    assert overview.size_bytes == Fact(88290099, source="metadata")
    assert overview.num_files == Fact(12, source="metadata")
    assert overview.avg_file_size_bytes == Fact(88290099 / 12, source="derived")
    assert overview.num_rows == Fact(1284991, source="exact")
    assert overview.num_columns == Fact(3, source="metadata")
    assert overview.num_fields_nested == Fact(4, source="metadata")
    assert overview.partition_columns == Fact(("year",), source="metadata")
    assert overview.clustering_columns == Fact((), source="metadata")
    assert overview.change_data_feed == Fact("true", source="metadata")
    assert overview.log_retention == Fact(None, source="metadata")
    assert overview.deleted_file_retention == Fact(None, source="metadata")
    assert overview.history_first_commit == Fact(FIRST_COMMIT, source="metadata")
    assert overview.history_last_commit == Fact(LAST_MODIFIED, source="metadata")
    assert overview.history_num_commits == Fact(3, source="metadata")
    assert overview.history_operations == Fact(
        {"MERGE": 1, "WRITE": 1, "WRITE (overwrite)": 1}, source="metadata"
    )
    assert overview.columns.value is not None
    assert overview.columns.value[0] == ColumnInfo("id", 1, "string", False, "Primary id")

    first_query = fake.queries[0][0]
    assert "information_schema.tables" in first_query
    assert len(fake.queries) == 6
    assert len(_queries_matching(fake, "DESCRIBE HISTORY")) == 1
    assert_read_only(fake)


def test_view(fake: FakeRunner) -> None:
    _register_all(fake, "VIEW")

    overview = inspect("main.sales.orders_v")

    assert overview.object_type == Fact(ObjectType.VIEW, source="metadata")
    assert overview.size_bytes == Fact.unavailable("metadata", "Not available for views")
    assert overview.change_data_feed == Fact.unavailable("metadata", "Not available for views")
    assert overview.history_num_commits == Fact.unavailable("metadata", "Not available for views")
    assert overview.num_rows == Fact.unavailable("exact", "Skipped for VIEW; use count_views=True")
    assert overview.num_columns == Fact(3, source="metadata")
    assert _queries_matching(fake, "DESCRIBE") == []
    assert _queries_matching(fake, "SHOW") == []
    assert _queries_matching(fake, "SELECT COUNT") == []
    assert_read_only(fake)


def test_view_with_count_views(fake: FakeRunner) -> None:
    _register_all(fake, "VIEW")

    overview = inspect("main.sales.orders_v", count_views=True)

    assert overview.num_rows == Fact(1284991, source="exact")
    assert len(_queries_matching(fake, "SELECT COUNT")) == 1
    assert _queries_matching(fake, "DESCRIBE") == []
    assert_read_only(fake)


def test_several_sources_fail(fake: FakeRunner) -> None:
    fake.on_error(TABLES_SQL, PermissionError("[INSUFFICIENT_PERMISSIONS] no USE CATALOG"))
    fake.on_error(DETAIL_SQL, RuntimeError("[DELTA_TABLE_ONLY_OPERATION] not a Delta table"))
    fake.on_error(COLUMNS_SQL, PermissionError("[INSUFFICIENT_PERMISSIONS] no USE CATALOG"))
    fake.on_error(PROPS_SQL, RuntimeError("[UNSUPPORTED_FEATURE] not supported"))
    fake.on_error(HISTORY_SQL, RuntimeError("[DELTA_MISSING_DELTA_TABLE] not a Delta table"))
    fake.on_schema(SCHEMA)

    overview = inspect("main.sales.orders")

    assert overview.object_type == Fact.unavailable(
        "metadata", "[INSUFFICIENT_PERMISSIONS] no USE CATALOG"
    )
    assert overview.size_bytes == Fact.unavailable(
        "metadata", "[DELTA_TABLE_ONLY_OPERATION] not a Delta table"
    )
    assert overview.avg_file_size_bytes == Fact.unavailable(
        "derived", "[DELTA_TABLE_ONLY_OPERATION] not a Delta table"
    )
    assert overview.columns == Fact.unavailable(
        "metadata", "[INSUFFICIENT_PERMISSIONS] no USE CATALOG"
    )
    assert overview.change_data_feed == Fact.unavailable(
        "metadata", "[UNSUPPORTED_FEATURE] not supported"
    )
    assert overview.num_rows == Fact.unavailable(
        "exact", "Skipped: object type unknown; use count_views=True"
    )
    assert overview.history_operations == Fact.unavailable(
        "metadata", "[DELTA_MISSING_DELTA_TABLE] not a Delta table"
    )
    assert len(_queries_matching(fake, "DESCRIBE HISTORY")) == 1
    assert overview.num_fields_nested == Fact(4, source="metadata")
    json.dumps(overview.to_dict())
    assert_read_only(fake)


def test_table_not_found_propagates(fake: FakeRunner) -> None:
    fake.on(TABLES_SQL, [])

    with pytest.raises(TableNotFoundError, match="not found or no access"):
        inspect("main.sales.missing")

    assert len(fake.queries) == 1
    assert_read_only(fake)


def test_missing_catalog_raises_table_not_found(fake: FakeRunner) -> None:
    fake.on_error(
        TABLES_SQL,
        RuntimeError("[NO_SUCH_CATALOG_EXCEPTION] Catalog 'dev' not found.\nSQLSTATE: 42704"),
    )
    _register_all(fake)

    with pytest.raises(TableNotFoundError, match="Catalog 'dev' not found"):
        inspect("dev.sales.orders")

    assert len(fake.queries) == 1
    assert_read_only(fake)


def test_invalid_table_name_raises_before_spark(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_runner(spark: object) -> None:
        raise AssertionError("get_runner must not be called")

    monkeypatch.setattr(dataowl, "get_runner", get_runner)

    with pytest.raises(ValueError):
        inspect("a.b.c; DROP TABLE x")


def test_spark_is_passed_to_get_runner(
    monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner
) -> None:
    session = object()
    received: list[object] = []

    def get_runner(spark: object) -> FakeRunner:
        received.append(spark)
        return fake_runner

    monkeypatch.setattr(dataowl, "get_runner", get_runner)
    _register_all(fake_runner)

    inspect("main.sales.orders", spark=session)  # type: ignore[arg-type]

    assert received == [session]


def test_to_dict(fake: FakeRunner) -> None:
    _register_all(fake)

    data = inspect("main.sales.orders").to_dict()

    assert json.loads(json.dumps(data)) == data
    assert data["table"] == {"catalog": "main", "schema": "sales", "table": "orders"}
    assert data["num_rows"] == {
        "value": 1284991,
        "source": "exact",
        "available": True,
        "reason": None,
    }
    assert data["object_type"]["value"] == "MANAGED"
    assert data["created"]["value"] == "2024-03-12T08:14:00"
    assert data["partition_columns"]["value"] == ["year"]
    assert data["avg_file_size_bytes"]["source"] == "derived"
    assert data["log_retention"] == {
        "value": None,
        "source": "metadata",
        "available": True,
        "reason": None,
    }
    assert data["columns"]["value"][0] == {
        "name": "id",
        "position": 1,
        "data_type": "string",
        "nullable": False,
        "comment": "Primary id",
    }
    assert data["history_first_commit"] == {
        "value": "2026-09-02T03:10:00",
        "source": "metadata",
        "available": True,
        "reason": None,
    }
    assert data["history_last_commit"]["value"] == "2026-09-30T03:12:00"
    assert data["history_num_commits"] == {
        "value": 3,
        "source": "metadata",
        "available": True,
        "reason": None,
    }
    assert data["history_operations"] == {
        "value": {"MERGE": 1, "WRITE": 1, "WRITE (overwrite)": 1},
        "source": "metadata",
        "available": True,
        "reason": None,
    }
    assert "etl-service@example.com" not in json.dumps(data)
    assert list(data) == [field for field in Overview.__dataclass_fields__]


def test_to_dict_unavailable_fact(fake: FakeRunner) -> None:
    _register_all(fake, "VIEW")

    data = inspect("main.sales.orders_v").to_dict()

    assert data["size_bytes"] == {
        "value": None,
        "source": "metadata",
        "available": False,
        "reason": "Not available for views",
    }


def test_show_prints_rendered_overview(
    fake: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_all(fake)

    inspect("main.sales.orders").show()

    assert "main.sales.orders" in capsys.readouterr().out


def test_public_api() -> None:
    assert set(dataowl.__all__) == {
        "ColumnAnalysis",
        "HistoryAnalysis",
        "Overview",
        "TableNotFoundError",
        "analyze",
        "history",
        "inspect",
    }
