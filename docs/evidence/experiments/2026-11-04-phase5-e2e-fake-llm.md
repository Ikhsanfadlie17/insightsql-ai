# Phase 5 — end-to-end run with the real backend and a scripted fake LLM

- **Date:** 2026-11-04 · **Commits tested:** `1fe5e2f`/`38ca2d2`, re-run after the review fixes · **LLM: mock** (scripted
  fake in `scripts/e2e/fake_llm_app.py`; OpenAI was never contacted)
- **What is real:** Streamlit UI, FastAPI, SQL validator, sandboxed DuckDB worker, analytics
  templates, insight verification, SQLite history, usage records.
- **What is fake:** the LLM plans and insight texts (fixed by keyword) and their token
  counts (fixed at 500 input / 60 output per call by the script).
- **Steps:** import the sample CSV, confirm the mapping, then
  `node scripts/e2e/client_analytics.js` and `node scripts/e2e/ai_analyst.js`.

| Check | Result |
|---|---|
| Client Analytics shows KPIs, denominators, Existing Active separately, warnings | pass; 0 browser errors |
| Default comparison is year-to-date (data ends 31 Oct) | pass (87 → 103, retention 63,2%) |
| AI: department question → validated SQL, bar chart, verified insight | pass |
| AI: "Bandingkan klien 2025 dengan 2026" → retention templates; KPIs equal the Client Analytics page (87/103/55/30/16/2/32/73) | pass |
| AI: ambiguous question → clarification, nothing executed | pass |
| AI: `DROP TABLE` plan (3 attempts) → rejected by the validator, table intact, error shown | pass |
| Usage recorded | 8 requests (2 + 2 + 1 + 3), all tied to their analysis; cost "not available" because `config/pricing.json` has no prices |

**Bug found only by the re-run (not by AppTest):** with older conversations of the same
title listed, follow-up questions went into an *older* conversation, because Streamlit
matches a selectbox value by its label. Fixed with unique labels (title · time · id) and
by selecting the new conversation through Session State before the widget is created;
regression tests added. Re-run after the fix: the 4 questions stay in one conversation.

Screenshots (final run): `../screenshots/phase5-*.png`.
