"""ProviderResponse → AgentProposal (PHASE2B_PLAN §3.3, LOCKED). The validator remains the semantic authority.

OK + JSON object without A3/A4 violations → parse_status OK (enum/shape left to validator checks 04–05)
OK + A3/A4 violation, or JSON that is not an object → SCHEMA_VIOLATION, parsed NULL, raw kept
MALFORMED (non-JSON body)                            → UNPARSEABLE
TIMEOUT                                              → TIMEOUT
every other provider fault (incl. REFUSAL)           → PROVIDER_ERROR
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from baaki.contracts.agent_proposal import AgentProposal, RawJson, money_key_violations, typed_date_violations
from baaki.domain.enums import Arm, ParseStatus, ProposalKind
from baaki.policy.schemas import action_proposal_v1, interpretation_v1
from baaki.providers.llm.base import ProviderRequest, ProviderResponse, ProviderStatus

NON_JSON_TEXT_CAP_BYTES: Final[int] = 8192  # §11.2 envelope cap

# The stored `schema_version` is a DOMAIN fact, derived from the proposal kind — never from the provider
# request's wire name, which is constrained by the provider's own naming rules and carries no version.
# This must agree with the validator's SCHEMA_FOR_KIND (policy/validate/ladder.py); a mismatch would fail
# check 04 with UNKNOWN_SCHEMA_VERSION.
SCHEMA_VERSION_FOR_KIND: Final[dict[ProposalKind, str]] = {
    ProposalKind.INTERPRETATION: interpretation_v1.SCHEMA_VERSION,
    ProposalKind.ACTION_PROPOSAL: action_proposal_v1.SCHEMA_VERSION,
}


def _envelope(response: ProviderResponse) -> dict[str, Any]:
    """`raw_response` is NOT NULL: non-JSON bodies use the §11.2 envelope; body-less faults record the status only."""
    if response.raw_text is None:
        return {"status": str(response.status)}
    data = response.raw_text.encode("utf-8")
    truncated = len(data) > NON_JSON_TEXT_CAP_BYTES
    text = data[:NON_JSON_TEXT_CAP_BYTES].decode("utf-8", errors="ignore") if truncated else response.raw_text
    return {"non_json_text": text, "truncated": truncated, "status": str(response.status)}


def _confidence(parsed: dict[str, Any]) -> float | None:
    c = parsed.get("confidence")
    if isinstance(c, bool) or not isinstance(c, int | float):
        return None
    return float(c) if 0.0 <= float(c) <= 1.0 else None


def _evidence(parsed: dict[str, Any]) -> list[dict[str, str]]:
    ev = parsed.get("evidence")
    if not isinstance(ev, list):
        return []
    out: list[dict[str, str]] = []
    for item in ev:
        if isinstance(item, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in item.items()):
            out.append({str(k): str(v) for k, v in item.items()})
    return out


def map_response(
    response: ProviderResponse,
    request: ProviderRequest,
    *,
    kind: ProposalKind,
    source_text: str,
    account_id: UUID,
    business_date: date,
    invoice_hint: UUID | None,
    created_at: datetime,
) -> AgentProposal:
    parsed: dict[str, Any] | None = None
    raw: dict[str, Any] | list[Any]
    if response.status is ProviderStatus.OK:
        body = response.raw_json
        assert body is not None  # ProviderResponse invariant
        raw = body
        if isinstance(body, dict) and not money_key_violations(body) and not typed_date_violations(body):
            status, parsed = ParseStatus.OK, body
        else:
            status = ParseStatus.SCHEMA_VIOLATION  # A3/A4 violation or non-object JSON; raw kept as evidence
    elif response.status is ProviderStatus.MALFORMED:
        status, raw = ParseStatus.UNPARSEABLE, _envelope(response)
    elif response.status is ProviderStatus.TIMEOUT:
        status, raw = ParseStatus.TIMEOUT, _envelope(response)
    else:  # RATE_LIMITED, CLIENT_ERROR, SERVER_ERROR, UNAVAILABLE, NO_CREDENTIALS, REFUSAL, BUDGET_EXHAUSTED
        status, raw = ParseStatus.PROVIDER_ERROR, _envelope(response)
    return AgentProposal(
        proposal_id=request.correlation_id,
        trace_id=request.trace_id,
        account_id=account_id,
        kind=kind,
        invoice_id=invoice_hint,
        business_date=business_date,
        arm=Arm.TREATMENT,
        provider=response.provider,
        model_id=response.model_id,
        prompt_template_id=request.prompt_template_id,
        schema_version=SCHEMA_VERSION_FOR_KIND[kind],
        prompt_hash=request.prompt_hash,
        input_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        raw_response=RawJson(raw),
        parsed=parsed,
        parse_status=status,
        confidence=_confidence(parsed) if parsed is not None else None,
        evidence=_evidence(parsed) if parsed is not None else [],
        latency_ms=response.latency_ms,
        created_at=created_at,
    )
