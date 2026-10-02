"""Analysis model: facts from step 2 (analyze)."""

from __future__ import annotations

from dataclasses import dataclass

from dataowl.model.facts import Fact


@dataclass(frozen=True)
class KeyAnalysis:
    """Grain and uniqueness of one key: a single column or a composite key.

    columns holds the column names as they appear in the schema. Rows with null in any key
    column are counted in rows_with_null_key and left out of every other per-key fact. The
    buckets (1, 2–10, 11–100, >100 rows per key) are fixed.
    """

    columns: tuple[str, ...]
    total_rows: Fact[int]
    rows_with_null_key: Fact[int]
    distinct_keys: Fact[int]
    duplicate_keys: Fact[int]
    rows_in_duplicate_keys: Fact[int]
    max_rows_per_key: Fact[int]
    median_rows_per_key: Fact[float]
    keys_with_1_row: Fact[int]
    keys_with_2_10_rows: Fact[int]
    keys_with_11_100_rows: Fact[int]
    keys_with_over_100_rows: Fact[int]
