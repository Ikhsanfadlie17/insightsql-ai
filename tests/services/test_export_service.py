"""Task 20: CSV, Excel, PDF, and SQL exports."""

from __future__ import annotations

import contextlib
import io
import re
import zlib
from datetime import UTC, date, datetime

import openpyxl
import pandas as pd
import pytest
import sqlglot

from backend.schemas.analytics import Period
from backend.schemas.exports import ExportMeta
from backend.schemas.insights import Insight
from backend.schemas.query import QueryResult
from backend.services.export_service import (
    MAX_PDF_ROWS,
    retention_table,
    safe_filename,
    to_csv,
    to_excel,
    to_pdf,
    to_sql,
)
from tests.frontend.fakes import retention

INDONESIAN = "Pengujian Mutu — Kalibrasi “Ekspres” Rp1.234,5 café"
RESULT = QueryResult(
    columns=["departemen", "klien", "pendapatan", "tanggal"],
    rows=[
        [INDONESIAN, 34, 1250000.5, "2026-01-05"],
        ["Pajak", 29, None, None],
        ['=HYPERLINK("http://x")', -3, 0.0, "2026-02-01"],
    ],
    row_count=3,
)
PREVIOUS = Period(label="Tahun 2025", start=date(2025, 1, 1), end=date(2026, 1, 1))
CURRENT = Period(label="Tahun 2026", start=date(2026, 1, 1), end=date(2027, 1, 1))
META = ExportMeta(
    title="Klien per departemen",
    question="Berapa klien per departemen?\nUrutkan */ DROP TABLE dataset; --",
    dataset_name="Layanan Klien Contoh",
    generated_at=datetime(2026, 11, 4, 3, 0, tzinfo=UTC),
    periods=[PREVIOUS, CURRENT],
    definitions={"retention_rate": "Klien kembali ÷ klien aktif periode sebelumnya."},
    sql="SELECT departemen, COUNT(DISTINCT id_klien) AS klien FROM dataset GROUP BY 1",
    assumptions=["Semua tahun dihitung."],
)
INSIGHT = Insight(
    source="llm",
    language="id",
    summary="Departemen Pengujian Mutu memiliki klien terbanyak (34).",
    facts=["Pengujian Mutu: 34 klien."],
    recommendations=["Bandingkan dengan tahun lalu."],
)


def _pdf_text(pdf: bytes) -> str:
    """Text of every content stream (decompressed), decoded as cp1252."""
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        data = match.group(1)
        with contextlib.suppress(zlib.error):
            data = zlib.decompress(data)
        chunks.append(data.decode("cp1252", "replace"))
    text = "".join(chunks)
    # Text operators hold strings in parentheses with backslash escapes.
    return re.sub(r"\\([()\\])", r"\1", text)


# --------------------------------------------------------------------------- csv
def test_csv_round_trips_via_pandas() -> None:
    frame = pd.read_csv(io.BytesIO(to_csv(RESULT)), encoding="utf-8-sig")

    assert list(frame.columns) == RESULT.columns
    assert frame.loc[0, "departemen"] == INDONESIAN
    assert frame["klien"].tolist() == [34, 29, -3]
    assert frame.loc[0, "pendapatan"] == 1250000.5
    assert pd.isna(frame.loc[1, "pendapatan"])


def test_csv_starts_with_a_bom_so_excel_reads_utf8() -> None:
    assert to_csv(RESULT).startswith(b"\xef\xbb\xbf")


def test_csv_neutralizes_formulas_in_text_only() -> None:
    frame = pd.read_csv(io.BytesIO(to_csv(RESULT)), encoding="utf-8-sig")

    assert frame.loc[2, "departemen"] == '\'=HYPERLINK("http://x")'
    assert frame.loc[2, "klien"] == -3  # numbers are never prefixed


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_neutralizes_every_formula_prefix(prefix: str) -> None:
    result = QueryResult(columns=["x"], rows=[[f"{prefix}1+1"]], row_count=1)

    text = to_csv(result).decode("utf-8-sig")

    assert f"'{prefix}1+1" in text


# ------------------------------------------------------------------------- excel
def test_excel_has_result_and_metadata_sheets() -> None:
    book = openpyxl.load_workbook(io.BytesIO(to_excel(RESULT, META)))

    assert book.sheetnames == ["Result", "Metadata"]
    result = book["Result"]
    assert [c.value for c in result[1]] == RESULT.columns
    assert result["A2"].value == INDONESIAN
    assert result["B2"].value == 34 and result["C2"].value == 1250000.5
    assert result["C3"].value is None


def test_excel_metadata_holds_question_periods_definitions_and_sql() -> None:
    book = openpyxl.load_workbook(io.BytesIO(to_excel(RESULT, META)))
    values = [str(c.value) for row in book["Metadata"].iter_rows() for c in row if c.value]
    text = "\n".join(values)

    assert META.question in values
    assert "Layanan Klien Contoh" in text
    assert "Tahun 2025" in text and "2025-01-01" in text and "2025-12-31" in text
    assert "retention_rate" in text and META.definitions["retention_rate"] in values
    assert META.sql in values
    assert "Semua tahun dihitung." in values


def test_excel_never_writes_formulas() -> None:
    book = openpyxl.load_workbook(io.BytesIO(to_excel(RESULT, META)))
    cell = book["Result"]["A4"]

    assert cell.value == '=HYPERLINK("http://x")'
    assert cell.data_type == "s"


def test_excel_drops_control_characters_instead_of_failing() -> None:
    result = QueryResult(columns=["x"], rows=[["a\x00b\x1fc"]], row_count=1)

    book = openpyxl.load_workbook(io.BytesIO(to_excel(result, META)))

    assert book["Result"]["A2"].value == "abc"


def test_excel_marks_a_truncated_result() -> None:
    result = RESULT.model_copy(update={"truncated": True})
    book = openpyxl.load_workbook(io.BytesIO(to_excel(result, META)))
    text = " ".join(str(c.value) for row in book["Metadata"].iter_rows() for c in row)

    assert "truncated" in text.lower()


# --------------------------------------------------------------------------- pdf
def test_pdf_contains_periods_insight_and_result() -> None:
    pdf = to_pdf(RESULT, META, INSIGHT)
    text = _pdf_text(pdf)

    assert pdf.startswith(b"%PDF")
    assert "Tahun 2025" in text and "Tahun 2026" in text
    assert "2025-12-31" in text
    assert "Departemen Pengujian Mutu memiliki klien terbanyak" in text
    assert "Bandingkan dengan tahun lalu." in text
    assert "Pajak" in text


def test_pdf_keeps_indonesian_and_western_characters() -> None:
    text = _pdf_text(to_pdf(RESULT, META, None))

    # Table cells wrap, so check each part (bytes are cp1252: "—" is 0x97, not "?").
    for part in ("Pengujian Mutu — Kalibrasi", "“Ekspres”", "Rp1.234,5", "café"):
        assert part in text
    assert "replaced" not in text


def test_pdf_replaces_characters_it_cannot_show_and_says_so() -> None:
    result = QueryResult(columns=["nama"], rows=[["日本 client"]], row_count=1)

    text = _pdf_text(to_pdf(result, META, None))

    assert "?? client" in text
    assert "replaced" in text


def test_pdf_limits_rows_and_points_to_the_full_export() -> None:
    rows = [[f"Klien {n:04d}", n] for n in range(MAX_PDF_ROWS + 25)]
    result = QueryResult(columns=["klien", "n"], rows=rows, row_count=len(rows))

    text = _pdf_text(to_pdf(result, META, None))

    assert f"Klien {MAX_PDF_ROWS - 1:04d}" in text
    assert f"Klien {MAX_PDF_ROWS:04d}" not in text
    assert "CSV or Excel" in text


def test_pdf_without_result_rows() -> None:
    empty = QueryResult(columns=["x"], rows=[], row_count=0)

    assert to_pdf(empty, META, None).startswith(b"%PDF")


# --------------------------------------------------------------------------- sql
def test_sql_file_has_the_question_as_a_comment_and_parses() -> None:
    text = to_sql(META.sql, META).decode("utf-8")

    assert "-- Question: Berapa klien per departemen?" in text
    assert "--   Urutkan */ DROP TABLE dataset; --" in text  # stays inside a comment
    assert "-- Dataset: Layanan Klien Contoh" in text
    assert "2026-11-04T03:00:00+00:00" in text
    statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    assert len(statements) == 1
    assert isinstance(statements[0], sqlglot.exp.Select)


def test_sql_file_with_several_statements_and_periods() -> None:
    sql = "SELECT 1 AS a;\nSELECT 2 AS b"

    text = to_sql(sql, META).decode("utf-8")

    assert "-- Period: Tahun 2025: 2025-01-01 to 2025-12-31" in text
    assert len([s for s in sqlglot.parse(text, read="duckdb") if s is not None]) == 2


# ----------------------------------------------------------------------- helpers
def test_retention_table_lists_counts_and_rates_with_na() -> None:
    table = retention_table(_retention(retention_rate=None, growth_rate=0.125))
    rows = {row[0]: row for row in table.rows}

    assert table.columns == ["metric", "previous", "current", "unit"]
    assert rows["Active clients"][1:3] == [3, 4]
    assert rows["Not returned"][1:3] == [1, None]
    assert rows["New clients"][1:3] == [None, 1]
    assert rows["Retention rate"][2] == "N/A"
    assert rows["Growth rate"][2] == 12.5 and rows["Growth rate"][3] == "%"


def test_safe_filename() -> None:
    assert safe_filename("Klien 2025 vs 2026 — Ekspres/../x", "xlsx") == (
        "klien-2025-vs-2026-ekspres-x.xlsx"
    )
    assert safe_filename("日本", "csv") == "insightsql-export.csv"


def _retention(**overrides):
    from backend.schemas.analytics import RetentionResult

    data = retention(**overrides)
    return RetentionResult.model_validate(data)
