from datetime import timedelta

import pytest

from baaki.contracts.canonical_payload import SuppressPayload
from baaki.contracts.policy_decision import KERNEL_TOKEN, ExecutableDecision, NonExecutableDecision, as_executable
from baaki.domain.enums import ActionType, Arm, DegradationLevel, SuppressReason, Verdict
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from tests.helpers import H64, NOW, TODAY

BASE = dict(decision_id=new_id(), trace_id=new_id(), arm=Arm.CONTROL, account_id=new_id(), invoice_id=new_id(), business_date=TODAY,
            policy_version="p", kernel_version="k", policy_hash=H64, snapshot_hash=H64, degradation_level=DegradationLevel.L1, decided_at=NOW)
SUP = SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION)


def test_token_required_on_init_and_model_validate():
    with pytest.raises(ContractViolation):
        ExecutableDecision(**BASE, tier=0, verdict=Verdict.ALLOW, action_type=ActionType.SUPPRESS, canonical_payload=SUP)
    with pytest.raises(ContractViolation):
        ExecutableDecision.model_validate({**BASE, "tier": 0, "verdict": Verdict.ALLOW, "action_type": ActionType.SUPPRESS, "canonical_payload": SUP})
    d = ExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.ALLOW, action_type=ActionType.SUPPRESS, canonical_payload=SUP)
    assert d.verdict is Verdict.ALLOW


def test_frozen():
    d = ExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.ALLOW, action_type=ActionType.SUPPRESS, canonical_payload=SUP)
    with pytest.raises(Exception):
        d.tier = 2  # type: ignore[misc]


def test_p3a_p3b_by_type():
    with pytest.raises(Exception):   # executable requires payload (missing field)
        ExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.ALLOW, action_type=ActionType.SUPPRESS)
    with pytest.raises(Exception):   # non-executable cannot carry payload
        NonExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.BLOCK, blocking_rules=[{"r": 1}], canonical_payload=SUP)


def test_p2_p5_p7_p8():
    with pytest.raises(ContractViolation):   # P2
        NonExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.BLOCK)
    with pytest.raises(ContractViolation):   # P5
        ExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=2, verdict=Verdict.ALLOW, action_type=ActionType.SUPPRESS, canonical_payload=SUP)
    with pytest.raises(ContractViolation):   # P7
        ExecutableDecision(_token=KERNEL_TOKEN, **{**BASE, "proposal_id": new_id(), "validation_id": new_id()}, tier=0, verdict=Verdict.ALLOW,
                           action_type=ActionType.SUPPRESS, canonical_payload=SUP)
    with pytest.raises(ContractViolation):   # P8
        NonExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.DEFER)
    ok = NonExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=0, verdict=Verdict.DEFER, defer_until=NOW + timedelta(hours=1))
    with pytest.raises(ContractViolation):
        as_executable(ok)
    with pytest.raises(Exception):   # tier 3 unrepresentable
        ExecutableDecision(_token=KERNEL_TOKEN, **BASE, tier=3, verdict=Verdict.ALLOW, action_type=ActionType.SUPPRESS, canonical_payload=SUP)  # type: ignore[arg-type]
