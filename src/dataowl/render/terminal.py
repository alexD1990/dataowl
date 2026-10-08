"""Terminal rendering. Formatting only; no Spark import."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, TypeVar

from dataowl.render.format import (
    format_bytes,
    format_date,
    format_datetime,
    format_decimal,
    format_fact,
    format_int,
    format_percent,
)

if TYPE_CHECKING:
    from dataowl.model.analysis import (
        ColumnAnalysis,
        ComparisonAnalysis,
        DayCount,
        KeyAnalysis,
        TimestampAnalysis,
    )
    from dataowl.model.facts import Fact
    from dataowl.model.overview import ColumnInfo, Overview

T = TypeVar("T")

NOT_SET = "not set (default)"
DATE_COMPARISON_NOTE = "Note: date values are compared as 00:00:00."
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
    lines += [""]
    lines += _history(overview)
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


def _history(overview: Overview) -> list[str]:
    facts: list[Fact[Any]] = [
        overview.history_first_commit,
        overview.history_last_commit,
        overview.history_num_commits,
        overview.history_operations,
    ]
    if all(not fact.available for fact in facts) and len({fact.reason for fact in facts}) == 1:
        return ["HISTORY", f"  {format_fact(overview.history_operations)}"]

    first = format_fact(overview.history_first_commit, format_datetime)
    last = format_fact(overview.history_last_commit, format_datetime)
    lines = [f"HISTORY  ({first} – {last}, {_commits(overview.history_num_commits)})"]

    operations = overview.history_operations
    if not operations.available or operations.value is None:
        return [*lines, f"  {format_fact(operations)}"]
    rows = [(f"{operation}:", format_int(n)) for operation, n in operations.value.items()]
    width = max((len(label) for label, _ in rows), default=0) + 2
    return lines + [f"  {label:<{width}}{value}" for label, value in rows]


def _commits(num_commits: Fact[int]) -> str:
    if not num_commits.available or num_commits.value is None:
        return f"commits: {format_fact(num_commits)}"
    noun = "commit" if num_commits.value == 1 else "commits"
    return f"{format_int(num_commits.value)} {noun}"


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


def render_column_analysis(analysis: ColumnAnalysis, *, show_days: bool = False) -> str:
    """Header, PRIMARY KEY, one block per key and timestamp column, and COMPARE.

    Blocks are separated by an empty line, and blocks without content are left out.
    show_days adds one line per day in the window to each TIMESTAMP block.
    """
    blocks: list[list[str]] = [[analysis.table.display_name()]]
    if analysis.primary_key is not None:
        blocks.append([_primary_key(analysis.primary_key)])
    blocks += [_key_block(key) for key in analysis.keys]
    blocks += [_timestamp_block(timestamp, show_days) for timestamp in analysis.timestamps]
    if analysis.comparison is not None:
        blocks.append(_comparison_block(analysis.comparison))
    return "\n\n".join("\n".join(block) for block in blocks)


def _primary_key(primary_key: Fact[tuple[str, ...] | None]) -> str:
    if not primary_key.available:
        return f"PRIMARY KEY  {format_fact(primary_key)}"
    if primary_key.value is None:
        return "PRIMARY KEY  none declared"
    return f"PRIMARY KEY  {', '.join(primary_key.value)}  (declared, not enforced)"


def _key_block(key: KeyAnalysis) -> list[str]:
    header = f"KEY  {' + '.join(key.columns)}"
    facts: list[Fact[Any]] = [
        key.total_rows,
        key.rows_with_null_key,
        key.distinct_keys,
        key.duplicate_keys,
        key.rows_in_duplicate_keys,
        key.max_rows_per_key,
        key.median_rows_per_key,
        key.keys_with_1_row,
        key.keys_with_2_10_rows,
        key.keys_with_11_100_rows,
        key.keys_with_over_100_rows,
    ]
    if _same_unavailable(facts):
        return [header, f"  {format_fact(facts[0])}"]

    rows_per_key = _numbers(
        [
            ("median", key.median_rows_per_key, format_decimal),
            ("max", key.max_rows_per_key, format_int),
        ]
    )
    rows = [
        ("Rows:", format_fact(key.total_rows, format_int)),
        ("Rows with null in key:", format_fact(key.rows_with_null_key, format_int)),
        ("Distinct keys:", format_fact(key.distinct_keys, format_int)),
        ("Keys occurring >1 time:", format_fact(key.duplicate_keys, format_int)),
        ("Rows in those keys:", format_fact(key.rows_in_duplicate_keys, format_int)),
        ("Rows per key:", rows_per_key),
        ("  1 row:", format_fact(key.keys_with_1_row, format_int)),
        ("  2–10 rows:", format_fact(key.keys_with_2_10_rows, format_int)),
        ("  11–100 rows:", format_fact(key.keys_with_11_100_rows, format_int)),
        ("  >100 rows:", format_fact(key.keys_with_over_100_rows, format_int)),
    ]
    return [header, *_aligned(rows)]


def _timestamp_block(timestamp: TimestampAnalysis, show_days: bool) -> list[str]:
    header = f"TIMESTAMP  {timestamp.column}"
    facts: list[Fact[Any]] = [
        timestamp.total_rows,
        timestamp.null_rows,
        timestamp.null_share,
        timestamp.min_value,
        timestamp.max_value,
        timestamp.future_values,
        timestamp.distinct_dates,
        timestamp.min_gap_days,
        timestamp.median_gap_days,
        timestamp.mean_gap_days,
        timestamp.max_gap_days,
        timestamp.window_first_day,
        timestamp.window_last_day,
        timestamp.rows_per_day,
        timestamp.rows_per_day_min,
        timestamp.rows_per_day_median,
        timestamp.rows_per_day_mean,
        timestamp.rows_per_day_max,
    ]
    if _same_unavailable(facts):
        return [header, f"  {format_fact(facts[0])}"]

    rows = [
        ("Min:", format_fact(timestamp.min_value, _date_or_datetime)),
        ("Max:", format_fact(timestamp.max_value, _date_or_datetime)),
        ("Nulls:", _nulls(timestamp)),
        ("Future values:", format_fact(timestamp.future_values, format_int)),
        ("Distinct dates:", format_fact(timestamp.distinct_dates, format_int)),
    ]
    gaps = _numbers(
        [
            ("min", timestamp.min_gap_days, format_int),
            ("median", timestamp.median_gap_days, format_decimal),
            ("mean", timestamp.mean_gap_days, format_decimal),
            ("max", timestamp.max_gap_days, format_int),
        ]
    )
    lines = [header, *_aligned(rows), "  Days between distinct dates:", f"    {gaps}"]
    lines += _window(timestamp, show_days)
    lines += [f"  Note: {note}." for note in timestamp.notes]
    return lines


def _nulls(timestamp: TimestampAnalysis) -> str:
    text = format_fact(timestamp.null_rows, format_int)
    share = timestamp.null_share
    if not share.available and share.reason == timestamp.null_rows.reason:
        return text
    return f"{text} ({format_fact(share, format_percent)})"


def _window(timestamp: TimestampAnalysis, show_days: bool) -> list[str]:
    first, last = timestamp.window_first_day, timestamp.window_last_day
    unit = "day" if timestamp.days == 1 else "days"
    label = f"Rows per day, last {timestamp.days} complete {unit}"
    if not first.available or first.value is None:
        return [f"  {label}:", f"    {format_fact(first)}"]
    if not last.available or last.value is None:
        return [f"  {label}:", f"    {format_fact(last)}"]

    stats = _numbers(
        [
            ("median", timestamp.rows_per_day_median, format_decimal),
            ("mean", timestamp.rows_per_day_mean, format_decimal),
            ("min", timestamp.rows_per_day_min, format_int),
            ("max", timestamp.rows_per_day_max, format_int),
        ]
    )
    lines = [
        f"  {label} ({format_date(first.value)} – {format_date(last.value)}):",
        f"    {stats}",
    ]
    rows_per_day = timestamp.rows_per_day
    if show_days and rows_per_day.available and rows_per_day.value is not None:
        lines += _day_table(rows_per_day.value)
    return lines


def _day_table(days: tuple[DayCount, ...]) -> list[str]:
    counts = [format_int(day.rows) for day in days]
    width = max((len(count) for count in counts), default=0)
    return [
        f"    {format_date(day.day)}  {count:>{width}}"
        for day, count in zip(days, counts, strict=True)
    ]


def _comparison_block(comparison: ComparisonAnalysis) -> list[str]:
    first, second = comparison.first_column, comparison.second_column
    header = f"COMPARE  {first} → {second}"
    facts: list[Fact[Any]] = [
        comparison.second_after_first,
        comparison.second_equal_first,
        comparison.second_before_first,
        comparison.either_null,
    ]
    if _same_unavailable(facts):
        return [header, f"  {format_fact(facts[0])}"]

    rows = [
        (f"{second} > {first}:", format_fact(comparison.second_after_first, format_int)),
        (f"{second} = {first}:", format_fact(comparison.second_equal_first, format_int)),
        (f"{second} < {first}:", format_fact(comparison.second_before_first, format_int)),
        ("Either is null:", format_fact(comparison.either_null, format_int)),
    ]
    lines = [header, *_aligned(rows)]
    first_is_date = comparison.first_data_type.strip().lower() == "date"
    second_is_date = comparison.second_data_type.strip().lower() == "date"
    if first_is_date != second_is_date:
        lines.append(f"  {DATE_COMPARISON_NOTE}")
    return lines


def _aligned(rows: list[tuple[str, str]]) -> list[str]:
    """Indent rows by two spaces and align the values to a common column."""
    width = max(len(label) for label, _ in rows) + 2
    return [f"  {label:<{width}}{value}" for label, value in rows]


def _numbers(parts: list[tuple[str, Fact[Any], Callable[[Any], str]]]) -> str:
    """'name value · name value …', or one 'n/a (<reason>)' if all share the same reason."""
    facts = [fact for _, fact, _ in parts]
    if _same_unavailable(facts):
        return format_fact(facts[0])
    return " · ".join(f"{name} {format_fact(fact, fmt)}" for name, fact, fmt in parts)


def _same_unavailable(facts: list[Fact[Any]]) -> bool:
    return all(not fact.available for fact in facts) and len({fact.reason for fact in facts}) == 1


def _date_or_datetime(value: date) -> str:
    return format_datetime(value) if isinstance(value, datetime) else format_date(value)
