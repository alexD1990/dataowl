from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from dataowl.model.facts import Fact
from dataowl.render.format import (
    format_bytes,
    format_date,
    format_datetime,
    format_decimal,
    format_fact,
    format_int,
    format_percent,
)


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (0.0, "0"),
        (18.0, "18"),
        (18, "18"),
        (7.06, "7.1"),
        (7.96, "8"),
        (0.04, "0"),
        (4283.33, "4 283.3"),
        (4328.0, "4 328"),
        (1284991.0, "1 284 991"),
        (1234567.89, "1 234 567.9"),
    ],
)
def test_format_decimal(value: float, expected: str) -> None:
    assert format_decimal(value) == expected


@pytest.mark.parametrize(
    ("share", "expected"),
    [
        (0.0, "0.00 %"),
        (0.01234, "1.23 %"),
        (0.000999, "0.10 %"),
        (0.5, "50.00 %"),
        (1.0, "100.00 %"),
    ],
)
def test_format_percent(share: float, expected: str) -> None:
    assert format_percent(share) == expected


def test_format_date() -> None:
    assert format_date(date(2026, 1, 4)) == "2026-01-04"
