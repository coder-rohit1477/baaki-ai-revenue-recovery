"""chain.v1 — the complete offline agent → validator → arm → kernel chain driven by a scripted model output
(D-2b2-G2-1/G2-2, LOCKED): TREATMENT arm only. Mirrors the production pipeline's case A/B/C composition."""

from __future__ import annotations

from typing import Any

from baaki.agent.context import InboundMessage, build_action_request, build_interpretation_request
from baaki.agent.mapping import map_response
from baaki.contracts.candidate import AccountFacts
from baaki.contracts.normalized_action import NormalizedActionProposal
from baaki.contracts.validation_input import ValidationInput
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.domain.enums import ActionType, Arm, DegradationLevel, ProposalKind, ValidationOutcome
from baaki.policy.arms import rules_only, treatment
from baaki.policy.ruleset import Ruleset
from baaki.policy.validate import validate
from baaki.providers.llm.base import CallBudget, ProviderStatus
from baaki.providers.llm.fixtures import FixtureProvider, Script, fault, ok
from baaki.rules_agent.interpreter import interpret
from eval.records import (
    ActualRecord,
    ChoiceStage,
    FaultKind,
    FaultRecord,
    KernelStage,
    LatencyRecord,
    ProposalStage,
    ValidatorStage,
)
from eval.schema import FinalEffect, StoppingLayer
from eval.sut.base import (
    CHAIN_SUT,
    StageClock,
    SutInputs,
    check_compatible,
    choice_stage,
    choose_target,
    guarded,
    ids_for,
    run_kernel,
    sut_version,
)
from eval.sut.classify import CLASSIFIER_VERSION, classify

FAULT_KEY = "__status__"  # a scripted output {"__status__": "TIMEOUT", "__text__": "..."} simulates a provider fault


def _script(scripted: dict[str, Any]) -> tuple[Script, ProposalKind]:
    if FAULT_KEY in scripted:
        status = ProviderStatus(str(scripted[FAULT_KEY]))
        outcome = fault(status, text=scripted.get("__text__"), latency_ms=int(scripted.get("__latency_ms__", 10)))
        kind = (
            ProposalKind.INTERPRETATION
            if scripted.get("__kind__", "INTERPRETATION") == "INTERPRETATION"
            else ProposalKind.ACTION_PROPOSAL
        )
        return Script(outcomes=(outcome, outcome)), kind
    kind = ProposalKind.INTERPRETATION if "intent" in scripted else ProposalKind.ACTION_PROPOSAL
    return Script(outcomes=(ok(scripted),)), kind


class ChainSut:
    def __init__(self) -> None:
        self._version = sut_version(CHAIN_SUT)

    @property
    def sut_id(self) -> str:
        return CHAIN_SUT

    @property
    def version(self) -> str:
        return self._version

    def run_item(self, inputs: SutInputs, facts: AccountFacts, arm: Arm, ruleset: Ruleset) -> ActualRecord:
        check_compatible(self.sut_id, arm)
        clock = StageClock()
        if inputs.scripted_output is None:
            return self._record(
                inputs,
                arm,
                clock,
                fault=FaultRecord(stage="proposal", kind=FaultKind.MISSING_SCRIPT, detail_class="no_scripted_output"),
            )
        if not facts.candidates:
            return self._record(inputs, arm, clock, effect=FinalEffect.INELIGIBLE)

        script, kind = _script(inputs.scripted_output)
        pid, tid = ids_for(inputs.item_id, arm)
        state: dict[str, Any] = {}

        def _propose() -> None:
            provider = FixtureProvider(default=script)
            if kind is ProposalKind.INTERPRETATION:
                request, source_text = build_interpretation_request(
                    facts, InboundMessage(text=inputs.text, received_at=facts.as_of), correlation_id=pid, trace_id=tid
                )
                hint = None
            else:
                request, source_text = build_action_request(
                    facts, interpretation=None, correlation_id=pid, trace_id=tid
                )
                hint = facts.candidates[0].invoice_id
            response = provider.complete_structured(request, CallBudget())
            proposal = map_response(
                response,
                request,
                kind=kind,
                source_text=source_text,
                account_id=facts.account_id,
                business_date=facts.business_date,
                invoice_hint=hint,
                created_at=facts.as_of,
            )
            cls, reasons = classify(inputs.scripted_output, facts)
            state.update(
                proposal=proposal,
                source_text=source_text,
                fixture_latency=response.latency_ms,
                proposal_stage=ProposalStage(
                    kind=str(kind),
                    parse_status=str(proposal.parse_status),
                    harness_classification=cls,
                    classification_reasons=reasons,
                    classifier_version=CLASSIFIER_VERSION,
                ),
            )

        exc = guarded(_propose, clock, "proposal")
        if exc is not None:
            return self._record(
                inputs,
                arm,
                clock,
                fault=FaultRecord(stage="proposal", kind=FaultKind.SUT_EXCEPTION, detail_class=type(exc).__name__),
            )
        proposal = state["proposal"]

        def _validate() -> None:
            bundle = validate(
                ValidationInput(proposal=proposal, source_text=state["source_text"], facts=facts),
                ruleset,
                now=facts.as_of,
            )
            flags = sorted(
                {
                    str(c["reason"])
                    for c in bundle.result.checks_run
                    if c["class"] == "SOFT" and not c["passed"] and c["reason"]
                }
            )
            state.update(
                bundle=bundle,
                validator_stage=ValidatorStage(
                    outcome=str(bundle.result.outcome),
                    reasons=[str(r) for r in bundle.result.rejection_reasons],
                    validator_flags=flags,
                ),
            )

        exc = guarded(_validate, clock, "validator")
        if exc is not None:
            return self._record(
                inputs,
                arm,
                clock,
                proposal_stage=state["proposal_stage"],
                fixture_latency=state["fixture_latency"],
                fault=FaultRecord(stage="validator", kind=FaultKind.SUT_EXCEPTION, detail_class=type(exc).__name__),
            )
        bundle = state["bundle"]
        target = choose_target(facts, bundle.resolved_invoice_ids, proposal.invoice_id)
        if target is None:
            return self._record(
                inputs,
                arm,
                clock,
                proposal_stage=state["proposal_stage"],
                validator_stage=state["validator_stage"],
                fixture_latency=state["fixture_latency"],
                effect=FinalEffect.INELIGIBLE,
            )
        target_c = facts.candidate(target)
        assert target_c is not None

        def _decide() -> None:
            passed = bundle.result.outcome is ValidationOutcome.PASS
            normalized = bundle.result.normalized
            choice = None
            level = DegradationLevel.L1
            if kind is ProposalKind.ACTION_PROPOSAL and passed and isinstance(normalized, NormalizedActionProposal):
                l0 = treatment.choose(normalized, ruleset)
                if l0 is not None:
                    choice, level = l0, DegradationLevel.L0
            if choice is None:
                interpretation = (
                    normalized
                    if (
                        kind is ProposalKind.INTERPRETATION
                        and passed
                        and isinstance(normalized, NormalizedInterpretation)
                    )
                    else interpret(inputs.text, inputs.anchor)
                )
                choice = rules_only.choose(facts, target_c, ruleset, interpretation)
            linked = kind is ProposalKind.ACTION_PROPOSAL
            with clock.stage("kernel"):
                kernel_st, effect = run_kernel(
                    choice,
                    facts,
                    target,
                    ruleset,
                    arm=arm,
                    level=level,
                    item_id=inputs.item_id,
                    proposal_id=proposal.proposal_id if linked else None,
                    validation_id=bundle.result.validation_id if linked else None,
                    rejected_ambiguous=bundle.rejected_ambiguous,
                )
            state.update(choice_stage=choice_stage(choice, facts), kernel_stage=kernel_st, effect=effect, level=level)

        exc = guarded(_decide, clock, "choice")
        if exc is not None:
            return self._record(
                inputs,
                arm,
                clock,
                proposal_stage=state["proposal_stage"],
                validator_stage=state["validator_stage"],
                fixture_latency=state["fixture_latency"],
                fault=FaultRecord(
                    stage="choice_or_kernel", kind=FaultKind.SUT_EXCEPTION, detail_class=type(exc).__name__
                ),
            )
        return self._record(
            inputs,
            arm,
            clock,
            proposal_stage=state["proposal_stage"],
            validator_stage=state["validator_stage"],
            choice_st=state["choice_stage"],
            kernel_st=state["kernel_stage"],
            effect=state["effect"],
            stopping=_stopping(state, inputs.scripted_output),
            fixture_latency=state["fixture_latency"],
        )

    def _record(  # type: ignore[no-untyped-def]
        self,
        inputs,
        arm,
        clock,
        *,
        proposal_stage=None,
        validator_stage=None,
        choice_st=None,
        kernel_st=None,
        effect=None,
        stopping=None,
        fault=None,
        fixture_latency=None,
    ) -> ActualRecord:
        return ActualRecord(
            item_id=inputs.item_id,
            sut_id=self.sut_id,
            sut_version=self.version,
            arm=arm,
            proposal=proposal_stage,
            validator=validator_stage,
            choice=choice_st,
            kernel=kernel_st,
            final_effect=effect,
            stopping_layer_observed=stopping,
            fault=fault,
            latency=LatencyRecord(
                total_ns=clock.total_ns, stages_ns=dict(clock.stages_ns), fixture_latency_ms=fixture_latency
            ),
        )


def _stopping(state: dict[str, Any], scripted: dict[str, Any]) -> StoppingLayer:
    ps: ProposalStage = state["proposal_stage"]
    vs: ValidatorStage = state["validator_stage"]
    ks: KernelStage = state["kernel_stage"]
    cs: ChoiceStage = state["choice_stage"]
    if ps.parse_status == "SCHEMA_VIOLATION":
        return StoppingLayer.CONTRACT_A3_A4
    if vs.outcome == "REJECT":
        return StoppingLayer.VALIDATOR_CHECK
    if ks.verdict == "BLOCK":
        return StoppingLayer.KERNEL_LADDER
    requested = scripted.get("action")
    if requested is not None and (
        str(cs.action) != str(requested) or (ks.verdict == "REQUIRE_APPROVAL" and ks.tier == 2)
    ):
        return StoppingLayer.KERNEL_TIER_CAP
    if requested is not None and ks.action is not None and str(ks.action) != str(requested):
        return StoppingLayer.KERNEL_TIER_CAP
    if ks.action is ActionType.SUPPRESS and requested is None and scripted.get("intent") is not None:
        return StoppingLayer.NONE_NEEDED
    return StoppingLayer.NONE_NEEDED
