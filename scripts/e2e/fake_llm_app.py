"""E2E only: the real backend (FastAPI + DuckDB + sandboxed worker) with a scripted fake
LLM. It never contacts OpenAI. Results prove the application wiring, not model quality.

    DATA_DIR=/tmp/insightsql-e2e uvicorn --app-dir scripts/e2e fake_llm_app:app --port 8000
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core.config import Settings
from backend.main import create_app
from backend.schemas.insights import InsightOutput
from backend.schemas.llm_outputs import ChartHint, RetentionParams, SQLPlanOutput
from backend.services.llm.client import LLMResponse, TokenUsage


def plan(**kw):
    base = dict(
        intent="sql",
        language="id",
        sql=None,
        retention=None,
        explanation="",
        chart=None,
        metrics=[],
        assumptions=[],
        clarification_question=None,
    )
    base.update(kw)
    return SQLPlanOutput(**base)


class Scripted:
    def complete_structured(self, *, system, user, schema, model=None):
        if schema is InsightOutput:
            data = json.loads(re.search(r"<data>\n(.*)\n</data>", user, re.S).group(1))
            nums = [
                v
                for row in data["rows"]
                for v in row
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            first = nums[0] if nums else 0
            out = InsightOutput(
                summary=f"Nilai pertama pada hasil adalah {first}.",
                facts=[f"Baris pertama bernilai {first}."],
                interpretations=[],
                recommendations=["Bandingkan dengan periode lain."],
                limitations=[],
            )
        else:
            q = re.search(r"<question>\n(.*?)\n</question>", user, re.S).group(1).lower()
            if "bandingkan" in q or "retensi" in q:
                out = plan(
                    intent="retention",
                    explanation="Perbandingan klien 2025 vs 2026 (YTD s.d. 31 Okt).",
                    retention=RetentionParams(
                        previous_start="2025-01-01",
                        previous_end="2025-11-01",
                        current_start="2026-01-01",
                        current_end="2026-11-01",
                        ytd=True,
                    ),
                )
            elif "departemen" in q:
                out = plan(
                    sql="SELECT departemen, COUNT(DISTINCT id_klien) AS klien_unik FROM dataset "
                    "WHERE year(tanggal_layanan) = 2026 GROUP BY departemen "
                    "ORDER BY klien_unik DESC",
                    explanation="Klien unik per departemen di 2026.",
                    chart=ChartHint(type="bar", x="departemen", y=["klien_unik"]),
                    assumptions=["Tahun ini = 2026."],
                )
            elif "hapus" in q:
                out = plan(sql="DROP TABLE dataset", explanation="x")
            else:
                out = plan(
                    intent="clarify", clarification_question="Periode mana yang Anda maksud?"
                )
        return LLMResponse(
            parsed=out,
            usage=TokenUsage(input_tokens=500, output_tokens=60, cached_tokens=0),
            model="scripted-fake",
            latency_ms=1.0,
            attempts=1,
        )


settings = Settings(
    _env_file=None,
    data_dir=os.environ["DATA_DIR"],
    openai_api_key="sk-not-real",
    openai_model="scripted-fake",
)
app = create_app(settings, llm_client=Scripted())
