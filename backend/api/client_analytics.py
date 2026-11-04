"""Client analytics endpoints: deterministic metrics from a confirmed column mapping.

All numbers come from fixed SQL templates (no LLM). Endpoints answer 409
``mapping_required`` until the user has confirmed a client identity and a date column.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from backend.schemas.analytics import (
    AnalyticsCoverage,
    ClientListPage,
    ClientListRequest,
    CompareRequest,
    DepartmentBreakdown,
    Granularity,
    IdentityReport,
    RetentionResult,
    TrendResult,
)
from backend.services.analytics_service import DatasetAnalytics

router = APIRouter(prefix="/datasets/{dataset_id}/client-analytics", tags=["client analytics"])


def _analytics(request: Request) -> DatasetAnalytics:
    return request.app.state.analytics


@router.get("/coverage", response_model=AnalyticsCoverage)
def coverage(dataset_id: str, request: Request) -> AnalyticsCoverage:
    """Rows, unique clients, date range, and the years that can be compared."""
    return _analytics(request).coverage(dataset_id)


@router.post("/compare", response_model=RetentionResult)
def compare(dataset_id: str, body: CompareRequest, request: Request) -> RetentionResult:
    """Client categories and rates for a previous vs a current period (``end`` exclusive)."""
    return _analytics(request).compare(dataset_id, body.previous, body.current, body.options)


@router.post("/clients", response_model=ClientListPage)
def clients(dataset_id: str, body: ClientListRequest, request: Request) -> ClientListPage:
    """One page of the clients in a category (drill-down)."""
    return _analytics(request).clients(
        dataset_id,
        body.previous,
        body.current,
        body.category,
        body.offset,
        body.limit,
        body.options,
    )


@router.post("/departments", response_model=DepartmentBreakdown)
def departments(dataset_id: str, body: CompareRequest, request: Request) -> DepartmentBreakdown:
    """Clients per department in both periods (a client can be in several departments)."""
    return _analytics(request).departments(dataset_id, body.previous, body.current, body.options)


@router.get("/trend", response_model=TrendResult)
def trend(
    dataset_id: str,
    request: Request,
    granularity: Annotated[Granularity, Query()] = "month",
) -> TrendResult:
    """Active clients, new clients, and transactions per month, quarter, or year."""
    return _analytics(request).trend(dataset_id, granularity)


@router.get("/identity-issues", response_model=IdentityReport)
def identity_issues(dataset_id: str, request: Request) -> IdentityReport:
    """Missing identities and inconsistent IDs or names."""
    return _analytics(request).identity_issues(dataset_id)
