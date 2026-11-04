"""Task 8: column mapping is confirmed by the user before analytics may use it."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import SAMPLE_DATA_DIR

SAMPLE_CSV = SAMPLE_DATA_DIR / "client_services.csv"
FULL = {
    "client_id": "id_klien",
    "client_name": "nama_klien",
    "transaction_date": "tanggal_layanan",
    "department": "departemen",
    "service_type": "jenis_layanan",
    "revenue": "pendapatan_rp",
    "status": "status",
}


def _dataset(client: TestClient, content: bytes | None = None, name: str = "x.csv") -> str:
    data = content if content is not None else SAMPLE_CSV.read_bytes()
    inspection = client.post("/datasets/inspect", files={"file": (name, data)}).json()
    return client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]


def _codes(state: dict) -> set[str]:
    return {w["code"] for w in state["warnings"]}


def test_suggestions_are_not_saved_until_confirmed(client: TestClient) -> None:
    dataset_id = _dataset(client)

    state = client.get(f"/datasets/{dataset_id}/column-mapping").json()

    assert state["mapping"] is None
    assert state["draft"]["client_id"] == "id_klien"
    assert state["draft"]["confirmed"] is False
    assert state["unique_clients"] is None
    assert {s["role"] for s in state["suggestions"]} == set(FULL)


def test_confirm_mapping(client: TestClient) -> None:
    dataset_id = _dataset(client)

    saved = client.put(f"/datasets/{dataset_id}/column-mapping", json=FULL)
    state = client.get(f"/datasets/{dataset_id}/column-mapping").json()

    assert saved.status_code == 200
    assert state["mapping"]["confirmed"] is True
    assert state["mapping"]["confirmed_at"]
    assert state["mapping"]["client_id"] == "id_klien"
    assert state["unique_clients"] == 150  # unique IDs, not 795 transactions


def test_user_can_override_a_suggestion(client: TestClient) -> None:
    dataset_id = _dataset(client)

    body = {**FULL, "department": "jenis_layanan", "service_type": "departemen"}
    state = client.put(f"/datasets/{dataset_id}/column-mapping", json=body).json()

    assert (state["mapping"]["department"], state["mapping"]["service_type"]) == (
        "jenis_layanan",
        "departemen",
    )


def test_unknown_column_is_rejected(client: TestClient) -> None:
    dataset_id = _dataset(client)

    response = client.put(f"/datasets/{dataset_id}/column-mapping", json={"client_id": "nope"})

    assert response.status_code == 422
    assert "nope" in response.json()["error"]["message"]


def test_incompatible_type_is_rejected(client: TestClient) -> None:
    dataset_id = _dataset(client)

    response = client.put(
        f"/datasets/{dataset_id}/column-mapping", json={"transaction_date": "nama_klien"}
    )

    assert response.status_code == 422
    assert "date" in response.json()["error"]["message"].lower()


def test_same_column_for_two_roles_is_rejected(client: TestClient) -> None:
    dataset_id = _dataset(client)

    response = client.put(
        f"/datasets/{dataset_id}/column-mapping",
        json={"client_id": "id_klien", "client_name": "id_klien"},
    )

    assert response.status_code == 422


def test_unknown_role_is_rejected(client: TestClient) -> None:
    dataset_id = _dataset(client)

    response = client.put(f"/datasets/{dataset_id}/column-mapping", json={"vip": "id_klien"})

    assert response.status_code == 422


def test_confirmed_mapping_warnings(client: TestClient) -> None:
    dataset_id = _dataset(client)

    state = client.put(f"/datasets/{dataset_id}/column-mapping", json=FULL).json()
    by_code = {w["code"]: w for w in state["warnings"]}

    assert "7 row" in by_code["missing_client_ids"]["message"]
    assert "8 client ID" in by_code["inconsistent_client_names"]["message"]
    history = by_code["history_completeness"]
    assert history["level"] == "warning"
    assert "2023-01-03" in history["message"]
    assert "New" in history["message"] and "Reactivated" in history["message"]


def test_partial_mapping_explains_what_is_missing(client: TestClient) -> None:
    dataset_id = _dataset(client)

    state = client.put(
        f"/datasets/{dataset_id}/column-mapping", json={"client_id": "id_klien"}
    ).json()

    assert state["mapping"]["transaction_date"] is None
    assert "no_transaction_date" in _codes(state)


def test_name_only_identity(client: TestClient) -> None:
    content = b"Nama Klien,Tanggal\nPT A,2026-01-01\n pt  a ,2026-02-01\nPT B,2026-03-01\n"
    dataset_id = _dataset(client, content)

    state = client.put(
        f"/datasets/{dataset_id}/column-mapping",
        json={"client_name": "nama_klien", "transaction_date": "tanggal"},
    ).json()

    assert "name_identity" in _codes(state)
    assert state["unique_clients"] == 2  # "PT A" and " pt  a " are one normalized name


def test_long_numeric_id_warning_is_surfaced(client: TestClient) -> None:
    content = b"NIK,Tanggal\n9171234567890124,2026-01-01\n3201234567890127,2026-02-01\n"
    dataset_id = _dataset(client, content)

    state = client.put(f"/datasets/{dataset_id}/column-mapping", json={"client_id": "nik"}).json()

    assert "client_id_long_identifier" in _codes(state)


def test_mapping_requires_ready_dataset(client: TestClient) -> None:
    inspection = client.post("/datasets/inspect", files={"file": ("e.csv", b"\n\n")}).json()
    dataset_id = client.post("/datasets", json={"upload_id": inspection["upload_id"]}).json()["id"]

    assert client.get(f"/datasets/{dataset_id}/column-mapping").status_code == 422
    assert (
        client.put(f"/datasets/{dataset_id}/column-mapping", json={"client_id": "x"}).status_code
        == 422
    )


def test_compatible_columns_per_role(client: TestClient) -> None:
    dataset_id = _dataset(client)

    compatible = client.get(f"/datasets/{dataset_id}/column-mapping").json()["compatible_columns"]

    assert compatible["transaction_date"] == ["tanggal_layanan"]
    assert compatible["revenue"] == ["pendapatan_rp"]
    assert "tanggal_layanan" not in compatible["client_id"]
    assert set(compatible) == set(FULL)


def test_all_empty_column_cannot_be_confirmed(client: TestClient) -> None:
    """Review L4: server validation must match the compatible-column list."""
    dataset_id = _dataset(client, b"Kode Klien,Kosong,Tanggal\nK1,,2026-01-01\nK2,,2026-02-01\n")

    response = client.put(f"/datasets/{dataset_id}/column-mapping", json={"client_id": "kosong"})

    assert response.status_code == 422
    assert "no values" in response.json()["error"]["message"]


def test_client_id_with_unconverted_values_warns(client: TestClient) -> None:
    """Review H2: IDs lost during number conversion must be called out at confirmation."""
    rows = "\n".join(f"{1000 + i},2026-01-0{1 + i % 9}" for i in range(60))
    content = f"Pelanggan,Tanggal\n{rows}\nKL-77,2026-03-01\n".encode()
    dataset_id = _dataset(client, content)

    state = client.put(
        f"/datasets/{dataset_id}/column-mapping", json={"client_id": "pelanggan"}
    ).json()

    warning = next(w for w in state["warnings"] if w["code"] == "client_id_unconverted")
    assert "Keep as text" in warning["message"]
