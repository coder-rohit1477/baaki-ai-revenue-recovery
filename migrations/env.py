"""Alembic environment (ARCHITECTURE.md H16).

Connects with BAAKI_MIGRATE_DSN, refuses to run unless the authenticated role is baaki_migrate,
then SET ROLE baaki_owner so every created object is owned by the NOLOGIN owner.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, text

DSN_ENV = "BAAKI_MIGRATE_DSN"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise SystemExit(f"{DSN_ENV} is required to run migrations (see ARCHITECTURE.md H16)")
    return dsn


def _sa(dsn: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn[len(prefix):]
    return dsn


def run_migrations_online() -> None:
    engine = create_engine(_sa(_dsn()), future=True)
    with engine.connect() as connection:
        who = connection.execute(text("select session_user")).scalar_one()
        if who != "baaki_migrate":
            raise SystemExit(f"migrations must run as baaki_migrate, not {who!r}")
        connection.execute(text("SET ROLE baaki_owner"))  # session-level; survives the commit below
        connection.commit()
        cur = connection.execute(text("select current_user")).scalar_one()
        if cur != "baaki_owner":
            raise SystemExit("SET ROLE baaki_owner failed")
        connection.commit()
        # Each migration runs in its own transaction: a failing migration leaves no partial objects.
        context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported: migrations must execute under SET ROLE baaki_owner")
run_migrations_online()
