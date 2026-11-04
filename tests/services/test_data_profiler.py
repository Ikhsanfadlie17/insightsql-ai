"""Task 7: dataset profiling computed in DuckDB."""

from __future__ import annotations

import pytest

from backend.core.config import Settings
from backend.core.errors import ValidationError
from backend.repositories.dataset_repo import DatasetRepository
from backend.schemas.profile import DatasetProfile
from backend.services.dataset_service import DatasetService
from tests.conftest import SAMPLE_DATA_DIR

FIXTURE = (
    b"ID,Tanggal,Nilai,Kategori,Kosong\n"
    b"A1,15/01/2026,10,x,\n"
    b"A1,15/01/2026,10,x,\n"
    b"A2,16/01/2026,20,y,\n"
    b"A3,,30,,\n"
    b"A4,17/01/2026,,y,\n"
)


@pytest.fixture
def service(settings: Settings) -> DatasetService:
    return DatasetService(settings, DatasetRepository(settings.metadata_db_path))


def _ingest(service: DatasetService, content: bytes, name: str = "f.csv") -> str:
    inspection = service.inspect_upload(name, content)
    record = service.create_dataset(inspection.upload_id, None, None)
    service.ingest(record.id, inspection.upload_id, None)
    assert service.get(record.id).status == "ready"
    return record.id


@pytest.fixture
def profile(service: DatasetService) -> DatasetProfile:
    return service.profile(_ingest(service, FIXTURE))


def _col(profile: DatasetProfile, name: str):
    return next(c for c in profile.columns if c.name == name)


def test_dataset_level_counts(profile: DatasetProfile) -> None:
    assert (profile.row_count, profile.column_count) == (5, 5)
    assert profile.duplicate_row_count == 1
    assert profile.missing_cells == 8  # Tanggal 1 + Nilai 1 + Kategori 1 + Kosong 5
    assert profile.missing_pct == pytest.approx(8 / 25 * 100)


def test_identifier_column(profile: DatasetProfile) -> None:
    column = _col(profile, "id")

    assert (column.original_name, column.logical_type) == ("ID", "text")
    assert (column.null_count, column.unique_count) == (0, 4)
    assert column.top_values[0].value == "A1" and column.top_values[0].count == 2


def test_numeric_statistics(profile: DatasetProfile) -> None:
    column = _col(profile, "nilai")

    assert column.null_count == 1
    assert (column.min, column.max) == (10, 30)
    assert column.mean == pytest.approx(17.5)
    assert column.median == pytest.approx(15.0)
    assert column.std == pytest.approx(9.574271, rel=1e-5)


def test_date_range(profile: DatasetProfile) -> None:
    column = _col(profile, "tanggal")

    assert column.logical_type == "date"
    assert (column.date_min, column.date_max) == ("2026-01-15", "2026-01-17")
    assert column.null_count == 1
    assert column.mean is None


def test_top_values_are_sorted_and_bounded(profile: DatasetProfile) -> None:
    column = _col(profile, "kategori")

    assert [(v.value, v.count) for v in column.top_values] == [("x", 2), ("y", 2)]
    assert column.null_pct == pytest.approx(20.0)


def test_all_missing_column(profile: DatasetProfile) -> None:
    column = _col(profile, "kosong")

    assert (column.null_count, column.unique_count, column.top_values) == (5, 0, [])
    assert any(n.code == "empty_column" for n in profile.notes)


def test_duplicate_rows_are_reported(profile: DatasetProfile) -> None:
    note = next(n for n in profile.notes if n.code == "duplicate_rows")

    assert "1 row" in note.message


def test_column_notes_are_carried_into_profile(service: DatasetService) -> None:
    profile = service.profile(_ingest(service, b"Nilai\n1.234\n5.678\n"))

    assert [n.code for n in profile.columns[0].notes] == ["ambiguous_number_format"]


def test_long_top_values_are_truncated(service: DatasetService) -> None:
    long_text = "x" * 500
    profile = service.profile(_ingest(service, f"Catatan\n{long_text}\nb\n".encode()))

    assert all(len(v.value) <= 81 for v in profile.columns[0].top_values)


def test_missing_headers_hint(service: DatasetService) -> None:
    content = b"Laporan Klien 2026,,,\n,,,\nA,1,2,3\nB,4,5,6\n"
    profile = service.profile(_ingest(service, content))

    assert any(n.code == "missing_headers" for n in profile.notes)


def test_no_date_column_hint(service: DatasetService) -> None:
    profile = service.profile(_ingest(service, b"A,B\n1,x\n2,y\n"))

    assert any(n.code == "no_date_column" for n in profile.notes)


def test_single_row_dataset(service: DatasetService) -> None:
    profile = service.profile(_ingest(service, b"A,B\n1,x\n"))

    assert profile.row_count == 1 and profile.duplicate_row_count == 0
    assert profile.columns[0].std is None  # undefined for one value


def test_wide_dataset(service: DatasetService) -> None:
    header = ",".join(f"Kolom {i}" for i in range(150))
    rows = "\n".join(",".join(str(r * i) for i in range(150)) for r in range(20))
    profile = service.profile(_ingest(service, f"{header}\n{rows}\n".encode()))

    assert profile.column_count == 150
    assert len(profile.columns) == 150


def test_sample_dataset_profile(service: DatasetService) -> None:
    content = (SAMPLE_DATA_DIR / "client_services.csv").read_bytes()
    profile = service.profile(_ingest(service, content, "client_services.csv"))

    client = _col(profile, "id_klien")
    revenue = _col(profile, "pendapatan_rp")
    date_column = _col(profile, "tanggal_layanan")
    assert profile.row_count == 795 and profile.duplicate_row_count == 15
    assert (client.null_count, client.unique_count) == (7, 150)
    assert revenue.null_count == 26
    assert (date_column.date_min, date_column.date_max) == ("2023-01-03", "2026-10-31")


def test_profile_is_stored_at_ingest_and_recomputed_if_missing(
    service: DatasetService, settings: Settings
) -> None:
    from backend.repositories.db import connect

    dataset_id = _ingest(service, FIXTURE)
    repo = DatasetRepository(settings.metadata_db_path)
    assert repo.get_document(dataset_id, "profile", DatasetProfile) is not None

    with connect(settings.metadata_db_path) as conn:
        conn.execute("DELETE FROM dataset_profiles")

    assert service.profile(dataset_id).row_count == 5
    assert repo.get_document(dataset_id, "profile", DatasetProfile) is not None


def test_profile_requires_ready_dataset(service: DatasetService) -> None:
    inspection = service.inspect_upload("f.csv", FIXTURE)
    record = service.create_dataset(inspection.upload_id, None, None)

    with pytest.raises(ValidationError):
        service.profile(record.id)


def test_duplicate_note_makes_no_promise_about_later_analytics(profile: DatasetProfile) -> None:
    """Review L1: the note must not claim behaviour of analytics that do not exist yet."""
    note = next(n for n in profile.notes if n.code == "duplicate_rows")

    assert "only affect" not in note.message
