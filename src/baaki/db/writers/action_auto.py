"""W10 create_recovery_action (§6.10). W19a transition_recovery_action lands here in Phase 4."""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Connection

from baaki.contracts.recovery_action import RecoveryAction
from baaki.db.writers._call import call


class CreatedAction(NamedTuple):
    action_id: UUID
    superseded: bool


def create_recovery_action(conn: Connection, action: RecoveryAction, *, outbox_id: UUID) -> CreatedAction:
    row = call(
        conn,
        "SELECT action_id, superseded FROM baaki_write.create_recovery_action(:action_id, :decision_id, "
        ":idempotency_key, :expires_at, :now, :outbox_id)",
        dict(action_id=action.action_id, decision_id=action.decision_id, idempotency_key=action.idempotency_key,
             expires_at=action.expires_at, now=action.created_at, outbox_id=outbox_id),
    ).one()
    return CreatedAction(UUID(str(row[0])), bool(row[1]))
