"""Task 4: SQLite metadata repository."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.core.errors import NotFoundError
from backend.repositories.dataset_repo import DatasetRepository
from backend.repositories.db import connect
from backend.schemas.datasets import DatasetColumn, DatasetRecord


class _Doc(BaseModel):
    value: int
    label: str


@pytest.fixture
def repo(tmp_path: Path) -> DatasetRepository:
    return DatasetRepository(tmp_path / "app.db")


def _record(dataset_id: str = "ds1", name: str = "Sales") -> DatasetRecord:
    return DatasetRecord(
        id=dataset_id,
        name=name,
        source_filename="sales.xlsx",
        sheet_name="Sheet1",
        status="processing",
        created_at=datetime(2026, 11, 4, 3, 0, tzinfo=UTC),
    )


def _columns() -> list[DatasetColumn]:
    return [
        DatasetColumn(position=0, name="id_klien", original_name="ID Klien", logical_type="text"),
        DatasetColumn(
            position=1,
            name="tanggal",
            original_name="Tanggal",
            logical_type="date",
            failed_conversions=2,
        ),
    ]


def test_schema_has_all_metadata_tables(tmp_path: Path) -> None:
    with connect(tmp_path / "app.db") as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert {
        "datasets",
        "dataset_columns",
        "dataset_profiles",
        "column_mappings",
        "conversations",
        "analyses",
        "llm_usage",
    } <= tables


def test_create_get_list_round_trip(repo: DatasetRepository) -> None:
    created = repo.create(_record())
    repo.create(_record("ds2", "Clients"))

    assert repo.get("ds1") == created
    # Newest first; equal timestamps fall back to insertion order.
    assert [r.id for r in repo.list()] == ["ds2", "ds1"]


def test_get_missing_raises_not_found(repo: DatasetRepository) -> None:
    with pytest.raises(NotFoundError):
        repo.get("missing")


def test_update_status(repo: DatasetRepository) -> None:
    repo.create(_record())

    repo.update_status("ds1", "ready", row_count=10, column_count=3)
    ready = repo.get("ds1")
    repo.update_status("ds1", "failed", error="Bad file")
    failed = repo.get("ds1")

    assert (ready.status, ready.row_count, ready.column_count) == ("ready", 10, 3)
    assert (failed.status, failed.error, failed.row_count) == ("failed", "Bad file", 10)


def test_rename(repo: DatasetRepository) -> None:
    repo.create(_record())

    repo.rename("ds1", "Renamed")

    assert repo.get("ds1").name == "Renamed"


def test_columns_round_trip_in_position_order(repo: DatasetRepository) -> None:
    repo.create(_record())

    repo.save_columns("ds1", list(reversed(_columns())))

    assert repo.get_columns("ds1") == _columns()


def test_documents_round_trip_and_overwrite(repo: DatasetRepository) -> None:
    repo.create(_record())

    assert repo.get_document("ds1", "profile", _Doc) is None
    repo.save_document("ds1", "profile", _Doc(value=1, label="a"))
    repo.save_document("ds1", "profile", _Doc(value=2, label="b"))
    repo.save_document("ds1", "mapping", _Doc(value=3, label="c"))

    assert repo.get_document("ds1", "profile", _Doc) == _Doc(value=2, label="b")
    assert repo.get_document("ds1", "mapping", _Doc) == _Doc(value=3, label="c")


def test_delete_cascades_to_child_rows(repo: DatasetRepository, tmp_path: Path) -> None:
    repo.create(_record())
    repo.save_columns("ds1", _columns())
    repo.save_document("ds1", "profile", _Doc(value=1, label="a"))
    repo.save_document("ds1", "mapping", _Doc(value=1, label="a"))
    with connect(tmp_path / "app.db") as conn:
        conn.execute(
            "INSERT INTO conversations (id, dataset_id, title, created_at) VALUES (?, ?, ?, ?)",
            ("c1", "ds1", "t", "2026-11-04T03:00:00+00:00"),
        )

    repo.delete("ds1")

    with pytest.raises(NotFoundError):
        repo.get("ds1")
    with connect(tmp_path / "app.db") as conn:
        for table in ("dataset_columns", "dataset_profiles", "column_mappings", "conversations"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_delete_missing_raises_not_found(repo: DatasetRepository) -> None:
    with pytest.raises(NotFoundError):
        repo.delete("missing")


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    with connect(tmp_path / "app.db") as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dataset_columns (dataset_id, position, name, original_name, "
            "logical_type, failed_conversions) VALUES (?, ?, ?, ?, ?, ?)",
            ("nope", 0, "a", "A", "text", 0),
        )


# --- Phase 3: notes, import options, schema migration --------------------------


def test_column_notes_round_trip(repo: DatasetRepository) -> None:
    from backend.schemas.datasets import ColumnNote

    repo.create(_record())
    note = ColumnNote(level="warning", code="ambiguous_number_format", message="m")
    column = DatasetColumn(
        position=0, name="nilai", original_name="Nilai", logical_type="integer", notes=[note]
    )

    repo.save_columns("ds1", [column])

    assert repo.get_columns("ds1")[0].notes == [note]


def test_import_options_are_stored(repo: DatasetRepository) -> None:
    record = _record().model_copy(update={"number_format": "en", "date_order": "month_first"})

    repo.create(record)

    stored = repo.get("ds1")
    assert (stored.number_format, stored.date_order) == ("en", "month_first")


def test_old_database_is_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    old = sqlite3.connect(db_path)
    old.executescript(
        """CREATE TABLE datasets (id TEXT PRIMARY KEY, name TEXT NOT NULL,
           source_filename TEXT NOT NULL, sheet_name TEXT, status TEXT NOT NULL, error TEXT,
           row_count INTEGER, column_count INTEGER, created_at TEXT NOT NULL);
           CREATE TABLE dataset_columns (dataset_id TEXT NOT NULL, position INTEGER NOT NULL,
           name TEXT NOT NULL, original_name TEXT NOT NULL, logical_type TEXT NOT NULL,
           failed_conversions INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (dataset_id, position));
           INSERT INTO datasets VALUES ('ds1','n','f.csv',NULL,'ready',NULL,1,1,
           '2026-11-04T03:00:00+00:00');
           INSERT INTO dataset_columns VALUES ('ds1', 0, 'a', 'A', 'text', 0);"""
    )
    old.commit()
    old.close()

    repo = DatasetRepository(db_path)

    assert repo.get("ds1").number_format == "auto"
    assert repo.get_columns("ds1")[0].notes == []
