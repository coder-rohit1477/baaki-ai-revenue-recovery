"""J. H18 — trusted execution context: created_by_role comes from session_user, never from a caller."""
from sqlalchemy import text

from tests.helpers import payment_entity, record_sweep, sweep_response


def test_sweep_created_by_role_is_session_user(app):
    sid = record_sweep(app, sweep_response([payment_entity("pay_actor", 1, None)]))
    assert app.execute(text("select created_by_role from baaki.sweep_run where sweep_run_id=:s"), {"s": sid}).scalar_one() == "baaki_app"


def test_no_writer_accepts_an_actor_or_role_parameter(su):
    rows = su.execute(text(
        "select p.proname, p.proargnames from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki_write'")).all()
    for name, args in rows:
        assert not any(a in ("p_actor", "p_actor_id", "p_role", "p_session_user", "p_created_by_role", "p_approved_by_role") for a in (args or [])), (name, args)


def test_approved_by_role_cannot_be_set_by_anyone_in_p1(app, ops):
    from tests.helpers import raises_privilege
    for conn in (app, ops):
        with raises_privilege():
            conn.execute(text("UPDATE baaki.recovery_action SET approved_by_role = 'baaki_ops', approved_by_note = 'me'"))
        conn.rollback()
