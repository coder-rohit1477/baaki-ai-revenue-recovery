"""RULES_ONLY arm — deterministic tree over the same features the model sees. Origin L1."""

from __future__ import annotations

from baaki.contracts.action_choice import ActionChoice
from baaki.contracts.candidate import AccountFacts, CandidateInvoice
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.policy.ruleset import Ruleset
from baaki.rules_agent.tree import choose as tree_choose


def choose(
    facts: AccountFacts,
    target: CandidateInvoice,
    ruleset: Ruleset,
    interpretation: NormalizedInterpretation | None = None,
) -> ActionChoice:
    return tree_choose(facts, target, ruleset, interpretation)
