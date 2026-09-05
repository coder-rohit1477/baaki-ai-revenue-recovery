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

from baaki.db.writers._call import WriterUniqueViolation
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
    """The recovery queue. Every column is read back from the database — nothing is computed here.

    `latest_action` is the account's most recent committed decision, or NULL when Baaki has not evaluated
    the account yet. It is never a guess about what Baaki *would* do.
    """
    return _rows(engine, """
        SELECT a.account_id, a.name, a.opt_out, i.invoice_id, i.invoice_number, i.state,
               i.due_date, v.outstanding_paise,
               GREATEST(0, (CURRENT_DATE - i.due_date))                       AS days_overdue,
               (SELECT count(*) FROM baaki.contact c
                 WHERE c.account_id = a.account_id AND c.active AND NOT c.opted_out) AS contactable,
               d.verdict AS latest_verdict, d.action_type AS latest_action,
               d.tier AS latest_tier, d.degradation_level AS latest_level,
               p.parsed ->> 'intent' AS latest_intent
        FROM baaki.account a
        JOIN baaki.invoice i ON i.account_id = a.account_id
        JOIN baaki.v_invoice_outstanding v ON v.invoice_id = i.invoice_id
        LEFT JOIN LATERAL (
            SELECT verdict, action_type, tier, degradation_level
            FROM baaki.policy_decision pd
            WHERE pd.account_id = a.account_id
            ORDER BY pd.decision_id DESC LIMIT 1
        ) d ON TRUE
        LEFT JOIN LATERAL (
            SELECT parsed FROM baaki.agent_proposal ap
            WHERE ap.account_id = a.account_id AND ap.kind = 'INTERPRETATION' AND ap.parsed IS NOT NULL
            ORDER BY ap.created_at DESC LIMIT 1
        ) p ON TRUE
        ORDER BY v.outstanding_paise DESC
    """)


def recent_activity(engine: Engine, limit: int = 8) -> list[dict[str, Any]]:
    """Recovery activity across the book: decisions taken and provider payments confirmed."""
    return _rows(engine, """
        SELECT * FROM (
            SELECT 'DECISION' AS kind, a.name, d.action_type::text AS detail,
                   d.verdict::text AS status, d.degradation_level::text AS level,
                   NULL::bigint AS amount_paise, d.decision_id::text AS ref
            FROM baaki.policy_decision d JOIN baaki.account a USING (account_id)
            UNION ALL
            SELECT 'PAYMENT', a.name, i.invoice_number::text,
                   CASE WHEN p.provider_payment_id LIKE 'demo\\_pay\\_%' ESCAPE '\\'
                        THEN 'Deterministic Simulator' ELSE 'Razorpay Test Mode' END,
                   NULL::text, p.amount_paise::bigint, p.payment_event_id::text
            FROM baaki.payment_event p
            JOIN baaki.invoice i ON i.invoice_id = p.attributed_invoice_id
            JOIN baaki.account a ON a.account_id = i.account_id
        ) x ORDER BY ref DESC LIMIT :lim
    """, {"lim": limit})


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


def reconcile_provider_payments(
    engine_app: Engine, *, invoice_id: UUID, raw_response: str, items: list[tuple[dict[str, Any], str]]
) -> dict[str, Any]:
    """Reconcile REAL provider payments through the same writers the simulator uses.

    This is the whole point of the Razorpay path: nothing downstream changes. The raw provider response is
    recorded as a reconciliation sweep, each payment payload is handed to the payment writer as a literal
    substring of that response, and the ledger applies it. Amounts, invoice state and the stopping rule are
    all derived in-database, exactly as before.

    Idempotency is the schema's, not ours: `uq_sweep_response` returns the existing sweep for an identical
    response, and `uq_payment_provider_id` refuses a payment already recorded. Re-checking is therefore
    safe and has no financial effect the second time.
    """
    now = datetime.now(UTC)
    applied: list[dict[str, Any]] = []
    already: list[str] = []
    with engine_app.connect() as app:
        sweep = record_sweep_run(
            app, sweep_run_id=new_id(), provider="razorpay", window_from=now - timedelta(days=1),
            window_to=now, requested_at=now, raw_response=raw_response,
        )
        app.commit()
        for obj, span in items:
            try:
                pid = record_payment_event(
                    app, payment_event_id=new_id(), webhook_event_id=None, sweep_run_id=sweep,
                    provider_payload_raw=span, attributed_invoice_id=invoice_id,
                    attribution_method=AttributionMethod.NOTES_INVOICE_ID,
                )
                ledger_apply_payment(app, payment_event_id=pid, trace_id=new_id())
                app.commit()
                applied.append({"provider_payment_id": str(obj.get("id")), "amount_paise": int(obj.get("amount", 0))})
            except WriterUniqueViolation:
                app.rollback()   # already reconciled on an earlier check — the ledger is unchanged
                already.append(str(obj.get("id")))
    return {
        "matched": len(items),
        "applied": applied,
        "already_reconciled": already,
        "outstanding_paise": outstanding(engine_app, invoice_id),
        "invoice_state": invoice_state(engine_app, invoice_id),
        "source": "razorpay_test_mode",
    }


# ── operational read models for the product surfaces ─────────────────────────────────────


def pending_approvals(engine: Engine) -> list[dict[str, Any]]:
    """Actions the kernel parked at PENDING_APPROVAL — real rows, not a UI construct.

    There is deliberately no approve/reject path in this system: `COLUMN_UPDATE_GRANTS` (§6.4A) is the only
    direct UPDATE capability in the schema and it does not include `recovery_action`, so no role — and
    therefore no model — can transition an action's state. Operator approval is a Phase 4 authority.
    """
    return _rows(engine, """
        SELECT r.action_id, r.action_type::text AS action_type, r.state::text AS state, r.created_at,
               d.tier, d.verdict::text AS verdict, d.degradation_level::text AS level,
               a.name, i.invoice_number, v.outstanding_paise
        FROM baaki.recovery_action r
        JOIN baaki.policy_decision d USING (decision_id)
        JOIN baaki.account a ON a.account_id = r.account_id
        JOIN baaki.invoice i ON i.invoice_id = r.invoice_id
        JOIN baaki.v_invoice_outstanding v ON v.invoice_id = i.invoice_id
        WHERE r.state = 'PENDING_APPROVAL'
        ORDER BY r.created_at DESC
    """)


def funnel(engine: Engine) -> list[dict[str, Any]]:
    """Recovery funnel. Every count is a query over committed state; none is derived in the browser."""
    row = _rows(engine, """
        SELECT
          (SELECT count(*) FROM baaki.invoice i JOIN baaki.v_invoice_outstanding v USING (invoice_id)
             WHERE i.due_date < CURRENT_DATE AND v.outstanding_paise > 0)              AS overdue,
          (SELECT count(DISTINCT account_id) FROM baaki.agent_proposal
             WHERE kind = 'INTERPRETATION')                                            AS replied,
          (SELECT count(DISTINCT account_id) FROM baaki.policy_decision)               AS active,
          (SELECT count(DISTINCT account_id) FROM baaki.agent_proposal
             WHERE kind = 'INTERPRETATION' AND parsed ->> 'intent' = 'WILL_PAY_ON_DATE') AS promised,
          (SELECT count(DISTINCT i.invoice_id) FROM baaki.invoice i
             JOIN baaki.v_invoice_outstanding v USING (invoice_id)
             WHERE v.outstanding_paise > 0 AND v.outstanding_paise < i.issued_paise)   AS part_paid,
          (SELECT count(*) FROM baaki.invoice WHERE state = 'PAID')                    AS paid
    """)[0]
    stopped = int(row["paid"])  # a settled invoice is no longer an eligible recovery candidate
    return [
        {"label": "Overdue", "n": int(row["overdue"])},
        {"label": "Customer replied", "n": int(row["replied"])},
        {"label": "Recovery active", "n": int(row["active"])},
        {"label": "Promise to pay", "n": int(row["promised"])},
        {"label": "Partially paid", "n": int(row["part_paid"])},
        {"label": "Paid", "n": int(row["paid"])},
        {"label": "Recovery stopped", "n": stopped},
    ]


def attention(engine: Engine) -> list[dict[str, Any]]:
    """Accounts genuinely needing a human. Only states that exist are reported."""
    out: list[dict[str, Any]] = []
    for r in pending_approvals(engine):
        out.append({"kind": "Approval required", "name": r["name"], "invoice": r["invoice_number"],
                    "detail": f"{r['action_type']} · tier {r['tier']}", "tone": "ai"})
    for r in _rows(engine, """
        SELECT a.name, i.invoice_number, v.outstanding_paise
        FROM baaki.account a JOIN baaki.invoice i ON i.account_id = a.account_id
        JOIN baaki.v_invoice_outstanding v ON v.invoice_id = i.invoice_id
        WHERE EXISTS (SELECT 1 FROM baaki.contact c WHERE c.account_id = a.account_id AND c.opted_out)
    """):
        out.append({"kind": "Opted out", "name": r["name"], "invoice": r["invoice_number"],
                    "detail": "excluded from future recovery contact", "tone": "bad"})
    for r in _rows(engine, """
        SELECT DISTINCT a.name, i.invoice_number, p.parse_status::text AS parse_status
        FROM baaki.agent_proposal p JOIN baaki.account a USING (account_id)
        JOIN baaki.invoice i ON i.account_id = a.account_id
        WHERE p.parse_status <> 'OK'
    """):
        out.append({"kind": "AI output rejected", "name": r["name"], "invoice": r["invoice_number"],
                    "detail": f"{r['parse_status']} — deterministic fallback used", "tone": "warn"})
    return out[:8]


def activity_timeline(engine: Engine, limit: int = 24) -> list[dict[str, Any]]:
    """Chronological audit trail. Each row is an event that actually happened, with its own timestamp."""
    return _rows(engine, """
        SELECT * FROM (
          SELECT p.created_at AS at, 'AI' AS lane,
                 CASE WHEN p.kind = 'INTERPRETATION' THEN 'AI interpreted customer response'
                      ELSE 'AI proposed a recovery action' END AS title,
                 COALESCE(p.parsed ->> 'intent', p.parse_status::text) AS detail, a.name
          FROM baaki.agent_proposal p JOIN baaki.account a USING (account_id)
          UNION ALL
          SELECT v.created_at, 'POLICY',
                 CASE WHEN v.outcome = 'PASS' THEN 'Validator accepted the proposal'
                      ELSE 'Validator rejected the proposal' END,
                 COALESCE(array_to_string(ARRAY(SELECT jsonb_array_elements_text(
                   to_jsonb(v.rejection_reasons))), ', '), v.outcome::text), a.name
          FROM baaki.validation_result v JOIN baaki.account a USING (account_id)
          UNION ALL
          SELECT d.decided_at, 'POLICY',
                 CASE WHEN d.verdict = 'REQUIRE_APPROVAL' THEN 'Policy kernel requires human approval'
                      WHEN d.verdict = 'ALLOW' THEN 'Policy kernel allowed the action'
                      ELSE 'Policy kernel blocked the action' END,
                 COALESCE(d.action_type::text, d.verdict::text), a.name
          FROM baaki.policy_decision d JOIN baaki.account a USING (account_id)
          UNION ALL
          SELECT r.created_at, 'ACTION',
                 CASE WHEN r.state = 'PENDING_APPROVAL' THEN 'Action held for operator approval'
                      ELSE 'Recovery action queued' END,
                 r.action_type::text, a.name
          FROM baaki.recovery_action r JOIN baaki.account a USING (account_id)
          UNION ALL
          SELECT p.created_at, 'MONEY', 'Payment confirmed',
                 CASE WHEN p.provider_payment_id LIKE 'demo\\_pay\\_%' ESCAPE '\\'
                      THEN 'Deterministic Simulator' ELSE 'Razorpay Test Mode' END
                 || ' · ' || (p.amount_paise / 100)::text, a.name
          FROM baaki.payment_event p JOIN baaki.invoice i ON i.invoice_id = p.attributed_invoice_id
          JOIN baaki.account a ON a.account_id = i.account_id
          UNION ALL
          SELECT (SELECT max(pe.created_at) FROM baaki.payment_event pe
                    WHERE pe.attributed_invoice_id = i.invoice_id),
                 'MONEY', 'Invoice settled — recovery stopped', i.invoice_number, a.name
          FROM baaki.invoice i JOIN baaki.account a USING (account_id)
          WHERE i.state = 'PAID'
        ) x WHERE at IS NOT NULL ORDER BY at DESC LIMIT :lim
    """, {"lim": limit})
