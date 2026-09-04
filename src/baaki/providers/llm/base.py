"""AiProviderPort — the only shape the core knows about a model provider (PHASE2B_PLAN §3.1–§3.4).

Provider-neutral by construction: no vendor type appears here. The port never raises for provider faults; every
fault is a `ProviderStatus`. Programming errors (malformed request, budget misuse) raise.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baaki.domain.errors import ContractViolation

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")

MAX_ATTEMPTS_PER_CALL: Final[int] = 2  # one initial attempt + at most one transport retry (§7, §3.2)
GLOBAL_MAX_ATTEMPTS: Final[int] = 3  # per (account_id, business_date) workflow, retries included (§3.2)


class ProviderStatus(StrEnum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    CLIENT_ERROR = "CLIENT_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    REFUSAL = "REFUSAL"
    MALFORMED = "MALFORMED"
    UNAVAILABLE = "UNAVAILABLE"
    NO_CREDENTIALS = "NO_CREDENTIALS"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


RETRYABLE: Final[frozenset[ProviderStatus]] = frozenset(
    {ProviderStatus.TIMEOUT, ProviderStatus.SERVER_ERROR, ProviderStatus.RATE_LIMITED}
)


def compute_prompt_hash(system_text: str, user_text: str) -> str:
    """sha256(system_text + "\\n" + user_text) over UTF-8 bytes (§3.1)."""
    return hashlib.sha256((system_text + "\n" + user_text).encode("utf-8")).hexdigest()


class TokenUsage(BaseModel):
    model_config = _STRICT
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_estimate_micro_usd: int | None = Field(default=None, ge=0)


class ProviderRequest(BaseModel):
    model_config = _STRICT
    correlation_id: UUID  # == proposal_id; tracing metadata only — never an idempotency guarantee (§3.4)
    trace_id: UUID
    prompt_template_id: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=64, max_length=64)
    system_text: str = Field(min_length=1)
    user_text: str = Field(min_length=1)
    schema_name: str = Field(min_length=1)
    json_schema: dict[str, Any]
    timeout_s: float = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: Literal[0] = 0
    seed: int | None = None

    @model_validator(mode="after")
    def _hash_bound(self) -> ProviderRequest:
        if self.prompt_hash != compute_prompt_hash(self.system_text, self.user_text):
            raise ContractViolation("prompt_hash must equal sha256(system_text + '\\n' + user_text)")
        if self.json_schema.get("additionalProperties") is not False:
            raise ContractViolation("provider json_schema must be closed (additionalProperties=false)")
        return self


class ProviderResponse(BaseModel):
    model_config = _STRICT
    status: ProviderStatus
    raw_json: dict[str, Any] | list[Any] | None = None  # provider output parsed as JSON, untouched (OK only)
    raw_text: str | None = None  # verbatim body when the output is not JSON (MALFORMED, REFUSAL)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    provider_request_id: str | None = None
    latency_ms: int = Field(ge=0)
    attempts: int = Field(ge=0, le=MAX_ATTEMPTS_PER_CALL)  # attempts spent on THIS call (0 == never sent)
    usage: TokenUsage | None = None
    error_class: str | None = None  # sanitized exception class name only; never a message body
    retry_after_s: float | None = Field(default=None, ge=0)  # RATE_LIMITED hint, if the provider supplied one

    @model_validator(mode="after")
    def _shape(self) -> ProviderResponse:
        if self.status is ProviderStatus.OK and self.raw_json is None:
            raise ContractViolation("OK requires raw_json")
        if self.status is not ProviderStatus.OK and self.raw_json is not None:
            raise ContractViolation("only OK carries raw_json")
        if self.status is ProviderStatus.BUDGET_EXHAUSTED and self.attempts != 0:
            raise ContractViolation("BUDGET_EXHAUSTED means no attempt was sent")
        return self


class BudgetMisuse(ContractViolation):
    """Programming error: the budget was constructed or driven incorrectly."""


class CallBudget:
    """Global attempt budget for one (account_id, business_date) workflow (§3.2, LOCKED).

    Every HTTP attempt — initial or retry — consumes one unit *before* it is sent. The ceiling includes retries.
    A fourth attempt is impossible: `try_consume()` returns False and the caller must not send.
    """

    def __init__(self, max_attempts: int = GLOBAL_MAX_ATTEMPTS) -> None:
        if max_attempts < 1 or max_attempts > GLOBAL_MAX_ATTEMPTS:
            raise BudgetMisuse(f"max_attempts must be in 1..{GLOBAL_MAX_ATTEMPTS}")
        self.max_attempts = max_attempts
        self._used = 0
        self.log: list[str] = []  # "<call>:<attempt>" in consumption order — deterministic audit of spending

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return self.max_attempts - self._used

    def try_consume(self, label: str) -> bool:
        if self._used >= self.max_attempts:
            return False
        self._used += 1
        self.log.append(label)
        return True


class AiProviderPort(Protocol):
    """`complete_structured` is the whole provider surface: no tools, no streaming, no state (§7)."""

    @property
    def name(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def complete_structured(self, request: ProviderRequest, budget: CallBudget) -> ProviderResponse: ...


def run_with_retry(
    request: ProviderRequest,
    budget: CallBudget,
    attempt: Callable[[int], ProviderResponse],
    *,
    provider: str,
    model_id: str,
) -> ProviderResponse:
    """The single-retry policy every provider must use (§3.2).

    `attempt(n)` performs one attempt (n = 1 or 2) and returns an attempt-level response whose `attempts` field is
    ignored; this function accounts attempts against the global budget, enforces the per-call cap of two, retries
    only for RETRYABLE statuses whose backoff fits the remaining per-call timeout, and returns the final response
    with cumulative `latency_ms` and the true `attempts` count.
    """
    total_latency = 0
    last: ProviderResponse | None = None
    for n in range(1, MAX_ATTEMPTS_PER_CALL + 1):
        if not budget.try_consume(f"{request.prompt_template_id}:{n}"):
            if last is None:
                return ProviderResponse(
                    status=ProviderStatus.BUDGET_EXHAUSTED,
                    provider=provider,
                    model_id=model_id,
                    latency_ms=0,
                    attempts=0,
                )
            return last  # a retry was warranted but the global budget forbids it
        r = attempt(n)
        if r.status is ProviderStatus.BUDGET_EXHAUSTED:
            raise BudgetMisuse("an attempt function must never report BUDGET_EXHAUSTED")
        total_latency += r.latency_ms
        last = r.model_copy(update={"attempts": n, "latency_ms": total_latency})
        if r.status not in RETRYABLE:
            return last
        remaining_ms = int(request.timeout_s * 1000) - total_latency
        backoff_ms = int((r.retry_after_s or 0.0) * 1000)
        if remaining_ms <= 0 or backoff_ms > remaining_ms:
            return last  # the retry cannot fit inside this call's timeout
    assert last is not None
    return last
