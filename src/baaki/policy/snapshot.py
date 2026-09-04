"""Snapshot assembly — AccountFacts (pre-target) and AccountSnapshot (post-target).

One REPEATABLE READ read-only transaction (S1). Every field derives from Phase 1 tables or the ruleset (SN1);
fields owned by later phases are represented as None/[] (S4). payment_event is never mutated (S5).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text

from baaki.contracts.account_snapshot import AccountSnapshot, ActivePaymentLink, TemplateCatalogueEntry
from baaki.contracts.candidate import (
    AccountFacts,
    AppliedPaymentFact,
    CandidateInvoice,
    ContactFact,
    InvoiceRef,
    PaidClaimFact,
)
from baaki.domain.enums import ActionType, Channel, InvoiceState, TemplatePurpose
from baaki.domain.errors import ContractViolation
from baaki.domain.money import Paise, paise
from baaki.ledger.invariants import ledger_invariants_ok
from baaki.ledger.projection import candidate_rows
from baaki.policy.ruleset import Ruleset

OUTBOUND_TYPES = (
    "SEND_REMINDER",
    "SEND_PAYMENT_LINK",
    "PROPOSE_INSTALLMENT_PLAN",
    "REQUEST_DISPUTE_DETAILS",
    "ESCALATE_TO_HUMAN",
)
INTENT_STATES = ("QUEUED", "EXECUTING", "EXECUTED", "CONFIRMED")


def assemble_account_facts(engine: Engine, account_id: UUID, as_of: datetime, ruleset: Ruleset) -> AccountFacts:
    """Runs its own REPEATABLE READ read-only transaction and returns immutable facts."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        facts = _assemble(conn, account_id, as_of, ruleset)
        conn.rollback()
    return facts


def _assemble(conn: Connection, account_id: UUID, as_of: datetime, ruleset: Ruleset) -> AccountFacts:
    row = conn.execute(
        text(
            "SELECT a.org_id, a.opt_out, o.timezone, o.kill_switch FROM baaki.account a "
            "JOIN baaki.organization o ON o.org_id = a.org_id WHERE a.account_id = :a"
        ),
        {"a": account_id},
    ).one_or_none()
    if row is None:
        raise ContractViolation(f"account {account_id} not found")
    org_id, opt_out, tz, kill_switch = UUID(str(row[0])), bool(row[1]), str(row[2]), bool(row[3])
    business_date = as_of.astimezone(ZoneInfo(tz)).date()

    cands = [
        CandidateInvoice(
            invoice_id=i, invoice_number=n, state=InvoiceState(s), due_date=d, days_overdue=od, outstanding_paise=o
        )
        for (i, n, s, d, od, o) in candidate_rows(conn, account_id, business_date)
    ]
    all_inv = [
        InvoiceRef(invoice_id=UUID(str(r[0])), invoice_number=str(r[1]))
        for r in conn.execute(
            text("SELECT invoice_id, invoice_number FROM baaki.invoice WHERE account_id = :a ORDER BY invoice_id"),
            {"a": account_id},
        )
    ]
    contactable = [
        ContactFact(contact_id=UUID(str(r[0])), channel=Channel(str(r[1])))
        for r in conn.execute(
            text(
                "SELECT contact_id, channel::text FROM baaki.contact WHERE account_id = :a AND active AND NOT "
                "opted_out ORDER BY contact_id"
            ),
            {"a": account_id},
        )
    ]
    since = as_of - timedelta(days=7)
    contact_rows = conn.execute(
        text(
            "SELECT invoice_id, created_at FROM baaki.recovery_action WHERE account_id = :a AND created_at >= :since "
            "AND action_type::text = ANY(:types) AND state::text = ANY(:states)"
        ),
        {"a": account_id, "since": since, "types": list(OUTBOUND_TYPES), "states": list(INTENT_STATES)},
    ).all()
    per_invoice: dict[str, int] = {}
    last_contact: datetime | None = None
    for inv_id, created in contact_rows:
        k = str(inv_id)
        per_invoice[k] = per_invoice.get(k, 0) + 1
        last_contact = created if last_contact is None or created > last_contact else last_contact
    # P8 facts: SEND_PAYMENT_LINK actions accepted by the provider within the active window. None exist in P2
    # (no executor); the query exists so the semantics are real, not invented.
    link_since = as_of - timedelta(hours=ruleset.link_active_window_hours)
    links: dict[str, ActivePaymentLink] = {}
    for inv_id, ref, executed_at, payload in conn.execute(
        text(
            "SELECT ra.invoice_id, ra.provider_ref, ra.executed_at, pd.canonical_payload FROM baaki.recovery_action ra "
            "JOIN baaki.policy_decision pd ON pd.decision_id = ra.decision_id WHERE ra.account_id = :a "
            "AND ra.action_type = 'SEND_PAYMENT_LINK' AND ra.state IN ('EXECUTED','CONFIRMED') AND "
            "ra.provider_ref IS NOT NULL "
            "AND ra.executed_at >= :since ORDER BY ra.executed_at DESC"
        ),
        {"a": account_id, "since": link_since},
    ):
        k = str(inv_id)
        if k not in links:
            links[k] = ActivePaymentLink(
                link_id=str(ref), created_at=executed_at, amount_paise=paise(int(payload["amount_paise"]))
            )
    claims = [
        PaidClaimFact(validation_id=UUID(str(r[0])), claim_at=r[1], invoice_ids=[UUID(x) for x in (r[2] or [])])
        for r in conn.execute(
            text(
                "SELECT v.validation_id, v.created_at, "
                "ARRAY(SELECT jsonb_array_elements_text(COALESCE(v.normalized -> 'invoice_ids', '[]'::jsonb))) "
                "FROM baaki.validation_result v WHERE v.account_id = :a AND v.outcome = 'PASS' "
                "AND v.normalized ->> 'intent' = 'ALREADY_PAID_CLAIM' AND v.created_at >= :since ORDER BY "
                "v.created_at DESC, v.validation_id DESC"
            ),
            {"a": account_id, "since": as_of - timedelta(hours=ruleset.paid_claim_ttl_hours)},
        )
    ]
    applied = [
        AppliedPaymentFact(invoice_id=UUID(str(r[0])), applied_at=r[1])
        for r in conn.execute(
            text(
                "SELECT pe.attributed_invoice_id, pe.applied_at FROM baaki.payment_event pe JOIN baaki.invoice i ON "
                "i.invoice_id = pe.attributed_invoice_id "
                "WHERE i.account_id = :a AND pe.applied_at IS NOT NULL"
            ),
            {"a": account_id},
        )
    ]
    catalogue = [
        TemplateCatalogueEntry(
            template_id=str(r[0]),
            channel=Channel(str(r[1])),
            action_type=ActionType(str(r[2])),
            purpose=TemplatePurpose(str(r[3])),
            active=bool(r[4]),
        )
        for r in conn.execute(
            text(
                "SELECT template_id, channel::text, action_type::text, purpose::text, active FROM "
                "baaki.template_registry ORDER BY template_id"
            )
        )
    ]
    return AccountFacts(
        as_of=as_of,
        business_date=business_date,
        org_id=org_id,
        account_id=account_id,
        timezone=tz,
        kill_switch=kill_switch,
        ledger_invariant_ok=ledger_invariants_ok(conn),
        opt_out=opt_out,
        candidates=cands,
        all_invoices=all_inv,
        contactable=contactable,
        contacts_7d=len(contact_rows),
        contacts_invoice_7d=per_invoice,
        last_contact_at=last_contact,
        active_payment_links=links,
        paid_claims=claims,
        applied_payments=applied,
        template_catalogue=catalogue,
    )


def paid_claim_until(facts: AccountFacts, target_invoice_id: UUID, ruleset: Ruleset) -> datetime | None:
    """§5.6: latest claim in scope; cleared by a payment applied strictly after claim_at within the scope."""
    ttl = timedelta(hours=ruleset.paid_claim_ttl_hours)
    for claim in facts.paid_claims:  # already ordered latest-first (created_at desc, validation_id desc)
        scoped_to_target = (not claim.invoice_ids) or (target_invoice_id in claim.invoice_ids)
        if not scoped_to_target:
            continue
        until = claim.claim_at + ttl
        if facts.as_of >= until:
            return None  # expired; later (older) claims are older still
        scope = set(claim.invoice_ids) if claim.invoice_ids else {i.invoice_id for i in facts.all_invoices}
        cleared = any(p.invoice_id in scope and p.applied_at > claim.claim_at for p in facts.applied_payments)
        return None if cleared else until
    return None


def build_snapshot(facts: AccountFacts, target_invoice_id: UUID, ruleset: Ruleset) -> AccountSnapshot:
    target = facts.candidate(target_invoice_id)
    if target is None:
        raise ContractViolation("target invoice is not a candidate (SC4)")
    return AccountSnapshot.build(
        as_of=facts.as_of,
        business_date=facts.business_date,
        account_id=facts.account_id,
        candidate_invoice_ids=facts.candidate_ids,
        target_invoice_id=target_invoice_id,
        outstanding_paise=Paise(int(target.outstanding_paise)),
        invoice_state=target.state,
        days_overdue=target.days_overdue,
        opt_out=facts.opt_out,
        kill_switch=facts.kill_switch,
        ledger_invariant_ok=facts.ledger_invariant_ok,
        open_dispute_ids=[],  # S4: dispute table is P3
        unverified_paid_claim_until=paid_claim_until(facts, target_invoice_id, ruleset),
        active_ptp=None,  # S4: promise_to_pay is P3
        active_payment_link=facts.active_payment_links.get(str(target_invoice_id)),
        contacts_7d=facts.contacts_7d,
        contacts_invoice_7d=facts.contacts_invoice_7d.get(str(target_invoice_id), 0),
        last_contact_at=facts.last_contact_at,
        contactable_contact_ids=[c.contact_id for c in facts.contactable],
        template_catalogue=facts.template_catalogue,
    )
