"""G. Writer-level idempotency — replay semantics per §6.6."""
from tests.helpers import (
    apply_payment,
    count,
    issue,
    payment_entity,
    raises_unique,
    raises_writer,
    record_payment,
    record_sweep,
    record_webhook,
    seed_org_account_contact,
    sign,
    sweep_response,
    webhook_body,
)


def test_webhook_and_sweep_replay_return_same_id(app):
    ent = payment_entity("pay_i", 100, None)
    body = webhook_body(ent)
    assert record_webhook(app, body, sign(body)) == record_webhook(app, body, sign(body))
    raw = sweep_response([ent])
    assert record_sweep(app, raw) == record_sweep(app, raw)
    assert count(app, "webhook_event") == 1 and count(app, "sweep_run") == 1


def test_payment_event_duplicate_then_apply_once(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    ent = payment_entity("pay_once", 1000, inv)
    body = webhook_body(ent); ev = record_webhook(app, body, sign(body))
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv); app.commit()
    with raises_unique():
        record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv)
    app.rollback()
    apply_payment(app, pid); app.commit()
    before = count(app, "ledger_entry")
    with raises_writer("already_applied"):
        apply_payment(app, pid)
    app.rollback()
    assert count(app, "ledger_entry") == before
