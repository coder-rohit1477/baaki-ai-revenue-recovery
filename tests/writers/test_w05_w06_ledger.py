from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import (
    apply_payment,
    count,
    issue,
    outstanding,
    payment_entity,
    raises_writer,
    record_payment,
    record_sweep,
    seed_org_account_contact,
    sweep_response,
    webhook_payment,
)


def _pay(app, inv, amount, pid_str):
    ev, ent = webhook_payment(app, inv, amount, pid_str)
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv)
    apply_payment(app, pid); app.commit()
    return pid


def _lines(app, pid):
    return {(r[0].split(":")[0], r[1]): r[2] for r in app.execute(text(
        "select account_code, direction, amount_paise from baaki.ledger_entry where payment_event_id=:p"), {"p": pid})}


def test_l2_partial_full_over(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids, amount=100_000)
    p1 = _pay(app, inv, 30_000, "pay_p1")
    assert outstanding(app, inv) == 70_000
    assert _lines(app, p1) == {("CASH_CLEARING", "DEBIT"): 30_000, ("AR", "CREDIT"): 30_000}
    assert app.execute(text("select state from baaki.invoice where invoice_id=:i"), {"i": inv}).scalar_one() == "OPEN"
    p2 = _pay(app, inv, 90_000, "pay_p2")          # over-credit: 70_000 to AR, 20_000 to BUYER_CREDIT
    assert outstanding(app, inv) == 0
    assert _lines(app, p2) == {("CASH_CLEARING", "DEBIT"): 90_000, ("AR", "CREDIT"): 70_000, ("BUYER_CREDIT", "CREDIT"): 20_000}
    assert app.execute(text("select state from baaki.invoice where invoice_id=:i"), {"i": inv}).scalar_one() == "PAID"
    txns = app.execute(text("select count(distinct txn_id) from baaki.ledger_entry where payment_event_id=:p"), {"p": p2}).scalar_one()
    assert txns == 1                                 # atomic, one txn_id
    # D2: payment against an already-PAID invoice → entirely BUYER_CREDIT
    p3 = _pay(app, inv, 5_000, "pay_p3")
    assert _lines(app, p3) == {("CASH_CLEARING", "DEBIT"): 5_000, ("BUYER_CREDIT", "CREDIT"): 5_000}
    assert outstanding(app, inv) == 0
    # view never negative anywhere
    assert app.execute(text("select min(outstanding_paise) from baaki.v_invoice_outstanding")).scalar_one() >= 0


def test_lw4_lw5_lw6(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    ent = payment_entity("pay_un", 100, None)
    sr = record_sweep(app, sweep_response([ent]))
    pid = record_payment(app, sweep_run_id=sr, item=ent, invoice_id=None); app.commit()
    with raises_writer("not_attributed"):
        apply_payment(app, pid)
    app.rollback()
    app.execute(text("SELECT baaki_write.ledger_post_unapplied(:p, :t)"), {"p": pid, "t": new_id()}); app.commit()
    assert outstanding(app, inv) == 450_000
    lines = _lines(app, pid)
    assert lines == {("CASH_CLEARING", "DEBIT"): 100, ("UNAPPLIED_CASH", "CREDIT"): 100}
    before = count(app, "ledger_entry")
    with raises_writer("already_applied"):
        app.execute(text("SELECT baaki_write.ledger_post_unapplied(:p, :t)"), {"p": pid, "t": new_id()})
    app.rollback()
    assert count(app, "ledger_entry") == before
    # attributed event cannot go to suspense
    ev, ent2 = webhook_payment(app, inv, 10, "pay_att")
    pid2 = record_payment(app, webhook_event_id=ev, item=ent2, invoice_id=inv); app.commit()
    with raises_writer("attributed_use_apply"):
        app.execute(text("SELECT baaki_write.ledger_post_unapplied(:p, :t)"), {"p": pid2, "t": new_id()})
    app.rollback()
    apply_payment(app, pid2); app.commit()
    with raises_writer("already_applied"):
        apply_payment(app, pid2)
    app.rollback()
