"""CONTROL arm — static cadence D+3 / D+7 / D+15 (§10.2, §5.4). Origin L2."""

from __future__ import annotations

from baaki.contracts.action_choice import ActionChoice
from baaki.contracts.candidate import AccountFacts, CandidateInvoice
from baaki.domain.enums import ActionType, DegradationLevel, TemplatePurpose
from baaki.policy.ruleset import Ruleset
from baaki.rules_agent.tree import pick_contact_and_template


def choose(facts: AccountFacts, target: CandidateInvoice, ruleset: Ruleset) -> ActionChoice:
    if target.days_overdue in ruleset.control_cadence_days_overdue:
        pick = pick_contact_and_template(facts, ActionType.SEND_REMINDER, TemplatePurpose.REMINDER)
        if pick is not None:
            contact_id, channel, tpl = pick
            return ActionChoice(
                action=ActionType.SEND_REMINDER,
                contact_id=contact_id,
                channel=channel,
                template_id=tpl.template_id,
                origin=DegradationLevel.L2,
            )
    return ActionChoice(action=ActionType.SUPPRESS, origin=DegradationLevel.L2)
