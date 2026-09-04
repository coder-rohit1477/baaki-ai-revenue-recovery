"""Phase 1 grants — the privilege model of ARCHITECTURE.md v3.2.1 §6.3, §6.4A, §6.6.

Forbidden privileges are REVOKEd explicitly (documentary) even where never granted.

Revision ID: 0003
Revises: 0002
"""

from alembic import op


def _sql(statement: str) -> None:
    """Execute raw SQL without SQLAlchemy bind-parameter parsing (PL/pgSQL bodies contain ':=' and JSON paths)."""
    # Raw DBAPI cursor, no parameters: neither SQLAlchemy (":name") nor psycopg ("%") token parsing applies.
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLES = ["baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"]

# Table SELECT per §6.3 (owner holds ALL implicitly; migrate acts only via SET ROLE baaki_owner).
SELECT_GRANTS: dict[str, list[str]] = {
    "organization":      ["baaki_app", "baaki_ops"],
    "account":           ["baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"],
    "contact":           ["baaki_app", "baaki_ops", "baaki_agent"],
    "template_registry": ["baaki_app", "baaki_ops", "baaki_agent"],
    "provider_secret":   [],
    "invoice":           ["baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"],
    "ledger_entry":      ["baaki_app", "baaki_ops"],
    "payment_event":     ["baaki_app", "baaki_ops"],
    "webhook_event":     ["baaki_app", "baaki_ops"],
    "sweep_run":         ["baaki_app", "baaki_ops"],
    "agent_proposal":    ["baaki_app", "baaki_ops", "baaki_agent"],
    "validation_result": ["baaki_app", "baaki_ops"],
    "policy_decision":   ["baaki_app", "baaki_ops"],
    "recovery_action":   ["baaki_app", "baaki_ops"],
    "outbox":            ["baaki_app", "baaki_ops"],
    "v_invoice_outstanding": ["baaki_app", "baaki_ops"],
}

# M-class direct INSERT (§6.1 rule 2).
INSERT_GRANTS: dict[str, list[str]] = {"account": ["baaki_app"], "contact": ["baaki_app"]}

# §6.4A — the only direct UPDATE capability in the schema.
COLUMN_UPDATE_GRANTS: list[tuple[str, str, str]] = [
    ("account", "risk_band", "baaki_app"),
    ("contact", "active", "baaki_app"),
]

# §6.6 EXECUTE matrix for W01–W10 (function name -> roles).
EXECUTE_GRANTS: dict[str, list[str]] = {
    "issue_invoice":            ["baaki_app"],
    "record_webhook_event":     ["baaki_app"],
    "record_sweep_run":         ["baaki_app"],
    "record_payment_event":     ["baaki_app"],
    "ledger_apply_payment":     ["baaki_app"],
    "ledger_post_unapplied":    ["baaki_app"],
    "record_agent_proposal":    ["baaki_agent"],   # revoked from baaki_app (§6.6)
    "record_validation_result": ["baaki_app"],
    "record_policy_decision":   ["baaki_app"],
    "create_recovery_action":   ["baaki_app"],
}

ALL_TABLES = [t for t in SELECT_GRANTS if t != "v_invoice_outstanding"]


def _fn_regprocs() -> str:
    # All P1 writers by name; argument lists are resolved via pg_proc in the DO block below.
    return ", ".join(f"'{n}'" for n in EXECUTE_GRANTS)


def upgrade() -> None:
    for role in APP_ROLES:
        _sql(f"GRANT USAGE ON SCHEMA baaki TO {role}")
        _sql(f"GRANT USAGE ON SCHEMA baaki_write TO {role}")
        _sql(f"REVOKE CREATE ON SCHEMA baaki FROM {role}")
        _sql(f"REVOKE CREATE ON SCHEMA baaki_write FROM {role}")
        # I10 (5): no role holds DELETE; F/D/C tables hold no DML for application roles.
        for t in ALL_TABLES:
            _sql(f"REVOKE ALL ON baaki.{t} FROM {role}")
        _sql(f"REVOKE ALL ON baaki.v_invoice_outstanding FROM {role}")

    for t, roles in SELECT_GRANTS.items():
        for role in roles:
            _sql(f"GRANT SELECT ON baaki.{t} TO {role}")
    for t, roles in INSERT_GRANTS.items():
        for role in roles:
            _sql(f"GRANT INSERT ON baaki.{t} TO {role}")
    for t, col, role in COLUMN_UPDATE_GRANTS:
        _sql(f"GRANT UPDATE ({col}) ON baaki.{t} TO {role}")

    # Function EXECUTE: revoke everything from app roles first, then grant per matrix.
    _sql(f"""
    DO $$
    DECLARE r record;
    BEGIN
      FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = 'baaki_write' AND p.proname IN ({_fn_regprocs()}) LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, baaki_app, baaki_ops, baaki_agent, baaki_sim', r.sig);
      END LOOP;
    END $$""")
    for fn, roles in EXECUTE_GRANTS.items():
        for role in roles:
            _sql(f"""
            DO $$
            DECLARE r record;
            BEGIN
              FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                       WHERE n.nspname = 'baaki_write' AND p.proname = '{fn}' LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO {role}', r.sig);
              END LOOP;
            END $$""")
    # Trigger and CHECK helper functions are never called directly by application roles.
    _sql("""
    DO $$
    DECLARE r record;
    BEGIN
      FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = 'baaki' AND p.proname LIKE 'trgf_%' LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
      END LOOP;
    END $$""")


def downgrade() -> None:
    for role in APP_ROLES:
        for t in ALL_TABLES:
            _sql(f"REVOKE ALL ON baaki.{t} FROM {role}")
        _sql(f"REVOKE ALL ON baaki.v_invoice_outstanding FROM {role}")
        _sql(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA baaki_write FROM {role}")
        _sql(f"REVOKE USAGE ON SCHEMA baaki FROM {role}")
        _sql(f"REVOKE USAGE ON SCHEMA baaki_write FROM {role}")
