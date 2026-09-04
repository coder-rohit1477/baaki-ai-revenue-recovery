"""C. H4/H5 — a hostile schema on the caller's search_path cannot redirect a writer's writes."""
from datetime import timedelta

from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import TODAY, seed_org_account_contact


def test_shadow_invoice_table_is_ignored(su, owner, app):
    ids = seed_org_account_contact(owner)
    su.execute(text("DROP SCHEMA IF EXISTS hostile CASCADE"))
    su.execute(text("CREATE SCHEMA hostile"))
    su.execute(text("GRANT ALL ON SCHEMA hostile TO baaki_app"))
    su.execute(text("CREATE TABLE hostile.invoice (LIKE baaki.invoice INCLUDING ALL)"))
    su.execute(text("CREATE TABLE hostile.ledger_entry (LIKE baaki.ledger_entry INCLUDING ALL)"))
    su.execute(text("GRANT ALL ON ALL TABLES IN SCHEMA hostile TO baaki_app"))
    su.commit()
    try:
        app.execute(text("SET search_path TO hostile, baaki, public"))
        inv = new_id()
        app.execute(text("SELECT baaki_write.issue_invoice(:i, :o, :a, 'INV-SP', 1000, :d1, :d2, :t)"),
                    {"i": inv, "o": ids["org"], "a": ids["account"], "d1": TODAY, "d2": TODAY + timedelta(days=10), "t": new_id()})
        app.commit()
        assert su.execute(text("select count(*) from baaki.invoice")).scalar_one() == 1
        assert su.execute(text("select count(*) from hostile.invoice")).scalar_one() == 0
        assert su.execute(text("select count(*) from hostile.ledger_entry")).scalar_one() == 0
        assert su.execute(text("select count(*) from baaki.ledger_entry")).scalar_one() == 2
    finally:
        su.execute(text("DROP SCHEMA hostile CASCADE")); su.commit()
