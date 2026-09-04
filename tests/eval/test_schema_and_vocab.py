"""Schema contracts: vocabulary parity with production (by value), closed models, invariants (D-2b2-1/14/15)."""
import pytest
from eval.schema import (
    FAMILY_OF,
    OPT_OUT_POSITIVE,
    PAIR_FEATURE_FIELDS,
    SAFETY_SUBTYPE,
    Ambiguity,
    ExpectedOutcome,
    Family,
    OptOutScope,
    PairFeature,
    PtpOracle,
    SchemaIntent,
    SemanticOracle,
    VerdictClass,
)

from baaki.domain.enums import ActionType, EscalationReason, SuppressReason
from baaki.policy.schemas.interpretation_v1 import Intent as ProductionIntent


def test_intent_vocabulary_equals_production_by_value_without_importing_it_in_eval():
    assert {i.value for i in SchemaIntent} == {i.value for i in ProductionIntent}  # the TEST may import production; eval/ may not


def test_family_mapping_is_the_locked_d_2b2_1():
    assert FAMILY_OF[SchemaIntent.UNSUBSCRIBE] is Family.OPT_OUT
    assert FAMILY_OF[SchemaIntent.DISPUTE_AMOUNT] is Family.DISPUTE and FAMILY_OF[SchemaIntent.DISPUTE_DELIVERY] is Family.DISPUTE
    assert FAMILY_OF[SchemaIntent.ALREADY_PAID_CLAIM] is Family.PAID_CLAIM
    assert FAMILY_OF[SchemaIntent.WILL_PAY_ON_DATE] is Family.PROMISE_TO_PAY and FAMILY_OF[SchemaIntent.REQUEST_INSTALLMENTS] is Family.PROMISE_TO_PAY
    assert FAMILY_OF[SchemaIntent.NEEDS_DOCUMENT] is Family.REQUEST_INFO
    assert FAMILY_OF[SchemaIntent.NO_CLEAR_INTENT] is Family.UNRELATED
    assert FAMILY_OF[SchemaIntent.WRONG_CONTACT] is None and SAFETY_SUBTYPE is SchemaIntent.WRONG_CONTACT
    assert len(Family) == 6


def test_opt_out_positive_set_is_locked():
    assert OPT_OUT_POSITIVE == {OptOutScope.GENERAL, OptOutScope.CHANNEL_INBOUND}
    assert set(OptOutScope) == {"NONE", "GENERAL", "CHANNEL_INBOUND", "CHANNEL_OTHER", "TEMPORARY", "AMBIGUOUS"}


def test_semantic_oracle_enforces_scope_intent_coupling():
    SemanticOracle(primary_intent=SchemaIntent.UNSUBSCRIBE, opt_out_scope=OptOutScope.GENERAL)
    with pytest.raises(ValueError):
        SemanticOracle(primary_intent=SchemaIntent.UNSUBSCRIBE)  # UNSUBSCRIBE needs a positive scope
    with pytest.raises(ValueError):
        SemanticOracle(primary_intent=SchemaIntent.NO_CLEAR_INTENT, opt_out_scope=OptOutScope.GENERAL)
    with pytest.raises(ValueError):
        SemanticOracle(primary_intent=SchemaIntent.NO_CLEAR_INTENT, opt_out_scope=OptOutScope.TEMPORARY)  # needs until-date
    with pytest.raises(ValueError):
        SemanticOracle(primary_intent=SchemaIntent.NO_CLEAR_INTENT, opt_out_scope=OptOutScope.CHANNEL_OTHER)  # needs flag
    with pytest.raises(ValueError):
        SemanticOracle(primary_intent=SchemaIntent.WILL_PAY_ON_DATE, secondary_intents=[SchemaIntent.WILL_PAY_ON_DATE])
    with pytest.raises(ValueError):
        SemanticOracle(primary_intent=SchemaIntent.WILL_PAY_ON_DATE, secondary_intents=[SchemaIntent.DISPUTE_AMOUNT])  # needs ambiguity
    SemanticOracle(primary_intent=SchemaIntent.WILL_PAY_ON_DATE, secondary_intents=[SchemaIntent.DISPUTE_AMOUNT], ambiguity=Ambiguity.MULTI_INTENT)


def test_ptp_oracle_requires_exactly_one_of_value_or_abstain():
    with pytest.raises(ValueError):
        PtpOracle(raw_date_span="Friday", normalization_rationale="x")  # neither value nor abstain
    with pytest.raises(ValueError):
        PtpOracle(raw_date_span="Friday", expected_date_iso="2026-09-04", abstain_date=True, normalization_rationale="x")
    with pytest.raises(ValueError):
        PtpOracle(expected_date_iso="2026-09-04", normalization_rationale="x")  # value without span
    PtpOracle(raw_date_span="next week", abstain_date=True, normalization_rationale="ENR-10")


def test_expected_outcome_shape():
    with pytest.raises(ValueError):
        ExpectedOutcome(verdict_class=VerdictClass.ALLOW)
    with pytest.raises(ValueError):
        ExpectedOutcome(verdict_class=VerdictClass.BLOCK)
    with pytest.raises(ValueError):
        ExpectedOutcome(verdict_class=VerdictClass.DEFER, action=ActionType.SEND_REMINDER)
    ExpectedOutcome(verdict_class=VerdictClass.REQUIRE_APPROVAL, action=ActionType.ESCALATE_TO_HUMAN, escalation_reason=EscalationReason.MANUAL_REVIEW)
    ExpectedOutcome(verdict_class=VerdictClass.ALLOW, action=ActionType.SUPPRESS, suppress_reason=SuppressReason.NO_ELIGIBLE_ACTION)


def test_pair_feature_map_is_closed_and_covers_every_feature():
    assert set(PAIR_FEATURE_FIELDS) == set(PairFeature)
    for fields in PAIR_FEATURE_FIELDS.values():
        assert fields <= set(SemanticOracle.model_fields)
    from pathlib import Path

    import eval.schema as schema_mod

    src = Path(schema_mod.__file__).read_text()
    assert "≤3" not in src and "token bound" not in src.split("PAIR_FEATURE_FIELDS")[1][:2000]  # no token-count rule anywhere
