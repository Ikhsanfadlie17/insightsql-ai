"""Task 5: dataset ingestion service."""

from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb
import pytest

from backend.core.config import Settings
from backend.core.errors import NotFoundError, UploadError, ValidationError
from backend.repositories.dataset_repo import DatasetRepository
from backend.services.dataset_service import DatasetService
from tests.conftest import SAMPLE_DATA_DIR

SAMPLE_CSV = SAMPLE_DATA_DIR / "client_services.csv"
SAMPLE_XLSX = SAMPLE_DATA_DIR / "client_services.xlsx"


@pytest.fixture
def service(settings: Settings) -> DatasetService:
    return DatasetService(settings, DatasetRepository(settings.metadata_db_path))


def _ingest(service: DatasetService, path: Path, sheet: str | None = None) -> str:
    inspection = service.inspect_upload(path.name, path.read_bytes())
    record = service.create_dataset(inspection.upload_id, sheet, None)
    service.ingest(record.id, inspection.upload_id, sheet)
    return record.id


def test_inspect_csv_reports_single_table(service: DatasetService) -> None:
    inspection = service.inspect_upload(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())

    assert inspection.file_type == "csv"
    assert inspection.filename == "client_services.csv"
    assert [(s.name, s.row_count) for s in inspection.sheets] == [("client_services", 795)]


def test_inspect_xlsx_lists_sheets(service: DatasetService) -> None:
    inspection = service.inspect_upload(SAMPLE_XLSX.name, SAMPLE_XLSX.read_bytes())

    assert [s.name for s in inspection.sheets] == ["Transactions", "Departments"]


def test_inspect_rejects_oversized_upload(settings: Settings) -> None:
    small = settings.model_copy(update={"max_upload_mb": 0.001})
    service = DatasetService(small, DatasetRepository(small.metadata_db_path))

    with pytest.raises(UploadError, match="larger than"):
        service.inspect_upload(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())


def test_inspect_rejects_empty_upload(service: DatasetService) -> None:
    with pytest.raises(UploadError):
        service.inspect_upload("x.csv", b"")


def test_inspect_strips_path_from_filename(service: DatasetService, settings: Settings) -> None:
    inspection = service.inspect_upload("../../etc/evil.csv", b"a,b\n1,2\n")

    assert inspection.filename == "evil.csv"
    stored = list(settings.uploads_dir.rglob("*.csv"))
    assert len(stored) == 1
    assert stored[0].is_relative_to(settings.uploads_dir)


def test_corrupt_xlsx_is_rejected_at_inspection(service: DatasetService) -> None:
    with pytest.raises(UploadError):
        service.inspect_upload("broken.xlsx", b"PK\x03\x04garbage")


def test_create_rejects_unknown_or_malformed_upload_id(service: DatasetService) -> None:
    with pytest.raises(NotFoundError):
        service.create_dataset("0" * 32, None, None)
    with pytest.raises(NotFoundError):
        service.create_dataset("../../etc", None, None)


def test_create_rejects_unknown_sheet(service: DatasetService) -> None:
    inspection = service.inspect_upload(SAMPLE_XLSX.name, SAMPLE_XLSX.read_bytes())

    with pytest.raises(ValidationError):
        service.create_dataset(inspection.upload_id, "Nope", None)


def test_ingest_csv_into_duckdb(service: DatasetService) -> None:
    dataset_id = _ingest(service, SAMPLE_CSV)

    record = service.get(dataset_id)
    assert (record.status, record.row_count, record.column_count) == ("ready", 795, 7)
    with duckdb.connect(str(service.duckdb_path(dataset_id)), read_only=True) as conn:
        types = dict(conn.execute("SELECT column_name, data_type FROM duckdb_columns()").fetchall())
        total = conn.execute("SELECT SUM(pendapatan_rp) FROM dataset").fetchone()[0]
    assert types["tanggal_layanan"] == "DATE"
    assert types["id_klien"] == "VARCHAR"
    assert types["pendapatan_rp"] == "BIGINT"
    assert total == 29_412_000_000


def test_ingest_stores_columns_with_original_headers(service: DatasetService) -> None:
    dataset_id = _ingest(service, SAMPLE_CSV)

    columns = service.columns(dataset_id)
    assert [c.name for c in columns][:3] == ["id_klien", "nama_klien", "tanggal_layanan"]
    assert columns[0].original_name == "ID Klien"
    assert columns[2].logical_type == "date"


def test_csv_and_xlsx_produce_identical_tables(service: DatasetService) -> None:
    csv_id = _ingest(service, SAMPLE_CSV)
    xlsx_id = _ingest(service, SAMPLE_XLSX, "Transactions")

    query = "SELECT * FROM dataset ORDER BY ALL"
    with duckdb.connect(str(service.duckdb_path(csv_id)), read_only=True) as a:
        rows_a = a.execute(query).fetchall()
    with duckdb.connect(str(service.duckdb_path(xlsx_id)), read_only=True) as b:
        rows_b = b.execute(query).fetchall()
    assert rows_a == rows_b


def test_same_upload_can_create_one_dataset_per_sheet(service: DatasetService) -> None:
    inspection = service.inspect_upload(SAMPLE_XLSX.name, SAMPLE_XLSX.read_bytes())
    first = service.create_dataset(inspection.upload_id, "Transactions", None)
    second = service.create_dataset(inspection.upload_id, "Departments", "Depts")
    service.ingest(first.id, inspection.upload_id, "Transactions")
    service.ingest(second.id, inspection.upload_id, "Departments")

    assert service.get(first.id).name == "client_services — Transactions"
    assert (service.get(second.id).name, service.get(second.id).row_count) == ("Depts", 5)


def test_preview_returns_json_safe_rows(service: DatasetService) -> None:
    dataset_id = _ingest(service, SAMPLE_CSV)

    preview = service.preview(dataset_id, limit=5)

    assert preview.row_count == 5
    assert preview.columns[:3] == ["id_klien", "nama_klien", "tanggal_layanan"]
    assert isinstance(preview.rows[0][2], str)  # ISO date string
    assert preview.rows[0][2] == "2023-01-03"


def test_preview_requires_ready_dataset(service: DatasetService) -> None:
    inspection = service.inspect_upload(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())
    record = service.create_dataset(inspection.upload_id, None, None)

    with pytest.raises(ValidationError):
        service.preview(record.id, limit=5)


def test_unreadable_file_marks_dataset_failed(service: DatasetService) -> None:
    inspection = service.inspect_upload("empty.csv", b"\n\n\n")
    record = service.create_dataset(inspection.upload_id, None, None)

    service.ingest(record.id, inspection.upload_id, None)

    failed = service.get(record.id)
    assert failed.status == "failed"
    assert failed.error
    assert "/" not in failed.error  # no filesystem paths leak
    assert not service.duckdb_path(record.id).exists()


def test_delete_removes_file_and_metadata(service: DatasetService) -> None:
    dataset_id = _ingest(service, SAMPLE_CSV)
    path = service.duckdb_path(dataset_id)

    service.delete(dataset_id)

    assert not path.exists()
    with pytest.raises(NotFoundError):
        service.get(dataset_id)


def test_cleanup_removes_only_stale_uploads(service: DatasetService, settings: Settings) -> None:
    old = service.inspect_upload("old.csv", b"a\n1\n")
    new = service.inspect_upload("new.csv", b"a\n1\n")
    old_dir = settings.uploads_dir / old.upload_id
    stale = time.time() - 48 * 3600
    os.utime(old_dir, (stale, stale))

    removed = service.cleanup_stale_uploads(max_age_hours=24)

    assert removed == 1
    assert not old_dir.exists()
    assert (settings.uploads_dir / new.upload_id).exists()


def test_long_filename_keeps_extension(service: DatasetService) -> None:
    """L1: truncation must not drop the .csv suffix."""
    inspection = service.inspect_upload("x" * 200 + ".csv", b"a,b\n1,2\n")

    assert inspection.filename.endswith(".csv")
    assert len(inspection.filename) <= 120


def test_ingest_after_delete_does_not_raise(service: DatasetService) -> None:
    """L2: deleting a dataset while it is being ingested must not crash the task."""
    inspection = service.inspect_upload(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())
    record = service.create_dataset(inspection.upload_id, None, None)
    service.delete(record.id)

    service.ingest(record.id, inspection.upload_id, None)

    assert not service.duckdb_path(record.id).exists()


def test_interrupted_imports_are_marked_failed(service: DatasetService) -> None:
    """M3: datasets left in "processing" by a restart become failed at startup."""
    inspection = service.inspect_upload(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())
    record = service.create_dataset(inspection.upload_id, None, None)
    tmp_file = service.duckdb_path(record.id).with_suffix(".tmp.duckdb")
    tmp_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file.write_bytes(b"partial")

    count = service.recover_interrupted_imports()

    failed = service.get(record.id)
    assert count == 1
    assert failed.status == "failed"
    assert "interrupted" in (failed.error or "").lower()
    assert not tmp_file.exists()


def test_duplicate_headers_are_ingested_with_unique_names(service: DatasetService) -> None:
    inspection = service.inspect_upload("dup.csv", b"Nama,Nama,Nilai\nA,B,1\nC,D,2\n")
    record = service.create_dataset(inspection.upload_id, None, None)
    service.ingest(record.id, inspection.upload_id, None)

    columns = service.columns(record.id)

    assert service.get(record.id).status == "ready"
    assert [(c.name, c.original_name) for c in columns] == [
        ("nama", "Nama"),
        ("nama_2", "Nama"),
        ("nilai", "Nilai"),
    ]


# --- Phase 3: import options and notes ------------------------------------------


def _ingest_bytes(service: DatasetService, name: str, content: bytes, **options) -> str:
    inspection = service.inspect_upload(name, content)
    record = service.create_dataset(inspection.upload_id, None, None, **options)
    service.ingest(record.id, inspection.upload_id, None)
    return record.id


def _column_values(service: DatasetService, dataset_id: str, column: str) -> list:
    with duckdb.connect(str(service.duckdb_path(dataset_id)), read_only=True) as conn:
        return [r[0] for r in conn.execute(f"SELECT {column} FROM dataset").fetchall()]


def test_number_format_option_changes_interpretation(service: DatasetService) -> None:
    content = b"Nilai\n1.234\n5.678\n"
    auto_id = _ingest_bytes(service, "a.csv", content)
    en_id = _ingest_bytes(service, "b.csv", content, number_format="en")

    assert _column_values(service, auto_id, "nilai") == [1234, 5678]
    assert _column_values(service, en_id, "nilai") == [1.234, 5.678]
    assert service.get(en_id).number_format == "en"
    auto_notes = service.columns(auto_id)[0].notes
    assert [n.code for n in auto_notes] == ["ambiguous_number_format"]
    assert service.columns(en_id)[0].notes == []


def test_date_order_option(service: DatasetService) -> None:
    dataset_id = _ingest_bytes(
        service, "d.csv", b"Tanggal\n03/04/2026\n05/06/2026\n", date_order="month_first"
    )

    assert [str(v) for v in _column_values(service, dataset_id, "tanggal")] == [
        "2026-03-04",
        "2026-05-06",
    ]


def test_excel_imports_always_use_plain_numbers(service: DatasetService) -> None:
    inspection = service.inspect_upload(SAMPLE_XLSX.name, SAMPLE_XLSX.read_bytes())

    record = service.create_dataset(inspection.upload_id, "Transactions", None, number_format="id")

    assert record.number_format == "plain"


def test_unusual_dataset_ingests_with_notes(service: DatasetService) -> None:
    content = (
        b"NIK,Nama,Nama,Tanggal,Nilai,Catatan\n"
        b"9171234567890123,A,a,15/01/2026,10,\n"
        b"3201234567890127,B,b,31/02/2026,n/a?,\n"
        b"3201234567890127,B,b,31/02/2026,n/a?,\n"
        b",,,,,\n"
    )
    dataset_id = _ingest_bytes(service, "odd.csv", content)

    record = service.get(dataset_id)
    columns = {c.original_name + str(c.position): c for c in service.columns(dataset_id)}
    assert record.status == "ready"
    assert record.row_count == 3  # the fully empty row is dropped
    assert columns["NIK0"].logical_type == "text"
    assert [n.code for n in columns["NIK0"].notes] == ["long_identifier"]
    assert columns["Catatan5"].logical_type == "text"  # all values missing
