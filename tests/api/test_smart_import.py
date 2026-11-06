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
        "rows_above_header": 2,
        "table_like_rows_above": 0,
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


def test_rows_missing_an_optional_column_are_all_stored(client: TestClient) -> None:
    lines = ["Nama,Jumlah,Catatan"]
    lines += [f"Klien {i},{100 + i},{'catatan' if i < 90 else ''}" for i in range(100)]
    content = "\n".join(lines).encode()
    upload = client.post("/datasets/inspect", files={"file": ("p.csv", content)}).json()

    dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]
    record = client.get(f"/datasets/{dataset_id}").json()

    assert upload["sheets"][0]["template_rows"] == 0 and upload["sheets"][0]["row_count"] == 100
    assert record["row_count"] == 100 and record["layout"]["template_rows_skipped"] == 0


def test_year_columns_keep_the_first_row_as_the_header(client: TestClient) -> None:
    rows = [
        b"Kota,2023,2024,2025",
        b"Jakarta,1,2,3",
        b"Medan,4,5,6",
        b"Budi Santoso,cuti,cuti,cuti",
    ]
    content = b"\n".join([*rows, b"Riau,7,8,9"]) + b"\n"
    upload = client.post("/datasets/inspect", files={"file": ("y.csv", content)}).json()

    sheet = upload["sheets"][0]
    dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]
    columns = client.get(f"/datasets/{dataset_id}/columns").json()

    assert sheet["header_row"] == 1 and sheet["columns"] == ["Kota", "2023", "2024", "2025"]
    assert client.get(f"/datasets/{dataset_id}").json()["row_count"] == 4
    assert "Budi Santoso" not in [c["original_name"] for c in columns]


def test_rows_above_the_header_are_counted(client: TestClient) -> None:
    content = b"Laporan,,,\nTotal,100,200,300\nNama,A,B,C\nx,1,2,3\n"
    upload = client.post("/datasets/inspect", files={"file": ("t.csv", content)}).json()

    sheet = upload["sheets"][0]
    dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]
    layout = client.get(f"/datasets/{dataset_id}").json()["layout"]

    assert sheet["header_row"] == 3
    assert sheet["rows_above_header"] == 2 and sheet["table_like_rows_above"] == 1
    assert layout["rows_above_header"] == 2 and layout["table_like_rows_above"] == 1


def test_an_unmatched_quote_fails_the_import_with_a_clear_message(client: TestClient) -> None:
    content = b'a,b\n1,"x\n2,y\n3,z\n'
    upload = client.post("/datasets/inspect", files={"file": ("q.csv", content)}).json()

    dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]
    record = client.get(f"/datasets/{dataset_id}").json()

    assert record["status"] == "failed" and "quote" in record["error"]


def test_a_small_excel_sheet_reports_exact_rows_and_template_rows(client: TestClient) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Laporan"])
    sheet.append(["No", "Nama", "Nilai"])
    for i in range(1, 6):
        sheet.append([i, f"Klien {i}", i * 10])
    for i in range(6, 10):
        sheet.append([i])
    out = io.BytesIO()
    book.save(out)

    info = client.post("/datasets/inspect", files={"file": ("x.xlsx", out.getvalue())}).json()[
        "sheets"
    ][0]

    assert info["header_row"] == 2 and info["rows_above_header"] == 1
    assert info["row_count"] == 5 and info["template_rows"] == 4


def test_a_header_row_past_the_end_is_refused(client: TestClient) -> None:
    upload = client.post("/datasets/inspect", files={"file": ("m.csv", _sheet_csv())}).json()

    response = client.post(
        f"/datasets/uploads/{upload['upload_id']}/layout", json={"header_row": 999}
    )

    assert response.status_code == 422


def test_a_header_row_far_down_an_excel_sheet_is_read_from_that_row(client: TestClient) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for i in range(801):
        sheet.append([i, "x"])
    sheet.append(["Kode", "Nama"])
    sheet.append(["A1", "Klien"])
    out = io.BytesIO()
    book.save(out)
    upload = client.post("/datasets/inspect", files={"file": ("x.xlsx", out.getvalue())}).json()

    info = client.post(
        f"/datasets/uploads/{upload['upload_id']}/layout", json={"header_row": 802}
    ).json()

    assert info["header_row"] == 802 and info["columns"] == ["Kode", "Nama"]
    assert info["row_count"] == 1


def test_a_plain_file_is_unchanged(client: TestClient) -> None:
    content = b"ID,Nama,Nilai\nA1,x,1\nA2,y,2\n"
    sheet = client.post("/datasets/inspect", files={"file": ("p.csv", content)}).json()["sheets"][0]

    assert sheet["header_row"] == 1 and sheet["ignored_columns"] == []
    assert sheet["columns"] == ["ID", "Nama", "Nilai"] and sheet["row_count"] == 2
