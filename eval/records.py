"""EXPECTED / ACTUAL / COMPARISON / artefact contracts (G2; PHASE2B2_PLAN §6, D-2b2-G2-2/4/9/10, LOCKED).

Rules: ExpectedRecord is built from the corpus item alone (oracle side) and carries no arm. ActualRecord is built by an
SUT driver alone and carries no oracle field. ComparisonRecord is a pure function of the two plus the profile. Nothing
here imports production decision logic (arch-tested).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from baaki.domain.enums import ActionType, Arm, Channel, DegradationLevel
from eval.schema import (
    AdversarialSpec,
    EvidenceGrade,
    FinalEffect,
    ProposalClassification,
    SafetyOracle,
    SchemaIntent,
    SemanticOracle,
    StoppingLayer,
)

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")
HARNESS_VERSION: Final[str] = "harness.g2.v1"
NOT_MEASURABLE: Final[str] = "NOT_MEASURABLE"


# ── G2-owned CHANNEL_OTHER sidecar (D-2b2-G2-4) ─────────────────────────────────────────────────────────────────
class GapMeta(BaseModel):
    model_config = _STRICT
    item_id: str = Field(pattern=r"^C-[0-9]{6}$")
    gap_id: Literal["GAP-2b2-1"]
    inbound_channel: Channel
    restricted_channel: Channel | None = None
    measurable: bool
    note: str = ""


# ── EXPECTED (oracle side; no arm) ───────────────────────────────────────────────────────────────────────────────
class ExpectedRecord(BaseModel):
    model_config = _STRICT
    item_id: str
    semantic: SemanticOracle
    safety: SafetyOracle
    optout_bucket: str
    adversarial: AdversarialSpec | None = None
    gap: GapMeta | None = None


# ── ACTUAL (SUT side; no oracle field) ───────────────────────────────────────────────────────────────────────────
class FaultKind(StrEnum):
    SUT_EXCEPTION = "SUT_EXCEPTION"
    CONTRACT_BREACH = "CONTRACT_BREACH"
    MISSING_STAGE = "MISSING_STAGE"
    MISSING_SCRIPT = "MISSING_SCRIPT"


class FaultRecord(BaseModel):
    model_config = _STRICT
    stage: str
    kind: FaultKind
    detail_class: str  # exception class name or short token; never a message body


class InterpretationStage(BaseModel):
    model_config = _STRICT
    intent: SchemaIntent | None = None
    detector_pattern: str | None = None
    date_span: str | None = None  # span the interpreter itself selected (end-to-end row)
    date_value: date | None = None
    amount_span: str | None = None
    amount_paise: int | None = None
    grammar_date_value: date | None = None  # grammar-only rows: production grammar applied to the supplied input spans
    grammar_date_abstain: bool | None = None
    grammar_amount_paise: int | None = None
    grammar_amount_abstain: bool | None = None


class ProposalStage(BaseModel):
    model_config = _STRICT
    kind: Literal["INTERPRETATION", "ACTION_PROPOSAL"]
    parse_status: str
    harness_classification: ProposalClassification
    classification_reasons: list[str] = Field(default_factory=list)
    classifier_version: str


class ValidatorStage(BaseModel):
    model_config = _STRICT
    outcome: Literal["PASS", "REJECT"]
    reasons: list[str] = Field(default_factory=list)
    validator_flags: list[str] = Field(default_factory=list)  # SOFT flags actually raised (DATE_IN_PAST, …)


class ChoiceStage(BaseModel):
    model_config = _STRICT
    action: ActionType
    origin: DegradationLevel
    contact_channel: Channel | None = None


class KernelStage(BaseModel):
    model_config = _STRICT
    verdict: Literal["ALLOW", "REQUIRE_APPROVAL", "BLOCK", "DEFER"]
    action: ActionType | None = None
    tier: int | None = None
    blocking_rule: str | None = None
    suppress_reason: str | None = None
    escalation_reason: str | None = None
    degradation_level: DegradationLevel
    out_channel: Channel | None = None
    out_contact_ok: bool | None = None  # payload contact ∈ contactable set (checked by the driver against facts)
    amount_paise: int | None = None  # payload money, if any
    target_is_candidate: bool = True


class LatencyRecord(BaseModel):
    model_config = _STRICT
    total_ns: int = Field(ge=0)
    stages_ns: dict[str, int] = Field(default_factory=dict)
    fixture_latency_ms: int | None = None  # synthetic, from the scripted response


class ActualRecord(BaseModel):
    model_config = _STRICT
    item_id: str
    sut_id: str
    sut_version: str
    arm: Arm
    interpretation: InterpretationStage | None = None
    proposal: ProposalStage | None = None
    validator: ValidatorStage | None = None
    choice: ChoiceStage | None = None
    kernel: KernelStage | None = None
    final_effect: FinalEffect | None = None
    stopping_layer_observed: StoppingLayer | None = None
    fault: FaultRecord | None = None
    latency: LatencyRecord


# ── COMPARISON ───────────────────────────────────────────────────────────────────────────────────────────────────
class InterpretationClass(StrEnum):
    CORRECT_SUBSTANTIVE = "CORRECT_SUBSTANTIVE"
    FALSE_SUBSTANTIVE = "FALSE_SUBSTANTIVE"
    MISSED = "MISSED"
    CORRECT_ABSTENTION = "CORRECT_ABSTENTION"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    FAULT = "FAULT"
    GAP = "GAP"


class FailureClass(StrEnum):
    OK = "OK"
    FAULT = "FAULT"
    PARTIAL = "PARTIAL"


class PtpComparison(BaseModel):
    model_config = _STRICT
    date_match: bool | None = None  # end-to-end (interpreter span selection + grammar)
    date_abstain_match: bool | None = None
    grammar_date_match: bool | None = None  # grammar-only on the supplied span
    grammar_date_abstain_match: bool | None = None
    amount_match: bool | None = None
    amount_abstain_match: bool | None = None
    grammar_amount_match: bool | None = None
    grammar_amount_abstain_match: bool | None = None
    flags_match: bool | None = None
    false_extraction_date: bool | None = None
    false_extraction_amount: bool | None = None


class ComparisonRecord(BaseModel):
    model_config = _STRICT
    item_id: str
    arm: Arm
    sut_id: str
    failure_class: FailureClass
    interpretation_class: InterpretationClass
    intent_match_9: bool | None = None
    family_match_6: bool | None = None
    wrong_contact_tp: bool = False
    wrong_contact_fp: bool = False
    wrong_contact_fn: bool = False
    optout_bucket: str
    optout_pred_interpreter: bool | None = None
    optout_pred_detector: bool | None = None
    optout_pred_union: bool | None = None
    ambiguous_conservative_review: bool | None = None
    ambiguous_treated_as_optout: bool | None = None
    gap_exposure: bool | Literal["NOT_MEASURABLE"] | None = None
    ptp: PtpComparison = Field(default_factory=PtpComparison)
    outcome_match: bool | None = None
    reason_match: bool | None = None
    false_escalation: bool | None = None
    policy_violation: bool = False
    policy_violation_reasons: list[str] = Field(default_factory=list)
    unsafe_proposal: bool | None = None
    unsafe_effect: bool | None = None
    proposal_classification_match: bool | None = None
    validator_match: bool | None = None
    kernel_match: bool | None = None
    final_effect_match: bool | None = None
    stopping_layer_match: bool | None = None
    pair_member_correct: bool | None = None


# ── metrics / gates / artefact ───────────────────────────────────────────────────────────────────────────────────
class MetricValue(BaseModel):
    model_config = _STRICT
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    rule_of_three_lower_bound: float | None = None
    label: Literal["measured", "report-only", "gated", "not-run"]
    note: str | None = None


class GateResult(BaseModel):
    model_config = _STRICT
    name: str
    status: Literal["LOCKED", "CANDIDATE", "INTEGRITY"]
    comparator: Literal[">=", "<=", "=="]
    threshold: float
    value: float | None
    verdict: Literal["PASS", "FAIL", "NOT_EVALUATED"]
    reason: str | None = None
    evaluated_on_split: str


class RunIdentity(BaseModel):
    model_config = _STRICT
    run_id: str
    harness_version: str
    git_commit: str | None
    split: str
    corpus_path: str
    corpus_hash: str
    profiles_hash: str
    config_hash: str
    safety_policy_hash: str
    ruleset_policy_hash: str
    gap_metadata_hash: str
    freeze_manifest: dict[str, str]
    freeze_hash: str
    sut_id: str
    sut_version: str
    arms: list[Arm]
    evidence_grade: EvidenceGrade


class ItemResult(BaseModel):
    model_config = _STRICT
    expected: ExpectedRecord
    actuals: list[ActualRecord]
    comparisons: list[ComparisonRecord]
    known_defect: bool = False  # D-G3-4: item is listed in eval/defects.v1.json (annotation only; never excluded)


class ChainCoverage(BaseModel):
    """What the in-process chain/rules SUT actually executed (always the full split)."""

    model_config = _STRICT
    n_items: int = Field(ge=0)
    n_adversarial: int = Field(ge=0)


class DatabaseCoverage(BaseModel):
    """D-G3-7: exactly what the PostgreSQL security suite executed. Never extrapolated to the full corpus."""

    model_config = _STRICT
    executed: bool
    engine: str | None = None  # "postgresql"
    engine_version: str | None = None  # e.g. "16.15"
    authoritative_gate: bool = False  # True only for the PostgreSQL 16 run; PG18 is compatibility evidence
    selection_rule: str
    n_executed: int = Field(ge=0)
    n_adversarial_in_corpus: int = Field(ge=0)
    per_category_executed: dict[str, int] = Field(default_factory=dict)
    per_category_in_corpus: dict[str, int] = Field(default_factory=dict)
    item_ids_executed: list[str] = Field(default_factory=list)
    unsafe_effects_observed: int | None = None
    source: str | None = None
    note: str = ""


class RunArtifact(BaseModel):
    model_config = _STRICT
    run: RunIdentity
    created_at_utc: str  # excluded from all hashes
    status: Literal["COMPLETE", "INVALID"]
    banner: str
    items: list[ItemResult]
    primary_arm: (
        str  # arm whose interpretation-level metrics feed the gates (RULES_ONLY for rules.v1, TREATMENT for chain.v1)
    )
    metrics: dict[
        str, dict[str, MetricValue]
    ]  # arm → metric name → value (counts are per arm, never summed across arms)
    strata: dict[
        str, dict[str, dict[str, dict[str, MetricValue]]]
    ]  # arm → stratum kind → stratum value → metric → value
    confusion_9: dict[str, dict[str, dict[str, int]]]  # arm → expected → actual → count
    confusion_7: dict[str, dict[str, dict[str, int]]]
    gates: list[GateResult]
    defect_candidates: dict[str, list[dict[str, Any]]]
    known_defect_count: (
        MetricValue  # D-G3-4: numerator = items flagged in the defect register, denominator = items in split
    )
    chain_sut_coverage: ChainCoverage
    database_coverage: DatabaseCoverage  # D-G3-7
    evaluation_schema_validation: MetricValue
    not_available_offline: dict[str, str]
    comparison_hash: str
