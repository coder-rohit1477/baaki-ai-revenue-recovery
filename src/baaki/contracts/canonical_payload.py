"""CanonicalPayload — kernel-produced, executor-consumed (§1.5, §1.5.1, §6.9).

Every money field is Paise (CP1). No free text the executor parses (CP3). No variant exists
for any F1–F7 capability (CP4). reason_code / assignee_queue are kernel-derived (CP6).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, NewType
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baaki.domain.enums import (
    ActionType,
    AssigneeQueue,
    Channel,
    EscalationReason,
    SuppressReason,
    queue_for_reason,
)
from baaki.domain.errors import ContractViolation
from baaki.domain.money import Paise

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")

TemplateId = NewType("TemplateId", str)


class LinkNotes(BaseModel):
    """§9.3 attribution by reference, never by amount."""

    model_config = _STRICT
    invoice_id: UUID
    action_id: UUID
    trace_id: UUID


class SuppressPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.SUPPRESS] = ActionType.SUPPRESS
    reason_code: SuppressReason


class ScheduleFollowupPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.SCHEDULE_FOLLOWUP] = ActionType.SCHEDULE_FOLLOWUP
    followup_date: date


class RequestDisputeDetailsPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.REQUEST_DISPUTE_DETAILS] = ActionType.REQUEST_DISPUTE_DETAILS
    contact_id: UUID
    channel: Channel
    template_id: TemplateId


class SendReminderPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.SEND_REMINDER] = ActionType.SEND_REMINDER
    contact_id: UUID
    channel: Channel
    template_id: TemplateId
    existing_link_ref: str | None = None


class SendPaymentLinkPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.SEND_PAYMENT_LINK] = ActionType.SEND_PAYMENT_LINK
    amount_paise: Paise  # == snapshot.outstanding_paise, asserted by W09 (CP5)
    contact_id: UUID
    channel: Channel
    template_id: TemplateId
    expires_at: datetime
    notes: LinkNotes

    @model_validator(mode="after")
    def _positive(self) -> SendPaymentLinkPayload:
        if int(self.amount_paise) <= 0:
            raise ContractViolation("amount_paise must be > 0 (CP1)")
        return self


class InstallmentPart(BaseModel):
    model_config = _STRICT
    amount_paise: Paise
    due_date: date


class ProposeInstallmentPlanPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.PROPOSE_INSTALLMENT_PLAN] = ActionType.PROPOSE_INSTALLMENT_PLAN
    parts: list[InstallmentPart] = Field(min_length=1)
    contact_id: UUID
    channel: Channel
    template_id: TemplateId

    @model_validator(mode="after")
    def _positive_parts(self) -> ProposeInstallmentPlanPayload:
        if any(int(p.amount_paise) <= 0 for p in self.parts):
            raise ContractViolation("installment parts must be > 0 (CP1)")
        return self


class EscalateToHumanPayload(BaseModel):
    model_config = _STRICT
    action_type: Literal[ActionType.ESCALATE_TO_HUMAN] = ActionType.ESCALATE_TO_HUMAN
    reason_code: EscalationReason
    assignee_queue: AssigneeQueue

    @model_validator(mode="after")
    def _queue_matches_reason(self) -> EscalateToHumanPayload:
        # §1.5.1 / §6.9 — the same rule W09 asserts as `queue_reason_mismatch`.
        if self.assignee_queue is not queue_for_reason(self.reason_code):
            raise ContractViolation("assignee_queue inconsistent with reason_code (§1.5.1)")
        return self


CanonicalPayload = Annotated[
    SuppressPayload
    | ScheduleFollowupPayload
    | RequestDisputeDetailsPayload
    | SendReminderPayload
    | SendPaymentLinkPayload
    | ProposeInstallmentPlanPayload
    | EscalateToHumanPayload,
    Field(discriminator="action_type"),
]
