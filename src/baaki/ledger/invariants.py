"""Ledger invariant checks feeding `snapshot.ledger_invariant_ok` (ARCHITECTURE.md L1, L5, §6.13)."""

from __future__ import annotations

from sqlalchemy import Connection, text


def ledger_invariants_ok(conn: Connection) -> bool:
    unbalanced = conn.execute(
        text(
            "SELECT count(*) FROM (SELECT txn_id, SUM(CASE WHEN direction='DEBIT' THEN amount_paise ELSE "
            "-amount_paise END) d "
            "FROM baaki.ledger_entry GROUP BY txn_id HAVING SUM(CASE WHEN direction='DEBIT' THEN amount_paise "
            "ELSE -amount_paise END) <> 0) x"
        )
    ).scalar_one()
    negative = conn.execute(
        text("SELECT count(*) FROM baaki.v_invoice_outstanding WHERE outstanding_paise < 0")
    ).scalar_one()
    issued_mismatch = conn.execute(
        text(
            "SELECT count(*) FROM baaki.invoice i JOIN baaki.ledger_entry l ON l.invoice_id = i.invoice_id "
            "AND l.source = 'ISSUANCE' AND l.direction = 'DEBIT' WHERE l.amount_paise <> i.issued_paise"
        )
    ).scalar_one()
    return int(unbalanced) == 0 and int(negative) == 0 and int(issued_mismatch) == 0
