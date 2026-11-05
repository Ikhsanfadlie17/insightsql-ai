"""Task 23: the evaluation set is well formed and every reference runs on the sample data."""

from __future__ import annotations

import re
from collections import Counter

import duckdb
import pytest

from backend.core.config import Settings
from backend.repositories.dataset_repo import DatasetRepository
from backend.services.dataset_service import DatasetService
from backend.services.sql_validator import TableSchema, validate_sql
from eval.run_eval import CATEGORIES, load_questions
from tests.conftest import SAMPLE_DATA_DIR

QUESTIONS = load_questions()
RESULT_QUESTIONS = [q for q in QUESTIONS if q.expected.kind == "result"]


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> tuple[DatasetService, str]:
    data_dir = tmp_path_factory.mktemp("eval-data")
    settings = Settings(_env_file=None, data_dir=data_dir, openai_api_key=None, openai_model=None)
    service = DatasetService(settings, DatasetRepository(settings.metadata_db_path))
    csv = SAMPLE_DATA_DIR / "client_services.csv"
    inspection = service.inspect_upload(csv.name, csv.read_bytes())
    record = service.create_dataset(inspection.upload_id, None, None)
    service.ingest(record.id, inspection.upload_id, None)
    assert service.get(record.id).status == "ready"
    return service, record.id


def test_size_and_coverage() -> None:
    """PRD §9: at least 50 questions; plan: ≥ 4 per category, ≥ 20 in Indonesian."""
    categories = Counter(q.category for q in QUESTIONS)
    assert len(QUESTIONS) >= 50
    assert set(categories) == set(CATEGORIES)
    assert min(categories.values()) >= 4
    assert sum(q.language == "id" for q in QUESTIONS) >= 20
    assert len({q.id for q in QUESTIONS}) == len(QUESTIONS)
    assert len({q.question.casefold() for q in QUESTIONS}) == len(QUESTIONS)


def test_expected_kinds_fit_the_category() -> None:
    kinds = {q.category: set() for q in QUESTIONS}
    for q in QUESTIONS:
        kinds[q.category].add(q.expected.kind)
    assert kinds["ambiguous"] == {"clarify"}
    assert kinds["invalid"] == {"reject"}
    assert kinds["client_retention"] == kinds["new_vs_existing"] == {"retention"}


@pytest.mark.parametrize("question", RESULT_QUESTIONS, ids=lambda q: q.id)
def test_reference_sql_is_valid_and_returns_rows(
    sample: tuple[DatasetService, str], question
) -> None:
    service, dataset_id = sample
    schema = TableSchema(columns={c.name: c.logical_type for c in service.columns(dataset_id)})

    validate_sql(question.expected.reference_sql, schema)  # raises on any broken rule
    with duckdb.connect(str(service.duckdb_path(dataset_id)), read_only=True) as conn:
        rows = conn.execute(question.expected.reference_sql).fetchall()

    assert rows, "the reference returns no rows"
    assert all(v is not None for row in rows for v in row), "a NULL reference is not gradable"


@pytest.mark.parametrize(
    "question",
    [q for q in RESULT_QUESTIONS if re.search(r"\bLIMIT\s+\d+", q.expected.reference_sql)],
    ids=lambda q: q.id,
)
def test_top_n_references_have_no_tie_at_the_cut(
    sample: tuple[DatasetService, str], question
) -> None:
    """A tie at the LIMIT boundary would make more than one answer correct."""
    service, dataset_id = sample
    sql = question.expected.reference_sql
    limit = int(re.search(r"\bLIMIT\s+(\d+)", sql).group(1))
    wider = re.sub(r"\bLIMIT\s+\d+", f"LIMIT {limit + 1}", sql)
    with duckdb.connect(str(service.duckdb_path(dataset_id)), read_only=True) as conn:
        rows = conn.execute(wider).fetchall()
    order_column = 1  # every top-N reference orders by its second column
    assert rows[limit - 1][order_column] != rows[limit][order_column]


def test_retention_periods_are_ordered() -> None:
    for q in QUESTIONS:
        if q.expected.kind == "retention":
            e = q.expected
            assert e.previous.start < e.previous.end <= e.current.start < e.current.end, q.id
