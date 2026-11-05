"""Smart import through the API: a Google Sheets CSV laid out for people (title rows, an
empty first column, a side table, pre-filled template rows, month-name dates) imports as is.
Dummy values throughout."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

HEADER = ["", "No", "Tahun", "Status", "ID Pembeli", "Nama Pembeli", "Tanggal Order",
          "Lama Kirim", "Nilai Order", "", "", "", "Daftar hari libur"]  # fmt: skip


def _sheet_csv(real: int = 8, templates: int = 6) -> bytes:
    rows = [
        [""] * len(HEADER),
        ["", "", "Diisi admin", "Diisi gudang", "", "", "Rekap Contoh"] + [""] * 6,
        ["", "", "", "", "", "", "", "Pengiriman (2 hari)"] + [""] * 5,
        HEADER,
    ]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]
    for i in range(real):
        year = 2025 if i < 5 else 2026
        rows.append(["", str(i + 1), str(year), "Closed", f"PLG-{i % 6:03d}", f"Klien {i}",
                     f"{i + 3}/{months[i]}/{year}", f"{i} hari", f'{(i + 1) * 1_250_000:,}',
                     "", "", "7 hari", f"{i + 1}/Jan/2025"])  # fmt: skip
    for i in range(real, real + templates):
        rows.append(["", str(i + 1), "", "", "", "", "", "-", "", "", "", "", ""])
    rows += [[""] * len(HEADER)] * 3
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("utf-8")


def test_inspection_finds_the_table(client: TestClient) -> None:
    sheet = client.post(
        "/datasets/inspect", files={"file": ("monitoring.csv", _sheet_csv())}
    ).json()["sheets"][0]

    assert sheet["header_row"] == 4 and sheet["header_detected"] is True
    assert sheet["row_count"] == 8 and sheet["template_rows"] == 6  # exact for CSV
    assert sheet["columns"] == HEADER[1:9]
    assert sheet["ignored_columns"] == ["column 12", "Daftar hari libur"]
    assert sheet["detected_types"]["Tanggal Order"] == "date"
    assert sheet["detected_types"]["Nilai Order"] == "integer"
    assert sheet["detected_types"]["ID Pembeli"] == "text"


def test_import_as_is_gives_the_real_rows(client: TestClient) -> None:
    upload = client.post("/datasets/inspect", files={"file": ("m.csv", _sheet_csv())}).json()
    dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]

    record = client.get(f"/datasets/{dataset_id}").json()
    columns = client.get(f"/datasets/{dataset_id}/columns").json()

    assert record["status"] == "ready", record["error"]
    assert record["row_count"] == 8  # 6 template rows and the empty rows are not data
    assert record["layout"] == {
        "header_row": 4,
        "header_chosen": False,
        "ignored_columns": ["column 12", "Daftar hari libur"],
        "template_rows_skipped": 6,
    }
    assert [c["original_name"] for c in columns] == HEADER[1:9]
    types = {c["original_name"]: c["logical_type"] for c in columns}
    assert types["Tanggal Order"] == "date" and types["Nilai Order"] == "integer"
    assert types["Lama Kirim"] == "text"


def test_template_rows_can_be_kept(client: TestClient) -> None:
    upload = client.post("/datasets/inspect", files={"file": ("m.csv", _sheet_csv())}).json()
    dataset_id = client.post(
        "/datasets", json={"upload_id": upload["upload_id"], "skip_template_rows": False}
    ).json()["id"]

    record = client.get(f"/datasets/{dataset_id}").json()

    assert record["row_count"] == 14
    assert record["layout"]["template_rows_skipped"] == 0


def test_the_header_row_can_be_chosen(client: TestClient) -> None:
    upload = client.post("/datasets/inspect", files={"file": ("m.csv", _sheet_csv())}).json()

    preview = client.post(
        f"/datasets/uploads/{upload['upload_id']}/layout", json={"header_row": 3}
    ).json()
    created = client.post(
        "/datasets",
        json={"upload_id": upload["upload_id"], "header_row": 4, "text_columns": ["Tahun"]},
    ).json()
    record = client.get(f"/datasets/{created['id']}").json()

    assert preview["header_row"] == 3 and preview["header_detected"] is False
    assert "Pengiriman (2 hari)" in preview["columns"]
    assert record["layout"]["header_chosen"] is True and record["row_count"] == 8
    assert record["text_columns"] == ["Tahun"]


def test_keep_as_text_names_are_checked_against_the_chosen_header(client: TestClient) -> None:
    upload = client.post("/datasets/inspect", files={"file": ("m.csv", _sheet_csv())}).json()

    response = client.post(
        "/datasets", json={"upload_id": upload["upload_id"], "text_columns": ["Tidak Ada"]}
    )

    assert response.status_code == 422


def test_a_plain_file_is_unchanged(client: TestClient) -> None:
    content = b"ID,Nama,Nilai\nA1,x,1\nA2,y,2\n"
    sheet = client.post("/datasets/inspect", files={"file": ("p.csv", content)}).json()["sheets"][0]

    assert sheet["header_row"] == 1 and sheet["ignored_columns"] == []
    assert sheet["columns"] == ["ID", "Nama", "Nilai"] and sheet["row_count"] == 2
