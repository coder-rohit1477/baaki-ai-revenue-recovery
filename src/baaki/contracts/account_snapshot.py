"""AccountSnapshot — the kernel's only view of the world (§1.3). Hashed for replay (P6)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baaki.domain.enums import ActionType, Channel, InvoiceState, TemplatePurpose
from baaki.domain.errors import ContractViolation
from baaki.domain.money import ClaimedPaise, Paise

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class TemplateCatalogueEntry(BaseModel):
    model_config = _STRICT
    template_id: str
    channel: Channel
    action_type: ActionType
    purpose: TemplatePurpose
    active: bool


class ActivePtp(BaseModel):
    model_config = _STRICT
    ptp_id: UUID
    due_date: date
    promised_paise: ClaimedPaise
    state: str


class ActivePaymentLink(BaseModel):
    model_config = _STRICT
    link_id: str
    created_at: datetime
    amount_paise: Paise


def _canonical(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list | tuple):
        return [_canonical(v) for v in obj]
    if isinstance(obj, UUID | datetime | date):
        return str(obj) if not isinstance(obj, datetime) else obj.isoformat()
    return obj


class AccountSnapshot(BaseModel):
    model_config = _STRICT

    snapshot_hash: str = Field(min_length=64, max_length=64)
    as_of: datetime
    business_date: date
    account_id: UUID
    candidate_invoice_ids: list[UUID]
    target_invoice_id: UUID
    outstanding_paise: Paise  # from v_invoice_outstanding only (S2)
    invoice_state: InvoiceState
    days_overdue: int
    opt_out: bool
    kill_switch: bool
    ledger_invariant_ok: bool
    open_dispute_ids: list[UUID] = Field(default_factory=list)
    unverified_paid_claim_until: datetime | None = None
    active_ptp: ActivePtp | None = None
    active_payment_link: ActivePaymentLink | None = None
    contacts_7d: int = Field(ge=0)
    contacts_invoice_7d: int = Field(ge=0)
    last_contact_at: datetime | None = None
    contactable_contact_ids: list[UUID]
    template_catalogue: list[TemplateCatalogueEntry]

    @staticmethod
    def compute_hash(fields: dict[str, Any]) -> str:
        body = json.dumps(_canonical(fields), separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()

    @model_validator(mode="after")
    def _hash_matches(self) -> AccountSnapshot:
        fields = self.model_dump(mode="json", exclude={"snapshot_hash"})
        if self.compute_hash(fields) != self.snapshot_hash:  # S3
            raise ContractViolation("snapshot_hash does not cover the snapshot fields (S3)")
        if self.target_invoice_id not in self.candidate_invoice_ids:  # SC3/SC4
            raise ContractViolation("target_invoice_id must be a candidate (SC4)")
        return self

    @classmethod
    def build(cls, **fields: Any) -> AccountSnapshot:
        probe = cls.model_construct(**fields)
        dumped = probe.model_dump(mode="json", exclude={"snapshot_hash"})
        return cls(snapshot_hash=cls.compute_hash(dumped), **fields)
