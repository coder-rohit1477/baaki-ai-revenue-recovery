import pytest

from baaki.contracts.agent_proposal import AgentProposal, RawJson
from baaki.domain.enums import Arm, ParseStatus, ProposalKind
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from tests.helpers import H64, NOW, TODAY


def _p(**kw):
    base = dict(proposal_id=new_id(), trace_id=new_id(), account_id=new_id(), kind=ProposalKind.INTERPRETATION, business_date=TODAY,
                arm=Arm.TREATMENT, provider="openai", model_id="m", prompt_template_id="t", schema_version="s", prompt_hash=H64, input_hash=H64,
                raw_response=RawJson({"a": 1}), parsed={"intent": "X"}, parse_status=ParseStatus.OK, confidence=0.5, evidence=[], latency_ms=1,
                created_at=NOW)
    base.update(kw)
    return AgentProposal(**base)


def test_a2_biconditional():
    with pytest.raises(ContractViolation):
        _p(parsed=None, parse_status=ParseStatus.OK)
    with pytest.raises(ContractViolation):
        _p(parsed={"x": 1}, parse_status=ParseStatus.TIMEOUT, confidence=None)
    _p(parsed=None, parse_status=ParseStatus.TIMEOUT, confidence=None)


def test_a4_no_typed_date_keys():
    with pytest.raises(ContractViolation):
        _p(parsed={"promised_date": "2026-09-09"})
    _p(parsed={"promised_date_raw": "next Tuesday"})


def test_a6_raw_response_is_opaque_wrapper():
    p = _p()
    assert isinstance(p.raw_response, RawJson)
    assert p.raw_response.unwrap_for_audit() == {"a": 1}
    with pytest.raises(Exception):
        p.raw_response.root = {}  # frozen


def test_frozen_and_extra_forbidden():
    p = _p()
    with pytest.raises(Exception):
        p.confidence = 0.1  # type: ignore[misc]
    with pytest.raises(Exception):
        _p(amount_paise=5)
