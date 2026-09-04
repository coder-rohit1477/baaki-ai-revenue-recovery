"""Corpus, profile, oracle and adversarial contracts (PHASE2B2_PLAN §4.1, D-2b2-1/4/5/6/13/14/15/16).

Layer A (semantic) and Layer B (safety/policy) are DATA in the corpus. Layer C (SUT result) is produced by the
harness in G2 and is not part of an item. Strict, closed models: unknown keys are corpus errors.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baaki.domain.enums import ActionType, Channel, EscalationReason, InvoiceState, SuppressReason

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")

CORPUS_VERSION: Final[str] = "corpus.v1"


class SchemaIntent(StrEnum):
    """The nine production intents — vocabulary copied by value (tests assert equality with the production enum)."""

    WILL_PAY_ON_DATE = "WILL_PAY_ON_DATE"
    REQUEST_INSTALLMENTS = "REQUEST_INSTALLMENTS"
    DISPUTE_AMOUNT = "DISPUTE_AMOUNT"
    DISPUTE_DELIVERY = "DISPUTE_DELIVERY"
    ALREADY_PAID_CLAIM = "ALREADY_PAID_CLAIM"
    WRONG_CONTACT = "WRONG_CONTACT"
    NEEDS_DOCUMENT = "NEEDS_DOCUMENT"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    NO_CLEAR_INTENT = "NO_CLEAR_INTENT"


class Family(StrEnum):
    """Six reporting families (D-2b2-1). WRONG_CONTACT is NOT a family: it is a separately reported safety subtype."""

    OPT_OUT = "OPT_OUT"
    DISPUTE = "DISPUTE"
    PAID_CLAIM = "PAID_CLAIM"
    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    REQUEST_INFO = "REQUEST_INFO"
    UNRELATED = "UNRELATED"


FAMILY_OF: Final[dict[SchemaIntent, Family | None]] = {
    SchemaIntent.UNSUBSCRIBE: Family.OPT_OUT,
    SchemaIntent.DISPUTE_AMOUNT: Family.DISPUTE,
    SchemaIntent.DISPUTE_DELIVERY: Family.DISPUTE,
    SchemaIntent.ALREADY_PAID_CLAIM: Family.PAID_CLAIM,
    SchemaIntent.WILL_PAY_ON_DATE: Family.PROMISE_TO_PAY,
    SchemaIntent.REQUEST_INSTALLMENTS: Family.PROMISE_TO_PAY,
    SchemaIntent.NEEDS_DOCUMENT: Family.REQUEST_INFO,
    SchemaIntent.NO_CLEAR_INTENT: Family.UNRELATED,
    SchemaIntent.WRONG_CONTACT: None,  # safety subtype, reported separately (D-2b2-1)
}
SAFETY_SUBTYPE: Final[SchemaIntent] = SchemaIntent.WRONG_CONTACT


class Split(StrEnum):
    TRAIN = "train"
    DEV = "dev"
    REGRESSION = "regression"
    HELDOUT = "heldout"


class Language(StrEnum):
    EN = "en"
    HI_LATN = "hi-Latn"
    MIXED = "mixed"
    HI_DEVA = "hi-Deva"


class Register(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    TRANSCRIPT = "transcript"


class Ambiguity(StrEnum):
    NONE = "none"
    DATE = "date"
    AMOUNT = "amount"
    MULTI_INTENT = "multi_intent"


class OptOutScope(StrEnum):
    """D-2b2-14 closed annotation classes. OPT_OUT_POSITIVE = GENERAL ∪ CHANNEL_INBOUND."""

    NONE = "NONE"
    GENERAL = "GENERAL"
    CHANNEL_INBOUND = "CHANNEL_INBOUND"
    CHANNEL_OTHER = "CHANNEL_OTHER"
    TEMPORARY = "TEMPORARY"
    AMBIGUOUS = "AMBIGUOUS"


OPT_OUT_POSITIVE: Final[frozenset[OptOutScope]] = frozenset({OptOutScope.GENERAL, OptOutScope.CHANNEL_INBOUND})
GAP_CHANNEL_OTHER: Final[str] = "GAP-2b2-1"


class EvidenceGrade(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"  # G1 seed items: infrastructure only, never evidence
    EVALUATION = "EVALUATION"


class Author(StrEnum):
    HAND = "hand"
    GENERATOR = "generator"


class PairFeature(StrEnum):
    """D-2b2-15 closed semantic-feature set. A pair differs in exactly one feature; no token bound exists."""

    NEGATION = "negation"
    TEMPORAL_BOUND = "temporal_bound"
    MODALITY = "modality"
    CHANNEL_SCOPE = "channel_scope"
    PREDICATE = "predicate"
    AMOUNT_ROLE = "amount_role"
    ADDRESSEE = "addressee"
    TENSE = "tense"


# feature → semantic-oracle fields that MAY differ between pair members; every other field must be identical.
PAIR_FEATURE_FIELDS: Final[dict[PairFeature, frozenset[str]]] = {
    PairFeature.NEGATION: frozenset(
        {"primary_intent", "secondary_intents", "opt_out_scope", "negation", "ptp", "ambiguity"}
    ),
    PairFeature.TEMPORAL_BOUND: frozenset(
        {"opt_out_scope", "temporary_restriction_until", "ptp", "ambiguity", "primary_intent"}
    ),
    PairFeature.MODALITY: frozenset({"primary_intent", "secondary_intents", "ptp", "ambiguity"}),
    PairFeature.CHANNEL_SCOPE: frozenset({"opt_out_scope", "channel_restriction_other", "primary_intent"}),
    PairFeature.PREDICATE: frozenset({"primary_intent", "secondary_intents", "ptp", "ambiguity"}),
    PairFeature.AMOUNT_ROLE: frozenset({"primary_intent", "secondary_intents", "ptp", "ambiguity"}),
    PairFeature.ADDRESSEE: frozenset({"primary_intent", "secondary_intents"}),
    PairFeature.TENSE: frozenset({"primary_intent", "secondary_intents", "ptp", "ambiguity"}),
}


class ValidatorFlag(StrEnum):
    """Validator POLICY flags, separate from extraction (D-2b2-5)."""

    DATE_IN_PAST = "DATE_IN_PAST"
    DATE_BEYOND_HORIZON = "DATE_BEYOND_HORIZON"
    AMOUNT_EXCEEDS_OUTSTANDING = "AMOUNT_EXCEEDS_OUTSTANDING"


class PtpOracle(BaseModel):
    """Independently authored PTP ground truth (D-2b2-5). Never produced by the production grammar."""

    model_config = _STRICT
    raw_date_span: str | None = None
    expected_date_iso: date | None = None
    abstain_date: bool = False
    raw_amount_span: str | None = None
    expected_amount_paise: int | None = Field(default=None, ge=1)
    abstain_amount: bool = False
    normalization_rationale: str = Field(min_length=1)
    expected_validator_flags: list[ValidatorFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> PtpOracle:
        if self.raw_date_span is None and (self.expected_date_iso is not None or self.abstain_date):
            raise ValueError("date expectation without a raw date span")
        if self.raw_date_span is not None and (self.expected_date_iso is None) != self.abstain_date:
            raise ValueError("a raw date span must yield exactly one of expected_date_iso or abstain_date")
        if self.raw_amount_span is None and (self.expected_amount_paise is not None or self.abstain_amount):
            raise ValueError("amount expectation without a raw amount span")
        if self.raw_amount_span is not None and (self.expected_amount_paise is None) != self.abstain_amount:
            raise ValueError("a raw amount span must yield exactly one of expected_amount_paise or abstain_amount")
        return self


class SemanticOracle(BaseModel):
    """Layer A — what the message means. Human/template authored; independent of every production module."""

    model_config = _STRICT
    primary_intent: SchemaIntent
    secondary_intents: list[SchemaIntent] = Field(default_factory=list)
    ambiguity: Ambiguity = Ambiguity.NONE
    opt_out_scope: OptOutScope = OptOutScope.NONE
    temporary_restriction_until: date | None = None
    channel_restriction_other: bool = False
    negation: bool = False
    ptp: PtpOracle | None = None

    @model_validator(mode="after")
    def _shape(self) -> SemanticOracle:
        if self.primary_intent in self.secondary_intents:
            raise ValueError("primary intent repeated in secondary_intents")
        if self.secondary_intents and self.ambiguity is not Ambiguity.MULTI_INTENT and self.ambiguity is Ambiguity.NONE:
            raise ValueError("secondary intents require ambiguity=multi_intent (or date/amount)")
        if self.opt_out_scope in OPT_OUT_POSITIVE and self.primary_intent is not SchemaIntent.UNSUBSCRIBE:
            raise ValueError("an OPT_OUT positive scope requires primary_intent UNSUBSCRIBE")
        if self.primary_intent is SchemaIntent.UNSUBSCRIBE and self.opt_out_scope not in OPT_OUT_POSITIVE:
            raise ValueError("UNSUBSCRIBE requires scope GENERAL or CHANNEL_INBOUND (D-2b2-14)")
        if self.opt_out_scope is OptOutScope.TEMPORARY and self.temporary_restriction_until is None:
            raise ValueError("TEMPORARY scope requires temporary_restriction_until")
        if self.opt_out_scope is OptOutScope.CHANNEL_OTHER and not self.channel_restriction_other:
            raise ValueError("CHANNEL_OTHER scope requires channel_restriction_other=true")
        return self


class VerdictClass(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"
    DEFER = "DEFER"
    INELIGIBLE = "INELIGIBLE"


class ContactSafety(StrEnum):
    NONE = "NONE"
    CONTACT_OPTED_OUT = "CONTACT_OPTED_OUT"
    NO_MESSAGE_TO_CONTACT = "NO_MESSAGE_TO_CONTACT"
    NAMED_CHANNEL_NOT_CONTACTED = "NAMED_CHANNEL_NOT_CONTACTED"


class ExpectedOutcome(BaseModel):
    """EXPECTED SAFE POLICY OUTCOME (Layer B). Declarative; compared against ACTUAL arm outcomes in G2."""

    model_config = _STRICT
    verdict_class: VerdictClass
    action: ActionType | None = None
    blocking_rule: str | None = None  # "P0".."P11" for BLOCK
    suppress_reason: SuppressReason | None = None
    escalation_reason: EscalationReason | None = None
    contact_safety: ContactSafety = ContactSafety.NONE

    @model_validator(mode="after")
    def _shape(self) -> ExpectedOutcome:
        if self.verdict_class in (VerdictClass.ALLOW, VerdictClass.REQUIRE_APPROVAL) and self.action is None:
            raise ValueError("executable outcome needs an action")
        if self.verdict_class is VerdictClass.BLOCK and self.blocking_rule is None:
            raise ValueError("BLOCK needs a blocking_rule")
        if (
            self.verdict_class in (VerdictClass.DEFER, VerdictClass.INELIGIBLE, VerdictClass.BLOCK)
            and self.action is not None
        ):
            raise ValueError("non-executable outcome carries no action")
        return self


class SafetyOracle(BaseModel):
    """Layer B result stored on the item; must equal `oracle.expected_outcome(semantic, profile)` (loader check)."""

    model_config = _STRICT
    governing_intent: SchemaIntent
    expected: ExpectedOutcome
    policy_gap: str | None = None  # GAP-2b2-1 for CHANNEL_OTHER; never a success


class AdversarialCategory(StrEnum):
    INSTRUCTION_OVERRIDE = "instruction_override"
    FINANCIAL_AUTHORIZATION = "financial_authorization"
    OPT_OUT_OVERRIDE = "opt_out_override"
    FABRICATED_PAYMENT = "fabricated_payment"
    FORCED_ESCALATION = "forced_escalation"
    CONTEXT_EXFILTRATION = "context_exfiltration"
    EMBEDDED_MALICIOUS_TEXT = "embedded_malicious_text"
    UNICODE_ENCODING = "unicode_encoding"


class AttackTarget(StrEnum):
    INTENT = "intent"
    ACTION = "action"
    CONTACT = "contact"
    INVOICE = "invoice"
    AMOUNT = "amount"
    APPROVAL = "approval"
    OPT_OUT = "opt_out"
    CONTEXT = "context"


class ProposalClassification(StrEnum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"


class ExpectedValidatorOutcome(BaseModel):
    model_config = _STRICT
    outcome: Literal["PASS", "REJECT"]
    reason: str | None = None  # a production rejection_reason label when REJECT


class FinalEffect(StrEnum):
    """Safe terminal states an adversarial item may end in. Anything else is an unsafe effect (D-2b2-16)."""

    NO_EFFECT_SUPPRESS = "NO_EFFECT_SUPPRESS"
    REJECTED_L1_FALLBACK = "REJECTED_L1_FALLBACK"
    PENDING_APPROVAL_NO_OUTBOX = "PENDING_APPROVAL_NO_OUTBOX"
    TEMPLATED_OUTBOUND_WITHIN_POLICY = "TEMPLATED_OUTBOUND_WITHIN_POLICY"
    BLOCKED = "BLOCKED"
    INELIGIBLE = "INELIGIBLE"


class StoppingLayer(StrEnum):
    CONTRACT_A3_A4 = "CONTRACT_A3_A4"
    W07_CHECK = "W07_CHECK"
    VALIDATOR_CHECK = "VALIDATOR_CHECK"
    KERNEL_LADDER = "KERNEL_LADDER"
    KERNEL_TIER_CAP = "KERNEL_TIER_CAP"
    W09_ASSERTION = "W09_ASSERTION"
    ROLE_GRANT = "ROLE_GRANT"
    CONTEXT_MINIMISATION = "CONTEXT_MINIMISATION"
    NONE_NEEDED = "NONE_NEEDED"


class AdversarialSpec(BaseModel):
    """D-2b2-6 locked per-item fields. Both `*_allowed` flags are literally false for this evaluation."""

    model_config = _STRICT
    adversarial_category: AdversarialCategory
    attack_text: str = Field(min_length=1)
    attack_target: AttackTarget
    expected_semantic_intent: SchemaIntent
    expected_safe_action: ActionType | None
    unsafe_proposal_allowed: Literal[False] = False
    unsafe_effect_allowed: Literal[False] = False
    expected_validator_outcome: ExpectedValidatorOutcome
    expected_kernel_outcome: ExpectedOutcome
    malicious_model_output: dict[str, Any]  # hand-written scripted output the compromised model is imagined to emit
    expected_proposal_classification: ProposalClassification
    expected_final_effect: FinalEffect
    stopping_layer: StoppingLayer
    stopping_detail: str | None = None  # e.g. "check 06", "P2", "A3 denylist"


class GeneratorMeta(BaseModel):
    model_config = _STRICT
    bank: str
    seed: int
    template: str


class CorpusItem(BaseModel):
    model_config = _STRICT
    id: str = Field(pattern=r"^C-[0-9]{6}$")
    corpus_version: str
    split: Split
    evidence_grade: EvidenceGrade
    language: Language
    message_register: Register
    text: str
    profile: str = Field(min_length=1)
    author: Author
    generator: GeneratorMeta | None = None
    pair_id: str | None = Field(default=None, pattern=r"^MP-[0-9]{4}$")
    pair_feature: PairFeature | None = None
    semantic: SemanticOracle
    safety: SafetyOracle
    adversarial: AdversarialSpec | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _shape(self) -> CorpusItem:
        if self.corpus_version != CORPUS_VERSION:
            raise ValueError(f"corpus_version must be {CORPUS_VERSION}")
        if (self.pair_id is None) != (self.pair_feature is None):
            raise ValueError("pair_id and pair_feature go together")
        if (self.author is Author.GENERATOR) != (self.generator is not None):
            raise ValueError("generator metadata iff author=generator")
        if self.adversarial is not None:
            a = self.adversarial
            if a.expected_semantic_intent is not self.semantic.primary_intent:
                raise ValueError("adversarial.expected_semantic_intent must equal semantic.primary_intent")
            if a.expected_safe_action != self.safety.expected.action:
                raise ValueError("adversarial.expected_safe_action must equal safety.expected.action")
            if a.attack_text not in self.text:
                raise ValueError("attack_text must be contained in the item text")
        return self


# ── facts profiles (evaluation-side description; profiles.py turns one into AccountFacts) ─────────────────────
class ProfileInvoice(BaseModel):
    model_config = _STRICT
    number: str
    state: InvoiceState
    days_overdue: int = Field(ge=0)
    outstanding_paise: int = Field(ge=0)


class ProfileSpec(BaseModel):
    model_config = _STRICT
    id: str = Field(pattern=r"^P-[A-Z0-9-]+$")
    description: str
    as_of: datetime  # timezone-aware
    timezone: str
    kill_switch: bool = False
    account_opt_out: bool = False
    contact_opted_out: bool = False  # the (only) contact is opted out → contactable set empty
    channels: list[Channel] = Field(default_factory=lambda: [Channel.EMAIL])
    invoices: list[ProfileInvoice] = Field(default_factory=list)
    contacts_7d: int = Field(default=0, ge=0)
    contacts_invoice_7d: int = Field(default=0, ge=0)
    active_payment_link: bool = False
    paid_claim_pending: bool = False

    @property
    def candidates(self) -> list[ProfileInvoice]:
        return [i for i in self.invoices if i.state is not InvoiceState.PAID and i.outstanding_paise > 0]

    @property
    def business_date(self) -> date:
        return self.as_of.date()
