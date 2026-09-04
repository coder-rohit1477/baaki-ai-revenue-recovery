"""I. H10/H11 — a raising writer aborts the enclosing transaction; nothing partial persists."""
from datetime import timedelta

from sqlalchemy import text

from baaki.contracts.canonical_payload import SuppressPayload
from baaki.db.writers.decision import record_policy_decision
from baaki.domain.enums import ActionType, SuppressReason
from baaki.domain.ids import new_id
from tests.helpers import (
    NOW,
    count,
    exec_decision,
    issue,
    payment_entity,
    raises_writer,
    record_payment,
    record_sweep,
    seed_org_account_contact,
    sweep_response,
)


def test_tx1_decision_then_failing_action_rolls_back_both(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    d = exec_decision(ids, inv, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION), tier=0)
    record_policy_decision(app, d, candidate_invoice_ids=[inv], trace_id=d.trace_id, account_id=d.account_id, business_date=d.business_date)
    with raises_writer("expires_before_now"):
        app.execute(text("SELECT * FROM baaki_write.create_recovery_action(:a, :d, :k, :e, :n, :o)"),
                    {"a": new_id(), "d": d.decision_id, "k": "2" * 64, "e": NOW - timedelta(days=1), "n": NOW, "o": new_id()})
    app.rollback()
    assert count(app, "policy_decision") == 0 and count(app, "recovery_action") == 0


def test_tx2_payment_event_then_failing_apply_rolls_back_both(owner, app):
    ids = seed_org_account_contact(owner)
    ent = payment_entity("pay_tx2", 100, None)
    sr = record_sweep(app, sweep_response([ent]))
    pid = record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None)     # unattributed, same txn
    with raises_writer("not_attributed"):
        app.execute(text("SELECT baaki_write.ledger_apply_payment(:p, :t)"), {"p": pid, "t": new_id()})
    app.rollback()
    assert count(app, "payment_event") == 0 and count(app, "ledger_entry") == 0


def test_caller_failure_after_issue_rolls_back_invoice_and_ledger(owner, app):
    ids = seed_org_account_contact(owner)
    from datetime import date
    app.execute(text("SELECT baaki_write.issue_invoice(:i, :o, :a, 'INV-TX', 100, :d1, :d2, :t)"),
                {"i": new_id(), "o": ids["org"], "a": ids["account"], "d1": date(2026, 8, 1), "d2": date(2026, 8, 9), "t": new_id()})
    app.rollback()                                                              # caller decides to abort
    assert count(app, "invoice") == 0 and count(app, "ledger_entry") == 0
