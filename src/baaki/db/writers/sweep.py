"""W03 record_sweep_run (§6.23). Hash, item_count and created_by_role are computed in-database."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call


def record_sweep_run(
    conn: Connection, *, sweep_run_id: UUID, provider: str, window_from: datetime,
    window_to: datetime, requested_at: datetime, raw_response: str,
) -> UUID:
    row = call(
        conn,
        "SELECT baaki_write.record_sweep_run(:sweep_run_id, :provider, :window_from, :window_to, "
        ":requested_at, :raw_response)",
        dict(sweep_run_id=sweep_run_id, provider=provider, window_from=window_from,
             window_to=window_to, requested_at=requested_at, raw_response=raw_response),
    ).scalar_one()
    return UUID(str(row))
