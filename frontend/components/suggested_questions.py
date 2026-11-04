"""Example questions built from the confirmed column mapping (no AI, no data values)."""

from __future__ import annotations

from typing import Any


def suggested_questions(mapping: dict[str, Any] | None) -> list[str]:
    """Up to four questions the dataset can answer, in Indonesian (answers follow the
    language of the question)."""
    mapping = mapping or {}
    identity = mapping.get("client_id") or mapping.get("client_name")
    date = mapping.get("transaction_date")
    questions: list[str] = []
    if identity and date:
        questions.append("Bandingkan klien tahun lalu dengan tahun ini (year-to-date).")
        questions.append("Berapa jumlah klien unik per tahun?")
    if identity and mapping.get("department"):
        questions.append("Departemen mana yang memiliki klien unik terbanyak tahun ini?")
    if mapping.get("revenue") and date:
        questions.append("Berapa total pendapatan per bulan tahun ini?")
    if mapping.get("service_type"):
        questions.append("Jenis layanan apa yang paling sering digunakan?")
    if not questions:
        questions.append("Berapa jumlah baris data per bulan?")
    return questions[:4]
