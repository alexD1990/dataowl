from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import assert_no_judgement

from dataowl.collect.history import (
    build_history_analysis,
    parse_commits,
    parse_commits_with_metrics,
)
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.history import HistoryAnalysis, HourCount, RowStats
from dataowl.render.terminal import render_history

FIXTURES = Path(__file__).parent.parent / "fixtures"

ORDERS = TableRef("main", "sales", "orders")
OSLO: Fact[str] = Fact("Europe/Oslo", source="metadata")


def _history_rows() -> list[dict[str, Any]]:
    """Load describe_history_table.json and convert timestamps to datetime."""
    rows: list[dict[str, Any]] = json.loads((FIXTURES / "describe_history_table.json").read_text())
    for row in rows:
        row["timestamp"] = datetime.fromisoformat(row["timestamp"])
    return rows


def full_analysis() -> HistoryAnalysis:
    """All commits from the fixture; the OPTIMIZE commit is moved to 14:05 the same day."""
    rows = _history_rows()
    rows[5]["timestamp"] = datetime(2026, 9, 5, 14, 5, 7)
    commits = Fact(parse_commits_with_metrics(rows), source="metadata")
    return build_history_analysis(ORDERS, None, OSLO, commits, None)


def limit_analysis() -> HistoryAnalysis:
    """The three newest commits; the DELETE commit has no numDeletedRows."""
    rows = _history_rows()[:3]
    del rows[1]["operationMetrics"]["numDeletedRows"]
    commits = Fact(parse_commits_with_metrics(rows), source="metadata")
    utc: Fact[str] = Fact("Etc/UTC", source="metadata")
    return build_history_analysis(ORDERS, 3, utc, commits, None)


def view_analysis() -> HistoryAnalysis:
    commits: Fact[Any] = Fact.unavailable("metadata", "Not available for views")
    return build_history_analysis(TableRef("main", "sales", "orders_v"), None, OSLO, commits, None)


def partial_analysis() -> HistoryAnalysis:
    time_zone: Fact[str] = Fact.unavailable(
        "metadata", "[UNSUPPORTED] current_timezone is not supported"
    )
    reason = (
        "Unexpected row format in DESCRIBE HISTORY: "
        "operationMetrics.numUpdatedRows is not a non-negative integer (str)"
    )
    commits = Fact(parse_commits(_history_rows()), source="metadata")
    return build_history_analysis(ORDERS, None, time_zone, commits, reason)


@pytest.mark.parametrize(
    ("build", "fixture"),
    [
        (full_analysis, "history_full.txt"),
        (limit_analysis, "history_limit.txt"),
        (view_analysis, "history_view.txt"),
        (partial_analysis, "history_partial.txt"),
    ],
)
def test_snapshot(build: Any, fixture: str) -> None:
    text = render_history(build())

    assert text + "\n" == (FIXTURES / fixture).read_text(encoding="utf-8")
    assert_no_judgement(text)


def test_window_is_shown_first() -> None:
    lines = render_history(full_analysis()).splitlines()

    assert lines[0] == "main.sales.orders"
    assert lines[2].startswith("HISTORY  (2026-09-02 03:10 – 2026-09-30 03:12")


def test_empty_row_stats() -> None:
    analysis = dataclasses.replace(full_analysis(), row_stats=Fact((), source="derived"))

    text = render_history(analysis)

    assert "\n\nROWS\n  –\n\nNote: " in text
    assert_no_judgement(text)


def test_hours_without_commits_are_left_out_and_counts_are_right_aligned() -> None:
    hours = tuple(HourCount(hour, {0: 12, 7: 3, 23: 1}.get(hour, 0)) for hour in range(24))
    analysis = dataclasses.replace(full_analysis(), commits_per_hour=Fact(hours, source="derived"))

    lines = render_history(analysis).splitlines()

    start = lines.index("  Commits per hour of day:") + 1
    assert lines[start : start + 4] == ["    00:00  12", "    07:00   3", "    23:00   1", ""]


def test_singular_day_commit_and_limit() -> None:
    rows = _history_rows()[:1]
    commits = Fact(parse_commits_with_metrics(rows), source="metadata")
    analysis = build_history_analysis(ORDERS, 1, OSLO, commits, None)

    lines = render_history(analysis).splitlines()

    assert lines[2] == "HISTORY  (2026-09-30 03:12 – 2026-09-30 03:12, 1 day, 1 commit)"
    assert lines[3] == "  Limit:            newest 1 commit"


def test_no_metric_shows_dash() -> None:
    text = render_history(limit_analysis())

    assert "  DELETE     deleted   0/1      –      –       –\n" in text


def test_operations_shown_when_unavailable_for_another_reason() -> None:
    reason = "Not available for views"
    analysis = dataclasses.replace(
        view_analysis(),
        operations=Fact.unavailable("metadata", "other"),
        row_stats=Fact((RowStats("WRITE", "inserted", 1, 1, 5, 5.0, 5),), source="derived"),
    )

    text = render_history(analysis)

    assert f"HISTORY\n  n/a ({reason})\n\nOPERATIONS\n  n/a (other)\n\nROWS\n" in text


def test_show_prints_rendered_history(capsys: pytest.CaptureFixture[str]) -> None:
    analysis = full_analysis()

    analysis.show()

    assert capsys.readouterr().out == render_history(analysis) + "\n"
