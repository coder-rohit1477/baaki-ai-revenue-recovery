"""W07 record_agent_proposal — the only writer baaki_agent may execute (§6.6)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection

from baaki.contracts.agent_proposal import AgentProposal
from baaki.db.writers._call import call, jsonb


def record_agent_proposal(conn: Connection, proposal: AgentProposal) -> UUID:
    p = proposal
    row = call(
        conn,
        "SELECT baaki_write.record_agent_proposal(:proposal_id, :trace_id, :account_id, "
        "CAST(:kind AS baaki.proposal_kind), :invoice_id, :business_date, :provider, :model_id, "
        ":prompt_template_id, :schema_version, :prompt_hash, :input_hash, CAST(:raw_response AS jsonb), "
        "CAST(:parsed AS jsonb), CAST(:parse_status AS baaki.parse_status), CAST(:confidence AS numeric), "
        "CAST(:evidence AS jsonb), :latency_ms)",
        dict(proposal_id=p.proposal_id, trace_id=p.trace_id, account_id=p.account_id, kind=str(p.kind),
             invoice_id=p.invoice_id, business_date=p.business_date, provider=p.provider, model_id=p.model_id,
             prompt_template_id=p.prompt_template_id, schema_version=p.schema_version,
             prompt_hash=p.prompt_hash, input_hash=p.input_hash,
             raw_response=jsonb(p.raw_response.unwrap_for_audit()), parsed=jsonb(p.parsed),
             parse_status=str(p.parse_status), confidence=p.confidence, evidence=jsonb(p.evidence),
             latency_ms=p.latency_ms),
    ).scalar_one()
    return UUID(str(row))
