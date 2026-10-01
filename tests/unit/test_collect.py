from __future__ import annotations

from conftest import FakeRunner

from dataowl.collect import safe_query, short_reason


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
