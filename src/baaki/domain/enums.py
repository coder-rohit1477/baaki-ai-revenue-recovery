"""The 19 Phase-1 enums. Single source of truth; migration 0001 must match exactly
(tests/schema/test_enums.py compares pg_enum labels to these members).

ARCHITECTURE.md v3.2.1 §1.5.1, §2.2, §3.2, §4.1, §6.11, §6.14, §6.15, §13.3.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ProposalKind(StrEnum):
    INTERPRETATION = "INTERPRETATION"
    ACTION_PROPOSAL = "ACTION_PROPOSAL"


class ParseStatus(StrEnum):
    OK = "OK"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    UNPARSEABLE = "UNPARSEABLE"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class Arm(StrEnum):
    CONTROL = "CONTROL"
    RULES_ONLY = "RULES_ONLY"
    TREATMENT = "TREATMENT"


class ValidationOutcome(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"


class RejectionReason(StrEnum):
    SYSTEM_HALTED = "SYSTEM_HALTED"
    LEDGER_INVARIANT_BREACH = "LEDGER_INVARIANT_BREACH"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    UNPARSEABLE = "UNPARSEABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    ENUM_OUT_OF_RANGE = "ENUM_OUT_OF_RANGE"
    FORBIDDEN_MONEY_FIELD = "FORBIDDEN_MONEY_FIELD"
    EVIDENCE_NOT_FOUND_IN_SOURCE = "EVIDENCE_NOT_FOUND_IN_SOURCE"
    EVIDENCE_MISSING_FOR_FIELD = "EVIDENCE_MISSING_FOR_FIELD"
    CONTACT_NOT_IN_ACCOUNT = "CONTACT_NOT_IN_ACCOUNT"
    INVOICE_REF_UNRESOLVED = "INVOICE_REF_UNRESOLVED"
    DATE_UNPARSEABLE = "DATE_UNPARSEABLE"
    DATE_AMBIGUOUS = "DATE_AMBIGUOUS"
    AMOUNT_UNPARSEABLE = "AMOUNT_UNPARSEABLE"
    AMOUNT_AMBIGUOUS = "AMOUNT_AMBIGUOUS"
    DATE_IN_PAST = "DATE_IN_PAST"
    DATE_BEYOND_HORIZON = "DATE_BEYOND_HORIZON"
    AMOUNT_EXCEEDS_OUTSTANDING = "AMOUNT_EXCEEDS_OUTSTANDING"
    CONFIDENCE_BELOW_THRESHOLD = "CONFIDENCE_BELOW_THRESHOLD"


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"
    DEFER = "DEFER"


EXECUTABLE_VERDICTS: Final[frozenset[Verdict]] = frozenset({Verdict.ALLOW, Verdict.REQUIRE_APPROVAL})
NON_EXECUTABLE_VERDICTS: Final[frozenset[Verdict]] = frozenset({Verdict.BLOCK, Verdict.DEFER})


class ActionType(StrEnum):
    SUPPRESS = "SUPPRESS"
    SCHEDULE_FOLLOWUP = "SCHEDULE_FOLLOWUP"
    REQUEST_DISPUTE_DETAILS = "REQUEST_DISPUTE_DETAILS"
    SEND_REMINDER = "SEND_REMINDER"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    PROPOSE_INSTALLMENT_PLAN = "PROPOSE_INSTALLMENT_PLAN"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"


# Authority tiers (§5.2). Tier 3 is not representable: no member maps to it.
ACTION_TIER: Final[dict[ActionType, int]] = {
    ActionType.SUPPRESS: 0,
    ActionType.SCHEDULE_FOLLOWUP: 0,
    ActionType.REQUEST_DISPUTE_DETAILS: 0,
    ActionType.SEND_REMINDER: 1,
    ActionType.SEND_PAYMENT_LINK: 1,
    ActionType.PROPOSE_INSTALLMENT_PLAN: 2,
    ActionType.ESCALATE_TO_HUMAN: 2,
}


class ActionState(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    CONFIRMED = "CONFIRMED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    EXPIRED = "EXPIRED"
    SUPERSEDED_DUPLICATE = "SUPERSEDED_DUPLICATE"
    COMPENSATED = "COMPENSATED"


class InvoiceState(StrEnum):
    OPEN = "OPEN"
    DUE = "DUE"
    OVERDUE = "OVERDUE"
    DISPUTED = "DISPUTED"
    PAID = "PAID"


class DrCr(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerSource(StrEnum):
    ISSUANCE = "ISSUANCE"
    PAYMENT = "PAYMENT"
    REATTRIBUTION = "REATTRIBUTION"


class Channel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"


class DegradationLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class TemplatePurpose(StrEnum):
    REMINDER = "REMINDER"
    COURTESY_NUDGE = "COURTESY_NUDGE"
    PAYMENT_LINK = "PAYMENT_LINK"
    DISPUTE_DETAILS_REQUEST = "DISPUTE_DETAILS_REQUEST"
    INSTALLMENT_PROPOSAL = "INSTALLMENT_PROPOSAL"


# §6.14 TPL4 — the only legal (action_type, purpose) pairs.
TEMPLATE_PAIRS: Final[frozenset[tuple[ActionType, TemplatePurpose]]] = frozenset(
    {
        (ActionType.SEND_REMINDER, TemplatePurpose.REMINDER),
        (ActionType.SEND_REMINDER, TemplatePurpose.COURTESY_NUDGE),
        (ActionType.SEND_PAYMENT_LINK, TemplatePurpose.PAYMENT_LINK),
        (ActionType.REQUEST_DISPUTE_DETAILS, TemplatePurpose.DISPUTE_DETAILS_REQUEST),
        (ActionType.PROPOSE_INSTALLMENT_PLAN, TemplatePurpose.INSTALLMENT_PROPOSAL),
    }
)


class SuppressReason(StrEnum):
    """§1.5.1 — kernel-derived; highest-precedence pressure-blocking condition, else NO_ELIGIBLE_ACTION."""

    DISPUTE_OPEN = "DISPUTE_OPEN"
    PAID_CLAIM_PENDING = "PAID_CLAIM_PENDING"
    PTP_ACTIVE = "PTP_ACTIVE"
    FREQUENCY_CAP = "FREQUENCY_CAP"
    NO_ELIGIBLE_ACTION = "NO_ELIGIBLE_ACTION"


class EscalationReason(StrEnum):
    """§1.5.1 — kernel-derived."""

    DISPUTE_UNRESOLVED = "DISPUTE_UNRESOLVED"
    PAID_CLAIM_UNVERIFIED = "PAID_CLAIM_UNVERIFIED"
    AMBIGUOUS_INTERPRETATION = "AMBIGUOUS_INTERPRETATION"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class AssigneeQueue(StrEnum):
    """§1.5.1 — a pure function of EscalationReason (see QUEUE_FOR_REASON)."""

    DISPUTES = "DISPUTES"
    COLLECTIONS = "COLLECTIONS"


def queue_for_reason(reason: EscalationReason) -> AssigneeQueue:
    """§1.5.1: DISPUTE_UNRESOLVED -> DISPUTES, everything else -> COLLECTIONS. W09 re-asserts this."""
    return (
        AssigneeQueue.DISPUTES
        if reason is EscalationReason.DISPUTE_UNRESOLVED
        else AssigneeQueue.COLLECTIONS
    )


class OptOutSource(StrEnum):
    """§6.18 / §6.18.1 — how an opt-out was established. Monotonic; no clearing path exists."""

    INBOUND_UNSUBSCRIBE = "INBOUND_UNSUBSCRIBE"
    INBOUND_RESTRICTION = "INBOUND_RESTRICTION"
    HUMAN = "HUMAN"


class PaymentSource(StrEnum):
    WEBHOOK = "WEBHOOK"
    SWEEP = "SWEEP"


class AttributionMethod(StrEnum):
    NOTES_INVOICE_ID = "NOTES_INVOICE_ID"
    REFERENCE_ACTION_ID = "REFERENCE_ACTION_ID"
    UNATTRIBUTED = "UNATTRIBUTED"
    HUMAN_REATTRIBUTION = "HUMAN_REATTRIBUTION"


# Postgres enum name -> Python enum. 19 in Phase 1 + opt_out_source in Phase 2 = 20 (§13.3).
# Tests assert label sets match pg_enum.
POSTGRES_ENUMS: Final[dict[str, type[StrEnum]]] = {
    "proposal_kind": ProposalKind,
    "parse_status": ParseStatus,
    "arm": Arm,
    "validation_outcome": ValidationOutcome,
    "rejection_reason": RejectionReason,
    "verdict": Verdict,
    "action_type": ActionType,
    "action_state": ActionState,
    "invoice_state": InvoiceState,
    "dr_cr": DrCr,
    "ledger_source": LedgerSource,
    "channel": Channel,
    "degradation_level": DegradationLevel,
    "template_purpose": TemplatePurpose,
    "suppress_reason": SuppressReason,
    "escalation_reason": EscalationReason,
    "assignee_queue": AssigneeQueue,
    "payment_source": PaymentSource,
    "attribution_method": AttributionMethod,
    "opt_out_source": OptOutSource,
}

# §6.15 — the seven forbidden capabilities. Tests assert none appears in any enum, payload,
# writer name, account code, or state.
FORBIDDEN_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"DISCOUNT", "SETTLEMENT", "WRITE_OFF", "REFUND", "MARK_PAID", "ADJUST_AMOUNT", "REVERSE_LEDGER"}
)

# A3 — keys that may never appear in AgentProposal.parsed (top-level; `settle*` by prefix).
MONEY_KEY_DENYLIST: Final[frozenset[str]] = frozenset(
    {
        "amount",
        "amount_paise",
        "total",
        "balance",
        "discount",
        "interest",
        "fee",
        "outstanding",
        "due_amount",
        "waiver",
        "credit",
    }
)
MONEY_KEY_DENY_PREFIX: Final[str] = "settle"
