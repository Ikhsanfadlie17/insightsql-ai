"""Task 19: conversation management and recent activity."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from tests.api.test_mapping_api import SAMPLE_CSV, _dataset
from tests.llm_fakes import FakeLLMClient
from tests.services.test_orchestrator import insight_echo, plan

SMALL_CSV = b"ID Klien,Departemen,Tanggal Layanan\nKL-1,Audit,2026-01-05\nKL-2,Pajak,2026-02-07\n"


def _client(settings: Settings, answers: int) -> TestClient:
    fake = FakeLLMClient(responses=[r for _ in range(answers) for r in (plan(), insight_echo)])
    app = create_app(settings.model_copy(update={"openai_model": "fake-model"}), llm_client=fake)
    return TestClient(app)


def _ask(client: TestClient, dataset_id: str, question: str, conversation_id: str | None = None):
    body = {"dataset_id": dataset_id, "question": question, "conversation_id": conversation_id}
    response = client.post("/analyses/ask", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _analysis_rows(settings: Settings, conversation_id: str) -> int:
    with sqlite3.connect(settings.metadata_db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM analyses WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]


def test_listing_is_filtered_by_dataset(settings: Settings) -> None:
    with _client(settings, 3) as client:
        first = _dataset(client)
        second = _dataset(client, SAMPLE_CSV.read_bytes(), name="second.csv")
        a = _ask(client, first, "Klien per departemen?")
        _ask(client, first, "Lanjut: per tahun?", a["conversation_id"])
        b = _ask(client, second, "Klien per departemen?")

        only_first = client.get("/conversations", params={"dataset_id": first}).json()
        everything = client.get("/conversations").json()
        unknown = client.get("/conversations", params={"dataset_id": "nope"})

    assert [c["id"] for c in only_first] == [a["conversation_id"]]
    assert only_first[0]["analysis_count"] == 2
    assert only_first[0]["last_question"] == "Lanjut: per tahun?"
    assert only_first[0]["last_activity_at"] >= only_first[0]["created_at"]
    assert {c["id"] for c in everything} == {a["conversation_id"], b["conversation_id"]}
    assert unknown.status_code == 404


def test_delete_cascades_to_analyses(settings: Settings) -> None:
    with _client(settings, 2) as client:
        dataset_id = _dataset(client)
        a = _ask(client, dataset_id, "Klien per departemen?")
        _ask(client, dataset_id, "Lanjut?", a["conversation_id"])
        assert _analysis_rows(settings, a["conversation_id"]) == 2

        deleted = client.delete(f"/conversations/{a['conversation_id']}")
        again = client.delete(f"/conversations/{a['conversation_id']}")
        analysis = client.get(f"/analyses/{a['analysis_id']}")
        recent = client.get("/analyses/recent").json()

    assert deleted.status_code == 204 and again.status_code == 404
    assert _analysis_rows(settings, a["conversation_id"]) == 0
    assert analysis.status_code == 404
    assert recent == []


def test_deleting_a_dataset_removes_its_conversations(settings: Settings) -> None:
    with _client(settings, 2) as client:
        keep = _dataset(client)
        drop = _dataset(client, SMALL_CSV, name="small.csv")
        kept = _ask(client, keep, "Klien per departemen?")
        dropped = _ask(client, drop, "Klien per departemen?")

        client.delete(f"/datasets/{drop}")
        everything = client.get("/conversations").json()
        recent = client.get("/analyses/recent").json()

    assert [c["id"] for c in everything] == [kept["conversation_id"]]
    assert [r["analysis_id"] for r in recent] == [kept["analysis_id"]]
    assert _analysis_rows(settings, dropped["conversation_id"]) == 0


def test_recent_analyses_across_datasets(settings: Settings) -> None:
    with _client(settings, 7) as client:
        first = _dataset(client)
        second = client.post(
            "/datasets",
            json={
                "upload_id": client.post(
                    "/datasets/inspect", files={"file": ("b.csv", SAMPLE_CSV.read_bytes())}
                ).json()["upload_id"],
                "name": "Data kedua",
            },
        ).json()["id"]
        ids = [_ask(client, first, f"Pertanyaan {n}?")["analysis_id"] for n in range(5)]
        ids += [_ask(client, second, f"Pertanyaan {n}?")["analysis_id"] for n in range(5, 7)]

        recent = client.get("/analyses/recent").json()
        three = client.get("/analyses/recent", params={"limit": 3}).json()
        too_many = client.get("/analyses/recent", params={"limit": 1000})

    assert [r["analysis_id"] for r in recent] == ids[::-1][:5]
    assert recent[0]["dataset_name"] == "Data kedua" and recent[0]["question"] == "Pertanyaan 6?"
    assert recent[0]["status"] == "answered" and recent[0]["conversation_id"]
    assert len(three) == 3
    assert too_many.status_code == 422
    assert "response_json" not in recent[0] and "table" not in recent[0]


def test_get_one_analysis(settings: Settings) -> None:
    with _client(settings, 1) as client:
        dataset_id = _dataset(client)
        a = _ask(client, dataset_id, "Klien per departemen?")

        found = client.get(f"/analyses/{a['analysis_id']}")
        missing = client.get("/analyses/nope")

    assert found.status_code == 200 and found.json()["sql"] == a["sql"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
