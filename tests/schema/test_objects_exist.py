"""A. Schema inventory — exactly the P1 objects of ARCHITECTURE.md §13.3."""
from sqlalchemy import text

from tests.conftest import P1_TABLES


def test_object_counts(su):
    q = lambda sql: su.execute(text(sql)).scalar_one()
    assert q("select count(*) from pg_tables where schemaname='baaki'") == 15
    assert q("select count(*) from pg_type t join pg_namespace n on n.oid=t.typnamespace where n.nspname='baaki' and t.typtype='e'") == 20
    assert q("select count(*) from pg_views where schemaname='baaki'") == 1
    assert q("select count(*) from pg_trigger tg join pg_class c on c.oid=tg.tgrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='baaki' and not tg.tgisinternal") == 5
    assert q("select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki_write'") == 14  # +W15/W16 approval writers
    assert q("select count(*) from pg_extension where extname='pgcrypto'") == 1
    assert q("select count(*) from pg_roles where rolname in ('baaki_owner','baaki_migrate','baaki_app','baaki_ops','baaki_agent','baaki_sim')") == 6


def test_table_names(su):
    names = {r[0] for r in su.execute(text("select tablename from pg_tables where schemaname='baaki'"))}
    assert names == set(P1_TABLES)


def test_trigger_names(su):
    names = {r[0] for r in su.execute(text(
        "select tgname from pg_trigger tg join pg_class c on c.oid=tg.tgrelid join pg_namespace n on n.oid=c.relnamespace "
        "where n.nspname='baaki' and not tg.tgisinternal"))}
    assert names == {"trg_ledger_balanced", "trg_ledger_one_invoice_per_txn", "trg_action_requires_executable_decision",
                     "trg_action_type_matches_decision", "trg_decision_linkage"}


def test_writer_names(su):
    names = {r[0] for r in su.execute(text("select proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki_write'"))}
    assert names == {"issue_invoice", "record_webhook_event", "record_sweep_run", "record_payment_event",
                     "ledger_apply_payment", "ledger_post_unapplied", "record_agent_proposal",
                     "record_validation_result", "record_policy_decision", "create_recovery_action",
                     "opt_out_contact_from_evidence", "opt_out_by_operator",
                     "approve_recovery_action", "reject_recovery_action"}


def test_every_object_owned_by_owner(su):
    bad = su.execute(text(
        "select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace "
        "where n.nspname in ('baaki','baaki_write') and c.relkind in ('r','v') and c.relowner <> 'baaki_owner'::regrole")).all()
    assert bad == []
    bad = su.execute(text(
        "select proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
        "where n.nspname in ('baaki','baaki_write') and p.proowner <> 'baaki_owner'::regrole "
        "and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')")).all()  # extension members excluded
    assert bad == []


def test_no_primary_key_has_default_and_all_single_column(su):
    rows = su.execute(text(
        "select c.conrelid::regclass::text, array_length(c.conkey,1), a.attname, pg_get_expr(d.adbin, d.adrelid) "
        "from pg_constraint c join pg_attribute a on a.attrelid=c.conrelid and a.attnum = any(c.conkey) "
        "left join pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum "
        "join pg_namespace n on n.oid=c.connamespace where n.nspname='baaki' and c.contype='p'")).all()
    assert rows, "no primary keys found"
    for rel, ncols, col, default in rows:
        assert ncols == 1, (rel, ncols)
        assert default is None, (rel, col, default)


def test_no_composite_superkeys(su):
    """§8: no UNIQUE constraint/index combines a PK column with trace_id/account_id copies."""
    rows = su.execute(text(
        "select c.conrelid::regclass::text, array_agg(a.attname order by a.attnum) from pg_constraint c "
        "join pg_attribute a on a.attrelid=c.conrelid and a.attnum = any(c.conkey) join pg_namespace n on n.oid=c.connamespace "
        "where n.nspname='baaki' and c.contype='u' group by 1, c.oid")).all()
    for rel, cols in rows:
        assert not ({"trace_id", "account_id"} & set(cols) and any(c.endswith("_id") and c.startswith(rel.split(".")[-1]) for c in cols)), (rel, cols)


def test_view_references_only_ledger(su):
    definition = su.execute(text("select pg_get_viewdef('baaki.v_invoice_outstanding'::regclass, true)")).scalar_one()
    assert "ledger_entry" in definition
    assert "issued_paise" not in definition and "invoice " not in definition.lower().replace("invoice_id", "")


def test_no_float_money_columns(su):
    rows = su.execute(text(
        "select table_name, column_name, data_type from information_schema.columns where table_schema='baaki' "
        "and data_type in ('real','double precision','money','numeric')")).all()
    allowed = {("agent_proposal", "confidence"), ("policy_decision", "effective_confidence")}
    assert {(t, c) for t, c, _ in rows} <= allowed, rows


def test_downgrade_and_upgrade_roundtrip(cluster):
    import os
    import subprocess

    from tests.conftest import ROOT
    env = dict(os.environ, BAAKI_MIGRATE_DSN=cluster.dsns["baaki_migrate"])
    subprocess.run(["uv", "run", "alembic", "downgrade", "base"], cwd=ROOT, env=env, check=True, capture_output=True)
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True)
    # secrets are re-seeded because 0001 downgrade drops provider_secret
    subprocess.run(["psql", cluster.dsns["baaki_migrate"], "-v", "ON_ERROR_STOP=1", "-q", "-v",
                    "webhook_secret=<TEST_WEBHOOK_SECRET>", "-f", str(ROOT / "bootstrap" / "secrets.sql")],
                   check=True, capture_output=True)
