"""PG16 end-to-end, zero network: fixture provider → AgentProposal → W07 → validator → kernel → pipeline → L0 decision.
Plus every provider fault status through the same path (PHASE2B_PLAN §8, §9, §18)."""
import socket
from datetime import timedelta

import pytest
from sqlalchemy import text

from baaki.agent.context import InboundMessage, build_interpretation_request
from baaki.agent.runtime import Absent, AgentWorkflow, Failed, Passed
from baaki.contracts.policy_decision import ExecutableDecision
from baaki.contracts.validation_input import ValidationInput
from baaki.domain.enums import ActionType, Arm, DegradationLevel, ParseStatus, ValidationOutcome, Verdict
from baaki.domain.ids import new_id
from baaki.pipeline.run import AlreadyDecided, Decided, run_decision_pipeline
from baaki.policy.snapshot import assemble_account_facts
from baaki.policy.validate import validate
from baaki.providers.llm.base import ProviderStatus
from baaki.providers.llm.fixtures import FixtureProvider, Script, fault, ok
from tests.conftest import _guarded_connect
from tests.helpers import count, seed_org_account_contact
from tests.phase2_helpers import IST, RULESET, issue_due, workday_as_of

AS_OF = workday_as_of()
BDATE = AS_OF.astimezone(IST).date()
MSG = InboundMessage(text="We will pay by Friday", received_at=AS_OF)
INTERP = {"intent": "WILL_PAY_ON_DATE", "promised_date_raw": "Friday", "promised_amount_raw": None, "invoice_refs": [], "contact_correction": None,
          "sentiment": "NEUTRAL", "confidence": 0.9, "evidence": [{"field": "promised_date_raw", "quote": "by Friday"}]}


def action_body(ids, **kw):
    b = {"action": "SEND_REMINDER", "contact_id": str(ids["contact"]), "channel": "EMAIL", "template_id": "tpl.reminder.email.v1",
         "followup_days": None, "rationale": "overdue", "confidence": 0.9}
    b.update(kw)
    return b


@pytest.fixture
def world(owner, app, db):
    ids = seed_org_account_contact(owner)
    inv = issue_due(app, ids, amount=450_000, due=BDATE - timedelta(days=15))
    eng = db.engine("baaki_app")
    yield ids, inv, eng, assemble_account_facts(eng, ids["account"], AS_OF, RULESET)
    eng.dispose()


def tally(su):
    return {t: count(su, t) for t in ("agent_proposal", "validation_result", "policy_decision", "recovery_action", "outbox")}


def compose(agent, eng, ids, facts, provider, *, message=MSG):
    """The 2b-1 composition used by tests: agent (baaki_agent) → validator verdict → agent → pipeline (baaki_app)."""
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    proposals = []
    gate = Absent()
    if message is not None:
        c1 = wf.propose_interpretation(agent, facts, message, now=AS_OF)
        proposals.append(c1.pair)
        v1 = validate(ValidationInput(proposal=c1.proposal, source_text=c1.source_text, facts=facts), RULESET, now=AS_OF)
        gate = Passed(v1.result.normalized) if v1.result.outcome is ValidationOutcome.PASS else Failed()  # type: ignore[arg-type]
    c2 = wf.propose_action(agent, facts, call1=gate, now=AS_OF)
    if c2.proposal is not None:
        proposals.append(c2.pair)
    result = run_decision_pipeline(eng, arm=Arm.TREATMENT, account_id=ids["account"], as_of=AS_OF, ruleset=RULESET, proposals=proposals,
                                   inbound_text=message.text if message else None, inbound_contact_id=ids["contact"] if message else None)
    return wf, gate, c2, result


def test_full_offline_treatment_flow_yields_l0_decision_and_one_queued_action(world, agent, su):
    ids, inv, eng, facts = world
    assert socket.socket.connect is _guarded_connect  # zero network for the whole flow
    h1 = build_interpretation_request(facts, MSG, correlation_id=new_id(), trace_id=new_id())[0].prompt_hash
    provider = FixtureProvider({h1: Script(outcomes=(ok(INTERP),))}, default=Script(outcomes=(ok(action_body(ids)),)))
    wf, gate, c2, r = compose(agent, eng, ids, facts, provider)
    assert isinstance(gate, Passed) and isinstance(r, Decided) and isinstance(r.decision, ExecutableDecision)
    assert r.degradation_level is DegradationLevel.L0 and r.decision.action_type is ActionType.SEND_REMINDER and r.decision.verdict is Verdict.ALLOW
    assert r.decision.proposal_id == c2.proposal.proposal_id and r.decision.effective_confidence == 0.9
    assert tally(su) == {"agent_proposal": 2, "validation_result": 2, "policy_decision": 1, "recovery_action": 1, "outbox": 1}
    assert su.execute(text("SELECT state::text FROM baaki.recovery_action")).scalar_one() == "QUEUED"
    assert su.execute(text("SELECT count(*) FROM baaki.agent_proposal WHERE provider='fixture' AND arm='TREATMENT'")).scalar_one() == 2
    assert wf.budget.used == 2 and len(provider.requests) == 2
    # a replay of the same day returns the existing rows, never a second action
    r2 = run_decision_pipeline(eng, arm=Arm.TREATMENT, account_id=ids["account"], as_of=AS_OF, ruleset=RULESET,
                               proposals=[(c2.proposal, c2.source_text)])
    assert isinstance(r2, AlreadyDecided) and r2.decision_id == r.decision_id and tally(su)["recovery_action"] == 1


@pytest.mark.parametrize("status,parse_status", [
    (ProviderStatus.TIMEOUT, "TIMEOUT"), (ProviderStatus.RATE_LIMITED, "PROVIDER_ERROR"), (ProviderStatus.CLIENT_ERROR, "PROVIDER_ERROR"),
    (ProviderStatus.SERVER_ERROR, "PROVIDER_ERROR"), (ProviderStatus.REFUSAL, "PROVIDER_ERROR"), (ProviderStatus.MALFORMED, "UNPARSEABLE"),
    (ProviderStatus.UNAVAILABLE, "PROVIDER_ERROR"), (ProviderStatus.NO_CREDENTIALS, "PROVIDER_ERROR"),
])
def test_every_call1_fault_records_evidence_rejects_and_falls_back_to_l1(world, agent, su, status, parse_status):
    ids, inv, eng, facts = world
    txt = "nope" if status in (ProviderStatus.MALFORMED, ProviderStatus.REFUSAL) else None
    provider = FixtureProvider(default=Script(outcomes=(fault(status, text=txt), fault(status, text=txt))))
    wf, gate, c2, r = compose(agent, eng, ids, facts, provider)
    assert isinstance(gate, Failed) and c2.proposal is None and c2.skipped_reason == "call1_failed"
    assert wf.budget.used <= 3 and all(req.prompt_template_id == "interp.v2" for req in provider.requests)
    assert su.execute(text("SELECT parse_status::text FROM baaki.agent_proposal")).scalar_one() == parse_status
    reason = su.execute(text("SELECT rejection_reasons::text[] FROM baaki.validation_result")).scalar_one()
    assert reason == [{"TIMEOUT": "PROVIDER_TIMEOUT", "PROVIDER_ERROR": "PROVIDER_TIMEOUT", "UNPARSEABLE": "UNPARSEABLE"}[parse_status]]
    assert isinstance(r, Decided) and r.degradation_level is DegradationLevel.L1 and r.decision.proposal_id is None
    assert tally(su)["policy_decision"] == 1 and tally(su)["recovery_action"] == 1 and tally(su)["agent_proposal"] == 1


def test_budget_exhausted_path_is_unreachable_through_the_runtime_and_safe_at_the_port(world, agent, su):
    ids, inv, eng, facts = world
    provider = FixtureProvider(default=Script(outcomes=(fault(ProviderStatus.TIMEOUT), fault(ProviderStatus.TIMEOUT), fault(ProviderStatus.TIMEOUT))))
    wf, gate, c2, r = compose(agent, eng, ids, facts, provider)
    assert wf.budget.used == 2 and c2.proposal is None  # case C never reaches call 2, so no attempt beyond the two
    assert len(provider.requests) == 1 and isinstance(r, Decided) and r.degradation_level is DegradationLevel.L1


def test_call2_fault_with_passed_call1_records_evidence_and_falls_back_to_l1_linked(world, agent, su):
    ids, inv, eng, facts = world
    h1 = build_interpretation_request(facts, MSG, correlation_id=new_id(), trace_id=new_id())[0].prompt_hash
    provider = FixtureProvider({h1: Script(outcomes=(ok(INTERP),))}, default=Script(outcomes=(fault(ProviderStatus.MALFORMED, text="<html>"),)))
    wf, gate, c2, r = compose(agent, eng, ids, facts, provider)
    assert isinstance(gate, Passed) and c2.proposal.parse_status is ParseStatus.UNPARSEABLE
    assert r.degradation_level is DegradationLevel.L1 and r.decision.proposal_id == c2.proposal.proposal_id  # linked, rejected, L1
    assert tally(su)["recovery_action"] == 1


def test_money_injected_into_call2_output_is_neutralised_end_to_end(world, agent, su):
    ids, inv, eng, facts = world
    h1 = build_interpretation_request(facts, MSG, correlation_id=new_id(), trace_id=new_id())[0].prompt_hash
    provider = FixtureProvider({h1: Script(outcomes=(ok(INTERP),))},
                               default=Script(outcomes=(ok(action_body(ids, action="SEND_PAYMENT_LINK", template_id="tpl.link.email.v1", amount=1)),)))
    wf, gate, c2, r = compose(agent, eng, ids, facts, provider)
    assert c2.proposal.parse_status is ParseStatus.SCHEMA_VIOLATION and c2.proposal.parsed is None
    assert su.execute(text("SELECT rejection_reasons::text[] FROM baaki.validation_result WHERE proposal_id=:p"), {"p": c2.proposal.proposal_id}).scalar_one() == ["SCHEMA_VIOLATION"]
    assert r.degradation_level is DegradationLevel.L1
    if isinstance(r.decision, ExecutableDecision) and r.decision.action_type is ActionType.SEND_PAYMENT_LINK:
        assert int(r.decision.canonical_payload.amount_paise) == 450_000  # money from the ledger, never from the model


def test_band_d_choice_is_discarded_to_l1_but_stays_linked(world, agent, su):
    ids, inv, eng, facts = world
    provider = FixtureProvider(default=Script(outcomes=(ok(action_body(ids, confidence=0.3)),)))
    wf, gate, c2, r = compose(agent, eng, ids, facts, provider, message=None)
    assert isinstance(gate, Absent) and r.degradation_level is DegradationLevel.L1 and r.decision.proposal_id == c2.proposal.proposal_id


def test_no_network_socket_is_ever_opened_by_the_flow(world, agent, su, monkeypatch):
    ids, inv, eng, facts = world
    opened = []
    real = socket.socket.connect
    def spy(self, address):
        opened.append(address)
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", spy)
    provider = FixtureProvider(default=Script(outcomes=(ok(action_body(ids)),)))
    compose(agent, eng, ids, facts, provider, message=None)
    assert all(a[0] in ("127.0.0.1", "::1", "localhost") for a in opened if isinstance(a, tuple))  # only the test database
