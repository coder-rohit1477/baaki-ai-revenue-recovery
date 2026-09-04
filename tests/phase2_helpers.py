"""Phase 2 test builders: ruleset, pure AccountFacts/snapshots, proposals, contexts. Deterministic, clock-free."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Connection, text

from baaki.contracts.account_snapshot import AccountSnapshot, ActivePaymentLink, TemplateCatalogueEntry
from baaki.contracts.action_choice import ActionChoice, DecisionContext
from baaki.contracts.agent_proposal import AgentProposal, RawJson
from baaki.contracts.candidate import (
    AccountFacts,
    AppliedPaymentFact,
    CandidateInvoice,
    ContactFact,
    InvoiceRef,
    PaidClaimFact,
)
from baaki.contracts.validation_input import ValidationInput
from baaki.domain.enums import (
    ActionType,
    Arm,
    Channel,
    DegradationLevel,
    InvoiceState,
    ParseStatus,
    ProposalKind,
    TemplatePurpose,
)
from baaki.domain.errors import UnauthorizedInvoker, WriterRefused
from baaki.domain.ids import new_id
from baaki.domain.money import paise
from baaki.policy.ruleset import DEFAULT_RULESET_PATH, Ruleset, load_ruleset
from baaki.policy.snapshot import build_snapshot

RULESET: Ruleset = load_ruleset(DEFAULT_RULESET_PATH)
IST = ZoneInfo("Asia/Kolkata")
TZ = "Asia/Kolkata"

# Fixed workday instant for pure tests: Tuesday 2026-09-01 11:00 IST.
AS_OF = datetime(2026, 9, 1, 11, 0, tzinfo=IST).astimezone(UTC)
BDATE = date(2026, 9, 1)
SUNDAY_AS_OF = datetime(2026, 9, 6, 11, 0, tzinfo=IST).astimezone(UTC)   # Sunday
LATE_AS_OF = datetime(2026, 9, 1, 19, 0, tzinfo=IST).astimezone(UTC)      # 19:00 exclusive bound

ORG = UUID("00000000-0000-7000-8000-0000000000a1")
ACC = UUID("00000000-0000-7000-8000-0000000000a2")
C_EMAIL = UUID("00000000-0000-7000-8000-0000000000c1")
C_SMS = UUID("00000000-0000-7000-8000-0000000000c2")
INV1 = UUID("00000000-0000-7000-8000-0000000000e1")
INV2 = UUID("00000000-0000-7000-8000-0000000000e2")
OTHER_INV = UUID("00000000-0000-7000-8000-0000000000e9")

T = TemplateCatalogueEntry
TEMPLATES = [
    T(template_id="tpl.reminder.email.v1", channel=Channel.EMAIL, action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.REMINDER, active=True),
    T(template_id="tpl.reminder.sms.v1", channel=Channel.SMS, action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.REMINDER, active=True),
    T(template_id="tpl.nudge.email.v1", channel=Channel.EMAIL, action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.COURTESY_NUDGE, active=True),
    T(template_id="tpl.link.email.v1", channel=Channel.EMAIL, action_type=ActionType.SEND_PAYMENT_LINK, purpose=TemplatePurpose.PAYMENT_LINK, active=True),
    T(template_id="tpl.dispute.email.v1", channel=Channel.EMAIL, action_type=ActionType.REQUEST_DISPUTE_DETAILS, purpose=TemplatePurpose.DISPUTE_DETAILS_REQUEST, active=True),
    T(template_id="tpl.installment.email.v1", channel=Channel.EMAIL, action_type=ActionType.PROPOSE_INSTALLMENT_PLAN, purpose=TemplatePurpose.INSTALLMENT_PROPOSAL, active=True),
    T(template_id="tpl.reminder.email.inactive", channel=Channel.EMAIL, action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.REMINDER, active=False),
]


def cand(invoice_id: UUID = INV1, number: str = "INV-1", days_overdue: int = 15, outstanding: int = 450_000,
         state: InvoiceState = InvoiceState.OVERDUE, business_date: date = BDATE) -> CandidateInvoice:
    return CandidateInvoice(invoice_id=invoice_id, invoice_number=number, state=state, due_date=business_date - timedelta(days=days_overdue),
                            days_overdue=days_overdue, outstanding_paise=paise(outstanding))


def facts(*, candidates: list[CandidateInvoice] | None = None, as_of: datetime = AS_OF, kill_switch: bool = False,
          ledger_ok: bool = True, opt_out: bool = False, contactable: list[ContactFact] | None = None, contacts_7d: int = 0,
          contacts_invoice_7d: dict[str, int] | None = None, last_contact_at: datetime | None = None,
          links: dict[str, ActivePaymentLink] | None = None, paid_claims: list[PaidClaimFact] | None = None,
          applied: list[AppliedPaymentFact] | None = None, templates: list[TemplateCatalogueEntry] | None = None,
          extra_invoices: list[InvoiceRef] | None = None) -> AccountFacts:
    cands = [cand()] if candidates is None else candidates
    bdate = as_of.astimezone(IST).date()
    return AccountFacts(
        as_of=as_of, business_date=bdate, org_id=ORG, account_id=ACC, timezone=TZ, kill_switch=kill_switch,
        ledger_invariant_ok=ledger_ok, opt_out=opt_out, candidates=cands,
        all_invoices=[InvoiceRef(invoice_id=c.invoice_id, invoice_number=c.invoice_number) for c in cands] + (extra_invoices or []),
        contactable=[ContactFact(contact_id=C_EMAIL, channel=Channel.EMAIL), ContactFact(contact_id=C_SMS, channel=Channel.SMS)] if contactable is None else contactable,
        contacts_7d=contacts_7d, contacts_invoice_7d=contacts_invoice_7d or {}, last_contact_at=last_contact_at,
        active_payment_links=links or {}, paid_claims=paid_claims or [], applied_payments=applied or [],
        template_catalogue=TEMPLATES if templates is None else templates,
    )


def snap(f: AccountFacts | None = None, target: UUID = INV1, **overrides: Any) -> AccountSnapshot:
    """Snapshot from facts, optionally overriding post-target fields (e.g. active_ptp, open_dispute_ids)."""
    f = f or facts()
    s = build_snapshot(f, target, RULESET)
    if not overrides:
        return s
    data = s.model_dump(exclude={"snapshot_hash"})
    data.update(overrides)
    return AccountSnapshot.build(**data)


def ctx(*, arm: Arm = Arm.RULES_ONLY, level: DegradationLevel = DegradationLevel.L1, proposal_id: UUID | None = None,
        validation_id: UUID | None = None, rejected_ambiguous: bool = False, business_date: date = BDATE) -> DecisionContext:
    return DecisionContext(trace_id=new_id(), arm=arm, degradation_level=level, proposal_id=proposal_id, validation_id=validation_id,
                           business_date=business_date, rejected_ambiguous=rejected_ambiguous, action_id=new_id())


def choice(action: ActionType = ActionType.SEND_REMINDER, *, origin: DegradationLevel = DegradationLevel.L1, confidence: float | None = None,
           contact_id: UUID | None = C_EMAIL, channel: Channel | None = Channel.EMAIL, template_id: str | None = "tpl.reminder.email.v1",
           followup_days: int | None = None) -> ActionChoice:
    if action in (ActionType.SUPPRESS, ActionType.SCHEDULE_FOLLOWUP, ActionType.ESCALATE_TO_HUMAN):
        contact_id, channel, template_id = None, None, None
    return ActionChoice(action=action, contact_id=contact_id, channel=channel, template_id=template_id, followup_days=followup_days,
                        confidence=confidence, origin=origin)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def interp_parsed(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"intent": "WILL_PAY_ON_DATE", "promised_date_raw": None, "promised_amount_raw": None, "invoice_refs": [],
                            "contact_correction": None, "sentiment": "NEUTRAL", "confidence": 0.9, "evidence": []}
    base.update(kw)
    return base


def action_parsed(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"action": "SEND_REMINDER", "contact_id": str(C_EMAIL), "channel": "EMAIL", "template_id": "tpl.reminder.email.v1",
                            "followup_days": None, "rationale": "overdue", "confidence": 0.9}
    base.update(kw)
    return base


def proposal(parsed: dict[str, Any] | None, *, source_text: str = "I will pay by Friday", kind: ProposalKind = ProposalKind.INTERPRETATION,
             schema_version: str | None = None, parse_status: ParseStatus = ParseStatus.OK, invoice_id: UUID | None = None,
             account_id: UUID = ACC, business_date: date = BDATE, input_hash: str | None = None, confidence: float | None = None) -> AgentProposal:
    if schema_version is None:
        schema_version = "interpretation.v1" if kind is ProposalKind.INTERPRETATION else "action_proposal.v1"
    if parse_status is not ParseStatus.OK:
        parsed = None
    conf = confidence if confidence is not None else (parsed.get("confidence") if parsed else None)
    return AgentProposal(
        proposal_id=new_id(), trace_id=new_id(), account_id=account_id, kind=kind, invoice_id=invoice_id, business_date=business_date,
        arm=Arm.TREATMENT, provider="offline-fixture", model_id="fixture", prompt_template_id="fixture.v1", schema_version=schema_version,
        prompt_hash="b" * 64, input_hash=input_hash or sha(source_text), raw_response=RawJson({"fixture": True}), parsed=parsed,
        parse_status=parse_status, confidence=conf, evidence=[], latency_ms=1, created_at=AS_OF,
    )


def vin(p: AgentProposal, f: AccountFacts | None = None, source_text: str = "I will pay by Friday") -> ValidationInput:
    return ValidationInput(proposal=p, source_text=source_text, facts=f or facts())


# ── DB helpers ───────────────────────────────────────────────────────────────────────────
def store_proposal(agent: Connection, p: AgentProposal) -> None:
    agent.execute(text(
        "SELECT baaki_write.record_agent_proposal(:p, :t, :a, CAST(:k AS baaki.proposal_kind), :inv, :d, :prov, :model, :ptid, :sv, :ph, :ih, "
        "CAST(:raw AS jsonb), CAST(:parsed AS jsonb), CAST(:ps AS baaki.parse_status), CAST(:conf AS numeric), CAST(:ev AS jsonb), :lat)"),
        {"p": p.proposal_id, "t": p.trace_id, "a": p.account_id, "k": str(p.kind), "inv": p.invoice_id, "d": p.business_date, "prov": p.provider,
         "model": p.model_id, "ptid": p.prompt_template_id, "sv": p.schema_version, "ph": p.prompt_hash, "ih": p.input_hash,
         "raw": json.dumps(p.raw_response.unwrap_for_audit()), "parsed": json.dumps(p.parsed) if p.parsed is not None else None,
         "ps": str(p.parse_status), "conf": p.confidence, "ev": json.dumps(p.evidence), "lat": p.latency_ms})
    agent.commit()


def issue_due(app: Connection, ids: dict[str, UUID], *, amount: int, due: date, number: str | None = None) -> UUID:
    inv = new_id()
    app.execute(text("SELECT baaki_write.issue_invoice(:i, :o, :a, :n, :amt, :d1, :d2, :t)"),
                {"i": inv, "o": ids["org"], "a": ids["account"], "n": number or f"INV-{inv}", "amt": amount,
                 "d1": due - timedelta(days=15), "d2": due, "t": new_id()})
    app.commit()
    return inv


def add_contact(owner: Connection, account_id: UUID, channel: str, seed: str) -> UUID:
    cid = new_id()
    owner.execute(text("INSERT INTO baaki.contact (contact_id, account_id, channel, address_hash, address_redacted) VALUES (:c, :a, CAST(:ch AS baaki.channel), :h, :r)"),
                  {"c": cid, "a": account_id, "ch": channel, "h": hashlib.sha256(seed.encode()).hexdigest(), "r": "x***"})
    owner.commit()
    return cid


def workday_as_of() -> datetime:
    """A Mon–Sat 11:00 IST instant that is >= the wall clock (so DB-created rows never post-date it)."""
    now_local = datetime.now(IST)
    cand_dt = datetime.combine(now_local.date(), time(11, 0), tzinfo=IST)
    if cand_dt < now_local:
        cand_dt += timedelta(days=1)
    while cand_dt.weekday() == 6:
        cand_dt += timedelta(days=1)
    return cand_dt.astimezone(UTC)


@contextmanager
def refused(code: str):
    """Python-wrapper counterpart of tests.helpers.raises_writer: the writer refused with `code`."""
    with pytest.raises(WriterRefused) as ei:
        yield
    assert ei.value.code == code, f"expected {code!r}, got {ei.value.code!r}"


@contextmanager
def unauthorized():
    """Grant denial (InsufficientPrivilege) or an H17 invoker check, both surfaced as UnauthorizedInvoker."""
    with pytest.raises(UnauthorizedInvoker):
        yield
