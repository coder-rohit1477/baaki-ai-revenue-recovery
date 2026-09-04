from sqlalchemy import text

from tests.helpers import (
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


def test_fields_extracted_from_evidence(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    ent = payment_entity("pay_e1", 123_456, inv, epoch=1_756_960_000)
    body = webhook_body(ent)
    ev = record_webhook(app, body, sign(body))
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv); app.commit()
    row = app.execute(text(
        "select provider, provider_payment_id, amount_paise, currency, provider_status, source, attribution_method, provider_payload_hash, "
        "extract(epoch from paid_at)::bigint from baaki.payment_event where payment_event_id=:p"), {"p": pid}).one()
    import hashlib
    assert tuple(row) == ("razorpay", "pay_e1", 123_456, "INR", "captured", "WEBHOOK", "NOTES_INVOICE_ID",
                          hashlib.sha256(ent.encode()).hexdigest(), 1_756_960_000)
    assert app.execute(text("select processed_at is not null from baaki.webhook_event where event_id=:e"), {"e": ev}).scalar_one()


def test_pe1_pe2_evidence_xor(owner, app):
    ids = seed_org_account_contact(owner)
    ent = payment_entity("pay_x", 100, None)
    with raises_writer("evidence_required"):
        record_payment(app, item=ent, invoice_id=None)
    app.rollback()
    body = webhook_body(ent); ev = record_webhook(app, body, sign(body)); sr = record_sweep(app, sweep_response([ent]))
    with raises_writer("evidence_ambiguous"):
        record_payment(app, webhook_event_id=ev, sweep_run_id=sr, item=ent, invoice_id=None)
    app.rollback()


def test_pe3_unverified_evidence_refused(app):
    ent = payment_entity("pay_u", 100, None)
    ev = record_webhook(app, webhook_body(ent), "bad-signature")
    with raises_writer("unverified_evidence"):
        record_payment(app, webhook_event_id=ev, item=ent, invoice_id=None)
    app.rollback()
    assert count(app, "payment_event") == 0


def test_pe6_sr4_containment(app):
    ent = payment_entity("pay_c", 100, None)
    body = webhook_body(ent); ev = record_webhook(app, body, sign(body))
    other = payment_entity("pay_other", 999_999, None)
    with raises_writer("payload_not_in_evidence"):
        record_payment(app, webhook_event_id=ev, item=other, invoice_id=None)
    app.rollback()
    sr = record_sweep(app, sweep_response([ent]))
    with raises_writer("payload_not_in_evidence"):
        record_payment(app, sweep_run_id=sr, item=other, invoice_id=None)
    app.rollback()


def test_pe7_sr5_duplicate_provider_payment_id(app):
    ent = payment_entity("pay_dup", 100, None)
    body = webhook_body(ent); ev = record_webhook(app, body, sign(body))
    record_payment(app, webhook_event_id=ev, item=ent, invoice_id=None); app.commit()
    sr = record_sweep(app, sweep_response([ent]))
    with raises_unique():
        record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None)
    app.rollback()


def test_pe8_pe9_pe10_payload_validation(owner, app):
    ids = seed_org_account_contact(owner)
    for ent, code in [
        (payment_entity("pay_usd", 100, None, currency="USD"), "currency_not_inr"),
        (payment_entity("pay_auth", 100, None, status="authorized"), "status_not_accepted"),
        (payment_entity("pay_zero", 0, None), "amount_not_positive"),
        ('{"id":"pay_nofields"}', "payload_field_missing"),
    ]:
        sr = record_sweep(app, sweep_response([ent]))
        with raises_writer(code):
            record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None)
        app.rollback()
    ent = payment_entity("pay_m", 100, None)
    sr = record_sweep(app, sweep_response([ent]))
    with raises_writer("method_not_allowed"):
        record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None, method="HUMAN_REATTRIBUTION")
    app.rollback()
    with raises_writer("attribution_inconsistent"):
        record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None, method="NOTES_INVOICE_ID")
    app.rollback()
    import pytest
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):   # enum cast fails: no AMOUNT_MATCH member exists (I6)
        record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None, method="AMOUNT_MATCH")
    app.rollback()
    assert count(app, "payment_event") == 0
