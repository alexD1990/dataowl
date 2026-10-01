"""Formatting of single values for display."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from dataowl.model.facts import Fact

T = TypeVar("T")

_UNITS = ("KB", "MB", "GB", "TB")


def format_bytes(n: float) -> str:
    """Bytes below 1024 as an integer with B, then KB to TB with one decimal (base 1024)."""
    if round(n) < 1024:
        return f"{round(n)} B"
    value = float(n)
    for unit in _UNITS:
        value /= 1024
        if round(value, 1) < 1024 or unit == _UNITS[-1]:
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def format_int(n: int) -> str:
    """Integer with a space as thousands separator."""
    return f"{n:,}".replace(",", " ")


def format_datetime(value: datetime) -> str:
    """YYYY-MM-DD HH:MM, without time zone conversion."""
    return value.strftime("%Y-%m-%d %H:%M")


def format_fact(fact: Fact[T], fmt: Callable[[T], str] | None = None, none_text: str = "–") -> str:
    """Format a fact: 'n/a (<reason>)' if unavailable, none_text if the value is None."""
    if not fact.available:
        return f"n/a ({fact.reason})"
    if fact.value is None:
        return none_text
    return fmt(fact.value) if fmt is not None else str(fact.value)
