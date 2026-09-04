"""RecoveryAction — the executable unit (§1.6). `from_decision` is a pure structural constructor."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baaki.contracts.policy_decision import ExecutableDecision
from baaki.domain.enums import ActionState, ActionType, Arm, Verdict
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")

DEFAULT_MAX_ATTEMPTS: Final[int] = 5


class RecoveryAction(BaseModel):
    model_config = _STRICT

    # Immutable identity and copies from the decision (W10 re-copies and the trigger re-verifies).
    action_id: UUID
    decision_id: UUID
    trace_id: UUID
    account_id: UUID
    invoice_id: UUID
    arm: Arm
    action_type: ActionType
    idempotency_key: str = Field(min_length=64, max_length=64)
    expires_at: datetime
    created_at: datetime
    # Lifecycle columns — writable only by W19a/W19b (P4). In P1 they never change.
    state: ActionState
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1)
    next_attempt_at: datetime | None = None
    approved_by_role: str | None = None
    approved_by_note: str | None = None
    approved_at: datetime | None = None
    provider_ref: str | None = None
    last_error_code: str | None = None
    executed_at: datetime | None = None
    confirmed_at: datetime | None = None
    updated_at: datetime

    MUTABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "state",
            "attempt_count",
            "max_attempts",
            "next_attempt_at",
            "approved_by_role",
            "approved_by_note",
            "approved_at",
            "provider_ref",
            "last_error_code",
            "executed_at",
            "confirmed_at",
            "updated_at",
        }
    )

    @staticmethod
    def initial_state(verdict: Verdict) -> ActionState:
        """R3 — a pure mapping, not policy."""
        if verdict is Verdict.REQUIRE_APPROVAL:
            return ActionState.PENDING_APPROVAL
        if verdict is Verdict.ALLOW:
            return ActionState.QUEUED
        raise ContractViolation(f"non-executable verdict {verdict} cannot create an action (P9)")

    @classmethod
    def from_decision(
        cls,
        decision: ExecutableDecision,
        now: datetime,
        expires_at: datetime,
        idempotency_key: str,
        *,
        action_id: UUID | None = None,
    ) -> RecoveryAction:
        """Pure structural constructor (§1.6): no policy evaluation, no transition, no I/O,
        no clock read (takes values). Persistence is a separate W10 call."""
        if expires_at <= now:
            raise ContractViolation("expires_at must be after now")
        return cls(
            action_id=action_id if action_id is not None else new_id(),
            decision_id=decision.decision_id,
            trace_id=decision.trace_id,
            account_id=decision.account_id,
            invoice_id=decision.invoice_id,
            arm=decision.arm,
            action_type=decision.action_type,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            created_at=now,
            state=cls.initial_state(decision.verdict),
            updated_at=now,
        )
