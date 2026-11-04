"""Task 17: insights explain real results only; anything unverifiable falls back to a template."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

import pytest

from backend.core.config import Settings
from backend.core.errors import LLMTimeoutError
from backend.repositories.usage_repo import UsageRepository
from backend.schemas.analytics import Period, RetentionResult
from backend.schemas.insights import InsightOutput
from backend.schemas.query import QueryResult
from backend.services.insight_generator import (
    MAX_INSIGHT_ROWS,
    InsightGenerator,
    deterministic_insight,
    extract_numbers,
    grounding_problems,
    subject_from_result,
    subject_from_retention,
)
from backend.services.llm.cost import PricingConfig
from backend.services.llm.gateway import LLMGateway
from tests.llm_fakes import FakeLLMClient

DEPTS = QueryResult(
    columns=["departemen", "klien_aktif"],
    rows=[["Audit", 34], ["Perpajakan", 29], ["Pelatihan", 25]],
    row_count=3,
)
CLIENTS = QueryResult(
    columns=["nama_klien", "pendapatan"],
    rows=[["PT Sentosa Finansial", 125_000_000], ["CV Mitra Jaya", 80_000_000]],
    row_count=2,
)


def _retention(**overrides) -> RetentionResult:
    data = dict(
        previous=Period(label="2025", start=date(2025, 1, 1), end=date(2026, 1, 1)),
        current=Period(label="2026", start=date(2026, 1, 1), end=date(2027, 1, 1)),
        identity_column="id_klien", identity_kind="client_id", date_column="tanggal",
        previous_active=87, current_active=103, new=30, returning=55, reactivated=16,
        between_period=2, not_returned=32, existing_active=73, transactions_previous=189,
        transactions_current=276, repeat_transactions_previous=102,
        repeat_transactions_current=173, clients_with_repeat_previous=61,
        clients_with_repeat_current=88, retention_rate=55 / 87, new_client_rate=30 / 103,
        growth_rate=16 / 87, excluded_missing_id_rows=3, excluded_missing_date_rows=0,
        duplicate_rows_excluded=12, data_start=date(2023, 1, 3), data_end=date(2026, 10, 31),
    )  # fmt: skip
    data.update(overrides)
    return RetentionResult(**data)


def _output(**fields) -> InsightOutput:
    base = {
        "summary": "",
        "facts": [],
        "interpretations": [],
        "recommendations": [],
        "limitations": [],
    }
    return InsightOutput(**{**base, **fields})


@pytest.fixture
def make(settings: Settings):
    def build(*responses, enabled: bool = True) -> tuple[InsightGenerator, FakeLLMClient]:
        fake = FakeLLMClient(responses=list(responses))
        cfg = settings.model_copy(
            update={"enable_llm_insights": enabled, "openai_model": "fake-model"}
        )
        gateway = LLMGateway(
            fake, cfg, UsageRepository(cfg.metadata_db_path), PricingConfig(models={})
        )
        return InsightGenerator(gateway, cfg), fake

    return build


# --- number extraction ----------------------------------------------------------------------
def test_extract_numbers_plan_example() -> None:
    assert extract_numbers("Retensi 62,5% dari 120 klien") == [Decimal("62.5"), Decimal("120")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.234,5 juta", [Decimal("1234.5")]),
        ("1,234.5 clients", [Decimal("1234.5")]),
        ("62.5% retained", [Decimal("62.5")]),
        ("turun -12 klien", [Decimal("-12")]),
        ("Rp 125.000.000", [Decimal("125000000")]),
        ("no numbers here", []),
    ],
)
def test_extract_numbers_formats(text: str, expected: list[Decimal]) -> None:
    assert extract_numbers(text) == expected


# --- grounding ------------------------------------------------------------------------------
def test_values_rounded_percentages_differences_and_shares_are_grounded() -> None:
    subject = subject_from_retention(_retention(), "id")

    for sentence in (
        "Retensi 63,2% (55 dari 87 klien).",
        "Retention 63% of 87 clients; growth 18,4%.",
        "Klien aktif naik 16 klien dari 87 menjadi 103 pada 2026.",
        "New clients were 29.1% of the 103 active clients in 2026.",
        "Data berakhir pada 2026-10-31.",
    ):
        assert grounding_problems([sentence], subject) == [], sentence


def test_shares_and_totals_of_a_category_result_are_grounded() -> None:
    subject = subject_from_result(DEPTS, "id")

    assert grounding_problems(["Audit: 34 dari total 88 klien (38,6%)."], subject) == []
    assert grounding_problems(["Audit unggul 5 klien atas Perpajakan."], subject) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Retensi 999 klien.",
        "Retensi 77%.",
        "Klien aktif 2027 naik.",
        "Data berakhir 2026-11-30.",
        "Pendapatan Rp 1,5 miliar.",
    ],
)
def test_invented_numbers_are_not_grounded(sentence: str) -> None:
    assert grounding_problems([sentence], subject_from_retention(_retention(), "id"))


def test_scaled_amounts_are_grounded() -> None:
    subject = subject_from_result(CLIENTS, "id")

    assert grounding_problems(["Pendapatan tertinggi Rp 125 juta."], subject) == []
    assert grounding_problems(["Pendapatan tertinggi Rp 125,0 juta."], subject) == []
    assert grounding_problems(["Pendapatan tertinggi Rp 126 juta."], subject)


# --- the generator --------------------------------------------------------------------------
def test_grounded_llm_insight_is_used(make) -> None:
    output = _output(
        summary="Audit memiliki klien aktif terbanyak (34).",
        facts=["Audit: 34 klien aktif; Perpajakan: 29; Pelatihan: 25."],
        interpretations=["Audit mungkin menjadi departemen dengan basis klien terbesar."],
        recommendations=["Bandingkan dengan periode sebelumnya sebelum menarik kesimpulan."],
        limitations=["Hanya 3 departemen yang ada di hasil."],
    )
    generator, fake = make(output)

    insight = generator.generate(question="Klien per departemen?", language="id", result=DEPTS)

    assert insight.source == "llm"
    assert insight.facts == output.facts and insight.ungrounded_numbers == []
    assert insight.llm_request_id is not None
    assert len(fake.calls) == 1 and "Audit" in fake.calls[0].user  # a known category value


def test_ungrounded_number_falls_back_to_the_template(make) -> None:
    generator, _ = make(_output(summary="Audit memiliki 999 klien.", facts=["Audit 999."]))

    insight = generator.generate(question="q", language="id", result=DEPTS)

    assert insight.source == "deterministic"
    assert "999" in insight.ungrounded_numbers
    assert insight.fallback_reason == "ungrounded_numbers"
    assert any("34" in fact for fact in insight.facts)  # the template uses real values


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("facts", "Klien aktif turun karena harga naik."),
        ("summary", "Retention fell because of poor service."),
        ("interpretations", "Penurunan ini disebabkan oleh kompetitor."),
    ],
)
def test_unsupported_causal_claims_fall_back(make, field: str, text: str) -> None:
    generator, _ = make(_output(**{field: [text] if field != "summary" else text}))

    insight = generator.generate(question="q", language="id", result=DEPTS)

    assert insight.source == "deterministic"
    assert insight.fallback_reason == "causal_claim"


def test_hedged_interpretation_is_allowed(make) -> None:
    output = _output(
        summary="Audit tertinggi dengan 34 klien.",
        interpretations=["Selisih ini mungkin terjadi karena ukuran tim, tetapi data tidak "
                         "menunjukkan penyebabnya."],
    )  # fmt: skip
    generator, _ = make(output)

    assert generator.generate(question="q", language="id", result=DEPTS).source == "llm"


def test_names_are_pseudonymised_before_sending_and_restored_after(make) -> None:
    def answer(call) -> InsightOutput:
        label = re.search(r"\[V\d+\]", call.user).group(0)
        return _output(summary=f"{label} memiliki pendapatan tertinggi (125 juta).")

    generator, fake = make(answer)

    insight = generator.generate(question="Klien teratas?", language="id", result=CLIENTS)

    sent = fake.calls[0].user
    assert "PT Sentosa Finansial" not in sent and "CV Mitra Jaya" not in sent
    assert insight.source == "llm"
    assert "PT Sentosa Finansial" in insight.summary


def test_unknown_placeholder_falls_back(make) -> None:
    generator, _ = make(_output(summary="[V9] tertinggi."))

    insight = generator.generate(question="q", language="id", result=CLIENTS)

    assert insight.source == "deterministic"
    assert insight.fallback_reason == "unknown_reference"


def test_result_sent_to_the_llm_is_capped(make) -> None:
    rows = [[f"2026-{m:02d}", m] for m in range(1, 13)] * 10
    big = QueryResult(columns=["bulan", "n"], rows=rows, row_count=len(rows))
    generator, fake = make(_output(summary="Ringkasan."))

    generator.generate(question="q", language="id", result=big)

    data = re.search(r"<data>\n(.*)\n</data>", fake.calls[0].user, re.S).group(1)
    assert len(json.loads(data)["rows"]) == MAX_INSIGHT_ROWS == 50


def test_disabled_insights_never_call_the_llm(make) -> None:
    generator, fake = make(enabled=False)

    insight = generator.generate(question="q", language="id", result=DEPTS)

    assert insight.source == "deterministic" and insight.fallback_reason == "llm_insights_disabled"
    assert fake.calls == []


def test_llm_failure_falls_back(make, settings: Settings) -> None:
    generator, _ = make(LLMTimeoutError("timeout"))

    insight = generator.generate(question="q", language="en", result=DEPTS)

    assert insight.source == "deterministic" and insight.fallback_reason == "llm_error"
    records = UsageRepository(settings.metadata_db_path).list_requests(10)
    assert records[0].purpose == "insight" and records[0].status == "error"


def test_empty_result_needs_no_llm(make) -> None:
    generator, fake = make()
    empty = QueryResult(columns=["a"], rows=[], row_count=0)

    insight = generator.generate(question="q", language="en", result=empty)

    assert insight.source == "deterministic" and fake.calls == []
    assert "no rows" in insight.summary.lower()


# --- the deterministic template -------------------------------------------------------------
def test_retention_template_in_indonesian() -> None:
    insight = deterministic_insight(subject_from_retention(_retention(), "id"))

    text = " ".join([insight.summary, *insight.facts])
    assert "63,2%" in text and "55" in text and "87" in text and "103" in text
    assert "+18,4%" in text
    assert any("histori" in item.lower() for item in insight.limitations)
    assert insight.interpretations == []
    assert grounding_problems([text], subject_from_retention(_retention(), "id")) == []


def test_retention_template_with_na_rates_in_english() -> None:
    result = _retention(previous_active=0, returning=0, not_returned=0, retention_rate=None,
                        growth_rate=None)  # fmt: skip
    insight = deterministic_insight(subject_from_retention(result, "en"))

    text = " ".join([insight.summary, *insight.facts])
    assert "N/A" in text and "1,234" not in text
    assert "0%" not in text  # an undefined rate is never shown as zero


def test_result_template_lists_real_values() -> None:
    insight = deterministic_insight(subject_from_result(DEPTS, "en"))

    assert insight.source == "deterministic"
    assert any("Audit" in f and "34" in f for f in insight.facts)
