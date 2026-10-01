"""dataowl integration tests against a Databricks development workspace.

These tests are run manually by a developer in Databricks. They are not run in CI and not
by `pytest tests/unit`. The module is skipped when DATAOWL_IT_SCHEMA is not set.

How to run
----------
1. Bring the repository into the workspace as a Git folder:
   Workspace > Create > Git folder, and paste the repository URL.

2. Create the test tables: run tests/integration/setup_test_tables.sql on a SQL warehouse
   or a cluster with Databricks Runtime 14.3 LTS or above. See the top of that file.

3. In a Python notebook attached to a cluster (Databricks Runtime 14.3 LTS or above),
   install the package and pytest. Install the package without the dev extra: the dev
   extra pulls PySpark from PyPI, which would shadow the PySpark that Databricks provides.

       %pip install /Workspace/<path to Git folder>/dataowl pytest
       dbutils.library.restartPython()

4. Set DATAOWL_IT_SCHEMA to the catalog and schema used by the setup script, and run
   pytest from the repository root:

       import os
       import sys

       import pytest

       sys.dont_write_bytecode = True
       os.environ["DATAOWL_IT_SCHEMA"] = "dev.dataowl_it"
       os.chdir("/Workspace/<path to Git folder>/dataowl")

       exit_code = pytest.main(["tests/integration", "-v", "-p", "no:cacheprovider"])
       assert exit_code == 0

Known pitfalls in Databricks
----------------------------
- The workspace file system does not support the writes pytest and Python do for caches.
  Set sys.dont_write_bytecode = True before running, and disable the pytest cache with
  -p no:cacheprovider. Otherwise pytest can fail or hang while writing __pycache__ and
  .pytest_cache.
- Run the setup script and the tests on the same day: expected values for
  timestamps_mixed are relative to the time the setup script ran (used from step 15).

Order: run the setup SQL, set DATAOWL_IT_SCHEMA, run pytest tests/integration.
"""

from __future__ import annotations

import os

import pytest

IT_SCHEMA = os.environ.get("DATAOWL_IT_SCHEMA", "")
if not IT_SCHEMA:
    pytest.skip("DATAOWL_IT_SCHEMA is not set", allow_module_level=True)

# PySpark is only imported after the check above, lazily through dataowl.runner.get_runner.
import json  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import TYPE_CHECKING, Any  # noqa: E402

from conftest import assert_read_only  # noqa: E402

import dataowl  # noqa: E402
from dataowl import TableNotFoundError, inspect  # noqa: E402
from dataowl.identifiers import TableRef  # noqa: E402
from dataowl.model.facts import Fact  # noqa: E402
from dataowl.model.overview import ObjectType  # noqa: E402
from dataowl.runner import SqlRunner, get_runner  # noqa: E402

if TYPE_CHECKING:
    from pyspark.sql.types import StructType

TABLES = [
    "small_table",
    "keys_with_duplicates",
    "timestamps_mixed",
    "merge_history",
    "nested_schema",
    "simple_view",
]
VIEWS = "Not available for views"


def table(name: str) -> str:
    return f"{IT_SCHEMA}.{name}"


class RecordingRunner:
    """Wraps a real SqlRunner and records every executed query."""

    def __init__(self, inner: SqlRunner) -> None:
        self._inner = inner
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        return self._inner.query(sql, params)

    def schema(self, table: TableRef) -> StructType:
        return self._inner.schema(table)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingRunner]:
    """Route inspect() through a RecordingRunner, and check principle 2 on the real SQL."""
    runner = RecordingRunner(get_runner())
    monkeypatch.setattr(dataowl, "get_runner", lambda spark=None: runner)
    yield runner
    assert runner.queries, "No queries were recorded"
    assert_read_only(runner)


def test_small_table(recorder: RecordingRunner) -> None:
    overview = inspect(table("small_table"))

    assert overview.object_type == Fact(ObjectType.MANAGED, source="metadata")
    assert overview.num_rows == Fact(1000, source="exact")
    assert overview.num_columns == Fact(4, source="metadata")
    assert overview.num_fields_nested == Fact(4, source="metadata")
    assert overview.format.available
    assert isinstance(overview.format.value, str)
    assert overview.format.value.upper() == "DELTA"
    assert overview.size_bytes.available
    assert overview.size_bytes.value is not None and overview.size_bytes.value > 0
    assert overview.num_files.available
    assert overview.num_files.value is not None and overview.num_files.value > 0
    assert overview.partition_columns == Fact((), source="metadata")


def test_ordinal_position_starts_at_1(recorder: RecordingRunner) -> None:
    overview = inspect(table("small_table"))

    assert overview.columns.value is not None
    first = overview.columns.value[0]
    assert first.name == "id"
    assert first.position == 1


def test_created_and_last_modified_are_datetime(recorder: RecordingRunner) -> None:
    overview = inspect(table("small_table"))

    assert isinstance(overview.created.value, datetime)
    assert isinstance(overview.last_modified.value, datetime)


def test_nested_schema(recorder: RecordingRunner) -> None:
    overview = inspect(table("nested_schema"))

    assert overview.num_columns == Fact(4, source="metadata")
    assert overview.num_fields_nested == Fact(12, source="metadata")
    assert overview.num_rows == Fact(10, source="exact")


def test_merge_history_change_data_feed(recorder: RecordingRunner) -> None:
    overview = inspect(table("merge_history"))

    assert overview.change_data_feed == Fact("true", source="metadata")
    assert overview.num_rows == Fact(240, source="exact")


def test_simple_view_without_count_views(recorder: RecordingRunner) -> None:
    overview = inspect(table("simple_view"))

    assert overview.object_type == Fact(ObjectType.VIEW, source="metadata")
    assert overview.num_rows == Fact.unavailable("exact", "Skipped for VIEW; use count_views=True")
    for fact in (
        overview.size_bytes,
        overview.num_files,
        overview.last_modified,
        overview.partition_columns,
        overview.clustering_columns,
        overview.change_data_feed,
        overview.log_retention,
        overview.deleted_file_retention,
    ):
        assert fact == Fact.unavailable("metadata", VIEWS)
    assert overview.avg_file_size_bytes == Fact.unavailable("derived", VIEWS)
    assert overview.num_columns == Fact(4, source="metadata")


def test_simple_view_with_count_views(recorder: RecordingRunner) -> None:
    overview = inspect(table("simple_view"), count_views=True)

    assert overview.num_rows == Fact(1000, source="exact")


@pytest.mark.parametrize("name", TABLES)
def test_to_dict_and_show(
    recorder: RecordingRunner, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    overview = inspect(table(name))

    json.dumps(overview.to_dict())
    overview.show()
    assert overview.table.table in capsys.readouterr().out


def test_table_not_found(recorder: RecordingRunner) -> None:
    with pytest.raises(TableNotFoundError):
        inspect(table("does_not_exist"))
