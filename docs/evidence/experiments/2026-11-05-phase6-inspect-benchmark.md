# Phase 6 — upload inspection time on a multi-sheet workbook (review finding H1)

- **Date:** 2026-11-05 · **Machine:** Windows 11 laptop, Python 3.13, python-calamine 0.9.1
- **LLM: none** (inspection never calls the LLM)
- **Why:** the Phase 6 review found that column-type hints at inspection re-parsed the whole
  workbook once per sheet (about S² sheet parses).
- **Input:** a generated workbook, 8 sheets × 40,000 rows × 8 columns, 10.6 MB (openpyxl,
  write-only). One run per version; not a benchmark, only an order-of-magnitude check.
- **Command:** a throwaway script that times `list_sheets(path)` (the inspection cost before
  Phase 6) and `DatasetService.inspect_upload(...)`; the old version was run from a
  temporary `git worktree` of `df17da2`, the new one from `090bacc`.

| Version | `list_sheets` only | full inspection | detected types (sheet 1) |
|---|---|---|---|
| `df17da2` (before fix) | 2.1 s | 20.9 s | ID Klien text, Tanggal date, Nilai numeric, others text |
| `090bacc` (after fix) | 1.9 s | 4.3 s | identical |

The fix samples every sheet with one `pandas.read_excel(sheet_name=None, nrows=501)` call.
Inspection is still about 2× the pre-Phase-6 cost because calamine parses each sheet in full
even when only 501 rows are requested. The frontend timeout for inspection was raised from
30 s to 120 s. A 50 MB workbook (the upload limit) was not measured.
