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
-- relative to current_timestamp() at the time this script runs, so the tests must run on
-- the same day as this script, in the same session time zone.
--
-- Declared primary keys: only orders_composite has one, PRIMARY KEY (order_id, customer_id).
-- Primary keys in Databricks are informational and not enforced. analyze(...).primary_key
-- is collected only when key is given:
--   orders_composite        Fact(("order_id", "customer_id"))   declared order, not sorted
--   every other table       Fact(None)                          no primary key declared
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
--   median_rows_per_key      1.0   (800 counts: 699 ones, 100 twos, one 51; the exact
--                                   median of 800 values is the mean of the 400th and 401st
--                                   sorted values, both 1)
--   keys_with_1_row          699   (800 - 101)
--   keys_with_2_10_rows      100
--   keys_with_11_100_rows      1   (key 0)
--   keys_with_over_100_rows    0
--   check: 699 + 100 * 2 + 51 = 950 = 1000 - 50 rows with a non-null key
--
-- Expected, key = key_id + key_part:
--   total_rows              1000
--   rows_with_null_key        60   (50 with NULL key_id, 10 with NULL key_part)
--   distinct_keys            841
--   duplicate_keys            52   ((700..749, 1) twice each, (0, 1) with 26, (0, 2) with 25)
--   rows_in_duplicate_keys   151   (50 * 2 + 26 + 25)
--   max_rows_per_key          26
--   median_rows_per_key      1.0   (841 counts: 789 ones, 50 twos, 26 and 25; the 421st
--                                   sorted value is 1)
--   keys_with_1_row          789   (841 - 52)
--   keys_with_2_10_rows       50
--   keys_with_11_100_rows      2   ((0, 1) and (0, 2))
--   keys_with_over_100_rows    0
--   check: 789 + 50 * 2 + 26 + 25 = 940 = 1000 - 60 rows without NULL in the key

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
-- Expected (relative to the time this script ran), the same for both columns unless noted:
--   total_rows      800
--   null_rows        50   (r 700..749)
--   null_share     0.0625 (50 / 800)
--   future_values    20   (r 750..769). For event_date, a DATE is compared as 00:00:00, and
--                        the dates 1..5 days ahead at 00:00 are after current_timestamp().
--   min_value       now - 400 days   (event_date: that date)
--   max_value       now + 5 days     (event_date: that date)
--   distinct_dates   26   (20 days in the past + 5 days ahead + the day 400 days ago)
--
-- Gaps between distinct dates (d = the day this script ran), 25 gaps:
--   d-400 -> d-20                  380
--   d-20 -> d-19 ... d-2 -> d-1     19 gaps of 1
--   d-1 -> d+1                       2   (today has no rows)
--   d+1 -> d+2 ... d+4 -> d+5        4 gaps of 1
--   min_gap_days      1
--   median_gap_days   1.0   (sorted: 23 ones, 2, 380; the 13th value is 1)
--   mean_gap_days    16.2   ((23 * 1 + 2 + 380) / 25 = 405 / 25; 405 is the span d-400..d+5)
--   max_gap_days    380
--
-- Rows per day, analyze(timestamp=[...], days=30), when the tests run on the same day as
-- this script. The window is the last 30 whole days, without today: d-30 .. d-1.
--   d-20 .. d-1    rows on every day: 19 days with 30 rows, and d-2 with 130 rows (30 + 100)
--   d-30 .. d-21   0 rows (10 days)
--   today, the 30 rows from d-400 and the 20 future rows are outside the window
--   rows_per_day         30 days, sum 700 (19 * 30 + 130)
--   rows_per_day_min      0
--   rows_per_day_median  30.0   (sorted: 10 zeros, 19 times 30, 130; the 15th and 16th are 30)
--   rows_per_day_mean    23.33  (700 / 30)
--   rows_per_day_max    130

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
-- orders_composite
-- ---------------------------------------------------------------------------------------
-- 600 rows from r = 0..599:
--   customer_id = r % 50     -> 50 customers with 12 rows each
--   order_id    = r DIV 50   -> 12 order ids (0..11) with 50 rows each
-- (customer_id, order_id) is unique: r = order_id * 50 + customer_id.
--
-- Declared primary key: PRIMARY KEY (order_id, customer_id). The order is deliberately not
-- alphabetical, so the test can check that the declared order is kept.
-- Expected analyze(key=...).primary_key: Fact(("order_id", "customer_id")).
--
-- Expected, key = customer_id:
--   total_rows 600, rows_with_null_key 0, distinct_keys 50, duplicate_keys 50,
--   rows_in_duplicate_keys 600, max_rows_per_key 12, median_rows_per_key 12.0,
--   keys_with_1_row 0, keys_with_2_10_rows 0, keys_with_11_100_rows 50,
--   keys_with_over_100_rows 0
--
-- Expected, key = order_id:
--   total_rows 600, rows_with_null_key 0, distinct_keys 12, duplicate_keys 12,
--   rows_in_duplicate_keys 600, max_rows_per_key 50, median_rows_per_key 50.0,
--   keys_with_1_row 0, keys_with_2_10_rows 0, keys_with_11_100_rows 12,
--   keys_with_over_100_rows 0
--
-- Expected, key = (customer_id, order_id):
--   total_rows 600, rows_with_null_key 0, distinct_keys 600, duplicate_keys 0,
--   rows_in_duplicate_keys 0, max_rows_per_key 1, median_rows_per_key 1.0,
--   keys_with_1_row 600, keys_with_2_10_rows 0, keys_with_11_100_rows 0,
--   keys_with_over_100_rows 0

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.orders_composite');

CREATE TABLE IDENTIFIER(it_prefix || '.orders_composite') (
  customer_id BIGINT NOT NULL,
  order_id BIGINT NOT NULL,
  amount INT,
  CONSTRAINT orders_composite_pk PRIMARY KEY (order_id, customer_id)
)
USING DELTA;

INSERT INTO IDENTIFIER(it_prefix || '.orders_composite')
SELECT id % 50, id DIV 50, CAST(id % 7 AS INT)
FROM range(600);


-- ---------------------------------------------------------------------------------------
-- cadence_weekly
-- ---------------------------------------------------------------------------------------
-- event_date (DATE) on fixed dates in the past: Mondays from 2026-01-05, 3 rows per week,
-- for weeks w = 0..19, except w = 10 (2026-03-16), which is left out as a known gap.
-- Rows come from r = 0..59 with w = r DIV 3, filtered on w <> 10.
--
-- Expected (analyze(timestamp="event_date")):
--   total_rows       57   (60 - 3)
--   null_rows         0
--   min_value        2026-01-05
--   max_value        2026-05-18   (2026-01-05 + 19 * 7 days)
--   future_values     0
--   distinct_dates   19
--   Gaps between distinct dates, 18 gaps: 17 of 7 days, and 1 of 14 days (2026-03-09 ->
--   2026-03-23, across the missing week):
--     min_gap_days      7
--     median_gap_days   7.0
--     mean_gap_days     7.39   ((17 * 7 + 14) / 18 = 133 / 18 = 7.388...)
--     max_gap_days     14

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.cadence_weekly');

CREATE TABLE IDENTIFIER(it_prefix || '.cadence_weekly') (
  id BIGINT,
  event_date DATE
)
USING DELTA;

INSERT INTO IDENTIFIER(it_prefix || '.cadence_weekly')
SELECT id, date_add(DATE '2026-01-05', CAST(7 * (id DIV 3) AS INT))
FROM range(60)
WHERE id DIV 3 <> 10;


-- ---------------------------------------------------------------------------------------
-- created_updated
-- ---------------------------------------------------------------------------------------
-- 100 rows from r = 0..99. created_at is r days after 2026-03-01 10:30:00, so every
-- non-null created_at has the time 10:30. The values are written and read in the session
-- time zone; run the tests in the same session time zone as this script.
--   r  0..39   updated_at = created_at + 1 hour       updated_at > created_at
--   r 40..69   updated_at = created_at                updated_at = created_at
--   r 70..79   updated_at = created_at - 1 hour       updated_at < created_at
--   r 80..89   updated_at = NULL
--   r 90..99   created_at = NULL, updated_at = 2026-03-01 12:00:00
--
--   updated_date (DATE):
--   r  0..19   the day after created_at
--   r 20..79   the same day as created_at
--   r 80..89   NULL
--   r 90..99   2026-03-01 (created_at is NULL)
--
-- Expected, compare=("created_at", "updated_at"):
--   second_after_first   40
--   second_equal_first   30
--   second_before_first  10
--   either_null          20   (10 with NULL updated_at, 10 with NULL created_at)
--
-- Expected, compare=("created_at", "updated_date"). Spark compares DATE with TIMESTAMP by
-- implicit type coercion; the DATE is compared as that date at 00:00:00:
--   second_after_first   20   (the next day at 00:00 is after 10:30 the day before)
--   second_equal_first    0   (no created_at is at 00:00)
--   second_before_first  60   (r 20..79: the same day at 00:00 is before 10:30 that day)
--   either_null          20

DROP TABLE IF EXISTS IDENTIFIER(it_prefix || '.created_updated');

CREATE TABLE IDENTIFIER(it_prefix || '.created_updated') (
  id BIGINT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  updated_date DATE
)
USING DELTA;

INSERT INTO IDENTIFIER(it_prefix || '.created_updated')
SELECT
  id,
  created_at,
  CASE
    WHEN id < 40 THEN timestampadd(HOUR, 1, created_at)
    WHEN id < 70 THEN created_at
    WHEN id < 80 THEN timestampadd(HOUR, -1, created_at)
    WHEN id < 90 THEN NULL
    ELSE TIMESTAMP '2026-03-01 12:00:00'
  END AS updated_at,
  CASE
    WHEN id < 20 THEN date_add(CAST(created_at AS DATE), 1)
    WHEN id < 80 THEN CAST(created_at AS DATE)
    WHEN id < 90 THEN NULL
    ELSE DATE '2026-03-01'
  END AS updated_date
FROM (
  SELECT
    id,
    CASE
      WHEN id < 90 THEN timestampadd(DAY, CAST(id AS INT), TIMESTAMP '2026-03-01 10:30:00')
    END AS created_at
  FROM range(100)
);


-- ---------------------------------------------------------------------------------------
-- merge_history
-- ---------------------------------------------------------------------------------------
-- Change Data Feed is enabled. Expected inspect: change_data_feed = "true", num_rows 240.
--
-- Expected Delta history, one commit per statement below:
--   version 0  CREATE TABLE   (operation name assumed; verified by test_merge_history_excerpt)
--   version 1  WRITE  (append)     100 rows inserted   ids 0..99         table: 100 rows
--   version 2  WRITE  (append)      50 rows inserted   ids 100..149      table: 150 rows
--   version 3  WRITE  (overwrite)  200 rows written    ids 0..199        table: 200 rows
--   version 4  MERGE                50 updated (150..199), 50 inserted (200..249), 0 deleted
--                                                                         table: 250 rows
--   version 5  UPDATE               30 rows updated    ids 0..29         table: 250 rows
--   version 6  DELETE               10 rows deleted    ids 240..249      table: 240 rows
--
-- Expected inspect history excerpt (operation categories as dataowl counts them):
--   CREATE TABLE 1, WRITE 2, WRITE (overwrite) 1, MERGE 1, UPDATE 1, DELETE 1
--   history_num_commits >= 7 (OPTIMIZE commits from predictive optimization may be added)

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
