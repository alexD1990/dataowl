# dataowl

dataowl reports facts about a table in Databricks (Unity Catalog / Delta Lake): size, files,
rows, schema and table properties. It is meant for data engineers who build a dbt staging
model and want the facts without writing exploratory SQL. It reports what exists and never
gives recommendations.

## Requirements

- Unity Catalog.
- Databricks Runtime 14.3 LTS or above, or serverless compute.
  Verified on serverless (Spark Connect).
- PySpark is provided by Databricks. dataowl has no other runtime dependencies.

## Installation

In a Databricks notebook:

```python
%pip install dataowl==0.1.0
```

## Usage

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

## Example output

Output for Databricks' public sample data (`samples.nyctaxi.trips`):

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

SCHEMA
  #  name                   type       nullable  comment
  1  tpep_pickup_datetime   timestamp  yes
  2  tpep_dropoff_datetime  timestamp  yes
  3  trip_distance          double     yes
  4  fare_amount            double     yes
  5  pickup_zip             int        yes
  6  dropoff_zip            int        yes
```

## Principles

- **Read-only.** Only `SELECT`, `DESCRIBE` and `SHOW`. Nothing is written, optimized or
  analyzed.
- **Facts only.** No recommendations or assessments.
- **No row values leave Spark.** Only aggregates and metadata are collected.
- **Unavailable facts are reported with a reason.** Missing permissions or an unsupported
  object type make the affected facts `n/a (<reason>)`; the rest of the report is still
  produced. Only a table that does not exist or is not accessible raises
  `TableNotFoundError`.

## Facts per object type

| Facts | Source | MANAGED, EXTERNAL | STREAMING_TABLE, MATERIALIZED_VIEW | FOREIGN | VIEW | UNKNOWN or n/a type |
|---|---|---|---|---|---|---|
| Object type, format, owner, comment, created | `information_schema.tables` | yes | yes | yes | yes | yes (n/a if the query fails) |
| Size, files, avg file size, last modified, partitioning, clustering | `DESCRIBE DETAIL` | yes | attempted¹ | attempted¹ | no | attempted¹ |
| Columns and schema | `information_schema.columns` | yes | yes | yes | yes | yes |
| Field count incl. nested | Spark schema | yes | yes | yes | yes | yes |
| Change Data Feed, log and deleted file retention | `SHOW TBLPROPERTIES` | yes | attempted¹ | attempted¹ | no | attempted¹ |
| Rows | `COUNT(*)` | yes | MATERIALIZED_VIEW: with `count_views=True`; STREAMING_TABLE: yes | with `count_views=True` | with `count_views=True` | UNKNOWN: yes; n/a type: with `count_views=True` |

¹ The query runs. If Databricks does not support it for the object, the facts are shown as
`n/a (<reason>)`.

"UNKNOWN" is a `table_type` dataowl does not recognize, such as `MANAGED_SHALLOW_CLONE`. The
original value is shown in the header. "n/a type" means the object type could not be read.

## Fields

- **Last modified** is the last data change, from `DESCRIBE DETAIL`. **Created** is from
  `information_schema.tables`.
- **Rows** is an exact `COUNT(*)`. On large tables this costs compute.
- **Files (avg ...)** is the size divided by the number of files.
- **Columns (... incl. nested)** counts every field, including fields inside structs, array
  elements and map keys and values.
- **not set (default)** means the table property is not set, so Databricks uses its default.
  dataowl does not report what the default is.
- **#** in the schema table is a running number from 1 in column order.

## Known deviations

- `information_schema.columns.ordinal_position` is documented as numbered from 1, but has
  been observed 0-based in Databricks (serverless, October 2026). dataowl keeps the raw
  value in `ColumnInfo.position` and shows a running number in the `#` column.

## License

MIT. Copyright (c) 2026 Alexandro Dronnen. See [LICENSE](LICENSE).
