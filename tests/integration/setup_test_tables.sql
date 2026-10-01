-- dataowl integration test tables
-- ==================================
--
-- Creates the test tables used by tests/integration/test_integration.py in a dedicated
-- schema in a Databricks development workspace. Never run this against production data.
--
-- Requirements
--   * Unity Catalog, and permission to create a schema in the chosen catalog.
--   * Databricks Runtime 14.3 LTS or above, or a Databricks SQL warehouse.
--     The script uses SQL session variables (DECLARE VARIABLE, DBR 14.1+),
--     IDENTIFIER() (DBR 13.3 LTS+) and EXECUTE IMMEDIATE (DBR 14.3+).
--
-- How to run
--   1. Bring the repository into the workspace as a Git folder:
--      Workspace > Create > Git folder, and paste the repository URL.
--   2. Open this file from the Git folder (or paste it into a SQL notebook or the SQL editor)
--      and attach it to a SQL warehouse or a cluster with DBR 14.3 LTS+.
--   3. Edit the two DEFAULT values in "Parameters" below (catalog and schema).
--   4. Run all statements in order, in one session. The variables live only in that session.
--   5. Set DATAOWL_IT_SCHEMA to "<catalog>.<schema>" and run the tests as described at the
--      top of tests/integration/test_integration.py.
--
-- The script is idempotent: every table is dropped and recreated. merge_history is dropped
-- first so its Delta history starts from a known state.
--
-- Data generation is deterministic (range() and modulo expressions). The expected values
-- are written as comments at each table. timestamps_mixed is the exception: its values are
-- relative to current_timestamp() at the time this script runs.
--
-- Note: predictive optimization or auto compaction may add OPTIMIZE commits to the history
-- of merge_history. The expected history below lists only the commits this script makes.


-- ---------------------------------------------------------------------------------------
-- Parameters
-- ---------------------------------------------------------------------------------------

DECLARE OR REPLACE VARIABLE it_catalog STRING DEFAULT 'dev';
DECLARE OR REPLACE VARIABLE it_schema STRING DEFAULT 'dataowl_it';

-- Fully quoted `catalog`.`schema`, used with IDENTIFIER(it_prefix || '.<table>').
DECLARE OR REPLACE VARIABLE it_prefix STRING;
SET VAR it_prefix =
  '`' || replace(it_catalog, '`', '``') || '`.`' || replace(it_schema, '`', '``') || '`';

DECLARE OR REPLACE VARIABLE it_stmt STRING;

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(it_prefix);


-- ---------------------------------------------------------------------------------------
-- small_table
-- ---------------------------------------------------------------------------------------
-- Expected (inspect):
--   object_type     MANAGED
--   format          DELTA
--   num_rows        1000
--   num_columns     4
--   num_fields_nested 4
--   first column    id, position 0 (ordinal_position is documented as numbered from 1,
--                   but observed 0-based in Databricks serverless, October 2026)
--   size_bytes, num_files  available and > 0
--   partition_columns      empty

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.small_table');

CREATE TABLE IDENTIFIER(it_prefix || '.small_table') (
  id BIGINT NOT NULL COMMENT 'Row id, 0..999',
  name STRING COMMENT 'name_<id>',
  amount DECIMAL(18, 2),
  created_at TIMESTAMP
)
USING DELTA
COMMENT 'dataowl integration test: 1000 rows';

INSERT INTO IDENTIFIER(it_prefix || '.small_table')
SELECT
  id,
  concat('name_', id),
  CAST(id % 100 AS DECIMAL(18, 2)),
  timestampadd(MINUTE, id, TIMESTAMP '2026-01-01 00:00:00')
FROM range(1000);


-- ---------------------------------------------------------------------------------------
-- keys_with_duplicates
-- ---------------------------------------------------------------------------------------
-- Rows are generated from r = 0..999 in blocks:
--   r   0..799   key_id = r          key_part = 1 (NULL for r 600..609)
--   r 800..849   key_id = r - 100    key_part = 1   -> second row for (700..749, 1)
--   r 850..899   key_id = r - 100    key_part = 2   -> (750..799, 2), new keys
--   r 900..949   key_id = NULL       key_part = 1
--   r 950..999   key_id = 0          key_part = r % 2 + 1  -> 25 rows part 1, 25 rows part 2
--
-- Expected, key = key_id:
--   total_rows              1000
--   rows_with_null_key        50
--   distinct_keys            800
--   duplicate_keys           101   (700..799 twice each, and key 0 with 51 rows)
--   rows_in_duplicate_keys   251   (100 * 2 + 51)
--   max_rows_per_key          51
--
-- Expected, key = key_id + key_part:
--   total_rows              1000
--   rows_with_null_key        60   (50 with NULL key_id, 10 with NULL key_part)
--   distinct_keys            841
--   duplicate_keys            52   ((700..749, 1) twice each, (0, 1) with 26, (0, 2) with 25)
--   rows_in_duplicate_keys   151   (50 * 2 + 26 + 25)
--   max_rows_per_key          26

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.keys_with_duplicates');

CREATE TABLE IDENTIFIER(it_prefix || '.keys_with_duplicates') (
  key_id BIGINT,
  key_part INT,
  payload STRING
)
USING DELTA;

INSERT INTO IDENTIFIER(it_prefix || '.keys_with_duplicates')
SELECT
  CASE
    WHEN id < 800 THEN id
    WHEN id < 900 THEN id - 100
    WHEN id < 950 THEN NULL
    ELSE 0
  END AS key_id,
  CASE
    WHEN id BETWEEN 600 AND 609 THEN NULL
    WHEN id < 850 THEN 1
    WHEN id < 900 THEN 2
    WHEN id < 950 THEN 1
    ELSE CAST(id % 2 + 1 AS INT)
  END AS key_part,
  concat('row_', id) AS payload
FROM range(1000);


-- ---------------------------------------------------------------------------------------
-- timestamps_mixed
-- ---------------------------------------------------------------------------------------
-- event_ts is TIMESTAMP, event_date is DATE (CAST(event_ts AS DATE)). Both have the same
-- NULLs and the same distribution. "now" is current_timestamp() when this script runs.
--   r   0..599   now - (r % 20 + 1) days   -> 30 rows on each of the days 1..20 days ago
--   r 600..699   now - 2 days              -> 100 more rows 2 days ago
--   r 700..749   NULL
--   r 750..769   now + (r % 5 + 1) days    -> 4 rows on each of the days 1..5 days ahead
--   r 770..799   now - 400 days
--
-- Expected (relative to the time this script ran):
--   total_rows      800
--   nulls            50
--   future values    20   (both columns: values after current_timestamp())
--   min             now - 400 days
--   max             now + 5 days
--   rows per day, when tests run on the same day as this script:
--     days 1..20 ago have rows: 19 days with 30 rows, and 2 days ago with 130 rows (sum 700)
--     today and days 21..30 ago have 0 rows
--     the 30 rows from 400 days ago and the 20 future rows are outside the window

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.timestamps_mixed');

CREATE TABLE IDENTIFIER(it_prefix || '.timestamps_mixed') (
  id BIGINT,
  event_ts TIMESTAMP,
  event_date DATE
)
USING DELTA;

INSERT INTO IDENTIFIER(it_prefix || '.timestamps_mixed')
SELECT id, ts AS event_ts, CAST(ts AS DATE) AS event_date
FROM (
  SELECT
    id,
    CASE
      WHEN id < 600 THEN timestampadd(DAY, -(id % 20 + 1), current_timestamp())
      WHEN id < 700 THEN timestampadd(DAY, -2, current_timestamp())
      WHEN id < 750 THEN NULL
      WHEN id < 770 THEN timestampadd(DAY, id % 5 + 1, current_timestamp())
      ELSE timestampadd(DAY, -400, current_timestamp())
    END AS ts
  FROM range(800)
);


-- ---------------------------------------------------------------------------------------
-- merge_history
-- ---------------------------------------------------------------------------------------
-- Change Data Feed is enabled. Expected inspect: change_data_feed = "true", num_rows 240.
--
-- Expected Delta history, one commit per statement below:
--   version 0  CREATE TABLE
--   version 1  WRITE  (append)     100 rows inserted   ids 0..99         table: 100 rows
--   version 2  WRITE  (append)      50 rows inserted   ids 100..149      table: 150 rows
--   version 3  WRITE  (overwrite)  200 rows written    ids 0..199        table: 200 rows
--   version 4  MERGE                50 updated (150..199), 50 inserted (200..249), 0 deleted
--                                                                         table: 250 rows
--   version 5  UPDATE               30 rows updated    ids 0..29         table: 250 rows
--   version 6  DELETE               10 rows deleted    ids 240..249      table: 240 rows

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.merge_history');

CREATE TABLE IDENTIFIER(it_prefix || '.merge_history') (
  id BIGINT,
  status STRING,
  amount INT
)
USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);

INSERT INTO IDENTIFIER(it_prefix || '.merge_history')
SELECT id, 'new', 1 FROM range(100);

INSERT INTO IDENTIFIER(it_prefix || '.merge_history')
SELECT id + 100, 'new', 1 FROM range(50);

INSERT OVERWRITE IDENTIFIER(it_prefix || '.merge_history')
SELECT id, 'new', 1 FROM range(200);

MERGE INTO IDENTIFIER(it_prefix || '.merge_history') AS t
USING (SELECT id, 'merged' AS status, 2 AS amount FROM range(150, 250)) AS s
ON t.id = s.id
WHEN MATCHED THEN UPDATE SET status = s.status, amount = s.amount
WHEN NOT MATCHED THEN INSERT (id, status, amount) VALUES (s.id, s.status, s.amount);

UPDATE IDENTIFIER(it_prefix || '.merge_history')
SET status = 'updated'
WHERE id < 30;

DELETE FROM IDENTIFIER(it_prefix || '.merge_history')
WHERE id >= 240;


-- ---------------------------------------------------------------------------------------
-- nested_schema
-- ---------------------------------------------------------------------------------------
-- Expected:
--   num_columns         4
--   num_fields_nested  12   counted with the rule in collect/columns.py (count_fields):
--     id, address, lines, attrs                    4 top-level fields
--     address: street, geo, geo.lat, geo.lon       4
--     lines (array element struct): sku, qty       2
--     attrs (map value struct): value, updated     2   (the STRING key adds nothing)
--   num_rows           10

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.nested_schema');

CREATE TABLE IDENTIFIER(it_prefix || '.nested_schema') (
  id BIGINT,
  address STRUCT<street: STRING, geo: STRUCT<lat: DOUBLE, lon: DOUBLE>>,
  lines ARRAY<STRUCT<sku: STRING, qty: INT>>,
  attrs MAP<STRING, STRUCT<value: STRING, updated: TIMESTAMP>>
)
USING DELTA;

INSERT INTO IDENTIFIER(it_prefix || '.nested_schema')
SELECT
  id,
  named_struct(
    'street', concat('street_', id),
    'geo', named_struct('lat', CAST(id AS DOUBLE), 'lon', CAST(-id AS DOUBLE))
  ),
  array(named_struct('sku', concat('sku_', id), 'qty', CAST(id % 3 AS INT))),
  map('k', named_struct('value', concat('v_', id), 'updated', TIMESTAMP '2026-01-01 00:00:00'))
FROM range(10);


-- ---------------------------------------------------------------------------------------
-- simple_view
-- ---------------------------------------------------------------------------------------
-- A persistent view cannot reference session variables in its definition, so the
-- statement is built as a string with the table name inlined.
--
-- Expected (inspect):
--   object_type  VIEW
--   num_rows     n/a without count_views; 1000 with count_views=True
--   detail and property facts  n/a ("Not available for views")
--   num_columns  4

SET VAR it_stmt =
  'CREATE OR REPLACE VIEW ' || it_prefix || '.simple_view AS SELECT * FROM '
  || it_prefix || '.small_table';
EXECUTE IMMEDIATE it_stmt;


-- ---------------------------------------------------------------------------------------
-- Cleanup (disabled). Remove the comment markers and run to delete everything above.
-- ---------------------------------------------------------------------------------------
-- DROP SCHEMA IF EXISTS IDENTIFIER(it_prefix) CASCADE;
