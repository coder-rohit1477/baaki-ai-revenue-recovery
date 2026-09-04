"""FixtureProvider — deterministic, offline, byte-stable replay (PHASE2B_PLAN §3, §9). Default provider everywhere.

Keyed by `prompt_hash`; an optional `default` script answers unknown hashes. Scripts are sequences of outcomes so a
retry path (e.g. TIMEOUT then OK) is reproducible. No clock, no randomness, no network, no secrets.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from baaki.domain.errors import ContractViolation
from baaki.providers.llm.base import (
    CallBudget,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    TokenUsage,
    run_with_retry,
)

FIXTURE_PROVIDER_NAME: Final[str] = "fixture"
FIXTURE_MODEL_ID: Final[str] = "fixture-model-v1"


class Outcome(BaseModel):
    """One attempt-level scripted result."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    status: ProviderStatus
    body: dict[str, Any] | list[Any] | None = None  # JSON output (OK)
    text: str | None = None  # non-JSON body (MALFORMED, REFUSAL)
    latency_ms: int = Field(default=10, ge=0)
    retry_after_s: float | None = Field(default=None, ge=0)
    provider_request_id: str | None = None
    usage: TokenUsage | None = None
    error_class: str | None = None


class Script(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    outcomes: tuple[Outcome, ...] = Field(min_length=1)


class FixtureMissing(ContractViolation):
    """Programming error: the test asked the fixture provider for an unscripted prompt."""


class FixtureProvider:
    def __init__(self, scripts: Mapping[str, Script] | None = None, *, default: Script | None = None) -> None:
        self._scripts: dict[str, Script] = dict(scripts or {})
        self._default = default
        self._cursor: dict[str, int] = {}
        self.requests: list[ProviderRequest] = []  # every request received, in order (never sent anywhere)

    @property
    def name(self) -> str:
        return FIXTURE_PROVIDER_NAME

    @property
    def model_id(self) -> str:
        return FIXTURE_MODEL_ID

    @classmethod
    def from_dir(cls, path: Path) -> FixtureProvider:
        """Load `*.json` files: {"prompt_hash": "<64 hex>" | "default", "outcomes": [Outcome, ...]}."""
        scripts: dict[str, Script] = {}
        default: Script | None = None
        for f in sorted(path.glob("*.json")):
            data = json.loads(f.read_bytes().decode("utf-8"))
            if "outcomes" not in data:
                continue  # not a script (e.g. golden hash files live in the same directory)
            script = Script(outcomes=tuple(Outcome.model_validate_json(json.dumps(o)) for o in data["outcomes"]))
            key = data["prompt_hash"]
            if key == "default":
                default = script
            else:
                scripts[str(key)] = script
        return cls(scripts, default=default)

    def add_script(self, prompt_hash: str, script: Script) -> None:
        self._scripts[prompt_hash] = script

    def script_for(self, request: ProviderRequest) -> Script:
        script = self._scripts.get(request.prompt_hash) or self._default
        if script is None:
            raise FixtureMissing(f"no fixture for prompt_hash {request.prompt_hash} ({request.prompt_template_id})")
        return script

    def complete_structured(self, request: ProviderRequest, budget: CallBudget) -> ProviderResponse:
        self.requests.append(request)
        script = self.script_for(request)
        key = request.prompt_hash

        def attempt(n: int) -> ProviderResponse:
            idx = self._cursor.get(key, 0)
            outcome = script.outcomes[min(idx, len(script.outcomes) - 1)]
            self._cursor[key] = idx + 1
            return ProviderResponse(
                status=outcome.status,
                raw_json=outcome.body if outcome.status is ProviderStatus.OK else None,
                raw_text=outcome.text,
                provider=self.name,
                model_id=self.model_id,
                provider_request_id=outcome.provider_request_id,
                latency_ms=outcome.latency_ms,
                attempts=1,
                usage=outcome.usage,
                error_class=outcome.error_class,
                retry_after_s=outcome.retry_after_s,
            )

        return run_with_retry(request, budget, attempt, provider=self.name, model_id=self.model_id)


def ok(body: dict[str, Any] | list[Any], **kw: Any) -> Outcome:
    return Outcome(status=ProviderStatus.OK, body=body, **kw)


def fault(status: ProviderStatus, text: str | None = None, **kw: Any) -> Outcome:
    if status is ProviderStatus.OK:
        raise ContractViolation("use ok() for OK outcomes")
    return Outcome(status=status, text=text, **kw)
