"""Exceptions raised by dataowl."""

from __future__ import annotations


class TableNotFoundError(Exception):
    """The table does not exist, or the current user has no access to it."""
