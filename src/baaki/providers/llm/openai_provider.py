"""Live OpenAI adapter behind the provider-neutral port (Phase 2b-3).

This is the whole live surface. It turns a `ProviderRequest` into one HTTPS call and the reply into a
`ProviderResponse`, and it does nothing else: it never sees account facts, never decides an action, never
touches the database, and never raises for a provider fault — every fault is a `ProviderStatus`.

Two invariants worth stating because the rest of the system leans on them:

* The model's output is a **proposal**. Whatever comes back is validated, then adjudicated by the policy
  kernel, before anything can happen. A malformed, hostile or absent reply degrades to the deterministic
  rules path; it cannot produce an effect of its own.
* Retry and budget policy live in `run_with_retry` (D-2b-9). This adapter performs one attempt when asked
  and reports what happened, so the ≤2-per-call and ≤3-per-workflow ceilings cannot drift.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Final

from pydantic import SecretStr

from baaki.domain.errors import ContractViolation
from baaki.providers.llm.base import (
    CallBudget,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    TokenUsage,
    run_with_retry,
)
from baaki.providers.llm.transport import Transport, TransportError, TransportOutcome, UrllibTransport

PROVIDER_NAME: Final[str] = "openai"
DEFAULT_ENDPOINT: Final[str] = "https://api.openai.com/v1/chat/completions"
LOCKED_MODEL_ID: Final[str] = "gpt-4o-mini-2024-07-18"  # D-2b-2 LOCKED; substitution is never automatic
DATED_SNAPSHOT: Final[re.Pattern[str]] = re.compile(r"-\d{4}-\d{2}-\d{2}$")
NON_JSON_TEXT_CAP_BYTES: Final[int] = 8192  # D-2b-9 locked

# Published list price for the locked snapshot, in micro-USD per 1M tokens. Integers only: this is a cost
# *estimate* for observability, never a billed figure and never money that reaches a payload.
INPUT_MICRO_USD_PER_M: Final[int] = 150_000
OUTPUT_MICRO_USD_PER_M: Final[int] = 600_000


class ModelIdNotLocked(ContractViolation):
    """The adapter was constructed with a model id that is not the locked dated snapshot."""


def estimate_cost_micro_usd(input_tokens: int, output_tokens: int) -> int:
    """Local list-price estimate in micro-USD. Integer arithmetic only."""
    return (input_tokens * INPUT_MICRO_USD_PER_M + output_tokens * OUTPUT_MICRO_USD_PER_M) // 1_000_000


def _cap_text(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")[:NON_JSON_TEXT_CAP_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def _retry_after(headers: dict[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


class OpenAIProvider:
    """`AiProviderPort` over the OpenAI chat-completions API with strict structured outputs."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        model_id: str = LOCKED_MODEL_ID,
        transport: Transport | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
    ) -> None:
        if not DATED_SNAPSHOT.search(model_id):
            raise ModelIdNotLocked(f"model id must be a dated snapshot, got {model_id!r}")
        if model_id != LOCKED_MODEL_ID:
            raise ModelIdNotLocked(
                f"model id {model_id!r} is not the locked snapshot {LOCKED_MODEL_ID!r}; "
                "changing it is a plan amendment (D-2b-2), never an automatic substitution"
            )
        self._api_key = api_key
        self._model_id = model_id
        self._transport = transport if transport is not None else UrllibTransport()
        self._endpoint = endpoint

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def model_id(self) -> str:
        return self._model_id

    # ── request construction ───────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        assert self._api_key is not None  # guarded by complete_structured before any call
        return {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.system_text},
                {"role": "user", "content": request.user_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "schema": request.json_schema, "strict": True},
            },
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        return payload

    # ── reply interpretation ───────────────────────────────────────────────────────────────
    def _from_outcome(self, outcome: TransportOutcome, latency_ms: int) -> ProviderResponse:
        base: dict[str, Any] = {
            "provider": PROVIDER_NAME,
            "model_id": self._model_id,
            "latency_ms": latency_ms,
            "attempts": 1,
            "provider_request_id": outcome.headers.get("x-request-id"),
            "error_class": outcome.error_class,
        }
        if outcome.error is TransportError.TIMEOUT:
            return ProviderResponse(status=ProviderStatus.TIMEOUT, **base)
        if outcome.error is TransportError.UNAVAILABLE:
            return ProviderResponse(status=ProviderStatus.UNAVAILABLE, **base)

        code = outcome.status_code
        if code in (401, 403):
            return ProviderResponse(status=ProviderStatus.NO_CREDENTIALS, **base)
        if code == 429:
            return ProviderResponse(
                status=ProviderStatus.RATE_LIMITED, retry_after_s=_retry_after(outcome.headers), **base
            )
        if code is not None and 500 <= code < 600:
            return ProviderResponse(status=ProviderStatus.SERVER_ERROR, **base)
        if not outcome.ok:
            return ProviderResponse(status=ProviderStatus.CLIENT_ERROR, **base)

        try:
            envelope = json.loads(outcome.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ProviderResponse(status=ProviderStatus.MALFORMED, raw_text=_cap_text(outcome.body), **base)
        if not isinstance(envelope, dict):
            return ProviderResponse(status=ProviderStatus.MALFORMED, raw_text=_cap_text(outcome.body), **base)

        usage = self._usage(envelope.get("usage"))
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ProviderResponse(
                status=ProviderStatus.MALFORMED, raw_text=_cap_text(outcome.body), usage=usage, **base
            )
        message = choices[0].get("message")
        message = message if isinstance(message, dict) else {}
        if message.get("refusal") or choices[0].get("finish_reason") == "content_filter":
            refusal = message.get("refusal")
            return ProviderResponse(
                status=ProviderStatus.REFUSAL,
                raw_text=_cap_text(str(refusal).encode("utf-8")) if refusal else None,
                usage=usage,
                **base,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return ProviderResponse(
                status=ProviderStatus.MALFORMED, raw_text=_cap_text(outcome.body), usage=usage, **base
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return ProviderResponse(
                status=ProviderStatus.MALFORMED, raw_text=_cap_text(content.encode("utf-8")), usage=usage, **base
            )
        if not isinstance(parsed, dict | list):
            # a bare scalar is not an object the mapping layer can validate; A4 will reject it either way
            return ProviderResponse(
                status=ProviderStatus.MALFORMED, raw_text=_cap_text(content.encode("utf-8")), usage=usage, **base
            )
        return ProviderResponse(status=ProviderStatus.OK, raw_json=parsed, usage=usage, **base)

    def _usage(self, raw: Any) -> TokenUsage | None:
        if not isinstance(raw, dict):
            return None
        try:
            input_tokens = int(raw.get("prompt_tokens", 0))
            output_tokens = int(raw.get("completion_tokens", 0))
        except (TypeError, ValueError):
            return None
        if input_tokens < 0 or output_tokens < 0:
            return None
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate_micro_usd=estimate_cost_micro_usd(input_tokens, output_tokens),
        )

    # ── port surface ───────────────────────────────────────────────────────────────────────
    def complete_structured(self, request: ProviderRequest, budget: CallBudget) -> ProviderResponse:
        if self._api_key is None or not self._api_key.get_secret_value():
            # No credential is a provider status, not a crash: the workflow degrades to the rules path.
            return ProviderResponse(
                status=ProviderStatus.NO_CREDENTIALS,
                provider=PROVIDER_NAME,
                model_id=self._model_id,
                latency_ms=0,
                attempts=0,
            )

        def attempt(_n: int) -> ProviderResponse:
            started = time.monotonic()
            outcome = self._transport.post_json(
                self._endpoint,
                headers=self._headers(),
                payload=self._payload(request),
                timeout_s=request.timeout_s,
            )
            return self._from_outcome(outcome, int((time.monotonic() - started) * 1000))

        return run_with_retry(request, budget, attempt, provider=PROVIDER_NAME, model_id=self._model_id)


__all__ = [
    "DEFAULT_ENDPOINT",
    "LOCKED_MODEL_ID",
    "ModelIdNotLocked",
    "OpenAIProvider",
    "PROVIDER_NAME",
    "estimate_cost_micro_usd",
]
