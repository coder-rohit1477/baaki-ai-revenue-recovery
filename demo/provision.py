"""Create (or recreate) the demo database: roles, schema, webhook secret.

Mirrors `tests/conftest.py`'s cluster fixture, but persistent and named. Uses the project's own
bootstrap/roles.sql and alembic migrations — the demo never invents schema.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import psycopg
from sqlalchemy import Engine, create_engine

from baaki.db.engine import sa_url

ROOT = Path(__file__).resolve().parents[1]
SUPER_DSN = os.environ.get("BAAKI_DEMO_SUPERUSER_DSN", "postgresql://postgres@127.0.0.1:5432/postgres")
DB_NAME = os.environ.get("BAAKI_DEMO_DB", "baaki_demo")
# The pre-flight uses its own database. `provision(recreate=True)` issues DROP DATABASE ... WITH (FORCE),
# which terminates every connection to that database — running it against the demo server's database
# would kill the live demo mid-presentation.
CHECK_DB_NAME: Final[str] = os.environ.get("BAAKI_DEMO_CHECK_DB", "baaki_demo_check")
ROLE_PASSWORD = "baaki-demo-pw"  # local demo cluster only; never a production credential
DEMO_WEBHOOK_SECRET = "<DEMO_WEBHOOK_SECRET>"
ROLES = ("baaki_migrate", "baaki_app", "baaki_ops", "baaki_agent", "baaki_sim")


def _with(dsn: str, *, user: str | None = None, db: str | None = None) -> str:
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if user is not None:
        netloc = f"{user}:{ROLE_PASSWORD}@{netloc.rsplit('@', 1)[-1]}"
    path = parts.path if db is None else f"/{db}"
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


@dataclass(frozen=True)
class Demo:
    dsns: dict[str, str]

    def engine(self, role: str) -> Engine:
        return create_engine(sa_url(self.dsns[role]), future=True)


def dsns(db: str | None = None) -> dict[str, str]:
    name = db or DB_NAME
    out = {r: _with(SUPER_DSN, user=r, db=name) for r in ROLES}
    out["super"] = _with(SUPER_DSN, db=name)
    return out


def provision(*, recreate: bool = True, db: str | None = None) -> Demo:
    """Build (or rebuild) a demo database. `db` selects which one, so callers cannot collide."""
    name = db or DB_NAME
    subprocess.run(
        ["psql", SUPER_DSN, "-v", "ON_ERROR_STOP=1", "-q",
         *sum([["-v", f"{r}={ROLE_PASSWORD}"] for r in
               ("migrate_pw", "app_pw", "ops_pw", "agent_pw", "sim_pw")], []),
         "-f", str(ROOT / "bootstrap" / "roles.sql")],
        check=True, capture_output=True,
    )
    with psycopg.connect(SUPER_DSN, autocommit=True) as c:
        if recreate:
            c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            c.execute(f"CREATE DATABASE {name} OWNER baaki_owner")
    d = dsns(name)
    env = dict(os.environ, BAAKI_MIGRATE_DSN=d["baaki_migrate"])
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True)
    subprocess.run(
        ["psql", d["baaki_migrate"], "-v", "ON_ERROR_STOP=1", "-q",
         "-v", f"webhook_secret={DEMO_WEBHOOK_SECRET}", "-f", str(ROOT / "bootstrap" / "secrets.sql")],
        check=True, capture_output=True,
    )
    return Demo(dsns=d)
