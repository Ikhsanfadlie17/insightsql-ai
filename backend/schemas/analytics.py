"""Client-analytics models: periods, retention results, drill-down, trend, identity checks.

Counts are unique client identities, never transactions. Rates are ``None`` when the
denominator is zero; the UI shows them as N/A.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.datasets import ColumnNote
from backend.schemas.query import QueryResult

Category = Literal[
    "new", "returning", "reactivated", "between_period", "not_returned", "existing_active"
]
CATEGORIES: tuple[str, ...] = (
    "new", "returning", "reactivated", "between_period", "not_returned", "existing_active",
)  # fmt: skip
Granularity = Literal["month", "quarter", "year"]
IdentityKind = Literal["client_id", "client_name"]

MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2200, 1, 1)


class Period(BaseModel):
    """A half-open date range ``[start, end)``: ``end`` is the first day *after* the period."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(max_length=60)
    start: date
    end: date

    @model_validator(mode="after")
    def _check(self) -> Period:
        if self.start >= self.end:
            raise ValueError("A period must start before it ends.")
        if self.start < MIN_DATE or self.end > MAX_DATE:
            raise ValueError("Period dates must be between 1900 and 2200.")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days


class RetentionOptions(BaseModel):
    """How rows are counted."""

    model_config = ConfigDict(extra="forbid")

    # Rows identical in every column are counted once in transaction counts.
    dedupe_exact_duplicates: bool = True


class CompareRequest(BaseModel):
    """Body of ``POST /client-analytics/compare`` (and the base of the other requests)."""

    model_config = ConfigDict(extra="forbid")

    previous: Period
    current: Period
    options: RetentionOptions = Field(default_factory=RetentionOptions)


class ClientListRequest(CompareRequest):
    category: Category
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class RetentionResult(BaseModel):
    """Period comparison. Partitions (checked by tests on every fixture):

    * current_active = new + returning + reactivated + between_period
    * previous_active = returning + not_returned

    ``existing_active`` (= returning + reactivated + between_period) overlaps those
    categories and must never be added to them.
    """

    previous: Period
    current: Period
    identity_column: str
    identity_kind: IdentityKind
    date_column: str

    previous_active: int
    current_active: int
    new: int
    returning: int
    reactivated: int
    between_period: int
    not_returned: int
    existing_active: int

    transactions_previous: int
    transactions_current: int
    repeat_transactions_previous: int
    repeat_transactions_current: int
    clients_with_repeat_previous: int
    clients_with_repeat_current: int

    retention_rate: float | None
    new_client_rate: float | None
    growth_rate: float | None

    excluded_missing_id_rows: int
    excluded_missing_date_rows: int
    duplicate_rows_excluded: int
    data_start: date | None
    data_end: date | None

    warnings: list[ColumnNote] = Field(default_factory=list)
    definitions: dict[str, str] = Field(default_factory=dict)
    sql: list[str] = Field(default_factory=list)


class ClientListPage(BaseModel):
    """One page of the clients in a category (for drill-down and export)."""

    category: Category
    previous: Period
    current: Period
    total: int
    offset: int
    limit: int
    result: QueryResult


class TrendResult(BaseModel):
    """Active clients, new clients, and transactions per month, quarter, or year.

    Buckets without data are included with zeros. The first/last bucket can be
    partial when the data starts or ends inside it.
    """

    granularity: Granularity
    result: QueryResult
    data_start: date | None
    data_end: date | None
    partial_first: bool = False
    partial_last: bool = False
    warnings: list[ColumnNote] = Field(default_factory=list)
    definitions: dict[str, str] = Field(default_factory=dict)
    sql: list[str] = Field(default_factory=list)


class DepartmentBreakdown(BaseModel):
    """Per-department client counts. A client can appear in several departments, so the
    rows must not be added up to a client total."""

    department_column: str
    previous: Period
    current: Period
    result: QueryResult
    clients_in_multiple_departments: int
    warnings: list[ColumnNote] = Field(default_factory=list)
    definitions: dict[str, str] = Field(default_factory=dict)
    sql: list[str] = Field(default_factory=list)


class IdentityReport(BaseModel):
    """Data-quality checks on the client identity."""

    identity_column: str
    identity_kind: IdentityKind
    missing_id_rows: int
    ids_differing_only_by_case_or_spacing: int
    ids_with_multiple_names: int | None  # None when no client name is mapped
    names_with_multiple_ids: int | None
    examples: QueryResult
    warnings: list[ColumnNote] = Field(default_factory=list)


class AnalyticsCoverage(BaseModel):
    """What the data covers; used to offer sensible periods."""

    identity_column: str
    identity_kind: IdentityKind
    date_column: str
    department_column: str | None
    rows: int
    unique_clients: int
    missing_id_rows: int
    missing_date_rows: int
    data_start: date | None
    data_end: date | None
    years: list[int]
    warnings: list[ColumnNote] = Field(default_factory=list)
