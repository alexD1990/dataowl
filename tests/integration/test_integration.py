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
- Run the setup script and the tests on the same day and in the same session time zone:
  expected values for timestamps_mixed are relative to the time the setup script ran (used
  from step 17). The rows-per-day window is the last `days` whole days without today, so
  with days=30 it covers the days 1..30 before the day the script ran.
- DATAOWL_IT_SCHEMA is read when the module is imported. If pytest.main runs again in the
  same notebook with another value, the old value is used. Run
  dbutils.library.restartPython() before changing the value.

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
from datetime import date, datetime, timedelta  # noqa: E402
from typing import TYPE_CHECKING, Any  # noqa: E402

from conftest import assert_read_only  # noqa: E402

import dataowl  # noqa: E402
from dataowl import (  # noqa: E402
    ColumnAnalysis,
    HistoryAnalysis,
    TableNotFoundError,
    analyze,
    history,
    inspect,
)
from dataowl.identifiers import TableRef  # noqa: E402
from dataowl.model.analysis import DayCount, KeyAnalysis  # noqa: E402
from dataowl.model.facts import Fact  # noqa: E402
from dataowl.model.history import RowStats  # noqa: E402
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
    "orders_composite",
    "cadence_weekly",
    "created_updated",
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
    """Route inspect(), analyze() and history() through a RecordingRunner (principle 2)."""
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


def test_ordinal_position_observed_base(recorder: RecordingRunner) -> None:
    # Documents observed behaviour that differs from the Databricks documentation: the
    # documentation says ordinal_position is numbered from 1, but Databricks (serverless,
    # October 2026) returns 0-based positions. ColumnInfo.position keeps the raw value.
    overview = inspect(table("small_table"))

    assert overview.columns.value is not None
    first = overview.columns.value[0]
    assert first.name == "id"
    assert first.position == 0
    positions = [column.position for column in overview.columns.value]
    assert positions == list(range(positions[0], positions[0] + len(positions)))


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


def test_catalog_not_found(recorder: RecordingRunner) -> None:
    missing = "dataowl_no_such_catalog_it.s.t"

    with pytest.raises(TableNotFoundError):
        inspect(missing)
    with pytest.raises(TableNotFoundError):
        analyze(missing, key="id")
    with pytest.raises(TableNotFoundError):
        history(missing)


# analyze() and the inspect history excerpt. Expected values are the comments at each table
# in setup_test_tables.sql.

KEY_FIELDS = (
    "total_rows",
    "rows_with_null_key",
    "distinct_keys",
    "duplicate_keys",
    "rows_in_duplicate_keys",
    "max_rows_per_key",
    "median_rows_per_key",
    "keys_with_1_row",
    "keys_with_2_10_rows",
    "keys_with_11_100_rows",
    "keys_with_over_100_rows",
)


def key_values(key: KeyAnalysis) -> dict[str, Any]:
    """The value of every fact, after checking that each one is available and exact."""
    result = {}
    for name in KEY_FIELDS:
        fact = getattr(key, name)
        assert fact.available, f"{name}: {fact.reason}"
        assert fact.source == "exact"
        result[name] = fact.value
    return result


def session_today(runner: RecordingRunner) -> date:
    """current_date() of the Spark session, which is the date analyze() uses."""
    today = runner.query("SELECT current_date() AS today")[0]["today"]
    assert isinstance(today, date) and not isinstance(today, datetime)
    return today


def test_analyze_keys_with_duplicates(recorder: RecordingRunner) -> None:
    analysis = analyze(table("keys_with_duplicates"), key=["key_id", ("key_id", "key_part")])

    single, composite = analysis.keys
    assert single.columns == ("key_id",)
    assert key_values(single) == {
        "total_rows": 1000,
        "rows_with_null_key": 50,
        "distinct_keys": 800,
        "duplicate_keys": 101,
        "rows_in_duplicate_keys": 251,
        "max_rows_per_key": 51,
        "median_rows_per_key": 1.0,
        "keys_with_1_row": 699,
        "keys_with_2_10_rows": 100,
        "keys_with_11_100_rows": 1,
        "keys_with_over_100_rows": 0,
    }
    assert composite.columns == ("key_id", "key_part")
    assert key_values(composite) == {
        "total_rows": 1000,
        "rows_with_null_key": 60,
        "distinct_keys": 841,
        "duplicate_keys": 52,
        "rows_in_duplicate_keys": 151,
        "max_rows_per_key": 26,
        "median_rows_per_key": 1.0,
        "keys_with_1_row": 789,
        "keys_with_2_10_rows": 50,
        "keys_with_11_100_rows": 2,
        "keys_with_over_100_rows": 0,
    }
    assert analysis.primary_key == Fact(None, source="metadata")


def test_analyze_orders_composite(recorder: RecordingRunner) -> None:
    analysis = analyze(
        table("orders_composite"), key=["customer_id", "order_id", ("customer_id", "order_id")]
    )

    customer, order, combination = analysis.keys
    assert key_values(customer) == {
        "total_rows": 600,
        "rows_with_null_key": 0,
        "distinct_keys": 50,
        "duplicate_keys": 50,
        "rows_in_duplicate_keys": 600,
        "max_rows_per_key": 12,
        "median_rows_per_key": 12.0,
        "keys_with_1_row": 0,
        "keys_with_2_10_rows": 0,
        "keys_with_11_100_rows": 50,
        "keys_with_over_100_rows": 0,
    }
    assert key_values(order) == {
        "total_rows": 600,
        "rows_with_null_key": 0,
        "distinct_keys": 12,
        "duplicate_keys": 12,
        "rows_in_duplicate_keys": 600,
        "max_rows_per_key": 50,
        "median_rows_per_key": 50.0,
        "keys_with_1_row": 0,
        "keys_with_2_10_rows": 0,
        "keys_with_11_100_rows": 12,
        "keys_with_over_100_rows": 0,
    }
    assert key_values(combination) == {
        "total_rows": 600,
        "rows_with_null_key": 0,
        "distinct_keys": 600,
        "duplicate_keys": 0,
        "rows_in_duplicate_keys": 0,
        "max_rows_per_key": 1,
        "median_rows_per_key": 1.0,
        "keys_with_1_row": 600,
        "keys_with_2_10_rows": 0,
        "keys_with_11_100_rows": 0,
        "keys_with_over_100_rows": 0,
    }

    primary_key = analysis.primary_key
    assert primary_key is not None
    assert primary_key.available, primary_key.reason
    assert primary_key.source == "metadata"
    assert primary_key.value is not None
    assert tuple(name.lower() for name in primary_key.value) == ("order_id", "customer_id")


def test_analyze_without_primary_key(recorder: RecordingRunner) -> None:
    analysis = analyze(table("small_table"), key="id")

    assert analysis.primary_key == Fact(None, source="metadata")


def test_analyze_timestamps_mixed(recorder: RecordingRunner) -> None:
    analysis = analyze(table("timestamps_mixed"), timestamp=["event_ts", "event_date"])
    today = session_today(recorder)

    expected_days = tuple(
        DayCount(today - timedelta(days=offset), 130 if offset == 2 else 30 if offset <= 20 else 0)
        for offset in range(30, 0, -1)
    )
    assert analysis.primary_key is None
    assert [t.column for t in analysis.timestamps] == ["event_ts", "event_date"]
    for timestamp in analysis.timestamps:
        assert timestamp.days == 30
        assert timestamp.total_rows == Fact(800, source="exact")
        assert timestamp.null_rows == Fact(50, source="exact")
        assert timestamp.null_share == Fact(0.0625, source="derived")
        assert timestamp.future_values == Fact(20, source="exact"), timestamp.column
        assert timestamp.distinct_dates == Fact(26, source="exact")
        assert timestamp.min_gap_days == Fact(1, source="exact")
        assert timestamp.median_gap_days == Fact(1.0, source="exact")
        assert timestamp.mean_gap_days.value == pytest.approx(16.2)
        assert timestamp.max_gap_days == Fact(380, source="exact")
        assert timestamp.window_first_day == Fact(today - timedelta(days=30), source="derived")
        assert timestamp.window_last_day == Fact(today - timedelta(days=1), source="derived")
        assert timestamp.rows_per_day == Fact(expected_days, source="derived")
        assert timestamp.rows_per_day.value is not None
        assert len(timestamp.rows_per_day.value) == 30
        assert timestamp.rows_per_day_min == Fact(0, source="derived")
        assert timestamp.rows_per_day_median == Fact(30.0, source="derived")
        assert timestamp.rows_per_day_mean.value == pytest.approx(700 / 30)
        assert timestamp.rows_per_day_max == Fact(130, source="derived")

    event_ts, event_date = analysis.timestamps
    assert isinstance(event_ts.min_value.value, datetime)
    assert isinstance(event_ts.max_value.value, datetime)
    assert event_ts.min_value.value.date() == today - timedelta(days=400)
    assert event_ts.max_value.value.date() == today + timedelta(days=5)
    assert event_date.min_value == Fact(today - timedelta(days=400), source="exact")
    assert event_date.max_value == Fact(today + timedelta(days=5), source="exact")
    assert not isinstance(event_date.min_value.value, datetime)


def test_analyze_cadence_weekly(recorder: RecordingRunner) -> None:
    analysis = analyze(table("cadence_weekly"), timestamp="event_date")

    [timestamp] = analysis.timestamps
    assert timestamp.total_rows == Fact(57, source="exact")
    assert timestamp.null_rows == Fact(0, source="exact")
    assert timestamp.min_value == Fact(date(2026, 1, 5), source="exact")
    assert timestamp.max_value == Fact(date(2026, 5, 18), source="exact")
    assert timestamp.future_values == Fact(0, source="exact")
    assert timestamp.distinct_dates == Fact(19, source="exact")
    assert timestamp.min_gap_days == Fact(7, source="exact")
    assert timestamp.median_gap_days == Fact(7.0, source="exact")
    assert timestamp.mean_gap_days.available
    assert timestamp.mean_gap_days.value == pytest.approx(133 / 18)
    assert timestamp.max_gap_days == Fact(14, source="exact")


def test_analyze_created_updated(recorder: RecordingRunner) -> None:
    timestamps = analyze(table("created_updated"), compare=("created_at", "updated_at"))
    dates = analyze(table("created_updated"), compare=("created_at", "updated_date"))

    assert timestamps.comparison is not None
    assert timestamps.comparison.second_after_first == Fact(40, source="exact")
    assert timestamps.comparison.second_equal_first == Fact(30, source="exact")
    assert timestamps.comparison.second_before_first == Fact(10, source="exact")
    assert timestamps.comparison.either_null == Fact(20, source="exact")

    # updated_date is compared as that date at 00:00:00, so the same day as created_at
    # (10:30) counts as before.
    assert dates.comparison is not None
    assert dates.comparison.second_data_type.lower() == "date"
    assert dates.comparison.second_after_first == Fact(20, source="exact")
    assert dates.comparison.second_equal_first == Fact(0, source="exact")
    assert dates.comparison.second_before_first == Fact(60, source="exact")
    assert dates.comparison.either_null == Fact(20, source="exact")


def test_merge_history_excerpt(recorder: RecordingRunner) -> None:
    overview = inspect(table("merge_history"))

    operations = overview.history_operations
    assert operations.available, operations.reason
    assert operations.value is not None
    # Operation categories as collect/history.py produces them. INSERT OVERWRITE is a WRITE
    # commit with mode Overwrite, counted as "WRITE (overwrite)".
    known = {"WRITE": 2, "WRITE (overwrite)": 1, "MERGE": 1, "UPDATE": 1, "DELETE": 1}
    for operation, count in known.items():
        assert operations.value.get(operation) == count, (operation, operations.value)
    # Assumption verified here: Databricks records the CREATE TABLE statement as the
    # operation "CREATE TABLE".
    assert operations.value.get("CREATE TABLE") == 1, operations.value
    # Other operations, such as OPTIMIZE from predictive optimization, are allowed.
    num_commits = overview.history_num_commits
    assert num_commits.available and num_commits.value is not None
    assert num_commits.value >= 7
    assert num_commits.value == sum(operations.value.values())
    first, last = overview.history_first_commit, overview.history_last_commit
    assert isinstance(first.value, datetime) and isinstance(last.value, datetime)
    assert first.value <= last.value


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("keys_with_duplicates", {"key": ["key_id", ("key_id", "key_part")]}),
        ("orders_composite", {"key": ("customer_id", "order_id")}),
        ("timestamps_mixed", {"timestamp": ["event_ts", "event_date"]}),
        ("cadence_weekly", {"timestamp": "event_date", "days": 7}),
        ("created_updated", {"compare": ("created_at", "updated_date")}),
    ],
)
def test_analyze_to_dict_and_show(
    recorder: RecordingRunner,
    capsys: pytest.CaptureFixture[str],
    name: str,
    kwargs: dict[str, Any],
) -> None:
    analysis = analyze(table(name), **kwargs)

    assert isinstance(analysis, ColumnAnalysis)
    json.dumps(analysis.to_dict())
    analysis.show()
    analysis.show(show_days=True)
    assert analysis.table.table in capsys.readouterr().out


# history(). Expected values are the comments at merge_history in setup_test_tables.sql.

# (operation, rows) -> (commits with metric, commits, sum, median, max)
EXPECTED_ROW_STATS = {
    ("WRITE", "inserted"): (2, 2, 150, 75.0, 100),
    ("WRITE (overwrite)", "inserted"): (1, 1, 200, 200.0, 200),
    ("MERGE", "inserted"): (1, 1, 50, 50.0, 50),
    ("MERGE", "updated"): (1, 1, 50, 50.0, 50),
    ("MERGE", "deleted"): (1, 1, 0, 0.0, 0),
    ("UPDATE", "updated"): (1, 1, 30, 30.0, 30),
    ("DELETE", "deleted"): (1, 1, 10, 10.0, 10),
}


def row_stats_by_key(analysis: HistoryAnalysis) -> dict[tuple[str, str], RowStats]:
    assert analysis.row_stats.available, analysis.row_stats.reason
    assert analysis.row_stats.value is not None
    return {(stats.operation, stats.kind): stats for stats in analysis.row_stats.value}


def test_history_merge_history(recorder: RecordingRunner) -> None:
    analysis = history(table("merge_history"))

    # Assumption verified here: current_timezone() returns the session time zone as a
    # non-empty string.
    time_zone = analysis.time_zone
    assert time_zone.available, time_zone.reason
    assert isinstance(time_zone.value, str) and time_zone.value

    operations = analysis.operations
    assert operations.available, operations.reason
    assert operations.value is not None
    known = {
        "CREATE TABLE": 1,
        "WRITE": 2,
        "WRITE (overwrite)": 1,
        "MERGE": 1,
        "UPDATE": 1,
        "DELETE": 1,
    }
    for operation, count in known.items():
        assert operations.value.get(operation) == count, (operation, operations.value)
    # Other operations, such as OPTIMIZE from predictive optimization, are allowed.

    # Assumptions verified here: UPDATE reports numUpdatedRows and DELETE numDeletedRows,
    # also when the table uses deletion vectors (the default for new tables in recent
    # Databricks Runtime and serverless). MERGE reports numTargetRowsDeleted as 0 when
    # nothing is deleted, rather than leaving the key out.
    stats = row_stats_by_key(analysis)
    for key, (with_metric, commits, total, median, maximum) in EXPECTED_ROW_STATS.items():
        assert key in stats, (key, sorted(stats))
        assert stats[key] == RowStats(*key, commits, with_metric, total, median, maximum), key
    assert not any(operation in {"CREATE TABLE", "OPTIMIZE"} for operation, _ in stats)

    num_commits = analysis.num_commits
    assert num_commits.available and num_commits.value is not None
    assert num_commits.value == sum(operations.value.values())
    assert analysis.num_days.available, analysis.num_days.reason
    assert analysis.num_days.value is not None and analysis.num_days.value >= 1
    per_hour = analysis.commits_per_hour
    assert per_hour.available, per_hour.reason
    assert per_hour.value is not None and len(per_hour.value) == 24
    assert sum(hour.commits for hour in per_hour.value) == num_commits.value
    assert analysis.limit is None
    assert all("LIMIT" not in sql.upper() for sql, _ in recorder.queries)


def test_history_merge_history_limit(recorder: RecordingRunner) -> None:
    analysis = history(table("merge_history"), limit=3)

    history_queries = [sql for sql, _ in recorder.queries if sql.startswith("DESCRIBE HISTORY")]
    assert len(history_queries) == 1
    assert history_queries[0].endswith(" LIMIT 3"), history_queries[0]
    assert analysis.limit == 3
    assert analysis.num_commits == Fact(3, source="metadata")

    operations = analysis.operations
    assert operations.available and operations.value is not None
    # OPTIMIZE commits made after the setup script can push MERGE, UPDATE or DELETE out of
    # the three newest commits, so the operations are checked only without OPTIMIZE.
    if "OPTIMIZE" not in operations.value:
        assert operations.value == {"DELETE": 1, "MERGE": 1, "UPDATE": 1}


def test_history_simple_view(recorder: RecordingRunner) -> None:
    analysis = history(table("simple_view"))

    assert analysis.time_zone.available, analysis.time_zone.reason
    facts = {
        name: value
        for name, value in vars(analysis).items()
        if isinstance(value, Fact) and name != "time_zone"
    }
    assert facts
    for name, fact in facts.items():
        assert fact == Fact.unavailable(fact.source, VIEWS), name
    assert not any(sql.startswith("DESCRIBE HISTORY") for sql, _ in recorder.queries)


@pytest.mark.parametrize("name", ["merge_history", "small_table"])
def test_history_to_dict_and_show(
    recorder: RecordingRunner, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    analysis = history(table(name))

    assert isinstance(analysis, HistoryAnalysis)
    json.dumps(analysis.to_dict())
    analysis.show()
    out = capsys.readouterr().out
    assert analysis.table.table in out
    assert "HISTORY  (" in out
