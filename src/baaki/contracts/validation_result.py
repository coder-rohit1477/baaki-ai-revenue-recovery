"""ValidationResult — deterministic normalisation; claim evidence, never authority (§1.2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baaki.domain.enums import RejectionReason, ValidationOutcome
from baaki.domain.errors import ContractViolation
from baaki.domain.money import ClaimedPaise

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class NormalizedInterpretation(BaseModel):
    """`normalized` for kind=INTERPRETATION. Money here is ClaimedPaise (V7)."""

    model_config = _STRICT

    intent: str
    promised_date: date | None = None
    promised_paise: ClaimedPaise | None = None
    invoice_ids: list[UUID] = Field(default_factory=list)
    contact_id: UUID | None = None
    effective_confidence: float = Field(ge=0, le=1)


class ValidationResult(BaseModel):
    model_config = _STRICT

    validation_id: UUID
    proposal_id: UUID
    trace_id: UUID  # derived from the proposal by W08 (V8)
    account_id: UUID  # derived
    business_date: date  # derived
    outcome: ValidationOutcome
    rejection_reasons: list[RejectionReason] = Field(default_factory=list)
    normalized: NormalizedInterpretation | dict[str, Any] | None = None
    checks_run: list[dict[str, Any]]
    validator_version: str = Field(min_length=1)
    validator_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime

    @model_validator(mode="after")
    def _invariants(self) -> ValidationResult:
        if self.outcome is ValidationOutcome.PASS:  # V2
            if self.rejection_reasons or self.normalized is None:
                raise ContractViolation("PASS requires empty reasons and normalized present (V2)")
        else:  # V3
            if not self.rejection_reasons or self.normalized is not None:
                raise ContractViolation("REJECT requires reasons and normalized=None (V3)")
        return self
