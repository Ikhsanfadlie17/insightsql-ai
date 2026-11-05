# Evaluation

Measured results only. Every number below is copied from a file in this repository (the
file is named next to it); nothing is estimated or projected. Targets are from PRD §9.
Read §4 "Limitations" before quoting any figure.

## 1. Text-to-SQL benchmark (PRD §9, plan Task 23)

**Set:** `eval/questions.yaml`, 50 questions — the 10 PRD categories × 5, 30 in Indonesian
and 20 in English — on `sample_data/client_services.csv` (795 rows, 2023-01-03 to
2026-10-31) with the suggested column mapping confirmed. References never come from the
model:

| Expected | Graded as correct when |
|---|---|
| `result` (30) | The model's result contains every column of the hand-written reference result (matched by values; names and order do not matter; extra model columns are allowed, missing ones are wrong), row by row, order-insensitive. Numbers: within 1e-6, or the reference rounded to the model's decimals with ≤ 0.5% relative error. |
| `retention` (10) | The deterministic retention engine, run for the stated periods, gives the same value(s) as the answer: from the engine if the model routed the question there, or from a one-row SQL result (a rate may be written × 100). |
| `clarify` (5) | Status `clarification`; nothing executed. |
| `reject` (5) | Status `unsupported`, or every generated query blocked by the SQL validator; nothing executed. |

**Runs** (both: LLM: real (gpt-6-luna, 2026-11-05), SQL prompt `2026-11-05.sql-v2`, one run
each, Windows 11 laptop on a home connection):

| | Run 1 | Run 2 (reported result) |
|---|---|---|
| Result file | `eval/results/20261105T030657Z` | `eval/results/20261105T033221Z` |
| Code / question file | `de6db38` / v1 | `638f484` (clean tree) / v2 |
| Grader | first version; re-graded with the final grader: 0 grades changed (`eval/regrade.py`) | final (after the Phase 7 review) |

| Metric (PRD §9) | Target | Run 1 | Run 2 |
|---|---|---|---|
| SQL execution success rate (after automatic repair) | ≥ 95% | 100% (30/30) | **100%** (30/30) |
| … on the first attempt | — | 96.7% (29/30) | 96.7% (29/30) |
| Query result accuracy, all data questions | ≥ 90% | 97.5% (39/40) | **97.5%** (39/40) |
| … answers from model-written SQL | — | 96.7% (29/30) | 100% (30/30) |
| … answers routed to the retention engine | — | 100% (10/10) | 100% (9/9) |
| … data questions not answered | — | none | new-05 (asked a clarification) |
| … excluding the 5 smoke-test-overlap questions (§4) | — | 100% (37/37) | 97.3% (36/37) |
| Clarify/reject accuracy ("semantic accuracy" in this project) | — | 100% (10/10) | 100% (10/10) |
| Error recovery | — | 1 case, repaired | 1 case, repaired |
| Latency per question, average / median | — | 6.4 s / 6.4 s | 6.7 s / 6.4 s |
| LLM requests · tokens in / out | — | 91 · 119,265 / 20,495 | 90 · 118,143 / 20,331 |
| Estimated API cost for 50 questions | — | US$0.0222 | US$0.0141 |
| SQL that breaks the security rules executed | none | none | **none** |

"Semantic accuracy" in PRD §9 is measured here as the right behaviour for ambiguous and
invalid questions. Answers routed to the retention engine test intent routing and period
extraction; the numbers themselves come from deterministic templates (Phase 5), not from
model-written SQL. The single automatic repair in each run is cpx-02: too few cases for a
recovery *rate*.

**Wrong answers, with causes:**

- Run 1, **cpx-04** ("for each year, total revenue ÷ unique clients"): the model limited
  every year to January–October and said so in its assumptions; the reference uses whole
  years. Caused by rule 11 of SQL prompt v2 (compare the same span when the data ends
  mid-year), applied to a per-year breakdown. In question file v2 the wording says
  "calendar year"; run 2 answered it correctly. The prompt was **not** changed between the
  runs.
- Run 2, **new-05**: the model asked whether "new client" in the 2025 comparison period has
  the same definition. The v2 wording added "Bandingkan dengan periode Januari–Juni 2025",
  which does leave that open; this is a weakness of the question, but it is still graded
  wrong. It is not reworded and re-run here (that would be tuning on the test set).

Security in both runs: delete, `DROP TABLE`, a prompt injection asking for the API key, an
off-topic question, and an update request were all refused before any SQL ran.

**Reproduce** (needs `OPENAI_API_KEY` and network; costs real money):

```bash
python -m eval.run_eval --model gpt-6-luna
python -m eval.run_eval --only cpx-04,new-05          # a subset
python -m eval.regrade eval/results/<run>.json        # re-grade stored SQL, no LLM calls
```

Without a key, `python -m pytest tests/eval` checks that the file has ≥ 50 questions, ≥ 4
per category and ≥ 20 in Indonesian; that every reference SQL passes the validator, runs,
returns no NULL, and has no tie at a `LIMIT` cut; that a scripted model returning the
references scores 100%; and that the grader rejects missing columns, coarse "rounding",
numbers found in large tables, and accepts validator-blocked writes as refusals.

## 2. Performance (plan Task 24)

Source: `docs/evidence/experiments/2026-11-05-phase7-perf-300k.json` (+ `.md`). Command
`python -m pytest -m perf -s tests/perf`; Windows 11, Python 3.13.17, Intel 64 laptop;
synthetic CSV of 300,000 rows × 7 columns (22.5 MB, 20,000 client IDs, 2023-01-01 to
2026-10-31); one run through the real API and the sandboxed query worker. **LLM: none.**

| Step | Seconds |
|---|---|
| Inspect upload (incl. column-type hints from 500 rows) | 0.27 |
| Import: load, type inference, DuckDB write, profile | 10.56 |
| Profile (recomputed in DuckDB) | 0.17 |
| Confirm column mapping (incl. unique-client count) | 0.11 |
| Retention comparison 2025 vs 2026 | 0.41 |
| Client list (first 100 returning clients) | 0.35 |
| Monthly trend | 0.45 |
| Group-by query in the sandbox worker | 0.25 |

Import runs once per file, in the background. Analytics stay far below the 10-second query
timeout at this size. Not measured: files near the 50 MB limit, multi-sheet workbooks of
this size (an 8-sheet workbook is in
`docs/evidence/experiments/2026-11-05-phase6-inspect-benchmark.md`), concurrent users,
Linux or Docker.

## 3. Security tests (plan Task 24)

`python -m pytest tests/security` (no key needed):

- SQL validator against the attack corpus (`tests/security/attack_corpus.yaml`, 102
  cases): writes, DDL and session statements, multiple statements and smuggling, file /
  external / system functions, tables and catalogs outside the dataset, resource
  exhaustion, malformed input, and syntactically valid bypass attempts — all rejected
  before execution.
- Upload names with path traversal (`../`, `..\`, absolute paths, encoded dots) stay inside
  the upload folder; oversized and disguised files (CSV named `.xlsx`, executables) are
  refused and not kept.
- The API key never appears in any route's response (all 29 operations in the OpenAPI
  schema, with real and unknown IDs) or in the logs, including when an error message
  carries it. Configured secrets are redacted from log messages, tracebacks, stack info,
  error responses, analysis answers, usage records, and failed-import messages. The sweep
  found the gap before the redaction existed; disabling either redaction layer makes it
  fail again (checked in the Phase 7 review).
- The sweep sends `{}` as the body, so body-required routes are swept on their validation
  path; their success paths are exercised by the earlier calls in the same test.

## 4. Limitations

- **Small sample, one model, synthetic data.** 50 questions; one wrong answer moves
  accuracy by 2.5 points. Each run was made once; LLM output varies between runs (run 1 and
  run 2 differ on which question failed). The dataset is synthetic.
- **Same author.** Questions, references, and grader were written by the developer of the
  system.
- **Overlap with the smoke test.** SQL prompt v2 was written the same day after a smoke test
  (`docs/evidence/experiments/2026-11-05-first-real-api.md`) that contained questions equal
  or close to five benchmark questions: amb-01 (verbatim), dat-01 (revenue per month in
  2025), inv-01 (delete data), ret-05 (client comparison 2025 vs 2026), and cpx-04 (its
  failure mode, unique clients per year, was seen there). Scores without these five are
  reported above.
- **Question file v2 was edited after the review**, which had seen run 1's results; the
  edits remove ambiguity and a zero-valued reference and are listed at the top of the
  file. Run 1 used v1.
- **Not graded:** the wording of written explanations (checked for grounded numbers at run
  time, not scored here) and chart choice.
- Latency includes two LLM calls per answered question over a home connection; it is not
  a server benchmark.
