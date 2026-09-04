"""B. §6.3 table privileges; I10; no DELETE anywhere; provider_secret unreadable."""
from sqlalchemy import text

APP_ROLES = ["baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"]
EXPECTED_SELECT = {
    "organization": {"baaki_app", "baaki_ops"},
    "account": {"baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"},
    "contact": {"baaki_app", "baaki_ops", "baaki_agent"},
    "template_registry": {"baaki_app", "baaki_ops", "baaki_agent"},
    "provider_secret": set(),
    "invoice": {"baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"},
    "ledger_entry": {"baaki_app", "baaki_ops"},
    "payment_event": {"baaki_app", "baaki_ops"},
    "webhook_event": {"baaki_app", "baaki_ops"},
    "sweep_run": {"baaki_app", "baaki_ops"},
    "agent_proposal": {"baaki_app", "baaki_ops", "baaki_agent"},
    "validation_result": {"baaki_app", "baaki_ops"},
    "policy_decision": {"baaki_app", "baaki_ops"},
    "recovery_action": {"baaki_app", "baaki_ops"},
    "outbox": {"baaki_app", "baaki_ops"},
    "v_invoice_outstanding": {"baaki_app", "baaki_ops"},
}
EXPECTED_INSERT = {"account": {"baaki_app"}, "contact": {"baaki_app"}}


def _privs(su, priv):
    rows = su.execute(text(
        "select table_name, grantee from information_schema.table_privileges where table_schema='baaki' and privilege_type=:p "
        "and grantee in ('baaki_app','baaki_ops','baaki_agent','baaki_sim')"), {"p": priv}).all()
    out = {}
    for t, g in rows:
        out.setdefault(t, set()).add(g)
    return out


def test_select_matrix(su):
    got = _privs(su, "SELECT")
    for t, exp in EXPECTED_SELECT.items():
        assert got.get(t, set()) == exp, (t, got.get(t))


def test_insert_only_on_m_class(su):
    assert _privs(su, "INSERT") == EXPECTED_INSERT


def test_no_delete_anywhere(su):
    assert _privs(su, "DELETE") == {}
    assert _privs(su, "TRUNCATE") == {}


def test_no_table_level_update(su):
    # Column-level UPDATE appears in table_privileges as UPDATE on the table for some PG versions;
    # assert against column_privileges instead (tests/roles/test_column_privileges.py) and that no
    # role holds UPDATE on any F/D/C table.
    got = _privs(su, "UPDATE")
    assert set(got) <= {"account", "contact"}, got


def test_ops_holds_no_dml(su):
    rows = su.execute(text(
        "select privilege_type from information_schema.table_privileges where table_schema='baaki' and grantee='baaki_ops' and privilege_type<>'SELECT'")).all()
    assert rows == []
    rows = su.execute(text("select 1 from information_schema.column_privileges where table_schema='baaki' and grantee='baaki_ops' and privilege_type<>'SELECT'")).all()
    assert rows == []


def test_schema_privileges(su):
    for role in APP_ROLES:
        for schema in ("baaki", "baaki_write"):
            assert su.execute(text("select has_schema_privilege(:r, :s, 'USAGE')"), {"r": role, "s": schema}).scalar_one() is True
            assert su.execute(text("select has_schema_privilege(:r, :s, 'CREATE')"), {"r": role, "s": schema}).scalar_one() is False
        assert su.execute(text("select has_schema_privilege(:r, 'public', 'CREATE')"), {"r": role}).scalar_one() is False
