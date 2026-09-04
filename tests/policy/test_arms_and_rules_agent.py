"""Arm strategies (CONTROL/RULES_ONLY/TREATMENT boundary), SC3/SC7, §5.6 paid-claim, restriction detector, interpreter."""
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from baaki.contracts.account_snapshot import ActivePaymentLink
from baaki.contracts.candidate import AppliedPaymentFact, ContactFact, PaidClaimFact
from baaki.contracts.normalized_action import NormalizedActionProposal
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.domain.enums import ActionType, Channel, DegradationLevel
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from baaki.domain.money import paise
from baaki.policy.arms import control, rules_only, treatment
from baaki.policy.kernel.target import select_target
from baaki.policy.snapshot import build_snapshot, paid_claim_until
from baaki.rules_agent.interpreter import classify_intent, interpret
from baaki.rules_agent.restriction import MATCHER_VERSION, PATTERNS, detect
from tests.phase2_helpers import AS_OF, BDATE, C_EMAIL, C_SMS, INV1, INV2, RULESET, cand, facts


def interp(intent):
    return NormalizedInterpretation(intent=intent, effective_confidence=1.0)


# ── CONTROL ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("days,expect", [(3, ActionType.SEND_REMINDER), (7, ActionType.SEND_REMINDER), (15, ActionType.SEND_REMINDER),
                                         (0, ActionType.SUPPRESS), (4, ActionType.SUPPRESS), (16, ActionType.SUPPRESS)])
def test_control_static_cadence(days, expect):
    c = control.choose(facts(candidates=[cand(days_overdue=days)]), cand(days_overdue=days), RULESET)
    assert c.action is expect and c.origin is DegradationLevel.L2 and c.confidence is None
    if expect is ActionType.SEND_REMINDER:
        assert (c.contact_id, c.channel, c.template_id) == (C_EMAIL, Channel.EMAIL, "tpl.reminder.email.v1")


def test_control_without_contactable_contact_suppresses():
    f = facts(contactable=[])
    assert control.choose(f, cand(days_overdue=7), RULESET).action is ActionType.SUPPRESS


# ── RULES_ONLY tree ─────────────────────────────────────────────────────────────────────
def test_rules_tree_thresholds():
    f = facts()
    assert rules_only.choose(f, cand(days_overdue=2), RULESET).action is ActionType.SUPPRESS
    c = rules_only.choose(f, cand(days_overdue=3), RULESET)
    assert c.action is ActionType.SEND_REMINDER and c.origin is DegradationLevel.L1
    c = rules_only.choose(f, cand(days_overdue=15), RULESET)
    assert c.action is ActionType.SEND_PAYMENT_LINK and c.template_id == "tpl.link.email.v1"


def test_rules_tree_reminder_reuses_active_link_instead_of_new_link():
    link = ActivePaymentLink(link_id="plink_1", created_at=AS_OF - timedelta(hours=1), amount_paise=paise(1))
    f = facts(links={str(INV1): link})
    c = rules_only.choose(f, cand(days_overdue=20), RULESET)
    assert c.action is ActionType.SEND_REMINDER and c.existing_link_ref == "plink_1"


@pytest.mark.parametrize("intent,expect", [
    ("REQUEST_INSTALLMENTS", ActionType.PROPOSE_INSTALLMENT_PLAN), ("NEEDS_DOCUMENT", ActionType.ESCALATE_TO_HUMAN),
    ("WRONG_CONTACT", ActionType.ESCALATE_TO_HUMAN), ("DISPUTE_AMOUNT", ActionType.REQUEST_DISPUTE_DETAILS),
    ("DISPUTE_DELIVERY", ActionType.REQUEST_DISPUTE_DETAILS), ("WILL_PAY_ON_DATE", ActionType.SUPPRESS),
    ("ALREADY_PAID_CLAIM", ActionType.SUPPRESS), ("UNSUBSCRIBE", ActionType.SUPPRESS), ("NO_CLEAR_INTENT", ActionType.SEND_PAYMENT_LINK),
])
def test_rules_tree_intents(intent, expect):
    c = rules_only.choose(facts(), cand(days_overdue=15), RULESET, interp(intent))
    assert c.action is expect


def test_rules_tree_channel_selection_falls_back_to_sms_contact():
    f = facts(contactable=[ContactFact(contact_id=C_SMS, channel=Channel.SMS)])
    c = rules_only.choose(f, cand(days_overdue=3), RULESET)
    assert (c.channel, c.template_id) == (Channel.SMS, "tpl.reminder.sms.v1")
    c = rules_only.choose(f, cand(days_overdue=15), RULESET)  # no SMS link template → suppress (never invent a channel)
    assert c.action is ActionType.SUPPRESS


# ── TREATMENT boundary ──────────────────────────────────────────────────────────────────
def test_treatment_consumes_validated_proposal_only_and_discards_band_d():
    n = NormalizedActionProposal(action=ActionType.SEND_REMINDER, contact_id=C_EMAIL, channel=Channel.EMAIL, template_id="tpl.reminder.email.v1", effective_confidence=0.9)
    c = treatment.choose(n, RULESET)
    assert c is not None and c.origin is DegradationLevel.L0 and c.confidence == 0.9
    assert treatment.choose(n.model_copy(update={"effective_confidence": 0.49}), RULESET) is None
    assert treatment.choose(n.model_copy(update={"effective_confidence": 0.50}), RULESET) is not None


# ── SC3 / SC7 / snapshot ────────────────────────────────────────────────────────────────
def test_select_target_rules():
    assert select_target([INV1, INV2], [INV2], INV1) == INV2
    assert select_target([INV1, INV2], [INV1, INV2], None) == INV1     # two refs → not "sole" → first candidate
    assert select_target([INV1, INV2], [], INV2) == INV2
    assert select_target([INV1, INV2], [new_id()], None) == INV1        # resolved but not a candidate (e.g. PAID) → fallback
    assert select_target([], [INV1], INV1) is None                      # SC7


def test_build_snapshot_rejects_non_candidate_target_sc4():
    with pytest.raises(ContractViolation):
        build_snapshot(facts(), INV2, RULESET)


def test_snapshot_fields_derive_from_facts_and_p2_nulls():
    f = facts(contacts_7d=2, contacts_invoice_7d={str(INV1): 1})
    s = build_snapshot(f, INV1, RULESET)
    assert int(s.outstanding_paise) == 450_000 and s.days_overdue == 15 and s.contacts_7d == 2 and s.contacts_invoice_7d == 1
    assert s.open_dispute_ids == [] and s.active_ptp is None and s.active_payment_link is None
    assert s.contactable_contact_ids == [C_EMAIL, C_SMS] and s.business_date == BDATE
    assert build_snapshot(f, INV1, RULESET).snapshot_hash == s.snapshot_hash


def test_paid_claim_derivation_5_6():
    vid = new_id()
    claim = PaidClaimFact(validation_id=vid, claim_at=AS_OF - timedelta(hours=10), invoice_ids=[])
    assert paid_claim_until(facts(paid_claims=[claim]), INV1, RULESET) == AS_OF + timedelta(hours=62)
    # cleared by a payment applied strictly after the claim (account scope)
    f = facts(paid_claims=[claim], applied=[AppliedPaymentFact(invoice_id=INV1, applied_at=AS_OF - timedelta(hours=5))])
    assert paid_claim_until(f, INV1, RULESET) is None
    # payment before the claim does not clear it
    f = facts(paid_claims=[claim], applied=[AppliedPaymentFact(invoice_id=INV1, applied_at=AS_OF - timedelta(hours=11))])
    assert paid_claim_until(f, INV1, RULESET) is not None
    # invoice-scoped claim on another invoice does not touch this target
    other = PaidClaimFact(validation_id=new_id(), claim_at=AS_OF - timedelta(hours=1), invoice_ids=[INV2])
    assert paid_claim_until(facts(paid_claims=[other]), INV1, RULESET) is None
    # expired
    old = PaidClaimFact(validation_id=new_id(), claim_at=AS_OF - timedelta(hours=73), invoice_ids=[])
    assert paid_claim_until(facts(paid_claims=[old]), INV1, RULESET) is None


# ── restriction detector (§6.18.1) ──────────────────────────────────────────────────────
@pytest.mark.parametrize("text,pid", [
    ("STOP", "STOP"), ("please stop messaging", "STOP"), ("Unsubscribe me now", "UNSUBSCRIBE"), ("do not contact me again", "DO_NOT_CONTACT"),
    ("don't call me", "DONT_CONTACT"), ("remove me from this list", "REMOVE_ME"), ("I opt out", "OPT_OUT"), ("no more reminders", "NO_MORE_MESSAGES"),
    ("msg mat karo", "HI_MAT_KARO"), ("band karo ye sab", "HI_BAND_KARO"), ("pareshan mat karo", "HI_MAT_KARO"),
])
def test_restriction_matches(text, pid):
    m = detect(text)
    assert m is not None and m.matched_pattern_id == pid and m.matcher_version == MATCHER_VERSION and m.span.lower() in text.lower()


@pytest.mark.parametrize("text", ["I will pay on Friday", "the bus stopped", "stopwatch", "unstoppable", "please contact accounts", "kal kar denge"])
def test_restriction_non_matches(text):
    assert detect(text) is None


def test_restriction_patterns_closed_and_ordered():
    ids = [p for p, _ in PATTERNS]
    assert len(ids) == len(set(ids)) == 10 and MATCHER_VERSION == "restriction.v1"


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=60))
def test_detector_total_and_deterministic(t):
    assert detect(t) == detect(t)


# ── keyword interpreter ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,intent", [
    ("already paid last week", "ALREADY_PAID_CLAIM"), ("payment done", "ALREADY_PAID_CLAIM"), ("wrong amount billed", "DISPUTE_AMOUNT"),
    ("goods not received", "DISPUTE_DELIVERY"), ("can we do installments", "REQUEST_INSTALLMENTS"), ("wrong number", "WRONG_CONTACT"),
    ("send the invoice copy", "NEEDS_DOCUMENT"), ("will pay by Friday", "WILL_PAY_ON_DATE"), ("STOP", "UNSUBSCRIBE"), ("ok", "NO_CLEAR_INTENT"),
])
def test_classify_intent(text, intent):
    assert classify_intent(text) == intent


def test_interpret_extracts_single_unambiguous_spans_only():
    n = interpret("will pay 15000 by Friday", BDATE)
    assert n.intent == "WILL_PAY_ON_DATE" and n.promised_date == BDATE + timedelta(days=3) and int(n.promised_paise) == 1_500_000
    n = interpret("will pay 4000 or 5000 on Friday or Monday", BDATE)
    assert n.promised_date is None and n.promised_paise is None  # two spans → no guess
    n = interpret("will pay next week", BDATE)
    assert n.promised_date is None
