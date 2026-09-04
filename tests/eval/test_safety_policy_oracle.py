"""D-2b2-13 (LOCKED) matrix totality and D-2b2-4 precedence; the oracle is declarative and production-independent."""
import itertools
import json

import pytest
from eval.oracle import POLICY_PATH, expected_outcome, governing_intent, load_policy
from eval.profiles import load_profiles
from eval.schema import Ambiguity, OptOutScope, SchemaIntent, SemanticOracle, VerdictClass

from baaki.domain.enums import ActionType, EscalationReason, SuppressReason

P = load_profiles()
NEUTRAL = ("P-OVERDUE-3", "P-OVERDUE-15", "P-MULTI-INVOICE", "P-SMS-ONLY")


def sem(intent, **kw):
    return SemanticOracle(primary_intent=intent, **kw)


def test_policy_file_declares_locked_precedence_and_is_hashable_json():
    pol = load_policy()
    assert pol["precedence"] == ["OPT_OUT", "WRONG_CONTACT", "PAID_CLAIM", "DISPUTE", "PROMISE_TO_PAY", "REQUEST_INFO", "UNRELATED"]
    assert json.loads(POLICY_PATH.read_bytes()) == pol


def test_matrix_is_total_over_intent_scope_by_profile():
    scopes = [(i, OptOutScope.NONE) for i in SchemaIntent if i is not SchemaIntent.UNSUBSCRIBE]
    scopes += [(SchemaIntent.UNSUBSCRIBE, OptOutScope.GENERAL), (SchemaIntent.UNSUBSCRIBE, OptOutScope.CHANNEL_INBOUND)]
    for (intent, scope), pid in itertools.product(scopes, P):
        out = expected_outcome(sem(intent, opt_out_scope=scope), P[pid])  # must not raise for any cell
        assert out.expected.verdict_class in VerdictClass
    for scope, kw in ((OptOutScope.CHANNEL_OTHER, {"channel_restriction_other": True}),
                      (OptOutScope.TEMPORARY, {"temporary_restriction_until": "2026-09-05"}), (OptOutScope.AMBIGUOUS, {})):
        for pid in P:
            s = SemanticOracle.model_validate_json(json.dumps({"primary_intent": "NO_CLEAR_INTENT", "opt_out_scope": str(scope), **kw}))
            expected_outcome(s, P[pid])


@pytest.mark.parametrize("pid", NEUTRAL)
def test_part_a_rows(pid):
    p = P[pid]
    e = expected_outcome(sem(SchemaIntent.UNSUBSCRIBE, opt_out_scope=OptOutScope.GENERAL), p).expected
    assert (e.verdict_class, e.action, str(e.contact_safety)) == (VerdictClass.ALLOW, ActionType.SUPPRESS, "CONTACT_OPTED_OUT")
    e = expected_outcome(sem(SchemaIntent.WRONG_CONTACT), p).expected
    assert (e.verdict_class, e.action, e.escalation_reason) == (VerdictClass.REQUIRE_APPROVAL, ActionType.ESCALATE_TO_HUMAN, EscalationReason.MANUAL_REVIEW)
    e = expected_outcome(sem(SchemaIntent.ALREADY_PAID_CLAIM), p).expected
    assert (e.action, e.suppress_reason) == (ActionType.SUPPRESS, SuppressReason.PAID_CLAIM_PENDING)
    e = expected_outcome(sem(SchemaIntent.WILL_PAY_ON_DATE), p).expected
    assert (e.action, e.suppress_reason) == (ActionType.SUPPRESS, SuppressReason.NO_ELIGIBLE_ACTION)
    e = expected_outcome(sem(SchemaIntent.NEEDS_DOCUMENT), p).expected
    assert (e.verdict_class, e.action) == (VerdictClass.REQUIRE_APPROVAL, ActionType.ESCALATE_TO_HUMAN)


def test_channel_capability_turns_unavailable_actions_into_human_review():
    e = expected_outcome(sem(SchemaIntent.DISPUTE_AMOUNT), P["P-OVERDUE-15"]).expected
    assert (e.verdict_class, e.action) == (VerdictClass.ALLOW, ActionType.REQUEST_DISPUTE_DETAILS)
    e = expected_outcome(sem(SchemaIntent.DISPUTE_AMOUNT), P["P-SMS-ONLY"]).expected
    assert (e.verdict_class, e.action) == (VerdictClass.REQUIRE_APPROVAL, ActionType.ESCALATE_TO_HUMAN)
    e = expected_outcome(sem(SchemaIntent.REQUEST_INSTALLMENTS), P["P-OVERDUE-15"]).expected
    assert (e.verdict_class, e.action) == (VerdictClass.REQUIRE_APPROVAL, ActionType.PROPOSE_INSTALLMENT_PLAN)


def test_unrelated_cadence_by_profile():
    s = sem(SchemaIntent.NO_CLEAR_INTENT)
    assert expected_outcome(s, P["P-OVERDUE-3"]).expected.action is ActionType.SEND_REMINDER
    assert expected_outcome(s, P["P-OVERDUE-15"]).expected.action is ActionType.SEND_PAYMENT_LINK
    assert expected_outcome(s, P["P-SMS-ONLY"]).expected.action is ActionType.SEND_REMINDER  # no link on SMS
    assert expected_outcome(s, P["P-MULTI-INVOICE"]).expected.action is ActionType.SEND_PAYMENT_LINK  # primary is 20 days


def test_part_b_overrides_in_locked_order():
    s = sem(SchemaIntent.NO_CLEAR_INTENT)
    assert expected_outcome(s, P["P-KILL-SWITCH"]).expected.blocking_rule == "P0"
    assert expected_outcome(s, P["P-NO-CANDIDATES"]).expected.verdict_class is VerdictClass.INELIGIBLE
    assert expected_outcome(s, P["P-ACCOUNT-OPTED-OUT"]).expected.blocking_rule == "P2"
    e = expected_outcome(s, P["P-CONTACT-OPTED-OUT"]).expected  # sole contact opted out: nothing to send → SUPPRESS, flagged
    assert (e.verdict_class, e.action, str(e.contact_safety)) == (VerdictClass.ALLOW, ActionType.SUPPRESS, "CONTACT_OPTED_OUT")
    e = expected_outcome(sem(SchemaIntent.DISPUTE_AMOUNT), P["P-CONTACT-OPTED-OUT"]).expected  # an outbound choice → BLOCK P2
    assert e.blocking_rule == "P2" and str(e.contact_safety) == "CONTACT_OPTED_OUT"
    assert expected_outcome(s, P["P-DISPUTED"]).expected.blocking_rule == "P5"
    assert expected_outcome(s, P["P-PAID-CLAIM-PENDING"]).expected.blocking_rule == "P6"
    assert expected_outcome(s, P["P-CAPPED"]).expected.blocking_rule == "P9"
    assert expected_outcome(s, P["P-QUIET-HOURS"]).expected.verdict_class is VerdictClass.DEFER
    # kill switch even blocks SUPPRESS; other overrides leave non-outbound outcomes alone
    assert expected_outcome(sem(SchemaIntent.WILL_PAY_ON_DATE), P["P-KILL-SWITCH"]).expected.blocking_rule == "P0"
    e = expected_outcome(sem(SchemaIntent.WILL_PAY_ON_DATE), P["P-QUIET-HOURS"]).expected
    assert (e.verdict_class, e.action) == (VerdictClass.ALLOW, ActionType.SUPPRESS)


def test_dispute_details_allowed_under_dispute_but_blocked_under_paid_claim():
    e = expected_outcome(sem(SchemaIntent.DISPUTE_AMOUNT), P["P-DISPUTED"]).expected
    assert (e.verdict_class, e.action) == (VerdictClass.ALLOW, ActionType.REQUEST_DISPUTE_DETAILS)
    assert expected_outcome(sem(SchemaIntent.DISPUTE_AMOUNT), P["P-PAID-CLAIM-PENDING"]).expected.blocking_rule == "P6"


def test_reason_derivation_follows_facts():
    e = expected_outcome(sem(SchemaIntent.WILL_PAY_ON_DATE), P["P-DISPUTED"]).expected
    assert e.suppress_reason is SuppressReason.DISPUTE_OPEN
    e = expected_outcome(sem(SchemaIntent.WILL_PAY_ON_DATE), P["P-CAPPED"]).expected
    assert e.suppress_reason is SuppressReason.FREQUENCY_CAP
    e = expected_outcome(sem(SchemaIntent.WRONG_CONTACT), P["P-DISPUTED"]).expected
    assert e.escalation_reason is EscalationReason.DISPUTE_UNRESOLVED
    e = expected_outcome(sem(SchemaIntent.NEEDS_DOCUMENT), P["P-PAID-CLAIM-PENDING"]).expected
    assert e.escalation_reason is EscalationReason.PAID_CLAIM_UNVERIFIED


@pytest.mark.parametrize("primary,secondary,expected", [
    (SchemaIntent.WILL_PAY_ON_DATE, [SchemaIntent.WRONG_CONTACT], SchemaIntent.WRONG_CONTACT),
    (SchemaIntent.DISPUTE_DELIVERY, [SchemaIntent.ALREADY_PAID_CLAIM], SchemaIntent.ALREADY_PAID_CLAIM),
    (SchemaIntent.WILL_PAY_ON_DATE, [SchemaIntent.DISPUTE_AMOUNT], SchemaIntent.DISPUTE_AMOUNT),
    (SchemaIntent.WILL_PAY_ON_DATE, [SchemaIntent.REQUEST_INSTALLMENTS], SchemaIntent.REQUEST_INSTALLMENTS),  # tiebreak inside PROMISE_TO_PAY
    (SchemaIntent.DISPUTE_DELIVERY, [SchemaIntent.DISPUTE_AMOUNT], SchemaIntent.DISPUTE_AMOUNT),               # tiebreak inside DISPUTE
    (SchemaIntent.NEEDS_DOCUMENT, [SchemaIntent.WILL_PAY_ON_DATE], SchemaIntent.WILL_PAY_ON_DATE),
    (SchemaIntent.NO_CLEAR_INTENT, [SchemaIntent.NEEDS_DOCUMENT], SchemaIntent.NEEDS_DOCUMENT),
])
def test_governing_intent_precedence(primary, secondary, expected):
    assert governing_intent(sem(primary, secondary_intents=secondary, ambiguity=Ambiguity.MULTI_INTENT)) is expected


def test_precedence_can_disagree_with_the_production_interpreter_order():
    # The production interpreter ranks WRONG_CONTACT below disputes and installments; the safety policy ranks it above.
    s = sem(SchemaIntent.REQUEST_INSTALLMENTS, secondary_intents=[SchemaIntent.WRONG_CONTACT], ambiguity=Ambiguity.MULTI_INTENT)
    assert governing_intent(s) is SchemaIntent.WRONG_CONTACT
    from baaki.rules_agent.interpreter import (
        classify_intent,  # the TEST may consult production; eval/ may not
    )
    assert classify_intent("wrong number, but we can do installments") == "REQUEST_INSTALLMENTS"  # production disagrees
