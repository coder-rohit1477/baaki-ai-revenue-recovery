"""W02 record_webhook_event (§6.6, §6.20). signature_ok and dedupe_key are computed in-database."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call


def record_webhook_event(
    conn: Connection, *, event_id: UUID, provider: str, raw_body: str,
    signature_header: str | None, received_at: datetime,
) -> UUID:
    row = call(
        conn,
        "SELECT baaki_write.record_webhook_event(:event_id, :provider, :raw_body, :signature_header, :received_at)",
        dict(event_id=event_id, provider=provider, raw_body=raw_body,
             signature_header=signature_header, received_at=received_at),
    ).scalar_one()
    return UUID(str(row))
