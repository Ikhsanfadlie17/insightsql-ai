# Database

InsightSQL AI keeps two kinds of data: **metadata** in one SQLite file and **analytical
data** in one DuckDB file per dataset. Both live under `DATA_DIR` (default `data/`; in
Docker the named volume `insightsql-data` mounted at `/app/data`).

```text
<DATA_DIR>/
├── app.db                      SQLite: datasets, columns, profiles, mappings, history, LLM requests
├── datasets/<dataset_id>.duckdb   one table "dataset" per file (one Excel sheet = one dataset)
└── uploads/<upload_id>/        raw upload + upload.json, kept for 24 h (two-step import)
```

## 1. SQLite metadata (`backend/repositories/db.py`)

Accessed with the standard-library `sqlite3` (no ORM). Every connection enables
`PRAGMA foreign_keys = ON`; deleting a dataset cascades to its columns, profile, mapping,
conversations, and analyses. `llm_requests` has no foreign keys on purpose: usage and cost
history survive a deleted dataset. Timestamps are ISO 8601 UTC strings; documents
(`*_json`) are Pydantic models serialised as JSON.

```sql
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    sheet_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    error TEXT,
    row_count INTEGER,
    column_count INTEGER,
    created_at TEXT NOT NULL,
    number_format TEXT NOT NULL DEFAULT 'auto',
    date_order TEXT NOT NULL DEFAULT 'auto',
    text_columns_json TEXT NOT NULL DEFAULT '[]',
    header_row INTEGER,
    skip_template_rows INTEGER NOT NULL DEFAULT 1,
    layout_json TEXT
);

CREATE TABLE IF NOT EXISTS dataset_columns (
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    logical_type TEXT NOT NULL,
    failed_conversions INTEGER NOT NULL DEFAULT 0,
    notes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (dataset_id, position)
);

CREATE TABLE IF NOT EXISTS dataset_profiles (
    dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
    document_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS column_mappings (
    dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
    document_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    sql TEXT,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_requests (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    dataset_id TEXT,
    analysis_id TEXT,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    attempts INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_tokens INTEGER,
    est_cost_usd REAL,
    cost_status TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    error_code TEXT,
    error_message TEXT,
    prompt_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_dataset ON conversations(dataset_id);
CREATE INDEX IF NOT EXISTS idx_analyses_conversation ON analyses(conversation_id);
CREATE INDEX IF NOT EXISTS idx_llm_requests_created ON llm_requests(created_at);
```

| Table | Written by | Notes |
|---|---|---|
| `datasets` | `DatasetRepository` | `status`: processing → ready / failed; the import options (`number_format`, `date_order`, `text_columns_json`) are kept for transparency. |
| `dataset_columns` | ingest | SQL name, original header, logical type, unconverted-value count, notes. |
| `dataset_profiles` | ingest (re-computed on request if missing) | `DatasetProfile` JSON. |
| `column_mappings` | `PUT /datasets/{id}/column-mapping` | confirmed `ColumnMapping` JSON; suggestions are never stored. |
| `conversations`, `analyses` | AI Analyst | `response_json` is the full `AnalysisResponse` shown in Query History and used for export. |
| `llm_requests` | `LLMGateway` | one row per provider request, success or error; never prompts or answers. Error messages are redacted. |

**Migrations.** The schema is applied with `CREATE TABLE IF NOT EXISTS` on every
connection. Columns added after the first release are added with `ALTER TABLE` when missing:

| Table | Column | Definition |
|---|---|---|
| `datasets` | `number_format` | `TEXT NOT NULL DEFAULT 'auto'` |
| `datasets` | `date_order` | `TEXT NOT NULL DEFAULT 'auto'` |
| `datasets` | `text_columns_json` | `TEXT NOT NULL DEFAULT '[]'` |
| `dataset_columns` | `notes_json` | `TEXT NOT NULL DEFAULT '[]'` |
| `datasets` | `header_row` | `INTEGER` |
| `datasets` | `skip_template_rows` | `INTEGER NOT NULL DEFAULT 1` |
| `datasets` | `layout_json` | `TEXT` |

A legacy empty `llm_usage` table (the Phase 1 design) is dropped when found.

## 2. DuckDB analytical data

Each ready dataset is a DuckDB file with one table, `dataset`. Columns use the normalised
SQL names (lower-case, ASCII, unique, e.g. `Pendapatan (Rp)` → `pendapatan_rp`); the
original headers are in `dataset_columns`. Logical types map to DuckDB types as follows:

| Logical type | DuckDB type |
|---|---|
| `integer` | `BIGINT` |
| `numeric` | `DOUBLE` |
| `date` | `DATE` |
| `datetime` | `TIMESTAMP` |
| `boolean` | `BOOLEAN` |
| `text` | `VARCHAR` |

Writes are atomic: the table is created in `<id>.tmp.duckdb` and renamed into place. Queries
open the file **read-only** in a separate worker process with time, memory, thread, row, and
result-size limits (ARCHITECTURE §7.2), so a query can only ever see that one dataset.

Example (the sample data):

```text
id_klien VARCHAR, nama_klien VARCHAR, tanggal_layanan DATE, departemen VARCHAR,
jenis_layanan VARCHAR, pendapatan_rp BIGINT, status VARCHAR
```

## 3. Lifecycle and retention

| Data | Created | Removed |
|---|---|---|
| Raw upload | `POST /datasets/inspect` | after 24 h: at startup and every `UPLOAD_CLEANUP_INTERVAL_MINUTES` (an upload whose import has not finished is skipped; creating a dataset restarts its clock) |
| Dataset (DuckDB + metadata + history) | `POST /datasets` | `DELETE /datasets/{id}` (Data Sources → Delete) |
| Conversation and its analyses | AI Analyst | `DELETE /conversations/{id}`, or with the dataset |
| LLM request records | every provider call | kept (cost history); remove `app.db` to reset |

Interrupted imports (status `processing` at startup) are marked `failed` and partial
`*.tmp.duckdb` files are removed.

**Backup:** stop the app and copy `DATA_DIR` (SQLite and DuckDB files are consistent when no
process has them open). In Docker, see DEPLOYMENT.md.
