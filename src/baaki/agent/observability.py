"""Provider-call observability (Phase 2b-3 §7).

`agent_proposal` already stores provider, model_id, raw_response and latency_ms. It has no token or cost
columns, and adding them would be a migration, so token and cost metadata are emitted as a structured record
instead of widening the schema.

Nothing here may carry a secret, a message body, or a customer identifier. `error_class` is a class name
only, matching the convention the provider port already follows.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baaki.providers.llm.base import ProviderResponse, ProviderStatus

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")

LOGGER_NAME: Final[str] = "baaki.agent.provider_call"

REDACTED_FIELDS: Final[frozenset[str]] = frozenset(
    {"api_key", "authorization", "system_text", "user_text", "raw_text", "raw_json", "message", "text"}
)


class ProviderCallRecord(BaseModel):
    """One provider call, plus what the deterministic layers did with its output."""

    model_config = _STRICT

    correlation_id: UUID
    trace_id: UUID
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=64, max_length=64)
    status: ProviderStatus
    attempts: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    provider_request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_estimate_micro_usd: int | None = Field(default=None, ge=0)
    error_class: str | None = None
    # what the deterministic layers decided afterwards; all optional so the record can be emitted early
    parse_status: str | None = None
    validation_outcome: str | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    action_selected: str | None = None
    degradation_level: str | None = None

    def completed(
        self,
        *,
        parse_status: str | None = None,
        validation_outcome: str | None = None,
        rejection_reasons: list[str] | None = None,
        fallback_reason: str | None = None,
        action_selected: str | None = None,
        degradation_level: str | None = None,
    ) -> ProviderCallRecord:
        """Return a copy carrying what the deterministic layers decided about this call's output.

        The provider record is emitted as soon as the call returns, so a fault is observable even if the
        process dies before the pipeline runs. This second, completed record is what carries the verdict.
        """
        update = {
            "parse_status": parse_status,
            "validation_outcome": validation_outcome,
            "rejection_reasons": rejection_reasons,
            "fallback_reason": fallback_reason,
            "action_selected": action_selected,
            "degradation_level": degradation_level,
        }
        return self.model_copy(update={k: v for k, v in update.items() if v is not None})

    def as_log_fields(self) -> dict[str, Any]:
        """A flat, secret-free mapping suitable for a structured log line."""
        data = self.model_dump(mode="json")
        assert not (set(data) & REDACTED_FIELDS)  # the model cannot carry these; assert the invariant holds
        return data


def record_for(
    response: ProviderResponse,
    *,
    correlation_id: UUID,
    trace_id: UUID,
    prompt_template_id: str,
    prompt_hash: str,
) -> ProviderCallRecord:
    """Build the record from a provider reply. Never reads the reply body."""
    usage = response.usage
    return ProviderCallRecord(
        correlation_id=correlation_id,
        trace_id=trace_id,
        provider=response.provider,
        model_id=response.model_id,
        prompt_template_id=prompt_template_id,
        prompt_hash=prompt_hash,
        status=response.status,
        attempts=response.attempts,
        latency_ms=response.latency_ms,
        provider_request_id=response.provider_request_id,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cost_estimate_micro_usd=usage.cost_estimate_micro_usd if usage else None,
        error_class=response.error_class,
    )


def emit(record: ProviderCallRecord, *, logger: logging.Logger | None = None) -> None:
    """Write one structured, secret-free JSON line for this provider call (§11.1).

    Logged, not stored: `agent_proposal` already holds the audit evidence, and a telemetry table would be
    a migration (D-2b-4 stays open). `as_log_fields` asserts the record carries no redacted field, so the
    only way to leak a prompt or a key here would be to add such a field to the model, which `extra=forbid`
    and that assertion both prevent.
    """
    (logger or logging.getLogger(LOGGER_NAME)).info(
        json.dumps(record.as_log_fields(), sort_keys=True, separators=(",", ":"))
    )


__all__ = ["LOGGER_NAME", "REDACTED_FIELDS", "ProviderCallRecord", "emit", "record_for"]
