"""B. §6.4A — the only direct UPDATE capability: account.risk_band and contact.active for baaki_app."""
from sqlalchemy import text


def test_column_update_grants_exact(su):
    rows = su.execute(text(
        "select table_name, column_name, grantee from information_schema.column_privileges "
        "where table_schema='baaki' and privilege_type='UPDATE' and grantee in ('baaki_app','baaki_ops','baaki_agent','baaki_sim')")).all()
    assert {(t, c, g) for t, c, g in rows} == {("account", "risk_band", "baaki_app"), ("contact", "active", "baaki_app")}
