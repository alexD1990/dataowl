from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dataowl.model.facts import Fact
from dataowl.render.format import format_bytes, format_datetime, format_fact, format_int


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        (1023.4, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2 - 1, "1.0 MB"),
        (1024**2, "1.0 MB"),
        (88290099, "84.2 MB"),
        (88290099 / 12, "7.0 MB"),
        (1024**3, "1.0 GB"),
        (1024**4, "1.0 TB"),
        (5 * 1024**5, "5120.0 TB"),
    ],
)
def test_format_bytes(n: float, expected: str) -> None:
    assert format_bytes(n) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0"), (999, "999"), (1000, "1 000"), (1284991, "1 284 991"), (-1234, "-1 234")],
)
def test_format_int(n: int, expected: str) -> None:
    assert format_int(n) == expected


def test_format_datetime() -> None:
    assert format_datetime(datetime(2026, 9, 30, 3, 12, 59)) == "2026-09-30 03:12"


def test_format_datetime_keeps_time_zone() -> None:
    value = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
    assert format_datetime(value) == "2026-09-30 23:30"


def test_format_fact_unavailable() -> None:
    assert format_fact(Fact.unavailable("exact", "denied")) == "n/a (denied)"


def test_format_fact_none_value() -> None:
    assert format_fact(Fact(None, source="metadata")) == "–"
    assert format_fact(Fact(None, source="metadata"), none_text="not set") == "not set"


def test_format_fact_with_and_without_fmt() -> None:
    assert format_fact(Fact(1284991, source="exact"), format_int) == "1 284 991"
    assert format_fact(Fact("DELTA", source="metadata")) == "DELTA"
    assert format_fact(Fact(0, source="exact")) == "0"
