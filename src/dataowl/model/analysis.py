"""Analysis model: facts from step 2 (analyze)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

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


GAP_NOTE = "gaps are measured in whole days; cadence below one day is not visible"
COUNT_NOTE = "counts reflect current values of the column, not historical changes"
TIMESTAMP_NOTES = (GAP_NOTE, COUNT_NOTE)


@dataclass(frozen=True)
class DayCount:
    """Number of rows on one day."""

    day: date
    rows: int


@dataclass(frozen=True)
class TimestampAnalysis:
    """Time span and cadence of one timestamp, timestamp_ntz or date column.

    min_value and max_value are datetime for timestamp and timestamp_ntz, and date for date.
    Gaps are measured in whole days between consecutive distinct dates.

    The window is the last `days` whole days, not including the current day, in the session
    time zone: from window_first_day (today - days) to window_last_day (today - 1).
    rows_per_day holds one DayCount per day in the window, in ascending order, with 0 for
    days without rows. The per-day statistics are computed from rows_per_day.
    """

    column: str
    data_type: str
    total_rows: Fact[int]
    null_rows: Fact[int]
    min_value: Fact[datetime | date]
    max_value: Fact[datetime | date]
    future_values: Fact[int]
    distinct_dates: Fact[int]
    min_gap_days: Fact[int]
    median_gap_days: Fact[float]
    mean_gap_days: Fact[float]
    max_gap_days: Fact[int]
    days: int
    window_first_day: Fact[date]
    window_last_day: Fact[date]
    rows_per_day: Fact[tuple[DayCount, ...]]
    rows_per_day_min: Fact[int]
    rows_per_day_median: Fact[float]
    rows_per_day_mean: Fact[float]
    rows_per_day_max: Fact[int]
    notes: tuple[str, ...] = TIMESTAMP_NOTES


@dataclass(frozen=True)
class ComparisonAnalysis:
    """Row counts comparing two user-selected time columns.

    Rows where at least one of the columns is null are counted only in either_null. The
    other three facts count rows where both columns are non-null, so the four facts together
    cover every row.
    """

    first_column: str
    second_column: str
    first_data_type: str
    second_data_type: str
    second_after_first: Fact[int]
    second_equal_first: Fact[int]
    second_before_first: Fact[int]
    either_null: Fact[int]
