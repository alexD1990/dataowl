# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.1.0] - 2026-10-01

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
  unknown object types unless `count_views=True`.
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
