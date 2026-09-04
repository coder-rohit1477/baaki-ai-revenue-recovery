"""AgentProposal — raw model output; scope, not authority (ARCHITECTURE.md §1.1)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from baaki.domain.enums import (
    MONEY_KEY_DENY_PREFIX,
    MONEY_KEY_DENYLIST,
    Arm,
    ParseStatus,
    ProposalKind,
)
from baaki.domain.errors import ContractViolation

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class RawJson(RootModel[dict[str, Any] | list[Any]]):
    """Opaque immutable audit evidence (A6).

    No component in policy/, actions/, ledger/, reconcile/ or providers/ may read a semantic
    field from it. The only sanctioned reader is the audit viewer via `unwrap_for_audit()`;
    tests/arch/test_import_graph.py asserts no other module calls it.
    """

    model_config = ConfigDict(frozen=True)

    def unwrap_for_audit(self) -> dict[str, Any] | list[Any]:
        return self.root


def money_key_violations(parsed: dict[str, Any]) -> list[str]:
    """A3 — top-level denylist plus `settle*` prefix, matching the DB CHECK exactly."""
    bad: list[str] = []
    for k in parsed:
        if k in MONEY_KEY_DENYLIST or k.startswith(MONEY_KEY_DENY_PREFIX):
            bad.append(k)
    return bad


def typed_date_violations(parsed: dict[str, Any]) -> list[str]:
    """A4 — dates may only appear as `*_raw` spans; a key ending in `_date` is forbidden."""
    return [k for k in parsed if k.endswith("_date")]


class AgentProposal(BaseModel):
    model_config = _STRICT

    proposal_id: UUID
    trace_id: UUID
    account_id: UUID
    kind: ProposalKind
    invoice_id: UUID | None = None  # scope hint only (A7)
    business_date: date
    arm: Arm
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=64, max_length=64)
    input_hash: str = Field(min_length=64, max_length=64)
    raw_response: RawJson
    parsed: dict[str, Any] | None = None
    parse_status: ParseStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[dict[str, str]] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    created_at: datetime

    @model_validator(mode="after")
    def _invariants(self) -> AgentProposal:
        if self.arm is not Arm.TREATMENT:  # A5
            raise ContractViolation("AgentProposal.arm must be TREATMENT (A5)")
        if (self.parse_status is ParseStatus.OK) != (self.parsed is not None):  # A2
            raise ContractViolation("parse_status=OK iff parsed is present (A2)")
        if self.parsed is not None:
            bad = money_key_violations(self.parsed)
            if bad:  # A3
                raise ContractViolation(f"forbidden money key(s) in parsed: {bad} (A3)")
            dated = typed_date_violations(self.parsed)
            if dated:  # A4
                raise ContractViolation(f"typed date key(s) in parsed: {dated} (A4)")
        if self.parsed is None and self.confidence is not None:
            raise ContractViolation("confidence requires parsed (A2)")
        return self
