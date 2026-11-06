# First real file: a spreadsheet laid out for people (Google Sheets CSV)

- **Date:** 2026-11-05 · **LLM: none** (import, profiling, mapping, client analytics) and
  **LLM: real (gpt-6-luna, 2026-11-05)** (process-duration questions)
- **Data:** a real working spreadsheet of the owner, used once. It holds personal data, so **the
  file is not in the repository**. On 2026-11-06, at the owner's request, its source, column
  names, and all figures were removed from this record: only the product lessons remain.
  Tests use dummy files with a similar layout (`tests/api/test_smart_import.py`,
  `tests/services/test_table_layout.py`), and real work files are no longer used for testing.
- **Layout of the sheet:** title and note rows above the header, an empty first column, dozens
  of data columns, a helper column and a side list beside the table, many pre-filled template
  rows (a row number and formula results such as "-" or "#REF!") after the real rows, and empty
  rows below. Dates were written with month names in three styles (`22/Jan/2025`,
  `22 Dec 2025`, `10-Feb-2025`), and amounts with thousands separators.

| Step | Before (commit `4933c2d`) | After (`47c1175` month names, smart import commit) |
|---|---|---|
| Import as downloaded | "ready", but columns named `column_1…`, template rows counted as data, warning "Most columns have no header" | header found, named columns, side columns and template rows skipped |
| Dates | all stayed **text, without any note** → "no date column", no period analysis | all converted, no failures |
| Row count in the import form | physical lines | exact count for a CSV |
| Mapping suggestion | none possible (no headers) | suggested from the headers; one suggestion was weak |
| Client analytics | — | worked, but retention was not a meaningful metric for one-off services |

Both gaps were product defects found only with real data: month-name dates were silently
left as text, and sheets laid out for people could not be imported without manual
cleaning. Smart import detects the header row (overridable), the main table, and trailing
template rows, and reports each adjustment; a plain table is read exactly as before.
*(Correction 2026-11-06: that last claim was wrong. The Phase 8 review showed that trailing
rows missing only an optional column were cut as template rows. The rule was narrowed and
tested on dummy data. See `phase-log.md`, Phase 8.)*

## Process-duration questions

The owner asked, in Indonesian, where a multi-step process took longest ("di mana"). With SQL
prompt v2 the model read "di mana" as a category, asked several clarifying questions, and
ended with durations per category. The dataset had no confirmed column roles, and the schema
context told the model to ask whenever roles were missing.

Changes: SQL prompt v3 rule 12 (stage durations between consecutive date steps; "di mana"
about a process means which step), the schema context lists date columns in file order, and
unmapped datasets may use clearly named columns (ask only when several could be meant).

Re-asked in a new conversation with v3, the question and a second wording were answered
directly with one row per stage, and the stage figures matched an independent DuckDB
computation on the same dataset. In one answer the explanation was rejected for a causal
claim and replaced by the template summary.
