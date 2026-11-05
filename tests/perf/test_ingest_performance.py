"""Task 24: performance on 300,000 rows (marker ``perf``; excluded from the default run).

    python -m pytest -m perf -s tests/perf

Generates a synthetic CSV shaped like the sample data, then times each step through the
real API (sandboxed query worker included). Prints the timings and writes them to
``$PERF_OUT`` (JSON) when set. Bounds are generous: the numbers recorded in
EVALUATION.md are the measured ones, not these limits.
"""

from __future__ import annotations

import json
import os
import platform
import random
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from backend.services.sql_validator import TableSchema, validate_sql

pytestmark = pytest.mark.perf

ROWS = 300_000
SERVICES = {
    "Audit": ["Audit Internal", "Audit Keuangan"],
    "Konsultasi Bisnis": ["Konsultasi Strategi", "Riset Pasar"],
    "Pelatihan": ["Pelatihan Karyawan", "Workshop Manajemen"],
    "Perpajakan": ["Pelaporan SPT", "Konsultasi Pajak"],
    "Teknologi Informasi": ["Implementasi ERP", "Audit Sistem TI"],
}
PERIODS = {
    "previous": {"label": "2025", "start": "2025-01-01", "end": "2026-01-01"},
    "current": {"label": "2026", "start": "2026-01-01", "end": "2027-01-01"},
}


def _write_csv(path: Path, rows: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    start, days = date(2023, 1, 1), (date(2026, 10, 31) - date(2023, 1, 1)).days
    departments = list(SERVICES)
    statuses = ["Selesai"] * 8 + ["Proses", "Dibatalkan"]
    with path.open("w", encoding="utf-8", newline="") as out:
        out.write("ID Klien,Nama Klien,Tanggal Layanan,Departemen,Jenis Layanan,"
                  "Pendapatan (Rp),Status\n")  # fmt: skip
        for _ in range(rows):
            client = rng.randint(1, 20_000)
            day = start + timedelta(days=rng.randint(0, days))
            dept = rng.choice(departments)
            out.write(
                f"KL-{client:05d},Klien {client},{day:%d/%m/%Y},{dept},"
                f"{rng.choice(SERVICES[dept])},{rng.randint(1, 2000) * 100_000},"
                f"{rng.choice(statuses)}\n"
            )


def _timed(label: str, timings: dict[str, float], call):
    started = time.perf_counter()
    result = call()
    timings[label] = round(time.perf_counter() - started, 2)
    return result


def test_300k_rows(settings: Settings, tmp_path: Path) -> None:
    csv = tmp_path / "perf.csv"
    _write_csv(csv, ROWS)
    content = csv.read_bytes()
    timings: dict[str, float] = {}

    with TestClient(create_app(settings)) as client:
        upload = _timed("inspect (incl. type hints)", timings, lambda: client.post(
            "/datasets/inspect", files={"file": ("perf.csv", content)})).json()  # fmt: skip
        created = _timed("import: load + types + DuckDB + profile", timings, lambda: client.post(
            "/datasets", json={"upload_id": upload["upload_id"]})).json()  # fmt: skip
        dataset_id = created["id"]
        record = client.get(f"/datasets/{dataset_id}").json()
        assert record["status"] == "ready", record
        assert record["row_count"] == ROWS

        service = client.app.state.dataset_service
        _timed("profile (recomputed)", timings, lambda: _recompute_profile(service, dataset_id))
        mapping = {
            "client_id": "id_klien", "client_name": "nama_klien",
            "transaction_date": "tanggal_layanan", "department": "departemen",
            "service_type": "jenis_layanan", "revenue": "pendapatan_rp", "status": "status",
        }  # fmt: skip
        _timed("confirm mapping (incl. unique clients)", timings, lambda: client.put(
            f"/datasets/{dataset_id}/column-mapping", json=mapping).raise_for_status())  # fmt: skip
        base = f"/datasets/{dataset_id}/client-analytics"
        compare = _timed("retention compare 2025 vs 2026", timings, lambda: client.post(
            f"{base}/compare", json=PERIODS)).json()  # fmt: skip
        assert compare["current_active"] > 0
        _timed("client list (first 100 returning)", timings, lambda: client.post(
            f"{base}/clients", json={**PERIODS, "category": "returning", "limit": 100}
        ).raise_for_status())  # fmt: skip
        _timed("monthly trend", timings, lambda: client.get(f"{base}/trend").raise_for_status())
        schema = TableSchema(columns={c.name: c.logical_type for c in service.columns(dataset_id)})
        query = validate_sql(
            "SELECT departemen, COUNT(DISTINCT id_klien) AS klien, SUM(pendapatan_rp) AS total "
            "FROM dataset GROUP BY departemen ORDER BY total DESC",
            schema,
        )
        executor = client.app.state.text_to_sql._executor  # noqa: SLF001 - timing the sandbox
        result = _timed("AI-style query in the sandbox worker", timings, lambda: executor.execute(
            service.duckdb_path(dataset_id), query))  # fmt: skip
        assert result.row_count == 5

    report = {
        "rows": ROWS,
        "csv_mb": round(len(content) / 1_048_576, 1),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "timings_s": timings,
    }
    print("\n" + json.dumps(report, indent=2))
    if os.environ.get("PERF_OUT"):
        Path(os.environ["PERF_OUT"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert all(seconds < 180 for seconds in timings.values()), timings


def _recompute_profile(service, dataset_id: str):
    from backend.services.data_profiler import profile_dataset

    return profile_dataset(service.duckdb_path(dataset_id), service.columns(dataset_id))
