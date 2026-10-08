# dataowl

dataowl reports facts about a table in Databricks (Unity Catalog / Delta Lake): size, files,
rows, schema, table properties and an excerpt of the Delta history, and facts about the
columns you choose: grain and uniqueness of keys, time span and cadence of time columns, and
how two time columns compare. From the Delta history it also reports how the table is
written: commits per day and per hour of day, operations, and rows inserted, updated and
deleted per operation. It is meant for data engineers who build a dbt staging model and want
the facts without writing exploratory SQL. It reports what exists and never gives
recommendations.

## Requirements

- Unity Catalog.
- Databricks Runtime 14.3 LTS or above, or serverless compute.
  Verified on serverless (Spark Connect).
- PySpark is provided by Databricks. dataowl has no other runtime dependencies.

## Installation

In a Databricks notebook:

```python
%pip install dataowl==0.3.0
```

## Usage

dataowl works in two steps: `inspect` gives an overview of the table, and `analyze` gives
facts about columns you choose. In addition, `history` gives detailed facts from the Delta
history about how the table is written.

`inspect`, `analyze` and `history` raise `TableNotFoundError` when the table does not exist
or is not accessible, including when the catalog does not exist.

### inspect

```python
import dataowl

dataowl.inspect("catalog.schema.table").show()
```

As a JSON-serializable dict, where every fact has a value, a source and a reason when it
is unavailable:

```python
overview = dataowl.inspect("catalog.schema.table")
overview.to_dict()
```

Row counts are skipped for views, materialized views and foreign tables unless you ask for
them:

```python
dataowl.inspect("catalog.schema.some_view", count_views=True).show()
```

Names with characters other than letters, digits and underscore are quoted with backticks:
`` dataowl.inspect("`my-catalog`.schema.table") ``.

The overview includes an excerpt of the Delta history from `DESCRIBE HISTORY`: the oldest and
newest commit, the number of commits and the number of commits per operation. The header of
the HISTORY block always shows the observation window (oldest – newest commit), because an
operation that is missing is only missing for the period the history covers:

- The history is limited by `delta.logRetentionDuration` (see **Log retention**). Older
  commits are not included.
- `WRITE (overwrite)` is a `WRITE` commit with mode `Overwrite`. It also includes partial
  overwrites, for example with `replaceWhere`.
- `CREATE OR REPLACE TABLE AS SELECT`, which dbt uses for the `table` materialization, is
  shown under its own operation name, not as `WRITE (overwrite)`.

### analyze

```python
analysis = dataowl.analyze(
    "catalog.schema.table",
    key=["customer_id", ("customer_id", "order_id")],
    timestamp="updated_at",
    compare=("created_at", "updated_at"),
)
analysis.show()
```

Full signature:

```python
analyze(
    table: str,
    *,
    key: str | tuple[str, ...] | list[str | tuple[str, ...]] | None = None,
    timestamp: str | list[str] | None = None,
    compare: tuple[str, str] | None = None,
    days: int = 30,
    spark: SparkSession | None = None,
) -> ColumnAnalysis
```

At least one of `key`, `timestamp` and `compare` must be given. Only the columns you give are
analyzed; dataowl never chooses or suggests columns. Each key, each timestamp column and the
comparison run their own queries against the table's data, so every column or combination
you add costs compute. Column names are matched case insensitively.

- **key** analyzes grain and uniqueness. A string is one column, a tuple is one composite
  key, and a list holds several keys of either kind, each analyzed separately:
  `key=["customer_id", ("customer_id", "order_id")]` analyzes `customer_id` on its own and
  the combination of `customer_id` and `order_id`. MAP columns cannot be used.
- **Primary key.** When `key` is given, dataowl also reads the declared primary key from
  `information_schema`. A primary key in Databricks is informational and not enforced. In
  `ColumnAnalysis.primary_key`, `None` means it was not collected (no `key`), a fact with
  value `None` means no primary key is declared, and an unavailable fact has a reason.
- **timestamp** analyzes one column or a list of columns of type `TIMESTAMP`,
  `TIMESTAMP_NTZ` or `DATE`, each separately: min, max, nulls, values after the current
  time, distinct dates, the gaps in whole days between distinct dates, and rows per day.
  Other types, including dates stored as `STRING`, raise `ValueError`.
- **days** (default 30) sets the window for rows per day: the last `days` whole days, not
  including today, in the session time zone. Days without rows count as 0.
- **compare** counts the rows where the second column is after, equal to or before the
  first, and the rows where at least one of them is null. Columns of different types are
  compared with Spark's implicit type conversion: a `DATE` is compared as that date at
  00:00:00, so a `DATE` on the same day as a `TIMESTAMP` later than midnight counts as
  before it.

```python
analysis.show(show_days=True)  # also prints one line per day in the window
analysis.to_dict()  # JSON-serializable, like Overview.to_dict()
```

`analyze` raises:

- `ValueError` for an invalid table name, when `key`, `timestamp` and `compare` are all
  `None`, for invalid `days`, and for an unknown or unsupported column. All input is
  validated before any query against the table's data.
- `RuntimeError` when no SparkSession is available, and when the schema cannot be read from
  `information_schema.columns`, since the columns cannot then be validated.
- `TableNotFoundError` when the table does not exist or is not accessible, including when
  the catalog does not exist.

Every other failure makes the affected facts `n/a (<reason>)`.

### history

```python
dataowl.history("catalog.schema.table").show()
dataowl.history("catalog.schema.table", limit=100).show()  # only the newest 100 commits
```

Full signature:

```python
history(
    table: str,
    *,
    spark: SparkSession | None = None,
    limit: int | None = None,
) -> HistoryAnalysis
```

`history` reads `DESCRIBE HISTORY` and reports:

- **The observation window:** the oldest and newest commit, the number of days and the
  number of commits. The window is shown at the top, because every other number only
  covers this period.
- **Commits per day:** median, min and max over every calendar day in the window. Days
  without commits count as 0.
- **Commits per hour of day:** the number of commits in each hour, 0–23.
- **Operations:** the number of commits per operation, with `WRITE (overwrite)` as its own
  category, as in `inspect`.
- **Rows per operation:** inserted, updated and deleted rows, from `operationMetrics`.

`limit` reads only the newest `limit` commits (`DESCRIBE HISTORY ... LIMIT <limit>`). Without
`limit`, every commit in the history is read.

Commit timestamps are used as Databricks returns them, in the session time zone, without
conversion. Days and hours of day are counted in that time zone, and its name is shown as
**Time zone** (from `current_timezone()`). The number of days counts calendar days from the
date of the oldest commit to the date of the newest, both included.

Rows per operation are based on these `operationMetrics` keys:

| Operation | inserted | updated | deleted |
|---|---|---|---|
| `MERGE` | `numTargetRowsInserted` | `numTargetRowsUpdated` | `numTargetRowsDeleted` |
| `WRITE`, `WRITE (overwrite)`, operations ending with `AS SELECT` | `numOutputRows` | | |
| `UPDATE` | | `numUpdatedRows` | |
| `DELETE` | | | `numDeletedRows` |

Other combinations of operation and row type are not shown. **commits** is shown as the
number of commits that have the metric out of all commits of the operation, for example
`27/29`. Sum, median and max cover only the commits that have the metric, and are shown as
`–` when none has it. If a metric has an unexpected format, only the rows per operation are
`n/a (<reason>)`.

The output ends with three notes:

- The history is limited by `delta.logRetentionDuration`; the counts cover the window shown.
- Rows replaced by `WRITE (overwrite)` are not counted as deleted.
- The oldest day in the window may be incomplete, because `limit` or the log retention can
  cut the history in the middle of a day.

```python
analysis = dataowl.history("catalog.schema.table")
analysis.to_dict()  # JSON-serializable, like Overview.to_dict()
```

For views, every fact except the time zone is `n/a (Not available for views)`, and
`DESCRIBE HISTORY` is not run.

`history` raises:

- `ValueError` for an invalid table name, and when `limit` is not `None` or an integer of
  at least 1.
- `RuntimeError` when no SparkSession is available.
- `TableNotFoundError` when the table does not exist or is not accessible, including when
  the catalog does not exist.

Every other failure makes the affected facts `n/a (<reason>)`.

## Example output

Output for Databricks' public sample data (`samples.nyctaxi.trips`).

```python
dataowl.inspect("samples.nyctaxi.trips").show()
```

```text
samples.nyctaxi.trips   (MANAGED, DELTA)

Size:                    354.2 KB
Files:                   1  (avg 354.2 KB)
Rows:                    21 932
Columns:                 6  (6 incl. nested)
Partitioned by:          –
Clustered by:            –
Change Data Feed:        true
Log retention:           not set (default)
Deleted file retention:  not set (default)
Created:                 2025-09-30 11:28
Last modified:           2026-09-14 15:07
Owner:                   System user
Comment:                 –

HISTORY  (2025-09-09 15:05 – 2026-09-14 15:07, 223 commits)
  CREATE OR REPLACE TABLE AS SELECT:  212
  SET TBLPROPERTIES:                  11

SCHEMA
  #  name                   type       nullable  comment
  1  tpep_pickup_datetime   timestamp  yes
  2  tpep_dropoff_datetime  timestamp  yes
  3  trip_distance          double     yes
  4  fare_amount            double     yes
  5  pickup_zip             int        yes
  6  dropoff_zip            int        yes
```

```python
dataowl.analyze(
    "samples.nyctaxi.trips",
    key=["pickup_zip", ("pickup_zip", "dropoff_zip")],
    timestamp="tpep_pickup_datetime",
    compare=("tpep_pickup_datetime", "tpep_dropoff_datetime"),
).show()
```

```text
samples.nyctaxi.trips

PRIMARY KEY  none declared

KEY  pickup_zip
  Rows:                    21 932
  Rows with null in key:   0
  Distinct keys:           128
  Keys occurring >1 time:  100
  Rows in those keys:      21 904
  Rows per key:            median 19 · max 1 227
    1 row:                 28
    2–10 rows:             27
    11–100 rows:           31
    >100 rows:             42

KEY  pickup_zip + dropoff_zip
  Rows:                    21 932
  Rows with null in key:   0
  Distinct keys:           3 369
  Keys occurring >1 time:  2 060
  Rows in those keys:      20 623
  Rows per key:            median 2 · max 143
    1 row:                 1 309
    2–10 rows:             1 533
    11–100 rows:           518
    >100 rows:             9

TIMESTAMP  tpep_pickup_datetime
  Min:             2016-01-01 00:04
  Max:             2016-02-29 23:51
  Nulls:           0 (0.00 %)
  Future values:   0
  Distinct dates:  60
  Days between distinct dates:
    min 1 · median 1 · mean 1 · max 1
  Rows per day, last 30 complete days (2026-09-08 – 2026-10-07):
    median 0 · mean 0 · min 0 · max 0
  Note: gaps are measured in whole days; cadence below one day is not visible.
  Note: counts reflect current values of the column, not historical changes.

COMPARE  tpep_pickup_datetime → tpep_dropoff_datetime
  tpep_dropoff_datetime > tpep_pickup_datetime:  21 931
  tpep_dropoff_datetime = tpep_pickup_datetime:  1
  tpep_dropoff_datetime < tpep_pickup_datetime:  0
  Either is null:                                0
```

The data in `samples.nyctaxi.trips` is from January and February 2016, so every day in the
rows-per-day window has 0 rows.

```python
dataowl.history("samples.nyctaxi.trips").show()
```

<!-- TODO before release: real output of history("samples.nyctaxi.trips") -->

## Principles

- **Read-only.** Only `SELECT`, `DESCRIBE` and `SHOW`. Nothing is written, optimized or
  analyzed.
- **Facts only.** No recommendations or assessments.
- **No row values leave Spark.** Only aggregates and metadata are collected. Min and max of
  time columns are the only exception.
- **You choose the columns.** `analyze` only analyzes the columns you give.
- **Unavailable facts are reported with a reason.** Missing permissions or an unsupported
  object type make the affected facts `n/a (<reason>)`; the rest of the report is still
  produced. Only a table that does not exist or is not accessible, including a table in a
  catalog that does not exist, raises `TableNotFoundError`.

## Facts per object type

| Facts | Source | MANAGED, EXTERNAL | STREAMING_TABLE, MATERIALIZED_VIEW | FOREIGN | VIEW | UNKNOWN or n/a type |
|---|---|---|---|---|---|---|
| Object type, format, owner, comment, created | `information_schema.tables` | yes | yes | yes | yes | yes (n/a if the query fails) |
| Size, files, avg file size, last modified, partitioning, clustering | `DESCRIBE DETAIL` | yes | attempted¹ | attempted¹ | no | attempted¹ |
| Columns and schema | `information_schema.columns` | yes | yes | yes | yes | yes |
| Field count incl. nested | Spark schema | yes | yes | yes | yes | yes |
| Change Data Feed, log and deleted file retention | `SHOW TBLPROPERTIES` | yes | attempted¹ | attempted¹ | no | attempted¹ |
| History excerpt | `DESCRIBE HISTORY` | yes | attempted¹ | attempted¹ | no | attempted¹ |
| `history()`: window, commits, operations, rows per operation | `DESCRIBE HISTORY` | yes | attempted¹ | attempted¹ | no | attempted¹ |
| Rows | `COUNT(*)` | yes | MATERIALIZED_VIEW: with `count_views=True`; STREAMING_TABLE: yes | with `count_views=True` | with `count_views=True` | UNKNOWN: yes; n/a type: with `count_views=True` |

¹ The query runs. If Databricks does not support it for the object, the facts are shown as
`n/a (<reason>)`.

"UNKNOWN" is a `table_type` dataowl does not recognize, such as `MANAGED_SHALLOW_CLONE`. The
original value is shown in the header. "n/a type" means the object type could not be read.

## Fields

### inspect

- **Last modified** is the last data change, from `DESCRIBE DETAIL`. **Created** is from
  `information_schema.tables`.
- **Rows** is an exact `COUNT(*)`. On large tables this costs compute.
- **Files (avg ...)** is the size divided by the number of files.
- **Columns (... incl. nested)** counts every field, including fields inside structs, array
  elements and map keys and values.
- **not set (default)** means the table property is not set, so Databricks uses its default.
  dataowl does not report what the default is.
- **#** in the schema table is a running number from 1 in column order.
- **HISTORY** shows the oldest and newest commit in the available history, the number of
  commits, and the number of commits per operation.

### analyze

- **PRIMARY KEY** is the declared primary key, in declared order. It is informational and
  not enforced by Databricks. The line is left out when `key` is not given.
- **KEY ... Rows** is every row in the table, including rows with null in a key column.
- **Rows with null in key** counts rows where at least one key column is null. These rows
  are left out of every other line in the KEY block.
- **Distinct keys** is the number of distinct key values without null.
- **Keys occurring >1 time** is the number of key values that occur in more than one row.
  **Rows in those keys** is the number of rows that have one of those key values.
- **Rows per key** is the median and the maximum number of rows per key value, followed by
  the number of key values with 1, 2–10, 11–100 and more than 100 rows.
- **TIMESTAMP ... Nulls** is the number of rows with null, and their share of all rows.
- **Future values** counts values after the current time of the session. A `DATE` is
  compared as that date at 00:00:00.
- **Days between distinct dates** is measured in whole days between consecutive distinct
  dates. Cadence below one day is not visible.
- **Rows per day** covers the last `days` whole days without today, in the session time
  zone, and counts current values of the column, not historical changes.
- **COMPARE first → second** counts rows where the second column is after (`>`), equal to
  (`=`) or before (`<`) the first. Rows with null in either column are counted only in
  **Either is null**, so the four numbers add up to all rows.
- Medians and means are rounded to one decimal and shown without decimals when the rounded
  value is whole.

### history

- **HISTORY (... days, ... commits)** is the oldest and newest commit, the number of
  calendar days from the date of the oldest to the date of the newest commit, both
  included, and the number of commits.
- **Limit** is shown only when `limit` is given: only the newest `limit` commits are read.
- **Time zone** is the session time zone, from `current_timezone()`. Commit timestamps,
  days and hours of day are in this time zone.
- **Commits per day** is the median, min and max over every day in the window, with 0 for
  days without commits.
- **Commits per hour of day** lists the hours of day that have commits, with the number of
  commits.
- **OPERATIONS** is the number of commits per operation, as in the HISTORY block of
  `inspect`.
- **ROWS** has one line per operation and row type (inserted, updated, deleted) with a
  metric, see the table under **history** above. **commits** is the number of commits with
  the metric out of all commits of the operation. **sum**, **median** and **max** cover the
  commits with the metric, and are `–` when none has it.

## Known deviations

- `information_schema.columns.ordinal_position` is documented as numbered from 1, but has
  been observed 0-based in Databricks (serverless, October 2026). dataowl keeps the raw
  value in `ColumnInfo.position` and shows a running number in the `#` column.

## License

MIT. Copyright (c) 2026 Alexandro Dronnen. See [LICENSE](LICENSE).
