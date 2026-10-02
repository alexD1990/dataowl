from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

import pytest
from conftest import FakeRunner, assert_read_only

from dataowl.collect.keys import collect_key_analyses, collect_key_analysis, normalize_keys
from dataowl.identifiers import TableRef
from dataowl.model.analysis import KeyAnalysis
from dataowl.model.facts import Fact, to_jsonable
from dataowl.model.overview import ColumnInfo

REF = TableRef("main", "sales", "orders")

COLUMNS = (
    ColumnInfo("customer_id", 0, "string", False, None),
    ColumnInfo("order_id", 1, "bigint", False, None),
    ColumnInfo("Region", 2, "string", True, None),
    ColumnInfo("attributes", 3, "map<string,string>", True, None),
    ColumnInfo("tags", 4, "array<string>", True, None),
    ColumnInfo("address", 5, "struct<street:string,zip:string>", True, None),
    ColumnInfo("legacy_map", 6, "MAP", True, None),
)

KEY_ROW: dict[str, Any] = {
    "total_rows": 1284991,
    "rows_with_null_key": 0,
    "distinct_keys": 52004,
    "duplicate_keys": 51870,
    "rows_in_duplicate_keys": 1284857,
    "max_rows_per_key": 4210,
    "median_rows_per_key": 18.0,
    "keys_with_1_row": 134,
    "keys_with_2_10_rows": 9902,
    "keys_with_11_100_rows": 41731,
    "keys_with_over_100_rows": 237,
}

UNIQUE_ROW: dict[str, Any] = {
    "total_rows": 1284991,
    "rows_with_null_key": 0,
    "distinct_keys": 1284991,
    "duplicate_keys": 0,
    "rows_in_duplicate_keys": 0,
    "max_rows_per_key": 1,
    "median_rows_per_key": 1.0,
    "keys_with_1_row": 1284991,
    "keys_with_2_10_rows": 0,
    "keys_with_11_100_rows": 0,
    "keys_with_over_100_rows": 0,
}

EMPTY_ROW: dict[str, Any] = {
    "total_rows": 3,
    "rows_with_null_key": 3,
    "distinct_keys": 0,
    "duplicate_keys": 0,
    "rows_in_duplicate_keys": 0,
    "max_rows_per_key": 0,
    "median_rows_per_key": None,
    "keys_with_1_row": 0,
    "keys_with_2_10_rows": 0,
    "keys_with_11_100_rows": 0,
    "keys_with_over_100_rows": 0,
}


def _only(*names: str) -> str:
    """Regex for the key query on exactly these columns, in order."""
    aliased = r",\s*".join(rf"{re.escape(name)} AS __dataowl_k{i}" for i, name in enumerate(names))
    return rf"SELECT\s+{aliased},\s*CASE"


def _facts(analysis: KeyAnalysis) -> list[Fact[Any]]:
    return [
        getattr(analysis, field.name)
        for field in dataclasses.fields(analysis)
        if field.name != "columns"
    ]


def _expected(columns: tuple[str, ...], row: dict[str, Any]) -> KeyAnalysis:
    values = {name: Fact(value, source="exact") for name, value in row.items()}
    return KeyAnalysis(columns=columns, **values)


# normalize_keys: input forms


def test_single_column() -> None:
    assert normalize_keys("customer_id", COLUMNS) == (("customer_id",),)


def test_several_columns_separately() -> None:
    assert normalize_keys(["customer_id", "order_id"], COLUMNS) == (
        ("customer_id",),
        ("order_id",),
    )


def test_composite_key() -> None:
    assert normalize_keys(("customer_id", "order_id"), COLUMNS) == (("customer_id", "order_id"),)


def test_mixed_input() -> None:
    assert normalize_keys(["customer_id", ("customer_id", "order_id")], COLUMNS) == (
        ("customer_id",),
        ("customer_id", "order_id"),
    )


def test_case_insensitive_match_gives_schema_names() -> None:
    assert normalize_keys(["CUSTOMER_ID", ("region", "Order_Id")], COLUMNS) == (
        ("customer_id",),
        ("Region", "order_id"),
    )


def test_composite_key_order_is_kept() -> None:
    assert normalize_keys([("order_id", "customer_id"), ("customer_id", "order_id")], COLUMNS) == (
        ("order_id", "customer_id"),
        ("customer_id", "order_id"),
    )


@pytest.mark.parametrize("column", ["tags", "address"])
def test_array_and_struct_are_accepted(column: str) -> None:
    assert normalize_keys(column, COLUMNS) == ((column,),)


# normalize_keys: validation


@pytest.mark.parametrize(
    ("key", "match"),
    [
        ("missing", "'missing' does not exist"),
        (["customer_id", "missing"], "'missing' does not exist"),
        (("customer_id", "missing"), "'missing' does not exist"),
        ("", "'' does not exist"),
    ],
)
def test_unknown_column(key: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        normalize_keys(key, COLUMNS)


@pytest.mark.parametrize(
    ("key", "column"),
    [
        ("attributes", "attributes"),
        ("ATTRIBUTES", "attributes"),
        (("customer_id", "attributes"), "attributes"),
        ("legacy_map", "legacy_map"),
    ],
)
def test_map_column(key: Any, column: str) -> None:
    with pytest.raises(ValueError, match=rf"'{column}' has type MAP"):
        normalize_keys(key, COLUMNS)


@pytest.mark.parametrize("key", [(), ("customer_id",), [("customer_id",)], ["order_id", ()]])
def test_tuple_with_fewer_than_two_columns(key: Any) -> None:
    with pytest.raises(ValueError, match="at least two columns"):
        normalize_keys(key, COLUMNS)


@pytest.mark.parametrize(
    "key", [("customer_id", "customer_id"), ("customer_id", "order_id", "CUSTOMER_ID")]
)
def test_duplicate_column_in_tuple(key: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="'customer_id' more than once"):
        normalize_keys(key, COLUMNS)


def test_empty_list() -> None:
    with pytest.raises(ValueError, match="empty list"):
        normalize_keys([], COLUMNS)


@pytest.mark.parametrize(
    "key",
    [
        ["customer_id", "customer_id"],
        ["customer_id", "Customer_ID"],
        [("customer_id", "order_id"), ("CUSTOMER_ID", "order_id")],
    ],
)
def test_duplicate_element_in_list(key: Any) -> None:
    with pytest.raises(ValueError, match="more than once"):
        normalize_keys(key, COLUMNS)


@pytest.mark.parametrize(
    "key", [None, 1, ["customer_id", 1], [["customer_id", "order_id"]], ("customer_id", 1)]
)
def test_invalid_element_type(key: Any) -> None:
    with pytest.raises(ValueError):
        normalize_keys(key, COLUMNS)


# collect_key_analysis


def test_single_column_analysis(fake_runner: FakeRunner) -> None:
    fake_runner.on(_only("`customer_id`"), [KEY_ROW])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id",))

    assert analysis == _expected(("customer_id",), KEY_ROW)
    assert all(fact.available and fact.source == "exact" for fact in _facts(analysis))
    assert len(fake_runner.queries) == 1
    assert_read_only(fake_runner)


def test_composite_key_analysis(fake_runner: FakeRunner) -> None:
    fake_runner.on(_only("`customer_id`", "`order_id`"), [UNIQUE_ROW])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id", "order_id"))

    assert analysis == _expected(("customer_id", "order_id"), UNIQUE_ROW)
    [(sql, params)] = fake_runner.queries
    assert params is None
    assert "CASE WHEN `customer_id` IS NULL OR `order_id` IS NULL THEN 1 ELSE 0 END" in sql
    assert "GROUP BY __dataowl_k0, __dataowl_k1" in sql
    assert_read_only(fake_runner)


def test_one_query_per_key_in_order(fake_runner: FakeRunner) -> None:
    fake_runner.on(_only("`customer_id`", "`order_id`"), [UNIQUE_ROW])
    fake_runner.on(_only("`customer_id`"), [KEY_ROW])
    fake_runner.on(_only("`order_id`"), [UNIQUE_ROW])
    keys = normalize_keys(["customer_id", ("customer_id", "order_id"), "Order_ID"], COLUMNS)

    analyses = collect_key_analyses(fake_runner, REF, keys)

    assert analyses == (
        _expected(("customer_id",), KEY_ROW),
        _expected(("customer_id", "order_id"), UNIQUE_ROW),
        _expected(("order_id",), UNIQUE_ROW),
    )
    assert len(fake_runner.queries) == 3
    assert all(
        not re.search(r"\bpercentile_approx\b|approx_percentile", sql)
        for sql, _ in fake_runner.queries
    )
    assert_read_only(fake_runner)


def test_sql_quotes_table_and_columns(fake_runner: FakeRunner) -> None:
    ref = TableRef("my-catalog", "raw", "order events")
    fake_runner.on(r".", [KEY_ROW])

    collect_key_analysis(fake_runner, ref, ("we`ird", "Region"))

    [(sql, _)] = fake_runner.queries
    assert "FROM `my-catalog`.`raw`.`order events`" in sql
    assert "`we``ird` AS __dataowl_k0" in sql
    assert "`Region` AS __dataowl_k1" in sql
    assert "`we``ird` IS NULL OR `Region` IS NULL" in sql
    unquoted = re.sub(r"`(?:[^`]|``)*`", "", sql)
    assert "Region" not in unquoted
    assert "ird" not in unquoted
    assert_read_only(fake_runner)


def test_column_names_colliding_with_internal_names(fake_runner: FakeRunner) -> None:
    columns = (
        ColumnInfo("c", 0, "int", True, None),
        ColumnInfo("has_null", 1, "boolean", True, None),
    )
    fake_runner.on(_only("`c`", "`has_null`"), [KEY_ROW])

    keys = normalize_keys(("c", "has_null"), columns)
    analysis = collect_key_analysis(fake_runner, REF, keys[0])

    [(sql, _)] = fake_runner.queries
    unquoted = re.sub(r"`(?:[^`]|``)*`", "", sql)
    assert not re.search(r"\bc\b", unquoted)
    assert not re.search(r"\bhas_null\b", unquoted)
    assert analysis.columns == ("c", "has_null")
    assert analysis.distinct_keys == Fact(52004, source="exact")
    assert_read_only(fake_runner)


def test_case_from_input_gives_schema_names_in_sql_and_model(fake_runner: FakeRunner) -> None:
    fake_runner.on(_only("`Region`", "`customer_id`"), [KEY_ROW])

    [key] = normalize_keys([("REGION", "Customer_Id")], COLUMNS)
    analysis = collect_key_analysis(fake_runner, REF, key)

    assert analysis.columns == ("Region", "customer_id")
    assert analysis.total_rows == Fact(1284991, source="exact")
    assert_read_only(fake_runner)


def test_last_select_returns_only_aggregates(fake_runner: FakeRunner) -> None:
    fake_runner.on(r".", [KEY_ROW])

    collect_key_analysis(fake_runner, REF, ("customer_id", "order_id"))

    [(sql, _)] = fake_runner.queries
    last_select = sql[sql.rindex("\nSELECT") :]
    assert "__dataowl_k" not in last_select
    assert "`customer_id`" not in last_select
    assert last_select.rstrip().endswith("FROM grouped")


def test_no_non_null_keys(fake_runner: FakeRunner) -> None:
    fake_runner.on(r".", [{**EMPTY_ROW, "median_rows_per_key": 0.0}])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id",))

    assert analysis.median_rows_per_key == Fact.unavailable("exact", "No non-null keys")
    assert analysis.distinct_keys == Fact(0, source="exact")
    assert analysis.rows_with_null_key == Fact(3, source="exact")
    assert analysis.max_rows_per_key == Fact(0, source="exact")


def test_median_is_float(fake_runner: FakeRunner) -> None:
    fake_runner.on(r".", [{**KEY_ROW, "median_rows_per_key": 18}])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id",))

    assert analysis.median_rows_per_key == Fact(18.0, source="exact")
    assert isinstance(analysis.median_rows_per_key.value, float)


def test_query_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(
        r".",
        PermissionError("[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table.\nmore"),
    )

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id", "order_id"))

    reason = "[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table."
    assert analysis.columns == ("customer_id", "order_id")
    assert all(fact == Fact.unavailable("exact", reason) for fact in _facts(analysis))
    assert_read_only(fake_runner)


def test_error_affects_only_its_own_key(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(_only("`order_id`"), RuntimeError("[TIMEOUT] query timed out"))
    fake_runner.on(_only("`customer_id`"), [KEY_ROW])

    first, second = collect_key_analyses(fake_runner, REF, (("customer_id",), ("order_id",)))

    assert first == _expected(("customer_id",), KEY_ROW)
    assert all(fact.reason == "[TIMEOUT] query timed out" for fact in _facts(second))


def test_no_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(r".", [])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id",))

    assert all(
        fact == Fact.unavailable("exact", "Key query returned no rows") for fact in _facts(analysis)
    )


@pytest.mark.parametrize(
    ("field", "value", "type_name"),
    [
        ("total_rows", "1284991", "str"),
        ("duplicate_keys", None, "NoneType"),
        ("keys_with_1_row", True, "bool"),
        ("max_rows_per_key", 4210.0, "float"),
        ("median_rows_per_key", "18", "str"),
        ("median_rows_per_key", False, "bool"),
    ],
)
def test_unexpected_type(fake_runner: FakeRunner, field: str, value: Any, type_name: str) -> None:
    fake_runner.on(r".", [{**KEY_ROW, field: value}])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id",))

    assert getattr(analysis, field) == Fact.unavailable(
        "exact", f"Unexpected {field} type in key query result: {type_name}"
    )
    others = [fact for fact in _facts(analysis) if fact is not getattr(analysis, field)]
    assert all(fact.available for fact in others)


def test_missing_result_column(fake_runner: FakeRunner) -> None:
    row = dict(KEY_ROW)
    del row["keys_with_over_100_rows"]
    fake_runner.on(r".", [row])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id",))

    assert analysis.keys_with_over_100_rows == Fact.unavailable(
        "exact", "Unexpected keys_with_over_100_rows type in key query result: NoneType"
    )


def test_to_jsonable(fake_runner: FakeRunner) -> None:
    fake_runner.on(r".", [EMPTY_ROW])

    analysis = collect_key_analysis(fake_runner, REF, ("customer_id", "order_id"))
    data = json.loads(json.dumps(to_jsonable(analysis)))

    assert data["columns"] == ["customer_id", "order_id"]
    assert data["distinct_keys"] == {
        "value": 0,
        "source": "exact",
        "available": True,
        "reason": None,
    }
    assert data["median_rows_per_key"]["reason"] == "No non-null keys"
    assert list(data) == [field.name for field in dataclasses.fields(KeyAnalysis)]
