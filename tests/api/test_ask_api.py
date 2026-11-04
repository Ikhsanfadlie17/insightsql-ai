"""Task 18: ask and conversation endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from tests.api.test_mapping_api import _dataset
from tests.llm_fakes import FakeLLMClient
from tests.services.test_orchestrator import insight_echo, plan


def _client(settings: Settings, *responses) -> tuple[TestClient, FakeLLMClient]:
    fake = FakeLLMClient(responses=list(responses))
    app = create_app(settings.model_copy(update={"openai_model": "fake-model"}), llm_client=fake)
    return TestClient(app), fake


def test_llm_not_configured(client: TestClient) -> None:
    dataset_id = _dataset(client)

    response = client.post("/analyses/ask", json={"dataset_id": dataset_id, "question": "x?"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
    assert client.get("/conversations", params={"dataset_id": dataset_id}).json() == []


def test_ask_and_read_the_conversation(settings: Settings) -> None:
    client, _ = _client(settings, plan(), insight_echo)
    with client:
        dataset_id = _dataset(client)
        client.put(f"/datasets/{dataset_id}/column-mapping",
                   json={"client_id": "id_klien", "transaction_date": "tanggal_layanan",
                         "department": "departemen"})  # fmt: skip

        answer = client.post(
            "/analyses/ask", json={"dataset_id": dataset_id, "question": "Klien per departemen?"}
        )
        body = answer.json()
        listed = client.get("/conversations", params={"dataset_id": dataset_id}).json()
        conversation = client.get(f"/conversations/{body['conversation_id']}").json()
        deleted = client.delete(f"/conversations/{body['conversation_id']}")
        missing = client.get(f"/conversations/{body['conversation_id']}")

    assert answer.status_code == 200 and body["status"] == "answered"
    assert body["table"]["rows"] and body["chart"]["type"] == "bar"
    assert body["usage"]["requests"] == 2
    assert [c["id"] for c in listed] == [body["conversation_id"]]
    assert listed[0]["analysis_count"] == 1
    assert conversation["analyses"][0]["analysis_id"] == body["analysis_id"]
    assert deleted.status_code == 204 and missing.status_code == 404


def test_ask_rejects_bad_requests(settings: Settings) -> None:
    client, fake = _client(settings)
    with client:
        dataset_id = _dataset(client)
        empty = client.post("/analyses/ask", json={"dataset_id": dataset_id, "question": "  "})
        extra = client.post(
            "/analyses/ask", json={"dataset_id": dataset_id, "question": "x", "sql": "DROP"}
        )
        unknown = client.post("/analyses/ask", json={"dataset_id": "nope", "question": "x"})
        bad_conversation = client.post(
            "/analyses/ask",
            json={"dataset_id": dataset_id, "conversation_id": "nope", "question": "x"},
        )

    assert empty.status_code == 422 and extra.status_code == 422
    assert unknown.status_code == 404 and bad_conversation.status_code == 404
    assert fake.calls == []


def test_deleting_a_dataset_deletes_its_conversations(settings: Settings) -> None:
    client, _ = _client(settings, plan(), insight_echo)
    with client:
        dataset_id = _dataset(client)
        body = client.post(
            "/analyses/ask", json={"dataset_id": dataset_id, "question": "Klien per departemen?"}
        ).json()
        client.delete(f"/datasets/{dataset_id}")

        assert client.get(f"/conversations/{body['conversation_id']}").status_code == 404
