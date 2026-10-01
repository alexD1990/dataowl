from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

import pytest

from dataowl.model.facts import Fact, derive, to_jsonable


def test_available_fact() -> None:
    fact = Fact(42, source="exact")

    assert fact.value == 42
    assert fact.source == "exact"
    assert fact.available is True
    assert fact.reason is None


def test_unavailable_fact() -> None:
    fact = Fact.unavailable("metadata", "Permission denied")

    assert fact.value is None
    assert fact.source == "metadata"
    assert fact.available is False
    assert fact.reason == "Permission denied"


def test_fact_is_frozen() -> None:
    fact = Fact(1, source="exact")

    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.value = 2  # type: ignore[misc]


def test_derive_with_available_inputs() -> None:
    size = Fact(1000, source="metadata")
    files = Fact(4, source="metadata")

    result = derive(lambda s, f: s / f, size, files)

    assert result == Fact(250.0, source="derived")


def test_derive_with_unavailable_input() -> None:
    size = Fact(1000, source="metadata")
    files = Fact.unavailable("metadata", "Not available for views")

    result = derive(lambda s, f: s / f, size, files)

    assert result.available is False
    assert result.value is None
    assert result.source == "derived"


def test_derive_propagates_reason_from_first_unavailable_input() -> None:
    first = Fact.unavailable("metadata", "first reason")
    second = Fact.unavailable("exact", "second reason")

    result = derive(lambda a, b: a + b, Fact(1, source="exact"), first, second)

    assert result.reason == "first reason"


def test_derive_does_not_call_fn_when_input_unavailable() -> None:
    def fail(*_: object) -> None:
        raise AssertionError("fn must not be called")

    result = derive(fail, Fact.unavailable("exact", "error"))

    assert result.available is False


def test_available_fact_with_none_value_is_valid() -> None:
    fact = Fact(None, source="metadata")

    assert fact.available is True
    assert fact.value is None


def test_available_fact_with_reason_raises() -> None:
    with pytest.raises(ValueError):
        Fact(1, source="exact", reason="unexpected")


@pytest.mark.parametrize("reason", [None, ""])
def test_unavailable_fact_without_reason_raises(reason: str | None) -> None:
    with pytest.raises(ValueError):
        Fact(None, source="exact", available=False, reason=reason)


def test_unavailable_fact_with_value_raises() -> None:
    with pytest.raises(ValueError):
        Fact(1, source="exact", available=False, reason="error")


class _Color(Enum):
    RED = "red"


@dataclass(frozen=True)
class _Inner:
    when: date
    tags: tuple[str, ...]


def test_to_jsonable_converts_supported_types() -> None:
    obj = {
        "fact": Fact(datetime(2026, 9, 30, 3, 12), source="metadata"),
        "unavailable": Fact.unavailable("exact", "denied"),
    }

    assert to_jsonable(obj["fact"]) == {
        "value": "2026-09-30T03:12:00",
        "source": "metadata",
        "available": True,
        "reason": None,
    }
    assert to_jsonable(obj["unavailable"]) == {
        "value": None,
        "source": "exact",
        "available": False,
        "reason": "denied",
    }
    assert to_jsonable(_Color.RED) == "red"
    assert to_jsonable(date(2026, 9, 30)) == "2026-09-30"
    assert to_jsonable((1, (2, 3))) == [1, [2, 3]]
    assert to_jsonable(_Inner(date(2026, 1, 1), ("a",))) == {"when": "2026-01-01", "tags": ["a"]}
    assert to_jsonable(Fact((_Inner(date(2026, 1, 1), ()),), source="metadata"))["value"] == [
        {"when": "2026-01-01", "tags": []}
    ]
    for plain in ("x", 1, 1.5, True, None):
        assert to_jsonable(plain) == plain
