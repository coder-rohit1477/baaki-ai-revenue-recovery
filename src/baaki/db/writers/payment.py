"""W04 record_payment_event (§6.20). No financial field is a parameter; all are extracted in-database."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call
from baaki.domain.enums import AttributionMethod


def record_payment_event(
    conn: Connection, *, payment_event_id: UUID, webhook_event_id: UUID | None,
    sweep_run_id: UUID | None, provider_payload_raw: str,
    attributed_invoice_id: UUID | None, attribution_method: AttributionMethod,
) -> UUID:
    row = call(
        conn,
        "SELECT baaki_write.record_payment_event(:payment_event_id, :webhook_event_id, :sweep_run_id, "
        ":provider_payload_raw, :attributed_invoice_id, CAST(:attribution_method AS baaki.attribution_method))",
        dict(payment_event_id=payment_event_id, webhook_event_id=webhook_event_id, sweep_run_id=sweep_run_id,
             provider_payload_raw=provider_payload_raw, attributed_invoice_id=attributed_invoice_id,
             attribution_method=str(attribution_method)),
    ).scalar_one()
    return UUID(str(row))
