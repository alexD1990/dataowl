from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeRunner
from pyspark.sql.types import (
    ArrayType,
    DecimalType,
    IntegerType,
    MapType,
    StringType,
    StructField,
    StructType,
)

from dataowl.collect.columns import collect_columns, count_fields
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo

REF = TableRef("Main", "Sales", "Orders")
COLUMNS_SQL = r"information_schema\.columns"

FLAT = StructType(
    [
        StructField("skattyter_id", StringType(), False),
        StructField("inntektsaar", IntegerType(), False),
        StructField("belop", DecimalType(18, 2), True),
    ]
)


def _row(
    name: str,
    position: int,
    full_data_type: str | None,
    data_type: str = "STRING",
    is_nullable: str = "YES",
    comment: str | None = None,
) -> dict[str, Any]:
    return {
        "column_name": name,
        "ordinal_position": position,
        "full_data_type": full_data_type,
        "data_type": data_type,
        "is_nullable": is_nullable,
        "comment": comment,
    }


FLAT_ROWS = [
    _row("skattyter_id", 1, "string", "STRING", "NO"),
    _row("inntektsaar", 2, "int", "INT", "NO"),
    _row("belop", 3, "decimal(18,2)", "DECIMAL", "YES", "Beløp i NOK"),
]


# count_fields


def test_count_flat_schema() -> None:
    assert count_fields(FLAT) == 3


def test_count_nested_struct() -> None:
    schema = StructType(
        [
            StructField("id", StringType()),
            StructField(
                "address",
                StructType(
                    [
                        StructField("street", StringType()),
                        StructField("geo", StructType([StructField("lat", IntegerType())])),
                    ]
                ),
            ),
        ]
    )

    # id, address, street, geo, lat
    assert count_fields(schema) == 5


def test_count_array_of_struct() -> None:
    item = StructType([StructField("sku", StringType()), StructField("qty", IntegerType())])
    schema = StructType([StructField("lines", ArrayType(item))])

    # lines, sku, qty
    assert count_fields(schema) == 3


def test_count_array_of_primitive() -> None:
    schema = StructType([StructField("tags", ArrayType(StringType()))])

    assert count_fields(schema) == 1


def test_count_map() -> None:
    value = StructType([StructField("v", IntegerType())])
    key = StructType([StructField("k1", StringType()), StructField("k2", StringType())])
    schema = StructType(
        [
            StructField("plain", MapType(StringType(), IntegerType())),
            StructField("complex", MapType(key, value)),
        ]
    )

    # plain, complex, k1, k2, v
    assert count_fields(schema) == 5


def test_count_docstring_example() -> None:
    schema = StructType(
        [
            StructField("a", IntegerType()),
            StructField(
                "b",
                StructType(
                    [
                        StructField("x", IntegerType()),
                        StructField("y", ArrayType(StructType([StructField("z", IntegerType())]))),
                    ]
                ),
            ),
            StructField("m", MapType(StringType(), StructType([StructField("v", IntegerType())]))),
        ]
    )

    assert count_fields(schema) == 7


def test_count_empty_schema() -> None:
    assert count_fields(StructType([])) == 0


# collect_columns


def test_flat_schema(fake_runner: FakeRunner) -> None:
    fake_runner.on(COLUMNS_SQL, FLAT_ROWS)
    fake_runner.on_schema(FLAT)

    info = collect_columns(fake_runner, REF)

    assert info.columns == Fact(
        (
            ColumnInfo("skattyter_id", 1, "string", False, None),
            ColumnInfo("inntektsaar", 2, "int", False, None),
            ColumnInfo("belop", 3, "decimal(18,2)", True, "Beløp i NOK"),
        ),
        source="metadata",
    )
    assert info.num_columns == Fact(3, source="metadata")
    assert info.num_fields_nested == Fact(3, source="metadata")
    assert fake_runner.schema_calls == [REF]


def test_nested_schema(fake_runner: FakeRunner) -> None:
    item = StructType([StructField("sku", StringType()), StructField("qty", IntegerType())])
    schema = StructType(
        [
            StructField("id", StringType()),
            StructField("lines", ArrayType(item)),
            StructField("attrs", MapType(StringType(), StringType())),
        ]
    )
    fake_runner.on(
        COLUMNS_SQL,
        [
            _row("id", 1, "string"),
            _row("lines", 2, "array<struct<sku:string,qty:int>>", "ARRAY"),
            _row("attrs", 3, "map<string,string>", "MAP"),
        ],
    )
    fake_runner.on_schema(schema)

    info = collect_columns(fake_runner, REF)

    assert info.num_columns == Fact(3, source="metadata")
    assert info.num_fields_nested == Fact(5, source="metadata")
    assert info.columns.value is not None
    assert info.columns.value[1].data_type == "array<struct<sku:string,qty:int>>"


def test_falls_back_to_data_type(fake_runner: FakeRunner) -> None:
    fake_runner.on(COLUMNS_SQL, [_row("id", 1, None, "STRING")])
    fake_runner.on_schema(StructType([StructField("id", StringType())]))

    info = collect_columns(fake_runner, REF)

    assert info.columns.value is not None
    assert info.columns.value[0].data_type == "STRING"


def test_empty_comment(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        COLUMNS_SQL,
        [_row("a", 1, "string", comment=None), _row("b", 2, "string", comment="")],
    )
    fake_runner.on_schema(
        StructType([StructField("a", StringType()), StructField("b", StringType())])
    )

    info = collect_columns(fake_runner, REF)

    assert info.columns.value is not None
    assert info.columns.value[0].comment is None
    assert info.columns.value[1].comment == ""


def test_nullable_is_bool(fake_runner: FakeRunner) -> None:
    fake_runner.on(COLUMNS_SQL, FLAT_ROWS)
    fake_runner.on_schema(FLAT)

    info = collect_columns(fake_runner, REF)

    assert info.columns.value is not None
    assert [c.nullable for c in info.columns.value] == [False, False, True]


def test_schema_error_keeps_column_facts(fake_runner: FakeRunner) -> None:
    fake_runner.on(COLUMNS_SQL, FLAT_ROWS)
    fake_runner.on_schema_error(RuntimeError("[PERMISSION_DENIED] no SELECT on table\nmore"))

    info = collect_columns(fake_runner, REF)

    assert info.num_fields_nested == Fact.unavailable(
        "metadata", "[PERMISSION_DENIED] no SELECT on table"
    )
    assert info.num_columns == Fact(3, source="metadata")
    assert info.columns.available


def test_information_schema_error_keeps_nested_count(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(COLUMNS_SQL, PermissionError("[INSUFFICIENT_PERMISSIONS] denied"))
    fake_runner.on_schema(FLAT)

    info = collect_columns(fake_runner, REF)

    reason = "[INSUFFICIENT_PERMISSIONS] denied"
    assert info.columns == Fact.unavailable("metadata", reason)
    assert info.num_columns == Fact.unavailable("metadata", reason)
    assert info.num_fields_nested == Fact(3, source="metadata")


def test_no_rows_is_unavailable(fake_runner: FakeRunner) -> None:
    fake_runner.on(COLUMNS_SQL, [])
    fake_runner.on_schema(FLAT)

    info = collect_columns(fake_runner, REF)

    reason = "information_schema.columns returned no rows"
    assert info.columns == Fact.unavailable("metadata", reason)
    assert info.num_columns == Fact.unavailable("metadata", reason)
    assert info.num_fields_nested == Fact(3, source="metadata")


def test_query_uses_lowercase_params_and_order(fake_runner: FakeRunner) -> None:
    fake_runner.on(COLUMNS_SQL, FLAT_ROWS)
    fake_runner.on_schema(FLAT)

    collect_columns(fake_runner, TableRef("My`Cat", "Sales", "Orders"))

    [(sql, params)] = fake_runner.queries
    assert params == {"schema": "sales", "table": "orders"}
    assert "FROM `My``Cat`.information_schema.columns" in sql
    assert "table_schema = :schema" in sql
    assert "table_name = :table" in sql
    assert "ORDER BY ordinal_position" in sql
    assert "lower(" not in sql.lower()
    assert "*" not in sql
    assert "sales" not in sql.lower()
    assert "orders" not in sql.lower()


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({k: v for k, v in _row("a", 1, "string").items() if k != "column_name"}, "missing"),
        (_row("a", "1", "string"), "ordinal_position has type str"),  # type: ignore[arg-type]
        (_row("a", 1, "string", is_nullable="MAYBE"), "is_nullable has value"),
    ],
)
def test_unexpected_row_format_is_unavailable(
    fake_runner: FakeRunner, row: dict[str, Any], detail: str
) -> None:
    fake_runner.on(COLUMNS_SQL, [row])
    fake_runner.on_schema(FLAT)

    info = collect_columns(fake_runner, REF)

    assert not info.columns.available
    assert not info.num_columns.available
    assert info.columns.reason is not None
    assert info.columns.reason.startswith("Unexpected row format in information_schema.columns")
    assert detail in info.columns.reason
    assert info.num_fields_nested == Fact(3, source="metadata")
