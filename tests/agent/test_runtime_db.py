"""AgentWorkflow on PostgreSQL: W07 as baaki_agent, cases A/B/C, budget, pre-checks, role boundary (PHASE2B_PLAN §5, §12)."""
import pytest
from sqlalchemy import text

from baaki.agent.context import InboundMessage, build_action_request, build_interpretation_request
from baaki.agent.runtime import Absent, AgentWorkflow, Failed, Passed
from baaki.contracts.validation_input import ValidationInput
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.domain.enums import ParseStatus, ValidationOutcome
from baaki.domain.errors import UnauthorizedInvoker
from baaki.domain.ids import new_id
from baaki.policy.snapshot import assemble_account_facts
from baaki.policy.validate import validate
from baaki.providers.llm.base import BudgetMisuse, ProviderStatus
from baaki.providers.llm.fixtures import FixtureProvider, Script, fault, ok
from tests.helpers import seed_org_account_contact
from tests.phase2_helpers import IST, RULESET, issue_due, workday_as_of

AS_OF = workday_as_of()
BDATE = AS_OF.astimezone(IST).date()
MSG = InboundMessage(text="We will pay by Friday", received_at=AS_OF)
INTERP = {"intent": "WILL_PAY_ON_DATE", "promised_date_raw": "Friday", "promised_amount_raw": None, "invoice_refs": [], "contact_correction": None,
          "sentiment": "NEUTRAL", "confidence": 0.9, "evidence": [{"field": "promised_date_raw", "quote": "by Friday"}]}


def action_body(ids):
    return {"action": "SEND_REMINDER", "contact_id": str(ids["contact"]), "channel": "EMAIL", "template_id": "tpl.reminder.email.v1",
            "followup_days": None, "rationale": "overdue", "confidence": 0.9}


@pytest.fixture
def setup(owner, app, db):
    ids = seed_org_account_contact(owner)
    inv = issue_due(app, ids, amount=450_000, due=BDATE - __import__("datetime").timedelta(days=15))
    eng = db.engine("baaki_app")
    facts = assemble_account_facts(eng, ids["account"], AS_OF, RULESET)
    yield ids, inv, facts, eng
    eng.dispose()


def rows(su):
    return su.execute(text("SELECT kind::text, parse_status::text, provider, model_id, prompt_template_id, schema_version FROM baaki.agent_proposal ORDER BY created_at, kind")).all()


def test_case_b_interpretation_then_validated_action(setup, agent, su):
    ids, inv, facts, eng = setup
    h1 = build_interpretation_request(facts, MSG, correlation_id=new_id(), trace_id=new_id())[0].prompt_hash
    provider = FixtureProvider({h1: Script(outcomes=(ok(INTERP, latency_ms=33),))})
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    c1 = wf.propose_interpretation(agent, facts, MSG, now=AS_OF)
    assert c1.status is ProviderStatus.OK and c1.attempts == 1 and c1.proposal.parse_status is ParseStatus.OK
    v1 = validate(ValidationInput(proposal=c1.proposal, source_text=c1.source_text, facts=facts), RULESET, now=AS_OF)
    assert v1.result.outcome is ValidationOutcome.PASS
    normalized = v1.result.normalized
    assert isinstance(normalized, NormalizedInterpretation)
    h2 = build_action_request(facts, interpretation=normalized, correlation_id=new_id(), trace_id=new_id())[0].prompt_hash
    provider.add_script(h2, Script(outcomes=(ok(action_body(ids)),)))
    c2 = wf.propose_action(agent, facts, call1=Passed(normalized), now=AS_OF)
    assert c2.status is ProviderStatus.OK and c2.proposal.invoice_id == inv and c2.proposal.parse_status is ParseStatus.OK
    assert rows(su) == [("INTERPRETATION", "OK", "fixture", "fixture-model-v1", "interp.v2", "interpretation.v1"),
                        ("ACTION_PROPOSAL", "OK", "fixture", "fixture-model-v1", "propose.v1", "action_proposal.v1")]
    assert wf.budget.used == 2 and wf.budget.log == ["interp.v2:1", "propose.v1:1"]
    assert "normalized" not in provider.requests[1].user_text and '"intent":"WILL_PAY_ON_DATE"' in provider.requests[1].user_text
    assert "by Friday" not in provider.requests[1].user_text  # call 2 never sees the raw call-1 output or the message


def test_case_a_absent_message_runs_call2_only(setup, agent, su):
    ids, inv, facts, eng = setup
    provider = FixtureProvider(default=Script(outcomes=(ok(action_body(ids)),)))
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    c2 = wf.propose_action(agent, facts, call1=Absent(), now=AS_OF)
    assert c2.status is ProviderStatus.OK and c2.skipped_reason is None
    assert [r[0] for r in rows(su)] == ["ACTION_PROPOSAL"] and '"inbound_message":"none"' in provider.requests[0].user_text
    assert wf.budget.used == 1


def test_case_c_failed_call1_skips_call2_without_spending(setup, agent, su):
    ids, inv, facts, eng = setup
    provider = FixtureProvider(default=Script(outcomes=(fault(ProviderStatus.TIMEOUT, latency_ms=100), fault(ProviderStatus.TIMEOUT, latency_ms=100))))
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    c1 = wf.propose_interpretation(agent, facts, MSG, now=AS_OF)
    assert c1.status is ProviderStatus.TIMEOUT and c1.attempts == 2 and c1.proposal.parse_status is ParseStatus.TIMEOUT
    c2 = wf.propose_action(agent, facts, call1=Failed(), now=AS_OF)
    assert c2.proposal is None and c2.skipped_reason == "call1_failed" and c2.attempts == 0
    assert wf.budget.used == 2 and len(provider.requests) == 1 and [r[1] for r in rows(su)] == ["TIMEOUT"]


def test_budget_after_two_call1_attempts_leaves_one_attempt_and_no_retry(setup, agent, su):
    ids, inv, facts, eng = setup
    provider = FixtureProvider(default=Script(outcomes=(fault(ProviderStatus.SERVER_ERROR), fault(ProviderStatus.SERVER_ERROR),
                                                        fault(ProviderStatus.SERVER_ERROR), ok(action_body(ids)))))
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    c1 = wf.propose_interpretation(agent, facts, MSG, now=AS_OF)
    assert c1.attempts == 2 and c1.proposal.parse_status is ParseStatus.PROVIDER_ERROR
    c2 = wf.propose_action(agent, facts, call1=Absent(), now=AS_OF)  # caller treats the day as message-less for call 2
    assert c2.status is ProviderStatus.SERVER_ERROR and c2.attempts == 1 and wf.budget.used == 3  # retry warranted, budget forbids
    with pytest.raises(BudgetMisuse):
        wf.propose_action(agent, facts, call1=Absent(), now=AS_OF)  # one logical call 2 per workflow → a 4th attempt is impossible
    assert wf.budget.used == 3 and [r[1] for r in rows(su)] == ["PROVIDER_ERROR", "PROVIDER_ERROR"]


def test_one_call1_per_workflow(setup, agent):
    ids, inv, facts, eng = setup
    wf = AgentWorkflow(FixtureProvider(default=Script(outcomes=(ok(INTERP),))), account_id=ids["account"], business_date=BDATE)
    wf.propose_interpretation(agent, facts, MSG, now=AS_OF)
    with pytest.raises(BudgetMisuse):
        wf.propose_interpretation(agent, facts, MSG, now=AS_OF)


def test_kill_switch_and_no_candidates_spend_nothing_and_write_nothing(setup, agent, su, owner):
    ids, inv, facts, eng = setup
    provider = FixtureProvider(default=Script(outcomes=(ok(INTERP),)))
    ks = facts.model_copy(update={"kill_switch": True})
    wf = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    assert wf.propose_interpretation(agent, ks, MSG, now=AS_OF).skipped_reason == "kill_switch"
    assert wf.propose_action(agent, ks, call1=Absent(), now=AS_OF).skipped_reason == "kill_switch"
    empty = facts.model_copy(update={"candidates": []})
    wf2 = AgentWorkflow(provider, account_id=ids["account"], business_date=BDATE)
    assert wf2.propose_interpretation(agent, empty, MSG, now=AS_OF).skipped_reason == "no_candidates"
    assert provider.requests == [] and wf.budget.used == 0 and wf2.budget.used == 0 and rows(su) == []


def test_w07_boundary_only_baaki_agent_may_record(setup, app, ops, sim):
    ids, inv, facts, eng = setup
    for conn in (app, ops, sim):
        wf = AgentWorkflow(FixtureProvider(default=Script(outcomes=(ok(INTERP),))), account_id=ids["account"], business_date=BDATE)
        with pytest.raises(UnauthorizedInvoker):
            wf.propose_interpretation(conn, facts, MSG, now=AS_OF)
        conn.rollback()


def test_workflow_refuses_facts_from_another_account(setup, agent, owner, app):
    ids, inv, facts, eng = setup
    other = seed_org_account_contact(owner)
    wf = AgentWorkflow(FixtureProvider(default=Script(outcomes=(ok(INTERP),))), account_id=other["account"], business_date=BDATE)
    from baaki.domain.errors import ContractViolation
    with pytest.raises(ContractViolation):
        wf.propose_interpretation(agent, facts, MSG, now=AS_OF)
