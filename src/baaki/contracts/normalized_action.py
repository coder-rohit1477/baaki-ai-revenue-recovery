"""NormalizedActionProposal — `ValidationResult.normalized` for kind = ACTION_PROPOSAL (P2-D3)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baaki.domain.enums import ActionType, Channel


class NormalizedActionProposal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    action: ActionType
    contact_id: UUID | None = None
    channel: Channel
    template_id: str | None = None
    followup_days: int | None = Field(default=None, ge=1, le=14)
    effective_confidence: float = Field(ge=0, le=1)
