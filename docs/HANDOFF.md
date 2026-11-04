# Handoff — melanjutkan InsightSQL AI di sesi baru

Catatan internal (bahasa Indonesia) untuk melanjutkan pekerjaan di mesin lokal atau sesi
Claude Code baru. Sumber kebenaran tetap: `CLAUDE.md`, `docs/PRD.md`,
`docs/ARCHITECTURE.md`, `docs/plans/2026-11-04-insightsql-implementation-plan.md`,
`docs/evidence/`.

## 1. Status per 2026-11-04 (commit `3032c65` di `main`)

| Fase | Status | Test (akhir fase) |
|---|---|---|
| 1 Desain | selesai | — |
| 2 Fondasi (Task 1–6) | selesai, direview, disetujui user | 149 |
| 3 Data Intelligence (Task 7–8) | selesai, direview, disetujui user | 290 |
| 4 Integrasi AI (Task 9–13) | selesai, direview, disetujui user | 558 |
| 5 Analytics (Task 14–18) | selesai, direview, temuan Critical/High/Medium diperbaiki — **MENUNGGU review user** | 757 |
| 6 Product Features (Task 19–21) | belum dimulai | — |
| 7 Quality (evaluasi Text-to-SQL, performa) | belum | — |
| 8 Deployment (Docker, README final) | belum | — |

- API OpenAI sungguhan **belum pernah diuji** (jaringan diblokir di lingkungan cloud, belum
  ada key). Semua test AI memakai LLM palsu (`tests/llm_fakes.py`,
  `scripts/e2e/fake_llm_app.py`).
- Laporan Phase 5 lengkap: `docs/evidence/phase-log.md` (bagian Phase 5).

## 2. Aturan kerja yang disepakati dengan user

1. Kerjakan per fase sesuai plan, test-first. Setelah tiap fase: full `pytest`, `ruff check`
   + `ruff format --check`, e2e dengan backend sungguhan, **review independen**, perbaiki
   Critical/High, evaluasi Medium/Low, lapor, lalu **TUNGGU review user** sebelum fase
   berikutnya.
2. Laporan fase = bagian baru di `docs/evidence/phase-log.md`; eksperimen nyata = satu file
   di `docs/evidence/experiments/`; label LLM `none` / `mock` / `real (<model>, <tanggal>)`.
3. Jangan mengarang benchmark, akurasi, latency, atau biaya. Bedakan hasil mock vs API nyata.
4. Prinsip PRD (tidak boleh dilanggar): LLM hanya menerjemahkan; DuckDB menghitung; tidak
   mengirim dataset penuh ke LLM; SQL wajib lewat validator (SQLGlot) + executor sandbox;
   isi dataset = data tidak tepercaya; model dari env `OPENAI_MODEL`; rasio dengan
   denominator 0 → N/A.
5. Jangan commit `.env`, data upload, atau tooling AI (`.claude/skills`, `.venv`).
6. Komunikasi dengan user dalam Bahasa Indonesia; kode, dokumen teknis, dan UI dalam bahasa
   Inggris; aplikasi menjawab dalam bahasa pertanyaan (ID/EN).
7. Dokumentasi portofolio final (technical case study, 12 bagian) **baru dibuat setelah user
   menyatakan proyek siap**.

## 3. Pekerjaan berikutnya (setelah user menyetujui Phase 5)

Phase 6 — lihat plan, Task 19–21:
- **Task 19:** halaman Query History + manajemen percakapan (endpoint `GET/DELETE
  /conversations` sudah ada di `backend/api/analyses.py`; halaman `frontend/views/query_history.py`
  masih placeholder).
- **Task 20:** export CSV / Excel (sheet metadata) / PDF / `.sql` + tombol unduh di AI Analyst
  dan Client Analytics.
- **Task 21:** halaman Settings, manajemen dataset, penyempurnaan UI.

Follow-up yang sudah tercatat dan cocok dikerjakan di Phase 6:
- Tabel hasil (`st.dataframe`) belum berformat angka Indonesia (review Phase 5 L8).
- Error validasi FastAPI dan route tidak dikenal masih `{"detail": ...}` (Phase 2 L5).
- Pembersihan upload kedaluwarsa hanya saat startup (Phase 2 M4).
- Batas ukuran upload ditegakkan setelah body di-spool (Phase 2 M1 → Phase 8).

Catatan path: plan masih menyebut `frontend/pages/...`; folder yang benar adalah
`frontend/views/` (folder `pages/` merusak navigasi Streamlit — ada regression test).

## 4. Setup lokal

Kebutuhan: Git, Python **3.13**, (opsional) Node.js 18+ untuk e2e Playwright. Di Windows
disarankan **WSL2**: watchdog memori worker query membaca `/proc` (Linux); di macOS/Windows
hanya timeout dan batas lain yang berlaku, sehingga beberapa test sandbox bisa berbeda.

```bash
git clone https://github.com/Ikhsanfadlie17/AI-Powered-Data-Analytics-Business-Intelligence-Platform.git
cd AI-Powered-Data-Analytics-Business-Intelligence-Platform
python3.13 -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env                 # isi OPENAI_API_KEY dan OPENAI_MODEL bila ingin uji AI
python -m pytest -q                  # harus: 757 passed
ruff check backend tests frontend scripts
```

Menjalankan aplikasi (dua terminal):

```bash
uvicorn backend.main:app --reload --port 8000
BACKEND_URL=http://localhost:8000 streamlit run frontend/app.py
```

Buka http://localhost:8501 → Data Sources → upload `sample_data/client_services.csv` →
tab Column mapping → Confirm. Client Analytics bekerja tanpa API key; AI Analyst butuh
`OPENAI_API_KEY` + `OPENAI_MODEL` di `.env` (backend dibaca saat start).

E2E dengan LLM palsu (tanpa API key): lihat `scripts/e2e/README.md`.

## 5. Uji API OpenAI sungguhan pertama kali (belum pernah dilakukan)

1. Isi `.env` (`OPENAI_API_KEY`, `OPENAI_MODEL`), opsional harga model di
   `config/pricing.json` agar biaya terestimasi.
2. Jalankan backend + frontend, ajukan beberapa pertanyaan di AI Analyst.
3. Cek `GET http://localhost:8000/usage/requests` dan `/usage/summary` (token, biaya, status).
4. Catat sebagai eksperimen `docs/evidence/experiments/<tanggal>-first-real-api.md` dengan
   label `LLM: real (<model>, <tanggal>)` — jangan sebut sebagai benchmark; evaluasi resmi
   ada di Phase 7.

## 6. Skill Claude Code

Skill pihak ketiga (superpowers, ponytail, ui-ux-pro-max, graphify, agent-skills) dipasang
di lingkungan cloud **di luar repo** dan sengaja tidak di-commit (keputusan user). Di mesin
lokal skill itu tidak ada. Bila ingin dipakai lagi, pasang **hanya di level proyek**
(`.claude/skills`, `.claude/commands`, `.claude/agents` di folder proyek; `.venv` untuk
graphify) — jangan `--global` / `~/.claude`. `.gitignore` sudah mengecualikan
`.claude/skills/`, `.claude/commands/`, `.claude/agents/`, `.claude/references/`,
`.claude/settings.local.json`, dan `.venv/`. Pekerjaan bisa dilanjutkan tanpa skill tersebut.
