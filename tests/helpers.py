"""Shared test helpers: assertions for database refusals, fixture data, evidence builders."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg.errors
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from baaki.contracts.policy_decision import KERNEL_TOKEN, ExecutableDecision, NonExecutableDecision
from baaki.domain.enums import (
    ActionType,
    Arm,
    DegradationLevel,
    Verdict,
)
from baaki.domain.ids import new_id

TEST_WEBHOOK_SECRET = "<TEST_WEBHOOK_SECRET>"
_UNSET: Any = object()   # sentinel: 'fill a sensible default' vs an explicit None
H64 = "a" * 64
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
TODAY = date(2026, 9, 4)


@contextmanager
def raises_writer(code: str):
    with pytest.raises(DBAPIError) as ei:
        yield
    orig = ei.value.orig
    assert isinstance(orig, psycopg.errors.RaiseException), f"expected RaiseException, got {type(orig).__name__}: {orig}"
    assert orig.diag.message_primary == code, f"expected {code!r}, got {orig.diag.message_primary!r}"


@contextmanager
def raises_privilege():
    with pytest.raises(DBAPIError) as ei:
        yield
    assert isinstance(ei.value.orig, psycopg.errors.InsufficientPrivilege), repr(ei.value.orig)


@contextmanager
def raises_unique():
    with pytest.raises(DBAPIError) as ei:
        yield
    assert isinstance(ei.value.orig, psycopg.errors.UniqueViolation), repr(ei.value.orig)


@contextmanager
def raises_check():
    with pytest.raises(DBAPIError) as ei:
        yield
    assert isinstance(ei.value.orig, psycopg.errors.CheckViolation), repr(ei.value.orig)


@contextmanager
def raises_any_db_error():
    with pytest.raises(DBAPIError):
        yield


def count(conn: Connection, table: str) -> int:
    return int(conn.execute(text(f"SELECT count(*) FROM baaki.{table}")).scalar_one())


def outstanding(conn: Connection, invoice_id: UUID) -> int:
    v = conn.execute(
        text("SELECT outstanding_paise FROM baaki.v_invoice_outstanding WHERE invoice_id = :i"), {"i": invoice_id}
    ).scalar()
    return int(v or 0)


# ── seed (as owner: C/M-class rows a migration or ingestion would create) ───────────────
def seed_org_account_contact(owner: Connection) -> dict[str, UUID]:
    org, acct, contact = new_id(), new_id(), new_id()
    owner.execute(text("INSERT INTO baaki.organization (org_id, name, timezone) VALUES (:o, 'Seller Pvt Ltd', 'Asia/Kolkata')"), {"o": org})
    owner.execute(text("INSERT INTO baaki.account (account_id, org_id, external_ref, name) VALUES (:a, :o, 'ACC-1', 'Buyer Ltd')"), {"a": acct, "o": org})
    owner.execute(text(
        "INSERT INTO baaki.contact (contact_id, account_id, channel, address_hash, address_redacted) "
        "VALUES (:c, :a, 'EMAIL', :h, 'a***@buyer.example')"), {"c": contact, "a": acct, "h": hashlib.sha256(b"a@buyer").hexdigest()})
    owner.execute(text(
        "INSERT INTO baaki.template_registry (template_id, channel, action_type, purpose, active, version, body_hash) "
        "VALUES ('tpl.reminder.email.inactive', 'EMAIL', 'SEND_REMINDER', 'REMINDER', false, 1, :h) ON CONFLICT (template_id) DO NOTHING"),
        {"h": H64})
    owner.commit()
    return {"org": org, "account": acct, "contact": contact}


def issue(app: Connection, ids: dict[str, UUID], amount: int = 450_000, number: str | None = None) -> UUID:
    inv = new_id()
    app.execute(text(
        "SELECT baaki_write.issue_invoice(:i, :o, :a, :n, :amt, :d1, :d2, :t)"),
        {"i": inv, "o": ids["org"], "a": ids["account"], "n": number or f"INV-{inv}",
         "amt": amount, "d1": TODAY - timedelta(days=30), "d2": TODAY - timedelta(days=15), "t": new_id()})
    app.commit()
    return inv


# ── provider evidence builders (A-R8 fixture shapes) ────────────────────────────────────
def payment_entity(payment_id: str, amount: int, invoice_id: UUID | None, *, currency: str = "INR",
                   status: str = "captured", epoch: int = 1_756_960_000) -> str:
    notes = {"invoice_id": str(invoice_id)} if invoice_id else {}
    return json.dumps({"id": payment_id, "amount": amount, "currency": currency, "status": status,
                       "created_at": epoch, "notes": notes}, separators=(",", ":"))


def webhook_body(entity_json: str, event: str = "payment.captured") -> str:
    return '{"event":"' + event + '","payload":{"payment":{"entity":' + entity_json + '}}}'


def sign(body: str, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def sweep_response(entities: list[str]) -> str:
    return '{"items":[' + ",".join(entities) + '],"count":' + str(len(entities)) + "}"


def record_webhook(app: Connection, body: str, header: str | None) -> UUID:
    eid = new_id()
    got = app.execute(text("SELECT baaki_write.record_webhook_event(:e, 'razorpay', :b, :h, :r)"),
                      {"e": eid, "b": body, "h": header, "r": NOW}).scalar_one()
    app.commit()
    return UUID(str(got))


def record_sweep(app: Connection, raw: str) -> UUID:
    sid = new_id()
    got = app.execute(text("SELECT baaki_write.record_sweep_run(:s, 'razorpay', :f, :t, :r, :raw)"),
                      {"s": sid, "f": NOW - timedelta(days=1), "t": NOW, "r": NOW, "raw": raw}).scalar_one()
    app.commit()
    return UUID(str(got))


def record_payment(app: Connection, *, webhook_event_id: UUID | None = None, sweep_run_id: UUID | None = None,
                   item: str, invoice_id: UUID | None, method: str | None = None) -> UUID:
    pid = new_id()
    m = method or ("NOTES_INVOICE_ID" if invoice_id else "UNATTRIBUTED")
    app.execute(text(
        "SELECT baaki_write.record_payment_event(:p, :w, :s, :raw, :inv, CAST(:m AS baaki.attribution_method))"),
        {"p": pid, "w": webhook_event_id, "s": sweep_run_id, "raw": item, "inv": invoice_id, "m": m})
    return pid


def apply_payment(app: Connection, pid: UUID) -> None:
    app.execute(text("SELECT baaki_write.ledger_apply_payment(:p, :t)"), {"p": pid, "t": new_id()})


def webhook_payment(app: Connection, invoice_id: UUID | None, amount: int, payment_id: str | None = None) -> tuple[UUID, str]:
    ent = payment_entity(payment_id or f"pay_{str(uuid4())[:8]}", amount, invoice_id)
    body = webhook_body(ent)
    ev = record_webhook(app, body, sign(body))
    return ev, ent


# ── decision chain builders ─────────────────────────────────────────────────────────────
def record_proposal(agent: Connection, ids: dict[str, UUID], invoice_id: UUID | None, *, parsed: Any = _UNSET,
                    parse_status: str = "OK", input_hash: str | None = None, business_date: date = TODAY) -> UUID:
    pid = new_id()
    if parsed is _UNSET:
        parsed = {"intent": "WILL_PAY_ON_DATE", "promised_date_raw": "next Tuesday"} if parse_status == "OK" else None
    agent.execute(text(
        "SELECT baaki_write.record_agent_proposal(:p, :t, :a, 'INTERPRETATION', :inv, :d, 'openai', 'model-x', "
        "'interp.v1', 'interpretation.v1', :h, :ih, CAST(:raw AS jsonb), CAST(:parsed AS jsonb), "
        "CAST(:ps AS baaki.parse_status), CAST(:conf AS numeric), '[]'::jsonb, 120)"),
        {"p": pid, "t": new_id(), "a": ids["account"], "inv": invoice_id, "d": business_date, "h": H64,
         "ih": input_hash or hashlib.sha256(str(pid).encode()).hexdigest(), "raw": json.dumps({"x": 1}),
         "parsed": json.dumps(parsed) if parsed is not None else None, "ps": parse_status,
         "conf": 0.9 if parse_status == "OK" else None})
    agent.commit()
    return pid


def record_validation(app: Connection, proposal_id: UUID, *, outcome: str = "PASS",
                      reasons: list[str] | None = None, normalized: Any = _UNSET) -> UUID:
    vid = new_id()
    if normalized is _UNSET:
        normalized = {"intent": "WILL_PAY_ON_DATE", "effective_confidence": 0.9} if outcome == "PASS" else None
    app.execute(text(
        "SELECT baaki_write.record_validation_result(:v, :p, CAST(:o AS baaki.validation_outcome), "
        "CAST(:r AS baaki.rejection_reason[]), CAST(:n AS jsonb), '[]'::jsonb, 'validator.v1', :h)"),
        {"v": vid, "p": proposal_id, "o": outcome, "r": reasons or [],
         "n": json.dumps(normalized) if normalized is not None else None, "h": H64})
    return vid


def exec_decision(ids: dict[str, UUID], invoice_id: UUID, action_type: ActionType, payload: Any, *,
                  verdict: Verdict = Verdict.ALLOW, tier: int | None = None, proposal_id: UUID | None = None,
                  validation_id: UUID | None = None, arm: Arm = Arm.CONTROL,
                  degradation: DegradationLevel = DegradationLevel.L1) -> ExecutableDecision:
    if tier is None:
        tier = 2 if verdict is Verdict.REQUIRE_APPROVAL else 1
    return ExecutableDecision(
        _token=KERNEL_TOKEN, decision_id=new_id(), trace_id=new_id(), proposal_id=proposal_id,
        validation_id=validation_id, arm=arm, account_id=ids["account"], invoice_id=invoice_id,
        business_date=TODAY, tier=tier, verdict=verdict, action_type=action_type, canonical_payload=payload,
        policy_version="policy.v1", kernel_version="kernel.v1", policy_hash=H64, snapshot_hash=H64,
        degradation_level=degradation, decided_at=NOW,
    )


def nonexec_decision(ids: dict[str, UUID], invoice_id: UUID, verdict: Verdict, *, proposal_id: UUID | None = None,
                     validation_id: UUID | None = None, arm: Arm = Arm.CONTROL) -> NonExecutableDecision:
    return NonExecutableDecision(
        _token=KERNEL_TOKEN, decision_id=new_id(), trace_id=new_id(), proposal_id=proposal_id,
        validation_id=validation_id, arm=arm, account_id=ids["account"], invoice_id=invoice_id,
        business_date=TODAY, tier=0, verdict=verdict,
        blocking_rules=[{"rule_id": "opt_out", "reason_code": "P2"}] if verdict is Verdict.BLOCK else [],
        defer_until=(NOW + timedelta(hours=12)) if verdict is Verdict.DEFER else None,
        policy_version="policy.v1", kernel_version="kernel.v1", policy_hash=H64, snapshot_hash=H64,
        degradation_level=DegradationLevel.L1, decided_at=NOW,
    )
