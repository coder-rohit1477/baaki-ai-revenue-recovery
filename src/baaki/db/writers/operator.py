"""Human-only writers (baaki_ops). Importable only by scripts/ops and tests (ARCHITECTURE.md §5.3, §6.22). P2: W12."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call


def opt_out_by_operator(
    conn: Connection, *, account_id: UUID | None = None, contact_id: UUID | None = None, actor_note: str
) -> bool:
    row = call(
        conn, "SELECT baaki_write.opt_out_by_operator(:a, :c, :n)", {"a": account_id, "c": contact_id, "n": actor_note}
    ).scalar_one()
    return bool(row)


def approve_recovery_action(conn: Connection, *, action_id: UUID, actor_note: str, outbox_id: UUID) -> str:
    """W15 — authorise a tier-2 action the kernel parked at PENDING_APPROVAL. Returns the new state.

    Approval makes an already-validated proposal executable; it never authors one. The writer refuses any
    state other than PENDING_APPROVAL, so a second approval, or an approval of a rejected action, fails.
    """
    row = call(
        conn,
        "SELECT baaki_write.approve_recovery_action(:a, :n, :o)",
        {"a": action_id, "n": actor_note, "o": outbox_id},
    ).scalar_one()
    return str(row)


def reject_recovery_action(conn: Connection, *, action_id: UUID, actor_note: str) -> str:
    """W16 — refuse a pending tier-2 action. Returns the new state. No outbox row is ever created."""
    row = call(
        conn, "SELECT baaki_write.reject_recovery_action(:a, :n)", {"a": action_id, "n": actor_note}
    ).scalar_one()
    return str(row)
