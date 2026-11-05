# First run against the real OpenAI API

- **Date:** 2026-11-05 · **LLM: real (gpt-6-luna, 2026-11-05)** · **Commit:** `f927d52`
- **Setup:** the user's local Windows run (`uvicorn backend.main:app --port 8000` +
  Streamlit), `OPENAI_MODEL=gpt-6-luna`, `LLM_MAX_OUTPUT_TOKENS=6000` (reasoning tokens
  count toward the cap), default reasoning effort, prices from the OpenAI pricing page in
  `config/pricing.json` ($0.10 input / $0.50 output per 1M tokens). Dataset:
  `sample_data/client_services.csv` (795 rows), suggested column mapping confirmed.
- **Method:** the user asked one question in the UI; the rest were sent to
  `POST /analyses/ask` with a small script. Every number was recomputed independently with
  pandas from the raw CSV. **This is a smoke test, not an evaluation** (the benchmark is
  Phase 7, Task 23).

| # | Question | Outcome | Numbers vs pandas | Insight | Time |
|---|---|---|---|---|---|
| 0 | Berapa klien unik per departemen di 2026? (mapping **not** confirmed) | clarification: which columns are client ID and date | — | — | 5.0 s |
| 1 | Berapa klien unik per departemen di 2026? | SQL, bar chart | equal (34/29/26/25/24) | **template fallback**: "2026" flagged as ungrounded | ≈ 9 s |
| 2 | Bandingkan jumlah klien 2025 dan 2026 | plain SQL per year: 91 → 103, "bertambah 12 klien" | equal | LLM, verified | 9.2 s |
| 3 | Layanan apa yang pendapatannya paling besar? | SQL: Implementasi ERP, Rp6.817.300.000 | equal | LLM, verified | 6.5 s |
| 4 | (follow-up) Bagaimana kalau hanya tahun 2025? | SQL with the 2025 filter: Rp2.345.400.000 | equal | **template fallback**: "2025" flagged | 6.9 s |
| 5 | Bagaimana performanya? | clarification (which metric and period) | — | — | 2.7 s |
| 6 | Hapus semua data | `unsupported`, nothing executed | — | — | 2.8 s |
| 7 | What was the total revenue per month in 2025? (English) | SQL, 12 months, English answer | equal (all 12 months) | LLM, verified | 7.4 s |

- **Usage:** 15 requests, 0 failed, 0 SQL repairs; 18,334 input / 3,209 output tokens;
  estimated cost **US$0.0034** in total (about US$0.0004–0.0005 per answered question).
  Times are end-to-end from the script, including SQL execution; one run each.
- **UI check:** the conversations render with chart, verified explanation, downloads, and
  per-answer usage; 0 browser errors (headless Chromium).

## Findings

1. **Misleading year comparison (question 2).** The model wrote plain SQL and compared the
   full year 2025 (91 clients) with 2026, whose data ends on 31 October (103). The answer
   "bertambah 12 klien" is not like-for-like; the year-to-date comparison used by Client
   Analytics is 87 → 103. The numbers themselves are correct; the comparison and the
   missing "2026 is partial" note are the problem. With the scripted fake LLM this question
   went to the retention templates, so the issue was invisible before.
2. **Grounding false positive (questions 1 and 4).** The insight check rejected LLM text
   that mentioned the year from the question / SQL filter (2026, 2025) because the year is
   not in the result table. The fallback is safe (a template summary), but 2 of 6 answered
   questions lost the written explanation.
3. Minor: a two-year comparison is drawn as a line chart.

Not observed: refusals, cut-off answers, invalid structured output, retries, rate limits.
