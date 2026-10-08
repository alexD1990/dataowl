from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeRunner

from dataowl.collect import is_catalog_not_found, safe_query, short_reason


def test_safe_query_returns_rows(fake_runner: FakeRunner) -> None:
    fake_runner.on(r"FROM t", [{"n": 1}])

    assert safe_query(fake_runner, "SELECT n FROM t", {"x": 1}) == [{"n": 1}]
    assert fake_runner.queries == [("SELECT n FROM t", {"x": 1})]


def test_safe_query_returns_exception(fake_runner: FakeRunner) -> None:
    error = PermissionError("Permission denied")
    fake_runner.on_error(r"FROM t", error)

    assert safe_query(fake_runner, "SELECT n FROM t") is error


def test_short_reason_first_line() -> None:
    exc = RuntimeError("[TABLE_OR_VIEW_NOT_FOUND] Not found.\nSQLSTATE: 42P01\nStack trace")

    assert short_reason(exc) == "[TABLE_OR_VIEW_NOT_FOUND] Not found."


def test_short_reason_skips_leading_blank_lines() -> None:
    assert short_reason(RuntimeError("\n\n  Real message  \nmore")) == "Real message"


def test_short_reason_truncates_to_200_characters() -> None:
    reason = short_reason(RuntimeError("x" * 500))

    assert len(reason) == 200
    assert reason.endswith("…")


def test_short_reason_keeps_message_of_exactly_200_characters() -> None:
    assert short_reason(RuntimeError("x" * 200)) == "x" * 200


def test_short_reason_empty_message_uses_type_name() -> None:
    assert short_reason(PermissionError()) == "PermissionError"


class _ConditionError(Exception):
    """Mimics a PySpark exception with getCondition() (PySpark 4)."""

    def __init__(self, message: str, condition: str | None) -> None:
        super().__init__(message)
        self._condition = condition

    def getCondition(self) -> str | None:
        return self._condition


class _ErrorClassError(Exception):
    """Mimics a PySpark exception with getErrorClass() only (PySpark 3.4 and 3.5)."""

    def __init__(self, message: str, error_class: str | None) -> None:
        super().__init__(message)
        self._error_class = error_class

    def getErrorClass(self) -> str | None:
        return self._error_class


def _from_message(error_class: str, line: str) -> Exception:
    return RuntimeError(f"[{error_class}] {line}\nSQLSTATE: 42P01")


def _from_condition(error_class: str, line: str) -> Exception:
    return _ConditionError(f"{line}\nSQLSTATE: 42P01", error_class)


def _from_error_class(error_class: str, line: str) -> Exception:
    return _ErrorClassError(f"{line}\nSQLSTATE: 42P01", error_class)


CATALOG_ERROR_CASES = [
    ("NO_SUCH_CATALOG_EXCEPTION", "Catalog 'dev' not found.", True),
    ("CATALOG_NOT_FOUND", "The catalog `dev` cannot be found.", True),
    (
        "TABLE_OR_VIEW_NOT_FOUND",
        "The table or view `dev`.`information_schema`.`tables` cannot be found.",
        True,
    ),
    (
        "TABLE_OR_VIEW_NOT_FOUND",
        "The table or view dev.INFORMATION_SCHEMA.tables cannot be found.",
        True,
    ),
    (
        "TABLE_OR_VIEW_NOT_FOUND",
        "The table or view `main`.`sales`.`orders` cannot be found.",
        False,
    ),
    ("PERMISSION_DENIED", "User does not have USE CATALOG on Catalog 'dev'.", False),
    (
        "INSUFFICIENT_PERMISSIONS",
        "Insufficient privileges: User does not have USE CATALOG on `dev`.information_schema.",
        False,
    ),
]


@pytest.mark.parametrize("make", [_from_message, _from_condition, _from_error_class])
@pytest.mark.parametrize(("error_class", "line", "expected"), CATALOG_ERROR_CASES)
def test_is_catalog_not_found(make: Any, error_class: str, line: str, expected: bool) -> None:
    assert is_catalog_not_found(make(error_class, line)) is expected


def test_is_catalog_not_found_condition_takes_precedence_over_message() -> None:
    exc = _ConditionError("[CATALOG_NOT_FOUND] The catalog `dev` cannot be found.", "OTHER")

    assert is_catalog_not_found(exc) is False


def test_is_catalog_not_found_falls_back_when_methods_give_no_value() -> None:
    message = "[CATALOG_NOT_FOUND] The catalog `dev` cannot be found."

    assert is_catalog_not_found(_ConditionError(message, None)) is True
    assert is_catalog_not_found(_ErrorClassError(message, "")) is True


def test_is_catalog_not_found_information_schema_only_on_later_line() -> None:
    exc = RuntimeError(
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view `main`.`s`.`t` cannot be found.\n"
        "while reading information_schema"
    )

    assert is_catalog_not_found(exc) is False


def test_is_catalog_not_found_requires_class_at_start_of_message() -> None:
    exc = RuntimeError("Error: [CATALOG_NOT_FOUND] The catalog `dev` cannot be found.")

    assert is_catalog_not_found(exc) is False


@pytest.mark.parametrize("message", ["", "   \n  ", "Catalog dev not found"])
def test_is_catalog_not_found_without_class(message: str) -> None:
    assert is_catalog_not_found(RuntimeError(message)) is False
