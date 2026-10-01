"""Parsing, validation and quoting of table and column names."""

from __future__ import annotations

import string
from dataclasses import dataclass

_UNQUOTED_CHARS = frozenset(string.ascii_letters + string.digits + "_")


@dataclass(frozen=True)
class TableRef:
    """A three-part table name. Parts are stored raw (unquoted), with case preserved."""

    catalog: str
    schema: str
    table: str

    def __post_init__(self) -> None:
        if not (self.catalog and self.schema and self.table):
            raise ValueError("TableRef parts must be non-empty")

    def quoted(self) -> str:
        return ".".join(_quote(part) for part in (self.catalog, self.schema, self.table))


def parse_table(name: str) -> TableRef:
    """Parse `catalog.schema.table`. Parts may be quoted with backticks."""
    if not name:
        raise ValueError("Table name is empty")
    if name != name.strip():
        raise ValueError(f"Table name has leading or trailing whitespace: {name!r}")

    parts: list[str] = []
    i = 0
    while True:
        if i < len(name) and name[i] == "`":
            part, i = _read_quoted(name, i)
        else:
            part, i = _read_unquoted(name, i)
        if not part:
            raise ValueError(f"Table name has an empty part: {name!r}")
        parts.append(part)

        if i == len(name):
            break
        if name[i] != ".":
            raise ValueError(
                f"Unexpected character {name[i]!r} after closing backtick in table name: {name!r}"
            )
        i += 1

    if len(parts) != 3:
        raise ValueError(
            f"Table name must have exactly three parts (catalog.schema.table), "
            f"got {len(parts)}: {name!r}"
        )
    return TableRef(*parts)


def quote_column(name: str) -> str:
    """Quote a column name as a single identifier. Dots are not treated as separators."""
    if not name:
        raise ValueError("Column name is empty")
    return _quote(name)


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _read_quoted(name: str, start: int) -> tuple[str, int]:
    """Read a backtick-quoted part starting at the opening backtick.

    Returns the raw part and the index after the closing backtick.
    """
    chars: list[str] = []
    i = start + 1
    while i < len(name):
        if name[i] == "`":
            if i + 1 < len(name) and name[i + 1] == "`":
                chars.append("`")
                i += 2
                continue
            return "".join(chars), i + 1
        chars.append(name[i])
        i += 1
    raise ValueError(f"Unterminated backtick in table name: {name!r}")


def _read_unquoted(name: str, start: int) -> tuple[str, int]:
    """Read an unquoted part. Returns the part and the index of the next '.' or the end."""
    i = start
    while i < len(name) and name[i] != ".":
        if name[i] not in _UNQUOTED_CHARS:
            raise ValueError(
                f"Invalid character {name[i]!r} in unquoted part of table name: {name!r}. "
                "Use backticks for names with characters other than letters, digits "
                "and underscore."
            )
        i += 1
    return name[start:i], i
