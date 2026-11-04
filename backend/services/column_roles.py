"""Heuristic suggestions of which column plays which role.

Suggestions are never applied automatically: the UI shows them with their reasons
and the user confirms or changes them (see ``mapping_service``). Rules:

* A column is only considered for a role if its type fits (e.g. dates for
  ``transaction_date``, numbers for ``revenue``).
* Header words (Indonesian and English) provide most of the evidence; for client
  identity there is no suggestion at all without a matching header word.
* Profile statistics adjust the score (repeating IDs, low-cardinality categories,
  birth-date headers, mostly-missing columns).
"""

from __future__ import annotations

from backend.schemas.mapping import ROLES, ColumnMapping, Role, RoleCandidate, RoleSuggestions
from backend.schemas.profile import ColumnProfile, DatasetProfile

MAX_CANDIDATES = 3
MIN_CONFIDENCE = 0.3
MAX_CATEGORY_VALUES = 50

ROLE_TYPES: dict[Role, set[str]] = {
    "client_id": {"text", "integer"},
    "client_name": {"text"},
    "transaction_date": {"date", "datetime"},
    "department": {"text", "integer"},
    "service_type": {"text", "integer"},
    "revenue": {"integer", "numeric"},
    "status": {"text", "integer", "boolean"},
}

_ENTITY = {
    "klien", "client", "clients", "pelanggan", "customer", "customers", "cust", "nasabah",
    "mitra", "member", "anggota", "pasien", "patient", "perusahaan", "company", "akun",
    "account", "konsumen", "consumer", "buyer", "pembeli",
}  # fmt: skip
_ID = {"id", "kode", "code", "no", "nomor", "nomer", "number", "num", "nik", "npwp", "kd"}
_NAME = {"nama", "name"}
_DATE = {
    "tanggal", "tgl", "date", "waktu", "time", "periode", "period", "transaksi",
    "transaction", "layanan", "service", "order", "invoice", "created", "timestamp",
}  # fmt: skip
_NOT_TRANSACTION_DATE = {"lahir", "birth", "dob", "ulang", "expired", "kadaluarsa", "jatuh"}
_DEPARTMENT = {
    "departemen", "department", "dept", "divisi", "division", "unit", "bagian", "cabang",
    "branch", "bidang", "region", "wilayah",
}  # fmt: skip
_SERVICE = {
    "layanan", "service", "services", "jenis", "produk", "product", "kategori", "category",
    "tipe", "type", "paket", "package", "program", "item",
}  # fmt: skip
_REVENUE = {
    "pendapatan", "revenue", "amount", "nilai", "total", "harga", "price", "omzet", "omset",
    "sales", "penjualan", "biaya", "fee", "rp", "idr", "nominal", "tagihan", "income",
}  # fmt: skip
_STATUS = {"status", "state", "keterangan"}


def _tokens(column: ColumnProfile) -> set[str]:
    return set(column.name.split("_"))


def _non_null(column: ColumnProfile, row_count: int) -> int:
    return row_count - column.null_count


def _score(role: Role, column: ColumnProfile, row_count: int) -> tuple[float, list[str]] | None:
    """Return (confidence, reasons) or None when the column cannot play the role."""
    if column.logical_type not in ROLE_TYPES[role]:
        return None
    non_null = _non_null(column, row_count)
    if non_null == 0:
        return None
    tokens = _tokens(column)
    header = column.original_name or column.name
    reasons: list[str] = []
    score = 0.0

    def hit(words: set[str]) -> set[str]:
        return tokens & words

    if role == "client_id":
        if hit(_ID) and hit(_ENTITY):
            score += 0.6
            reasons.append(f"Header '{header}' names an identifier of a client")
        elif hit(_ID) and not hit(_NAME):
            score += 0.35
            reasons.append(f"Header '{header}' names an identifier")
        elif hit(_ENTITY) and not hit(_NAME):
            score += 0.25
            reasons.append(f"Header '{header}' refers to clients")
        else:
            return None  # never guess a client identity without header evidence
        if column.unique_count < 2:
            return None
        if column.unique_count < non_null:
            score += 0.2
            reasons.append(
                f"{column.unique_count:,} distinct values repeat across {non_null:,} rows, "
                "as client IDs do in transaction data"
            )
        else:
            score += 0.05
            reasons.append("Every value is unique (could also be a transaction ID)")
    elif role == "client_name":
        if hit(_NAME) and hit(_ENTITY):
            score += 0.6
            reasons.append(f"Header '{header}' names a client name")
        elif hit(_NAME):
            score += 0.4
            reasons.append(f"Header '{header}' names a name")
        else:
            return None
        if 1 < column.unique_count < non_null:
            score += 0.1
            reasons.append("Values repeat across rows")
    elif role == "transaction_date":
        score += 0.25
        reasons.append(f"'{header}' contains dates")
        if hit(_NOT_TRANSACTION_DATE):
            score -= 0.4
            reasons.append("Header suggests a non-transaction date (e.g. birth date)")
        elif hit(_DATE):
            score += 0.45
            reasons.append(f"Header '{header}' describes when something happened")
        if non_null / row_count >= 0.9:
            score += 0.1
            reasons.append("Filled in for at least 90% of rows")
    else:
        words = {
            "department": _DEPARTMENT,
            "service_type": _SERVICE,
            "revenue": _REVENUE,
            "status": _STATUS,
        }[role]
        if not hit(words):
            return None
        score += 0.6
        reasons.append(f"Header '{header}' matches {role.replace('_', ' ')}")
        if role == "revenue":
            if column.min is not None and column.min >= 0:
                score += 0.1
                reasons.append("All values are zero or positive")
        elif column.unique_count <= MAX_CATEGORY_VALUES and column.unique_count < non_null:
            score += 0.25
            reasons.append(f"{column.unique_count:,} distinct categories")
        else:
            score -= 0.3
            reasons.append(f"{column.unique_count:,} distinct values: too many for a category")

    confidence = round(max(0.0, min(1.0, score)), 2)
    return (confidence, reasons) if confidence >= MIN_CONFIDENCE else None


def suggest_roles(profile: DatasetProfile) -> list[RoleSuggestions]:
    """Rank candidate columns for every role (best first, at most three)."""
    suggestions: list[RoleSuggestions] = []
    for role in ROLES:
        candidates: list[RoleCandidate] = []
        for column in profile.columns:
            scored = _score(role, column, profile.row_count)
            if scored is None:
                continue
            confidence, reasons = scored
            candidates.append(
                RoleCandidate(
                    column=column.name,
                    original_name=column.original_name or column.name,
                    confidence=confidence,
                    reasons=reasons,
                )
            )
        candidates.sort(key=lambda c: (-c.confidence, c.column))
        suggestions.append(RoleSuggestions(role=role, candidates=candidates[:MAX_CANDIDATES]))
    return suggestions


def draft_mapping(suggestions: list[RoleSuggestions]) -> ColumnMapping:
    """Best unconfirmed proposal: highest confidence first, each column used once."""
    pairs = sorted(
        ((c.confidence, s.role, c.column) for s in suggestions for c in s.candidates),
        key=lambda p: (-p[0], ROLES.index(p[1])),
    )
    chosen: dict[str, str] = {}
    used: set[str] = set()
    for _, role, column in pairs:
        if role not in chosen and column not in used:
            chosen[role] = column
            used.add(column)
    return ColumnMapping(**chosen, confirmed=False)
