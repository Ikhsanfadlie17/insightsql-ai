"""Task 8: heuristic column-role suggestions (suggestions only, never auto-applied)."""

from __future__ import annotations

import pytest

from backend.core.config import Settings
from backend.repositories.dataset_repo import DatasetRepository
from backend.services.column_roles import draft_mapping, suggest_roles
from backend.services.dataset_service import DatasetService
from tests.conftest import SAMPLE_DATA_DIR


@pytest.fixture
def service(settings: Settings) -> DatasetService:
    return DatasetService(settings, DatasetRepository(settings.metadata_db_path))


def _profile(service: DatasetService, content: bytes):
    inspection = service.inspect_upload("f.csv", content)
    record = service.create_dataset(inspection.upload_id, None, None)
    service.ingest(record.id, inspection.upload_id, None)
    return service.profile(record.id)


def _best(suggestions, role: str) -> str | None:
    entry = next(s for s in suggestions if s.role == role)
    return entry.candidates[0].column if entry.candidates else None


def test_sample_dataset_suggestions(service: DatasetService) -> None:
    profile = _profile(service, (SAMPLE_DATA_DIR / "client_services.csv").read_bytes())

    draft = draft_mapping(suggest_roles(profile))

    assert draft.model_dump(exclude={"confirmed", "confirmed_at"}) == {
        "client_id": "id_klien",
        "client_name": "nama_klien",
        "transaction_date": "tanggal_layanan",
        "department": "departemen",
        "service_type": "jenis_layanan",
        "revenue": "pendapatan_rp",
        "status": "status",
    }
    assert draft.confirmed is False


def test_english_headers(service: DatasetService) -> None:
    content = (
        b"Customer ID,Customer Name,Order Date,Department,Product,Amount,Status\n"
        b"C1,Acme,2026-01-02,Sales,Widget,100,Paid\n"
        b"C2,Beta,2026-01-03,Sales,Gadget,200,Open\n"
        b"C1,Acme,2026-02-02,Ops,Widget,150,Paid\n"
    )
    draft = draft_mapping(suggest_roles(_profile(service, content)))

    assert (draft.client_id, draft.client_name, draft.transaction_date) == (
        "customer_id",
        "customer_name",
        "order_date",
    )
    assert (draft.department, draft.service_type, draft.revenue, draft.status) == (
        "department",
        "product",
        "amount",
        "status",
    )


def test_every_candidate_explains_itself(service: DatasetService) -> None:
    profile = _profile(service, (SAMPLE_DATA_DIR / "client_services.csv").read_bytes())

    for entry in suggest_roles(profile):
        assert len(entry.candidates) <= 3
        for candidate in entry.candidates:
            assert 0 < candidate.confidence <= 1
            assert candidate.reasons
            assert candidate.original_name


def test_no_guess_without_evidence(service: DatasetService) -> None:
    profile = _profile(service, b"A,B,C\nx1,10,p\nx2,20,q\nx1,30,p\n")

    draft = draft_mapping(suggest_roles(profile))

    assert draft.client_id is None
    assert draft.client_name is None
    assert draft.revenue is None


def test_unnamed_date_column_is_a_low_confidence_candidate(service: DatasetService) -> None:
    profile = _profile(service, b"Kolom X,Nilai\n2026-01-01,1\n2026-02-01,2\n")

    entry = next(s for s in suggest_roles(profile) if s.role == "transaction_date")

    assert entry.candidates[0].column == "kolom_x"
    assert entry.candidates[0].confidence < 0.5


def test_birth_date_is_not_preferred_as_transaction_date(service: DatasetService) -> None:
    content = (
        b"Tanggal Lahir,Tanggal Transaksi,ID Pelanggan\n"
        b"1990-01-01,2026-01-05,P1\n1985-05-05,2026-02-05,P2\n"
    )
    suggestions = suggest_roles(_profile(service, content))

    assert _best(suggestions, "transaction_date") == "tanggal_transaksi"


def test_type_gate_excludes_incompatible_columns(service: DatasetService) -> None:
    content = b"Tanggal,Total,Kode Klien\nbesok,abc,K1\nlusa,def,K2\n"
    suggestions = suggest_roles(_profile(service, content))

    assert _best(suggestions, "transaction_date") is None  # text, not dates
    assert _best(suggestions, "revenue") is None  # text, not numbers
    assert _best(suggestions, "client_id") == "kode_klien"


def test_draft_never_uses_a_column_twice(service: DatasetService) -> None:
    content = b"Layanan\nAudit\nPajak\nAudit\n"
    draft = draft_mapping(suggest_roles(_profile(service, content)))

    used = [v for v in draft.model_dump(exclude={"confirmed", "confirmed_at"}).values() if v]
    assert len(used) == len(set(used))


def test_high_cardinality_text_is_not_a_department(service: DatasetService) -> None:
    rows = "\n".join(f"Unit {i}" for i in range(120))
    profile = _profile(service, f"Unit\n{rows}\n".encode())

    entry = next(s for s in suggest_roles(profile) if s.role == "department")
    assert not entry.candidates or entry.candidates[0].confidence < 0.5
