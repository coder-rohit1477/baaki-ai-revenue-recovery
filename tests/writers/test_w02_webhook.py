from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import NOW, count, payment_entity, raises_writer, record_webhook, sign, webhook_body


def test_ws_signature_computed_in_db(app):
    body = webhook_body(payment_entity("pay_1", 100, None))
    ok = record_webhook(app, body, sign(body))
    bad = record_webhook(app, webhook_body(payment_entity("pay_2", 100, None)), "deadbeef")
    tampered_body = webhook_body(payment_entity("pay_3", 100, None))
    tampered = record_webhook(app, tampered_body.replace('"amount":100', '"amount":999'), sign(tampered_body))
    rows = dict(app.execute(text("select event_id, signature_ok from baaki.webhook_event")).all())
    assert rows[ok] is True and rows[bad] is False and rows[tampered] is False


def test_replay_is_idempotent(app):
    body = webhook_body(payment_entity("pay_r", 100, None))
    a = record_webhook(app, body, sign(body))
    b = record_webhook(app, body, sign(body))
    assert a == b and count(app, "webhook_event") == 1


def test_no_secret_or_invalid_json_refused(app):
    with raises_writer("no_secret_for_provider"):
        app.execute(text("SELECT baaki_write.record_webhook_event(:e, 'stripe', '{}', 'x', :r)"), {"e": new_id(), "r": NOW})
    app.rollback()
    with raises_writer("invalid_json"):
        app.execute(text("SELECT baaki_write.record_webhook_event(:e, 'razorpay', 'not json', 'x', :r)"), {"e": new_id(), "r": NOW})
    app.rollback()
    assert count(app, "webhook_event") == 0
