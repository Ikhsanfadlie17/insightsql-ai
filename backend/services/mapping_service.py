"""Review and confirmation of column roles.

Analytics (Phase 5) may only use a mapping the user confirmed here. Suggestions
are returned as an unsaved draft.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb

from backend.core.errors import ValidationError
from backend.repositories.dataset_repo import DatasetRepository
from backend.schemas.datasets import ColumnNote, DatasetColumn
from backend.schemas.mapping import (
    ROLE_LABELS,
    ROLES,
    ColumnMapping,
    MappingState,
    MappingUpdate,
)
from backend.schemas.profile import DatasetProfile
from backend.services.column_roles import ROLE_TYPES, draft_mapping, suggest_roles
from backend.services.dataset_service import TABLE_NAME, DatasetService
from backend.services.sql_utils import quote_identifier as q

_TYPE_HINTS = {
    "transaction_date": "a date column",
    "revenue": "a numeric column",
    "client_name": "a text column",
}


def normalized_name_sql(column: str) -> str:
    """SQL expression for the name-based client identity (trim, collapse spaces, case-fold)."""
    return f"lower(trim(regexp_replace({q(column)}, '\\s+', ' ', 'g')))"


class MappingService:
    """Suggest, validate, store, and explain the column mapping of a dataset."""

    def __init__(self, datasets: DatasetService, repo: DatasetRepository) -> None:
        self._datasets = datasets
        self._repo = repo

    def state(self, dataset_id: str) -> MappingState:
        """Saved mapping (if any), the unsaved draft, suggestions, and warnings."""
        profile = self._datasets.profile(dataset_id)
        saved = self._repo.get_document(dataset_id, "mapping", ColumnMapping)
        suggestions = suggest_roles(profile)
        active = saved if saved is not None and saved.confirmed else None
        shown = active or draft_mapping(suggestions)
        return MappingState(
            mapping=active,
            draft=draft_mapping(suggestions),
            suggestions=suggestions,
            compatible_columns={
                role: [
                    c.name
                    for c in profile.columns
                    if c.logical_type in ROLE_TYPES[role] and c.null_count < profile.row_count
                ]
                for role in ROLES
            },
            warnings=self._warnings(dataset_id, shown, profile),
            unique_clients=self._unique_clients(dataset_id, active) if active else None,
        )

    def confirm(self, dataset_id: str, update: MappingUpdate) -> MappingState:
        """Validate the user's choice and store it as the confirmed mapping."""
        self._datasets.require_ready(dataset_id)
        columns = {c.name: c for c in self._repo.get_columns(dataset_id)}
        self._validate(update, columns)
        mapping = ColumnMapping(
            **update.model_dump(), confirmed=True, confirmed_at=datetime.now(UTC)
        )
        self._repo.save_document(dataset_id, "mapping", mapping)
        return self.state(dataset_id)

    @staticmethod
    def _validate(update: MappingUpdate, columns: dict[str, DatasetColumn]) -> None:
        seen: dict[str, str] = {}
        for role in ROLES:
            name = getattr(update, role)
            if name is None:
                continue
            label = ROLE_LABELS[role]
            column = columns.get(name)
            if column is None:
                raise ValidationError(f"Column '{name}' does not exist in this dataset.")
            if column.logical_type not in ROLE_TYPES[role]:
                hint = _TYPE_HINTS.get(role, "a " + "/".join(sorted(ROLE_TYPES[role])) + " column")
                raise ValidationError(
                    f"Column '{column.original_name}' ({column.logical_type}) cannot be used "
                    f"as {label}: it must be {hint}."
                )
            if name in seen:
                raise ValidationError(
                    f"Column '{column.original_name}' is used for both {seen[name]} and {label}."
                )
            seen[name] = label

    def _unique_clients(self, dataset_id: str, mapping: ColumnMapping) -> int | None:
        if mapping.client_id:
            expression = q(mapping.client_id)
        elif mapping.client_name:
            expression = normalized_name_sql(mapping.client_name)
        else:
            return None
        sql = f"SELECT COUNT(DISTINCT {expression}) FROM {TABLE_NAME}"
        with duckdb.connect(str(self._datasets.duckdb_path(dataset_id)), read_only=True) as conn:
            return int(conn.execute(sql).fetchone()[0])

    def _inconsistent_names(self, dataset_id: str, id_column: str, name_column: str) -> int:
        sql = (
            f"SELECT COUNT(*) FROM (SELECT {q(id_column)} FROM {TABLE_NAME} "
            f"WHERE {q(id_column)} IS NOT NULL GROUP BY 1 "
            f"HAVING COUNT(DISTINCT {q(name_column)}) > 1)"
        )
        with duckdb.connect(str(self._datasets.duckdb_path(dataset_id)), read_only=True) as conn:
            return int(conn.execute(sql).fetchone()[0])

    def _warnings(
        self, dataset_id: str, mapping: ColumnMapping, profile: DatasetProfile
    ) -> list[ColumnNote]:
        by_name = {c.name: c for c in profile.columns}
        notes: list[ColumnNote] = []

        def add(level: str, code: str, message: str) -> None:
            notes.append(ColumnNote(level=level, code=code, message=message))

        if mapping.client_id:
            id_profile = by_name[mapping.client_id]
            if id_profile.null_count:
                add(
                    "warning",
                    "missing_client_ids",
                    f"{id_profile.null_count:,} row(s) have no client ID. They are excluded "
                    "from client counts and client analytics.",
                )
            for note in id_profile.notes:
                if note.level == "warning":
                    add("warning", f"client_id_{note.code}", f"Client ID column: {note.message}")
            if mapping.client_name:
                inconsistent = self._inconsistent_names(
                    dataset_id, mapping.client_id, mapping.client_name
                )
                if inconsistent:
                    add(
                        "info",
                        "inconsistent_client_names",
                        f"{inconsistent:,} client ID(s) appear with more than one name (for "
                        "example different spellings). Clients are counted by ID, so totals "
                        "are not affected, but the names may need cleaning.",
                    )
        elif mapping.client_name:
            add(
                "warning",
                "name_identity",
                "No client ID column is selected, so clients are identified by name "
                "(trimmed, spaces collapsed, case ignored). Different spellings of one "
                "client will be counted as different clients. Select a client ID if one exists.",
            )
        else:
            add(
                "info",
                "no_client_identity",
                "Select a client ID (or client name) column to enable client analytics.",
            )

        if mapping.transaction_date:
            date_profile = by_name[mapping.transaction_date]
            if mapping.client_id or mapping.client_name:
                add(
                    "warning",
                    "history_completeness",
                    f"New and Reactivated clients are classified from the history in this "
                    f"dataset, which starts on {date_profile.date_min}. Clients who were "
                    "active before that date but are missing from the data will be counted "
                    "as New (or not recognized as Reactivated).",
                )
            if date_profile.null_count:
                add(
                    "warning",
                    "missing_transaction_dates",
                    f"{date_profile.null_count:,} row(s) have no transaction date and are "
                    "excluded from period analysis.",
                )
        else:
            add(
                "info",
                "no_transaction_date",
                "Select a transaction date column to enable period comparisons and trends.",
            )
        return notes
