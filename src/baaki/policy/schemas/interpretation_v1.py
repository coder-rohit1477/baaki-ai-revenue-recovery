"""interpretation.v1 — call-1 output schema. No money field; dates/amounts only as verbatim spans."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final[str] = "interpretation.v1"


class Intent(StrEnum):
    WILL_PAY_ON_DATE = "WILL_PAY_ON_DATE"
    REQUEST_INSTALLMENTS = "REQUEST_INSTALLMENTS"
    DISPUTE_AMOUNT = "DISPUTE_AMOUNT"
    DISPUTE_DELIVERY = "DISPUTE_DELIVERY"
    ALREADY_PAID_CLAIM = "ALREADY_PAID_CLAIM"
    WRONG_CONTACT = "WRONG_CONTACT"
    NEEDS_DOCUMENT = "NEEDS_DOCUMENT"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    NO_CLEAR_INTENT = "NO_CLEAR_INTENT"


class Sentiment(StrEnum):
    COOPERATIVE = "COOPERATIVE"
    NEUTRAL = "NEUTRAL"
    FRUSTRATED = "FRUSTRATED"
    HOSTILE = "HOSTILE"


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    field: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class InterpretationV1(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    intent: Intent
    promised_date_raw: str | None = None
    promised_amount_raw: str | None = None
    invoice_refs: list[str] = Field(default_factory=list)
    contact_correction: str | None = None
    sentiment: Sentiment
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)

    # Fields whose non-null value is a claim that must be backed by evidence (check 08).
    CLAIM_FIELDS: ClassVar[tuple[str, ...]] = (
        "promised_date_raw",
        "promised_amount_raw",
        "invoice_refs",
        "contact_correction",
    )
