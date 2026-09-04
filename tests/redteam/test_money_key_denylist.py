"""J. A3 — money keys rejected at Pydantic, at W07, and by the CHECK."""
import pytest
from sqlalchemy import text

from baaki.contracts.agent_proposal import AgentProposal, RawJson
from baaki.domain.enums import MONEY_KEY_DENYLIST, Arm, ParseStatus, ProposalKind
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from tests.helpers import H64, NOW, TODAY, issue, raises_writer, record_proposal, seed_org_account_contact

KEYS = sorted(MONEY_KEY_DENYLIST) + ["settle_amount", "settlement"]


def _proposal(parsed):
    return AgentProposal(proposal_id=new_id(), trace_id=new_id(), account_id=new_id(), kind=ProposalKind.INTERPRETATION,
                         business_date=TODAY, arm=Arm.TREATMENT, provider="openai", model_id="m", prompt_template_id="t",
                         schema_version="interpretation.v1", prompt_hash=H64, input_hash=H64, raw_response=RawJson({"a": 1}),
                         parsed=parsed, parse_status=ParseStatus.OK, confidence=0.5, evidence=[], latency_ms=1, created_at=NOW)


@pytest.mark.parametrize("key", KEYS)
def test_pydantic_layer(key):
    with pytest.raises(ContractViolation):
        _proposal({"intent": "X", key: 1})


@pytest.mark.parametrize("key", KEYS)
def test_writer_layer(owner, app, agent, key):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    with raises_writer("forbidden_money_field"):
        record_proposal(agent, ids, inv, parsed={"intent": "X", key: 1})
    agent.rollback()


def test_check_layer_bypassing_writer(owner):
    from tests.helpers import raises_check
    ids = seed_org_account_contact(owner)
    with raises_check():
        owner.execute(text(
            "INSERT INTO baaki.agent_proposal (proposal_id, trace_id, account_id, kind, business_date, arm, provider, model_id, prompt_template_id, "
            "schema_version, prompt_hash, input_hash, raw_response, parsed, parse_status, evidence, latency_ms) VALUES "
            "(:p, :p, :a, 'INTERPRETATION', current_date, 'TREATMENT', 'x','x','x','x', :h, :h, '{}', '{\"credit\": 1}', 'OK', '[]', 1)"),
            {"p": new_id(), "a": ids["account"], "h": H64})
    owner.rollback()


def test_a5_arm_forced_treatment_in_python():
    with pytest.raises(ContractViolation):
        AgentProposal(proposal_id=new_id(), trace_id=new_id(), account_id=new_id(), kind=ProposalKind.INTERPRETATION, business_date=TODAY,
                      arm=Arm.CONTROL, provider="o", model_id="m", prompt_template_id="t", schema_version="s", prompt_hash=H64, input_hash=H64,
                      raw_response=RawJson({}), parsed=None, parse_status=ParseStatus.TIMEOUT, evidence=[], latency_ms=1, created_at=NOW)
