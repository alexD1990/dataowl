from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeRunner

from dataowl.collect.properties import collect_properties
from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ObjectType, PropertiesInfo

REF = TableRef("main", "sales", "orders")
PROPS_SQL = r"SHOW TBLPROPERTIES"
MANAGED: Fact[ObjectType] = Fact(ObjectType.MANAGED, source="metadata")
NOT_SET: Fact[str] = Fact(None, source="metadata")


def _prop(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": value}


def _all(info: PropertiesInfo) -> list[Fact[str]]:
    return [info.change_data_feed, info.log_retention, info.deleted_file_retention]


def test_all_set(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        PROPS_SQL,
        [
            _prop("delta.enableChangeDataFeed", "true"),
            _prop("delta.logRetentionDuration", "interval 60 days"),
            _prop("delta.deletedFileRetentionDuration", "interval 14 days"),
            _prop("delta.minReaderVersion", "1"),
        ],
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    assert info == PropertiesInfo(
        change_data_feed=Fact("true", source="metadata"),
        log_retention=Fact("interval 60 days", source="metadata"),
        deleted_file_retention=Fact("interval 14 days", source="metadata"),
    )
    assert fake_runner.queries == [("SHOW TBLPROPERTIES `main`.`sales`.`orders`", None)]


def test_none_set(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        PROPS_SQL,
        [_prop("delta.minReaderVersion", "1"), _prop("delta.minWriterVersion", "7")],
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    assert _all(info) == [NOT_SET, NOT_SET, NOT_SET]


def test_mixed(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        PROPS_SQL,
        [_prop("delta.enableChangeDataFeed", "false"), _prop("delta.minReaderVersion", "1")],
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    assert info.change_data_feed == Fact("false", source="metadata")
    assert info.log_retention == NOT_SET
    assert info.deleted_file_retention == NOT_SET


def test_keys_match_case_insensitively(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        PROPS_SQL,
        [
            _prop("DELTA.ENABLECHANGEDATAFEED", "TRUE"),
            _prop("delta.logretentionduration", "interval 30 days"),
            _prop("Delta.DeletedFileRetentionDuration", "interval 7 days"),
        ],
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    assert info.change_data_feed == Fact("TRUE", source="metadata")
    assert info.log_retention == Fact("interval 30 days", source="metadata")
    assert info.deleted_file_retention == Fact("interval 7 days", source="metadata")


def test_zero_rows_means_not_set(fake_runner: FakeRunner) -> None:
    fake_runner.on(PROPS_SQL, [])

    info = collect_properties(fake_runner, REF, MANAGED)

    assert _all(info) == [NOT_SET, NOT_SET, NOT_SET]


def test_view_runs_no_query(fake_runner: FakeRunner) -> None:
    info = collect_properties(fake_runner, REF, Fact(ObjectType.VIEW, source="metadata"))

    assert fake_runner.queries == []
    assert _all(info) == [Fact.unavailable("metadata", "Not available for views")] * 3


@pytest.mark.parametrize(
    "object_type",
    [
        Fact(ObjectType.UNKNOWN, source="metadata"),
        Fact.unavailable("metadata", "Permission denied"),
    ],
)
def test_runs_for_unknown_or_unavailable_object_type(
    fake_runner: FakeRunner, object_type: Fact[ObjectType]
) -> None:
    fake_runner.on(PROPS_SQL, [_prop("delta.enableChangeDataFeed", "true")])

    info = collect_properties(fake_runner, REF, object_type)

    assert len(fake_runner.queries) == 1
    assert info.change_data_feed == Fact("true", source="metadata")


def test_query_error(fake_runner: FakeRunner) -> None:
    fake_runner.on_error(
        PROPS_SQL, RuntimeError("[UNSUPPORTED_FEATURE] SHOW TBLPROPERTIES not supported\nmore")
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    reason = "[UNSUPPORTED_FEATURE] SHOW TBLPROPERTIES not supported"
    assert _all(info) == [Fact.unavailable("metadata", reason)] * 3


@pytest.mark.parametrize("row", [{"value": "true"}, {"key": None, "value": "x"}, {"key": 1}])
def test_rows_without_string_key_are_skipped(fake_runner: FakeRunner, row: dict[str, Any]) -> None:
    fake_runner.on(PROPS_SQL, [row, _prop("delta.enableChangeDataFeed", "true")])

    info = collect_properties(fake_runner, REF, MANAGED)

    assert info.change_data_feed == Fact("true", source="metadata")
    assert info.log_retention == NOT_SET
    assert info.deleted_file_retention == NOT_SET


def test_irrelevant_row_with_bad_value_is_ignored(fake_runner: FakeRunner) -> None:
    fake_runner.on(
        PROPS_SQL,
        [
            _prop("delta.minReaderVersion", 1),
            _prop("delta.logRetentionDuration", "interval 30 days"),
        ],
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    assert info.log_retention == Fact("interval 30 days", source="metadata")


@pytest.mark.parametrize(("value", "type_name"), [(None, "NoneType"), (True, "bool")])
def test_bad_value_makes_only_that_property_unavailable(
    fake_runner: FakeRunner, value: Any, type_name: str
) -> None:
    fake_runner.on(
        PROPS_SQL,
        [
            _prop("delta.enableChangeDataFeed", value),
            _prop("delta.logRetentionDuration", "interval 30 days"),
        ],
    )

    info = collect_properties(fake_runner, REF, MANAGED)

    assert info.change_data_feed == Fact.unavailable(
        "metadata",
        "Unexpected row format in SHOW TBLPROPERTIES: value of delta.enableChangeDataFeed "
        f"has type {type_name}",
    )
    assert info.log_retention == Fact("interval 30 days", source="metadata")
    assert info.deleted_file_retention == NOT_SET
