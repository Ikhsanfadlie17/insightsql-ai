"""Download endpoints: an AI Analyst answer or a client-analytics comparison as a file."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from backend.core.errors import NothingToExportError
from backend.schemas.analysis import AnalysisResponse
from backend.schemas.analytics import CompareRequest
from backend.schemas.exports import ExportFormat, ExportMeta
from backend.schemas.insights import Insight
from backend.schemas.query import QueryResult
from backend.services import export_service as export

router = APIRouter(tags=["exports"])
Format = Annotated[ExportFormat, Query()]


def _file(
    fmt: ExportFormat,
    name: str,
    result: QueryResult | None,
    meta: ExportMeta,
    insight: Insight | None = None,
) -> Response:
    if fmt == "sql":
        if not meta.sql:
            raise NothingToExportError("There is no SQL to export for this result.")
        content = export.to_sql(meta.sql, meta)
    else:
        if result is None:
            raise NothingToExportError("There is no result table to export.")
        if fmt == "csv":
            content = export.to_csv(result)
        elif fmt == "xlsx":
            content = export.to_excel(result, meta)
        else:
            content = export.to_pdf(result, meta, insight)
    filename = export.safe_filename(name, fmt)
    return Response(
        content=content,
        media_type=export.MIME_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _analysis_meta(analysis: AnalysisResponse, dataset_name: str) -> ExportMeta:
    retention = analysis.retention
    definitions = (
        dict(retention.definitions)
        if retention is not None
        else {k.label: k.definition for k in analysis.kpis if k.definition}
    )
    return ExportMeta(
        title=analysis.question[:80],
        question=analysis.question,
        dataset_name=dataset_name,
        generated_at=datetime.now(UTC),
        periods=[retention.previous, retention.current] if retention is not None else [],
        definitions=definitions,
        sql=analysis.sql,
        assumptions=analysis.assumptions,
        notes=[w.message for w in analysis.warnings],
    )


@router.get("/analyses/{analysis_id}/export")
def export_analysis(analysis_id: str, request: Request, format: Format) -> Response:  # noqa: A002
    """Download one answer as CSV, Excel (result + metadata), PDF, or SQL."""
    analysis = request.app.state.history_repo.get_analysis(analysis_id)
    dataset = request.app.state.dataset_service.get(analysis.dataset_id)
    result = analysis.table if analysis.status == "answered" else None
    meta = _analysis_meta(analysis, dataset.name)
    return _file(format, analysis.question[:80], result, meta, analysis.insight)


@router.post("/datasets/{dataset_id}/client-analytics/export")
def export_client_analytics(
    dataset_id: str,
    body: CompareRequest,
    request: Request,
    format: Format,  # noqa: A002
) -> Response:
    """Download a period comparison with its periods, definitions, and template SQL."""
    dataset = request.app.state.dataset_service.get(dataset_id)
    r = request.app.state.analytics.compare(dataset_id, body.previous, body.current, body.options)
    title = f"Client analytics: {r.previous.label} vs {r.current.label}"
    meta = ExportMeta(
        title=title,
        dataset_name=dataset.name,
        generated_at=datetime.now(UTC),
        periods=[r.previous, r.current],
        definitions=r.definitions,
        sql=";\n\n".join(s.strip().rstrip(";") for s in r.sql) or None,
        notes=[w.message for w in r.warnings],
    )
    name = f"client analytics {r.previous.label} vs {r.current.label}"
    return _file(format, name, export.retention_table(r), meta)
