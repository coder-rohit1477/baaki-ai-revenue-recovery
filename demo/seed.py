"""Synthetic demo data. SYNTHETIC — no real customer, invoice or payment exists in this database.

Amounts are paise (the project's only money unit). Every row is written through the same writers the
production path uses; nothing here bypasses a constraint or a grant.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import Engine, text

from baaki.contracts.account_snapshot import TemplateCatalogueEntry
from baaki.domain.enums import ActionType, Channel, TemplatePurpose
from baaki.domain.ids import new_id

H64 = "0" * 64
ORG_NAME = "Meridian Supplies Pvt Ltd"

TEMPLATES = [
    TemplateCatalogueEntry(template_id="tpl.reminder.email.v1", channel=Channel.EMAIL,
                           action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.REMINDER, active=True),
    TemplateCatalogueEntry(template_id="tpl.reminder.sms.v1", channel=Channel.SMS,
                           action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.REMINDER, active=True),
    TemplateCatalogueEntry(template_id="tpl.nudge.email.v1", channel=Channel.EMAIL,
                           action_type=ActionType.SEND_REMINDER, purpose=TemplatePurpose.COURTESY_NUDGE, active=True),
    TemplateCatalogueEntry(template_id="tpl.link.email.v1", channel=Channel.EMAIL,
                           action_type=ActionType.SEND_PAYMENT_LINK, purpose=TemplatePurpose.PAYMENT_LINK, active=True),
    TemplateCatalogueEntry(template_id="tpl.dispute.email.v1", channel=Channel.EMAIL,
                           action_type=ActionType.REQUEST_DISPUTE_DETAILS,
                           purpose=TemplatePurpose.DISPUTE_DETAILS_REQUEST, active=True),
    TemplateCatalogueEntry(template_id="tpl.installment.email.v1", channel=Channel.EMAIL,
                           action_type=ActionType.PROPOSE_INSTALLMENT_PLAN,
                           purpose=TemplatePurpose.INSTALLMENT_PROPOSAL, active=True),
]


@dataclass(frozen=True)
class SeededAccount:
    account_id: UUID
    contact_id: UUID
    invoice_id: UUID
    invoice_number: str
    name: str
    amount_paise: int
    days_overdue: int


# The dashboard needs enough shape to be credible; these are the background accounts.
BACKGROUND = [  # (name, invoice number, paise, days overdue)
    ("Ganesh Enterprises", "INV-1017", 84_000_00, 32),
    ("Nandi Logistics", "INV-1023", 12_500_00, 9),
    ("Kaveri Foods", "INV-1029", 47_800_00, 21),
    ("Orbit Print House", "INV-1031", 9_650_00, 4),
    ("Blue River Textiles", "INV-1038", 1_26_000_00, 15),
]


def _templates(owner) -> None:
    for t in TEMPLATES:
        owner.execute(
            text("INSERT INTO baaki.template_registry (template_id, channel, action_type, purpose, active, version, body_hash) "
                 "VALUES (:t, CAST(:c AS baaki.channel), CAST(:a AS baaki.action_type), CAST(:p AS baaki.template_purpose), :act, 1, :h) "
                 "ON CONFLICT (template_id) DO NOTHING"),
            {"t": t.template_id, "c": str(t.channel), "a": str(t.action_type), "p": str(t.purpose),
             "act": t.active, "h": H64},
        )
    owner.commit()


def _org(owner) -> UUID:
    org = new_id()
    owner.execute(text("INSERT INTO baaki.organization (org_id, name, timezone) VALUES (:o, :n, 'Asia/Kolkata')"),
                  {"o": org, "n": ORG_NAME})
    owner.commit()
    return org


def _account(owner, org: UUID, name: str, ref: str) -> tuple[UUID, UUID]:
    acct, contact = new_id(), new_id()
    owner.execute(text("INSERT INTO baaki.account (account_id, org_id, external_ref, name) VALUES (:a, :o, :r, :n)"),
                  {"a": acct, "o": org, "r": ref, "n": name})
    handle = f"ap@{ref.lower()}.example"
    owner.execute(
        text("INSERT INTO baaki.contact (contact_id, account_id, channel, address_hash, address_redacted) "
             "VALUES (:c, :a, 'EMAIL', :h, :red)"),
        {"c": contact, "a": acct, "h": hashlib.sha256(handle.encode()).hexdigest(),
         "red": handle[0] + "***@" + handle.split("@")[1]},
    )
    owner.commit()
    return acct, contact


def _invoice(app, org: UUID, acct: UUID, number: str, amount: int, days_overdue: int, today: date) -> UUID:
    inv = new_id()
    due = today - timedelta(days=days_overdue)
    app.execute(
        text("SELECT baaki_write.issue_invoice(:i, :o, :a, :n, :amt, :d1, :d2, :t)"),
        {"i": inv, "o": org, "a": acct, "n": number, "amt": amount,
         "d1": due - timedelta(days=15), "d2": due, "t": new_id()},
    )
    app.commit()
    return inv


def seed(engine_owner: Engine, engine_app: Engine, *, today: date) -> dict[str, SeededAccount]:
    """Seed the organisation, the three scenario accounts and the dashboard background. Returns the trio."""
    with engine_owner.connect() as owner, engine_app.connect() as app:
        owner.execute(text("SET ROLE baaki_owner"))
        owner.commit()
        _templates(owner)
        org = _org(owner)

        made: dict[str, SeededAccount] = {}
        scenarios = [
            ("A", "Sharma Traders", "INV-1042", 25_000_00, 18),
            ("B", "Vertex Components", "INV-1044", 42_000_00, 26),
            ("C", "Lotus Interiors", "INV-1046", 18_500_00, 11),
            ("D", "Deccan Hardware", "INV-1035", 31_500_00, 47),
        ]
        for key, name, number, amount, overdue in scenarios:
            acct, contact = _account(owner, org, name, number.replace("INV", "ACC"))
            inv = _invoice(app, org, acct, number, amount, overdue, today)
            made[key] = SeededAccount(acct, contact, inv, number, name, amount, overdue)

        for name, number, amount, overdue in BACKGROUND:
            acct, _ = _account(owner, org, name, number.replace("INV", "ACC"))
            _invoice(app, org, acct, number, amount, overdue, today)
    return made
