"""Column roles: suggestions, the user-confirmed mapping, and its state."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.datasets import ColumnNote

Role = Literal[
    "client_id",
    "client_name",
    "transaction_date",
    "department",
    "service_type",
    "revenue",
    "status",
]
ROLES: tuple[Role, ...] = (
    "client_id",
    "client_name",
    "transaction_date",
    "department",
    "service_type",
    "revenue",
    "status",
)
ROLE_LABELS: dict[Role, str] = {
    "client_id": "Client ID",
    "client_name": "Client name",
    "transaction_date": "Transaction date",
    "department": "Department",
    "service_type": "Service type",
    "revenue": "Revenue",
    "status": "Status",
}


class RoleCandidate(BaseModel):
    """One column that could play a role, with the evidence behind the suggestion."""

    column: str
    original_name: str
    confidence: float
    reasons: list[str]


class RoleSuggestions(BaseModel):
    """Ranked candidates (best first, at most 3) for one role."""

    role: Role
    candidates: list[RoleCandidate] = Field(default_factory=list)


class MappingUpdate(BaseModel):
    """The user's choice of column per role (None = not used)."""

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = None
    client_name: str | None = None
    transaction_date: str | None = None
    department: str | None = None
    service_type: str | None = None
    revenue: str | None = None
    status: str | None = None


class ColumnMapping(MappingUpdate):
    """A mapping plus whether a user confirmed it. Only confirmed mappings are used."""

    confirmed: bool = False
    confirmed_at: datetime | None = None


class MappingState(BaseModel):
    """Everything the UI needs to let the user review and confirm column roles."""

    mapping: ColumnMapping | None  # saved and confirmed by the user; None until then
    draft: ColumnMapping  # best suggestion per role; never saved or used automatically
    suggestions: list[RoleSuggestions]
    warnings: list[ColumnNote] = Field(default_factory=list)
    unique_clients: int | None = None  # only for a confirmed mapping with a client identity
