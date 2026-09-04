"""kernel.decide — the deterministic P0–P14 ladder with the §4.3 tier-cap truth table.

Pure: no I/O, no clock reads (all times come from the snapshot), no randomness, no model access.
Money enters only as `snapshot.outstanding_paise` (I2, S2). Decisions are constructed with KERNEL_TOKEN.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Final
from uuid import UUID

from baaki.contracts.account_snapshot import AccountSnapshot, TemplateCatalogueEntry
from baaki.contracts.action_choice import ActionChoice, DecisionContext
from baaki.contracts.canonical_payload import (
    EscalateToHumanPayload,
    InstallmentPart,
    LinkNotes,
    ProposeInstallmentPlanPayload,
    RequestDisputeDetailsPayload,
    ScheduleFollowupPayload,
    SendPaymentLinkPayload,
    SendReminderPayload,
    SuppressPayload,
    TemplateId,
)
from baaki.contracts.policy_decision import KERNEL_TOKEN, ExecutableDecision, NonExecutableDecision
from baaki.domain.enums import (
    ACTION_TIER,
    ActionType,
    DegradationLevel,
    EscalationReason,
    SuppressReason,
    TemplatePurpose,
    Verdict,
    queue_for_reason,
)
from baaki.domain.errors import ContractViolation
from baaki.domain.money import Paise
from baaki.policy.kernel.quiet_hours import in_window, next_window_open
from baaki.policy.ruleset import Ruleset

KERNEL_VERSION: Final[str] = "kernel.v1"
PRESSURE: Final[frozenset[ActionType]] = frozenset(
    {ActionType.SEND_REMINDER, ActionType.SEND_PAYMENT_LINK, ActionType.PROPOSE_INSTALLMENT_PLAN}
)
OUTBOUND: Final[frozenset[ActionType]] = PRESSURE | {ActionType.REQUEST_DISPUTE_DETAILS, ActionType.ESCALATE_TO_HUMAN}
NEEDS_TEMPLATE: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.REQUEST_DISPUTE_DETAILS,
        ActionType.SEND_REMINDER,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.PROPOSE_INSTALLMENT_PLAN,
    }
)
INSTALLMENT_PARTS: Final[int] = 3


def catalogue_tier(action: ActionType) -> int:
    return ACTION_TIER[action]


def authority_tier(action: ActionType, confidence: float | None, ruleset: Ruleset) -> int:
    """I4: min(catalogue_tier, tier_cap(confidence)); no confidence (L1/L2) ⟹ catalogue tier."""
    ct = catalogue_tier(action)
    if confidence is None:
        return ct
    band = ruleset.tier_cap.band_for(confidence)
    if band.cap is None:
        return -1  # discard
    return min(ct, band.cap)


class _Ladder:
    """Evaluates P0–P12 for a given action against the snapshot. Records every level evaluated."""

    def __init__(self, snap: AccountSnapshot, ruleset: Ruleset, tz: str) -> None:
        self.s, self.r, self.tz = snap, ruleset, tz
        self.matched: list[str] = []
        s = snap
        self.p5 = s.invoice_state.value == "DISPUTED" or bool(s.open_dispute_ids)
        self.p6 = s.unverified_paid_claim_until is not None and s.as_of < s.unverified_paid_claim_until
        self.p7 = s.active_ptp is not None and s.as_of < datetime.combine(
            s.active_ptp.due_date, time.min, tzinfo=s.as_of.tzinfo
        ) + timedelta(days=ruleset.ptp_grace_business_days)
        self.p8 = s.active_payment_link is not None and s.active_payment_link.created_at > s.as_of - timedelta(
            hours=ruleset.link_active_window_hours
        )
        self.p9 = (
            s.contacts_7d >= ruleset.contact_cap_account_7d or s.contacts_invoice_7d >= ruleset.contact_cap_invoice_7d
        )
        self.p10 = not in_window(s.as_of, tz, ruleset.quiet_hours)

    def suppress_reason(self) -> SuppressReason:
        if self.p5:
            return SuppressReason.DISPUTE_OPEN
        if self.p6:
            return SuppressReason.PAID_CLAIM_PENDING
        if self.p7:
            return SuppressReason.PTP_ACTIVE
        if self.p9:
            return SuppressReason.FREQUENCY_CAP
        return SuppressReason.NO_ELIGIBLE_ACTION

    def escalation_reason(self, rejected_ambiguous: bool) -> EscalationReason:
        if self.p5:
            return EscalationReason.DISPUTE_UNRESOLVED
        if self.p6:
            return EscalationReason.PAID_CLAIM_UNVERIFIED
        if rejected_ambiguous:
            return EscalationReason.AMBIGUOUS_INTERPRETATION
        return EscalationReason.MANUAL_REVIEW

    def run(self, choice: ActionChoice) -> tuple[str, str] | None:
        """Returns (rule_id, reason_code) of the first blocking/deferring level, else None. Records all levels."""
        s, a = self.s, choice.action

        def hit(rule: str, reason: str) -> tuple[str, str]:
            return (rule, reason)

        self.matched.append("P0")
        if s.kill_switch:
            return hit("P0", "kill_switch")
        self.matched.append("P1")
        if not s.ledger_invariant_ok:
            return hit("P1", "ledger_invariant_breach")
        self.matched.append("P2")
        if s.opt_out or (choice.contact_id is not None and choice.contact_id not in s.contactable_contact_ids):
            return hit("P2", "opt_out")
        self.matched.append("P3")
        if s.invoice_state.value == "PAID":
            return hit("P3", "invoice_paid")
        self.matched.append("P4")
        if int(s.outstanding_paise) == 0:
            return hit("P4", "zero_outstanding")
        self.matched.append("P5")
        if self.p5 and a not in (ActionType.REQUEST_DISPUTE_DETAILS, ActionType.ESCALATE_TO_HUMAN, ActionType.SUPPRESS):
            return hit("P5", "dispute_open")
        self.matched.append("P6")
        if self.p6 and a not in (ActionType.SUPPRESS, ActionType.ESCALATE_TO_HUMAN):
            return hit("P6", "paid_claim_pending")
        self.matched.append("P7")
        if self.p7 and a in PRESSURE and not self._is_t2_nudge(choice):
            return hit("P7", "ptp_active")
        self.matched.append("P8")
        if self.p8 and a is ActionType.SEND_PAYMENT_LINK:
            return hit("P8", "payment_link_active")
        self.matched.append("P9")
        if self.p9 and a in OUTBOUND:
            return hit("P9", "frequency_cap")
        self.matched.append("P10")
        if self.p10 and a in OUTBOUND:
            return hit("P10", "quiet_hours")
        self.matched.append("P11")
        if a in NEEDS_TEMPLATE and not self._template_ok(choice):
            return hit("P11", "template.incompatible")
        return None

    def _is_t2_nudge(self, choice: ActionChoice) -> bool:
        s = self.s
        if choice.action is not ActionType.SEND_REMINDER or s.active_ptp is None:
            return False
        if (s.active_ptp.due_date - s.business_date).days != self.r.ptp_nudge_days_before_due:
            return False
        tpl = self._template(choice.template_id)
        return tpl is not None and tpl.purpose is TemplatePurpose.COURTESY_NUDGE

    def _template(self, template_id: str | None) -> TemplateCatalogueEntry | None:
        for t in self.s.template_catalogue:
            if t.template_id == template_id:
                return t
        return None

    def _template_ok(self, choice: ActionChoice) -> bool:
        tpl = self._template(choice.template_id)
        return (
            tpl is not None
            and tpl.active
            and choice.channel is not None
            and tpl.channel is choice.channel
            and tpl.action_type is choice.action
        )


def _base(
    ctx: DecisionContext,
    snap: AccountSnapshot,
    ruleset: Ruleset,
    tier: int,
    matched: list[str],
    effective_confidence: float | None,
) -> dict[str, Any]:
    return dict(
        decision_id=_decision_id_for(ctx),
        trace_id=ctx.trace_id,
        proposal_id=ctx.proposal_id,
        validation_id=ctx.validation_id,
        arm=ctx.arm,
        account_id=snap.account_id,
        invoice_id=snap.target_invoice_id,
        business_date=ctx.business_date,
        tier=tier,
        matched_rules=list(matched),
        effective_confidence=effective_confidence,
        policy_version=ruleset.policy_version,
        kernel_version=KERNEL_VERSION,
        policy_hash=ruleset.policy_hash,
        snapshot_hash=snap.snapshot_hash,
        degradation_level=ctx.degradation_level,
        decided_at=snap.as_of,
    )


def _decision_id_for(ctx: DecisionContext) -> UUID:
    # Deterministic derivation from the pre-generated action_id keeps the kernel free of id generation (P6).
    return UUID(int=ctx.action_id.int ^ 0x5A5A5A5A5A5A5A5A5A5A5A5A5A5A5A5A)


def decide(
    choice: ActionChoice, snapshot: AccountSnapshot, ruleset: Ruleset, ctx: DecisionContext, *, org_timezone: str
) -> ExecutableDecision | NonExecutableDecision:
    """P0–P14 with the §4.3 truth table. Returns a decision constructed with KERNEL_TOKEN."""
    if choice.origin is DegradationLevel.L0 and choice.confidence is None:
        raise ContractViolation("L0 choice requires effective_confidence")
    if choice.origin is not DegradationLevel.L0 and choice.confidence is not None:
        raise ContractViolation("only L0 choices carry confidence")
    if choice.origin is DegradationLevel.L0 and ruleset.tier_cap.band_for(float(choice.confidence)).cap is None:  # type: ignore[arg-type]
        raise ContractViolation("band D choice must be discarded by the pipeline before decide() (§4.3)")
    ladder = _Ladder(snapshot, ruleset, org_timezone)

    # P13 truth table — applied before the ladder so the ladder runs on the final action (§4.3).
    requested = choice.action
    final = choice
    force_approval = False
    auth = authority_tier(requested, choice.confidence, ruleset)
    if choice.origin is DegradationLevel.L0:
        band = ruleset.tier_cap.band_for(float(choice.confidence))  # type: ignore[arg-type]
        if catalogue_tier(requested) >= 1 and auth < catalogue_tier(requested) and (band.cap or 0) == 0:
            final = ActionChoice(action=ActionType.SUPPRESS, origin=choice.origin, confidence=choice.confidence)
        elif requested in band.force_approval:
            force_approval = True
    assert authority_tier(final.action, final.confidence, ruleset) <= catalogue_tier(requested), "I4 violated"

    block = ladder.run(final)
    if block is not None:
        rule, reason = block
        if rule == "P10":
            defer_until = next_window_open(snapshot.as_of, org_timezone, ruleset.quiet_hours)
            return _non_executable(
                dict(
                    _base(ctx, snapshot, ruleset, 0, ladder.matched, final.confidence),
                    verdict=Verdict.DEFER,
                    defer_until=defer_until,
                )
            )
        return _non_executable(
            dict(
                _base(ctx, snapshot, ruleset, 0, ladder.matched, final.confidence),
                verdict=Verdict.BLOCK,
                blocking_rules=[{"rule_id": rule, "reason_code": reason, "detail": f"action={final.action}"}],
            )
        )

    ladder.matched.append("P12")
    tier = catalogue_tier(final.action)
    verdict = Verdict.REQUIRE_APPROVAL if tier == 2 else Verdict.ALLOW
    ladder.matched.append("P13")
    if force_approval:
        verdict, tier = Verdict.REQUIRE_APPROVAL, 2
    ladder.matched.append("P14")
    payload = _payload(final, snapshot, ruleset, ctx, ladder)
    return _executable(
        dict(
            _base(ctx, snapshot, ruleset, tier, ladder.matched, final.confidence),
            verdict=verdict,
            action_type=final.action,
            canonical_payload=payload,
        )
    )


def _non_executable(data: dict[str, Any]) -> NonExecutableDecision:
    return NonExecutableDecision(_token=KERNEL_TOKEN, **data)  # type: ignore[call-arg]


def _executable(data: dict[str, Any]) -> ExecutableDecision:
    return ExecutableDecision(_token=KERNEL_TOKEN, **data)  # type: ignore[call-arg]


def _payload(c: ActionChoice, s: AccountSnapshot, r: Ruleset, ctx: DecisionContext, ladder: _Ladder):  # type: ignore[no-untyped-def]
    a = c.action
    if a is ActionType.SUPPRESS:
        return SuppressPayload(reason_code=ladder.suppress_reason())
    if a is ActionType.SCHEDULE_FOLLOWUP:
        days = c.followup_days if c.followup_days is not None else 3
        return ScheduleFollowupPayload(followup_date=s.business_date + timedelta(days=days))
    if a is ActionType.ESCALATE_TO_HUMAN:
        reason = ladder.escalation_reason(ctx.rejected_ambiguous)
        return EscalateToHumanPayload(reason_code=reason, assignee_queue=queue_for_reason(reason))
    if c.contact_id is None or c.channel is None or c.template_id is None:
        raise ContractViolation(f"{a} requires contact_id, channel and template_id")
    tid = TemplateId(c.template_id)
    if a is ActionType.REQUEST_DISPUTE_DETAILS:
        return RequestDisputeDetailsPayload(contact_id=c.contact_id, channel=c.channel, template_id=tid)
    if a is ActionType.SEND_REMINDER:
        ref = c.existing_link_ref or (s.active_payment_link.link_id if s.active_payment_link is not None else None)
        return SendReminderPayload(contact_id=c.contact_id, channel=c.channel, template_id=tid, existing_link_ref=ref)
    if a is ActionType.SEND_PAYMENT_LINK:
        return SendPaymentLinkPayload(
            amount_paise=Paise(int(s.outstanding_paise)),
            contact_id=c.contact_id,
            channel=c.channel,
            template_id=tid,
            expires_at=s.as_of + timedelta(hours=r.link_active_window_hours),
            notes=LinkNotes(invoice_id=s.target_invoice_id, action_id=ctx.action_id, trace_id=ctx.trace_id),
        )
    if a is ActionType.PROPOSE_INSTALLMENT_PLAN:
        total = int(s.outstanding_paise)
        base, rem = divmod(total, INSTALLMENT_PARTS)
        step = r.ptp_horizon_days // INSTALLMENT_PARTS
        parts = [
            InstallmentPart(
                amount_paise=Paise(base + (rem if i == INSTALLMENT_PARTS - 1 else 0)),
                due_date=s.business_date + timedelta(days=step * (i + 1)),
            )
            for i in range(INSTALLMENT_PARTS)
        ]
        return ProposeInstallmentPlanPayload(parts=parts, contact_id=c.contact_id, channel=c.channel, template_id=tid)
    raise ContractViolation(f"unknown action {a}")  # unreachable: closed enum
