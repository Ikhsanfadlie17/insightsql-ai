"""SQLite connection helper and metadata schema."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    sheet_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    error TEXT,
    row_count INTEGER,
    column_count INTEGER,
    created_at TEXT NOT NULL,
    number_format TEXT NOT NULL DEFAULT 'auto',
    date_order TEXT NOT NULL DEFAULT 'auto',
    text_columns_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS dataset_columns (
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    logical_type TEXT NOT NULL,
    failed_conversions INTEGER NOT NULL DEFAULT 0,
    notes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (dataset_id, position)
);

CREATE TABLE IF NOT EXISTS dataset_profiles (
    dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
    document_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS column_mappings (
    dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
    document_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    sql TEXT,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id TEXT PRIMARY KEY,
    analysis_id TEXT REFERENCES analyses(id) ON DELETE SET NULL,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    est_cost_usd REAL,
    latency_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_dataset ON conversations(dataset_id);
CREATE INDEX IF NOT EXISTS idx_analyses_conversation ON analyses(conversation_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
"""

# Columns added after the first release: (table, column, definition). Applied to
# databases created by an older version, which CREATE TABLE IF NOT EXISTS skips.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("datasets", "number_format", "TEXT NOT NULL DEFAULT 'auto'"),
    ("datasets", "date_order", "TEXT NOT NULL DEFAULT 'auto'"),
    ("datasets", "text_columns_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("dataset_columns", "notes_json", "TEXT NOT NULL DEFAULT '[]'"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection with the schema applied and foreign keys enforced.

    The transaction is committed on success and rolled back on error.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
