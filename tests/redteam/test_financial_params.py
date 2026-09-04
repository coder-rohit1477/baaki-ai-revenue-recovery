"""J. LW1–LW3, SR2, WS1, PE4/PE5 — no writer signature exposes financial authority to the caller."""
from sqlalchemy import text

FORBIDDEN = {
    "p_amount_paise", "p_amount", "p_account_code", "p_lines", "p_signature_ok", "p_provider_payload_hash", "p_raw_response_hash",
    "p_item_count", "p_provider_payment_id", "p_currency", "p_provider_status", "p_paid_at", "p_source", "p_state", "p_applied_at",
    "p_created_by_role", "p_approved_by_role", "p_txn_id", "p_direction",
}


def test_signatures(su):
    rows = su.execute(text(
        "select p.proname, p.proargnames from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki_write'")).all()
    sigs = {name: set(args or []) for name, args in rows}
    for name, args in sigs.items():
        assert not (args & FORBIDDEN), (name, args & FORBIDDEN)
    # W01 legitimately takes issued_paise (issuance is the one operation whose amount is an input)
    assert "p_issued_paise" in sigs["issue_invoice"]
    assert sigs["ledger_apply_payment"] == {"p_payment_event_id", "p_trace_id"}
    assert sigs["ledger_post_unapplied"] == {"p_payment_event_id", "p_trace_id"}
    assert sigs["record_payment_event"] == {"p_payment_event_id", "p_webhook_event_id", "p_sweep_run_id", "p_provider_payload_raw",
                                            "p_attributed_invoice_id", "p_attribution_method"}
    assert sigs["record_webhook_event"] == {"p_event_id", "p_provider", "p_raw_body", "p_signature_header", "p_received_at"}
    assert sigs["record_sweep_run"] == {"p_sweep_run_id", "p_provider", "p_window_from", "p_window_to", "p_requested_at", "p_raw_response"}
    assert "post_ledger_transaction" not in sigs
