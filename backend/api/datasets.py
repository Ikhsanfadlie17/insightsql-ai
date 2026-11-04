"""Dataset endpoints: upload inspection, creation, listing, preview, deletion."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field

from backend.api.deps import get_dataset_service
from backend.core.errors import UploadError
from backend.schemas.datasets import DatasetColumn, DatasetRecord, UploadInspection
from backend.schemas.query import QueryResult
from backend.services.dataset_service import DatasetService

router = APIRouter(prefix="/datasets", tags=["datasets"])
Service = Annotated[DatasetService, Depends(get_dataset_service)]


class CreateDatasetRequest(BaseModel):
    """Create a dataset from an inspected upload."""

    upload_id: str
    sheet: str | None = None
    name: str | None = Field(default=None, max_length=200)


class RenameDatasetRequest(BaseModel):
    """New display name for a dataset."""

    name: str = Field(max_length=200)


@router.post("/inspect", response_model=UploadInspection)
async def inspect_upload(
    request: Request, service: Service, file: Annotated[UploadFile, File()]
) -> UploadInspection:
    """Validate an upload and list its sheets. Reads at most limit + 1 bytes."""
    settings = request.app.state.settings
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise UploadError(f"The file is larger than the {settings.max_upload_mb:g} MB limit.")
    return service.inspect_upload(file.filename or "", content)


@router.post("", response_model=DatasetRecord, status_code=201)
def create_dataset(
    body: CreateDatasetRequest, service: Service, background: BackgroundTasks
) -> DatasetRecord:
    """Register a dataset and ingest it in the background (poll ``GET /datasets/{id}``)."""
    record = service.create_dataset(body.upload_id, body.sheet, body.name)
    background.add_task(service.ingest, record.id, body.upload_id, record.sheet_name)
    return record


@router.get("", response_model=list[DatasetRecord])
def list_datasets(service: Service) -> list[DatasetRecord]:
    """List all datasets, newest first."""
    return service.list()


@router.get("/{dataset_id}", response_model=DatasetRecord)
def get_dataset(dataset_id: str, service: Service) -> DatasetRecord:
    """Return one dataset, including its ingestion status."""
    return service.get(dataset_id)


@router.patch("/{dataset_id}", response_model=DatasetRecord)
def rename_dataset(dataset_id: str, body: RenameDatasetRequest, service: Service) -> DatasetRecord:
    """Rename a dataset."""
    return service.rename(dataset_id, body.name)


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, service: Service) -> Response:
    """Delete a dataset, its DuckDB file, and all related metadata."""
    service.delete(dataset_id)
    return Response(status_code=204)


@router.get("/{dataset_id}/columns", response_model=list[DatasetColumn])
def get_columns(dataset_id: str, service: Service) -> list[DatasetColumn]:
    """Return the dataset's columns with original headers and inferred types."""
    return service.columns(dataset_id)


@router.get("/{dataset_id}/preview", response_model=QueryResult)
def preview_dataset(
    dataset_id: str, service: Service, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> QueryResult:
    """Return the first rows of a ready dataset."""
    return service.preview(dataset_id, limit)
