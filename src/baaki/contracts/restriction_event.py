"""Restriction event contracts — arm-independent opt-out evidence (ARCHITECTURE.md §6.18.1).

Phase 2 defines the contract and the deterministic detector output; the table, writer and W11b are Phase 4.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RestrictionMatch(BaseModel):
    """Output of `rules_agent.restriction.detect` — pure, versioned."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    matched_pattern_id: str
    matcher_version: str
    span: str  # the literal substring that matched


class RestrictionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    restriction_event_id: UUID
    contact_id: UUID
    account_id: UUID
    message_id: UUID
    raw_body_hash: str = Field(min_length=64, max_length=64)  # computed by the P4 writer from the stored body
    matched_pattern_id: str
    matcher_version: str
    detected_at: datetime
    created_by_role: str  # session_user (TEC)
