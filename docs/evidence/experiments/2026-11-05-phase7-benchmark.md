# Phase 7 — Text-to-SQL benchmark with gpt-6-luna (two runs)

- **Date:** 2026-11-05 · **LLM: real (gpt-6-luna, 2026-11-05)** · SQL prompt
  `2026-11-05.sql-v2`, insight model gpt-6-luna, default reasoning effort,
  `LLM_MAX_OUTPUT_TOKENS=6000`.
- **Setup:** `python -m eval.run_eval --model gpt-6-luna` from the repository root on the
  user's Windows 11 laptop; a temporary data folder; the sample CSV imported with defaults
  and the suggested mapping confirmed; each question in a new conversation.
- **Raw results:** `eval/results/20261105T030657Z.{json,md}` (run 1) and
  `eval/results/20261105T033221Z.{json,md}` (run 2). Summaries in both files were later
  recomputed from the stored records when metric definitions changed; the reason is in each
  file's metadata. Grades were not edited.

| | Run 1 | Run 2 |
|---|---|---|
| Code · question file · grader | `de6db38` · v1 · first version | `638f484` · v2 · after the Phase 7 review |
| Correct, data questions | 39/40 (cpx-04 wrong) | 39/40 (new-05 not answered: clarification) |
| Correct, clarify/reject | 10/10 | 10/10 |
| SQL success after repair / first attempt | 30/30 / 29/30 | 30/30 / 29/30 |
| Latency avg / median | 6.4 s / 6.4 s | 6.7 s / 6.4 s |
| Requests · tokens in / out · cost | 91 · 119,265 / 20,495 · US$0.0222 | 90 · 118,143 / 20,331 · US$0.0141 |

Between the runs: the grader was made stricter (review H1–H3, M5) — re-grading run 1's
stored SQL with it changed 0 grades — and four questions were reworded (v2, see the file
header). The SQL prompt and the model were not changed.

Observations: every retention / new-vs-existing question that was answered went to the
deterministic engine; cpx-02 needed one automatic SQL repair in both runs; no request
failed, was cut off, or was retried. Total API spend for Phase 7 benchmarking:
US$0.0363 (plus US$0.0078 for the morning smoke test).
