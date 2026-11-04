"""Persistence for dataset records, columns, profiles, and column mappings."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel

from backend.core.errors import NotFoundError
from backend.repositories.db import connect
from backend.schemas.datasets import DatasetColumn, DatasetRecord, DatasetStatus

DocumentKind = Literal["profile", "mapping"]
_DOCUMENT_TABLES: dict[DocumentKind, str] = {
    "profile": "dataset_profiles",
    "mapping": "column_mappings",
}
ModelT = TypeVar("ModelT", bound=BaseModel)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_record(row: sqlite3.Row) -> DatasetRecord:
    data = dict(row)
    data["text_columns"] = json.loads(data.pop("text_columns_json", None) or "[]")
    return DatasetRecord.model_validate(data)


def _to_column(row: sqlite3.Row) -> DatasetColumn:
    data = dict(row)
    data["notes"] = json.loads(data.pop("notes_json") or "[]")
    data.pop("dataset_id", None)
    return DatasetColumn.model_validate(data)


class DatasetRepository:
    """SQLite-backed store for dataset metadata. One connection per operation."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def create(self, record: DatasetRecord) -> DatasetRecord:
        """Insert a new dataset record."""
        with connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO datasets (id, name, source_filename, sheet_name, status, error,
                   row_count, column_count, created_at, number_format, date_order,
                   text_columns_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.name,
                    record.source_filename,
                    record.sheet_name,
                    record.status,
                    record.error,
                    record.row_count,
                    record.column_count,
                    record.created_at.isoformat(),
                    record.number_format,
                    record.date_order,
                    json.dumps(record.text_columns),
                ),
            )
        return record

    def get(self, dataset_id: str) -> DatasetRecord:
        """Return one dataset or raise ``NotFoundError``."""
        with connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")
        return _to_record(row)

    def list(self) -> list[DatasetRecord]:
        """Return all datasets, most recently created first."""
        with connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC, rowid DESC")
            return [_to_record(r) for r in rows]

    def update_status(
        self,
        dataset_id: str,
        status: DatasetStatus,
        *,
        error: str | None = None,
        row_count: int | None = None,
        column_count: int | None = None,
    ) -> None:
        """Set the status (and optionally counts); counts left as None are unchanged."""
        with connect(self._db_path) as conn:
            cursor = conn.execute(
                """UPDATE datasets SET status = ?, error = ?,
                   row_count = COALESCE(?, row_count),
                   column_count = COALESCE(?, column_count) WHERE id = ?""",
                (status, error, row_count, column_count, dataset_id),
            )
        if cursor.rowcount == 0:
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")

    def rename(self, dataset_id: str, name: str) -> None:
        """Change a dataset's display name."""
        with connect(self._db_path) as conn:
            cursor = conn.execute("UPDATE datasets SET name = ? WHERE id = ?", (name, dataset_id))
        if cursor.rowcount == 0:
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")

    def delete(self, dataset_id: str) -> None:
        """Delete a dataset; child rows are removed by ``ON DELETE CASCADE``."""
        with connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        if cursor.rowcount == 0:
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")

    def save_columns(self, dataset_id: str, columns: list[DatasetColumn]) -> None:
        """Replace the stored column list of a dataset."""
        with connect(self._db_path) as conn:
            conn.execute("DELETE FROM dataset_columns WHERE dataset_id = ?", (dataset_id,))
            conn.executemany(
                """INSERT INTO dataset_columns (dataset_id, position, name, original_name,
                   logical_type, failed_conversions, notes_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        dataset_id,
                        c.position,
                        c.name,
                        c.original_name,
                        c.logical_type,
                        c.failed_conversions,
                        json.dumps([n.model_dump() for n in c.notes]),
                    )
                    for c in columns
                ],
            )

    def get_columns(self, dataset_id: str) -> list[DatasetColumn]:
        """Return columns in their original order."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY position",
                (dataset_id,),
            )
            return [_to_column(r) for r in rows]

    def save_document(self, dataset_id: str, kind: DocumentKind, document: BaseModel) -> None:
        """Store (or replace) a JSON document such as the profile or column mapping."""
        table = _DOCUMENT_TABLES[kind]
        with connect(self._db_path) as conn:
            conn.execute(
                f"""INSERT INTO {table} (dataset_id, document_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(dataset_id) DO UPDATE SET
                    document_json = excluded.document_json, updated_at = excluded.updated_at""",
                (dataset_id, document.model_dump_json(), _now()),
            )

    def get_document(
        self, dataset_id: str, kind: DocumentKind, model: type[ModelT]
    ) -> ModelT | None:
        """Load a stored JSON document as ``model``, or None if absent."""
        table = _DOCUMENT_TABLES[kind]
        with connect(self._db_path) as conn:
            row = conn.execute(
                f"SELECT document_json FROM {table} WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return None if row is None else model.model_validate_json(row["document_json"])
