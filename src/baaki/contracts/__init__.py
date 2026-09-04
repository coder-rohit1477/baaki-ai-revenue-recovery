"""Cross-boundary contracts (ARCHITECTURE.md §1). KERNEL_TOKEN is deliberately NOT re-exported."""

from baaki.contracts.account_snapshot import AccountSnapshot, TemplateCatalogueEntry
from baaki.contracts.agent_proposal import AgentProposal, RawJson
from baaki.contracts.canonical_payload import (
    CanonicalPayload,
    EscalateToHumanPayload,
    InstallmentPart,
    LinkNotes,
    ProposeInstallmentPlanPayload,
    RequestDisputeDetailsPayload,
    ScheduleFollowupPayload,
    SendPaymentLinkPayload,
    SendReminderPayload,
    SuppressPayload,
    TemplateId,
)
from baaki.contracts.policy_decision import (
    ExecutableDecision,
    NonExecutableDecision,
    PolicyDecision,
    as_executable,
)
from baaki.contracts.recovery_action import RecoveryAction
from baaki.contracts.validation_result import NormalizedInterpretation, ValidationResult

__all__ = [
    "AccountSnapshot",
    "AgentProposal",
    "CanonicalPayload",
    "EscalateToHumanPayload",
    "ExecutableDecision",
    "InstallmentPart",
    "LinkNotes",
    "NonExecutableDecision",
    "NormalizedInterpretation",
    "PolicyDecision",
    "ProposeInstallmentPlanPayload",
    "RawJson",
    "RecoveryAction",
    "RequestDisputeDetailsPayload",
    "ScheduleFollowupPayload",
    "SendPaymentLinkPayload",
    "SendReminderPayload",
    "SuppressPayload",
    "TemplateCatalogueEntry",
    "TemplateId",
    "ValidationResult",
    "as_executable",
]
