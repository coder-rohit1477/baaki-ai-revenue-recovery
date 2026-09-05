"""AgentWorkflow — one (account_id, business_date) workflow: build context → port → map → W07 (PHASE2B_PLAN §5, §12).

Upstream producer only. Never calls W08/W09/W10, never validates, never decides. The caller (tests in 2b-1; the
composition entrypoint in 2b-4) runs the deterministic validator between call 1 and call 2 and passes its verdict
back as a `Call1Gate`, so "absent", "passed" and "failed" stay distinct (§5.3 cases A/B/C).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Connection

from baaki.agent.context import InboundMessage, build_action_request, build_interpretation_request
from baaki.agent.mapping import map_response
from baaki.agent.observability import ProviderCallRecord, emit, record_for
from baaki.contracts.agent_proposal import AgentProposal
from baaki.contracts.candidate import AccountFacts
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.db.writers.proposal import record_agent_proposal
from baaki.domain.enums import ProposalKind
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from baaki.providers.llm.base import (
    AiProviderPort,
    BudgetMisuse,
    CallBudget,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
)


@dataclass(frozen=True)
class Absent:
    """Case A — no inbound message existed; call 1 was never attempted."""


@dataclass(frozen=True)
class Passed:
    """Case B — call 1 was attempted and the deterministic validator returned PASS."""

    normalized: NormalizedInterpretation


@dataclass(frozen=True)
class Failed:
    """Case C — call 1 was attempted and did not PASS (REJECT or any provider fault)."""


Call1Gate = Absent | Passed | Failed


@dataclass(frozen=True)
class CallResult:
    proposal: AgentProposal | None
    source_text: str | None
    status: ProviderStatus | None  # None when no attempt was made
    skipped_reason: str | None  # kill_switch | no_candidates | call1_failed
    attempts: int
    record: ProviderCallRecord | None = None  # telemetry for this call; None when no attempt was made

    @property
    def pair(self) -> tuple[AgentProposal, str]:
        if self.proposal is None or self.source_text is None:
            raise ContractViolation("no proposal was produced")
        return self.proposal, self.source_text


def _telemetry(response: ProviderResponse, request: ProviderRequest, proposal: AgentProposal) -> ProviderCallRecord:
    """Emit this call's record now, so a provider fault is observable even if the process dies next.

    The verdict fields stay empty here: the validator and the kernel have not run yet, and the runtime is
    forbidden from calling them (§2.1). The composition entrypoint emits the completed record afterwards.
    """
    record = record_for(
        response,
        correlation_id=request.correlation_id,
        trace_id=request.trace_id,
        prompt_template_id=request.prompt_template_id,
        prompt_hash=request.prompt_hash,
    ).completed(parse_status=proposal.parse_status.value)
    emit(record)
    return record


class AgentWorkflow:
    def __init__(
        self, provider: AiProviderPort, *, account_id: UUID, business_date: date, seed: int | None = None
    ) -> None:
        self.provider = provider
        self.account_id = account_id
        self.business_date = business_date
        self.seed = seed
        self.budget = CallBudget()  # ≤3 attempts including retries for this workflow (§3.2)
        self._call1_done = False
        self._call2_done = False  # one logical call of each kind per workflow (§5.1)

    def _check(self, facts: AccountFacts) -> str | None:
        if facts.account_id != self.account_id or facts.business_date != self.business_date:
            raise ContractViolation("facts belong to a different workflow")
        if facts.kill_switch:
            return "kill_switch"  # §8: no attempt is spent under the kill switch
        if not facts.candidates:
            return "no_candidates"  # SC7: no attempt is spent on an ineligible account
        return None

    def propose_interpretation(
        self, conn: Connection, facts: AccountFacts, message: InboundMessage, *, now: datetime
    ) -> CallResult:
        if self._call1_done:
            raise BudgetMisuse("call 1 already made for this workflow")
        self._call1_done = True
        skip = self._check(facts)
        if skip is not None:
            return CallResult(None, None, None, skip, 0)
        proposal_id, trace_id = new_id(), new_id()
        request, source_text = build_interpretation_request(
            facts, message, correlation_id=proposal_id, trace_id=trace_id, seed=self.seed
        )
        response = self.provider.complete_structured(request, self.budget)
        proposal = map_response(
            response,
            request,
            kind=ProposalKind.INTERPRETATION,
            source_text=source_text,
            account_id=facts.account_id,
            business_date=facts.business_date,
            invoice_hint=None,  # account-level scope; invoice_refs are hints resolved by the validator (SC1)
            created_at=now,
        )
        record_agent_proposal(conn, proposal)
        conn.commit()
        record = _telemetry(response, request, proposal)
        return CallResult(proposal, source_text, response.status, None, response.attempts, record)

    def propose_action(self, conn: Connection, facts: AccountFacts, *, call1: Call1Gate, now: datetime) -> CallResult:
        if self._call2_done:
            raise BudgetMisuse("call 2 already made for this workflow")
        self._call2_done = True
        if isinstance(call1, Failed):
            return CallResult(None, None, None, "call1_failed", 0)  # case C: no attempt, L1 downstream
        skip = self._check(facts)
        if skip is not None:
            return CallResult(None, None, None, skip, 0)
        proposal_id, trace_id = new_id(), new_id()
        request, source_text = build_action_request(
            facts,
            interpretation=call1.normalized if isinstance(call1, Passed) else None,
            correlation_id=proposal_id,
            trace_id=trace_id,
            seed=self.seed,
        )
        response = self.provider.complete_structured(request, self.budget)
        proposal = map_response(
            response,
            request,
            kind=ProposalKind.ACTION_PROPOSAL,
            source_text=source_text,
            account_id=facts.account_id,
            business_date=facts.business_date,
            invoice_hint=facts.candidates[0].invoice_id,  # SC2 primary as the scope hint
            created_at=now,
        )
        record_agent_proposal(conn, proposal)
        conn.commit()
        record = _telemetry(response, request, proposal)
        return CallResult(proposal, source_text, response.status, None, response.attempts, record)
