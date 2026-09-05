"""Phase 2b-3: the live adapter, exercised entirely offline.

No network, no credentials, no spend. Every outcome the provider can produce is driven through a fake
transport, because a fault path that is only exercised in production is not a fault path that has been
tested. The adapter must never raise for a provider fault — each one is a `ProviderStatus`.
"""

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from baaki.providers.llm.base import (
    CallBudget,
    ProviderRequest,
    ProviderStatus,
    compute_prompt_hash,
)
from baaki.providers.llm.openai_provider import (
    LOCKED_MODEL_ID,
    ModelIdNotLocked,
    OpenAIProvider,
    estimate_cost_micro_usd,
)
from baaki.providers.llm.transport import TransportError, TransportOutcome

SYSTEM, USER = "system rules", "user facts"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent"],
    "properties": {"intent": {"type": "string"}},
}


def make_request(**kw: Any) -> ProviderRequest:
    return ProviderRequest(
        correlation_id=uuid4(),
        trace_id=uuid4(),
        prompt_template_id=kw.pop("template", "interp.v1"),
        prompt_hash=compute_prompt_hash(SYSTEM, USER),
        system_text=SYSTEM,
        user_text=USER,
        schema_name="interpretation",
        json_schema=SCHEMA,
        timeout_s=kw.pop("timeout_s", 8.0),
        max_output_tokens=400,
        **kw,
    )


class FakeTransport:
    """Returns scripted outcomes and records what it was asked to send."""

    def __init__(self, *outcomes: TransportOutcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float
    ) -> TransportOutcome:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout_s": timeout_s})
        return self.outcomes.pop(0) if self.outcomes else TransportOutcome(error=TransportError.UNAVAILABLE)


def http(status: int, body: Any, headers: dict[str, str] | None = None) -> TransportOutcome:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return TransportOutcome(status_code=status, body=raw, headers=headers or {})


def completion(content: Any, *, usage: dict[str, int] | None = None, **message: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    msg.update(message)
    out: dict[str, Any] = {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}
    if usage is not None:
        out["usage"] = usage
    return out


def provider(*outcomes: TransportOutcome, key: str | None = "sk-test-not-a-real-key") -> tuple[Any, FakeTransport]:
    t = FakeTransport(*outcomes)
    return OpenAIProvider(SecretStr(key) if key is not None else None, transport=t), t


# ── model lock (D-2b-2) ────────────────────────────────────────────────────────────────────
def test_the_locked_dated_snapshot_is_accepted():
    p, _ = provider()
    assert p.model_id == LOCKED_MODEL_ID and p.name == "openai"


@pytest.mark.parametrize("bad", ["gpt-4o-mini", "gpt-4o", "gpt-4o-mini-latest", "o4-mini"])
def test_an_undated_model_id_is_refused(bad):
    with pytest.raises(ModelIdNotLocked):
        OpenAIProvider(SecretStr("k"), model_id=bad, transport=FakeTransport())


def test_a_different_dated_snapshot_is_refused_rather_than_substituted():
    """Changing the model is a plan amendment, never something the adapter does on its own."""
    with pytest.raises(ModelIdNotLocked, match="plan amendment"):
        OpenAIProvider(SecretStr("k"), model_id="gpt-4o-mini-2030-01-01", transport=FakeTransport())


# ── the status matrix ──────────────────────────────────────────────────────────────────────
def test_ok_returns_the_parsed_structured_payload():
    p, t = provider(
        http(200, completion('{"intent": "UNSUBSCRIBE"}', usage={"prompt_tokens": 120, "completion_tokens": 8}))
    )
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is ProviderStatus.OK and r.raw_json == {"intent": "UNSUBSCRIBE"}
    assert r.attempts == 1 and r.provider == "openai" and r.model_id == LOCKED_MODEL_ID
    assert r.usage is not None and r.usage.input_tokens == 120 and r.usage.output_tokens == 8
    assert r.usage.cost_estimate_micro_usd == estimate_cost_micro_usd(120, 8)
    assert len(t.calls) == 1


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (TransportOutcome(error=TransportError.TIMEOUT), ProviderStatus.TIMEOUT),
        (TransportOutcome(error=TransportError.UNAVAILABLE), ProviderStatus.UNAVAILABLE),
        (http(401, {"error": "bad key"}), ProviderStatus.NO_CREDENTIALS),
        (http(403, {"error": "forbidden"}), ProviderStatus.NO_CREDENTIALS),
        (http(400, {"error": "bad request"}), ProviderStatus.CLIENT_ERROR),
        (http(404, {"error": "no such model"}), ProviderStatus.CLIENT_ERROR),
        (http(500, {"error": "boom"}), ProviderStatus.SERVER_ERROR),
        (http(503, {"error": "down"}), ProviderStatus.SERVER_ERROR),
    ],
)
def test_transport_and_http_faults_map_to_statuses_without_raising(outcome, expected):
    p, _ = provider(outcome, outcome)  # retryable ones get their second attempt
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is expected and r.raw_json is None


def test_rate_limited_carries_the_retry_after_hint():
    p, _ = provider(http(429, {"error": "slow down"}, {"retry-after": "0.25"}), http(429, {"error": "slow down"}))
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is ProviderStatus.RATE_LIMITED and r.attempts >= 1


@pytest.mark.parametrize(
    "body",
    [
        b"<html>gateway</html>",
        b"",
        json.dumps(completion("not json at all")).encode(),
        json.dumps({"choices": []}).encode(),
    ],
)
def test_unparseable_or_shapeless_bodies_are_malformed(body):
    p, _ = provider(TransportOutcome(status_code=200, body=body, headers={}))
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is ProviderStatus.MALFORMED and r.raw_json is None


def test_a_scalar_json_payload_is_malformed_not_ok():
    p, _ = provider(http(200, completion("42")))
    assert p.complete_structured(make_request(), CallBudget()).status is ProviderStatus.MALFORMED


def test_a_refusal_is_its_own_status():
    p, _ = provider(http(200, completion(None, refusal="I can't help with that")))
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is ProviderStatus.REFUSAL and r.raw_json is None


def test_a_content_filter_finish_reason_is_a_refusal():
    body = {"choices": [{"index": 0, "message": {"content": None}, "finish_reason": "content_filter"}]}
    p, _ = provider(http(200, body))
    assert p.complete_structured(make_request(), CallBudget()).status is ProviderStatus.REFUSAL


# ── credentials ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", [None, ""])
def test_a_missing_key_degrades_instead_of_crashing(key):
    p, t = provider(key=key)
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is ProviderStatus.NO_CREDENTIALS and r.attempts == 0
    assert t.calls == [], "no request may be sent without a credential"


def test_the_key_is_sent_as_a_bearer_header_and_never_appears_in_the_response():
    p, t = provider(http(200, completion('{"intent": "NO_CLEAR_INTENT"}')))
    r = p.complete_structured(make_request(), CallBudget())
    assert t.calls[0]["headers"]["Authorization"] == "Bearer sk-test-not-a-real-key"
    assert "sk-test-not-a-real-key" not in r.model_dump_json()


def test_the_secret_is_not_printable_from_the_provider_or_settings():
    p, _ = provider()
    assert "sk-test" not in repr(p.__dict__.get("_api_key"))
    assert "sk-test" not in str(SecretStr("sk-test-not-a-real-key"))


# ── request construction ───────────────────────────────────────────────────────────────────
def test_the_request_uses_strict_structured_output_and_the_locked_ceilings():
    p, t = provider(http(200, completion('{"intent": "NO_CLEAR_INTENT"}')))
    p.complete_structured(make_request(), CallBudget())
    sent = t.calls[0]["payload"]
    assert sent["model"] == LOCKED_MODEL_ID and sent["temperature"] == 0 and sent["max_tokens"] == 400
    rf = sent["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == SCHEMA and rf["json_schema"]["name"] == "interpretation"
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert t.calls[0]["timeout_s"] == 8.0


def test_the_adapter_sends_the_prompt_text_verbatim():
    """Untrusted-text handling belongs to the context builder; the adapter must not touch it."""
    p, t = provider(http(200, completion('{"intent": "NO_CLEAR_INTENT"}')))
    p.complete_structured(make_request(), CallBudget())
    assert t.calls[0]["payload"]["messages"][0]["content"] == SYSTEM
    assert t.calls[0]["payload"]["messages"][1]["content"] == USER


# ── budget and retry are delegated, not reimplemented ──────────────────────────────────────
def test_retryable_faults_use_at_most_two_attempts_per_call():
    p, t = provider(http(500, {"e": 1}), http(500, {"e": 2}), http(500, {"e": 3}))
    r = p.complete_structured(make_request(), CallBudget())
    assert r.attempts == 2 and len(t.calls) == 2


def test_a_non_retryable_fault_is_not_retried():
    p, t = provider(http(400, {"e": 1}), http(200, completion('{"intent": "X"}')))
    r = p.complete_structured(make_request(), CallBudget())
    assert r.status is ProviderStatus.CLIENT_ERROR and r.attempts == 1 and len(t.calls) == 1


def test_an_exhausted_workflow_budget_prevents_any_send():
    budget = CallBudget()
    for n in range(3):
        assert budget.try_consume(f"prior:{n}")
    p, t = provider(http(200, completion('{"intent": "X"}')))
    r = p.complete_structured(make_request(), budget)
    assert r.status is ProviderStatus.BUDGET_EXHAUSTED and r.attempts == 0 and t.calls == []


def test_three_attempts_is_the_ceiling_across_two_calls():
    """≤3 calls per recovery cycle, retries included — the locked D-2b-9 ceiling."""
    budget = CallBudget()
    p, t = provider(*[http(500, {"e": n}) for n in range(5)])
    p.complete_structured(make_request(template="interp.v1"), budget)
    p.complete_structured(make_request(template="propose.v1", timeout_s=6.0), budget)
    assert budget.used == 3 and len(t.calls) == 3


# ── cost estimate ──────────────────────────────────────────────────────────────────────────
def test_cost_estimate_is_integer_micro_usd():
    assert estimate_cost_micro_usd(0, 0) == 0
    assert estimate_cost_micro_usd(1_000_000, 0) == 150_000
    assert estimate_cost_micro_usd(0, 1_000_000) == 600_000
    assert isinstance(estimate_cost_micro_usd(1234, 567), int)


def test_absent_or_malformed_usage_is_reported_as_none():
    p, _ = provider(http(200, completion('{"intent": "X"}')))
    assert p.complete_structured(make_request(), CallBudget()).usage is None
    p2, _ = provider(http(200, completion('{"intent": "X"}', usage={"prompt_tokens": -1, "completion_tokens": 2})))
    assert p2.complete_structured(make_request(), CallBudget()).usage is None
