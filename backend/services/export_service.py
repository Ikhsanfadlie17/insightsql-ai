"""Exports of a result table: CSV, Excel (with a metadata sheet), PDF, and SQL.

Values come from uploaded files and LLM-written SQL aliases, so they are untrusted:
text that a spreadsheet would run as a formula is neutralized (CSV: a leading
apostrophe; Excel: always stored as a text cell), and XML control characters, which
Excel files cannot hold, are dropped.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import timedelta
from typing import Any

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font

from backend.schemas.analytics import Period, RetentionResult
from backend.schemas.exports import ExportMeta
from backend.schemas.insights import Insight
from backend.schemas.query import QueryResult

MAX_PDF_ROWS = 50
MAX_PDF_COLUMNS = 10
MAX_PDF_CELL_CHARS = 60
MAX_EXCEL_CELL_CHARS = 32_767
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_PDF_ENCODING = "cp1252"  # Helvetica (built in) covers Western European text only
MIME_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "sql": "application/sql; charset=utf-8",
}


# ----------------------------------------------------------------------- helpers
def last_day(period: Period) -> str:
    """Inclusive last day of a half-open period."""
    return (period.end - timedelta(days=1)).isoformat()


def period_text(period: Period) -> str:
    return f"{period.label}: {period.start.isoformat()} to {last_day(period)}"


def safe_filename(name: str, extension: str) -> str:
    """ASCII slug for a download name (never a path)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:80].strip("-")
    return f"{slug or 'insightsql-export'}.{extension}"


def _neutralize(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _pct(rate: float | None) -> float | str:
    return "N/A" if rate is None else round(rate * 100, 1)


def retention_table(r: RetentionResult) -> QueryResult:
    """Client-analytics comparison as rows of (metric, previous, current, unit).

    Rates are percentages with one decimal; a rate whose denominator is zero is "N/A".
    A blank cell means the metric does not apply to that period.
    """
    rows: list[list[Any]] = [
        ["Active clients", r.previous_active, r.current_active, "clients"],
        ["New clients", None, r.new, "clients"],
        ["Returning clients", None, r.returning, "clients"],
        ["Reactivated clients", None, r.reactivated, "clients"],
        ["Between-period clients", None, r.between_period, "clients"],
        ["Not returned", r.not_returned, None, "clients"],
        ["Existing active (overlaps the categories above)", None, r.existing_active, "clients"],
        ["Transactions", r.transactions_previous, r.transactions_current, "transactions"],
        [
            "Repeat transactions",
            r.repeat_transactions_previous,
            r.repeat_transactions_current,
            "transactions",
        ],
        [
            "Clients with repeat transactions",
            r.clients_with_repeat_previous,
            r.clients_with_repeat_current,
            "clients",
        ],
        ["Retention rate", None, _pct(r.retention_rate), "%"],
        ["New client rate", None, _pct(r.new_client_rate), "%"],
        ["Growth rate", None, _pct(r.growth_rate), "%"],
        ["Rows excluded: missing client ID", None, r.excluded_missing_id_rows, "rows"],
        ["Rows excluded: missing date", None, r.excluded_missing_date_rows, "rows"],
        ["Exact duplicate rows counted once", None, r.duplicate_rows_excluded, "rows"],
    ]
    return QueryResult(
        columns=["metric", "previous", "current", "unit"], rows=rows, row_count=len(rows)
    )


# --------------------------------------------------------------------------- csv
def to_csv(result: QueryResult) -> bytes:
    """UTF-8 CSV with a BOM (so Excel shows accented text correctly)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_neutralize(str(c)) for c in result.columns])
    for row in result.rows:
        writer.writerow(
            ["" if v is None else _neutralize(v) if isinstance(v, str) else v for v in row]
        )
    return ("﻿" + buffer.getvalue()).encode("utf-8")


# ------------------------------------------------------------------------- excel
def _excel_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    return text[:MAX_EXCEL_CELL_CHARS]


def _put(sheet, row: int, column: int, value: Any, bold: bool = False) -> None:
    cell = sheet.cell(row=row, column=column)
    cell.value = _excel_value(value)
    if isinstance(cell.value, str):
        cell.data_type = "s"  # never a formula, even if the text starts with "="
    if bold:
        cell.font = Font(bold=True)


def _metadata_rows(result: QueryResult, meta: ExportMeta) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = [
        ("Title", meta.title),
        ("Question", meta.question or ""),
        ("Dataset", meta.dataset_name),
        ("Generated at (UTC)", meta.generated_at.isoformat()),
        ("Rows exported", result.row_count),
    ]
    if result.truncated:
        rows.append(("Note", "The result was truncated to the row limit; it is not complete."))
    for n, period in enumerate(meta.periods):
        name = ("Previous period", "Current period")[n] if len(meta.periods) == 2 else "Period"
        rows.append((name, f"{period_text(period)} (inclusive)"))
    for key, text in meta.definitions.items():
        rows.append((f"Definition: {key}", text))
    rows.extend(("Assumption", text) for text in meta.assumptions)
    rows.extend(("Data note", text) for text in meta.notes)
    if meta.sql:
        rows.append(("SQL", meta.sql))
    rows.append(("Source", "InsightSQL AI: numbers computed by DuckDB from the dataset."))
    return rows


def to_excel(result: QueryResult, meta: ExportMeta) -> bytes:
    """Workbook with a ``Result`` sheet and a ``Metadata`` sheet."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Result"
    for c, name in enumerate(result.columns, start=1):
        _put(sheet, 1, c, name, bold=True)
    for r, row in enumerate(result.rows, start=2):
        for c, value in enumerate(row, start=1):
            _put(sheet, r, c, value)
    sheet.freeze_panes = "A2"
    for c, name in enumerate(result.columns, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=c).column_letter].width = min(
            max(12, len(str(name)) + 2), 40
        )

    info = book.create_sheet("Metadata")
    for r, (key, value) in enumerate(_metadata_rows(result, meta), start=1):
        _put(info, r, 1, key, bold=True)
        _put(info, r, 2, value)
        info.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    info.column_dimensions["A"].width = 28
    info.column_dimensions["B"].width = 100

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- pdf
class _Pdf(FPDF):
    def __init__(self, landscape: bool) -> None:
        super().__init__(orientation="L" if landscape else "P", format="A4")
        self.core_fonts_encoding = _PDF_ENCODING
        self.replaced = False
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(15, 15, 15)

    def clean(self, value: object, limit: int | None = None) -> str:
        text = "".join(
            ch for ch in str(value) if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
        )
        if limit is not None and len(text) > limit:
            text = text[: limit - 1] + "…"
        encoded = text.encode(_PDF_ENCODING, "replace").decode(_PDF_ENCODING)
        if encoded != text:
            self.replaced = True
        return encoded

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(100, 116, 139)
        self.cell(0, 6, f"InsightSQL AI · page {self.page_no()}", align="R")

    def heading(self, text: str) -> None:
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(15, 23, 42)
        self.multi_cell(0, 6, self.clean(text), new_x="LMARGIN", new_y="NEXT")

    def para(self, text: str, size: float = 9, font: str = "Helvetica") -> None:
        self.set_font(font, size=size)
        self.set_text_color(15, 23, 42)
        self.multi_cell(0, 4.6, self.clean(text), new_x="LMARGIN", new_y="NEXT")

    def bullets(self, title: str, items: list[str]) -> None:
        if items:
            self.heading(title)
            for item in items:
                self.para(f"- {item}")


def _pdf_value(value: Any) -> str:
    """Numbers in Indonesian format (1.234,5), like the app."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    if isinstance(value, float):
        text = f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
        return text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return str(value)


def _pdf_table(pdf: _Pdf, result: QueryResult) -> None:
    columns = result.columns[:MAX_PDF_COLUMNS]
    rows = result.rows[:MAX_PDF_ROWS]
    pdf.set_font("Helvetica", size=7.5)
    with pdf.table(
        first_row_as_headings=True, line_height=4.2, padding=1, text_align="LEFT"
    ) as table:
        header = table.row()
        for name in columns:
            header.cell(pdf.clean(name, MAX_PDF_CELL_CHARS))
        for values in rows:
            line = table.row()
            for value in values[: len(columns)]:
                line.cell(pdf.clean(_pdf_value(value), MAX_PDF_CELL_CHARS))
    omitted = []
    if len(result.rows) > MAX_PDF_ROWS:
        omitted.append(f"the first {MAX_PDF_ROWS} of {result.row_count:,} rows are shown")
    if len(result.columns) > MAX_PDF_COLUMNS:
        omitted.append(f"the first {MAX_PDF_COLUMNS} of {len(result.columns)} columns are shown")
    if omitted:
        pdf.para("Only " + " and ".join(omitted) + "; the CSV or Excel export has all of it.", 8)
    if result.truncated:
        pdf.para("The result was truncated to the row limit; it is not complete.", 8)


def to_pdf(result: QueryResult, meta: ExportMeta, insight: Insight | None) -> bytes:
    """A short report: context, insight, result table, definitions, and SQL."""
    pdf = _Pdf(landscape=len(result.columns) > 6)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.multi_cell(0, 8, pdf.clean(meta.title), new_x="LMARGIN", new_y="NEXT")
    pdf.para(f"Dataset: {meta.dataset_name}")
    pdf.para(f"Generated: {meta.generated_at.isoformat()} (UTC)")
    if meta.question:
        pdf.para(f"Question: {meta.question}")
    for n, period in enumerate(meta.periods):
        name = ("Previous period", "Current period")[n] if len(meta.periods) == 2 else "Period"
        pdf.para(f"{name}: {period_text(period)} (inclusive)")

    if insight is not None:
        source = "AI-written, numbers checked" if insight.source == "llm" else "Template summary"
        pdf.heading(f"Summary ({source})")
        pdf.para(insight.summary)
        pdf.bullets("Facts", insight.facts)
        pdf.bullets("Interpretation (not proven by the data)", insight.interpretations)
        pdf.bullets("Recommendations", insight.recommendations)
        pdf.bullets("Limitations", insight.limitations)

    pdf.heading(f"Result ({result.row_count:,} row(s))")
    if result.rows:
        _pdf_table(pdf, result)
    else:
        pdf.para("The query returned no rows.")

    if meta.definitions:
        pdf.heading("Definitions")
        for key, text in meta.definitions.items():
            pdf.para(f"{key}: {text}")
    pdf.bullets("Assumptions", meta.assumptions)
    pdf.bullets("Data notes", meta.notes)
    if meta.sql:
        pdf.heading("SQL")
        pdf.para(meta.sql, size=7.5, font="Courier")
    if pdf.replaced:
        pdf.heading("Note")
        pdf.para(
            "Some characters cannot be shown in this PDF and were replaced by '?'. "
            "The CSV and Excel exports keep the exact values."
        )
    return bytes(pdf.output())


# --------------------------------------------------------------------------- sql
def _comment(label: str, value: str) -> list[str]:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [f"-- {label}: {lines[0]}"] + [f"--   {line}" for line in lines[1:]]


def to_sql(sql: str, meta: ExportMeta) -> bytes:
    """The validated SQL with a header comment: question, dataset, time, periods."""
    header = ["-- InsightSQL AI export (read-only SQL, validated before it was run)"]
    header += _comment("Title", meta.title)
    if meta.question:
        header += _comment("Question", meta.question)
    header += _comment("Dataset", meta.dataset_name)
    header.append(f"-- Generated: {meta.generated_at.isoformat()}")
    header += [f"-- Period: {period_text(p)}" for p in meta.periods]
    header.append('-- The data is in a table named "dataset" (DuckDB).')
    body = sql.strip()
    if not body.endswith(";"):
        body += ";"
    return ("\n".join(header) + "\n\n" + body + "\n").encode("utf-8")
