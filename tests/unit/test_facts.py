from __future__ import annotations

import dataclasses

import pytest

from dataowl.model.facts import Fact, derive


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
