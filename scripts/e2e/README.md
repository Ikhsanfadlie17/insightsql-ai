# End-to-end scripts

Run the real backend and frontend, then drive the browser with Playwright. The AI Analyst
script uses **`fake_llm_app.py`: the real backend with a scripted fake LLM** (`LLM: mock`).
It checks the wiring (UI → API → validator → sandboxed DuckDB → history), not model quality,
and it never contacts OpenAI.

```bash
export DATA_DIR=/tmp/insightsql-e2e
uvicorn --app-dir scripts/e2e fake_llm_app:app --port 8000      # or backend.main:app without AI
BACKEND_URL=http://localhost:8000 streamlit run frontend/app.py --server.headless true --server.port 8501
```

Import `sample_data/client_services.csv` and confirm the column mapping (Data Sources page
or `PUT /datasets/{id}/column-mapping`), then:

```bash
node scripts/e2e/client_analytics.js /tmp/shots   # KPIs, warnings, charts, departments
node scripts/e2e/ai_analyst.js /tmp/shots         # SQL answer, retention, clarification, DROP rejected
node scripts/e2e/all_pages.js /tmp/shots          # all six pages, desktop + phone width
```

The scripts print `check true/false` lines and the number of browser page errors.

`all_pages.js` fails a page when its marker text is missing, Streamlit shows an exception
or "Page not found", or content is wider than the main column. Ask a few questions first
so AI Analyst, Query History, and Settings have content.

On Windows without a global Playwright: install it in any scratch folder
(`npm install playwright`) and run the scripts with `NODE_PATH=<that folder>/node_modules`.
