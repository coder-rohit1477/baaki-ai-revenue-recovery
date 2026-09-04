"""W09 record_policy_decision (§6.7–6.9). Takes an ExecutableDecision | NonExecutableDecision."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from baaki.contracts.policy_decision import ExecutableDecision, NonExecutableDecision
from baaki.db.writers._call import call, jsonb


def record_policy_decision(
    conn: Connection, decision: ExecutableDecision | NonExecutableDecision, *,
    candidate_invoice_ids: list[UUID],
    trace_id: UUID | None = None, account_id: UUID | None = None, business_date: date | None = None,
) -> UUID:
    d = decision
    payload: dict[str, Any] | None = None
    if isinstance(d, ExecutableDecision):
        payload = d.canonical_payload.model_dump(mode="json")
    row = call(
        conn,
        "SELECT baaki_write.record_policy_decision(:decision_id, :proposal_id, :validation_id, :trace_id, "
        ":account_id, :business_date, :invoice_id, CAST(:arm AS baaki.arm), CAST(:verdict AS baaki.verdict), "
        "CAST(:tier AS smallint), CAST(:action_type AS baaki.action_type), CAST(:payload AS jsonb), :defer_until, "
        "CAST(:matched_rules AS text[]), CAST(:blocking_rules AS jsonb), CAST(:effective_confidence AS numeric), "
        ":policy_version, "
        ":kernel_version, :policy_hash, :snapshot_hash, CAST(:degradation_level AS baaki.degradation_level), "
        "CAST(:candidates AS uuid[]))",
        dict(decision_id=d.decision_id, proposal_id=d.proposal_id, validation_id=d.validation_id,
             trace_id=trace_id if d.proposal_id is None else d.trace_id,
             account_id=account_id if d.proposal_id is None else d.account_id,
             business_date=business_date if d.proposal_id is None else d.business_date,
             invoice_id=d.invoice_id, arm=str(d.arm), verdict=str(d.verdict), tier=d.tier,
             action_type=str(d.action_type) if d.action_type is not None else None, payload=jsonb(payload),
             defer_until=d.defer_until, matched_rules=list(d.matched_rules),
             blocking_rules=jsonb(d.blocking_rules), effective_confidence=d.effective_confidence,
             policy_version=d.policy_version, kernel_version=d.kernel_version, policy_hash=d.policy_hash,
             snapshot_hash=d.snapshot_hash, degradation_level=str(d.degradation_level),
             candidates=[str(c) for c in candidate_invoice_ids]),
    ).scalar_one()
    return UUID(str(row))
