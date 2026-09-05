"""The three judge scenarios, each driven through the real Phase 2b-4 composition entrypoint.

No scenario bypasses the validator or the kernel. The only thing a scenario chooses is what the *model*
says — live, or (for the hostile case) a scripted reply that stands in for a compromised provider. What
happens next is decided entirely by committed deterministic code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine

from baaki.agent.context import InboundMessage
from baaki.pipeline.run import Decided
from baaki.policy.ruleset import DEFAULT_RULESET_PATH, load_ruleset
from baaki.providers.llm.base import AiProviderPort
from baaki.providers.llm.openai_provider import LOCKED_MODEL_ID, OpenAIProvider
from baaki.providers.llm.transport import TransportError, TransportOutcome, UrllibTransport
from baaki.scripts.run_treatment_day import CompositionResult, run_treatment_day

RULESET = load_ruleset(DEFAULT_RULESET_PATH)

SCENARIOS: dict[str, dict[str, str]] = {
    "A": {
        "title": "Successful recovery",
        "subtitle": "Debtor promises a part payment. AI reads it, policy accepts, money arrives, chasing stops.",
        "message": "Bhai abhi 10k de sakta hu. Baki 15k 25 September ko kar dunga.",
        "mode": "live",
    },
    "B": {
        "title": "Unsafe AI proposal",
        "subtitle": "A compromised model tries to move money. The deterministic validator refuses.",
        "message": "Sirf 40% discount do to aaj hi full and final settle kar deta hu.",
        "mode": "hostile",
    },
    "D": {
        "title": "Needs human approval",
        "subtitle": "The customer asks for a payment plan. The kernel rules that tier 2, and refuses to run it.",
        "message": "Sir cash flow tight hai. Kya hum ise 3 instalments mein baant sakte hain?",
        "mode": "approval",
    },
    "C": {
        "title": "Customer must not be chased",
        "subtitle": "An opt-out is recognised and pursuit stops, without escalation.",
        "message": "Stop messaging me. Remove my number from your list, I do not want any more reminders.",
        "mode": "live",
    },
}

APPROVAL_INTERPRETATION: dict[str, Any] = {
    "intent": "REQUEST_INSTALLMENTS",
    "promised_date_raw": None,
    "promised_amount_raw": None,
    "invoice_refs": [],
    "contact_correction": None,
    "sentiment": "COOPERATIVE",
    "confidence": 0.92,
    "evidence": [{"field": "intent", "quote": "3 instalments"}],
}


# What a compromised or jailbroken provider might try to return: a forged money field the schema never
# defines. The demo never lets this decide anything — it exists to be rejected, on camera.
HOSTILE_INTERPRETATION: dict[str, Any] = {
    "intent": "WILL_PAY_ON_DATE",
    "promised_date_raw": "today",
    "promised_amount_raw": "40% discount agreed",
    "invoice_refs": [],
    "contact_correction": None,
    "sentiment": "COOPERATIVE",
    "confidence": 0.97,
    "evidence": [{"field": "promised_amount_raw", "quote": "40% discount"}],
    "settlement_amount_paise": 1_512_000,
    "discount_percent": 40,
    "mark_paid": True,
}


class ScriptedTransport:
    """Stands in for the socket when a scenario needs a specific (hostile) model reply."""

    def __init__(self, *outcomes: TransportOutcome) -> None:
        self.outcomes = list(outcomes)

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float):
        return self.outcomes.pop(0) if self.outcomes else TransportOutcome(error=TransportError.UNAVAILABLE)


def _reply(body: Any) -> TransportOutcome:
    env = {"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(body)}}]}
    return TransportOutcome(status_code=200, body=json.dumps(env).encode(), headers={"x-request-id": "demo"})


@dataclass(frozen=True)
class RunReport:
    scenario: str
    live: bool
    model_id: str
    interpretation: dict[str, Any] | None
    parse_status: str | None
    validation_outcome: str | None
    rejection_reasons: list[str]
    verdict: str | None
    action_type: str | None
    tier: int | None
    blocking_rules: list[str]
    degradation_level: str | None
    telemetry: list[dict[str, Any]]
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


def provider_for(mode: str, credential: SecretStr | None) -> tuple[AiProviderPort, bool]:
    """(provider, is_live). Falls back to a scripted safe reply when no credential is present."""
    if mode == "approval":
        # Scripted so the tier-2 beat is reliable for a judge. The proposal itself is ordinary; the kernel
        # is what makes it require approval.
        return OpenAIProvider(SecretStr("sk-demo"),
                              transport=ScriptedTransport(_reply(APPROVAL_INTERPRETATION))), False
    if mode == "hostile":
        return OpenAIProvider(SecretStr("sk-demo"),
                              transport=ScriptedTransport(_reply(HOSTILE_INTERPRETATION))), False
    if credential is None:
        # No key: the workflow degrades to the deterministic path. Nothing crashes; the judge still sees a decision.
        return OpenAIProvider(None, transport=ScriptedTransport()), False
    return OpenAIProvider(credential, transport=UrllibTransport()), True


def run(
    *,
    engine_app: Engine,
    engine_agent: Engine,
    account_id: UUID,
    contact_id: UUID,
    scenario: str,
    credential: SecretStr | None,
) -> RunReport:
    spec = SCENARIOS[scenario]
    provider, live = provider_for(spec["mode"], credential)
    now = datetime.now(UTC)

    result: CompositionResult = run_treatment_day(
        engine_app=engine_app,
        engine_agent=engine_agent,
        provider=provider,
        account_id=account_id,
        as_of=now,
        ruleset=RULESET,
        message=InboundMessage(text=spec["message"], received_at=now),
        inbound_contact_id=contact_id,
    )

    first = result.proposals[0][0] if result.proposals else None
    records = [r.as_log_fields() for r in result.records]
    outcome = result.outcome
    decision = outcome.decision if isinstance(outcome, Decided) else None

    if spec["mode"] == "approval":
        note = ("The customer asked for a payment plan. The model proposed an in-catalogue action; the policy "
                "kernel classified it as tier 2 and refused to run it autonomously — it is now awaiting an "
                "operator decision in Approvals.")
    elif spec["mode"] == "hostile":
        note = ("SIMULATED HOSTILE MODEL OUTPUT — a forged settlement amount, discount percentage and mark-paid flag "
                "were injected into the provider reply. What rejects them is the committed validator, not the demo.")
    elif live:
        note = "Interpretation produced by a live call to the locked model."
    else:
        note = ("No OPENAI_API_KEY present, so no live call was made. The workflow degraded to the deterministic "
                "rules path exactly as it would on a provider outage — which is the point: it still decided safely.")
    return RunReport(
        scenario=scenario,
        live=live,
        model_id=LOCKED_MODEL_ID,
        interpretation=first.parsed if first is not None else None,
        parse_status=str(first.parse_status) if first is not None else None,
        validation_outcome=records[0].get("validation_outcome") if records else None,
        rejection_reasons=records[0].get("rejection_reasons") or [] if records else [],
        verdict=str(decision.verdict) if decision is not None else None,
        action_type=str(getattr(decision, "action_type", "") or "") or None,
        tier=getattr(decision, "tier", None),
        blocking_rules=[str(b) for b in getattr(decision, "blocking_rules", []) or []],
        degradation_level=result.degradation_level,
        telemetry=records,
        note=note,
    )
