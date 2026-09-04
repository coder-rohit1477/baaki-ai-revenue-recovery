"""PolicyDecision — the only financial authority object (§1.4, §6.7, §6.10).

Discriminated union: ExecutableDecision (ALLOW / REQUIRE_APPROVAL) carries action_type and
canonical_payload; NonExecutableDecision (BLOCK / DEFER) carries neither. P3a/P3b are therefore
unsatisfiable to violate in Python.

Construction requires KERNEL_TOKEN (layer 1 of §6.10). Only policy.kernel and tests/ may import
it (tests/arch/test_import_graph.py). This is a convention with teeth, not the security boundary —
the database FK/trigger/role layers are (§6.17).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from baaki.contracts.canonical_payload import CanonicalPayload
from baaki.domain.enums import ActionType, Arm, DegradationLevel, Verdict
from baaki.domain.errors import ContractViolation


class _KernelToken:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<KERNEL_TOKEN>"


KERNEL_TOKEN: Final[_KernelToken] = _KernelToken()
_TOKEN_CTX: Final[str] = "_kernel_token"


class _DecisionBase(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    decision_id: UUID
    trace_id: UUID
    proposal_id: UUID | None = None
    validation_id: UUID | None = None
    arm: Arm
    account_id: UUID
    invoice_id: UUID
    business_date: date
    tier: Literal[0, 1, 2]
    matched_rules: list[str] = Field(default_factory=list)
    blocking_rules: list[dict[str, Any]] = Field(default_factory=list)
    effective_confidence: float | None = Field(default=None, ge=0, le=1)
    policy_version: str = Field(min_length=1)
    kernel_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=64, max_length=64)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    degradation_level: DegradationLevel
    decided_at: datetime

    def __init__(self, *, _token: object = None, **data: Any) -> None:
        # Route through the validator with the token in context so model_validate() paths and
        # __init__ paths are guarded identically.
        self.__pydantic_validator__.validate_python(
            data, self_instance=self, context={_TOKEN_CTX: _token}
        )

    @model_validator(mode="before")
    @classmethod
    def _require_token(cls, data: Any, info: ValidationInfo) -> Any:
        ctx = info.context or {}
        if ctx.get(_TOKEN_CTX) is not KERNEL_TOKEN:
            raise ContractViolation("PolicyDecision may only be constructed by the kernel (§6.10)")
        return data

    @model_validator(mode="after")
    def _shared_invariants(self) -> _DecisionBase:
        if (self.proposal_id is None) != (self.validation_id is None):  # ck_proposal_paired
            raise ContractViolation("proposal_id and validation_id must be both set or both null")
        if self.arm is not Arm.TREATMENT and self.proposal_id is not None:  # P7
            raise ContractViolation("non-TREATMENT arms cannot carry a proposal (P7)")
        return self


class ExecutableDecision(_DecisionBase):
    verdict: Literal[Verdict.ALLOW, Verdict.REQUIRE_APPROVAL]
    action_type: ActionType
    canonical_payload: CanonicalPayload
    defer_until: None = None

    @model_validator(mode="after")
    def _executable_invariants(self) -> ExecutableDecision:
        if self.canonical_payload.action_type is not self.action_type:
            raise ContractViolation("canonical_payload.action_type must equal action_type")
        if (self.tier == 2) != (self.verdict is Verdict.REQUIRE_APPROVAL):  # P5 (and its converse)
            raise ContractViolation("tier 2 iff REQUIRE_APPROVAL (P5)")
        if self.blocking_rules:
            raise ContractViolation("executable decisions carry no blocking_rules")
        return self


class NonExecutableDecision(_DecisionBase):
    verdict: Literal[Verdict.BLOCK, Verdict.DEFER]
    action_type: None = None
    canonical_payload: None = None
    defer_until: datetime | None = None

    @model_validator(mode="after")
    def _non_executable_invariants(self) -> NonExecutableDecision:
        if self.verdict is Verdict.BLOCK and not self.blocking_rules:  # P2
            raise ContractViolation("BLOCK requires blocking_rules (P2)")
        if (self.verdict is Verdict.DEFER) != (self.defer_until is not None):  # P8
            raise ContractViolation("DEFER iff defer_until (P8)")
        if self.tier == 2:
            raise ContractViolation("tier 2 implies REQUIRE_APPROVAL (P5)")
        return self


PolicyDecision = Annotated[
    ExecutableDecision | NonExecutableDecision, Field(discriminator="verdict")
]


def as_executable(decision: ExecutableDecision | NonExecutableDecision) -> ExecutableDecision:
    """The only narrowing path from the union (§6.10 layer 3)."""
    if isinstance(decision, ExecutableDecision):
        return decision
    raise ContractViolation(f"decision {decision.decision_id} is not executable ({decision.verdict})")
