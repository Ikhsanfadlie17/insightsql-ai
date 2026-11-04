# Phase 5 — independent recomputation of client-retention metrics

- **Date:** 2026-11-04 · **Commit tested:** `38ca2d2` · **LLM:** none (deterministic)
- **Question:** do the analytics SQL templates produce the same numbers as an independent
  calculation from the raw file?
- **Data:** `sample_data/client_services.csv` (synthetic; 795 rows, 150 client IDs,
  dates `DD/MM/YYYY` 2023-01-03 … 2026-10-31, 7 rows without ID, 15 exact duplicate rows).
- **Method:** a separate agent, without reading `analytics_service.py`,
  `tests/ground_truth.py`, or the analytics tests, computed the metrics from the raw CSV
  twice (pure Python with `csv` + sets; DuckDB SQL with `strptime '%d/%m/%Y'`). Application
  numbers came from the real path: `inspect_upload → ingest → MappingService.confirm →
  AnalyticsSource.for_dataset → compare_periods / client_trend`. Definitions:
  ARCHITECTURE §8.
- **Result:** **0 differences.** 15 metrics × 4 comparisons, 4 repeat metrics × 4
  comparisons, and all 46 monthly trend rows (active, new, transactions) are identical; the
  three partition invariants hold in all comparisons.

| Metric | 2025 vs 2026 | YTD to 31 Oct | 2024 vs 2026 | 2023 vs 2024 |
|---|---|---|---|---|
| previous_active | 91 | 87 | 52 | 41 |
| current_active | 103 | 103 | 103 | 52 |
| new | 30 | 30 | 30 | 29 |
| returning | 57 | 55 | 37 | 23 |
| reactivated | 16 | 16 | 6 | 0 |
| between_period | 0 | 2 | 30 | 0 |
| not_returned | 34 | 32 | 15 | 18 |
| existing_active | 73 | 73 | 73 | 23 |
| transactions_previous | 232 | 189 | 136 | 129 |
| transactions_current | 276 | 276 | 276 | 136 |
| excluded_missing_id_rows | 3 | 3 | 0 | 4 |
| duplicate_rows_excluded | 12 | 12 | 11 | 3 |
| retention_rate | 0.62637 | 0.63218 | 0.71154 | 0.56098 |
| new_client_rate | 0.29126 | 0.29126 | 0.29126 | 0.55769 |
| growth_rate | 0.13187 | 0.18391 | 0.98077 | 0.26829 |

Spot checks: YTD between-period = KL-0102 (first seen 2025-11-13) and KL-0106
(2025-12-24), both in the gap 2025-11-01 … 2025-12-31.

**Not covered by this data:** IDs with surrounding whitespace or case variants, rows
without a date — these are covered by hand-computed fixtures in
`tests/services/test_analytics_service.py`.

**Single timing (not a benchmark):** one `compare_periods` call on the sample took
≈ 276 ms wall-clock, most of it the isolated worker process start-up (dev container).
