"""W01 issue_invoice · W05 ledger_apply_payment · W06 ledger_post_unapplied (§6.6, §6.12).

None of these accepts an account code, a line list, or a payment amount.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call
from baaki.domain.money import Paise


def issue_invoice(
    conn: Connection, *, invoice_id: UUID, org_id: UUID, account_id: UUID, invoice_number: str,
    issued_paise: Paise, issued_date: date, due_date: date, trace_id: UUID,
) -> UUID:
    row = call(
        conn,
        "SELECT baaki_write.issue_invoice(:invoice_id, :org_id, :account_id, :invoice_number, "
        ":issued_paise, :issued_date, :due_date, :trace_id)",
        dict(invoice_id=invoice_id, org_id=org_id, account_id=account_id, invoice_number=invoice_number,
             issued_paise=int(issued_paise), issued_date=issued_date, due_date=due_date, trace_id=trace_id),
    ).scalar_one()
    return UUID(str(row))


def ledger_apply_payment(conn: Connection, *, payment_event_id: UUID, trace_id: UUID) -> UUID:
    row = call(
        conn, "SELECT baaki_write.ledger_apply_payment(:payment_event_id, :trace_id)",
        dict(payment_event_id=payment_event_id, trace_id=trace_id),
    ).scalar_one()
    return UUID(str(row))


def ledger_post_unapplied(conn: Connection, *, payment_event_id: UUID, trace_id: UUID) -> UUID:
    row = call(
        conn, "SELECT baaki_write.ledger_post_unapplied(:payment_event_id, :trace_id)",
        dict(payment_event_id=payment_event_id, trace_id=trace_id),
    ).scalar_one()
    return UUID(str(row))
