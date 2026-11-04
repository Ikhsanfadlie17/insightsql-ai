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
