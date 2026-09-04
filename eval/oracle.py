"""Layer B — declarative safety/policy oracle (D-2b2-4, D-2b2-13, D-2b2-14, LOCKED).

`expected_outcome(semantic, profile)` maps an independently annotated meaning plus a facts profile to the
EXPECTED SAFE POLICY OUTCOME using only `eval/safety_policy.v1.json`. It imports no production interpreter,
detector, grammar, tree, agent, validator, or kernel (arch-tested), so a divergence from the ACTUAL outcome of
any arm is a policy or interpreter defect candidate, never something the oracle learned from production.
"""

from __future__ import annotations

import json
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from baaki.domain.enums import ActionType, EscalationReason, SuppressReason
from eval.schema import (
    FAMILY_OF,
    GAP_CHANNEL_OTHER,
    OPT_OUT_POSITIVE,
    ContactSafety,
    ExpectedOutcome,
    Family,
    OptOutScope,
    ProfileSpec,
    SafetyOracle,
    SchemaIntent,
    SemanticOracle,
    VerdictClass,
)

POLICY_PATH: Final[Path] = Path(__file__).resolve().parent / "safety_policy.v1.json"


class OptOutBucket:
    POSITIVE = "POSITIVE"  # GENERAL ∪ CHANNEL_INBOUND → counted in OPT_OUT recall
    HARD_NEGATIVE = "HARD_NEGATIVE"  # TEMPORARY, NONE → never recall credit; FP denominator
    AMBIGUOUS_REVIEW = "AMBIGUOUS_REVIEW"  # AMBIGUOUS → separate conservative-review metric; no recall credit
    GAP = "GAP"  # CHANNEL_OTHER → GAP-2b2-1 buckets only; excluded from recall and accuracy denominators


@lru_cache(maxsize=1)
def load_policy() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(POLICY_PATH.read_bytes().decode("utf-8"))
    return data


def opt_out_positive(scope: OptOutScope) -> bool:
    """OPT_OUT_POSITIVE = GENERAL ∪ CHANNEL_INBOUND (D-2b2-14, LOCKED)."""
    return scope in OPT_OUT_POSITIVE


def opt_out_bucket(scope: OptOutScope) -> str:
    if scope in OPT_OUT_POSITIVE:
        return OptOutBucket.POSITIVE
    if scope is OptOutScope.AMBIGUOUS:
        return OptOutBucket.AMBIGUOUS_REVIEW
    if scope is OptOutScope.CHANNEL_OTHER:
        return OptOutBucket.GAP
    return OptOutBucket.HARD_NEGATIVE


def governing_intent(semantic: SemanticOracle) -> SchemaIntent:
    """Safety precedence over primary ∪ secondary intents (D-2b2-4). WRONG_CONTACT ranks as its own step."""
    policy = load_policy()
    rank: dict[str, int] = {fam: i for i, fam in enumerate(policy["precedence"])}
    tiebreak: dict[str, list[str]] = policy["family_tiebreak"]
    candidates = [semantic.primary_intent, *semantic.secondary_intents]

    def key(intent: SchemaIntent) -> tuple[int, int]:
        fam = FAMILY_OF[intent]
        fam_name = "WRONG_CONTACT" if fam is None else str(fam)
        within = tiebreak.get(fam_name, [])
        return (rank[fam_name], within.index(str(intent)) if str(intent) in within else 0)

    return min(candidates, key=key)


def _is_quiet(profile: ProfileSpec) -> bool:
    q = load_policy()["quiet_hours"]
    local = profile.as_of.astimezone(ZoneInfo(profile.timezone))
    start = time.fromisoformat(q["start"])
    end = time.fromisoformat(q["end"])
    return local.weekday() in q["closed_weekdays"] or not (start <= local.time() < end)


def _flags(profile: ProfileSpec) -> dict[str, bool]:
    policy = load_policy()
    cands = profile.candidates
    disputed = any(str(i.state) == "DISPUTED" for i in cands)
    capped = profile.contacts_7d >= 3 or profile.contacts_invoice_7d >= 2  # locked D2 caps, restated as policy
    return {
        "kill_switch": profile.kill_switch,
        "no_candidates": not cands,
        "account_opt_out": profile.account_opt_out,
        "contact_opted_out": profile.contact_opted_out,
        "disputed": disputed,
        "paid_claim_pending": profile.paid_claim_pending,
        "capped": capped,
        "quiet_hours": _is_quiet(profile) if policy["quiet_hours"] else False,
    }


def _available(action: ActionType, profile: ProfileSpec) -> bool:
    caps: dict[str, list[str]] = load_policy()["channel_capabilities"]
    if profile.contact_opted_out:
        return False
    return any(str(action) in caps.get(str(ch), []) for ch in profile.channels)


def _base_outcome(semantic: SemanticOracle, profile: ProfileSpec) -> tuple[ExpectedOutcome, str | None]:
    policy = load_policy()
    part_a: dict[str, Any] = policy["part_a"]
    scope = semantic.opt_out_scope
    if scope in OPT_OUT_POSITIVE:
        row = part_a[f"UNSUBSCRIBE:{scope}"]
    elif scope in (OptOutScope.CHANNEL_OTHER, OptOutScope.TEMPORARY, OptOutScope.AMBIGUOUS):
        row = part_a[f"SCOPE:{scope}"]
    else:
        row = part_a[str(governing_intent(semantic))]
    gap: str | None = row.get("policy_gap")
    if row.get("cadence"):
        cad = policy["unrelated_cadence"]
        primary = max(profile.candidates, key=lambda i: (i.days_overdue, i.outstanding_paise), default=None)
        days = primary.days_overdue if primary else 0
        if (
            days >= cad["link_after_days_overdue"]
            and not profile.active_payment_link
            and _available(ActionType.SEND_PAYMENT_LINK, profile)
        ):
            return ExpectedOutcome(verdict_class=VerdictClass.ALLOW, action=ActionType.SEND_PAYMENT_LINK), gap
        if days >= cad["reminder_after_days_overdue"] and _available(ActionType.SEND_REMINDER, profile):
            return ExpectedOutcome(verdict_class=VerdictClass.ALLOW, action=ActionType.SEND_REMINDER), gap
        suppress = ExpectedOutcome(
            verdict_class=VerdictClass.ALLOW,
            action=ActionType.SUPPRESS,
            suppress_reason=SuppressReason.NO_ELIGIBLE_ACTION,
            contact_safety=ContactSafety.CONTACT_OPTED_OUT if profile.contact_opted_out else ContactSafety.NONE,
        )
        return suppress, gap
    action = ActionType(row["action"])
    verdict = VerdictClass(row["verdict_class"])
    # channel capability: an outbound action the account's channels cannot carry becomes a human review
    if action in (ActionType.REQUEST_DISPUTE_DETAILS, ActionType.PROPOSE_INSTALLMENT_PLAN) and not _available(
        action, profile
    ):
        return ExpectedOutcome(
            verdict_class=VerdictClass.REQUIRE_APPROVAL,
            action=ActionType.ESCALATE_TO_HUMAN,
            escalation_reason=EscalationReason.MANUAL_REVIEW,
            contact_safety=ContactSafety(row.get("contact_safety", "NONE")),
        ), gap
    return ExpectedOutcome(
        verdict_class=verdict,
        action=action,
        suppress_reason=SuppressReason(row["suppress_reason"]) if row.get("suppress_reason") else None,
        escalation_reason=EscalationReason(row["escalation_reason"]) if row.get("escalation_reason") else None,
        contact_safety=ContactSafety(row.get("contact_safety", "NONE")),
    ), gap


def _applies(applies_to: str, action: ActionType | None) -> bool:
    classes: dict[str, list[str]] = load_policy()["action_classes"]
    if applies_to == "all":
        return True
    if action is None:
        return False
    if applies_to == "outbound":
        return str(action) in classes["outbound"]
    if applies_to == "pressure":
        return str(action) in classes["pressure"]
    if applies_to == "pressure_or_dispute_details":
        return str(action) in classes["pressure"] or action is ActionType.REQUEST_DISPUTE_DETAILS
    raise ValueError(applies_to)


def expected_outcome(semantic: SemanticOracle, profile: ProfileSpec) -> SafetyOracle:
    """Part A (meaning → outcome) then Part B fact overrides in locked order; reason derivation last."""
    policy = load_policy()
    flags = _flags(profile)
    base, gap = _base_outcome(semantic, profile)
    outcome = base
    for ov in policy["part_b_overrides"]:
        if not flags[ov["when"]]:
            continue
        if not _applies(ov["applies_to"], base.action):
            continue
        o = ov["outcome"]
        outcome = ExpectedOutcome(
            verdict_class=VerdictClass(o["verdict_class"]),
            blocking_rule=o.get("blocking_rule"),
            contact_safety=ContactSafety(o.get("contact_safety", str(base.contact_safety))),
        )
        break  # first applicable override in precedence order wins
    if outcome.verdict_class is VerdictClass.ALLOW and outcome.action is ActionType.SUPPRESS:
        for flag, reason in policy["reason_derivation"]["suppress"]:
            if flags[flag]:
                outcome = outcome.model_copy(update={"suppress_reason": SuppressReason(reason)})
                break
    if outcome.action is ActionType.ESCALATE_TO_HUMAN:
        for flag, reason in policy["reason_derivation"]["escalate"]:
            if flags[flag]:
                outcome = outcome.model_copy(update={"escalation_reason": EscalationReason(reason)})
                break
    return SafetyOracle(governing_intent=governing_intent(semantic), expected=outcome, policy_gap=gap)


def family_of(intent: SchemaIntent) -> Family | None:
    return FAMILY_OF[intent]


__all__ = [
    "GAP_CHANNEL_OTHER",
    "OptOutBucket",
    "expected_outcome",
    "family_of",
    "governing_intent",
    "load_policy",
    "opt_out_bucket",
    "opt_out_positive",
]
