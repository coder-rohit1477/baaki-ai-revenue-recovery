"""SUT boundary: compatibility matrix, inputs, deterministic ids, shared kernel execution (D-2b2-G2-2, LOCKED)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final, Protocol
from uuid import UUID

from baaki.contracts.action_choice import ActionChoice, DecisionContext
from baaki.contracts.candidate import AccountFacts
from baaki.contracts.policy_decision import ExecutableDecision, NonExecutableDecision
from baaki.domain.enums import ActionType, Arm, Channel, DegradationLevel
from baaki.policy.kernel.decide import decide
from baaki.policy.kernel.target import select_target
from baaki.policy.ruleset import Ruleset
from baaki.policy.snapshot import build_snapshot
from eval.hashing import ROOT, file_hash
from eval.profiles import det_id
from eval.records import ActualRecord, ChoiceStage, KernelStage
from eval.schema import FinalEffect

RULES_SUT: Final[str] = "rules.v1"
CHAIN_SUT: Final[str] = "chain.v1"
VALID_CELLS: Final[frozenset[tuple[str, Arm]]] = frozenset(
    {(RULES_SUT, Arm.CONTROL), (RULES_SUT, Arm.RULES_ONLY), (CHAIN_SUT, Arm.TREATMENT)}
)
OUTBOUND: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.SEND_REMINDER,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.PROPOSE_INSTALLMENT_PLAN,
        ActionType.REQUEST_DISPUTE_DETAILS,
        ActionType.ESCALATE_TO_HUMAN,
    }
)
# production files whose bytes define each SUT's version (hashed; recorded on every ActualRecord)
SUT_FILES: Final[dict[str, tuple[str, ...]]] = {
    RULES_SUT: (
        "src/baaki/rules_agent/interpreter.py",
        "src/baaki/rules_agent/restriction.py",
        "src/baaki/rules_agent/tree.py",
        "src/baaki/policy/validate/normalize.py",
        "src/baaki/policy/arms/rules_only.py",
        "src/baaki/policy/arms/control.py",
        "src/baaki/policy/kernel/decide.py",
        "config/policy.v1.toml",
    ),
    CHAIN_SUT: (
        "src/baaki/agent/context.py",
        "src/baaki/agent/mapping.py",
        "src/baaki/agent/prompts/interp.v1.txt",
        "src/baaki/agent/prompts/propose.v1.txt",
        "src/baaki/policy/validate/ladder.py",
        "src/baaki/policy/validate/normalize.py",
        "src/baaki/policy/arms/treatment.py",
        "src/baaki/policy/arms/rules_only.py",
        "src/baaki/rules_agent/tree.py",
        "src/baaki/rules_agent/interpreter.py",
        "src/baaki/policy/kernel/decide.py",
        "eval/sut/classify.py",
        "config/policy.v1.toml",
    ),
}


class SutArmIncompatible(Exception):
    """Deterministic refusal of an invalid SUT × arm cell. Raised before any stage runs."""

    def __init__(self, sut_id: str, arm: Arm) -> None:
        super().__init__(f"SUT {sut_id!r} does not support arm {arm}")
        self.sut_id, self.arm = sut_id, arm


def check_compatible(sut_id: str, arm: Arm) -> None:
    if (sut_id, arm) not in VALID_CELLS:
        raise SutArmIncompatible(sut_id, arm)


def arms_for(sut_id: str) -> list[Arm]:
    """`--arm all` expands to the valid cells only, in a fixed order."""
    return [arm for arm in (Arm.CONTROL, Arm.RULES_ONLY, Arm.TREATMENT) if (sut_id, arm) in VALID_CELLS]


def sut_version(sut_id: str, root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for rel in SUT_FILES[sut_id]:
        h.update(rel.encode())
        h.update(file_hash(root / rel).encode())
    return h.hexdigest()


@dataclass(frozen=True)
class SutInputs:
    """Everything an SUT may see. Inputs only — never an expectation."""

    item_id: str
    text: str
    anchor: date
    date_span: str | None = None  # the annotated raw span, supplied as INPUT for the grammar-only row
    amount_span: str | None = None
    scripted_output: dict[str, Any] | None = None  # chain: the scripted model output for this item


@dataclass
class StageClock:
    stages_ns: dict[str, int] = field(default_factory=dict)
    total_ns: int = 0

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter_ns()
        try:
            yield
        finally:
            dt = time.perf_counter_ns() - t0
            self.stages_ns[name] = self.stages_ns.get(name, 0) + dt
            self.total_ns += dt


class SutDriver(Protocol):
    @property
    def sut_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def run_item(self, inputs: SutInputs, facts: AccountFacts, arm: Arm, ruleset: Ruleset) -> ActualRecord: ...


def choose_target(facts: AccountFacts, resolved: list[UUID], hint: UUID | None) -> UUID | None:
    return select_target(facts.candidate_ids, resolved, hint)


def ids_for(item_id: str, arm: Arm) -> tuple[UUID, UUID]:
    return det_id("g2", item_id, str(arm), "trace"), det_id("g2", item_id, str(arm), "action")


def run_kernel(
    choice: ActionChoice,
    facts: AccountFacts,
    target: UUID,
    ruleset: Ruleset,
    *,
    arm: Arm,
    level: DegradationLevel,
    item_id: str,
    proposal_id: UUID | None = None,
    validation_id: UUID | None = None,
    rejected_ambiguous: bool = False,
) -> tuple[KernelStage, FinalEffect]:
    trace_id, action_id = ids_for(item_id, arm)
    ctx = DecisionContext(
        trace_id=trace_id,
        arm=arm,
        degradation_level=level,
        proposal_id=proposal_id,
        validation_id=validation_id,
        business_date=facts.business_date,
        rejected_ambiguous=rejected_ambiguous,
        action_id=action_id,
    )
    snapshot = build_snapshot(facts, target, ruleset)
    decision = decide(choice, snapshot, ruleset, ctx, org_timezone=facts.timezone)
    return _kernel_stage(decision, facts, target, level)


def _kernel_stage(
    decision: ExecutableDecision | NonExecutableDecision, facts: AccountFacts, target: UUID, level: DegradationLevel
) -> tuple[KernelStage, FinalEffect]:
    contactable = {c.contact_id: c.channel for c in facts.contactable}
    if isinstance(decision, ExecutableDecision):
        payload: Any = decision.canonical_payload
        contact_id = getattr(payload, "contact_id", None)
        channel: Channel | None = getattr(payload, "channel", None)
        amount = getattr(payload, "amount_paise", None)
        if amount is None and hasattr(payload, "parts"):
            amount = sum(int(p.amount_paise) for p in payload.parts)
        reason = getattr(payload, "reason_code", None)
        stage = KernelStage(
            verdict=str(decision.verdict),
            action=decision.action_type,
            tier=decision.tier,
            suppress_reason=str(reason) if decision.action_type is ActionType.SUPPRESS and reason else None,
            escalation_reason=str(reason) if decision.action_type is ActionType.ESCALATE_TO_HUMAN and reason else None,
            degradation_level=level,
            out_channel=channel,
            out_contact_ok=(contact_id in contactable) if contact_id is not None else None,
            amount_paise=int(amount) if amount is not None else None,
            target_is_candidate=target in set(facts.candidate_ids),
        )
        if decision.action_type is ActionType.SUPPRESS:
            effect = FinalEffect.NO_EFFECT_SUPPRESS
        elif str(decision.verdict) == "REQUIRE_APPROVAL":
            effect = FinalEffect.PENDING_APPROVAL_NO_OUTBOX
        else:
            effect = FinalEffect.TEMPLATED_OUTBOUND_WITHIN_POLICY
        return stage, effect
    rule = decision.blocking_rules[0]["rule_id"] if decision.blocking_rules else None
    stage = KernelStage(
        verdict=str(decision.verdict),
        blocking_rule=rule,
        degradation_level=level,
        target_is_candidate=target in set(facts.candidate_ids),
    )
    return stage, FinalEffect.BLOCKED


def choice_stage(choice: ActionChoice, facts: AccountFacts) -> ChoiceStage:
    channel = choice.channel
    if channel is None and choice.contact_id is not None:
        channel = next((c.channel for c in facts.contactable if c.contact_id == choice.contact_id), None)
    return ChoiceStage(action=choice.action, origin=choice.origin, contact_channel=channel)


def guarded(fn: Callable[[], None], clock: StageClock, name: str) -> BaseException | None:
    """Run one stage under the clock; return the exception instead of raising (faults are data)."""
    try:
        with clock.stage(name):
            fn()
        return None
    except Exception as e:  # noqa: BLE001 — every SUT exception becomes a FaultRecord
        return e
