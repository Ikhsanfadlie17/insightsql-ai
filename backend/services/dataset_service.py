"""Dataset lifecycle: upload inspection, ingestion into DuckDB, preview, deletion.

Each dataset (one CSV, or one Excel sheet) is stored as its own DuckDB file
containing a single table named ``dataset``. Raw uploads are kept until they are
older than ``cleanup_stale_uploads``' age limit so several sheets of one workbook
can be ingested from a single upload.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from backend.core.config import Settings
from backend.core.errors import NotFoundError, UploadError, ValidationError
from backend.core.redaction import redact
from backend.repositories.dataset_repo import DatasetRepository
from backend.schemas.datasets import (
    DatasetColumn,
    DatasetRecord,
    DateOrder,
    FileType,
    ImportLayout,
    NumberFormat,
    SheetInfo,
    UploadInspection,
)
from backend.schemas.profile import DatasetProfile
from backend.schemas.query import QueryResult
from backend.services.column_normalizer import normalize_columns
from backend.services.data_profiler import profile_dataset
from backend.services.file_loader import (
    apply_layout,
    blank_na,
    detect_file_type,
    detect_layout,
    layout_headers,
    list_sheets,
    load_table,
    read_csv_cells,
    read_headers,
    read_samples,
    sample_rows,
)
from backend.services.query_results import rows_to_jsonable
from backend.services.sql_utils import quote_identifier
from backend.services.type_inference import detect_types, infer_types

logger = logging.getLogger(__name__)

TABLE_NAME = "dataset"
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_META_FILE = "upload.json"
_HEAD_BYTES = 64 * 1024
INSPECT_SAMPLE_ROWS = 500  # rows read at inspection to suggest column types


def _safe_filename(filename: str) -> str:
    """Basename with unsafe characters replaced, at most 120 chars, extension kept."""
    name = Path(filename.replace("\\", "/")).name
    name = _SAFE_FILENAME.sub("_", name).strip(" .") or "upload"
    stem, suffix = Path(name).stem, Path(name).suffix[:10]
    return stem[: 120 - len(suffix)] + suffix


class DatasetService:
    """Create, ingest, preview, and delete datasets."""

    def __init__(self, settings: Settings, repo: DatasetRepository) -> None:
        self._settings = settings
        self._repo = repo
        # Uploads with an import created but not finished: never removed by cleanup.
        self._uploads_in_use: Counter[str] = Counter()
        self._uploads_lock = threading.Lock()

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

    @classmethod
    def _sheets(cls, path: Path, file_type: FileType, content: bytes) -> list[SheetInfo]:
        if file_type == "xlsx":
            sheets = list_sheets(path)
            if not sheets:
                raise UploadError("The workbook has no sheets.")
        else:
            lines = [line for line in content.splitlines() if line.strip()]
            sheets = [SheetInfo(name=path.stem, row_count=max(len(lines) - 1, 0))]
        samples = cls._samples(path, file_type)
        for sheet in sheets:
            cls._describe(
                sheet, samples.get(sheet.name if file_type == "xlsx" else None), file_type
            )
        return sheets

    @staticmethod
    def _samples(
        path: Path, file_type: FileType, header_row: int | None = None
    ) -> dict[str | None, pd.DataFrame]:
        """Raw cells for the inspection: the whole CSV (cheap, and it gives exact row
        counts), or the first rows of every Excel sheet (one pass over the workbook)."""
        try:
            if file_type == "csv":
                return {None: read_csv_cells(path)}
            return read_samples(path, file_type, INSPECT_SAMPLE_ROWS, header_row)
        except UploadError:
            return {}  # e.g. an empty file: the import reports it
        except Exception:
            logger.warning("Could not sample the upload", exc_info=True)
            return {}

    @classmethod
    def _describe(
        cls,
        sheet: SheetInfo,
        raw: pd.DataFrame | None,
        file_type: FileType,
        header_row: int | None = None,
    ) -> None:
        """Fill in where the table is (smart import), its headers, and type hints."""
        if raw is None or raw.empty:
            return
        # The whole CSV was read; an Excel read is complete when it ended before the limit.
        complete = file_type == "csv" or len(raw) < sample_rows(INSPECT_SAMPLE_ROWS, header_row)
        layout = detect_layout(
            raw,
            header_index=None if header_row is None else header_row - 1,
            skip_template_rows=complete,
        )
        sheet.columns = layout_headers(raw, layout)
        sheet.header_row = layout.header_row
        sheet.header_detected = not layout.chosen
        sheet.ignored_columns = layout.ignored_column_labels
        sheet.template_rows = layout.template_rows
        sheet.rows_above_header = layout.rows_above_header
        sheet.table_like_rows_above = layout.table_like_rows_above
        table = apply_layout(raw, layout)
        if file_type == "csv":
            table = blank_na(table).dropna(how="all")
        if complete:
            sheet.row_count = len(table)  # exact: what the import will store
        else:
            sheet.row_count = max(sheet.row_count - layout.header_index, 0)  # estimate
        body = table.head(INSPECT_SAMPLE_ROWS).set_axis(range(len(table.columns)), axis=1)
        sheet.detected_types = cls._detected_types(body, sheet.columns, file_type)

    def sheet_layout(self, upload_id: str, sheet: str | None, header_row: int) -> SheetInfo:
        """The sheet described with a header row chosen by the user (import form)."""
        path, file_type = self._upload(upload_id)
        if file_type == "xlsx":
            infos = {s.name: s for s in list_sheets(path)}
            name = sheet or next(iter(infos))
            if name not in infos:
                raise ValidationError(f"Sheet '{name}' does not exist in this workbook.")
            info = infos[name]
        else:
            name = None
            lines = [line for line in path.read_bytes().splitlines() if line.strip()]
            info = SheetInfo(name=path.stem, row_count=max(len(lines) - 1, 0))
        raw = self._samples(path, file_type, header_row).get(name)
        if raw is not None and header_row > len(raw):
            raise ValidationError(f"Row {header_row} is past the end of this sheet.")
        self._describe(info, raw, file_type, header_row)
        return info

    @staticmethod
    def _detected_types(
        frame: pd.DataFrame | None, headers: list[str], file_type: FileType
    ) -> dict[str, str]:
        """Type of each header (as shown in the form) in a sample of rows; {} on failure.

        Only a hint for the import form: the stored types come from all rows at ingest.
        Columns are matched by position, so the keys are exactly ``SheetInfo.columns``
        (e.g. "2024-01-01" for a date header cell, "NA" for a CSV header written NA).
        """
        if frame is None or frame.empty:
            return {}
        width = frame.shape[1]
        labels = (list(headers) + [""] * width)[:width]
        try:
            names = normalize_columns(labels)  # same names as at ingest (identifier rule)
            sample = frame.set_axis([n.name for n in names], axis=1)
            number_format = "plain" if file_type == "xlsx" else "auto"
            types = detect_types(sample, number_format=number_format)
        except Exception:
            logger.warning("Type hints failed for an upload sample", exc_info=True)
            return {}
        detected: dict[str, str] = {}
        for label, n in zip(labels, names, strict=True):
            if label and label not in detected:
                detected[label] = types[n.name]
        return detected

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
        with self._uploads_lock:
            in_use = set(self._uploads_in_use)
        for entry in root.iterdir():
            if entry.name in in_use:
                continue
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed

    # ---------------------------------------------------------------- datasets
    def create_dataset(
        self,
        upload_id: str,
        sheet: str | None,
        name: str | None,
        number_format: NumberFormat = "auto",
        date_order: DateOrder = "auto",
        text_columns: list[str] | None = None,
        header_row: int | None = None,
        skip_template_rows: bool = True,
    ) -> DatasetRecord:
        """Register a dataset in ``processing`` state; call :meth:`ingest` afterwards.

        ``number_format`` applies to CSV files only: Excel numeric cells are already
        canonical, so Excel imports always use ``plain``. ``text_columns`` lists original
        headers the user wants kept as text (IDs, codes, phone numbers).

        Raises:
            ValidationError: Also when ``text_columns`` names a header that does not exist.

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
            number_format = "plain"
        else:
            sheet = None

        requested = list(dict.fromkeys(text_columns or []))
        if requested:
            headers = set(read_headers(path, file_type, sheet, header_row))
            unknown = [h for h in requested if h not in headers]
            if unknown:
                raise ValidationError(
                    "Columns to keep as text were not found: " + ", ".join(unknown) + "."
                )

        stem = Path(path.name).stem
        default_name = f"{stem} — {sheet}" if sheet else stem
        record = DatasetRecord(
            id=uuid.uuid4().hex,
            name=(name or "").strip()[:120] or default_name,
            source_filename=path.name,
            sheet_name=sheet,
            status="processing",
            created_at=datetime.now(UTC),
            number_format=number_format,
            date_order=date_order,
            text_columns=requested,
            header_row=header_row,
            skip_template_rows=skip_template_rows,
        )
        created = self._repo.create(record)
        # Phase 6 review L2: the import restarts the upload's 24 h clock, and cleanup
        # skips the upload until ingestion has finished.
        os.utime(path.parent)
        with self._uploads_lock:
            self._uploads_in_use[upload_id] += 1
        return created

    def ingest(self, dataset_id: str, upload_id: str, sheet: str | None) -> None:
        """Load, normalize, type, and store the data; sets the status to ready/failed.

        Never raises: failures are recorded on the dataset with a safe message.
        """
        target = self.duckdb_path(dataset_id)
        try:
            record = self._repo.get(dataset_id)
            path, file_type = self._upload(upload_id)
            frame, layout = load_table(
                path,
                file_type,
                sheet,
                header_row=record.header_row,
                skip_template_rows=record.skip_template_rows,
            )
            self._repo.save_layout(
                dataset_id,
                ImportLayout(
                    header_row=layout.header_row,
                    header_chosen=layout.chosen,
                    ignored_columns=layout.ignored_column_labels,
                    template_rows_skipped=layout.template_rows,
                    rows_above_header=layout.rows_above_header,
                    table_like_rows_above=layout.table_like_rows_above,
                ),
            )
            names = normalize_columns([str(c) for c in frame.columns])
            frame.columns = [n.name for n in names]
            inference = infer_types(
                frame,
                number_format="plain" if file_type == "xlsx" else record.number_format,
                date_order=record.date_order,
                text_columns={n.name for n in names if n.original in record.text_columns},
            )
            self._write_duckdb(target, inference.dataframe, inference.types)
            columns = [
                DatasetColumn(
                    position=i,
                    name=n.name,
                    original_name=n.original,
                    logical_type=inference.types[n.name],
                    failed_conversions=inference.failed_counts.get(n.name, 0),
                    notes=inference.notes.get(n.name, []),
                )
                for i, n in enumerate(names)
            ]
            self._repo.save_columns(dataset_id, columns)
            self._save_profile(dataset_id, target, columns)
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
        finally:
            with self._uploads_lock:
                self._uploads_in_use[upload_id] -= 1
                if self._uploads_in_use[upload_id] <= 0:
                    del self._uploads_in_use[upload_id]

    def _save_profile(self, dataset_id: str, path: Path, columns: list[DatasetColumn]) -> None:
        """Profile at ingest time; a profiling failure must not fail the import."""
        try:
            self._repo.save_document(dataset_id, "profile", profile_dataset(path, columns))
        except Exception:
            logger.exception(
                "Profiling failed for dataset %s; it will be retried on request", dataset_id
            )

    def profile(self, dataset_id: str) -> DatasetProfile:
        """Return the stored profile, computing (and storing) it if it is missing."""
        self.require_ready(dataset_id)
        stored = self._repo.get_document(dataset_id, "profile", DatasetProfile)
        if stored is not None:
            return stored
        profile = profile_dataset(self.duckdb_path(dataset_id), self._repo.get_columns(dataset_id))
        self._repo.save_document(dataset_id, "profile", profile)
        return profile

    def _mark_failed(self, dataset_id: str, message: str) -> None:
        try:
            self._repo.update_status(dataset_id, "failed", error=redact(message))
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
            f"CAST({quote_identifier(c)} AS DATE) AS {quote_identifier(c)}"
            if types[c] == "date"
            else quote_identifier(c)
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
        # A processing dataset may be deleted: ingest notices and cleans up (Phase 2 L2).
        self._repo.get(dataset_id)
        path = self.duckdb_path(dataset_id)
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".duckdb.wal").unlink(missing_ok=True)
        except PermissionError as exc:
            # Windows: a running query, profile, or export still has the file open.
            raise ValidationError(
                "This dataset is in use by a running query or export. Try again in a moment."
            ) from exc
        self._repo.delete(dataset_id)
