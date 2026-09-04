"""action_proposal.v1 — call-2 output schema. No amount, no reason_code, no assignee_queue, no free-text recipient."""

from __future__ import annotations

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baaki.domain.enums import ActionType, Channel

SCHEMA_VERSION: Final[str] = "action_proposal.v1"


class ActionProposalV1(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    action: ActionType
    contact_id: UUID | None = None
    channel: Channel
    template_id: str | None = None
    followup_days: int | None = Field(default=None, ge=1, le=14)
    rationale: str = Field(max_length=280)  # display only; never parsed by any code path
    confidence: float = Field(ge=0, le=1)
