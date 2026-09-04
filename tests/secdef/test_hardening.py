"""C. H1–H19 structural assertions over pg_proc for all 10 writers."""
import re

from sqlalchemy import text

FORBIDDEN_PARAM_NAMES = {"amount_paise", "p_amount_paise", "account_code", "p_account_code", "lines", "p_lines",
                         "signature_ok", "p_signature_ok", "provider_payload_hash", "p_provider_payload_hash",
                         "raw_response_hash", "p_raw_response_hash", "item_count", "p_item_count",
                         "provider_payment_id", "p_provider_payment_id", "state", "p_state", "approved_by",
                         "p_approved_by", "approved_by_role", "p_approved_by_role", "created_by_role", "p_created_by_role",
                         "source", "p_source", "arm", "p_arm_override"}


def _writers(su):
    return su.execute(text(
        "select p.proname, p.prosecdef, p.proconfig, l.lanname, p.prokind, p.provolatile, p.proleakproof, p.prosrc, p.proargnames, "
        "p.proowner::regrole::text from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_language l on l.oid=p.prolang "
        "where n.nspname='baaki_write'")).mappings().all()


def test_h1_h3_h4_h8_h9_h14(su):
    rows = _writers(su)
    assert len(rows) == 12
    for r in rows:
        assert r["proowner"] == "baaki_owner", r["proname"]                       # H1
        assert r["prosecdef"] is True, r["proname"]                                # H3
        assert r["proconfig"] and any(c.replace(" ", "") == "search_path=baaki,pg_catalog" for c in r["proconfig"]), r["proname"]  # H4
        assert r["lanname"] == "plpgsql", r["proname"]                             # H8
        assert r["prokind"] == "f", r["proname"]                                   # H9
        assert r["provolatile"] == "v" and r["proleakproof"] is False, r["proname"]  # H14


def test_h5_schema_qualified_references(su):
    tables = ["organization", "account", "contact", "template_registry", "provider_secret", "invoice", "ledger_entry", "payment_event",
              "webhook_event", "sweep_run", "agent_proposal", "validation_result", "policy_decision", "recovery_action", "outbox",
              "v_invoice_outstanding"]
    for r in _writers(su):
        src = r["prosrc"]
        for t in tables:
            for m in re.finditer(rf"\b(FROM|INTO|UPDATE|JOIN)\s+{t}\b", src, flags=re.I):
                raise AssertionError(f"{r['proname']}: unqualified reference {m.group(0)!r}")


def test_h7_h9_h13_no_dynamic_sql_txn_control_or_guc(su):
    for r in _writers(su):
        src = r["prosrc"]
        assert not re.search(r"\bEXECUTE\b", src, flags=re.I), r["proname"]          # H7
        assert "format(" not in src.lower(), r["proname"]                            # H7
        assert not re.search(r"\b(COMMIT|ROLLBACK)\b", src, flags=re.I), r["proname"]  # H9
        assert "current_setting" not in src.lower(), r["proname"]                    # H13


def test_no_caller_supplied_financial_authority(su):
    for r in _writers(su):
        names = set(r["proargnames"] or [])
        leak = names & FORBIDDEN_PARAM_NAMES
        assert not leak, (r["proname"], leak)


def test_h19_provider_secret_unreadable_and_no_signature_param(su):
    for role in ("baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"):
        assert su.execute(text("select has_table_privilege(:r, 'baaki.provider_secret', 'SELECT')"), {"r": role}).scalar_one() is False
    names = {n for r in _writers(su) if r["proname"] == "record_webhook_event" for n in r["proargnames"]}
    assert "p_signature_ok" not in names and "signature_ok" not in names


def test_h2_owner_nologin(su):
    assert su.execute(text("select rolcanlogin from pg_roles where rolname='baaki_owner'")).scalar_one() is False
