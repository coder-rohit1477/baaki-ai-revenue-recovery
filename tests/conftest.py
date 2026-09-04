"""Test harness: throwaway database per session, role-scoped connections, network guard.

Requires a PostgreSQL superuser DSN in BAAKI_TEST_SUPERUSER_DSN (default: 127.0.0.1:5432).
Roles are created with bootstrap/roles.sql; the schema with alembic as baaki_migrate; the
webhook secret with bootstrap/secrets.sql. Nothing here touches a shared database.
"""

from __future__ import annotations

import os
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from baaki.db.engine import sa_url

ROOT = Path(__file__).resolve().parents[1]
SUPER_DSN = os.environ.get("BAAKI_TEST_SUPERUSER_DSN", "postgresql://postgres@127.0.0.1:5432/postgres")
TEST_WEBHOOK_SECRET = "<TEST_WEBHOOK_SECRET>"
ROLES = ("baaki_owner", "baaki_migrate", "baaki_app", "baaki_ops", "baaki_agent", "baaki_sim")
P1_TABLES = (
    "organization", "account", "contact", "template_registry", "provider_secret", "invoice",
    "webhook_event", "sweep_run", "payment_event", "ledger_entry", "agent_proposal",
    "validation_result", "policy_decision", "recovery_action", "outbox",
)


TEST_ROLE_PASSWORD = "baaki-test-pw"   # test-only; roles are created by bootstrap/roles.sql with this password


def _with(dsn: str, *, user: str | None = None, db: str | None = None) -> str:
    """Rewrite user (with the test role password) and/or database of a libpq URL.

    Trust-auth clusters ignore the password; password-auth clusters (the postgres:16 image) require it.
    """
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{TEST_ROLE_PASSWORD}@{host}"
    path = parts.path if db is None else f"/{db}"
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


@dataclass(frozen=True)
class Cluster:
    dbname: str
    super_dsn: str
    dsns: dict[str, str]

    def engine(self, role: str) -> Engine:
        return create_engine(sa_url(self.dsns[role]), future=True)


# ── network guard (ARCHITECTURE.md §7.4; test plan F.0) ─────────────────────────────────
_ALLOWED_HOSTS: set[tuple[str, int]] = set()
_real_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address):  # type: ignore[no-untyped-def]
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        if (host, int(port)) in _ALLOWED_HOSTS or host in ("127.0.0.1", "::1", "localhost"):
            return _real_connect(self, address)
        raise RuntimeError(f"network access blocked by test guard: {address}")
    return _real_connect(self, address)


@pytest.fixture(autouse=True)
def _network_guard(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.get_closest_marker("network"):
        yield
        return
    parts = urlsplit(SUPER_DSN)
    _ALLOWED_HOSTS.add((parts.hostname or "127.0.0.1", parts.port or 5432))
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = _real_connect  # type: ignore[method-assign]


# ── cluster lifecycle ────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def cluster() -> Iterator[Cluster]:
    dbname = f"baaki_test_{os.getpid()}"
    subprocess.run(
        ["psql", SUPER_DSN, "-v", "ON_ERROR_STOP=1", "-q",
         *sum([["-v", f"{r}={TEST_ROLE_PASSWORD}"] for r in ("migrate_pw", "app_pw", "ops_pw", "agent_pw", "sim_pw")], []),
         "-f", str(ROOT / "bootstrap" / "roles.sql")],
        check=True, capture_output=True,
    )
    with psycopg.connect(SUPER_DSN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {dbname} OWNER baaki_owner")
    dsns = {r: _with(SUPER_DSN, user=r, db=dbname) for r in ROLES if r != "baaki_owner"}
    dsns["super"] = _with(SUPER_DSN, db=dbname)
    env = dict(os.environ, BAAKI_MIGRATE_DSN=dsns["baaki_migrate"])
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True)
    subprocess.run(
        ["psql", dsns["baaki_migrate"], "-v", "ON_ERROR_STOP=1", "-q", "-v", f"webhook_secret={TEST_WEBHOOK_SECRET}",
         "-f", str(ROOT / "bootstrap" / "secrets.sql")],
        check=True, capture_output=True,
    )
    cl = Cluster(dbname=dbname, super_dsn=SUPER_DSN, dsns=dsns)
    yield cl
    with psycopg.connect(SUPER_DSN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)")


@pytest.fixture
def db(cluster: Cluster) -> Iterator[Cluster]:
    """Clean slate per test: truncate all P1 tables except provider_secret (bootstrap data)."""
    tables = ", ".join(f"baaki.{t}" for t in P1_TABLES if t != "provider_secret")
    with psycopg.connect(cluster.dsns["super"], autocommit=True) as c:
        c.execute(f"TRUNCATE {tables} CASCADE")
    yield cluster


def _conn(cluster: Cluster, role: str) -> Iterator[Connection]:
    eng = cluster.engine(role)
    with eng.connect() as conn:
        yield conn
    eng.dispose()


@pytest.fixture
def app(db: Cluster) -> Iterator[Connection]:
    yield from _conn(db, "baaki_app")


@pytest.fixture
def ops(db: Cluster) -> Iterator[Connection]:
    yield from _conn(db, "baaki_ops")


@pytest.fixture
def agent(db: Cluster) -> Iterator[Connection]:
    yield from _conn(db, "baaki_agent")


@pytest.fixture
def sim(db: Cluster) -> Iterator[Connection]:
    yield from _conn(db, "baaki_sim")


@pytest.fixture
def su(db: Cluster) -> Iterator[Connection]:
    yield from _conn(db, "super")


@pytest.fixture
def owner(db: Cluster) -> Iterator[Connection]:
    """baaki_owner context: connect as baaki_migrate and SET ROLE (the only way to reach owner)."""
    eng = db.engine("baaki_migrate")
    with eng.connect() as conn:
        conn.execute(text("SET ROLE baaki_owner"))
        conn.commit()  # SET is transactional; persist it for the fixture lifetime
        yield conn
    eng.dispose()
