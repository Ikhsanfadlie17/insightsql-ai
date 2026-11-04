"""Task 20: export endpoints for AI Analyst answers and client analytics."""

from __future__ import annotations

import io

import openpyxl
import pandas as pd
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from tests.api.test_mapping_api import _dataset
from tests.llm_fakes import FakeLLMClient
from tests.services.test_orchestrator import insight_echo, plan

MAPPING = {
    "client_id": "id_klien",
    "transaction_date": "tanggal_layanan",
    "department": "departemen",
}
PERIODS = {
    "previous": {"label": "Tahun 2025", "start": "2025-01-01", "end": "2026-01-01"},
    "current": {"label": "Tahun 2026", "start": "2026-01-01", "end": "2027-01-01"},
}


def _client(settings: Settings, *responses) -> TestClient:
    fake = FakeLLMClient(responses=list(responses))
    app = create_app(settings.model_copy(update={"openai_model": "fake-model"}), llm_client=fake)
    return TestClient(app)


def _answered(client: TestClient) -> tuple[str, dict]:
    dataset_id = _dataset(client)
    client.put(f"/datasets/{dataset_id}/column-mapping", json=MAPPING)
    body = client.post(
        "/analyses/ask", json={"dataset_id": dataset_id, "question": "Klien per departemen?"}
    ).json()
    assert body["status"] == "answered"
    return dataset_id, body


def test_export_an_answer_in_every_format(settings: Settings) -> None:
    with _client(settings, plan(), insight_echo) as client:
        _, answer = _answered(client)
        url = f"/analyses/{answer['analysis_id']}/export"
        csv = client.get(url, params={"format": "csv"})
        xlsx = client.get(url, params={"format": "xlsx"})
        pdf = client.get(url, params={"format": "pdf"})
        sql = client.get(url, params={"format": "sql"})

    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.headers["content-disposition"] == 'attachment; filename="klien-per-departemen.csv"'
    frame = pd.read_csv(io.BytesIO(csv.content), encoding="utf-8-sig")
    assert list(frame.columns) == answer["table"]["columns"]
    assert frame.values.tolist() == answer["table"]["rows"]

    book = openpyxl.load_workbook(io.BytesIO(xlsx.content))
    assert book.sheetnames == ["Result", "Metadata"]
    metadata = [c.value for row in book["Metadata"].iter_rows() for c in row]
    assert "Klien per departemen?" in metadata and answer["sql"] in metadata

    assert pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    assert "-- Question: Klien per departemen?" in sql.text and answer["sql"] in sql.text


def test_export_errors(settings: Settings) -> None:
    clarify = plan("clarify", clarification_question="Periode mana?", chart=None)
    with _client(settings, clarify) as client:
        dataset_id = _dataset(client)
        question = client.post(
            "/analyses/ask", json={"dataset_id": dataset_id, "question": "Klien?"}
        ).json()
        url = f"/analyses/{question['analysis_id']}/export"
        no_table = client.get(url, params={"format": "csv"})
        no_sql = client.get(url, params={"format": "sql"})
        bad_format = client.get(url, params={"format": "docx"})
        unknown = client.get("/analyses/nope/export", params={"format": "csv"})

    assert question["status"] == "clarification"
    assert no_table.status_code == 409 and no_table.json()["error"]["code"] == "nothing_to_export"
    assert no_sql.status_code == 409
    assert bad_format.status_code == 422
    assert unknown.status_code == 404


def test_export_client_analytics_with_periods_and_definitions(settings: Settings) -> None:
    with _client(settings) as client:
        dataset_id = _dataset(client)
        client.put(f"/datasets/{dataset_id}/column-mapping", json=MAPPING)
        compare = client.post(f"/datasets/{dataset_id}/client-analytics/compare", json=PERIODS)
        url = f"/datasets/{dataset_id}/client-analytics/export"
        xlsx = client.post(url, params={"format": "xlsx"}, json=PERIODS)
        csv = client.post(url, params={"format": "csv"}, json=PERIODS)
        pdf = client.post(url, params={"format": "pdf"}, json=PERIODS)
        sql = client.post(url, params={"format": "sql"}, json=PERIODS)

    r = compare.json()
    book = openpyxl.load_workbook(io.BytesIO(xlsx.content))
    result = {row[0].value: row for row in book["Result"].iter_rows(min_row=2)}
    assert result["Active clients"][1].value == r["previous_active"]
    assert result["Active clients"][2].value == r["current_active"]
    metadata = [str(c.value) for row in book["Metadata"].iter_rows() for c in row]
    assert any("Tahun 2025: 2025-01-01 to 2025-12-31" in v for v in metadata)
    assert r["definitions"]["retention_rate"] in metadata
    assert xlsx.headers["content-disposition"] == (
        'attachment; filename="client-analytics-tahun-2025-vs-tahun-2026.xlsx"'
    )
    assert csv.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "-- Period: Tahun 2026: 2026-01-01 to 2026-12-31" in sql.text


def test_export_client_analytics_needs_a_mapping(settings: Settings) -> None:
    with _client(settings) as client:
        dataset_id = _dataset(client)
        response = client.post(
            f"/datasets/{dataset_id}/client-analytics/export",
            params={"format": "csv"},
            json=PERIODS,
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mapping_required"
