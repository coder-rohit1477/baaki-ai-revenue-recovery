"""H. Concurrent duplicate deliveries yield exactly one financial effect."""
import threading

import psycopg
import psycopg.errors
from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import (
    issue,
    outstanding,
    payment_entity,
    record_webhook,
    seed_org_account_contact,
    sign,
    webhook_body,
)


def test_tx4_concurrent_t6_for_same_payment(cluster, owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids, amount=10_000)
    ent = payment_entity("pay_conc", 10_000, inv)
    body = webhook_body(ent); ev = record_webhook(app, body, sign(body))
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker():
        with psycopg.connect(cluster.dsns["baaki_app"]) as c:
            c.execute("SET default_transaction_isolation = 'serializable'")
            barrier.wait()
            try:
                with c.transaction():
                    pid = new_id()
                    c.execute("SELECT baaki_write.record_payment_event(%s, %s, NULL, %s, %s, 'NOTES_INVOICE_ID')", (pid, ev, ent, inv))
                    c.execute("SELECT baaki_write.ledger_apply_payment(%s, %s)", (pid, new_id()))
                outcomes.append("ok")
            except psycopg.errors.UniqueViolation:
                outcomes.append("unique")
            except psycopg.errors.SerializationFailure:
                outcomes.append("serialization")
            except psycopg.errors.RaiseException as e:
                outcomes.append(e.diag.message_primary or "raise")

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert outcomes.count("ok") == 1, outcomes
    assert app.execute(text("select count(*) from baaki.payment_event")).scalar_one() == 1
    assert app.execute(text("select count(*) from baaki.ledger_entry where source='PAYMENT'")).scalar_one() == 2
    assert outstanding(app, inv) == 0


def test_concurrent_issue_same_number(cluster, owner):
    ids = seed_org_account_contact(owner)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker():
        from datetime import date
        with psycopg.connect(cluster.dsns["baaki_app"]) as c:
            barrier.wait()
            try:
                c.execute("SELECT baaki_write.issue_invoice(%s, %s, %s, 'INV-RACE', 100, %s, %s, %s)",
                          (new_id(), ids["org"], ids["account"], date(2026, 8, 1), date(2026, 8, 20), new_id()))
                c.commit(); outcomes.append("ok")
            except psycopg.errors.UniqueViolation:
                outcomes.append("unique")

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(outcomes) == ["ok", "unique"]
