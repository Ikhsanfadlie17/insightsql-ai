"""Task 5: dataset REST API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from tests.conftest import SAMPLE_DATA_DIR

SAMPLE_CSV = SAMPLE_DATA_DIR / "client_services.csv"
SAMPLE_XLSX = SAMPLE_DATA_DIR / "client_services.xlsx"


def _upload(client: TestClient, path=SAMPLE_CSV) -> dict:
    with path.open("rb") as handle:
        response = client.post("/datasets/inspect", files={"file": (path.name, handle)})
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_inspect_create_and_get(client: TestClient) -> None:
    inspection = _upload(client)

    created = client.post("/datasets", json={"upload_id": inspection["upload_id"]})
    dataset_id = created.json()["id"]
    fetched = client.get(f"/datasets/{dataset_id}").json()

    assert created.status_code == 201
    assert created.json()["status"] == "processing"
    # TestClient runs background tasks before returning, so ingestion is done.
    assert (fetched["status"], fetched["row_count"], fetched["column_count"]) == ("ready", 795, 7)


def test_xlsx_sheet_selection(client: TestClient) -> None:
    inspection = _upload(client, SAMPLE_XLSX)

    created = client.post(
        "/datasets", json={"upload_id": inspection["upload_id"], "sheet": "Departments"}
    ).json()

    assert [s["name"] for s in inspection["sheets"]] == ["Transactions", "Departments"]
    assert client.get(f"/datasets/{created['id']}").json()["row_count"] == 5


def test_list_datasets(client: TestClient) -> None:
    inspection = _upload(client)
    client.post("/datasets", json={"upload_id": inspection["upload_id"], "name": "A"})
    client.post("/datasets", json={"upload_id": inspection["upload_id"], "name": "B"})

    names = [d["name"] for d in client.get("/datasets").json()]

    assert names == ["B", "A"]


def test_oversized_upload_is_rejected(settings: Settings) -> None:
    small = settings.model_copy(update={"max_upload_mb": 0.001})
    with TestClient(create_app(small)) as client, SAMPLE_CSV.open("rb") as handle:
        response = client.post("/datasets/inspect", files={"file": ("x.csv", handle)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upload_error"


def test_unsupported_file_is_rejected(client: TestClient) -> None:
    response = client.post("/datasets/inspect", files={"file": ("x.exe", b"MZ...")})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upload_error"


def test_preview_limits_rows(client: TestClient) -> None:
    inspection = _upload(client)
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    preview = client.get(f"/datasets/{dataset_id}/preview", params={"limit": 5}).json()
    too_many = client.get(f"/datasets/{dataset_id}/preview", params={"limit": 1000})

    assert preview["row_count"] == 5 and len(preview["rows"]) == 5
    assert preview["columns"][0] == "id_klien"
    assert too_many.status_code == 422


def test_columns_endpoint(client: TestClient) -> None:
    inspection = _upload(client)
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    columns = client.get(f"/datasets/{dataset_id}/columns").json()

    assert columns[0] == {
        "position": 0,
        "name": "id_klien",
        "original_name": "ID Klien",
        "logical_type": "text",
        "failed_conversions": 0,
        "notes": [],
    }


def test_rename_dataset(client: TestClient) -> None:
    inspection = _upload(client)
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    response = client.patch(f"/datasets/{dataset_id}", json={"name": "  Client services 2026 "})

    assert response.status_code == 200
    assert response.json()["name"] == "Client services 2026"
    assert client.patch(f"/datasets/{dataset_id}", json={"name": "   "}).status_code == 422


def test_delete_dataset(client: TestClient) -> None:
    inspection = _upload(client)
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    deleted = client.delete(f"/datasets/{dataset_id}")

    assert deleted.status_code == 204
    assert client.get(f"/datasets/{dataset_id}").status_code == 404
    assert client.delete(f"/datasets/{dataset_id}").status_code == 404


def test_failed_ingestion_is_reported(client: TestClient) -> None:
    inspection = client.post("/datasets/inspect", files={"file": ("e.csv", b"\n\n")}).json()
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    record = client.get(f"/datasets/{dataset_id}").json()

    assert record["status"] == "failed"
    assert record["error"]


def test_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.get("/datasets/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_inspect_endpoint_is_sync() -> None:
    """M2: parsing large workbooks must not block the event loop."""
    import inspect

    from backend.api.datasets import inspect_upload

    assert not inspect.iscoroutinefunction(inspect_upload)


def test_create_with_import_options(client: TestClient) -> None:
    inspection = client.post(
        "/datasets/inspect", files={"file": ("n.csv", b"Nilai\n1.234\n5.678\n")}
    ).json()

    created = client.post(
        "/datasets",
        json={"upload_id": inspection["upload_id"], "number_format": "en", "date_order": "auto"},
    ).json()
    columns = client.get(f"/datasets/{created['id']}/columns").json()

    assert created["number_format"] == "en"
    assert columns[0]["logical_type"] == "numeric"
    assert columns[0]["notes"] == []


def test_invalid_import_option_is_rejected(client: TestClient) -> None:
    inspection = _upload(client)

    response = client.post(
        "/datasets", json={"upload_id": inspection["upload_id"], "number_format": "fr"}
    )

    assert response.status_code == 422


def test_profile_endpoint(client: TestClient) -> None:
    inspection = _upload(client)
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    profile = client.get(f"/datasets/{dataset_id}/profile")

    assert profile.status_code == 200
    body = profile.json()
    assert body["row_count"] == 795
    assert {c["name"] for c in body["columns"]} >= {"id_klien", "tanggal_layanan"}


def test_profile_of_failed_dataset_is_rejected(client: TestClient) -> None:
    inspection = client.post("/datasets/inspect", files={"file": ("e.csv", b"\n\n")}).json()
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    response = client.get(f"/datasets/{dataset_id}/profile")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
