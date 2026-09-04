"""W08 record_validation_result (§6.8). trace/account/business_date are derived in-database."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from baaki.db.writers._call import call, jsonb
from baaki.domain.enums import RejectionReason, ValidationOutcome


def record_validation_result(
    conn: Connection, *, validation_id: UUID, proposal_id: UUID, outcome: ValidationOutcome,
    rejection_reasons: list[RejectionReason], normalized: dict[str, Any] | None,
    checks_run: list[dict[str, Any]], validator_version: str, validator_hash: str,
) -> UUID:
    row = call(
        conn,
        "SELECT baaki_write.record_validation_result(:validation_id, :proposal_id, "
        "CAST(:outcome AS baaki.validation_outcome), CAST(:reasons AS baaki.rejection_reason[]), "
        "CAST(:normalized AS jsonb), CAST(:checks_run AS jsonb), :validator_version, :validator_hash)",
        dict(validation_id=validation_id, proposal_id=proposal_id, outcome=str(outcome),
             reasons=[str(r) for r in rejection_reasons], normalized=jsonb(normalized),
             checks_run=jsonb(checks_run), validator_version=validator_version, validator_hash=validator_hash),
    ).scalar_one()
    return UUID(str(row))
