"""Dataset lifecycle: upload inspection, ingestion into DuckDB, preview, deletion.

Each dataset (one CSV, or one Excel sheet) is stored as its own DuckDB file
containing a single table named ``dataset``. Raw uploads are kept until they are
older than ``cleanup_stale_uploads``' age limit so several sheets of one workbook
can be ingested from a single upload.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from backend.core.config import Settings
from backend.core.errors import NotFoundError, UploadError, ValidationError
from backend.repositories.dataset_repo import DatasetRepository
from backend.schemas.datasets import (
    DatasetColumn,
    DatasetRecord,
    FileType,
    SheetInfo,
    UploadInspection,
)
from backend.schemas.query import QueryResult
from backend.services.column_normalizer import normalize_columns
from backend.services.file_loader import detect_file_type, list_sheets, load_dataframe
from backend.services.query_results import rows_to_jsonable
from backend.services.type_inference import infer_types

logger = logging.getLogger(__name__)

TABLE_NAME = "dataset"
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_META_FILE = "upload.json"
_HEAD_BYTES = 64 * 1024


def _safe_filename(filename: str) -> str:
    """Basename with unsafe characters replaced, at most 120 chars, extension kept."""
    name = Path(filename.replace("\\", "/")).name
    name = _SAFE_FILENAME.sub("_", name).strip(" .") or "upload"
    stem, suffix = Path(name).stem, Path(name).suffix[:10]
    return stem[: 120 - len(suffix)] + suffix


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class DatasetService:
    """Create, ingest, preview, and delete datasets."""

    def __init__(self, settings: Settings, repo: DatasetRepository) -> None:
        self._settings = settings
        self._repo = repo

    # ----------------------------------------------------------------- uploads
    def inspect_upload(self, filename: str, content: bytes) -> UploadInspection:
        """Validate and store an upload, returning its sheets (or single CSV table).

        Raises:
            UploadError: If the file is empty, too large, or not a valid CSV/XLSX.
        """
        if not content:
            raise UploadError("The uploaded file is empty.")
        if len(content) > self._settings.max_upload_bytes:
            raise UploadError(
                f"The file is larger than the {self._settings.max_upload_mb:g} MB limit."
            )
        safe_name = _safe_filename(filename)
        file_type = detect_file_type(safe_name, content[:_HEAD_BYTES])

        upload_id = uuid.uuid4().hex
        upload_dir = self._settings.uploads_dir / upload_id
        upload_dir.mkdir(parents=True)
        path = upload_dir / safe_name
        path.write_bytes(content)
        try:
            sheets = self._sheets(path, file_type, content)
        except UploadError:
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise
        meta = {"filename": safe_name, "file_type": file_type}
        (upload_dir / _META_FILE).write_text(json.dumps(meta), encoding="utf-8")
        return UploadInspection(
            upload_id=upload_id, filename=safe_name, file_type=file_type, sheets=sheets
        )

    @staticmethod
    def _sheets(path: Path, file_type: FileType, content: bytes) -> list[SheetInfo]:
        if file_type == "xlsx":
            sheets = list_sheets(path)
            if not sheets:
                raise UploadError("The workbook has no sheets.")
            return sheets
        lines = [line for line in content.splitlines() if line.strip()]
        return [SheetInfo(name=path.stem, row_count=max(len(lines) - 1, 0))]

    def _upload(self, upload_id: str) -> tuple[Path, FileType]:
        if not _UPLOAD_ID.match(upload_id):
            raise NotFoundError("Upload not found. Please upload the file again.")
        meta_path = self._settings.uploads_dir / upload_id / _META_FILE
        if not meta_path.exists():
            raise NotFoundError("Upload not found. Please upload the file again.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta_path.parent / meta["filename"], meta["file_type"]

    def cleanup_stale_uploads(self, max_age_hours: float = 24) -> int:
        """Delete raw uploads older than ``max_age_hours``; returns how many were removed."""
        root = self._settings.uploads_dir
        if not root.exists():
            return 0
        cutoff = time.time() - max_age_hours * 3600
        removed = 0
        for entry in root.iterdir():
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed

    # ---------------------------------------------------------------- datasets
    def create_dataset(self, upload_id: str, sheet: str | None, name: str | None) -> DatasetRecord:
        """Register a dataset in ``processing`` state; call :meth:`ingest` afterwards.

        Raises:
            NotFoundError: Unknown or expired upload.
            ValidationError: The sheet does not exist in the workbook.
        """
        path, file_type = self._upload(upload_id)
        if file_type == "xlsx":
            sheet_names = [s.name for s in list_sheets(path)]
            sheet = sheet or sheet_names[0]
            if sheet not in sheet_names:
                raise ValidationError(f"Sheet '{sheet}' does not exist in this workbook.")
        else:
            sheet = None

        stem = Path(path.name).stem
        default_name = f"{stem} — {sheet}" if sheet else stem
        record = DatasetRecord(
            id=uuid.uuid4().hex,
            name=(name or "").strip()[:120] or default_name,
            source_filename=path.name,
            sheet_name=sheet,
            status="processing",
            created_at=datetime.now(UTC),
        )
        return self._repo.create(record)

    def ingest(self, dataset_id: str, upload_id: str, sheet: str | None) -> None:
        """Load, normalize, type, and store the data; sets the status to ready/failed.

        Never raises: failures are recorded on the dataset with a safe message.
        """
        target = self.duckdb_path(dataset_id)
        try:
            path, file_type = self._upload(upload_id)
            frame = load_dataframe(path, file_type, sheet)
            names = normalize_columns([str(c) for c in frame.columns])
            frame.columns = [n.name for n in names]
            inference = infer_types(frame, locale_numbers=file_type == "csv")
            self._write_duckdb(target, inference.dataframe, inference.types)
            columns = [
                DatasetColumn(
                    position=i,
                    name=n.name,
                    original_name=n.original,
                    logical_type=inference.types[n.name],
                    failed_conversions=inference.failed_counts.get(n.name, 0),
                )
                for i, n in enumerate(names)
            ]
            self._repo.save_columns(dataset_id, columns)
            self._repo.update_status(
                dataset_id, "ready", row_count=len(frame), column_count=len(columns)
            )
            logger.info("Ingested dataset %s (%d rows)", dataset_id, len(frame))
        except (UploadError, NotFoundError) as exc:
            target.unlink(missing_ok=True)
            self._mark_failed(dataset_id, exc.message)
        except Exception:
            # Unexpected errors may contain paths or internals: log them, show a generic message.
            target.unlink(missing_ok=True)
            logger.exception("Ingestion failed for dataset %s", dataset_id)
            self._mark_failed(dataset_id, "The file could not be processed.")

    def _mark_failed(self, dataset_id: str, message: str) -> None:
        try:
            self._repo.update_status(dataset_id, "failed", error=message)
        except NotFoundError:
            logger.info("Dataset %s was deleted during ingestion", dataset_id)

    def recover_interrupted_imports(self) -> int:
        """Mark datasets left in ``processing`` (e.g. by a restart) as failed.

        Called once at startup, before any new ingestion can start. Also removes
        partially written DuckDB files. Returns how many datasets were affected.
        """
        stuck = [d for d in self._repo.list() if d.status == "processing"]
        for record in stuck:
            self._repo.update_status(
                record.id, "failed", error="The import was interrupted. Please import again."
            )
        if self._settings.datasets_dir.exists():
            for partial in self._settings.datasets_dir.glob("*.tmp.duckdb"):
                partial.unlink(missing_ok=True)
        return len(stuck)

    def _write_duckdb(self, target: Path, frame: pd.DataFrame, types: dict[str, str]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.duckdb")
        tmp.unlink(missing_ok=True)
        select_list = ", ".join(
            f"CAST({_quote(c)} AS DATE) AS {_quote(c)}" if types[c] == "date" else _quote(c)
            for c in frame.columns
        )
        with duckdb.connect(str(tmp)) as conn:
            conn.register("source_frame", frame)
            conn.execute(f"CREATE TABLE {TABLE_NAME} AS SELECT {select_list} FROM source_frame")
            conn.unregister("source_frame")
        tmp.replace(target)

    def get(self, dataset_id: str) -> DatasetRecord:
        """Return one dataset record."""
        return self._repo.get(dataset_id)

    def list(self) -> list[DatasetRecord]:
        """Return all datasets, newest first."""
        return self._repo.list()

    def columns(self, dataset_id: str) -> list[DatasetColumn]:
        """Return the stored columns of a dataset."""
        self._repo.get(dataset_id)
        return self._repo.get_columns(dataset_id)

    def rename(self, dataset_id: str, name: str) -> DatasetRecord:
        """Rename a dataset (1-120 visible characters)."""
        clean = name.strip()
        if not clean:
            raise ValidationError("The dataset name cannot be empty.")
        self._repo.rename(dataset_id, clean[:120])
        return self._repo.get(dataset_id)

    def duckdb_path(self, dataset_id: str) -> Path:
        """Location of a dataset's DuckDB file (IDs are server-generated hex)."""
        return self._settings.datasets_dir / f"{dataset_id}.duckdb"

    def require_ready(self, dataset_id: str) -> DatasetRecord:
        """Return the record, or raise ``ValidationError`` if it is not ready."""
        record = self._repo.get(dataset_id)
        if record.status != "ready":
            raise ValidationError(f"Dataset is not ready (status: {record.status}).")
        return record

    def preview(self, dataset_id: str, limit: int) -> QueryResult:
        """Return the first ``limit`` rows of a ready dataset."""
        self.require_ready(dataset_id)
        started = time.perf_counter()
        with duckdb.connect(str(self.duckdb_path(dataset_id)), read_only=True) as conn:
            cursor = conn.execute(f"SELECT * FROM {TABLE_NAME} LIMIT ?", [limit])
            columns = [d[0] for d in cursor.description]
            rows = rows_to_jsonable(cursor.fetchall())
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def delete(self, dataset_id: str) -> None:
        """Delete the dataset's DuckDB file and all of its metadata."""
        self._repo.get(dataset_id)
        path = self.duckdb_path(dataset_id)
        path.unlink(missing_ok=True)
        path.with_suffix(".duckdb.wal").unlink(missing_ok=True)
        self._repo.delete(dataset_id)
