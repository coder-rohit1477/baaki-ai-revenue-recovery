"""J. A1–A7, S1–S3, AC8–AC10, AC13, migration-role misuse, owner login."""
import pytest
from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import raises_privilege

WRITER_CALLS = {
    "issue_invoice": "SELECT baaki_write.issue_invoice(:u, :u, :u, 'x', 1, current_date, current_date, :u)",
    "record_webhook_event": "SELECT baaki_write.record_webhook_event(:u, 'razorpay', '{}', 'x', now())",
    "record_sweep_run": "SELECT baaki_write.record_sweep_run(:u, 'razorpay', now(), now(), now(), '{}')",
    "record_payment_event": "SELECT baaki_write.record_payment_event(:u, :u, NULL, '{}', NULL, 'UNATTRIBUTED')",
    "ledger_apply_payment": "SELECT baaki_write.ledger_apply_payment(:u, :u)",
    "ledger_post_unapplied": "SELECT baaki_write.ledger_post_unapplied(:u, :u)",
    "record_agent_proposal": ("SELECT baaki_write.record_agent_proposal(:u, :u, :u, 'INTERPRETATION', NULL, current_date, 'x','x','x','x', "
                              "repeat('a',64), repeat('a',64), '{}', NULL, 'TIMEOUT', NULL, '[]', 1)"),
    "record_validation_result": "SELECT baaki_write.record_validation_result(:u, :u, 'PASS', '{}', '{}', '[]', 'v', repeat('a',64))",
    "record_policy_decision": ("SELECT baaki_write.record_policy_decision(:u, NULL, NULL, :u, :u, current_date, :u, 'CONTROL', 'BLOCK', CAST(0 AS smallint), NULL, NULL, NULL, "
                               "'{}', '[{\"r\": 1}]', NULL, 'p','k', repeat('a',64), repeat('a',64), 'L1', ARRAY[:u]::uuid[])"),
    "create_recovery_action": "SELECT * FROM baaki_write.create_recovery_action(:u, :u, repeat('a',64), now() + interval '1 day', now(), :u)",
}
APP_ONLY = [k for k in WRITER_CALLS if k != "record_agent_proposal"]


@pytest.mark.parametrize("fn", APP_ONLY)
def test_a3_agent_cannot_execute_app_writers(agent, fn):
    with raises_privilege():
        agent.execute(text(WRITER_CALLS[fn]), {"u": new_id()})
    agent.rollback()


def test_a6_app_cannot_execute_w07(app):
    with raises_privilege():
        app.execute(text(WRITER_CALLS["record_agent_proposal"]), {"u": new_id()})
    app.rollback()


@pytest.mark.parametrize("fn", list(WRITER_CALLS))
def test_ac13_ops_holds_no_writer_in_p1(ops, fn):
    with raises_privilege():
        ops.execute(text(WRITER_CALLS[fn]), {"u": new_id()})
    ops.rollback()


@pytest.mark.parametrize("fn", list(WRITER_CALLS))
def test_s3_sim_cannot_execute_any_writer(sim, fn):
    with raises_privilege():
        sim.execute(text(WRITER_CALLS[fn]), {"u": new_id()})
    sim.rollback()


def test_a1_a2_agent_visibility(agent):
    for table in ("ledger_entry", "payment_event", "policy_decision", "recovery_action", "validation_result", "outbox", "webhook_event", "sweep_run"):
        with raises_privilege():
            agent.execute(text(f"SELECT * FROM baaki.{table}"))
        agent.rollback()
    for table in ("account", "contact", "invoice", "template_registry", "agent_proposal"):
        agent.execute(text(f"SELECT * FROM baaki.{table}"))


def test_s1_sim_visibility(sim):
    with raises_privilege():
        sim.execute(text("SELECT * FROM baaki.contact"))
    sim.rollback()
    for table in ("account", "invoice"):
        sim.execute(text(f"SELECT * FROM baaki.{table}"))


@pytest.mark.parametrize("role,target", [("app", "baaki_ops"), ("app", "baaki_owner"), ("ops", "baaki_owner"), ("ops", "baaki_app"),
                                         ("agent", "baaki_app"), ("sim", "baaki_app")])
def test_ac8_ac9_set_role_refused(request, role, target):
    conn = request.getfixturevalue(role)
    with raises_privilege():
        conn.execute(text(f"SET ROLE {target}"))
    conn.rollback()


def test_ac10_set_session_authorization_refused(app):
    with raises_privilege():
        app.execute(text("SET SESSION AUTHORIZATION baaki_ops"))
    app.rollback()


def test_migrate_role_without_set_role_has_no_dml(cluster):
    eng = cluster.engine("baaki_migrate")
    with eng.connect() as c:
        with raises_privilege():
            c.execute(text("INSERT INTO baaki.organization (org_id, name, timezone) VALUES (gen_random_uuid(), 'x', 'UTC')"))
        c.rollback()
        with raises_privilege():
            c.execute(text("SELECT baaki_write.issue_invoice(gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'x', 1, current_date, current_date, gen_random_uuid())"))
        c.rollback()
    eng.dispose()
