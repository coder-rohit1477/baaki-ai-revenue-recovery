"""Shared call helper: bind typed parameters, invoke a writer, map database refusals to domain errors."""

from __future__ import annotations

import json
from typing import Any

import psycopg.errors
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from baaki.domain.errors import UnauthorizedInvoker, WriterRefused


class WriterUniqueViolation(WriterRefused):
    """The writer's natural key already exists (caller decides whether that means 'already done')."""


def jsonb(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str, sort_keys=True)


def call(conn: Connection, sql: str, params: dict[str, Any]) -> Any:
    try:
        return conn.execute(text(sql), params)
    except DBAPIError as exc:  # pragma: no cover - mapping exercised in tests
        orig = exc.orig
        if isinstance(orig, psycopg.errors.InsufficientPrivilege):
            raise UnauthorizedInvoker(str(orig).strip()) from exc
        if isinstance(orig, psycopg.errors.UniqueViolation):
            raise WriterUniqueViolation("unique_violation", str(orig).strip()) from exc
        if isinstance(orig, psycopg.errors.RaiseException):
            code = (orig.diag.message_primary or "").strip()
            if code == "unauthorized_invoker":
                raise UnauthorizedInvoker(code) from exc
            raise WriterRefused(code, orig.diag.message_detail or "") from exc
        raise
