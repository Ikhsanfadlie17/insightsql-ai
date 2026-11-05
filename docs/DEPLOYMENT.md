# Deployment

Two processes: the **FastAPI backend** (port 8000; owns the data, the API key, and every
query) and the **Streamlit frontend** (port 8501; holds no secrets). Data lives in
`DATA_DIR` (see [DATABASE.md](DATABASE.md)).

## 1. Configuration

All settings are environment variables, usually in a `.env` file in the project folder
(copy [`.env.example`](../.env.example)). The backend reads them **at start**: restart it
after a change. A variable set in the operating system overrides the same name in `.env`.
The full list with defaults is in the [README](../README.md#configuration). The essentials:

| Variable | Needed for | Notes |
|---|---|---|
| `OPENAI_API_KEY` | AI Analyst | Backend only; never returned by the API or written to logs. |
| `OPENAI_MODEL` | AI Analyst | Any model your account can use (nothing is hard-coded). Tested: `gpt-6-luna`. |
| `LLM_MAX_OUTPUT_TOKENS` | reasoning models | Reasoning tokens count toward this cap; `6000` was used with `gpt-6-luna` (default 1500). |
| `config/pricing.json` | cost estimates | USD per 1M tokens, from the provider's pricing page; models without a price show "N/A". |
| `BACKEND_URL` | frontend | Where the frontend finds the API (`http://localhost:8000` locally, `http://backend:8000` in Compose). |

Without a key and model the app still works for upload, profiling, column mapping, Client
Analytics, Query History, and export; the AI Analyst says what is missing.

## 2. Local (development)

Requirements: Python 3.13, Git. Windows, Linux, and macOS work; see the note on Windows
below.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env               # Windows: copy .env.example .env — then edit it
uvicorn backend.main:app --port 8000
```

In a second terminal (same folder, venv active):

```bash
BACKEND_URL=http://localhost:8000 streamlit run frontend/app.py
# Windows PowerShell: $env:BACKEND_URL = "http://localhost:8000"; streamlit run frontend/app.py
```

Open http://localhost:8501.

**Windows note.** The query worker's memory watchdog reads `/proc` (Linux). On Windows and
macOS a memory-hungry query is still stopped by the 10-second timeout, but its memory can
grow until then; one test is skipped there. Docker (Linux) or WSL2 has the full protection.

## 3. Docker Compose

```bash
cp .env.example .env               # optional: without it AI features are off
docker compose up --build -d
docker compose ps                  # both services should become "healthy"
```

- One image (`python:3.13-slim`, multi-stage, non-root user `app`, uid 10001) runs both
  services; [`docker-compose.yml`](../docker-compose.yml) starts the backend on 8000 and the
  frontend on 8501. The frontend waits until the backend is healthy.
- Health checks: backend `GET /health`, frontend `GET /_stcore/health`.
- Data persists in the named volume `insightsql-data` (`/app/data`). `.env` is read by
  Compose (`env_file`, optional) and never copied into the image (`.dockerignore`).
- Stop: `docker compose down` (keeps the data) · remove data too:
  `docker compose down -v`.
- Backup: `docker compose stop backend`, then copy the volume, e.g.
  `docker run --rm -v insightsql-data:/data -v "$PWD":/backup python:3.13-slim tar czf /backup/insightsql-data.tgz -C /data .`

**Verification status (2026-11-05): the image build and `docker compose up` have NOT been
run** — Docker is not installed on the development machine. What was verified: every
runtime dependency resolves to a Linux CPython 3.13 wheel (`pip install --dry-run
--platform manylinux…`), the exact service and health-check commands run and fail correctly
when the service is down, and static tests (`tests/test_deployment_files.py`) check the
non-root user, the build context exclusions, ports, volume, and health checks.

## 4. Operations

| Task | How |
|---|---|
| Is it up? Is AI configured? | `GET /health` → `{"status": "ok", "llm_configured": …, "model": …}`; the Settings page shows the same, plus limits. |
| AI usage and cost | Settings page, or `GET /usage/summary` and `GET /usage/requests` (per request: purpose, model, tokens, estimated cost, latency, errors — never prompts). |
| Logs | Standard output (`LOG_LEVEL`, default `INFO`). Configured secrets are redacted. |
| Raw uploads | Deleted after 24 h, at start and every `UPLOAD_CLEANUP_INTERVAL_MINUTES`. |
| Upgrade | Stop, pull the new code (or image), start. The metadata schema migrates itself on first use (DATABASE.md §1). |
| Reset | Stop and delete `DATA_DIR` (or `docker compose down -v`). |

## 5. Security notes for a shared deployment

The MVP is **single-tenant without login** (ARCHITECTURE D7): anyone who can reach port
8501 or 8000 can see and delete every dataset and spend the API budget. Before exposing it
beyond a trusted machine or network, put it behind authentication (a reverse proxy with
login, or a VPN), serve it over HTTPS, and remove the backend's `ports:` entry from
`docker-compose.yml` (it is published for local development only; the frontend reaches the
backend inside the Compose network). Upload size is enforced after the body is
received (Phase 2 review M1); a reverse-proxy body limit is advisable.
