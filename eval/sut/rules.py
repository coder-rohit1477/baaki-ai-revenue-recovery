"""rules.v1 — the deterministic interpretation SUT (D-2b2-G2-2, LOCKED): CONTROL and RULES_ONLY arms."""

from __future__ import annotations

from baaki.contracts.candidate import AccountFacts
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.domain.enums import Arm, DegradationLevel
from baaki.policy.arms import control, rules_only
from baaki.policy.ruleset import Ruleset
from baaki.policy.validate.normalize import parse_amount, parse_date
from baaki.rules_agent.interpreter import classify_intent, interpret
from baaki.rules_agent.restriction import detect
from eval.records import (
    ActualRecord,
    ChoiceStage,
    FaultKind,
    FaultRecord,
    InterpretationStage,
    KernelStage,
    LatencyRecord,
)
from eval.schema import FinalEffect, SchemaIntent
from eval.sut.base import (
    RULES_SUT,
    StageClock,
    SutInputs,
    check_compatible,
    choice_stage,
    choose_target,
    guarded,
    run_kernel,
    sut_version,
)


class RulesSut:
    def __init__(self) -> None:
        self._version = sut_version(RULES_SUT)

    @property
    def sut_id(self) -> str:
        return RULES_SUT

    @property
    def version(self) -> str:
        return self._version

    def run_item(self, inputs: SutInputs, facts: AccountFacts, arm: Arm, ruleset: Ruleset) -> ActualRecord:
        check_compatible(self.sut_id, arm)
        clock = StageClock()
        interp_stage: InterpretationStage | None = None
        normalized: NormalizedInterpretation | None = None
        fault: FaultRecord | None = None
        choice_st: ChoiceStage | None = None
        kernel_st: KernelStage | None = None
        effect: FinalEffect | None = None

        def _interpret() -> None:
            nonlocal interp_stage, normalized
            intent = classify_intent(inputs.text)
            match = detect(inputs.text)
            normalized = interpret(inputs.text, inputs.anchor)
            g_date = parse_date(inputs.date_span, inputs.anchor) if inputs.date_span is not None else None
            g_amt = parse_amount(inputs.amount_span) if inputs.amount_span is not None else None
            interp_stage = InterpretationStage(
                intent=SchemaIntent(str(intent)),
                detector_pattern=match.matched_pattern_id if match else None,
                date_value=normalized.promised_date,
                amount_paise=int(normalized.promised_paise) if normalized.promised_paise is not None else None,
                grammar_date_value=g_date.value if g_date is not None and g_date.status == "ok" else None,
                grammar_date_abstain=(g_date.status != "ok") if g_date is not None else None,
                grammar_amount_paise=int(g_amt.value)
                if g_amt is not None and g_amt.status == "ok" and g_amt.value
                else None,
                grammar_amount_abstain=(g_amt.status != "ok") if g_amt is not None else None,
            )

        exc = guarded(_interpret, clock, "interpretation")
        if exc is not None:
            fault = FaultRecord(stage="interpretation", kind=FaultKind.SUT_EXCEPTION, detail_class=type(exc).__name__)
            return self._record(inputs, arm, clock, interp_stage, None, None, None, fault)

        if not facts.candidates:
            return self._record(inputs, arm, clock, interp_stage, None, None, FinalEffect.INELIGIBLE, None)

        target = choose_target(facts, [], None)
        assert target is not None
        target_c = facts.candidate(target)
        assert target_c is not None

        def _choose() -> None:
            nonlocal choice_st, kernel_st, effect
            if arm is Arm.CONTROL:
                choice, level = control.choose(facts, target_c, ruleset), DegradationLevel.L2
            else:
                choice, level = rules_only.choose(facts, target_c, ruleset, normalized), DegradationLevel.L1
            choice_st = choice_stage(choice, facts)
            with clock.stage("kernel"):
                kernel_st, effect = run_kernel(
                    choice, facts, target, ruleset, arm=arm, level=level, item_id=inputs.item_id
                )

        exc = guarded(_choose, clock, "choice")
        if exc is not None:
            fault = FaultRecord(stage="choice_or_kernel", kind=FaultKind.SUT_EXCEPTION, detail_class=type(exc).__name__)
        return self._record(inputs, arm, clock, interp_stage, choice_st, kernel_st, effect, fault)

    def _record(
        self,
        inputs: SutInputs,
        arm: Arm,
        clock: StageClock,
        interp: InterpretationStage | None,
        choice_st: ChoiceStage | None,
        kernel_st: KernelStage | None,
        effect: FinalEffect | None,
        fault: FaultRecord | None,
    ) -> ActualRecord:
        return ActualRecord(
            item_id=inputs.item_id,
            sut_id=self.sut_id,
            sut_version=self.version,
            arm=arm,
            interpretation=interp,
            choice=choice_st,
            kernel=kernel_st,
            final_effect=effect,
            fault=fault,
            latency=LatencyRecord(total_ns=clock.total_ns, stages_ns=dict(clock.stages_ns)),
        )
