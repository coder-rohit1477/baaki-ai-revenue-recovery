"""RULES_ONLY decision tree → ActionChoice (PHASE2_PLAN §4). Deterministic; origin L1."""

from __future__ import annotations

from uuid import UUID

from baaki.contracts.account_snapshot import TemplateCatalogueEntry
from baaki.contracts.action_choice import ActionChoice
from baaki.contracts.candidate import AccountFacts, CandidateInvoice
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.domain.enums import ActionType, Channel, DegradationLevel, TemplatePurpose
from baaki.policy.ruleset import Ruleset


def pick_contact_and_template(
    facts: AccountFacts, action: ActionType, purpose: TemplatePurpose
) -> tuple[UUID, Channel, TemplateCatalogueEntry] | None:
    """First contactable contact (by id) whose channel has an active template for (action, purpose); template by id."""
    for contact in facts.contactable:
        tpls = sorted(
            (
                t
                for t in facts.template_catalogue
                if t.active and t.action_type is action and t.purpose is purpose and t.channel is contact.channel
            ),
            key=lambda t: t.template_id,
        )
        if tpls:
            return contact.contact_id, contact.channel, tpls[0]
    return None


def _outbound(
    facts: AccountFacts,
    action: ActionType,
    purpose: TemplatePurpose,
    origin: DegradationLevel,
    *,
    existing_link_ref: str | None = None,
) -> ActionChoice:
    pick = pick_contact_and_template(facts, action, purpose)
    if pick is None:
        return ActionChoice(action=ActionType.SUPPRESS, origin=origin)
    contact_id, channel, tpl = pick
    return ActionChoice(
        action=action,
        contact_id=contact_id,
        channel=channel,
        template_id=tpl.template_id,
        origin=origin,
        existing_link_ref=existing_link_ref,
    )


def choose(
    facts: AccountFacts, target: CandidateInvoice, ruleset: Ruleset, interpretation: NormalizedInterpretation | None
) -> ActionChoice:
    l1 = DegradationLevel.L1
    intent = interpretation.intent if interpretation is not None else None
    if intent == "REQUEST_INSTALLMENTS":
        return _outbound(facts, ActionType.PROPOSE_INSTALLMENT_PLAN, TemplatePurpose.INSTALLMENT_PROPOSAL, l1)
    if intent in ("NEEDS_DOCUMENT", "WRONG_CONTACT"):
        return ActionChoice(action=ActionType.ESCALATE_TO_HUMAN, origin=l1)
    if intent in ("DISPUTE_AMOUNT", "DISPUTE_DELIVERY"):
        return _outbound(facts, ActionType.REQUEST_DISPUTE_DETAILS, TemplatePurpose.DISPUTE_DETAILS_REQUEST, l1)
    if intent in ("WILL_PAY_ON_DATE", "ALREADY_PAID_CLAIM", "UNSUBSCRIBE"):
        return ActionChoice(action=ActionType.SUPPRESS, origin=l1)
    link = facts.active_payment_links.get(str(target.invoice_id))
    if target.days_overdue >= ruleset.rules_only.link_after_days_overdue and link is None:
        return _outbound(facts, ActionType.SEND_PAYMENT_LINK, TemplatePurpose.PAYMENT_LINK, l1)
    if target.days_overdue >= ruleset.rules_only.reminder_after_days_overdue:
        return _outbound(
            facts,
            ActionType.SEND_REMINDER,
            TemplatePurpose.REMINDER,
            l1,
            existing_link_ref=link.link_id if link else None,
        )
    return ActionChoice(action=ActionType.SUPPRESS, origin=l1)
