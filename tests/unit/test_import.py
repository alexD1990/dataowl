from __future__ import annotations


def test_import_dataowl() -> None:
    import dataowl

    assert dataowl.__name__ == "dataowl"
