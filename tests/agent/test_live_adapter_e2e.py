"""Phase 2b-3: the live adapter driven through the whole deterministic path, offline on PostgreSQL 16.

Identical wiring to production, with the socket replaced by a fake transport. The point is not that the
adapter works — that is covered unit-wise — but that a hostile or broken model reply still cannot produce a
financial effect, because the validator and the kernel sit between it and the executor.
"""

import json
import socket
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from baaki.agent.context import InboundMessage, build_interpretation_request
from baaki.agent.observability import record_for
from baaki.agent.runtime import Absent, AgentWorkflow, Failed, Passed
from baaki.contracts.validation_input import ValidationInput
from baaki.domain.enums import ActionType, Arm, DegradationLevel, ParseStatus, ValidationOutcome
from baaki.domain.ids import new_id
from baaki.pipeline.run import Decided, run_decision_pipeline
from baaki.policy.snapshot import assemble_account_facts
from baaki.policy.validate import validate
from baaki.providers.llm.base import ProviderStatus
from baaki.providers.llm.openai_provider import OpenAIProvider
from baaki.providers.llm.transport import TransportError, TransportOutcome
from tests.conftest import _guarded_connect
from tests.helpers import count, seed_org_account_contact
from tests.phase2_helpers import IST, RULESET, issue_due, workday_as_of

AS_OF = workday_as_of()
BDATE = AS_OF.astimezone(IST).date()
MSG = InboundMessage(text="We will pay by Friday", received_at=AS_OF)
INTERP: dict[str, Any] = {
    "intent": "WILL_PAY_ON_DATE",
    "promised_date_raw": "Friday",
    "promised_amount_raw": None,
    "invoice_refs": [],
    "contact_correction": None,
    "sentiment": "NEUTRAL",
    "confidence": 0.9,
    "evidence": [{"field": "promised_date_raw", "quote": "by Friday"}],
}


class ScriptedTransport:
    def __init__(self, *outcomes: TransportOutcome) -> None:
        self.outcomes = list(outcomes)
        self.sent = 0

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float):
        self.sent += 1
        return self.outcomes.pop(0) if self.outcomes else TransportOutcome(error=TransportError.UNAVAILABLE)


def reply(body: Any, *, usage: dict[str, int] | None = None) -> TransportOutcome:
    message: dict[str, Any] = {"role": "assistant", "content": json.dumps(body)}
    env: dict[str, Any] = {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
    if usage:
        env["usage"] = usage
    return TransportOutcome(status_code=200, body=json.dumps(env).encode(), headers={"x-request-id": "req_test"})


def action_body(ids: dict[str, Any], **kw: Any) -> dict[str, Any]:
    b = {
        "action": "SEND_REMINDER",
        "contact_id": str(ids["contact"]),
        "channel": "EMAIL",
        "template_id": "tpl.reminder.email.v1",
        "followup_days": None,
        "rationale": "overdue",
        "confidence": 0.9,
    }
    b.update(kw)
    return b


@pytest.fixture
def world(owner, app, db):
    ids = seed_org_account_contact(owner)
    inv = issue_due(app, ids, amount=450_000, due=BDATE - timedelta(days=15))
    eng = db.engine("baaki_app")
    yield ids, inv, eng, assemble_account_facts(eng, ids["account"], AS_OF, RULESET)
    eng.dispose()


def drive(agent, eng, ids, facts, provider, *, message=MSG):
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    proposals, gate = [], Absent()
    if message is not None:
        c1 = wf.propose_interpretation(agent, facts, message, now=AS_OF)
        proposals.append(c1.pair)
        v1 = validate(
            ValidationInput(proposal=c1.proposal, source_text=c1.source_text, facts=facts), RULESET, now=AS_OF
        )
        gate = Passed(v1.result.normalized) if v1.result.outcome is ValidationOutcome.PASS else Failed()
    c2 = wf.propose_action(agent, facts, call1=gate, now=AS_OF)
    if c2.proposal is not None:
        proposals.append(c2.pair)
    result = run_decision_pipeline(
        eng,
        arm=Arm.TREATMENT,
        account_id=ids["account"],
        as_of=AS_OF,
        ruleset=RULESET,
        proposals=proposals,
        inbound_text=message.text if message else None,
        inbound_contact_id=ids["contact"] if message else None,
    )
    return wf, gate, c2, result


def tally(su):
    return {
        t: count(su, t) for t in ("agent_proposal", "validation_result", "policy_decision", "recovery_action", "outbox")
    }


def test_a_valid_live_shaped_proposal_is_accepted_when_policy_allows(world, agent, su):
    ids, inv, eng, facts = world
    assert socket.socket.connect is _guarded_connect  # no real socket anywhere in this test
    t = ScriptedTransport(
        reply(INTERP, usage={"prompt_tokens": 210, "completion_tokens": 30}),
        reply(action_body(ids), usage={"prompt_tokens": 180, "completion_tokens": 25}),
    )
    p = OpenAIProvider(SecretStr("sk-offline-fake"), transport=t)
    wf, gate, c2, r = drive(agent, eng, ids, facts, p)
    assert isinstance(gate, Passed) and isinstance(r, Decided)
    assert r.degradation_level is DegradationLevel.L0 and r.decision.action_type is ActionType.SEND_REMINDER
    assert tally(su) == {
        "agent_proposal": 2,
        "validation_result": 2,
        "policy_decision": 1,
        "recovery_action": 1,
        "outbox": 1,
    }
    assert su.execute(text("SELECT provider FROM baaki.agent_proposal LIMIT 1")).scalar_one() == "openai"
    assert wf.budget.used == 2 and t.sent == 2  # <= 3 per cycle


def test_money_injected_by_the_model_is_neutralised_end_to_end(world, agent, su):
    ids, inv, eng, facts = world
    t = ScriptedTransport(
        reply(INTERP), reply(action_body(ids, action="SEND_PAYMENT_LINK", template_id="tpl.link.email.v1", amount=1))
    )
    p = OpenAIProvider(SecretStr("sk-offline-fake"), transport=t)
    _, _, c2, r = drive(agent, eng, ids, facts, p)
    assert c2.proposal.parse_status is ParseStatus.SCHEMA_VIOLATION and c2.proposal.parsed is None
    assert r.degradation_level is DegradationLevel.L1
    payload = su.execute(text("SELECT canonical_payload FROM baaki.policy_decision")).scalar_one()
    payload = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    if "amount_paise" in payload:
        assert int(payload["amount_paise"]) == 450_000  # from the ledger, never from the model


def test_a_foreign_contact_proposed_by_the_model_is_rejected(world, agent, su):
    ids, inv, eng, facts = world
    t = ScriptedTransport(reply(INTERP), reply(action_body(ids, contact_id=str(new_id()))))
    p = OpenAIProvider(SecretStr("sk-offline-fake"), transport=t)
    _, _, c2, r = drive(agent, eng, ids, facts, p)
    reasons = su.execute(
        text("SELECT rejection_reasons::text[] FROM baaki.validation_result WHERE proposal_id=:p"),
        {"p": c2.proposal.proposal_id},
    ).scalar_one()
    assert "CONTACT_NOT_IN_ACCOUNT" in reasons and r.degradation_level is DegradationLevel.L1
    assert su.execute(text("SELECT count(*) FROM baaki.outbox")).scalar_one() <= 1


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (TransportOutcome(error=TransportError.TIMEOUT), ProviderStatus.TIMEOUT),
        (TransportOutcome(error=TransportError.UNAVAILABLE), ProviderStatus.UNAVAILABLE),
        (TransportOutcome(status_code=500, body=b"{}", headers={}), ProviderStatus.SERVER_ERROR),
        (TransportOutcome(status_code=401, body=b"{}", headers={}), ProviderStatus.NO_CREDENTIALS),
        (TransportOutcome(status_code=200, body=b"<html>", headers={}), ProviderStatus.MALFORMED),
    ],
)
def test_every_provider_failure_degrades_to_the_deterministic_rules_path(world, agent, su, outcome, expected):
    ids, inv, eng, facts = world
    p = OpenAIProvider(SecretStr("sk-offline-fake"), transport=ScriptedTransport(outcome, outcome, outcome))
    _, gate, c2, r = drive(agent, eng, ids, facts, p)
    assert isinstance(gate, Failed) and c2.proposal is None
    assert isinstance(r, Decided) and r.degradation_level is DegradationLevel.L1
    assert tally(su)["recovery_action"] == 1  # the business still gets a safe decision


def test_no_credential_still_produces_a_safe_decision(world, agent, su):
    ids, inv, eng, facts = world
    t = ScriptedTransport()
    p = OpenAIProvider(None, transport=t)
    _, gate, c2, r = drive(agent, eng, ids, facts, p)
    assert t.sent == 0 and isinstance(gate, Failed)
    assert isinstance(r, Decided) and r.degradation_level is DegradationLevel.L1


def test_the_observability_record_is_secret_free(world, agent, su):
    ids, inv, eng, facts = world
    t = ScriptedTransport(reply(INTERP, usage={"prompt_tokens": 210, "completion_tokens": 30}))
    p = OpenAIProvider(SecretStr("sk-offline-fake"), transport=t)
    request, _ = build_interpretation_request(facts, MSG, correlation_id=new_id(), trace_id=new_id())
    from baaki.providers.llm.base import CallBudget

    response = p.complete_structured(request, CallBudget())
    rec = record_for(
        response,
        correlation_id=request.correlation_id,
        trace_id=request.trace_id,
        prompt_template_id=request.prompt_template_id,
        prompt_hash=request.prompt_hash,
    )
    fields = rec.as_log_fields()
    assert fields["provider"] == "openai" and fields["status"] == "OK" and fields["attempts"] == 1
    assert fields["input_tokens"] == 210 and fields["cost_estimate_micro_usd"] is not None
    blob = json.dumps(fields)
    assert "sk-offline-fake" not in blob and MSG.text not in blob and "Bearer" not in blob
