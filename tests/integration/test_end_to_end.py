"""Task 22: one pass through the whole product with the real backend and a scripted LLM.

Upload the sample workbook -> profile -> confirm mapping -> compare 2025 vs 2026 (checked
against an independent pandas computation from the raw file) -> three AI questions
(aggregate, retention, clarification) -> every export -> delete; nothing is left behind.
LLM: mock. This proves the wiring of our code, not model quality.
"""

from __future__ import annotations

import io
import json
import re
from datetime import date

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from backend.schemas.insights import InsightOutput
from backend.schemas.llm_outputs import ChartHint, RetentionParams, SQLPlanOutput
from tests import ground_truth
from tests.conftest import SAMPLE_DATA_DIR
from tests.llm_fakes import FakeLLMClient

XLSX = SAMPLE_DATA_DIR / "client_services.xlsx"
PREVIOUS = (date(2025, 1, 1), date(2026, 1, 1))
CURRENT = (date(2026, 1, 1), date(2027, 1, 1))
PERIODS = {
    "previous": {"label": "2025", "start": "2025-01-01", "end": "2026-01-01"},
    "current": {"label": "2026", "start": "2026-01-01", "end": "2027-01-01"},
}
DEPT_SQL = (
    "SELECT departemen, COUNT(DISTINCT id_klien) AS klien_unik FROM dataset "
    "WHERE year(tanggal_layanan) = 2026 GROUP BY departemen ORDER BY klien_unik DESC"
)


def _plan(intent: str, **fields) -> SQLPlanOutput:
    base = {
        "intent": intent,
        "language": "id",
        "sql": None,
        "retention": None,
        "explanation": "",
        "chart": None,
        "metrics": [],
        "assumptions": [],
        "clarification_question": None,
    }
    return SQLPlanOutput(**{**base, **fields})


def _echo(call) -> InsightOutput:
    """A grounded insight: repeats the first number of the data it was given."""
    data = json.loads(re.search(r"<data>\n(.*)\n</data>", call.user, re.S).group(1))
    first = next(v for row in data["rows"] for v in row if isinstance(v, int | float))
    facts = [] if "facts of this comparison" in call.user else [f"Nilai pertama: {first}."]
    summary = "Ringkasan perbandingan." if not facts else f"Nilai pertama adalah {first}."
    return InsightOutput(
        summary=summary, facts=facts, interpretations=[], recommendations=[], limitations=[]
    )


@pytest.fixture
def raw() -> pd.DataFrame:
    """The workbook as a person would open it: no code shared with the backend."""
    return pd.read_excel(XLSX, sheet_name="Transactions", engine="openpyxl")


def test_full_product_flow(settings: Settings, raw: pd.DataFrame) -> None:
    fake = FakeLLMClient(
        responses=[
            _plan("sql", sql=DEPT_SQL, explanation="Klien unik per departemen 2026.",
                  chart=ChartHint(type="bar", x="departemen", y=["klien_unik"])),
            _echo,
            _plan("retention", explanation="Perbandingan 2025 dan 2026.",
                  retention=RetentionParams(previous_start="2025-01-01",
                                            previous_end="2026-01-01",
                                            current_start="2026-01-01",
                                            current_end="2027-01-01", ytd=False)),
            _echo,
            _plan("clarify", clarification_question="Periode mana yang dimaksud?"),
        ]
    )  # fmt: skip
    configured = settings.model_copy(update={"openai_model": "fake-model"})
    with TestClient(create_app(configured, llm_client=fake)) as client:
        # 1. Upload and inspect: both sheets with their headers and type hints.
        inspection = client.post(
            "/datasets/inspect", files={"file": (XLSX.name, XLSX.read_bytes())}
        ).json()
        sheets = {s["name"]: s for s in inspection["sheets"]}
        assert sheets["Transactions"]["row_count"] == len(raw)
        assert sheets["Transactions"]["detected_types"]["Tanggal Layanan"] in {"date", "datetime"}

        # 2. Import the transactions sheet (ingest runs as a background task).
        created = client.post(
            "/datasets", json={"upload_id": inspection["upload_id"], "sheet": "Transactions"}
        ).json()
        dataset_id = created["id"]
        record = client.get(f"/datasets/{dataset_id}").json()
        assert record["status"] == "ready" and record["row_count"] == len(raw)

        # 3. Profile matches the raw file.
        profile = client.get(f"/datasets/{dataset_id}/profile").json()
        assert profile["row_count"] == len(raw)
        assert profile["duplicate_row_count"] == int(raw.duplicated().sum())
        columns = {c["original_name"]: c for c in profile["columns"]}
        assert columns["ID Klien"]["unique_count"] == raw["ID Klien"].nunique()
        assert columns["Tanggal Layanan"]["logical_type"] in {"date", "datetime"}

        # 4. Confirm the suggested mapping, unchanged.
        state = client.get(f"/datasets/{dataset_id}/column-mapping").json()
        draft = {k: v for k, v in state["draft"].items() if k not in {"confirmed", "confirmed_at"}}
        assert draft["client_id"] == "id_klien" and draft["transaction_date"] == "tanggal_layanan"
        assert client.put(f"/datasets/{dataset_id}/column-mapping", json=draft).status_code == 200

        # 5. Client analytics equal the independent computation from the raw workbook.
        compare = client.post(
            f"/datasets/{dataset_id}/client-analytics/compare", json=PERIODS
        ).json()
        truth = ground_truth.compute(raw, "ID Klien", "Tanggal Layanan", PREVIOUS, CURRENT)
        for name in ("previous_active", "current_active", "returning", "new", "reactivated",
                     "between_period", "not_returned", "existing_active",
                     "transactions_previous", "transactions_current"):  # fmt: skip
            assert compare[name] == getattr(truth, name), name
        assert compare["retention_rate"] == pytest.approx(truth.retention_rate)

        # 6a. Aggregate question: the numbers come from the query, equal to pandas.
        aggregate = client.post(
            "/analyses/ask",
            json={"dataset_id": dataset_id, "question": "Klien unik per departemen 2026?"},
        ).json()
        assert aggregate["status"] == "answered" and aggregate["insight"]["source"] == "llm"
        in_2026 = raw[raw["Tanggal Layanan"].dt.year == 2026]
        expected = in_2026.groupby("Departemen")["ID Klien"].nunique().to_dict()
        assert dict(map(tuple, aggregate["table"]["rows"])) == expected

        # 6b. Retention question: same engine, same numbers as step 5.
        retention = client.post(
            "/analyses/ask",
            json={"dataset_id": dataset_id, "question": "Bandingkan klien 2025 dan 2026",
                  "conversation_id": aggregate["conversation_id"]},
        ).json()  # fmt: skip
        assert retention["status"] == "answered"
        assert retention["retention"]["current_active"] == truth.current_active
        assert retention["retention"]["returning"] == truth.returning

        # 6c. Ambiguous question: a clarification, nothing executed.
        clarify = client.post(
            "/analyses/ask",
            json={"dataset_id": dataset_id, "question": "Bagaimana performanya?",
                  "conversation_id": aggregate["conversation_id"]},
        ).json()  # fmt: skip
        assert clarify["status"] == "clarification" and clarify["sql"] is None
        assert len(fake.calls) == 5  # every queued answer was used, no more

        history = client.get(f"/conversations/{aggregate['conversation_id']}").json()
        assert [a["status"] for a in history["analyses"]] == [
            "answered", "answered", "clarification",
        ]  # fmt: skip

        # 7. Every export format, for an answer and for the client analytics comparison.
        url = f"/analyses/{aggregate['analysis_id']}/export"
        csv = client.get(url, params={"format": "csv"})
        frame = pd.read_csv(io.BytesIO(csv.content), encoding="utf-8-sig")
        assert frame.values.tolist() == aggregate["table"]["rows"]
        book = openpyxl.load_workbook(
            io.BytesIO(client.get(url, params={"format": "xlsx"}).content)
        )
        assert book.sheetnames == ["Result", "Metadata"]
        assert client.get(url, params={"format": "pdf"}).content.startswith(b"%PDF")
        assert aggregate["sql"] in client.get(url, params={"format": "sql"}).text  # as executed
        ca_url = f"/datasets/{dataset_id}/client-analytics/export"
        for fmt in ("csv", "xlsx", "pdf", "sql"):
            assert client.post(ca_url, params={"format": fmt}, json=PERIODS).status_code == 200

        usage = client.get("/usage/summary").json()["totals"]
        assert usage["requests"] == 5 and usage["failed"] == 0

        # 8. Delete: data, mapping, conversations, and (after cleanup) the upload are gone.
        assert client.delete(f"/datasets/{dataset_id}").status_code == 204
        assert client.get(f"/datasets/{dataset_id}").status_code == 404
        assert client.get(f"/conversations/{aggregate['conversation_id']}").status_code == 404
        assert client.get("/conversations").json() == []
        client.app.state.dataset_service.cleanup_stale_uploads(max_age_hours=0)

    leftovers = sorted(
        p.relative_to(configured.data_dir).as_posix()
        for p in configured.data_dir.rglob("*")
        if p.is_file() and not p.name.startswith("app.db")
    )
    assert leftovers == []
