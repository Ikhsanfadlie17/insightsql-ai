"""Task 14: client-retention analytics. Every expected number below is computed by hand
from the fixture rows (see the comments), and the sample dataset is checked against the
independent pandas implementation in ``tests/ground_truth.py``."""

from __future__ import annotations

from datetime import date, datetime
from itertools import combinations
from pathlib import Path

import duckdb
import pytest

from backend.core.config import Settings
from backend.core.errors import MappingRequiredError, ValidationError
from backend.repositories.dataset_repo import DatasetRepository
from backend.schemas.analytics import Period, RetentionOptions
from backend.schemas.mapping import ColumnMapping, MappingUpdate
from backend.services.analytics_service import (
    AnalyticsService,
    AnalyticsSource,
    year_period,
)
from backend.services.dataset_service import DatasetService
from backend.services.mapping_service import MappingService
from backend.services.query_executor import QueryExecutor
from tests import ground_truth
from tests.conftest import SAMPLE_DATA_DIR

ID_DATE = {"client_id": "VARCHAR", "tx_date": "DATE"}

# The fixture from the implementation plan (Task 14). Row 11 duplicates row 10 exactly,
# row 12 has no client ID.
PLAN_ROWS = [
    ("A", "2024-03-01"),
    ("A", "2025-02-10"),
    ("A", "2026-01-15"),
    ("B", "2025-06-01"),
    ("C", "2026-04-01"),
    ("D", "2024-11-20"),
    ("D", "2026-07-07"),
    ("E", "2025-09-09"),
    ("E", "2026-02-02"),
    ("E", "2026-08-08"),
    ("E", "2026-08-08"),
    (None, "2026-05-05"),
]


def _mapping(**roles: str | None) -> ColumnMapping:
    return ColumnMapping(**roles, confirmed=True)


def make_source(
    tmp_path: Path,
    rows: list[tuple],
    types: dict[str, str] = ID_DATE,
    mapping: ColumnMapping | None = None,
    name: str = "fixture",
) -> AnalyticsSource:
    path = tmp_path / f"{name}.duckdb"
    columns = ", ".join(f'"{c}" {t}' for c, t in types.items())
    with duckdb.connect(str(path)) as conn:
        conn.execute(f"CREATE TABLE dataset ({columns})")
        if rows:
            marks = ", ".join("?" for _ in types)
            conn.executemany(f"INSERT INTO dataset VALUES ({marks})", rows)
    logical = {
        "VARCHAR": "text",
        "DATE": "date",
        "TIMESTAMP": "datetime",
        "BIGINT": "integer",
        "DOUBLE": "numeric",
    }
    return AnalyticsSource(
        duckdb_path=path,
        columns={c: logical[t] for c, t in types.items()},
        mapping=mapping or _mapping(client_id="client_id", transaction_date="tx_date"),
    )


@pytest.fixture
def svc(settings: Settings) -> AnalyticsService:
    return AnalyticsService(QueryExecutor(settings))


@pytest.fixture
def plan(tmp_path: Path) -> AnalyticsSource:
    return make_source(tmp_path, PLAN_ROWS)


def _codes(result) -> set[str]:
    return {w.code for w in result.warnings}


def _assert_partitions(r) -> None:
    assert r.new + r.returning + r.reactivated + r.between_period == r.current_active
    assert r.returning + r.not_returned == r.previous_active
    assert r.existing_active == r.returning + r.reactivated + r.between_period


# --- periods -----------------------------------------------------------------------------
def test_year_period_is_a_half_open_calendar_year() -> None:
    p = year_period(2025)
    assert (p.start, p.end, p.label) == (date(2025, 1, 1), date(2026, 1, 1), "2025")


def test_year_period_through_cuts_both_years_at_the_same_day() -> None:
    assert year_period(2025, through=date(2026, 10, 31)).end == date(2025, 11, 1)
    assert year_period(2026, through=date(2026, 10, 31)).end == date(2026, 11, 1)
    assert "31 Oct" in year_period(2025, through=date(2026, 10, 31)).label


def test_year_period_clamps_february_29() -> None:
    assert year_period(2025, through=date(2024, 2, 29)).end == date(2025, 3, 1)
    assert year_period(2024, through=date(2024, 2, 29)).end == date(2024, 3, 1)


def test_period_rejects_empty_or_reversed_ranges() -> None:
    with pytest.raises(ValueError):
        Period(label="x", start=date(2026, 1, 1), end=date(2026, 1, 1))
    with pytest.raises(ValueError):
        Period(label="x", start=date(2026, 2, 1), end=date(2026, 1, 1))


# --- the plan fixture: 2025 vs 2026 -------------------------------------------------------
def test_plan_fixture_adjacent_years(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    r = svc.compare_periods(plan, year_period(2025), year_period(2026), RetentionOptions())

    # previous 2025: A, B, E      current 2026: A, C, D, E
    assert (r.previous_active, r.current_active) == (3, 4)
    # returning A, E | new C | reactivated D (seen 2024, absent 2025) | not returned B
    assert (r.returning, r.new, r.reactivated, r.between_period, r.not_returned) == (2, 1, 1, 0, 1)
    assert r.existing_active == 3  # A, D, E: overlaps returning + reactivated
    _assert_partitions(r)
    # 2026 rows: A, C, D, E, E (the exact duplicate E row is excluded) -> 5
    assert r.transactions_current == 5 and r.transactions_previous == 3
    assert r.repeat_transactions_current == 1 and r.clients_with_repeat_current == 1
    assert r.repeat_transactions_previous == 0 and r.clients_with_repeat_previous == 0
    assert r.duplicate_rows_excluded == 1 and r.excluded_missing_id_rows == 1
    assert r.excluded_missing_date_rows == 0
    assert round(r.retention_rate, 4) == 0.6667  # 2 / 3
    assert r.new_client_rate == 0.25  # 1 / 4
    assert round(r.growth_rate, 4) == 0.3333  # (4 - 3) / 3
    assert (r.data_start, r.data_end) == (date(2024, 3, 1), date(2026, 8, 8))
    assert {"history_completeness", "incomplete_current_period", "missing_client_ids"} <= _codes(r)
    assert any("incomplete" in w.message.lower() for w in r.warnings)
    assert "history_starts_late" not in _codes(r)


def test_plan_fixture_non_adjacent_years(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    g = svc.compare_periods(plan, year_period(2024), year_period(2026), RetentionOptions())

    # previous 2024: A, D      current 2026: A, C, D, E (E first seen 2025, in the gap)
    assert (g.previous_active, g.current_active) == (2, 4)
    assert (g.returning, g.new, g.reactivated, g.between_period, g.not_returned) == (2, 1, 0, 1, 0)
    assert g.existing_active == 3
    _assert_partitions(g)
    assert "non_adjacent_periods" in _codes(g)


def test_ytd_comparison_moves_a_client_into_the_gap(
    svc: AnalyticsService, plan: AnalyticsSource
) -> None:
    through = date(2026, 8, 8)
    r = svc.compare_periods(
        plan, year_period(2025, through=through), year_period(2026, through=through)
    )

    # previous 1 Jan-8 Aug 2025: A, B.  current 1 Jan-8 Aug 2026: A, C, D, E.
    # E was first seen on 2025-09-09: after the previous YTD window, before 2026 -> between.
    assert (r.previous_active, r.current_active) == (2, 4)
    assert (r.returning, r.new, r.reactivated, r.between_period, r.not_returned) == (1, 1, 1, 1, 1)
    _assert_partitions(r)
    assert "incomplete_current_period" not in _codes(r)  # data runs to 8 Aug
    assert "unequal_period_lengths" not in _codes(r)
    assert "non_adjacent_periods" in _codes(r)


def test_unequal_period_lengths_are_flagged(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    r = svc.compare_periods(plan, year_period(2025), year_period(2026, through=date(2026, 8, 8)))

    assert "unequal_period_lengths" in _codes(r)


def test_empty_previous_period_gives_na_rates(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    r = svc.compare_periods(plan, year_period(2023), year_period(2024))

    assert r.previous_active == 0 and r.current_active == 2  # A, D
    assert r.new == 2 and r.new_client_rate == 1.0
    assert r.retention_rate is None and r.growth_rate is None
    assert "no_data_previous" in _codes(r)
    assert "history_starts_late" in _codes(r)  # data starts 2024-03-01, after 2023-01-01


def test_periods_without_data(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    r = svc.compare_periods(plan, year_period(2030), year_period(2031))

    assert r.previous_active == r.current_active == r.returning == r.new == 0
    assert r.reactivated == r.between_period == r.not_returned == r.existing_active == 0
    assert r.transactions_current == r.transactions_previous == 0
    assert r.retention_rate is None and r.new_client_rate is None and r.growth_rate is None
    assert "no_data" in _codes(r)


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (Period(label="a", start=date(2025, 1, 1), end=date(2025, 7, 1)),
         Period(label="b", start=date(2025, 6, 1), end=date(2025, 12, 1))),
        (year_period(2026), year_period(2025)),
        (year_period(2025), year_period(2025)),
    ],
)  # fmt: skip
def test_overlapping_or_reversed_periods_are_rejected(
    svc: AnalyticsService, plan: AnalyticsSource, previous: Period, current: Period
) -> None:
    with pytest.raises(ValidationError, match="before"):
        svc.compare_periods(plan, previous, current)


def test_dedupe_can_be_turned_off(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    r = svc.compare_periods(
        plan, year_period(2025), year_period(2026), RetentionOptions(dedupe_exact_duplicates=False)
    )

    assert r.transactions_current == 6 and r.duplicate_rows_excluded == 0
    assert r.current_active == 4  # client counts never depend on duplicates


def test_definitions_and_sql_are_returned(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    r = svc.compare_periods(plan, year_period(2025), year_period(2026))

    assert "previous" in r.definitions["retention_rate"].lower()
    assert {"new", "returning", "reactivated", "between_period", "not_returned",
            "existing_active", "retention_rate", "new_client_rate", "growth_rate"} <= set(
        r.definitions
    )  # fmt: skip
    assert len(r.sql) == 1 and "COUNT" in r.sql[0].upper()
    assert r.identity_column == "client_id" and r.identity_kind == "client_id"


# --- edge cases ---------------------------------------------------------------------------
def test_boundary_dates(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [
        ("P1", "2025-12-31"),  # last day of previous
        ("P1", "2026-01-01"),  # first day of current -> P1 returning
        ("P2", "2026-12-31"),  # last day of current -> new
        ("P3", "2027-01-01"),  # day after current: outside both periods
        ("P4", "2025-01-01"),  # first day of previous, never again -> not returned
        ("P5", "2024-12-31"),  # day before previous ...
        ("P5", "2026-06-01"),  # ... and back in current -> reactivated
    ]
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert (r.previous_active, r.current_active) == (2, 3)
    assert (r.returning, r.new, r.reactivated, r.between_period, r.not_returned) == (1, 1, 1, 0, 1)
    assert (r.transactions_previous, r.transactions_current) == (2, 3)
    _assert_partitions(r)


def test_datetime_boundaries(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [("Q1", datetime(2025, 12, 31, 23, 59, 59)), ("Q2", datetime(2026, 1, 1, 0, 0, 0))]
    source = make_source(tmp_path, rows, {"client_id": "VARCHAR", "tx_date": "TIMESTAMP"})

    r = svc.compare_periods(source, year_period(2025), year_period(2026))

    assert (r.previous_active, r.current_active, r.returning, r.new, r.not_returned) == (
        1, 1, 0, 1, 1,
    )  # fmt: skip


def test_empty_and_padded_ids(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [
        (None, "2026-03-01"),
        ("", "2026-03-02"),
        ("   ", "2026-03-03"),
        ("X", "2026-03-04"),
        (" X ", "2026-03-05"),  # same client as "X" after trimming
        ("x", "2026-03-06"),  # case is significant for IDs: a different client
    ]
    source = make_source(tmp_path, rows)

    r = svc.compare_periods(source, year_period(2025), year_period(2026))
    issues = svc.identity_issues(source)

    assert r.current_active == 2 and r.new == 2
    assert r.excluded_missing_id_rows == 3
    assert r.transactions_current == 3
    assert issues.missing_id_rows == 3
    assert issues.ids_differing_only_by_case_or_spacing == 1  # {"X", "x"}


def test_trailing_space_id_is_the_same_client(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [("A ", "2025-05-01"), ("A", "2026-05-01")]
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert (r.previous_active, r.current_active, r.returning, r.new) == (1, 1, 1, 0)


def test_tabs_and_no_break_spaces_around_ids_are_trimmed(svc, tmp_path: Path) -> None:
    rows = [("\tA", "2025-05-01"), ("A ", "2026-05-01"), ("  ", "2026-06-01")]
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert (r.previous_active, r.current_active, r.returning) == (1, 1, 1)
    assert r.excluded_missing_id_rows == 1


def test_invalid_dates_are_excluded_and_counted(svc: AnalyticsService, tmp_path: Path) -> None:
    # Invalid dates are stored as NULL at import (with a column note).
    rows = [("A", None), ("A", "2026-02-01"), ("B", None)]
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert (r.current_active, r.new) == (1, 1)  # B has no usable date
    assert r.excluded_missing_date_rows == 2
    assert "missing_transaction_dates" in _codes(r)


def test_history_starting_mid_activity(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [("A", "2025-06-15"), ("A", "2026-03-01"), ("B", "2026-02-01")]
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert (r.previous_active, r.current_active, r.returning, r.new) == (1, 2, 1, 1)
    assert {"history_starts_late", "history_completeness"} <= _codes(r)


def test_multiple_transactions_per_period(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [("A", "2026-01-05"), ("A", "2026-02-05"), ("A", "2026-03-05"), ("B", "2026-01-09")]
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert r.current_active == 2 and r.transactions_current == 4
    assert r.repeat_transactions_current == 2 and r.clients_with_repeat_current == 1


def test_integer_client_ids(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [(1001, "2025-01-05"), (1001, "2026-01-05"), (1002, "2026-02-05")]
    source = make_source(tmp_path, rows, {"client_id": "BIGINT", "tx_date": "DATE"})

    r = svc.compare_periods(source, year_period(2025), year_period(2026))

    assert (r.current_active, r.returning, r.new) == (2, 1, 1)


def test_name_identity_when_no_id_is_mapped(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [("PT Maju ", "2026-01-01"), ("pt  maju", "2026-02-01"), ("CV Jaya", "2026-03-01")]
    source = make_source(
        tmp_path,
        rows,
        {"client_name": "VARCHAR", "tx_date": "DATE"},
        _mapping(client_name="client_name", transaction_date="tx_date"),
    )

    r = svc.compare_periods(source, year_period(2025), year_period(2026))

    assert r.current_active == 2
    assert r.identity_kind == "client_name"
    assert "name_identity" in _codes(r)


def test_identity_report_with_names(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [
        ("K1", "PT A", "2026-01-01"),
        ("K1", "PT  A.", "2026-01-02"),  # one ID, two different names
        ("K1", "pt a", "2026-01-03"),  # same as "PT A" after normalising: not a conflict
        ("K2", "CV B", "2026-01-04"),
        ("K3", "cv b", "2026-01-05"),  # one name, two IDs
    ]
    source = make_source(
        tmp_path,
        rows,
        {"client_id": "VARCHAR", "client_name": "VARCHAR", "tx_date": "DATE"},
        _mapping(client_id="client_id", client_name="client_name", transaction_date="tx_date"),
    )

    report = svc.identity_issues(source)

    assert report.ids_with_multiple_names == 1
    assert report.names_with_multiple_ids == 1
    assert report.examples.row_count == 2


@pytest.mark.parametrize(
    "mapping",
    [
        ColumnMapping(client_id="client_id", transaction_date="tx_date", confirmed=False),
        _mapping(client_id="client_id"),
        _mapping(transaction_date="tx_date"),
    ],
)
def test_requires_a_confirmed_identity_and_date(tmp_path: Path, mapping: ColumnMapping) -> None:
    with pytest.raises(MappingRequiredError):
        make_source(tmp_path, PLAN_ROWS, mapping=mapping)


# --- drill-down, trend, departments, coverage ---------------------------------------------
@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("new", ["C"]),
        ("returning", ["A", "E"]),
        ("reactivated", ["D"]),
        ("between_period", []),
        ("not_returned", ["B"]),
        ("existing_active", ["A", "D", "E"]),
    ],
)
def test_client_list_per_category(
    svc: AnalyticsService, plan: AnalyticsSource, category: str, expected: list[str]
) -> None:
    page = svc.client_list(plan, year_period(2025), year_period(2026), category)

    assert [row[0] for row in page.result.rows] == expected
    assert page.total == len(expected)
    assert page.result.columns[0] == "client"


def test_client_list_pagination(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    page = svc.client_list(
        plan, year_period(2025), year_period(2026), "existing_active", offset=1, limit=1
    )

    assert [row[0] for row in page.result.rows] == ["D"]
    assert (page.total, page.offset, page.limit) == (3, 1, 1)


def test_client_list_rejects_unknown_categories(svc, plan) -> None:
    with pytest.raises(ValidationError):
        svc.client_list(plan, year_period(2025), year_period(2026), "vip")


def test_monthly_trend(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    trend = svc.client_trend(plan, "month")
    rows = {r[0]: r[1:] for r in trend.result.rows}
    assert trend.result.columns[:4] == ["period", "active_clients", "new_clients", "transactions"]

    assert rows["2024-03"] == [1, 1, 1]  # A, first seen
    assert rows["2024-04"] == [0, 0, 0]  # months without data are shown as zero
    assert rows["2026-05"] == [0, 0, 0]  # only a row without client ID
    assert rows["2026-08"] == [1, 0, 1]  # E twice, exact duplicate excluded
    assert len(rows) == 30  # 2024-03 .. 2026-08
    assert trend.partial_last and not trend.partial_first
    assert "partial_last_bucket" in _codes(trend)


def test_yearly_trend(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    rows = {r[0]: r[1:] for r in svc.client_trend(plan, "year").result.rows}

    assert rows == {"2024": [2, 2, 2], "2025": [3, 2, 3], "2026": [4, 1, 5]}


def test_quarterly_trend_labels(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    rows = {r[0]: r[1:] for r in svc.client_trend(plan, "quarter").result.rows}

    assert rows["2026-Q3"] == [2, 0, 2]  # D (Jul), E (Aug)
    assert rows["2024-Q2"] == [0, 0, 0]


def test_department_breakdown(svc: AnalyticsService, tmp_path: Path) -> None:
    rows = [
        ("A", "2025-03-01", "Audit"),
        ("A", "2026-03-01", "Audit"),
        ("A", "2026-04-01", "Pajak"),
        ("B", "2025-05-01", "Pajak"),
        ("B", "2026-05-01", "Audit"),
        ("C", "2026-06-01", None),
    ]
    source = make_source(
        tmp_path,
        rows,
        {"client_id": "VARCHAR", "tx_date": "DATE", "dept": "VARCHAR"},
        _mapping(client_id="client_id", transaction_date="tx_date", department="dept"),
    )

    out = svc.department_breakdown(source, year_period(2025), year_period(2026))
    table = {r[0]: r[1:] for r in out.result.rows}

    assert out.result.columns[:6] == [
        "department", "previous_active", "current_active", "returning", "retention_rate",
        "transactions_current",
    ]  # fmt: skip
    assert table["Audit"][:5] == [1, 2, 1, 1.0, 2]
    assert table["Pajak"][:5] == [1, 1, 0, 0.0, 1]
    assert table["(no department)"][:5] == [0, 1, 0, None, 1]
    assert out.clients_in_multiple_departments == 1  # A used Audit and Pajak in 2026
    assert "clients_in_multiple_departments" in _codes(out)


def test_department_breakdown_needs_a_department_role(svc, plan) -> None:
    with pytest.raises(MappingRequiredError):
        svc.department_breakdown(plan, year_period(2025), year_period(2026))


def test_coverage(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    c = svc.coverage(plan)

    assert (c.data_start, c.data_end) == (date(2024, 3, 1), date(2026, 8, 8))
    assert c.years == [2024, 2025, 2026]
    assert c.unique_clients == 5 and c.rows == 12
    assert c.missing_id_rows == 1 and c.missing_date_rows == 0


# --- the sample dataset against the independent implementation -----------------------------
SAMPLE_MAPPING = MappingUpdate(
    client_id="id_klien",
    client_name="nama_klien",
    transaction_date="tanggal_layanan",
    department="departemen",
    revenue="pendapatan_rp",
)


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> AnalyticsSource:
    root = tmp_path_factory.mktemp("sample")
    settings = Settings(_env_file=None, data_dir=root / "data", openai_api_key=None)
    repo = DatasetRepository(settings.metadata_db_path)
    datasets = DatasetService(settings, repo)
    inspection = datasets.inspect_upload(
        "client_services.csv", (SAMPLE_DATA_DIR / "client_services.csv").read_bytes()
    )
    record = datasets.create_dataset(inspection.upload_id, None, None)
    datasets.ingest(record.id, inspection.upload_id, None)
    MappingService(datasets, repo).confirm(record.id, SAMPLE_MAPPING)
    return AnalyticsSource.for_dataset(datasets, repo, record.id)


@pytest.fixture(scope="module")
def sample_svc(tmp_path_factory) -> AnalyticsService:
    root = tmp_path_factory.mktemp("svc")
    return AnalyticsService(QueryExecutor(Settings(_env_file=None, data_dir=root)))


@pytest.mark.parametrize(("prev_year", "cur_year"), list(combinations(range(2023, 2027), 2)))
def test_sample_dataset_matches_ground_truth(
    sample_svc: AnalyticsService, sample: AnalyticsSource, prev_year: int, cur_year: int
) -> None:
    previous, current = year_period(prev_year), year_period(cur_year)
    r = sample_svc.compare_periods(sample, previous, current)
    truth = ground_truth.compute(
        ground_truth.load(sample.duckdb_path),
        "id_klien",
        "tanggal_layanan",
        (previous.start, previous.end),
        (current.start, current.end),
    )

    _assert_partitions(r)
    for name in (
        "previous_active", "current_active", "returning", "new", "reactivated",
        "between_period", "not_returned", "existing_active", "transactions_previous",
        "transactions_current", "clients_with_repeat_previous", "clients_with_repeat_current",
        "excluded_missing_id_rows", "excluded_missing_date_rows", "duplicate_rows_excluded",
        "retention_rate", "new_client_rate", "growth_rate",
    ):  # fmt: skip
        assert getattr(r, name) == pytest.approx(getattr(truth, name)), name
    for category, clients in truth.categories.items():
        page = sample_svc.client_list(sample, previous, current, category, limit=500)
        assert {row[0] for row in page.result.rows} == clients, category


def test_sample_dataset_ytd_matches_ground_truth(sample_svc, sample) -> None:
    through = date(2026, 10, 31)
    previous, current = year_period(2025, through=through), year_period(2026, through=through)
    r = sample_svc.compare_periods(sample, previous, current)
    truth = ground_truth.compute(
        ground_truth.load(sample.duckdb_path),
        "id_klien",
        "tanggal_layanan",
        (previous.start, previous.end),
        (current.start, current.end),
    )

    assert (r.previous_active, r.current_active, r.returning, r.new, r.reactivated) == (
        truth.previous_active, truth.current_active, truth.returning, truth.new,
        truth.reactivated,
    )  # fmt: skip
    assert r.duplicate_rows_excluded == truth.duplicate_rows_excluded > 0
    assert r.excluded_missing_id_rows == truth.excluded_missing_id_rows > 0


def test_sample_dataset_monthly_trend_matches_ground_truth(sample_svc, sample) -> None:
    truth = ground_truth.monthly_trend(
        ground_truth.load(sample.duckdb_path), "id_klien", "tanggal_layanan"
    )
    rows = {r[0]: tuple(r[1:4]) for r in sample_svc.client_trend(sample, "month").result.rows}

    for bucket, expected in truth.items():
        assert rows[bucket] == expected, bucket
    assert all(v == (0, 0, 0) for k, v in rows.items() if k not in truth)


# --- Phase 5 review regressions -------------------------------------------------------------
def test_zero_width_space_and_bom_around_ids(svc: AnalyticsService, tmp_path: Path) -> None:
    """M8: invisible format characters are trimmed like whitespace."""
    rows = [("​B", "2025-03-01"), ("B", "2026-03-01"), ("﻿C", "2025-04-01"),
            ("C", "2024-04-01")]  # fmt: skip
    r = svc.compare_periods(make_source(tmp_path, rows), year_period(2025), year_period(2026))

    assert (r.previous_active, r.current_active, r.returning) == (2, 1, 1)  # B, C | B
    assert r.new == 0


def test_typo_dates_do_not_explode_the_trend(svc: AnalyticsService, tmp_path: Path) -> None:
    """L3: a year 0202 is flagged, years come from the data, the trend is not zero-filled."""
    rows = [("A", "2025-03-01"), ("A", "0202-05-01"), ("B", "2026-01-10")]
    source = make_source(tmp_path, rows)

    coverage = svc.coverage(source)
    trend = svc.client_trend(source, "month")
    r = svc.compare_periods(source, year_period(2025), year_period(2026))

    assert coverage.years == [202, 2025, 2026]
    assert "implausible_dates" in _codes(coverage) and "implausible_dates" in _codes(r)
    assert trend.result.row_count == 3


def test_literal_no_department_value_is_not_merged_with_missing(
    svc: AnalyticsService, tmp_path: Path
) -> None:
    """L9."""
    rows = [("A", "2026-01-01", "(no department)"), ("B", "2026-01-02", None)]
    source = make_source(
        tmp_path,
        rows,
        {"client_id": "VARCHAR", "tx_date": "DATE", "dept": "VARCHAR"},
        _mapping(client_id="client_id", transaction_date="tx_date", department="dept"),
    )

    out = svc.department_breakdown(source, year_period(2025), year_period(2026))

    assert out.result.row_count == 2
    assert [r[2] for r in out.result.rows] == [1, 1]


def test_trend_respects_the_dedupe_option(svc: AnalyticsService, plan: AnalyticsSource) -> None:
    """L7."""
    kept = svc.client_trend(plan, "year", RetentionOptions(dedupe_exact_duplicates=False))

    assert {r[0]: r[3] for r in kept.result.rows}["2026"] == 6
