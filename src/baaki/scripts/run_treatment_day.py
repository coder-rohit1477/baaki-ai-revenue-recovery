"""Composition entrypoint — the agent leg and the pipeline leg, in one run (PHASE2B_PLAN §14, D-2b-3).

Until now this composition existed only inside `tests/agent/test_live_adapter_e2e.py::drive`. Promoting it
to `src/` is the whole of Phase 2b-4: the same two legs, the same two roles, no new authority.

    agent leg   (baaki_agent)  build context → provider port → map → W07
    barrier                    the model credential is gone from the environment
    pipeline leg (baaki_app)   validate → decide → act → ledger

This module sequences. It does not decide: it never touches a balance, never writes an action, and cannot
overrule the validator or the kernel. If the provider fails at any point the run still completes — the
deterministic tree produces the action and the record says `degradation_level = L1`.

Credential separation (§4): `take_model_credential` removes the model credential from the environment before any
engine exists, so no leg — and no library reading the environment — can obtain it afterwards. The only
reference left is the SecretStr held by the provider. `assert_no_model_credential` re-checks at the barrier
and refuses to run the pipeline leg if anything put the key back.

Facts assembly runs as `baaki_app`, not `baaki_agent`: `assemble_account_facts` reads `organization`,
`policy_decision` and `recovery_action`, and the agent role holds no SELECT on those (§6.3 grant matrix).
That is why the credential is taken at process start rather than merely before the pipeline call.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine

from baaki.agent.context import InboundMessage
from baaki.agent.observability import ProviderCallRecord, emit
from baaki.agent.runtime import Absent, AgentWorkflow, Call1Gate, Failed, Passed
from baaki.config import Settings, assert_no_model_credential, load_settings, take_model_credential
from baaki.contracts.agent_proposal import AgentProposal
from baaki.contracts.validation_input import ValidationInput
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.db.engine import engine_for
from baaki.db.writers._call import WriterUniqueViolation
from baaki.domain.enums import Arm, ValidationOutcome
from baaki.pipeline.run import AlreadyDecided, Decided, Ineligible, run_decision_pipeline
from baaki.policy.ruleset import DEFAULT_RULESET_PATH, Ruleset, load_ruleset
from baaki.policy.snapshot import assemble_account_facts
from baaki.policy.validate import validate
from baaki.providers.llm.base import AiProviderPort
from baaki.providers.llm.fixtures import FixtureProvider

PipelineOutcome = Ineligible | Decided | AlreadyDecided


@dataclass(frozen=True)
class CompositionResult:
    """What the two legs produced. `outcome` is the deterministic verdict — the only authority here."""

    outcome: PipelineOutcome
    proposals: list[tuple[AgentProposal, str]] = field(default_factory=list)
    records: list[ProviderCallRecord] = field(default_factory=list)
    gate: Call1Gate = field(default_factory=Absent)

    @property
    def degradation_level(self) -> str | None:
        return self.outcome.degradation_level.value if isinstance(self.outcome, Decided) else None


def _verdict(outcome: PipelineOutcome) -> tuple[str | None, str | None]:
    """(action_selected, fallback_reason) — read off the deterministic result, never inferred."""
    if isinstance(outcome, Ineligible):
        return None, outcome.reason
    if isinstance(outcome, AlreadyDecided):
        return None, "already_decided"
    action = getattr(outcome.decision, "action_type", None)
    return (action.value if action is not None else None), None


def run_treatment_day(
    *,
    engine_app: Engine,
    engine_agent: Engine,
    provider: AiProviderPort,
    account_id: UUID,
    as_of: datetime,
    ruleset: Ruleset,
    message: InboundMessage | None = None,
    inbound_contact_id: UUID | None = None,
    arm: Arm = Arm.TREATMENT,
) -> CompositionResult:
    """One (account, business day) run: agent leg, barrier, pipeline leg.

    Re-running the same (account_id, business_date) is safe: W07 is bounded by
    `uq_proposal_daily(invoice_id, business_date, kind, input_hash)` and the pipeline returns the existing
    rows as `AlreadyDecided`. Nothing is written twice.
    """
    facts = assemble_account_facts(engine_app, account_id, as_of, ruleset)
    workflow = AgentWorkflow(provider, account_id=account_id, business_date=facts.business_date)

    proposals: list[tuple[AgentProposal, str]] = []
    verdicts: dict[UUID, tuple[str | None, list[str] | None]] = {}
    records: list[ProviderCallRecord] = []
    gate: Call1Gate = Absent()

    # ── agent leg — baaki_agent; W07 only ────────────────────────────────────────────────
    with engine_agent.connect() as conn:
        if message is not None:
            call1 = workflow.propose_interpretation(conn, facts, message, now=as_of)
            if call1.record is not None:
                records.append(call1.record)
            if call1.proposal is not None and call1.source_text is not None:
                proposals.append(call1.pair)
                bundle = validate(
                    ValidationInput(proposal=call1.proposal, source_text=call1.source_text, facts=facts),
                    ruleset,
                    now=as_of,
                )
                reasons = [str(r) for r in bundle.result.rejection_reasons]
                verdicts[call1.proposal.proposal_id] = (bundle.result.outcome.value, reasons or None)
                gate = (
                    Passed(bundle.result.normalized)
                    if bundle.result.outcome is ValidationOutcome.PASS
                    and isinstance(bundle.result.normalized, NormalizedInterpretation)
                    else Failed()
                )
        try:
            call2 = workflow.propose_action(conn, facts, call1=gate, now=as_of)
        except WriterUniqueViolation:
            # `uq_proposal_daily(invoice_id, business_date, kind, input_hash)` already holds an identical
            # action proposal for this invoice-day: this is a re-run, not a new fact. Absorb it and let the
            # pipeline return the decision that already exists. Interpretation rows are account-scoped
            # (invoice_id NULL) and so are never constrained by that index.
            conn.rollback()
        else:
            if call2.record is not None:
                records.append(call2.record)
            if call2.proposal is not None and call2.source_text is not None:
                proposals.append(call2.pair)

    # ── barrier — the pipeline leg must not be able to reach the model credential ────────
    assert_no_model_credential()

    # ── pipeline leg — baaki_app; validator → kernel → executor → ledger ─────────────────
    outcome = run_decision_pipeline(
        engine_app,
        arm=arm,
        account_id=account_id,
        as_of=as_of,
        ruleset=ruleset,
        proposals=proposals,
        inbound_text=message.text if message is not None else None,
        inbound_contact_id=inbound_contact_id,
    )

    action_selected, fallback_reason = _verdict(outcome)
    level = outcome.degradation_level.value if isinstance(outcome, Decided) else None
    completed = [
        r.completed(
            validation_outcome=verdicts.get(r.correlation_id, (None, None))[0],
            rejection_reasons=verdicts.get(r.correlation_id, (None, None))[1],
            action_selected=action_selected,
            fallback_reason=fallback_reason,
            degradation_level=level,
        )
        for r in records
    ]
    for record in completed:
        emit(record)
    return CompositionResult(outcome=outcome, proposals=proposals, records=completed, gate=gate)


def build_provider(settings: Settings) -> AiProviderPort:
    """Live adapter when a credential was taken, deterministic replay otherwise.

    A missing credential is not an error: `FixtureProvider` keeps the demo and the default suite offline,
    and a live run without a key would degrade to L1 anyway (NO_CREDENTIALS).
    """
    if settings.openai_api_key is None:
        return FixtureProvider({})
    from baaki.providers.llm.openai_provider import OpenAIProvider

    return OpenAIProvider(settings.openai_api_key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one TREATMENT day for an account (agent leg + pipeline leg).")
    parser.add_argument("--account-id", required=True, type=UUID)
    parser.add_argument("--message", default=None, help="inbound debtor message; omitted means case A (no call 1)")
    parser.add_argument("--contact-id", default=None, type=UUID)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    # taken before any engine exists: from here on the environment holds no model credential
    credential = take_model_credential()
    settings = load_settings()
    settings = settings.model_copy(update={"openai_api_key": credential})

    as_of = datetime.now(UTC)
    ruleset = load_ruleset(DEFAULT_RULESET_PATH)
    engine_app = engine_for(settings, "app")
    engine_agent = engine_for(settings, "agent")
    try:
        result = run_treatment_day(
            engine_app=engine_app,
            engine_agent=engine_agent,
            provider=build_provider(settings),
            account_id=args.account_id,
            as_of=as_of,
            ruleset=ruleset,
            message=InboundMessage(text=args.message, received_at=as_of) if args.message else None,
            inbound_contact_id=args.contact_id,
        )
    finally:
        engine_agent.dispose()
        engine_app.dispose()
    logging.getLogger(__name__).info(
        "outcome=%s degradation_level=%s", type(result.outcome).__name__, result.degradation_level
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
