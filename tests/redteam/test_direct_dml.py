"""J. R1–R13, U1–U17 — direct DML against F/D/C tables and sensitive columns is impossible for app roles."""
import pytest
from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import H64, raises_privilege, seed_org_account_contact

F_D_C_TABLES = ["invoice", "ledger_entry", "payment_event", "agent_proposal", "validation_result", "policy_decision",
                "recovery_action", "outbox", "webhook_event", "sweep_run", "template_registry", "organization", "provider_secret"]


@pytest.mark.parametrize("role", ["app", "ops"])
@pytest.mark.parametrize("table", F_D_C_TABLES)
def test_r_direct_insert_refused(request, role, table):
    conn = request.getfixturevalue(role)
    with raises_privilege():
        conn.execute(text(f"INSERT INTO baaki.{table} DEFAULT VALUES"))
    conn.rollback()


def test_r12_r13_m_class_insert(owner, app, ops):
    ids = seed_org_account_contact(owner)
    app.execute(text("INSERT INTO baaki.account (account_id, org_id, external_ref, name) VALUES (:a, :o, 'ACC-APP', 'x')"), {"a": new_id(), "o": ids["org"]})
    app.execute(text("INSERT INTO baaki.contact (contact_id, account_id, channel, address_hash, address_redacted) VALUES (:c, :a, 'SMS', :h, 'x')"),
                {"c": new_id(), "a": ids["account"], "h": H64})
    app.commit()
    with raises_privilege():
        ops.execute(text("INSERT INTO baaki.account (account_id, org_id, external_ref, name) VALUES (:a, :o, 'ACC-OPS', 'x')"), {"a": new_id(), "o": ids["org"]})
    ops.rollback()


UPDATES = [
    ("invoice", "state = 'PAID'"), ("invoice", "issued_paise = 1"),
    ("recovery_action", "state = 'CONFIRMED'"), ("recovery_action", "provider_ref = 'fake'"), ("recovery_action", "confirmed_at = now()"),
    ("recovery_action", "attempt_count = 0"), ("recovery_action", "approved_by_role = 'baaki_ops'"),
    ("account", "opt_out = false"), ("contact", "opted_out = false"),
    ("payment_event", "amount_paise = 1"), ("payment_event", "attributed_invoice_id = NULL"),
    ("ledger_entry", "amount_paise = 1"), ("organization", "kill_switch = false"),
    ("outbox", "claimed_at = now()"), ("webhook_event", "signature_ok = true"), ("sweep_run", "raw_response = 'x'"),
    ("agent_proposal", "parsed = NULL"), ("validation_result", "outcome = 'PASS'"), ("policy_decision", "verdict = 'ALLOW'"),
]


@pytest.mark.parametrize("role", ["app", "ops", "agent", "sim"])
@pytest.mark.parametrize("table,assignment", UPDATES)
def test_u_direct_update_refused(request, role, table, assignment):
    conn = request.getfixturevalue(role)
    with raises_privilege():
        conn.execute(text(f"UPDATE baaki.{table} SET {assignment}"))
    conn.rollback()


def test_u15_safe_columns_updatable_by_app_only(owner, app, ops):
    ids = seed_org_account_contact(owner)
    app.execute(text("UPDATE baaki.account SET risk_band = 3 WHERE account_id = :a"), {"a": ids["account"]})
    app.execute(text("UPDATE baaki.contact SET active = false WHERE contact_id = :c"), {"c": ids["contact"]})
    app.commit()
    with raises_privilege():
        ops.execute(text("UPDATE baaki.account SET risk_band = 4"))
    ops.rollback()


@pytest.mark.parametrize("role", ["app", "ops", "agent", "sim"])
@pytest.mark.parametrize("table", F_D_C_TABLES + ["account", "contact"])
def test_u16_delete_refused_everywhere(request, role, table):
    conn = request.getfixturevalue(role)
    with raises_privilege():
        conn.execute(text(f"DELETE FROM baaki.{table}"))
    conn.rollback()


@pytest.mark.parametrize("role", ["app", "ops", "agent", "sim"])
def test_u17_provider_secret_unreadable(request, role):
    conn = request.getfixturevalue(role)
    with raises_privilege():
        conn.execute(text("SELECT * FROM baaki.provider_secret"))
    conn.rollback()
