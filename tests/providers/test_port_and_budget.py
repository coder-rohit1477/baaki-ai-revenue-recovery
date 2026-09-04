"""PHASE2B_PLAN §3.1–§3.2: port contracts, ProviderStatus model, global CallBudget, single-retry policy."""
from uuid import uuid4

import pytest

from baaki.agent.context import provider_json_schema
from baaki.domain.errors import ContractViolation
from baaki.providers.llm.base import (
    GLOBAL_MAX_ATTEMPTS,
    MAX_ATTEMPTS_PER_CALL,
    RETRYABLE,
    BudgetMisuse,
    CallBudget,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    compute_prompt_hash,
    run_with_retry,
)

SCHEMA = provider_json_schema("interpretation")


def req(timeout_s=8.0, template="interp.v1"):
    sys_t, usr_t = "system", "user"
    return ProviderRequest(correlation_id=uuid4(), trace_id=uuid4(), prompt_template_id=template,
                           prompt_hash=compute_prompt_hash(sys_t, usr_t), system_text=sys_t, user_text=usr_t,
                           schema_name="interpretation.v1", json_schema=SCHEMA, timeout_s=timeout_s, max_output_tokens=100)


def resp(status, latency=10, retry_after=None, body=None):
    return ProviderResponse(status=status, raw_json=body if status is ProviderStatus.OK else None, provider="t", model_id="m",
                            latency_ms=latency, attempts=1, retry_after_s=retry_after)


def scripted(*outcomes):
    calls = []
    def attempt(n):
        calls.append(n)
        return outcomes[min(len(calls) - 1, len(outcomes) - 1)]
    return attempt, calls


# ── status model ─────────────────────────────────────────────────────────────────────────
def test_status_model_is_the_locked_set():
    assert {s.value for s in ProviderStatus} == {"OK", "TIMEOUT", "RATE_LIMITED", "CLIENT_ERROR", "SERVER_ERROR", "REFUSAL",
                                                  "MALFORMED", "UNAVAILABLE", "NO_CREDENTIALS", "BUDGET_EXHAUSTED"}
    assert RETRYABLE == {ProviderStatus.TIMEOUT, ProviderStatus.SERVER_ERROR, ProviderStatus.RATE_LIMITED}
    assert (MAX_ATTEMPTS_PER_CALL, GLOBAL_MAX_ATTEMPTS) == (2, 3)


def test_request_contract_binds_hash_and_requires_closed_schema():
    with pytest.raises(ContractViolation):
        ProviderRequest(correlation_id=uuid4(), trace_id=uuid4(), prompt_template_id="x", prompt_hash="0" * 64, system_text="s",
                        user_text="u", schema_name="interpretation.v1", json_schema=SCHEMA, timeout_s=1.0, max_output_tokens=1)
    open_schema = dict(SCHEMA, additionalProperties=True)
    with pytest.raises(ContractViolation):
        ProviderRequest(correlation_id=uuid4(), trace_id=uuid4(), prompt_template_id="x", prompt_hash=compute_prompt_hash("s", "u"),
                        system_text="s", user_text="u", schema_name="interpretation.v1", json_schema=open_schema, timeout_s=1.0,
                        max_output_tokens=1)
    r = req()
    assert r.temperature == 0
    with pytest.raises(Exception):
        r.model_copy(update={"temperature": 1}).model_validate(r.model_dump() | {"temperature": 1})


def test_response_contract_shape():
    with pytest.raises(ContractViolation):
        ProviderResponse(status=ProviderStatus.OK, provider="t", model_id="m", latency_ms=1, attempts=1)  # OK needs raw_json
    with pytest.raises(ContractViolation):
        ProviderResponse(status=ProviderStatus.TIMEOUT, raw_json={"a": 1}, provider="t", model_id="m", latency_ms=1, attempts=1)
    with pytest.raises(ContractViolation):
        ProviderResponse(status=ProviderStatus.BUDGET_EXHAUSTED, provider="t", model_id="m", latency_ms=0, attempts=1)
    with pytest.raises(Exception):
        ProviderResponse(status=ProviderStatus.TIMEOUT, provider="t", model_id="m", latency_ms=1, attempts=3)  # > per-call cap


# ── CallBudget ───────────────────────────────────────────────────────────────────────────
def test_budget_three_units_then_refuses_forever():
    b = CallBudget()
    assert [b.try_consume(f"c:{i}") for i in range(5)] == [True, True, True, False, False]
    assert b.used == 3 and b.remaining == 0 and b.log == ["c:0", "c:1", "c:2"]


def test_budget_construction_bounds():
    for bad in (0, 4, -1):
        with pytest.raises(BudgetMisuse):
            CallBudget(bad)


# ── run_with_retry ───────────────────────────────────────────────────────────────────────
def test_retry_once_on_timeout_then_ok():
    b = CallBudget(); attempt, calls = scripted(resp(ProviderStatus.TIMEOUT, 100), resp(ProviderStatus.OK, 20, body={"x": 1}))
    r = run_with_retry(req(), b, attempt, provider="t", model_id="m")
    assert (r.status, r.attempts, r.latency_ms, calls, b.used) == (ProviderStatus.OK, 2, 120, [1, 2], 2)


def test_no_retry_on_non_retryable():
    for s in (ProviderStatus.CLIENT_ERROR, ProviderStatus.REFUSAL, ProviderStatus.MALFORMED, ProviderStatus.NO_CREDENTIALS,
              ProviderStatus.UNAVAILABLE):
        b = CallBudget(); attempt, calls = scripted(resp(s), resp(ProviderStatus.OK, body={"x": 1}))
        r = run_with_retry(req(), b, attempt, provider="t", model_id="m")
        assert (r.status, r.attempts, calls, b.used) == (s, 1, [1], 1), s


def test_two_timeouts_is_terminal_timeout_with_two_attempts():
    b = CallBudget(); attempt, calls = scripted(resp(ProviderStatus.TIMEOUT), resp(ProviderStatus.TIMEOUT), resp(ProviderStatus.OK, body={}))
    r = run_with_retry(req(), b, attempt, provider="t", model_id="m")
    assert (r.status, r.attempts, calls, b.used) == (ProviderStatus.TIMEOUT, 2, [1, 2], 2)  # never a third attempt per call


def test_rate_limited_retries_only_when_retry_after_fits_timeout():
    b = CallBudget(); attempt, calls = scripted(resp(ProviderStatus.RATE_LIMITED, 100, retry_after=30.0), resp(ProviderStatus.OK, body={}))
    r = run_with_retry(req(timeout_s=8.0), b, attempt, provider="t", model_id="m")
    assert (r.status, r.attempts, calls) == (ProviderStatus.RATE_LIMITED, 1, [1])
    b = CallBudget(); attempt, calls = scripted(resp(ProviderStatus.RATE_LIMITED, 100, retry_after=1.0), resp(ProviderStatus.OK, body={}))
    r = run_with_retry(req(timeout_s=8.0), b, attempt, provider="t", model_id="m")
    assert (r.status, r.attempts, calls) == (ProviderStatus.OK, 2, [1, 2])
    b = CallBudget(); attempt, calls = scripted(resp(ProviderStatus.TIMEOUT, 8000), resp(ProviderStatus.OK, body={}))
    r = run_with_retry(req(timeout_s=8.0), b, attempt, provider="t", model_id="m")
    assert (r.status, r.attempts) == (ProviderStatus.TIMEOUT, 1)  # the timeout consumed the whole call budget


def test_global_budget_across_two_calls_never_sends_a_fourth_attempt():
    b = CallBudget()
    a1, c1 = scripted(resp(ProviderStatus.TIMEOUT), resp(ProviderStatus.TIMEOUT))
    r1 = run_with_retry(req(template="interp.v1"), b, a1, provider="t", model_id="m")
    assert (r1.attempts, b.used) == (2, 2)
    a2, c2 = scripted(resp(ProviderStatus.TIMEOUT), resp(ProviderStatus.OK, body={}))
    r2 = run_with_retry(req(template="propose.v1"), b, a2, provider="t", model_id="m")
    assert (r2.status, r2.attempts, c2, b.used) == (ProviderStatus.TIMEOUT, 1, [1], 3)  # retry warranted but budget forbids
    a3, c3 = scripted(resp(ProviderStatus.OK, body={}))
    r3 = run_with_retry(req(template="propose.v1"), b, a3, provider="t", model_id="m")
    assert (r3.status, r3.attempts, c3, b.used) == (ProviderStatus.BUDGET_EXHAUSTED, 0, [], 3)  # nothing sent
    assert len(c1) + len(c2) + len(c3) == 3 and b.log == ["interp.v1:1", "interp.v1:2", "propose.v1:1"]


def test_attempt_function_may_not_claim_budget_exhausted():
    b = CallBudget()
    with pytest.raises(BudgetMisuse):
        run_with_retry(req(), b, lambda n: ProviderResponse(status=ProviderStatus.BUDGET_EXHAUSTED, provider="t", model_id="m",
                                                            latency_ms=0, attempts=0), provider="t", model_id="m")
