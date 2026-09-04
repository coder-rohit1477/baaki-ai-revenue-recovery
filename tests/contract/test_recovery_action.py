
import pytest

from baaki.contracts.recovery_action import RecoveryAction
from baaki.domain.enums import ActionState, Verdict
from baaki.domain.errors import ContractViolation


def test_r3_initial_state_mapping():
    assert RecoveryAction.initial_state(Verdict.REQUIRE_APPROVAL) is ActionState.PENDING_APPROVAL
    assert RecoveryAction.initial_state(Verdict.ALLOW) is ActionState.QUEUED
    for v in (Verdict.BLOCK, Verdict.DEFER):
        with pytest.raises(ContractViolation):
            RecoveryAction.initial_state(v)


def test_mutable_fields_set_matches_architecture():
    assert RecoveryAction.MUTABLE_FIELDS == {"state", "attempt_count", "max_attempts", "next_attempt_at", "approved_by_role", "approved_by_note",
                                             "approved_at", "provider_ref", "last_error_code", "executed_at", "confirmed_at", "updated_at"}
    immutable = set(RecoveryAction.model_fields) - RecoveryAction.MUTABLE_FIELDS
    assert {"action_id", "decision_id", "trace_id", "account_id", "invoice_id", "arm", "action_type", "idempotency_key", "expires_at", "created_at"} == immutable
