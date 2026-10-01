"""Fact: a single value together with its source and availability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")
R = TypeVar("R")

SourceType = Literal["metadata", "exact", "derived"]


@dataclass(frozen=True)
class Fact(Generic[T]):
    """A fact with its source.

    An unavailable fact requires value None and a non-empty reason. An available fact
    requires reason None. An available fact may still have value None, e.g. a table
    property that is not set.
    """

    value: T | None
    source: SourceType
    available: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.available and self.reason is not None:
            raise ValueError("An available fact must not have a reason")
        if not self.available and not self.reason:
            raise ValueError("An unavailable fact requires a non-empty reason")
        if not self.available and self.value is not None:
            raise ValueError("An unavailable fact must have value None")

    @classmethod
    def unavailable(cls, source: SourceType, reason: str) -> Fact[T]:
        return cls(value=None, source=source, available=False, reason=reason)


def derive(fn: Callable[..., R], *facts: Fact[Any]) -> Fact[R]:
    """Apply fn to the values of facts, or propagate the first unavailable input."""
    for fact in facts:
        if not fact.available:
            assert fact.reason is not None  # guaranteed by Fact.__post_init__
            return Fact.unavailable("derived", fact.reason)
    return Fact(fn(*(fact.value for fact in facts)), source="derived")
