"""B. §6.6 EXECUTE matrix for W01–W10; PUBLIC revoked; ops holds none in P1."""
from sqlalchemy import text

MATRIX = {
    "issue_invoice": {"baaki_app"}, "record_webhook_event": {"baaki_app"}, "record_sweep_run": {"baaki_app"},
    "record_payment_event": {"baaki_app"}, "ledger_apply_payment": {"baaki_app"}, "ledger_post_unapplied": {"baaki_app"},
    "record_agent_proposal": {"baaki_agent"}, "record_validation_result": {"baaki_app"},
    "record_policy_decision": {"baaki_app"}, "create_recovery_action": {"baaki_app"},
    "opt_out_contact_from_evidence": {"baaki_app"}, "opt_out_by_operator": {"baaki_ops"},
}
ROLES = ["baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"]


def _sigs(su):
    return {r[0]: r[1] for r in su.execute(text(
        "select p.proname, p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki_write'"))}


def test_execute_matrix(su):
    sigs = _sigs(su)
    assert set(sigs) == set(MATRIX)
    for fn, exp in MATRIX.items():
        got = {r for r in ROLES if su.execute(text("select has_function_privilege(:r, :f, 'EXECUTE')"), {"r": r, "f": sigs[fn]}).scalar_one()}
        assert got == exp, (fn, got)


def test_public_revoked_on_all_writers(su):
    for fn, sig in _sigs(su).items():
        # A fresh role with no grants stands in for PUBLIC.
        su.execute(text("DROP ROLE IF EXISTS baaki_probe_anon"))
        su.execute(text("CREATE ROLE baaki_probe_anon"))
        try:
            assert su.execute(text("select has_function_privilege('baaki_probe_anon', :f, 'EXECUTE')"), {"f": sig}).scalar_one() is False, fn
        finally:
            su.execute(text("DROP ROLE baaki_probe_anon"))
            su.commit()


def test_trigger_helpers_not_public(su):
    rows = su.execute(text(
        "select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki' and p.proname like 'trgf_%'")).all()
    assert len(rows) == 5
    su.execute(text("DROP ROLE IF EXISTS baaki_probe_anon")); su.execute(text("CREATE ROLE baaki_probe_anon"))
    try:
        for (sig,) in rows:
            assert su.execute(text("select has_function_privilege('baaki_probe_anon', :f, 'EXECUTE')"), {"f": sig}).scalar_one() is False
    finally:
        su.execute(text("DROP ROLE baaki_probe_anon")); su.commit()
