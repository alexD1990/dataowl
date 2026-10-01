"""Terminal rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dataowl.model.overview import Overview


def render_overview(overview: Overview) -> str:
    """Placeholder until step 11: only the table name."""
    table = overview.table
    return f"{table.catalog}.{table.schema}.{table.table}"
