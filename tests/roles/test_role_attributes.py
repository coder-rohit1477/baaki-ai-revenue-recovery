"""B. §6.2 role attributes; owner NOLOGIN; single membership."""
from sqlalchemy import text

EXPECTED = {
    "baaki_owner":   dict(rolcanlogin=False, rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False, rolbypassrls=False),
    "baaki_migrate": dict(rolcanlogin=True,  rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False, rolbypassrls=False),
    "baaki_app":     dict(rolcanlogin=True,  rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False, rolbypassrls=False),
    "baaki_ops":     dict(rolcanlogin=True,  rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False, rolbypassrls=False),
    "baaki_agent":   dict(rolcanlogin=True,  rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False, rolbypassrls=False),
    "baaki_sim":     dict(rolcanlogin=True,  rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolinherit=False, rolreplication=False, rolbypassrls=False),
}


def test_role_attributes(su):
    for role, attrs in EXPECTED.items():
        row = su.execute(text(
            "select rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls from pg_roles where rolname=:r"),
            {"r": role}).mappings().one()
        assert dict(row) == attrs, role


def test_single_membership(su):
    rows = su.execute(text(
        "select m.rolname member, r.rolname role from pg_auth_members am join pg_roles m on m.oid=am.member join pg_roles r on r.oid=am.roleid "
        "where m.rolname like 'baaki_%' or r.rolname like 'baaki_%'")).all()
    assert [(a, b) for a, b in rows] == [("baaki_migrate", "baaki_owner")]


def test_owner_cannot_login(cluster):
    import psycopg
    import pytest

    from tests.conftest import _with
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(_with(cluster.super_dsn, user="baaki_owner", db=cluster.dbname), connect_timeout=3)
