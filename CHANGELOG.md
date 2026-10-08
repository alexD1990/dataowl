# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.2.0] - YYYY-MM-DD

Column analysis (`analyze`) and an excerpt of the Delta history in `inspect`.

### Added
- `dataowl.analyze(table, *, key=None, timestamp=None, compare=None, days=30, spark=None)`
  returning a `ColumnAnalysis` with `to_dict()` and `show(show_days=False)`. Only the
  columns given by the user are analyzed, and at least one of `key`, `timestamp` and
  `compare` is required.
- Key analysis (`key`): rows, rows with null in the key, distinct keys, keys occurring more
  than once and their rows, median and maximum rows per key, and the number of keys with 1,
  2–10, 11–100 and more than 100 rows. Single columns, composite keys and lists of both.
- Declared primary key from `information_schema.table_constraints` and
  `information_schema.key_column_usage`, collected when `key` is given. Shown as
  informational and not enforced.
- Time column analysis (`timestamp`) for `TIMESTAMP`, `TIMESTAMP_NTZ` and `DATE`: min, max,
  nulls and their share, values after the current time, distinct dates, gaps in whole days
  between distinct dates, and rows per day for the last `days` whole days without today.
- Comparison of two time columns (`compare`): rows where the second column is after, equal
  to or before the first, and rows where either is null.
- `show(show_days=True)` prints one line per day in the rows-per-day window.
- History excerpt in `inspect` from `DESCRIBE HISTORY`: new `Overview` fields
  `history_first_commit`, `history_last_commit`, `history_num_commits` and
  `history_operations`. `WRITE` with mode `Overwrite` is counted as `WRITE (overwrite)`.
  Not run for views.

### Changed
- The `inspect` output has a HISTORY block between the summary and the schema, with the
  observation window of the history.

## [0.1.0] - 2026-10-08

First release: overview facts for a table (`inspect`).

### Added
- `dataowl.inspect(table, *, spark=None, count_views=False)` returning an `Overview` with
  `to_dict()` and `show()`.
- Facts from `information_schema.tables`: object type (with the raw `table_type`), format,
  owner, comment and created time.
- Facts from `DESCRIBE DETAIL`: size, number of files, average file size, last data
  modification, partition and clustering columns. Not run for views.
- Schema from `information_schema.columns`, and the field count including nested fields
  from the Spark schema.
- Exact row count with `COUNT(*)`. Skipped for views, materialized views, foreign tables and
  objects whose type cannot be read, unless `count_views=True`.
- Change Data Feed, log retention and deleted file retention from `SHOW TBLPROPERTIES`. Not
  run for views.
- Every fact carries its source (`metadata`, `exact`, `derived`). Facts that cannot be
  collected are marked unavailable with a reason; only a missing table raises
  (`TableNotFoundError`).
- Terminal rendering of the overview.

### Known deviations
- `information_schema.columns.ordinal_position` is observed 0-based in Databricks, although
  the documentation says it is numbered from 1. `ColumnInfo.position` keeps the raw value;
  the `#` column in the output is a running number from 1.
