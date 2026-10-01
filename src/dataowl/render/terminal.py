"""Terminal rendering. Formatting only; no Spark import."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from dataowl.render.format import format_bytes, format_datetime, format_fact, format_int

if TYPE_CHECKING:
    from dataowl.model.facts import Fact
    from dataowl.model.overview import ColumnInfo, Overview

T = TypeVar("T")

NOT_SET = "not set (default)"
_COLUMN_COMMENT_WIDTH = 40
_TABLE_COMMENT_WIDTH = 80


def render_overview(overview: Overview) -> str:
    header = (
        f"{overview.table.display_name()}   "
        f"({_header_value(overview.object_type_raw)}, {_header_value(overview.format)})"
    )
    rows = [
        ("Size:", format_fact(overview.size_bytes, format_bytes)),
        ("Files:", _files(overview)),
        ("Rows:", format_fact(overview.num_rows, format_int)),
        ("Columns:", _columns_count(overview)),
        ("Partitioned by:", format_fact(overview.partition_columns, _names)),
        ("Clustered by:", format_fact(overview.clustering_columns, _names)),
        ("Change Data Feed:", format_fact(overview.change_data_feed, none_text=NOT_SET)),
        ("Log retention:", format_fact(overview.log_retention, none_text=NOT_SET)),
        (
            "Deleted file retention:",
            format_fact(overview.deleted_file_retention, none_text=NOT_SET),
        ),
        ("Created:", format_fact(overview.created, format_datetime)),
        ("Last modified:", format_fact(overview.last_modified, format_datetime)),
        ("Owner:", format_fact(overview.owner)),
        ("Comment:", format_fact(overview.comment, _table_comment)),
    ]
    width = max(len(label) for label, _ in rows) + 2
    lines = [header, ""]
    lines += [f"{label:<{width}}{value}" for label, value in rows]
    lines += ["", "SCHEMA"]
    lines += _schema(overview.columns)
    return "\n".join(lines)


def _files(overview: Overview) -> str:
    return _with_secondary(
        format_fact(overview.num_files, format_int),
        overview.num_files,
        overview.avg_file_size_bytes,
        format_bytes,
        "avg {}",
        "avg",
    )


def _columns_count(overview: Overview) -> str:
    return _with_secondary(
        format_fact(overview.num_columns, format_int),
        overview.num_columns,
        overview.num_fields_nested,
        format_int,
        "{} incl. nested",
        "incl. nested",
    )


def _with_secondary(
    text: str,
    primary: Fact[Any],
    secondary: Fact[T],
    fmt: Callable[[T], str],
    template: str,
    label: str,
) -> str:
    """Append '  (<secondary>)' to text.

    The secondary part is left out when both facts are unavailable for the same reason.
    """
    if not secondary.available and secondary.reason == primary.reason:
        return text
    if not secondary.available or secondary.value is None:
        return f"{text}  ({label}: {format_fact(secondary)})"
    return f"{text}  ({template.format(fmt(secondary.value))})"


def _names(names: tuple[str, ...]) -> str:
    return ", ".join(names) if names else "–"


def _schema(columns: Fact[tuple[ColumnInfo, ...]]) -> list[str]:
    if not columns.available or columns.value is None:
        return [f"  {format_fact(columns)}"]

    table = [("#", "name", "type", "nullable", "comment")]
    table += [
        (
            str(number),
            column.name,
            column.data_type,
            "yes" if column.nullable else "no",
            _shorten(column.comment or "", _COLUMN_COMMENT_WIDTH),
        )
        for number, column in enumerate(columns.value, start=1)
    ]
    widths = [max(len(row[i]) for row in table) for i in range(4)]
    return [
        (
            "  "
            + "  ".join(cell.ljust(width) for cell, width in zip(row[:4], widths, strict=True))
            + "  "
            + row[4]
        ).rstrip()
        for row in table
    ]


def _header_value(fact: Fact[str]) -> str:
    """Header value; the reason for an unavailable fact is shown on the lines below."""
    return format_fact(fact) if fact.available else "n/a"


def _table_comment(comment: str) -> str:
    return _shorten(comment, _TABLE_COMMENT_WIDTH) or "–"


def _shorten(text: str, width: int) -> str:
    """Collapse line breaks and whitespace, and cut to width characters including '…'."""
    text = " ".join(text.split())
    if len(text) > width:
        return text[: width - 1] + "…"
    return text
