"""Invariant probes that need production prompt/schema code (hence under eval/sut): provider schema closure and
money-in-prompt. Both are measurements over inputs; neither touches an oracle field."""

from __future__ import annotations

from typing import Any

from baaki.agent.context import (
    BEGIN,
    InboundMessage,
    build_action_request,
    build_interpretation_request,
    provider_json_schema,
)
from baaki.contracts.candidate import AccountFacts
from eval.profiles import det_id
from eval.schema import CorpusItem


def _closed(schema: dict[str, Any]) -> bool:
    ok = True

    def walk(node: Any) -> None:
        nonlocal ok
        if isinstance(node, dict):
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                ok = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return ok


def provider_schema_closure() -> tuple[int, int]:
    """(closed, total) over the two provider-facing schemas — the locked invariant, unchanged in meaning."""
    schemas = [provider_json_schema("interpretation"), provider_json_schema("action_proposal")]
    return sum(1 for s in schemas if _closed(s)), len(schemas)


def _money_tokens(facts: AccountFacts) -> list[str]:
    toks: list[str] = []
    for c in facts.candidates:
        p = int(c.outstanding_paise)
        toks += [str(p), f"{p // 100}", f"{p // 100:,}", f"₹{p // 100:,}"]
    return toks


def money_in_prompt(items: list[CorpusItem], facts_by_profile: dict[str, AccountFacts]) -> tuple[int, int]:
    """(prompts containing a profile money value in their trusted part, prompts built). Must be (0, n)."""
    hits = prompts = 0
    for it in items:
        facts = facts_by_profile[it.profile]
        cid = det_id("probe", it.id)
        toks = _money_tokens(facts)
        r1, _ = build_interpretation_request(
            facts, InboundMessage(text=it.text, received_at=facts.as_of), correlation_id=cid, trace_id=cid
        )
        trusted = r1.system_text + r1.user_text.split(BEGIN, 1)[0]  # the untrusted block may legitimately quote amounts
        prompts += 1
        hits += any(t in trusted for t in toks)
        r2, _ = build_action_request(facts, interpretation=None, correlation_id=cid, trace_id=cid)
        prompts += 1
        hits += any(t in r2.system_text + r2.user_text for t in toks)
    return hits, prompts
