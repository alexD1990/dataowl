from __future__ import annotations

import dataclasses

import pytest

from dataowl.identifiers import TableRef, parse_table, quote_column


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("main.sales.orders", TableRef("main", "sales", "orders")),
        ("Main.Sales.Orders_2024", TableRef("Main", "Sales", "Orders_2024")),
        ("`my-catalog`.schema.table", TableRef("my-catalog", "schema", "table")),
        ("`a`.`b`.`c`", TableRef("a", "b", "c")),
        ("c.s.`with space`", TableRef("c", "s", "with space")),
    ],
)
def test_parse_valid_names(name: str, expected: TableRef) -> None:
    assert parse_table(name) == expected


def test_parse_dot_inside_backticks() -> None:
    assert parse_table("`a.b`.schema.`t.x`") == TableRef("a.b", "schema", "t.x")


def test_parse_escaped_backtick() -> None:
    assert parse_table("c.s.`we``ird`") == TableRef("c", "s", "we`ird")


def test_parse_only_escaped_backtick() -> None:
    assert parse_table("c.s.````") == TableRef("c", "s", "`")


@pytest.mark.parametrize("name", ["table", "schema.table"])
def test_parse_too_few_parts(name: str) -> None:
    with pytest.raises(ValueError, match="exactly three parts"):
        parse_table(name)


def test_parse_too_many_parts() -> None:
    with pytest.raises(ValueError, match="exactly three parts"):
        parse_table("a.b.c.d")


def test_parse_empty_string() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_table("")


@pytest.mark.parametrize("name", ["a..c", ".b.c", "a.b.", "a.``.c"])
def test_parse_empty_part(name: str) -> None:
    with pytest.raises(ValueError, match="empty part"):
        parse_table(name)


@pytest.mark.parametrize("name", [" a.b.c", "a.b.c ", "a.b.c\n"])
def test_parse_leading_or_trailing_whitespace(name: str) -> None:
    with pytest.raises(ValueError, match="whitespace"):
        parse_table(name)


@pytest.mark.parametrize("name", ["a.b.`c", "a.b.`c``", "`a.b.c"])
def test_parse_unterminated_backtick(name: str) -> None:
    with pytest.raises(ValueError, match="Unterminated backtick"):
        parse_table(name)


def test_parse_character_after_closing_backtick() -> None:
    with pytest.raises(ValueError, match="after closing backtick"):
        parse_table("a.b.`c`d")


@pytest.mark.parametrize("name", ["my-catalog.s.t", "a.b c.d", "a.b.c`", "a.b.tø"])
def test_parse_invalid_unquoted_character(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid character"):
        parse_table(name)


@pytest.mark.parametrize(
    "name",
    [
        "a.b.c; DROP TABLE x",
        "a.b.c;DROP TABLE x",
        "a.b.c -- comment",
        "a.b.`c`; DROP TABLE x",
        "a.b.c/* x */",
    ],
)
def test_parse_sql_injection_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        parse_table(name)


def test_quoted() -> None:
    assert TableRef("main", "sales", "orders").quoted() == "`main`.`sales`.`orders`"


def test_quoted_escapes_backticks() -> None:
    assert TableRef("c", "s", "we`ird").quoted() == "`c`.`s`.`we``ird`"


def test_quoted_catalog() -> None:
    assert TableRef("my`cat", "s", "t").quoted_catalog() == "`my``cat`"


def test_quoted_keeps_injection_inside_identifier() -> None:
    ref = TableRef("c", "s", "x`; DROP TABLE y; --")
    assert ref.quoted() == "`c`.`s`.`x``; DROP TABLE y; --`"


@pytest.mark.parametrize(
    "ref",
    [
        TableRef("main", "sales", "orders"),
        TableRef("my-catalog", "a.b", "we`ird"),
        TableRef("`", "``", "with space"),
        TableRef("x`; DROP TABLE y; --", "S", "T"),
    ],
)
def test_roundtrip(ref: TableRef) -> None:
    assert parse_table(ref.quoted()) == ref


def test_table_ref_is_frozen() -> None:
    ref = TableRef("a", "b", "c")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.table = "d"  # type: ignore[misc]


@pytest.mark.parametrize("parts", [("", "s", "t"), ("c", "", "t"), ("c", "s", "")])
def test_table_ref_rejects_empty_part(parts: tuple[str, str, str]) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        TableRef(*parts)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("id", "`id`"),
        ("Belop", "`Belop`"),
        ("a.b", "`a.b`"),
        ("we`ird", "`we``ird`"),
        ("x`; DROP TABLE y", "`x``; DROP TABLE y`"),
    ],
)
def test_quote_column(name: str, expected: str) -> None:
    assert quote_column(name) == expected


def test_quote_column_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        quote_column("")
