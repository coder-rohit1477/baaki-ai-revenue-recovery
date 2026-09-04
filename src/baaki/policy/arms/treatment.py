"""TREATMENT arm — strategy boundary only (§13.2). Consumes an already-validated ACTION_PROPOSAL; makes no model call.

Band D (confidence < 0.50) discards the L0 choice (§4.3); the caller falls back to the RULES_ONLY tree at L1.
"""

from __future__ import annotations

from baaki.contracts.action_choice import ActionChoice
from baaki.contracts.normalized_action import NormalizedActionProposal
from baaki.domain.enums import DegradationLevel
from baaki.policy.ruleset import Ruleset


def choose(normalized: NormalizedActionProposal, ruleset: Ruleset) -> ActionChoice | None:
    band = ruleset.tier_cap.band_for(normalized.effective_confidence)
    if band.cap is None:
        return None  # discard → L1 fallback (pipeline records degradation_level = L1)
    return ActionChoice(
        action=normalized.action,
        contact_id=normalized.contact_id,
        channel=normalized.channel,
        template_id=normalized.template_id,
        followup_days=normalized.followup_days,
        confidence=normalized.effective_confidence,
        origin=DegradationLevel.L0,
    )
