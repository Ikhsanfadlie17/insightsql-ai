# Evaluation

Measured results only. Every number below comes from a file in this repository; nothing is
estimated or projected. Targets are from PRD §9. Read "Limitations" before quoting any
figure.

## 1. Text-to-SQL benchmark (PRD §9, plan Task 23)

**Run:** `eval/results/20261105T030657Z.{json,md}` · **LLM: real (gpt-6-luna, 2026-11-05)**
· SQL prompt `2026-11-05.sql-v2` · code at commit `de6db38` (see the note in the result
file) · Windows 11 laptop, one run.

**Set:** `eval/questions.yaml`, 50 questions, the 10 PRD categories × 5, 30 in Indonesian
and 20 in English, on `sample_data/client_services.csv` (795 rows, 2023-01-03 to
2026-10-31) with the suggested column mapping confirmed. References never come from the
model:

| Expected | How it is graded |
|---|---|
| `result` (30) | Hand-written reference SQL runs on the same table. Rows must match, order-insensitive; columns are matched by their values (names and order do not matter, and extra columns on either side are allowed if every column of the narrower result appears in the wider one); numbers within 1e-6, or equal to the reference rounded to the model's decimals. |
| `retention` (10) | The analytics engine computes the metric for the stated periods; the answer must hold the same value(s), from the engine or anywhere in a SQL result table. |
| `clarify` (5) / `reject` (5) | Status `clarification` / `unsupported` (or a validator rejection); nothing executed. |

| Metric (PRD §9) | Target | Measured |
|---|---|---|
| SQL execution success rate | ≥ 95% | **100%** (30 of 30 questions answered with model SQL) |
| Query result accuracy | ≥ 90% | **97.5%** (39 of 40 result/retention questions) |
| Semantic accuracy (clarify / reject) | — | **100%** (10 of 10) |
| Error recovery rate | — | 1 of 1 (one failed query repaired automatically, cpx-02) — too few cases to be a rate |
| Latency per question | — | average 6.4 s, median 6.4 s, range 1.4–16.0 s (end to end, incl. SQL and insight) |
| Tokens | — | 91 LLM requests, 119,265 input / 20,495 output |
| Estimated API cost | — | **US$0.0222** for 50 questions (prices in `config/pricing.json`, 2026-11-05) |
| Security: SQL that breaks the rules executed | none | none: 5 of 5 invalid requests (delete, `DROP TABLE`, prompt injection asking for the key, off-topic, update) were refused before any SQL ran |

Per category: 5/5 in every category except complex business analytics (4/5). By language:
Indonesian 30/30, English 19/20. All 10 retention / new-vs-existing questions were routed
to the deterministic retention engine (none was answered with model-written SQL).

**The one wrong answer (cpx-04, English):** "For each year, what is the total revenue
divided by the number of unique clients in that year?" The model restricted every year to
January–October and said so in its assumptions; the reference uses whole years. This comes
from rule 11 of SQL prompt v2 (compare the same span when the data ends mid-year), which was
added earlier the same day and is applied here to a per-year breakdown where the question
asks for calendar years. It was **not** tuned away and re-run: changing the prompt after
seeing the test set and reporting the new score would overstate accuracy. A fix belongs in
a later prompt version, measured on new questions.

**Reproduce** (needs `OPENAI_API_KEY`, network; costs real money):

```bash
python -m eval.run_eval --model gpt-6-luna
python -m eval.run_eval --only cpx-04,dat-02     # a subset
```

Checks that run without a key (`python -m pytest tests/eval`): the question file has ≥ 50
questions, ≥ 4 per category and ≥ 20 in Indonesian; every reference SQL passes the
validator, runs, returns no NULL, and has no tie at a `LIMIT` cut; a scripted model that
returns the references scores 100% and wrong answers are caught.

## 2. Performance (plan Task 24)

**Run:** `python -m pytest -m perf -s tests/perf` on 2026-11-05 · Windows 11, Python
3.13.17, Intel 64 (family 6 model 183) laptop · synthetic CSV, 300,000 rows × 7 columns,
22.5 MB, 20,000 client IDs, dates 2023-01-01 to 2026-10-31 · one run, through the real API
and the sandboxed query worker. **LLM: none.**

| Step | Seconds |
|---|---|
| Inspect upload (incl. column-type hints from 500 rows) | 0.27 |
| Import: load, type inference, DuckDB write, profile | 10.56 |
| Profile (recomputed in DuckDB) | 0.17 |
| Confirm column mapping (incl. unique-client count) | 0.11 |
| Retention comparison 2025 vs 2026 | 0.41 |
| Client list (first 100 returning clients) | 0.35 |
| Monthly trend | 0.45 |
| AI-style query (group by department) in the sandbox worker | 0.25 |

Import is the only step that takes seconds; it runs once per file, in the background.
Analytics stay below the 10-second query timeout by a wide margin at this size. Not
measured: files near the 50 MB upload limit, multi-sheet workbooks of this size (see
`docs/evidence/experiments/2026-11-05-phase6-inspect-benchmark.md` for an 8-sheet
workbook), concurrent users, Linux or Docker.

## 3. Security tests (plan Task 24)

`python -m pytest tests/security` (no key needed):

- SQL validator against the attack corpus (`tests/security/attack_corpus.yaml`, 102
  cases): writes, DDL and session statements, multiple statements and smuggling, file /
  external / system functions, tables and catalogs outside the dataset, resource
  exhaustion, malformed input, and syntactically valid bypass attempts — all rejected
  before execution.
- Upload names with path traversal (`../`, `..\`, absolute paths, encoded dots) stay inside
  the upload folder; oversized files and disguised files (CSV named `.xlsx`, executables)
  are refused and not kept.
- The API key never appears in any route's response (every path in the OpenAPI schema,
  with real and unknown IDs) or in the logs, including when an error message carries it:
  configured secrets are redacted from log records, tracebacks, error responses, analysis
  answers, and usage records. The sweep found that gap before the redaction existed.

## 4. Limitations

- **One run, one model, one dataset.** LLM output varies between runs; 50 questions is a
  small sample (one wrong answer moves accuracy by 2.5 points). The dataset is synthetic.
- **Same author.** The questions and references were written by the developer of the
  system. SQL prompt v2 was changed earlier the same day after a smoke test with similar
  questions (`docs/evidence/experiments/2026-11-05-first-real-api.md`); for example ret-05
  resembles a smoke-test question. Scores on unseen users' questions may be lower.
- **Lenient but explicit grading.** Extra or missing columns are accepted when the values
  match, and rounding to fewer decimals is accepted. A model answer that is "right for a
  different reading of the question" is graded wrong (cpx-04).
- **Not graded:** the wording of the written explanations (they are checked for grounded
  numbers at run time, not scored here) and chart choice.
- Latency includes two LLM calls per answered question on a home connection; it is not a
  server benchmark.
