"""Ledger-derived reads. `v_invoice_outstanding` is the sole source of outstanding balances (I5, S2)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import Connection, text

from baaki.domain.money import Paise, paise


def outstanding_for_account(conn: Connection, account_id: UUID) -> dict[UUID, Paise]:
    rows = conn.execute(
        text(
            "SELECT i.invoice_id, COALESCE(v.outstanding_paise, 0) FROM baaki.invoice i "
            "LEFT JOIN baaki.v_invoice_outstanding v ON v.invoice_id = i.invoice_id WHERE i.account_id = :a"
        ),
        {"a": account_id},
    ).all()
    return {UUID(str(r[0])): paise(int(r[1])) for r in rows}


def candidate_rows(
    conn: Connection, account_id: UUID, business_date: date
) -> list[tuple[UUID, str, str, date, int, Paise]]:
    """SC2: state ≠ PAID and outstanding > 0, ordered (days_overdue desc, outstanding desc, invoice_id asc)."""
    rows = conn.execute(
        text(
            "SELECT i.invoice_id, i.invoice_number, i.state::text, i.due_date, "
            "GREATEST(0, (:bd - i.due_date))::int AS days_overdue, v.outstanding_paise "
            "FROM baaki.invoice i JOIN baaki.v_invoice_outstanding v ON v.invoice_id = i.invoice_id "
            "WHERE i.account_id = :a AND i.state <> 'PAID' AND v.outstanding_paise > 0 "
            "ORDER BY days_overdue DESC, v.outstanding_paise DESC, i.invoice_id ASC"
        ),
        {"a": account_id, "bd": business_date},
    ).all()
    return [(UUID(str(r[0])), str(r[1]), str(r[2]), r[3], int(r[4]), paise(int(r[5]))) for r in rows]
