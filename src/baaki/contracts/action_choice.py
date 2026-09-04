"""ActionChoice and DecisionContext — the kernel's non-snapshot inputs (PHASE2_PLAN §6)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baaki.domain.enums import ActionType, Arm, Channel, DegradationLevel

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class ActionChoice(BaseModel):
    """What an arm strategy proposes. Carries identifiers only — never money."""

    model_config = _STRICT
    action: ActionType
    contact_id: UUID | None = None
    channel: Channel | None = None
    template_id: str | None = None
    followup_days: int | None = Field(default=None, ge=1, le=14)
    existing_link_ref: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)  # only for origin L0
    origin: DegradationLevel  # L0 (validated model proposal) | L1 (rules tree) | L2 (static cadence)


class DecisionContext(BaseModel):
    model_config = _STRICT
    trace_id: UUID
    arm: Arm
    degradation_level: DegradationLevel
    proposal_id: UUID | None = None
    validation_id: UUID | None = None
    business_date: date
    rejected_ambiguous: bool = False
    action_id: UUID  # pre-generated so SEND_PAYMENT_LINK notes can reference it; W10 reuses it
