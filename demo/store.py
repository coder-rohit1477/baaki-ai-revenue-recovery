"""Read-side queries and the simulated payment path.

Every figure the dashboard shows comes from here, i.e. from PostgreSQL, after the deterministic writers
committed it. The demo computes no balance of its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from baaki.db.writers.ledger import ledger_apply_payment
from baaki.db.writers.payment import record_payment_event
from baaki.db.writers.sweep import record_sweep_run
from baaki.domain.enums import AttributionMethod
from baaki.domain.ids import new_id

# The simulated confirmation goes through the reconciliation-sweep path rather than a forged webhook:
# minting a fake Razorpay signature would be pretending to hold an integration we do not have.
# `provider` is still recorded as "razorpay" because that is the sweep_run column's domain; the payload
# itself is synthetic and every surface that shows it is labelled SIMULATED.


# Tables the demo owns and may clear on reset. provider_secret and template_registry are bootstrap
# data that `seed()` does not recreate from scratch, so they are deliberately excluded.
RESETTABLE_TABLES: Final[tuple[str, ...]] = (
    "outbox", "recovery_action", "policy_decision", "validation_result", "agent_proposal",
    "ledger_entry", "payment_event", "sweep_run", "webhook_event", "invoice",
    "contact", "account", "organization",
)


def provider_payment_id() -> str:
    """A unique id for one simulated provider payment.

    `uuid4` is random, not time-ordered: the previous `new_id().hex[:10]` was the first 40 bits of a UUIDv7,
    i.e. a millisecond timestamp, so two payments in the same millisecond collided on
    `uq_payment_provider_id`. Production payment identity is unaffected — this value only ever labels a
    synthetic demo payload.
    """
    return f"demo_pay_{uuid4().hex}"


def truncate_demo_data(engine: Engine) -> None:
    """Clear every table the demo writes, so a reset restores the baseline instead of appending to it.

    Requires a role that may TRUNCATE (the demo passes its superuser engine). CASCADE is safe here because
    RESETTABLE_TABLES already lists the whole dependency closure the demo touches.
    """
    tables = ", ".join(f"baaki.{t}" for t in RESETTABLE_TABLES)
    with engine.connect() as c:
        c.execute(text(f"TRUNCATE {tables} CASCADE"))
        c.commit()


def _rows(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params or {})]


def dashboard(engine: Engine) -> dict[str, Any]:
    # "Overdue" is derived the same way the policy snapshot derives it: past due date with money still owed.
    totals = _rows(engine, """
        SELECT COALESCE(SUM(v.outstanding_paise), 0) AS at_risk_paise,
               COUNT(*) FILTER (WHERE i.due_date < CURRENT_DATE AND v.outstanding_paise > 0) AS overdue_accounts
        FROM baaki.v_invoice_outstanding v JOIN baaki.invoice i USING (invoice_id)
    """)[0]
    recovered = _rows(engine, """
        SELECT COALESCE(SUM(amount_paise), 0) AS recovered_paise
        FROM baaki.ledger_entry WHERE account_code LIKE 'AR%' AND direction = 'CREDIT'
    """)[0]
    decisions = _rows(engine, "SELECT COUNT(*) AS decisions FROM baaki.policy_decision")[0]
    return {
        "at_risk_paise": int(totals["at_risk_paise"]),
        "overdue_accounts": int(totals["overdue_accounts"]),
        "recovered_paise": int(recovered["recovered_paise"]),
        "decisions": int(decisions["decisions"]),
    }


def accounts(engine: Engine) -> list[dict[str, Any]]:
    return _rows(engine, """
        SELECT a.account_id, a.name, a.opt_out, i.invoice_id, i.invoice_number, i.state,
               i.due_date, v.outstanding_paise
        FROM baaki.account a
        JOIN baaki.invoice i ON i.account_id = a.account_id
        JOIN baaki.v_invoice_outstanding v ON v.invoice_id = i.invoice_id
        ORDER BY v.outstanding_paise DESC
    """)


def outstanding(engine: Engine, invoice_id: UUID) -> int:
    rows = _rows(engine, "SELECT outstanding_paise FROM baaki.v_invoice_outstanding WHERE invoice_id = :i",
                 {"i": invoice_id})
    return int(rows[0]["outstanding_paise"]) if rows else 0


def invoice_state(engine: Engine, invoice_id: UUID) -> str:
    return str(_rows(engine, "SELECT state FROM baaki.invoice WHERE invoice_id = :i", {"i": invoice_id})[0]["state"])


def timeline(engine: Engine, account_id: UUID) -> dict[str, Any]:
    """Account → proposal → validation → decision → action → payment → ledger, in one payload."""
    return {
        "proposals": _rows(engine, """
            SELECT proposal_id, kind, parse_status, provider, model_id, confidence, parsed, latency_ms, created_at
            FROM baaki.agent_proposal WHERE account_id = :a ORDER BY created_at
        """, {"a": account_id}),
        "validations": _rows(engine, """
            SELECT v.validation_id, v.proposal_id, v.outcome, v.rejection_reasons, v.created_at
            FROM baaki.validation_result v WHERE v.account_id = :a ORDER BY v.created_at
        """, {"a": account_id}),
        "decisions": _rows(engine, """
            SELECT decision_id, verdict, action_type, tier, blocking_rules, degradation_level, created_at
            FROM baaki.policy_decision WHERE account_id = :a ORDER BY created_at
        """, {"a": account_id}),
        "actions": _rows(engine, """
            SELECT r.action_id, r.action_type, r.state, r.channel, r.template_id, r.created_at
            FROM baaki.recovery_action r JOIN baaki.policy_decision d USING (decision_id)
            WHERE d.account_id = :a ORDER BY r.created_at
        """, {"a": account_id}),
        "payments": _rows(engine, """
            SELECT p.payment_event_id, p.amount_paise, p.attribution_method, p.created_at
            FROM baaki.payment_event p JOIN baaki.invoice i ON i.invoice_id = p.attributed_invoice_id
            WHERE i.account_id = :a ORDER BY p.created_at
        """, {"a": account_id}),
        "ledger": _rows(engine, """
            SELECT l.entry_id, l.account_code, l.direction, l.amount_paise, l.created_at
            FROM baaki.ledger_entry l JOIN baaki.invoice i ON i.invoice_id = l.invoice_id
            WHERE i.account_id = :a ORDER BY l.created_at
        """, {"a": account_id}),
    }


def simulate_payment(engine_app: Engine, *, invoice_id: UUID, amount_paise: int) -> dict[str, Any]:
    """SIMULATED provider confirmation → W03 sweep → W04 payment event → W05 ledger application.

    This is **not** a Razorpay integration. It is the reconciliation-sweep path the architecture already
    defines, fed a synthetic provider payload, so the money arithmetic, the invoice state transition and the
    ledger entries are all produced by the real in-database writers — never by the demo.
    """
    now = datetime.now(UTC)
    entity = json.dumps(
        {"id": provider_payment_id(), "amount": amount_paise, "currency": "INR",
         "status": "captured", "created_at": 1_756_960_000, "notes": {"invoice_id": str(invoice_id)}},
        separators=(",", ":"),
    )
    response = '{"items":[' + entity + '],"count":1}'
    with engine_app.connect() as app:
        sweep = record_sweep_run(
            app, sweep_run_id=new_id(), provider="razorpay", window_from=now - timedelta(days=1),
            window_to=now, requested_at=now, raw_response=response,
        )
        pid = record_payment_event(
            app, payment_event_id=new_id(), webhook_event_id=None, sweep_run_id=sweep,
            provider_payload_raw=entity, attributed_invoice_id=invoice_id,
            attribution_method=AttributionMethod.NOTES_INVOICE_ID,
        )
        ledger_apply_payment(app, payment_event_id=pid, trace_id=new_id())
        app.commit()
    return {
        "payment_event_id": str(pid),
        "amount_paise": amount_paise,
        "outstanding_paise": outstanding(engine_app, invoice_id),
        "invoice_state": invoice_state(engine_app, invoice_id),
        "simulated": True,
    }
