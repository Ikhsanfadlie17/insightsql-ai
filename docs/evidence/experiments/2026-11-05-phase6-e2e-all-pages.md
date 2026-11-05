# Phase 6 — all six pages, desktop and phone, with the real backend and a scripted fake LLM

- **Date:** 2026-11-05 · **Machine:** Windows 11, Python 3.13, Streamlit 1.66.1, Playwright
  1.56.1 (Chromium 141) · **Commits tested:** `df17da2` (final run), earlier runs on the
  intermediate commits · **LLM: mock** (`scripts/e2e/fake_llm_app.py`; OpenAI was never
  contacted)
- **What is real:** Streamlit UI, FastAPI, SQL validator, sandboxed DuckDB worker, SQLite
  history and usage records, settings endpoint.
- **What is fake:** LLM plans and insight texts (fixed by keyword) and their token counts
  (500 input / 60 output per call, set by the script).
- **Steps:** import `sample_data/client_services.csv` through the API, confirm the suggested
  column mapping, ask three questions (department question, 2025 vs 2026 comparison, an
  ambiguous question), then
  `node scripts/e2e/all_pages.js docs/evidence/screenshots/phase6 http://localhost:8766`.

| Check (per page, at 1440 px and 390 px width) | Result |
|---|---|
| Marker text present, no Streamlit exception, no "Page not found" | 12 / 12 pass |
| No content wider than the main column, no sideways page scroll | 12 / 12 pass |
| Browser page errors | 0 (desktop), 0 (phone) |
| Settings: 5 AI requests, 0 failed, 2.500 / 300 tokens, cost "N/A" (no prices in `config/pricing.json`) | as expected |

**Problems found only by looking at the screenshots or the running app (not by AppTest),
all fixed before the final run:**
1. The delete confirmation on Data Sources was hidden: Streamlit resets an expander whose
   `expanded` argument changes between reruns. The controls are now inline.
2. At phone width, the Settings tables were clipped by their bordered card (the env var
   column was cut off). Markdown tables now scroll inside the card; `all_pages.js` checks
   element widths against the main column.
3. Profile statistics (Min/Max/Mean/Median) were truncated and in English number format
   (`4,000,(`); they now use the Indonesian format and fixed column widths.
4. The script itself opened `/overview`, which is not a route (the default page is `/`);
   the script now fails on Streamlit's "Page not found" dialog.

Manual check on 2026-11-05 with the real backend and no LLM: Settings showed "Not
configured — missing OPENAI_MODEL" and "API key: Set (hidden)" for the local environment;
a dataset imported with "Tanggal Layanan" kept as text showed the new profile warnings;
rename and delete worked, and the dataset's DuckDB file was removed.

Screenshots: `../screenshots/phase6/*-desktop.png`, `../screenshots/phase6/*-mobile.png`.
