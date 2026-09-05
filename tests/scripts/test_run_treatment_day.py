"""Phase 2b-4: the composition entrypoint — two legs, two roles, one business day.

The point of these tests is not that the entrypoint runs, but that it cannot become a back door: the model
still proposes, the deterministic layers still decide, the credential is unreachable from the pipeline leg,
and a re-run cannot duplicate the invoice-scoped proposal.
"""

import json
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from baaki.agent.context import InboundMessage
from baaki.config import ModelCredentialLeak
from baaki.domain.enums import DegradationLevel, ParseStatus
from baaki.pipeline.run import AlreadyDecided, Decided
from baaki.providers.llm.openai_provider import OpenAIProvider
from baaki.providers.llm.transport import TransportError, TransportOutcome
from baaki.scripts.run_treatment_day import run_treatment_day
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
    """The socket, replaced. Identical wiring to production otherwise."""

    def __init__(self, *outcomes: TransportOutcome) -> None:
        self.outcomes = list(outcomes)
        self.sent = 0

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float):
        self.sent += 1
        return self.outcomes.pop(0) if self.outcomes else TransportOutcome(error=TransportError.UNAVAILABLE)


def reply(body: Any) -> TransportOutcome:
    env = {"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(body)}}]}
    return TransportOutcome(status_code=200, body=json.dumps(env).encode(), headers={"x-request-id": "req_test"})


def action_body(ids: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "SEND_REMINDER",
        "contact_id": str(ids["contact"]),
        "channel": "EMAIL",
        "template_id": "tpl.reminder.email.v1",
        "followup_days": None,
        "rationale": "overdue",
        "confidence": 0.9,
    }


def provider_for(*outcomes: TransportOutcome, key: str | None = "sk-test-not-a-real-key") -> OpenAIProvider:
    return OpenAIProvider(SecretStr(key) if key else None, transport=ScriptedTransport(*outcomes))


@pytest.fixture
def world(owner, app, db):
    ids = seed_org_account_contact(owner)
    issue_due(app, ids, amount=450_000, due=BDATE - timedelta(days=15))
    eng_app, eng_agent = db.engine("baaki_app"), db.engine("baaki_agent")
    yield ids, eng_app, eng_agent
    eng_app.dispose()
    eng_agent.dispose()


def drive(world, provider, *, message=MSG):
    ids, eng_app, eng_agent = world
    return run_treatment_day(
        engine_app=eng_app,
        engine_agent=eng_agent,
        provider=provider,
        account_id=ids["account"],
        as_of=AS_OF,
        ruleset=RULESET,
        message=message,
        inbound_contact_id=ids["contact"],
    )


# ── the happy path ───────────────────────────────────────────────────────────────────────


def test_a_model_proposal_reaches_a_deterministic_decision(world):
    ids, _, _ = world
    result = drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    assert isinstance(result.outcome, Decided)
    assert len(result.proposals) == 2  # call 1 and call 2 both recorded via W07
    assert len(result.records) == 2
    assert all(r.parse_status == ParseStatus.OK.value for r in result.records)
    assert all(r.degradation_level == result.outcome.degradation_level.value for r in result.records)


def test_the_entrypoint_records_the_deterministic_verdict_not_the_model_s_claim(world):
    ids, _, _ = world
    result = drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    call1 = result.records[0]
    # the validator's own outcome, taken from the bundle — never inferred from what the model said
    assert call1.validation_outcome in {"PASS", "REJECT"}
    assert call1.action_selected == getattr(result.outcome.decision, "action_type", None).value


# ── every provider fault still lands safely ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome",
    [
        TransportOutcome(error=TransportError.TIMEOUT),
        TransportOutcome(error=TransportError.UNAVAILABLE),
        TransportOutcome(status_code=500, body=b"{}", headers={}),
        TransportOutcome(status_code=429, body=b"{}", headers={}),
        TransportOutcome(status_code=200, body=b"not json at all", headers={}),
    ],
)
def test_every_provider_fault_degrades_to_the_deterministic_path(world, outcome):
    result = drive(world, provider_for(outcome, outcome, outcome))
    assert isinstance(result.outcome, Decided)
    assert result.outcome.degradation_level is DegradationLevel.L1
    assert all(r.parse_status != ParseStatus.OK.value for r in result.records)


def test_no_credential_still_produces_a_safe_decision(world):
    result = drive(world, provider_for(key=None))
    assert isinstance(result.outcome, Decided)
    assert result.outcome.degradation_level is DegradationLevel.L1


def test_an_absent_message_skips_call_one_entirely(world):
    ids, _, _ = world
    result = drive(world, provider_for(reply(action_body(ids))), message=None)
    assert isinstance(result.outcome, Decided)
    assert len(result.records) == 1  # case A: call 2 only


# ── idempotency ──────────────────────────────────────────────────────────────────────────


def test_a_second_run_never_duplicates_the_action_proposal(world, su):
    """The real proposal idempotency boundary: `uq_proposal_daily(invoice_id, business_date, kind, input_hash)`.

    The entrypoint absorbs the unique violation instead of dying, so a re-run completes safely.
    """
    ids, _, _ = world
    first = drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    assert isinstance(first.outcome, Decided)

    second = drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    assert isinstance(second.outcome, Decided | AlreadyDecided)  # completed; did not raise

    kinds = dict(su.execute(text("SELECT kind, count(*) FROM baaki.agent_proposal GROUP BY kind")).all())
    assert kinds["ACTION_PROPOSAL"] == 1  # the invoice-scoped proposal is written exactly once per day


def test_a_re_run_opens_a_new_decision_cycle_by_design(world, su):
    """Recorded so it cannot drift silently — this is committed pipeline behaviour, not entrypoint policy.

    A linked decision's per-day uniqueness is keyed on `validation_id` (§5.8); an unlinked one on
    (invoice, day, arm) with `proposal_id IS NULL`. On a re-run the action proposal is refused by
    `uq_proposal_daily` and absorbed, so the pipeline runs with no proposals and takes the unlinked path —
    which does not match the first run's linked decision. A second decision is therefore written.

    Consequence for callers: the entrypoint is safe to re-run (it never raises and never duplicates the
    invoice-scoped proposal) but it is NOT decision-level idempotent. The demo seeds a fresh account per
    scenario rather than relying on replay.
    """
    ids, _, _ = world
    drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    before = count(su, "policy_decision")
    second = drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    assert isinstance(second.outcome, Decided | AlreadyDecided)  # completed safely
    assert count(su, "policy_decision") == before + 1  # a new cycle, by design


# ── the two roles ────────────────────────────────────────────────────────────────────────


def test_the_agent_leg_and_the_pipeline_leg_use_different_roles(world, su):
    ids, _, _ = world
    drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
    # W07 is executable only by baaki_agent, W09/W10 only by baaki_app; both rows exist, so both legs ran
    # under their own role. A single-role run cannot produce this pair.
    assert count(su, "agent_proposal") >= 1
    assert count(su, "policy_decision") >= 1


def test_the_agent_role_cannot_write_a_decision(agent):
    with pytest.raises(Exception):  # InsufficientPrivilege — W09 is app-only
        agent.execute(text("SELECT baaki_write.record_decision(NULL)"))


# ── the barrier ──────────────────────────────────────────────────────────────────────────


def test_the_pipeline_leg_refuses_to_run_while_the_credential_is_reachable(world, monkeypatch):
    ids, _, _ = world
    monkeypatch.setenv("OPENAI_API_KEY", "sk-put-back-by-something")
    with pytest.raises(ModelCredentialLeak):
        drive(world, provider_for(reply(INTERP), reply(action_body(ids))))
