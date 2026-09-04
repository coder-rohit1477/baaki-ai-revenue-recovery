from datetime import timedelta

from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import TODAY, count, issue, outstanding, raises_unique, raises_writer, seed_org_account_contact


def test_l1_issue_creates_invoice_and_balanced_lines(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids, amount=450_000, number="INV-1")
    assert count(app, "invoice") == 1 and count(app, "ledger_entry") == 2
    assert outstanding(app, inv) == 450_000
    row = app.execute(text("select state, issued_paise from baaki.invoice where invoice_id=:i"), {"i": inv}).one()
    assert row == ("OPEN", 450_000)
    ar = app.execute(text("select amount_paise from baaki.ledger_entry where account_code like 'AR:%' and invoice_id=:i"), {"i": inv}).scalar_one()
    assert ar == 450_000   # issued_paise == AR debit by construction


def test_lw10_duplicate_number_writes_nothing(owner, app):
    ids = seed_org_account_contact(owner)
    issue(app, ids, number="INV-DUP")
    before = count(app, "ledger_entry")
    with raises_unique():
        issue(app, ids, number="INV-DUP")
    app.rollback()
    assert count(app, "ledger_entry") == before


def test_refusals_write_nothing(owner, app):
    ids = seed_org_account_contact(owner)
    for code, kw in [("issued_paise_not_positive", dict(amt=0)), ("due_before_issued", dict(d2=TODAY - timedelta(days=40))),
                     ("account_not_in_org", dict(o=new_id()))]:
        params = dict(i=new_id(), o=ids["org"], a=ids["account"], n="INV-X" + code, amt=100, d1=TODAY - timedelta(days=30), d2=TODAY, t=new_id())
        params.update(kw)
        with raises_writer(code):
            app.execute(text("SELECT baaki_write.issue_invoice(:i, :o, :a, :n, :amt, :d1, :d2, :t)"), params)
        app.rollback()
    assert count(app, "invoice") == 0 and count(app, "ledger_entry") == 0
