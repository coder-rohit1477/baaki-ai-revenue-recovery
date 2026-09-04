"""Deterministic idempotency key for recovery actions (ARCHITECTURE.md §3.4).

Includes: invoice_id, action_type, canonical_payload_hash, business_date, arm.
Excludes, deliberately: attempt_count and every timestamp — including either disables idempotency.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any
from uuid import UUID

from baaki.domain.enums import ActionType, Arm


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def idempotency_key(
    invoice_id: UUID,
    action_type: ActionType,
    canonical_payload_hash_: str,
    business_date: date,
    arm: Arm,
) -> str:
    material = "|".join(
        [str(invoice_id), str(action_type), canonical_payload_hash_, business_date.isoformat(), str(arm)]
    )
    return hashlib.sha256(material.encode()).hexdigest()
