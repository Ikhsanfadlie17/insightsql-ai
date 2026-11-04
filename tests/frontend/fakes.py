"""Fake backend client shared by the Streamlit AppTest suites."""

from __future__ import annotations

import copy
from typing import Any

from frontend.api_client import ApiError

READY: dict[str, Any] = {
    "id": "d1",
    "name": "client_services",
    "source_filename": "client_services.csv",
    "sheet_name": None,
    "status": "ready",
    "error": None,
    "row_count": 3,
    "column_count": 2,
    "created_at": "2026-11-04T03:00:00+00:00",
    "number_format": "auto",
    "date_order": "auto",
}
FAILED: dict[str, Any] = {
    **READY,
    "id": "d2",
    "name": "broken",
    "status": "failed",
    "error": "Bad file",
    "row_count": None,
    "column_count": None,
}
UNCONVERTED = "2 value(s) could not be read as dates and are stored as empty (e.g. '31/02/2026')."
COLUMNS: list[dict[str, Any]] = [
    {"position": 0, "name": "id_klien", "original_name": "ID Klien", "logical_type": "text",
     "failed_conversions": 0, "notes": []},
    {"position": 1, "name": "tanggal", "original_name": "Tanggal", "logical_type": "date",
     "failed_conversions": 2,
     "notes": [{"level": "warning", "code": "unconverted_values", "message": UNCONVERTED}]},
]  # fmt: skip
PROFILE: dict[str, Any] = {
    "row_count": 3,
    "column_count": 2,
    "duplicate_row_count": 1,
    "missing_cells": 1,
    "missing_pct": 16.7,
    "generated_at": "2026-11-04T03:00:00+00:00",
    "notes": [
        {"level": "warning", "code": "duplicate_rows", "message": "1 row(s) are duplicates."}
    ],
    "columns": [
        {"name": "id_klien", "original_name": "ID Klien", "logical_type": "text",
         "null_count": 0, "null_pct": 0.0, "unique_count": 3,
         "top_values": [{"value": "KL-0001", "count": 1}], "min": None, "max": None,
         "mean": None, "median": None, "std": None, "date_min": None, "date_max": None,
         "notes": []},
        {"name": "tanggal", "original_name": "Tanggal", "logical_type": "date",
         "null_count": 1, "null_pct": 33.3, "unique_count": 2, "top_values": [],
         "min": None, "max": None, "mean": None, "median": None, "std": None,
         "date_min": "2026-01-15", "date_max": "2026-02-01",
         "notes": COLUMNS[1]["notes"]},
    ],
}  # fmt: skip
ROLES = [
    "client_id",
    "client_name",
    "transaction_date",
    "department",
    "service_type",
    "revenue",
    "status",
]
EMPTY_MAPPING = {role: None for role in ROLES}


def mapping_state(confirmed: bool) -> dict[str, Any]:
    """A MappingState payload, confirmed or not."""
    chosen = {**EMPTY_MAPPING, "client_id": "id_klien", "transaction_date": "tanggal"}
    saved = {**chosen, "confirmed": True, "confirmed_at": "2026-11-04T03:10:00+00:00"}
    return {
        "mapping": saved if confirmed else None,
        "draft": {**chosen, "confirmed": False, "confirmed_at": None},
        "suggestions": [
            {"role": "client_id", "candidates": [{"column": "id_klien",
             "original_name": "ID Klien", "confidence": 0.8,
             "reasons": ["Header 'ID Klien' names an identifier of a client"]}]},
            {"role": "transaction_date", "candidates": [{"column": "tanggal",
             "original_name": "Tanggal", "confidence": 0.8, "reasons": ["Contains dates"]}]},
        ] + [{"role": r, "candidates": []} for r in ROLES
             if r not in ("client_id", "transaction_date")],
        "compatible_columns": {r: ["id_klien"] for r in ROLES} | {"transaction_date": ["tanggal"]},
        "warnings": [{"level": "warning", "code": "history_completeness",
                      "message": "New and Reactivated depend on full history."}],
        "unique_clients": 3 if confirmed else None,
    }  # fmt: skip


class FakeApi:
    """Minimal stand-in for ApiClient with configurable failures."""

    def __init__(
        self,
        datasets: list[dict[str, Any]] | None = None,
        down: bool = False,
        confirmed: bool = False,
        failing: set[str] | None = None,
    ) -> None:
        self.datasets = datasets if datasets is not None else [READY]
        self.down = down
        self.confirmed = confirmed
        self.failing = failing or set()
        self.confirm_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []

    def _check(self, endpoint: str = "") -> None:
        if self.down:
            raise ApiError("backend_unreachable", "The backend is not reachable.", 0)
        if endpoint in self.failing:
            raise ApiError("validation_error", f"{endpoint} failed.", 422)

    def health(self) -> dict[str, Any]:
        self._check("health")
        return {"status": "ok", "llm_configured": False, "model": None}

    def list_datasets(self) -> list[dict[str, Any]]:
        self._check("list_datasets")
        return self.datasets

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        self._check("get_dataset")
        return next(d for d in self.datasets if d["id"] == dataset_id)

    def columns(self, dataset_id: str) -> list[dict[str, Any]]:
        self._check("columns")
        return copy.deepcopy(COLUMNS)

    def preview(self, dataset_id: str, limit: int = 50) -> dict[str, Any]:
        self._check("preview")
        return {
            "columns": ["id_klien", "tanggal"],
            "rows": [["KL-0001", "2026-01-15"], ["KL-0002", None], ["KL-0003", "2026-02-01"]],
            "row_count": 3,
            "truncated": False,
            "elapsed_ms": 1.0,
        }

    def profile(self, dataset_id: str) -> dict[str, Any]:
        self._check("profile")
        return copy.deepcopy(PROFILE)

    def column_mapping(self, dataset_id: str) -> dict[str, Any]:
        self._check("column_mapping")
        return mapping_state(self.confirmed)

    def confirm_mapping(self, dataset_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        self._check("confirm_mapping")
        self.confirm_calls.append(mapping)
        self.confirmed = True
        return mapping_state(True)


# ------------------------------------------------------------- client analytics
def _period(year: int) -> dict[str, Any]:
    return {"label": str(year), "start": f"{year}-01-01", "end": f"{year + 1}-01-01"}


COVERAGE: dict[str, Any] = {
    "identity_column": "id_klien",
    "identity_kind": "client_id",
    "date_column": "tanggal",
    "department_column": "departemen",
    "rows": 12,
    "unique_clients": 5,
    "missing_id_rows": 1,
    "missing_date_rows": 0,
    "data_start": "2024-03-01",
    "data_end": "2026-08-08",
    "years": [2024, 2025, 2026],
    "warnings": [],
}


def retention(**overrides: Any) -> dict[str, Any]:
    """The plan fixture's 2025 vs 2026 result (hand-computed)."""
    result = {
        "previous": _period(2025),
        "current": _period(2026),
        "identity_column": "id_klien",
        "identity_kind": "client_id",
        "date_column": "tanggal",
        "previous_active": 3,
        "current_active": 4,
        "new": 1,
        "returning": 2,
        "reactivated": 1,
        "between_period": 0,
        "not_returned": 1,
        "existing_active": 3,
        "transactions_previous": 3,
        "transactions_current": 5,
        "repeat_transactions_previous": 0,
        "repeat_transactions_current": 1,
        "clients_with_repeat_previous": 0,
        "clients_with_repeat_current": 1,
        "retention_rate": 2 / 3,
        "new_client_rate": 0.25,
        "growth_rate": 1 / 3,
        "excluded_missing_id_rows": 1,
        "excluded_missing_date_rows": 0,
        "duplicate_rows_excluded": 1,
        "data_start": "2024-03-01",
        "data_end": "2026-08-08",
        "warnings": [
            {"level": "warning", "code": "history_completeness",
             "message": "New and Reactivated are classified from the history in this dataset."},
            {"level": "warning", "code": "incomplete_current_period",
             "message": "The data ends on 2026-08-08; the current period looks incomplete."},
        ],
        "definitions": {
            "retention_rate": "Returning ÷ active in 2025 (the previous period).",
            "new": "Active in 2026 and first seen in 2026.",
            "existing_active": "Overlaps returning + reactivated + between-period.",
        },
        "sql": ["WITH src AS (SELECT DISTINCT * FROM dataset) SELECT 1"],
    }  # fmt: skip
    result.update(overrides)
    return result


TREND: dict[str, Any] = {
    "granularity": "month",
    "result": {
        "columns": ["period", "active_clients", "new_clients", "transactions"],
        "rows": [["2026-06", 1, 0, 1], ["2026-07", 1, 0, 1], ["2026-08", 1, 0, 1]],
        "row_count": 3, "truncated": False, "elapsed_ms": 1.0,
    },
    "data_start": "2024-03-01", "data_end": "2026-08-08",
    "partial_first": False, "partial_last": True,
    "warnings": [{"level": "warning", "code": "partial_last_bucket",
                  "message": "The last month is only partly covered."}],
    "definitions": {"active_clients": "Unique clients with a transaction in the bucket."},
    "sql": ["SELECT 1"],
}  # fmt: skip
DEPARTMENTS: dict[str, Any] = {
    "department_column": "departemen",
    "previous": _period(2025),
    "current": _period(2026),
    "result": {
        "columns": ["department", "previous_active", "current_active", "returning",
                    "retention_rate", "transactions_current", "transactions_previous"],
        "rows": [["Audit", 1, 2, 1, 1.0, 2, 1], ["(no department)", 0, 1, 0, None, 1, 0]],
        "row_count": 2, "truncated": False, "elapsed_ms": 1.0,
    },
    "clients_in_multiple_departments": 1,
    "warnings": [{"level": "info", "code": "clients_in_multiple_departments",
                  "message": "1 client(s) used more than one department."}],
    "definitions": {}, "sql": [],
}  # fmt: skip
IDENTITY: dict[str, Any] = {
    "identity_column": "id_klien", "identity_kind": "client_id", "missing_id_rows": 1,
    "ids_differing_only_by_case_or_spacing": 0, "ids_with_multiple_names": None,
    "names_with_multiple_ids": None,
    "examples": {"columns": ["issue", "value", "variants"], "rows": [], "row_count": 0,
                 "truncated": False, "elapsed_ms": 0.0},
    "warnings": [],
}  # fmt: skip
CLIENTS_BY_CATEGORY = {
    "new": ["C"], "returning": ["A", "E"], "reactivated": ["D"], "between_period": [],
    "not_returned": ["B"], "existing_active": ["A", "D", "E"],
}  # fmt: skip


class AnalyticsFakeApi(FakeApi):
    """FakeApi plus the client-analytics endpoints."""

    def __init__(self, result: dict[str, Any] | None = None, mapped: bool = True, **kwargs):
        super().__init__(confirmed=mapped, **kwargs)
        self.mapped = mapped
        self.result = result or retention()
        self.coverage = copy.deepcopy(COVERAGE)
        self.compare_calls: list[tuple[dict, dict, bool]] = []
        self.client_calls: list[dict[str, Any]] = []

    def _mapping_required(self) -> None:
        if not self.mapped:
            raise ApiError(
                "mapping_required",
                "Confirm the column mapping (Data Sources → Column mapping) to enable client "
                "analytics.",
                409,
            )

    def analytics_coverage(self, dataset_id: str) -> dict[str, Any]:
        self._check("analytics_coverage")
        self._mapping_required()
        return copy.deepcopy(self.coverage)

    def analytics_compare(self, dataset_id, previous, current, dedupe=True) -> dict[str, Any]:
        self._check("analytics_compare")
        self._mapping_required()
        self.compare_calls.append((previous, current, dedupe))
        return copy.deepcopy(self.result)

    def analytics_clients(
        self, dataset_id, previous, current, category, offset=0, limit=100, dedupe=True
    ) -> dict[str, Any]:
        self._check("analytics_clients")
        self.client_calls.append({"category": category, "offset": offset, "limit": limit})
        ids = CLIENTS_BY_CATEGORY[category]
        rows = [[c, "2024-03-01", "2026-08-08", 1, 1] for c in ids[offset : offset + limit]]
        return {
            "category": category, "previous": previous, "current": current,
            "total": len(ids), "offset": offset, "limit": limit,
            "result": {"columns": ["client", "first_seen", "last_seen",
                                   "transactions_previous", "transactions_current"],
                       "rows": rows, "row_count": len(rows),
                       "truncated": offset + len(rows) < len(ids), "elapsed_ms": 1.0},
        }  # fmt: skip

    def analytics_trend(self, dataset_id: str, granularity="month", dedupe=True) -> dict[str, Any]:
        self._check("analytics_trend")
        return copy.deepcopy(TREND)

    def analytics_departments(self, dataset_id, previous, current, dedupe=True):
        self._check("analytics_departments")
        return copy.deepcopy(DEPARTMENTS)

    def analytics_identity(self, dataset_id: str) -> dict[str, Any]:
        self._check("analytics_identity")
        return copy.deepcopy(IDENTITY)


# ------------------------------------------------------------------- AI analyst
def analysis(question: str = "Klien per departemen?", **overrides: Any) -> dict[str, Any]:
    """An answered AnalysisResponse payload."""
    data = {
        "analysis_id": "a1", "conversation_id": "c1", "dataset_id": "d1",
        "question": question, "status": "answered", "language": "id",
        "answer": "Audit memiliki klien aktif terbanyak (34).",
        "kpis": [], "chart": {"type": "bar", "x": "departemen", "y": ["klien"], "color": None,
                              "title": "", "top_n": None},
        "table": {"columns": ["departemen", "klien"], "rows": [["Audit", 34], ["Pajak", 29]],
                  "row_count": 2, "truncated": False, "elapsed_ms": 3.0},
        "sql": "SELECT departemen, COUNT(DISTINCT id_klien) AS klien FROM dataset GROUP BY 1",
        "explanation": "Menghitung klien unik per departemen.",
        "assumptions": ["Semua tahun."],
        "insight": {"source": "llm", "language": "id", "summary": "Audit tertinggi (34).",
                    "facts": ["Audit: 34 klien."], "interpretations": [],
                    "recommendations": ["Bandingkan per tahun."], "limitations": [],
                    "ungrounded_numbers": [], "fallback_reason": None, "llm_request_id": "r1"},
        "retention": None, "warnings": [], "error_code": None, "repairs": 0,
        "usage": {"requests": 2, "input_tokens": 1200, "output_tokens": 150,
                  "est_cost_usd": None, "cost_complete": False, "request_ids": ["r0", "r1"]},
        "created_at": "2026-11-04T03:00:00+00:00",
    }  # fmt: skip
    data.update(overrides)
    return data


class AnalystFakeApi(AnalyticsFakeApi):
    """AnalyticsFakeApi plus the AI Analyst endpoints."""

    def __init__(self, llm: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.llm = llm
        self.ask_calls: list[tuple[str, str, str | None]] = []
        self.conversations: dict[str, dict[str, Any]] = {}
        self.next_answer: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        self._check("health")
        return {"status": "ok", "llm_configured": self.llm, "model": "m" if self.llm else None}

    def ask(self, dataset_id: str, question: str, conversation_id: str | None) -> dict[str, Any]:
        self._check("ask")
        self.ask_calls.append((dataset_id, question, conversation_id))
        conversation_id = conversation_id or f"c{len(self.conversations) + 1}"
        answer = analysis(question, conversation_id=conversation_id, dataset_id=dataset_id,
                          analysis_id=f"a{len(self.ask_calls)}")  # fmt: skip
        if self.next_answer:
            answer.update(self.next_answer)
        conversation = self.conversations.setdefault(
            conversation_id,
            {"id": conversation_id, "dataset_id": dataset_id, "title": question,
             "created_at": "2026-11-04T03:00:00+00:00", "analysis_count": 0, "analyses": []},
        )  # fmt: skip
        conversation["analyses"].append(answer)
        conversation["analysis_count"] += 1
        return answer

    def list_conversations(self, dataset_id: str) -> list[dict[str, Any]]:
        self._check("list_conversations")
        return [
            {k: v for k, v in c.items() if k != "analyses"}
            for c in self.conversations.values()
            if c["dataset_id"] == dataset_id
        ]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        self._check("get_conversation")
        if conversation_id not in self.conversations:
            raise ApiError("not_found", "This conversation was not found.", 404)
        return copy.deepcopy(self.conversations[conversation_id])

    def delete_conversation(self, conversation_id: str) -> None:
        self.conversations.pop(conversation_id, None)
