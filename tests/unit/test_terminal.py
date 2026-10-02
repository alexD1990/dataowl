from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

import pytest
from conftest import assert_no_judgement

from dataowl.identifiers import TableRef
from dataowl.model.facts import Fact
from dataowl.model.overview import ColumnInfo, ObjectType, Overview
from dataowl.render.terminal import render_overview

FIXTURES = Path(__file__).parent.parent / "fixtures"

VIEWS = "Not available for views"


def _m(value: object) -> Fact[object]:
    return Fact(value, source="metadata")


def table_overview() -> Overview:
    return Overview(
        table=TableRef("main", "skatt", "inntekt"),
        object_type=Fact(ObjectType.MANAGED, source="metadata"),
        object_type_raw=Fact("MANAGED", source="metadata"),
        format=Fact("DELTA", source="metadata"),
        owner=Fact("team-x", source="metadata"),
        comment=Fact(
            "Inntekt per skattyter og inntektsår.\n\nKilde: likningsgrunnlaget, levert nattlig "
            "fra fagsystemet.",
            source="metadata",
        ),
        created=Fact(datetime(2024, 3, 12, 8, 14, 33), source="metadata"),
        last_modified=Fact(datetime(2026, 9, 30, 3, 12, 5), source="metadata"),
        size_bytes=Fact(88290099, source="metadata"),
        num_files=Fact(12, source="metadata"),
        avg_file_size_bytes=Fact(88290099 / 12, source="derived"),
        num_rows=Fact(1284991, source="exact"),
        num_columns=Fact(4, source="metadata"),
        num_fields_nested=Fact(6, source="metadata"),
        partition_columns=Fact(("inntektsaar",), source="metadata"),
        clustering_columns=Fact((), source="metadata"),
        change_data_feed=Fact(None, source="metadata"),
        log_retention=Fact("interval 60 days", source="metadata"),
        deleted_file_retention=Fact(None, source="metadata"),
        history_first_commit=Fact(datetime(2026, 9, 2, 3, 10, 2), source="metadata"),
        history_last_commit=Fact(datetime(2026, 9, 30, 3, 12, 5), source="metadata"),
        history_num_commits=Fact(29, source="metadata"),
        history_operations=Fact({"WRITE": 27, "DELETE": 1, "MERGE": 1}, source="metadata"),
        columns=Fact(
            (
                ColumnInfo("skattyter_id", 0, "STRING", False, None),
                ColumnInfo("inntektsaar", 1, "INT", False, ""),
                ColumnInfo("belop", 2, "DECIMAL(18,2)", True, "Beløp i NOK"),
                ColumnInfo(
                    "adresse",
                    3,
                    "STRUCT<gate: STRING, postnr: STRING>",
                    True,
                    "Folkeregistrert adresse ved utgangen av\ninntektsåret, slik den er registrert",
                ),
            ),
            source="metadata",
        ),
    )


def view_overview() -> Overview:
    return Overview(
        table=TableRef("main", "skatt", "inntekt_v"),
        object_type=Fact(ObjectType.VIEW, source="metadata"),
        object_type_raw=Fact("VIEW", source="metadata"),
        format=Fact(None, source="metadata"),
        owner=Fact("team-x", source="metadata"),
        comment=Fact(None, source="metadata"),
        created=Fact(datetime(2025, 1, 2, 9, 0), source="metadata"),
        last_modified=Fact.unavailable("metadata", VIEWS),
        size_bytes=Fact.unavailable("metadata", VIEWS),
        num_files=Fact.unavailable("metadata", VIEWS),
        avg_file_size_bytes=Fact.unavailable("derived", VIEWS),
        num_rows=Fact.unavailable("exact", "Skipped for VIEW; use count_views=True"),
        num_columns=Fact(2, source="metadata"),
        num_fields_nested=Fact(2, source="metadata"),
        partition_columns=Fact.unavailable("metadata", VIEWS),
        clustering_columns=Fact.unavailable("metadata", VIEWS),
        change_data_feed=Fact.unavailable("metadata", VIEWS),
        log_retention=Fact.unavailable("metadata", VIEWS),
        deleted_file_retention=Fact.unavailable("metadata", VIEWS),
        history_first_commit=Fact.unavailable("metadata", VIEWS),
        history_last_commit=Fact.unavailable("metadata", VIEWS),
        history_num_commits=Fact.unavailable("metadata", VIEWS),
        history_operations=Fact.unavailable("metadata", VIEWS),
        columns=Fact(
            (
                ColumnInfo("skattyter_id", 1, "STRING", True, None),
                ColumnInfo("inntektsaar", 2, "INT", True, None),
            ),
            source="metadata",
        ),
    )


def unavailable_overview() -> Overview:
    denied = "[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG on Catalog 'my-catalog'."
    not_delta = "[DELTA_TABLE_ONLY_OPERATION] `my-catalog`.`raw`.`events` is not a Delta table."
    return Overview(
        table=TableRef("my-catalog", "raw", "events"),
        object_type=Fact.unavailable("metadata", denied),
        object_type_raw=Fact.unavailable("metadata", denied),
        format=Fact.unavailable("metadata", denied),
        owner=Fact.unavailable("metadata", denied),
        comment=Fact.unavailable("metadata", denied),
        created=Fact.unavailable("metadata", denied),
        last_modified=Fact.unavailable("metadata", not_delta),
        size_bytes=Fact.unavailable("metadata", not_delta),
        num_files=Fact(0, source="metadata"),
        avg_file_size_bytes=Fact.unavailable("derived", "No files"),
        num_rows=Fact.unavailable("exact", "Skipped: object type unknown; use count_views=True"),
        num_columns=Fact.unavailable("metadata", denied),
        num_fields_nested=Fact(3, source="metadata"),
        partition_columns=Fact.unavailable("metadata", not_delta),
        clustering_columns=Fact.unavailable("metadata", "Not present in DESCRIBE DETAIL output"),
        change_data_feed=Fact("true", source="metadata"),
        log_retention=Fact.unavailable(
            "metadata",
            "Unexpected row format in SHOW TBLPROPERTIES: value of delta.logRetentionDuration "
            "has type NoneType",
        ),
        deleted_file_retention=Fact(None, source="metadata"),
        history_first_commit=Fact.unavailable("metadata", not_delta),
        history_last_commit=Fact.unavailable("metadata", not_delta),
        history_num_commits=Fact.unavailable("metadata", not_delta),
        history_operations=Fact.unavailable("metadata", not_delta),
        columns=Fact.unavailable("metadata", denied),
    )


@pytest.mark.parametrize(
    ("overview", "snapshot"),
    [
        (table_overview(), "overview_table.txt"),
        (view_overview(), "overview_view.txt"),
        (unavailable_overview(), "overview_unavailable.txt"),
    ],
)
def test_snapshot(overview: Overview, snapshot: str) -> None:
    text = render_overview(overview)

    assert text + "\n" == (FIXTURES / snapshot).read_text(encoding="utf-8")
    assert_no_judgement(text)


def test_values_are_aligned() -> None:
    lines = render_overview(table_overview()).splitlines()
    fact_lines = lines[2 : lines.index("", 2)]

    starts = {len(line) - len(line.split(":", 1)[1].lstrip()) for line in fact_lines}
    assert len(starts) == 1


def test_no_trailing_whitespace() -> None:
    for overview in (table_overview(), view_overview(), unavailable_overview()):
        for line in render_overview(overview).splitlines():
            assert line == line.rstrip()


def test_long_comment_is_cut_to_40_characters() -> None:
    lines = render_overview(table_overview()).splitlines()
    adresse = next(line for line in lines if "adresse" in line)
    comment = adresse.split("    ")[-1].strip()

    assert comment.endswith("…")
    assert len(comment) == 40


def test_table_comment_is_cut_to_80_characters() -> None:
    lines = render_overview(table_overview()).splitlines()
    comment = next(line for line in lines if line.startswith("Comment:")).split(":", 1)[1].strip()

    assert comment.endswith("…")
    assert len(comment) == 80
    assert "\n" not in comment


@pytest.mark.parametrize("comment", [None, ""])
def test_missing_table_comment_is_dash(comment: str | None) -> None:
    overview = dataclasses.replace(view_overview(), comment=Fact(comment, source="metadata"))
    lines = render_overview(overview).splitlines()

    assert "Comment:                 –" in lines


def test_header_shows_only_na_for_unavailable_facts() -> None:
    header = render_overview(unavailable_overview()).splitlines()[0]

    assert header == "`my-catalog`.raw.events   (n/a, n/a)"


@pytest.mark.parametrize("first_position", [0, 1])
def test_schema_number_is_running_number(first_position: int) -> None:
    columns = tuple(
        ColumnInfo(name, first_position + i, "STRING", True, None)
        for i, name in enumerate(["a", "b", "c"])
    )
    overview = dataclasses.replace(view_overview(), columns=Fact(columns, source="metadata"))
    lines = render_overview(overview).splitlines()
    rows = lines[lines.index("SCHEMA") + 2 :]

    assert [row.split()[0] for row in rows] == ["1", "2", "3"]
    assert [row.split()[1] for row in rows] == ["a", "b", "c"]


def _history_lines(overview: Overview) -> list[str]:
    lines = render_overview(overview).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("HISTORY"))
    end = lines.index("SCHEMA")
    assert lines[start - 1] == ""
    assert lines[end - 1] == ""
    return lines[start : end - 1]


def test_history_block() -> None:
    assert _history_lines(table_overview()) == [
        "HISTORY  (2026-09-02 03:10 – 2026-09-30 03:12, 29 commits)",
        "  WRITE:   27",
        "  DELETE:  1",
        "  MERGE:   1",
    ]


def test_history_values_are_aligned_within_block() -> None:
    operations = {"WRITE": 1203, "WRITE (overwrite)": 4, "DELETE": 1}
    overview = dataclasses.replace(
        table_overview(),
        history_num_commits=Fact(1208, source="metadata"),
        history_operations=Fact(operations, source="metadata"),
    )

    lines = _history_lines(overview)

    assert lines == [
        "HISTORY  (2026-09-02 03:10 – 2026-09-30 03:12, 1 208 commits)",
        "  WRITE:              1 203",
        "  WRITE (overwrite):  4",
        "  DELETE:             1",
    ]
    assert_no_judgement("\n".join(lines))


def test_history_single_commit() -> None:
    commit = Fact(datetime(2026, 9, 30, 3, 12), source="metadata")
    overview = dataclasses.replace(
        table_overview(),
        history_first_commit=commit,
        history_last_commit=commit,
        history_num_commits=Fact(1, source="metadata"),
        history_operations=Fact({"CREATE TABLE": 1}, source="metadata"),
    )

    assert _history_lines(overview) == [
        "HISTORY  (2026-09-30 03:12 – 2026-09-30 03:12, 1 commit)",
        "  CREATE TABLE:  1",
    ]


def test_history_unavailable() -> None:
    assert _history_lines(view_overview()) == ["HISTORY", "  n/a (Not available for views)"]


def test_history_partly_unavailable() -> None:
    overview = dataclasses.replace(
        table_overview(),
        history_first_commit=Fact.unavailable("metadata", "first missing"),
        history_num_commits=Fact.unavailable("metadata", "count missing"),
        history_operations=Fact.unavailable("metadata", "operations missing"),
    )

    lines = _history_lines(overview)

    assert lines == [
        "HISTORY  (n/a (first missing) – 2026-09-30 03:12, commits: n/a (count missing))",
        "  n/a (operations missing)",
    ]
    assert_no_judgement("\n".join(lines))
