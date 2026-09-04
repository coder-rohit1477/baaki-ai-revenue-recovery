"""Account facts and candidate invoices — pre-target snapshot inputs (ARCHITECTURE.md §1.3, §6.8.3 SC2)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baaki.contracts.account_snapshot import ActivePaymentLink, TemplateCatalogueEntry
from baaki.domain.enums import Channel, InvoiceState
from baaki.domain.money import Paise

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class CandidateInvoice(BaseModel):
    model_config = _STRICT
    invoice_id: UUID
    invoice_number: str
    state: InvoiceState
    due_date: date
    days_overdue: int = Field(ge=0)
    outstanding_paise: Paise  # from v_invoice_outstanding only


class InvoiceRef(BaseModel):
    model_config = _STRICT
    invoice_id: UUID
    invoice_number: str


class ContactFact(BaseModel):
    model_config = _STRICT
    contact_id: UUID
    channel: Channel


class PaidClaimFact(BaseModel):
    """A PASS validation with intent ALREADY_PAID_CLAIM (§5.6)."""

    model_config = _STRICT
    validation_id: UUID
    claim_at: datetime
    invoice_ids: list[UUID] = Field(default_factory=list)  # empty => account-scoped


class AppliedPaymentFact(BaseModel):
    model_config = _STRICT
    invoice_id: UUID
    applied_at: datetime


class AccountFacts(BaseModel):
    """Everything the validator and arm strategies need before a target is chosen. Read-only facts."""

    model_config = _STRICT
    as_of: datetime
    business_date: date
    org_id: UUID
    account_id: UUID
    timezone: str
    kill_switch: bool
    ledger_invariant_ok: bool
    opt_out: bool
    candidates: list[CandidateInvoice]  # SC2 order; may be empty (SC7)
    all_invoices: list[InvoiceRef]  # for check 10 (account-scoped resolution)
    contactable: list[ContactFact]  # active ∧ ¬opted_out
    contacts_7d: int = Field(ge=0)
    contacts_invoice_7d: dict[str, int] = Field(default_factory=dict)  # invoice_id (str) -> intent count
    last_contact_at: datetime | None = None
    active_payment_links: dict[str, ActivePaymentLink] = Field(default_factory=dict)  # invoice_id (str) -> link
    paid_claims: list[PaidClaimFact] = Field(default_factory=list)
    applied_payments: list[AppliedPaymentFact] = Field(default_factory=list)
    template_catalogue: list[TemplateCatalogueEntry]

    @property
    def candidate_ids(self) -> list[UUID]:
        return [c.invoice_id for c in self.candidates]

    def candidate(self, invoice_id: UUID) -> CandidateInvoice | None:
        for c in self.candidates:
            if c.invoice_id == invoice_id:
                return c
        return None
