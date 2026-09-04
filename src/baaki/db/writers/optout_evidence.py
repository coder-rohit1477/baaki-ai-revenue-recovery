"""W11 opt_out_contact_from_evidence — evidence-gated inbound opt-out (baaki_app)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call


def opt_out_contact_from_evidence(conn: Connection, *, contact_id: UUID, validation_id: UUID) -> bool:
    row = call(
        conn, "SELECT baaki_write.opt_out_contact_from_evidence(:c, :v)", {"c": contact_id, "v": validation_id}
    ).scalar_one()
    return bool(row)
