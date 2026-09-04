"""Comparison engine, D-2b2-G2-9 interpretation family, denominators, OPT_OUT scoring, stats (D-2b2-G2-5/6)."""
from datetime import date
from pathlib import Path

import pytest
from eval.compare import build_expected, compare
from eval.loader import load_corpus
from eval.metrics import compute_metrics
from eval.profiles import load_profiles
from eval.records import (
    ActualRecord,
    FaultKind,
    FaultRecord,
    GapMeta,
    InterpretationClass,
    InterpretationStage,
    KernelStage,
    LatencyRecord,
)
from eval.schema import SchemaIntent
from eval.stats import metric, rule_of_three_lower_bound, wilson

from baaki.domain.enums import ActionType, Arm, Channel, DegradationLevel

ROOT = Path(__file__).resolve().parents[2]
ITEMS = {i.id: i for i in load_corpus(ROOT / "eval" / "corpus" / "train.v1.jsonl")}
P = load_profiles()


def actual(item_id, intent, *, detector=None, kernel=None, fault=None, arm=Arm.RULES_ONLY, date_value=None, amount=None):
    return ActualRecord(item_id=item_id, sut_id="rules.v1", sut_version="0" * 64, arm=arm,
                        interpretation=None if intent == "FAULT" else InterpretationStage(intent=SchemaIntent(intent), detector_pattern=detector, date_value=date_value, amount_paise=amount),
                        kernel=kernel, final_effect=None, fault=fault, latency=LatencyRecord(total_ns=1))


def suppress(reason="NO_ELIGIBLE_ACTION"):
    return KernelStage(verdict="ALLOW", action=ActionType.SUPPRESS, tier=0, suppress_reason=reason, degradation_level=DegradationLevel.L1)


def cmp(item_id, act):
    it = ITEMS[item_id]
    return compare(build_expected(it, {}), act, P[it.profile])


def test_interpretation_classes_cover_the_locked_family():
    # C-000001 WILL_PAY_ON_DATE (substantive); C-000033 NO_CLEAR_INTENT
    assert cmp("C-000001", actual("C-000001", "WILL_PAY_ON_DATE")).interpretation_class is InterpretationClass.CORRECT_SUBSTANTIVE
    assert cmp("C-000001", actual("C-000001", "DISPUTE_AMOUNT")).interpretation_class is InterpretationClass.FALSE_SUBSTANTIVE
    assert cmp("C-000001", actual("C-000001", "NO_CLEAR_INTENT")).interpretation_class is InterpretationClass.MISSED
    assert cmp("C-000033", actual("C-000033", "NO_CLEAR_INTENT")).interpretation_class is InterpretationClass.CORRECT_ABSTENTION
    assert cmp("C-000033", actual("C-000033", "WILL_PAY_ON_DATE")).interpretation_class is InterpretationClass.FALSE_POSITIVE
    f = FaultRecord(stage="interpretation", kind=FaultKind.SUT_EXCEPTION, detail_class="ValueError")
    assert cmp("C-000001", actual("C-000001", "FAULT", fault=f)).interpretation_class is InterpretationClass.FAULT
    assert cmp("C-000024", actual("C-000024", "NO_CLEAR_INTENT")).interpretation_class is InterpretationClass.GAP  # CHANNEL_OTHER


def test_metric_denominators_and_partition_sum_to_one():
    rows = [cmp("C-000001", actual("C-000001", "WILL_PAY_ON_DATE")), cmp("C-000001", actual("C-000001", "DISPUTE_AMOUNT")),
            cmp("C-000001", actual("C-000001", "NO_CLEAR_INTENT")), cmp("C-000001", actual("C-000001", "FAULT", fault=FaultRecord(stage="interpretation", kind=FaultKind.SUT_EXCEPTION, detail_class="X"))),
            cmp("C-000033", actual("C-000033", "NO_CLEAR_INTENT")), cmp("C-000033", actual("C-000033", "WILL_PAY_ON_DATE")),
            cmp("C-000024", actual("C-000024", "NO_CLEAR_INTENT"))]
    ai = {(r.item_id, str(r.arm)): None for r in rows}
    m = compute_metrics(rows, ITEMS, ai)["metrics"]
    sub = ("correct_substantive_rate", "false_substantive_interpretation_rate", "missed_interpretation_rate", "fault_share_sub")
    assert all(m[k].denominator == 4 for k in sub) and sum(m[k].numerator for k in sub) == 4
    nci = ("correct_abstention_rate", "false_positive_interpretation_rate", "fault_share_nci")
    assert all(m[k].denominator == 2 for k in nci) and sum(m[k].numerator for k in nci) == 2
    assert m["intent_accuracy_9"].denominator == 6  # GAP item excluded
    assert m["policy_gap_count"].numerator == 1 and m["gap_unmeasured_count"].numerator == 1
    assert m["fault_rate"].numerator == 1 and m["fault_rate"].denominator == 7


def test_optout_scoring_by_scope():
    # C-000019 GENERAL positive, C-000023 CHANNEL_INBOUND positive, C-000027 TEMPORARY, C-000031 negated NONE, C-000032 AMBIGUOUS
    rows = [cmp("C-000019", actual("C-000019", "UNSUBSCRIBE", detector="STOP")), cmp("C-000023", actual("C-000023", "NO_CLEAR_INTENT", detector=None)),
            cmp("C-000027", actual("C-000027", "UNSUBSCRIBE", detector="STOP")), cmp("C-000031", actual("C-000031", "NO_CLEAR_INTENT", detector="STOP")),
            cmp("C-000032", actual("C-000032", "NO_CLEAR_INTENT", kernel=KernelStage(verdict="REQUIRE_APPROVAL", action=ActionType.ESCALATE_TO_HUMAN, tier=2, escalation_reason="MANUAL_REVIEW", degradation_level=DegradationLevel.L1)))]
    m = compute_metrics(rows, ITEMS, {(r.item_id, str(r.arm)): None for r in rows})["metrics"]
    assert (m["optout_recall_interpreter"].numerator, m["optout_recall_interpreter"].denominator) == (1, 2)
    assert (m["optout_recall_detector"].numerator, m["optout_recall_detector"].denominator) == (1, 2)
    assert (m["optout_false_positive_rate_detector"].numerator, m["optout_false_positive_rate_detector"].denominator) == (2, 2)  # TEMPORARY + negated
    assert (m["optout_false_positive_rate_interpreter"].numerator) == 1
    assert (m["ambiguous_conservative_review_rate"].numerator, m["ambiguous_conservative_review_rate"].denominator) == (1, 1)
    assert m["ambiguous_treated_as_optout_rate"].numerator == 0
    r = [x for x in rows if x.item_id == "C-000032"][0]
    assert r.optout_bucket == "AMBIGUOUS_REVIEW" and r.intent_match_9 is True  # AMBIGUOUS gets no recall credit, but is scored for intent


def test_gap_exposure_is_not_measurable_without_measurable_sidecar():
    it = ITEMS["C-000024"]
    e = build_expected(it, {"C-000024": GapMeta(item_id="C-000024", gap_id="GAP-2b2-1", inbound_channel=Channel.EMAIL, restricted_channel=None, measurable=False)})
    c = compare(e, actual("C-000024", "NO_CLEAR_INTENT", kernel=KernelStage(verdict="ALLOW", action=ActionType.SEND_PAYMENT_LINK, tier=1, degradation_level=DegradationLevel.L1, out_channel=Channel.EMAIL, out_contact_ok=True, amount_paise=450000)), P[it.profile])
    assert c.gap_exposure == "NOT_MEASURABLE" and c.optout_bucket == "GAP"
    e2 = build_expected(it, {"C-000024": GapMeta(item_id="C-000024", gap_id="GAP-2b2-1", inbound_channel=Channel.EMAIL, restricted_channel=Channel.SMS, measurable=True)})
    c2 = compare(e2, actual("C-000024", "NO_CLEAR_INTENT", kernel=KernelStage(verdict="ALLOW", action=ActionType.SEND_REMINDER, tier=1, degradation_level=DegradationLevel.L1, out_channel=Channel.SMS, out_contact_ok=True)), P[it.profile])
    assert c2.gap_exposure is True
    assert build_expected(ITEMS["C-000001"], {"C-000001": e2.gap}).gap is None  # sidecar only attaches to CHANNEL_OTHER items


def test_policy_violation_reasons_follow_d_2b2_16():
    it = ITEMS["C-000001"]
    bad = KernelStage(verdict="ALLOW", action=ActionType.PROPOSE_INSTALLMENT_PLAN, tier=2, degradation_level=DegradationLevel.L0, out_channel=Channel.EMAIL, out_contact_ok=False, amount_paise=1, target_is_candidate=False)
    c = compare(build_expected(it, {}), actual("C-000001", "WILL_PAY_ON_DATE", kernel=bad), P[it.profile])
    assert c.policy_violation and set(c.policy_violation_reasons) == {"allow_for_tier2_action", "payload_amount_ne_ledger_outstanding", "contact_outside_contactable_set", "decision_for_non_candidate_invoice"}
    good = KernelStage(verdict="ALLOW", action=ActionType.SEND_PAYMENT_LINK, tier=1, degradation_level=DegradationLevel.L1, out_channel=Channel.EMAIL, out_contact_ok=True, amount_paise=450000)
    assert not compare(build_expected(it, {}), actual("C-000001", "WILL_PAY_ON_DATE", kernel=good), P[it.profile]).policy_violation


def test_ptp_comparison_and_false_extraction():
    it = ITEMS["C-000001"]  # Friday → 2026-09-04
    c = compare(build_expected(it, {}), actual("C-000001", "WILL_PAY_ON_DATE", date_value=date(2026, 9, 4)), P[it.profile])
    assert c.ptp.date_match is True and c.ptp.date_abstain_match is True
    it7 = ITEMS["C-000007"]  # "next week" abstain, "half" abstain
    c7 = compare(build_expected(it7, {}), actual("C-000007", "ALREADY_PAID_CLAIM", date_value=date(2026, 9, 8), amount=100), P[it7.profile])
    assert c7.ptp.false_extraction_date is True and c7.ptp.false_extraction_amount is True and c7.ptp.date_abstain_match is False


def test_expected_and_actual_are_never_mutated_by_compare():
    it = ITEMS["C-000001"]
    e = build_expected(it, {}); a = actual("C-000001", "DISPUTE_AMOUNT")
    e_dump, a_dump = e.model_dump(), a.model_dump()
    c = compare(e, a, P[it.profile])
    assert e.model_dump() == e_dump and a.model_dump() == a_dump and c.intent_match_9 is False


def test_wilson_and_rule_of_three():
    lo, hi = wilson(100, 100)
    assert 0.96 < lo < 0.97 and hi == 1.0
    assert rule_of_three_lower_bound(100) == 0.97 and rule_of_three_lower_bound(12) == pytest.approx(0.75)
    m = metric(100, 100, zero_miss_bound=True)
    assert m.rate == 1.0 and m.rule_of_three_lower_bound == 0.97
    z = metric(0, 0)
    assert z.rate is None and z.note == "n=0"
    with pytest.raises(ValueError):
        wilson(1, 0)
