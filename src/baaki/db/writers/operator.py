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
