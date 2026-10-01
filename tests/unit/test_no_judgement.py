from __future__ import annotations

import pytest
from conftest import assert_no_judgement


@pytest.mark.parametrize(
    "text",
    [
        "Rows:  1 284 991",
        "Change Data Feed:  not set (default)",
        "unsafe_flag  safety  recommended_by  shoulder  suitably",
        "",
    ],
)
def test_accepts_neutral_text(text: str) -> None:
    assert_no_judgement(text)


@pytest.mark.parametrize(
    "text",
    [
        "We recommend incremental",
        "This should be merge",
        "Key is SAFE",
        "Suitable for append",
        "unique ✓",
        "⚠ many nulls",
        "✗ duplicates",
        "safe.",
    ],
)
def test_rejects_judgement(text: str) -> None:
    with pytest.raises(AssertionError):
        assert_no_judgement(text)
