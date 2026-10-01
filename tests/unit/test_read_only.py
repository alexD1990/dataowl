from __future__ import annotations

import pytest
from conftest import FakeRunner, assert_read_only


def _runner_with(*queries: str) -> FakeRunner:
    fake = FakeRunner()
    fake.on(r".", [])
    for sql in queries:
        fake.query(sql)
    return fake


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM `c`.`s`.`t`",
        "  \n select created, last_altered, createdAt FROM t",
        "WITH base AS (SELECT 1) SELECT * FROM base",
        "DESCRIBE DETAIL `c`.`s`.`t`",
        "describe history t",
        "SHOW TBLPROPERTIES `c`.`s`.`t`",
        "-- leading comment\nSELECT 1",
        "/* block\ncomment */ SELECT deleted_file_retention, updated_at FROM t",
        "SELECT * FROM t WHERE x = 'analyze'",
        "SELECT COUNT(*) AS n FROM `main`.`update`.`drop`",
        "DESCRIBE DETAIL `c`.`s`.`we``delete``ird`",
        "SHOW TBLPROPERTIES `analyze table`.s.t",
    ],
)
def test_accepts_read_only_sql(sql: str) -> None:
    assert_read_only(_runner_with(sql))


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
        "CREATE TABLE t (a INT)",
        "ALTER TABLE t ADD COLUMN b INT",
        "DROP TABLE t",
        "OPTIMIZE t",
        "VACUUM t",
        "ANALYZE TABLE t COMPUTE STATISTICS",
        "SELECT 1; DROP TABLE t",
        "select 1; analyze  table t compute statistics",
        "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
        "-- SELECT\nDELETE FROM t",
        "/* SELECT */ VACUUM t",
        "EXPLAIN SELECT 1",
        "SELECT 1 FROM `c`.`s`.`t`; DROP TABLE x",
        "SELECT 1 FROM `a``b`DELETE",
    ],
)
def test_rejects_writing_sql(sql: str) -> None:
    with pytest.raises(AssertionError):
        assert_read_only(_runner_with("SELECT 1", sql))


def test_empty_runner_passes() -> None:
    assert_read_only(FakeRunner())
