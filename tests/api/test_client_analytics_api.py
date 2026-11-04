"""Task 14: client-analytics endpoints (deterministic, mapping required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.test_mapping_api import FULL, _dataset

Y2025 = {"label": "2025", "start": "2025-01-01", "end": "2026-01-01"}
Y2026 = {"label": "2026", "start": "2026-01-01", "end": "2027-01-01"}
BASE = "/datasets/{}/client-analytics"


def _confirmed(client: TestClient, mapping: dict | None = None) -> str:
    dataset_id = _dataset(client)
    assert client.put(f"/datasets/{dataset_id}/column-mapping", json=mapping or FULL).is_success
    return dataset_id


def test_requires_a_confirmed_mapping(client: TestClient) -> None:
    dataset_id = _dataset(client)

    for response in (
        client.get(BASE.format(dataset_id) + "/coverage"),
        client.post(BASE.format(dataset_id) + "/compare", json={"previous": Y2025,
                                                               "current": Y2026}),
        client.get(BASE.format(dataset_id) + "/trend"),
    ):  # fmt: skip
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "mapping_required"


def test_mapping_without_a_date_is_not_enough(client: TestClient) -> None:
    dataset_id = _confirmed(client, {"client_id": "id_klien"})

    response = client.get(BASE.format(dataset_id) + "/coverage")

    assert response.status_code == 409
    assert "date" in response.json()["error"]["message"].lower()


def test_unknown_dataset(client: TestClient) -> None:
    assert client.get(BASE.format("nope") + "/coverage").status_code == 404


def test_compare(client: TestClient) -> None:
    dataset_id = _confirmed(client)

    response = client.post(
        BASE.format(dataset_id) + "/compare", json={"previous": Y2025, "current": Y2026}
    )

    assert response.status_code == 200
    r = response.json()
    assert r["new"] + r["returning"] + r["reactivated"] + r["between_period"] == r["current_active"]
    assert r["returning"] + r["not_returned"] == r["previous_active"]
    assert r["existing_active"] == r["returning"] + r["reactivated"] + r["between_period"]
    assert r["identity_column"] == "id_klien"
    assert r["definitions"]["retention_rate"]
    assert {w["code"] for w in r["warnings"]} >= {"history_completeness"}
    assert r["sql"] and "dataset" in r["sql"][0]


def test_compare_is_repeatable(client: TestClient) -> None:
    dataset_id = _confirmed(client)
    body = {"previous": Y2025, "current": Y2026}

    first = client.post(BASE.format(dataset_id) + "/compare", json=body).json()
    second = client.post(BASE.format(dataset_id) + "/compare", json=body).json()

    assert first == second


def test_compare_rejects_bad_periods(client: TestClient) -> None:
    dataset_id = _confirmed(client)
    url = BASE.format(dataset_id) + "/compare"

    overlapping = client.post(url, json={"previous": Y2026, "current": Y2025})
    empty = client.post(
        url,
        json={"previous": {"label": "x", "start": "2025-01-01", "end": "2025-01-01"},
              "current": Y2026},
    )  # fmt: skip
    extra = client.post(url, json={"previous": Y2025, "current": Y2026, "sql": "DROP"})

    assert overlapping.status_code == 422
    assert overlapping.json()["error"]["code"] == "validation_error"
    assert empty.status_code == 422
    assert extra.status_code == 422


def test_client_list_and_pagination(client: TestClient) -> None:
    dataset_id = _confirmed(client)
    url = BASE.format(dataset_id) + "/clients"
    body = {"previous": Y2025, "current": Y2026, "category": "existing_active"}

    compare = client.post(
        BASE.format(dataset_id) + "/compare", json={"previous": Y2025, "current": Y2026}
    ).json()
    page = client.post(url, json={**body, "limit": 10}).json()
    rest = client.post(url, json={**body, "offset": 10, "limit": 500}).json()

    assert page["total"] == compare["existing_active"]
    assert page["result"]["row_count"] == 10 and page["result"]["truncated"] is True
    assert page["result"]["columns"][:2] == ["client", "client_name"]
    ids = [r[0] for r in page["result"]["rows"] + rest["result"]["rows"]]
    assert len(ids) == len(set(ids)) == compare["existing_active"]


def test_client_list_rejects_unknown_category_and_large_pages(client: TestClient) -> None:
    dataset_id = _confirmed(client)
    url = BASE.format(dataset_id) + "/clients"

    assert (
        client.post(url, json={"previous": Y2025, "current": Y2026, "category": "vip"}).status_code
        == 422
    )
    assert (
        client.post(
            url, json={"previous": Y2025, "current": Y2026, "category": "new", "limit": 501}
        ).status_code
        == 422
    )


def test_trend_and_granularity(client: TestClient) -> None:
    dataset_id = _confirmed(client)

    monthly = client.get(BASE.format(dataset_id) + "/trend").json()
    yearly = client.get(BASE.format(dataset_id) + "/trend", params={"granularity": "year"})
    bad = client.get(BASE.format(dataset_id) + "/trend", params={"granularity": "week"})

    assert monthly["result"]["columns"] == [
        "period", "active_clients", "new_clients", "transactions",
    ]  # fmt: skip
    assert [r[0] for r in yearly.json()["result"]["rows"]] == ["2023", "2024", "2025", "2026"]
    assert bad.status_code == 422


def test_departments(client: TestClient) -> None:
    dataset_id = _confirmed(client)

    out = client.post(
        BASE.format(dataset_id) + "/departments", json={"previous": Y2025, "current": Y2026}
    ).json()

    assert {r[0] for r in out["result"]["rows"]} <= {
        "Audit", "Konsultasi Bisnis", "Pelatihan", "Perpajakan", "Teknologi Informasi",
        "(no department)",
    }  # fmt: skip
    assert out["department_column"] == "departemen"


def test_departments_without_a_department_role(client: TestClient) -> None:
    dataset_id = _confirmed(
        client, {"client_id": "id_klien", "transaction_date": "tanggal_layanan"}
    )

    response = client.post(
        BASE.format(dataset_id) + "/departments", json={"previous": Y2025, "current": Y2026}
    )

    assert response.status_code == 409


def test_coverage_and_identity_issues(client: TestClient) -> None:
    dataset_id = _confirmed(client)

    coverage = client.get(BASE.format(dataset_id) + "/coverage").json()
    issues = client.get(BASE.format(dataset_id) + "/identity-issues").json()

    assert coverage["unique_clients"] == 150 and coverage["rows"] == 795
    assert coverage["years"] == [2023, 2024, 2025, 2026]
    assert coverage["data_end"] == "2026-10-31"
    assert issues["missing_id_rows"] == 7
    assert issues["ids_with_multiple_names"] >= 1


def test_mapping_change_is_not_served_from_cache(client: TestClient) -> None:
    dataset_id = _confirmed(client)
    url = BASE.format(dataset_id) + "/coverage"
    assert client.get(url).json()["identity_kind"] == "client_id"

    client.put(
        f"/datasets/{dataset_id}/column-mapping",
        json={"client_name": "nama_klien", "transaction_date": "tanggal_layanan"},
    )

    assert client.get(url).json()["identity_kind"] == "client_name"


def test_invalid_dates_from_a_real_import_are_counted(client: TestClient) -> None:
    rows = "\n".join(
        f"K{i % 4},2026-0{1 + i % 9}-1{i % 9}" for i in range(60)
    )  # 2 invalid of 62 (< 5%)
    content = f"ID Klien,Tanggal\n{rows}\nK1,2026-02-30\nK2,bukan tanggal\n".encode()
    dataset_id = _dataset(client, content)
    saved = client.put(
        f"/datasets/{dataset_id}/column-mapping",
        json={"client_id": "id_klien", "transaction_date": "tanggal"},
    )
    assert saved.is_success, saved.text

    r = client.post(
        BASE.format(dataset_id) + "/compare", json={"previous": Y2025, "current": Y2026}
    ).json()

    assert r["excluded_missing_date_rows"] == 2
    assert r["current_active"] == 4
    assert "missing_transaction_dates" in {w["code"] for w in r["warnings"]}
