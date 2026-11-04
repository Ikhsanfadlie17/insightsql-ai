"""Small helpers for building SQL from trusted, server-generated identifiers."""

from __future__ import annotations


def quote_identifier(identifier: str) -> str:
    """Double-quote an identifier for DuckDB, escaping embedded quotes."""
    return '"' + identifier.replace('"', '""') + '"'
