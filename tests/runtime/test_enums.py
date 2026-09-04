from baaki.domain.enums import (
    ACTION_TIER,
    POSTGRES_ENUMS,
    TEMPLATE_PAIRS,
    ActionState,
    ActionType,
    AssigneeQueue,
    EscalationReason,
    InvoiceState,
    RejectionReason,
    queue_for_reason,
)


def test_counts():
    assert len(POSTGRES_ENUMS) == 19
    assert len(RejectionReason) == 20 and len(ActionType) == 7 and len(ActionState) == 11 and len(InvoiceState) == 5
    assert set(ACTION_TIER.values()) == {0, 1, 2}          # tier 3 unrepresentable
    assert len(TEMPLATE_PAIRS) == 5


def test_queue_for_reason():
    assert queue_for_reason(EscalationReason.DISPUTE_UNRESOLVED) is AssigneeQueue.DISPUTES
    for r in (EscalationReason.PAID_CLAIM_UNVERIFIED, EscalationReason.AMBIGUOUS_INTERPRETATION, EscalationReason.MANUAL_REVIEW):
        assert queue_for_reason(r) is AssigneeQueue.COLLECTIONS
