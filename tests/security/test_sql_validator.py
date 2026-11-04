"""Task 9: the SQL validator is the gate between LLM output and execution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.core.errors import SQLValidationError
from backend.services.sql_validator import TableSchema, ValidatedSQL, validate_sql

SCHEMA = TableSchema(
    columns={
        "id_klien": "text",
        "tanggal": "date",
        "departemen": "text",
        "pendapatan": "integer",
    }
)
CORPUS = yaml.safe_load((Path(__file__).parent / "attack_corpus.yaml").read_text())


def test_corpus_is_large_enough() -> None:
    assert len(CORPUS) >= 30


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: c["sql"][:60] or "<empty>")
def test_attack_corpus_is_rejected(case: dict) -> None:
    with pytest.raises(SQLValidationError) as caught:
        validate_sql(case["sql"], SCHEMA)

    rules = {reason.split(":")[0] for reason in caught.value.reasons}
    assert case["rule"] in rules, caught.value.reasons


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM dataset",
        "SELECT departemen, SUM(pendapatan) AS total FROM dataset GROUP BY departemen "
        "ORDER BY total DESC",
        "SELECT COUNT(DISTINCT id_klien) FROM dataset WHERE EXTRACT(year FROM tanggal) = 2026",
        "SELECT date_trunc('month', tanggal) AS bulan, COUNT(*) FROM dataset GROUP BY 1",
        "SELECT strftime(tanggal, '%Y-%m') AS ym FROM dataset",
        "WITH y AS (SELECT DISTINCT id_klien FROM dataset WHERE year(tanggal) = 2025) "
        "SELECT COUNT(*) FROM y",
        "WITH a AS (SELECT id_klien FROM dataset), b AS (SELECT id_klien FROM a) "
        "SELECT COUNT(*) FROM b",
        "SELECT id_klien, ROW_NUMBER() OVER (PARTITION BY departemen ORDER BY tanggal) "
        "FROM dataset",
        "SELECT departemen FROM dataset UNION ALL SELECT departemen FROM dataset",
        'SELECT "id_klien" FROM "dataset"',
        "SELECT d.departemen FROM dataset AS d",
        "SELECT a.id_klien FROM dataset a JOIN dataset b ON a.id_klien = b.id_klien "
        "AND a.tanggal < b.tanggal",
        "SELECT id_klien FROM dataset WHERE id_klien IN (SELECT id_klien FROM dataset "
        "WHERE pendapatan > 100)",
        "SELECT CASE WHEN pendapatan > 1000000 THEN 'besar' ELSE 'kecil' END FROM dataset",
        "SELECT COALESCE(departemen, 'Tidak diketahui') FROM dataset",
        "SELECT * FROM dataset",
        "SELECT 1",
        "SELECT COUNT(*) FROM dataset;",
        "-- komentar\nSELECT COUNT(*) FROM dataset",
        "SELECT 'DROP TABLE dataset' AS teks FROM dataset",
    ],
)
def test_safe_analytical_queries_are_accepted(sql: str) -> None:
    validated = validate_sql(sql, SCHEMA)

    assert isinstance(validated, ValidatedSQL)
    assert validated.sql.strip()
    assert ";" not in validated.sql.rstrip(";")[-1:]


def test_reasons_name_the_rule() -> None:
    with pytest.raises(SQLValidationError) as caught:
        validate_sql("SELECT * FROM read_csv('x')", SCHEMA)

    assert caught.value.reasons
    assert caught.value.reasons[0].startswith("forbidden_function")
    assert "read_csv" in caught.value.reasons[0]


def test_validated_sql_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        ValidatedSQL("DROP TABLE dataset")  # type: ignore[call-arg]


def test_validated_sql_is_immutable() -> None:
    validated = validate_sql("SELECT 1", SCHEMA)

    with pytest.raises(AttributeError):
        validated.sql = "DROP TABLE dataset"  # type: ignore[misc]


def test_output_is_a_single_normalized_statement() -> None:
    validated = validate_sql("select count(*) from dataset;   -- trailing", SCHEMA)

    assert validated.sql.upper().startswith("SELECT")
    assert ";" not in validated.sql


def test_overlong_sql_is_rejected() -> None:
    sql = "SELECT " + " + ".join(["pendapatan"] * 5000) + " FROM dataset"

    with pytest.raises(SQLValidationError) as caught:
        validate_sql(sql, SCHEMA)

    assert caught.value.reasons[0].startswith("too_long")


def test_cte_named_like_a_forbidden_table_still_resolves_only_internally() -> None:
    validated = validate_sql(
        "WITH other_table AS (SELECT id_klien FROM dataset) SELECT * FROM other_table", SCHEMA
    )

    assert "other_table" in validated.sql


def test_too_many_joins_are_rejected() -> None:
    joins = " ".join(f"JOIN dataset t{i} ON t{i}.id_klien = t0.id_klien" for i in range(1, 7))

    with pytest.raises(SQLValidationError) as caught:
        validate_sql(f"SELECT COUNT(*) FROM dataset t0 {joins}", SCHEMA)

    assert any(r.startswith("too_many_joins") for r in caught.value.reasons)


def test_columns_are_checked_against_the_active_dataset_only() -> None:
    other = TableSchema(columns={"nik": "text"})

    validate_sql("SELECT nik FROM dataset", other)
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT nik FROM dataset", SCHEMA)


def test_trailing_comment_is_not_a_second_statement_but_trailing_sql_is() -> None:
    validate_sql("SELECT 1; -- done", SCHEMA)

    with pytest.raises(SQLValidationError):
        validate_sql("SELECT 1; -- done\nDROP TABLE dataset", SCHEMA)


def test_deeply_nested_sql_is_rejected_quickly() -> None:
    import time

    sql = "SELECT " + "(" * 3000 + "1" + ")" * 3000
    started = time.perf_counter()

    with pytest.raises(SQLValidationError) as caught:
        validate_sql(sql, SCHEMA)

    assert time.perf_counter() - started < 2
    assert caught.value.reasons[0].split(":")[0] in {"parse_error", "too_complex"}


def test_huge_expressions_are_rejected_before_column_resolution() -> None:
    import time

    sql = "SELECT " + " + ".join(["pendapatan"] * 1500) + " FROM dataset"
    started = time.perf_counter()

    with pytest.raises(SQLValidationError) as caught:
        validate_sql(sql, SCHEMA)

    assert time.perf_counter() - started < 2
    assert caught.value.reasons[0].startswith("too_complex")


def test_equality_join_between_both_sides_is_accepted() -> None:
    validate_sql("SELECT COUNT(*) FROM dataset a JOIN dataset b USING (id_klien)", SCHEMA)
    validate_sql(
        "WITH y AS (SELECT id_klien FROM dataset) "
        "SELECT COUNT(*) FROM dataset d JOIN y ON d.id_klien = y.id_klien",
        SCHEMA,
    )
