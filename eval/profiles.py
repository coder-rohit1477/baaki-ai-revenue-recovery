"""Facts profiles → AccountFacts (pure, deterministic ids). Imports contracts as data types only."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Final

from baaki.contracts.account_snapshot import ActivePaymentLink, TemplateCatalogueEntry
from baaki.contracts.candidate import AccountFacts, CandidateInvoice, ContactFact, InvoiceRef, PaidClaimFact
from baaki.domain.enums import ActionType, Channel, TemplatePurpose
from baaki.domain.money import paise
from eval.schema import ProfileSpec

PROFILES_PATH: Final[Path] = Path(__file__).resolve().parent / "profiles.v1.json"
NAMESPACE: Final[uuid.UUID] = uuid.UUID("7b2e0000-0000-4000-8000-00000000eb2b")

TEMPLATES_V1: Final[tuple[tuple[str, Channel, ActionType, TemplatePurpose], ...]] = (
    ("tpl.reminder.email.v1", Channel.EMAIL, ActionType.SEND_REMINDER, TemplatePurpose.REMINDER),
    ("tpl.reminder.sms.v1", Channel.SMS, ActionType.SEND_REMINDER, TemplatePurpose.REMINDER),
    ("tpl.nudge.email.v1", Channel.EMAIL, ActionType.SEND_REMINDER, TemplatePurpose.COURTESY_NUDGE),
    ("tpl.link.email.v1", Channel.EMAIL, ActionType.SEND_PAYMENT_LINK, TemplatePurpose.PAYMENT_LINK),
    (
        "tpl.dispute.email.v1",
        Channel.EMAIL,
        ActionType.REQUEST_DISPUTE_DETAILS,
        TemplatePurpose.DISPUTE_DETAILS_REQUEST,
    ),
    (
        "tpl.installment.email.v1",
        Channel.EMAIL,
        ActionType.PROPOSE_INSTALLMENT_PLAN,
        TemplatePurpose.INSTALLMENT_PROPOSAL,
    ),
)


def det_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, ":".join(parts))


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, ProfileSpec]:
    data = json.loads(PROFILES_PATH.read_bytes().decode("utf-8"))
    specs = [ProfileSpec.model_validate_json(json.dumps(p)) for p in data["profiles"]]
    ids = [s.id for s in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate profile id")
    return {s.id: s for s in specs}


def to_account_facts(spec: ProfileSpec) -> AccountFacts:
    """Build the same AccountFacts shape the pipeline assembles, from the declarative profile."""
    org, acc = det_id(spec.id, "org"), det_id(spec.id, "account")
    bdate = spec.business_date
    cands = [
        CandidateInvoice(
            invoice_id=det_id(spec.id, "inv", i.number),
            invoice_number=i.number,
            state=i.state,
            due_date=bdate - timedelta(days=i.days_overdue),
            days_overdue=i.days_overdue,
            outstanding_paise=paise(i.outstanding_paise),
        )
        for i in spec.invoices
        if i.state.value != "PAID" and i.outstanding_paise > 0
    ]
    cands.sort(key=lambda c: (-c.days_overdue, -int(c.outstanding_paise), str(c.invoice_id)))
    all_inv = [InvoiceRef(invoice_id=det_id(spec.id, "inv", i.number), invoice_number=i.number) for i in spec.invoices]
    contactable = (
        []
        if spec.contact_opted_out
        else [ContactFact(contact_id=det_id(spec.id, "contact", str(ch)), channel=ch) for ch in spec.channels]
    )
    primary = cands[0].invoice_id if cands else None
    links = {}
    if spec.active_payment_link and primary is not None:
        links[str(primary)] = ActivePaymentLink(
            link_id="plink_profile", created_at=spec.as_of - timedelta(hours=1), amount_paise=paise(1)
        )
    claims = []
    if spec.paid_claim_pending:
        claims.append(
            PaidClaimFact(
                validation_id=det_id(spec.id, "claim"), claim_at=spec.as_of - timedelta(hours=10), invoice_ids=[]
            )
        )
    return AccountFacts(
        as_of=spec.as_of,
        business_date=bdate,
        org_id=org,
        account_id=acc,
        timezone=spec.timezone,
        kill_switch=spec.kill_switch,
        ledger_invariant_ok=True,
        opt_out=spec.account_opt_out,
        candidates=cands,
        all_invoices=all_inv,
        contactable=contactable,
        contacts_7d=spec.contacts_7d,
        contacts_invoice_7d={str(primary): spec.contacts_invoice_7d} if primary else {},
        last_contact_at=None,
        active_payment_links=links,
        paid_claims=claims,
        applied_payments=[],
        template_catalogue=[
            TemplateCatalogueEntry(template_id=t, channel=c, action_type=a, purpose=p, active=True)
            for t, c, a, p in TEMPLATES_V1
        ],
    )
