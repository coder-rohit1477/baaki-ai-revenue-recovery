"""Role-scoped engines (ARCHITECTURE.md §6.2). The runtime knows only app/agent/sim DSNs."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import Engine, create_engine

from baaki.config import Settings

RuntimeRole = Literal["app", "agent", "sim"]


def sa_url(dsn: str) -> str:
    """libpq-style DSNs select the psycopg (v3) dialect explicitly; psycopg2 is not installed."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://"):]
    return dsn


def engine_from_dsn(dsn: str, *, isolation_level: str | None = None) -> Engine:
    if isolation_level:
        return create_engine(sa_url(dsn), future=True, pool_pre_ping=True, isolation_level=isolation_level)
    return create_engine(sa_url(dsn), future=True, pool_pre_ping=True)


def engine_for(settings: Settings, role: RuntimeRole) -> Engine:
    dsn = {
        "app": settings.baaki_app_dsn,
        "agent": settings.baaki_agent_dsn,
        "sim": settings.baaki_sim_dsn,
    }[role]
    return engine_from_dsn(dsn)
