"""The single credential-gated live call (Phase 2b-3 §9).

Deselected by default (`addopts = -q -m 'not network'`) and skipped without a credential, so the normal
suite is network-free and spend-free. Run deliberately with:

    OPENAI_API_KEY=... uv run pytest tests/providers/test_openai_live_smoke.py -m network

Scope is provider, schema and adapter validation **only**. This test touches no database, builds no
`AccountFacts`, runs no pipeline, and therefore cannot create a recovery action, send a message, move money,
mark an invoice paid, create a payment, or alter the ledger. Malformed and unsafe payloads are proven to
reach deterministic rejection offline, in `test_openai_adapter.py` and `tests/agent/test_live_adapter_e2e.py`.
"""

import json
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from baaki.agent.observability import record_for
from baaki.policy.schemas.interpretation_v1 import InterpretationV1
from baaki.providers.llm.base import CallBudget, ProviderRequest, ProviderStatus, compute_prompt_hash
from baaki.providers.llm.openai_provider import LOCKED_MODEL_ID, ModelIdNotLocked, OpenAIProvider

pytestmark = pytest.mark.network

# Freshly authored for this test. Not drawn from any corpus, and never from protected G4 material.
SMOKE_MESSAGE = "Hi, we will settle invoice INV-1 on Friday by bank transfer. Please do not call before then."


def _key() -> SecretStr:
    raw = os.environ.get("OPENAI_API_KEY")
    if not raw:
        pytest.skip("OPENAI_API_KEY not set: the live smoke is credential-gated")
    return SecretStr(raw)


@pytest.fixture(scope="module")
def live_response():
    from baaki.agent.context import (
        CALL1_MAX_OUTPUT_TOKENS,
        CALL1_TIMEOUT_S,
        load_template,
        provider_json_schema,
    )

    key = _key()
    system_text = load_template("interp.v1")
    user_text = "<<<BAAKI_UNTRUSTED_MESSAGE_BEGIN>>>\n" + SMOKE_MESSAGE + "\n<<<BAAKI_UNTRUSTED_MESSAGE_END>>>"
    request = ProviderRequest(
        correlation_id=uuid4(),
        trace_id=uuid4(),
        prompt_template_id="interp.v1",
        prompt_hash=compute_prompt_hash(system_text, user_text),
        system_text=system_text,
        user_text=user_text,
        schema_name="interpretation",
        json_schema=provider_json_schema("interpretation"),
        timeout_s=CALL1_TIMEOUT_S,
        max_output_tokens=CALL1_MAX_OUTPUT_TOKENS,
    )
    provider = OpenAIProvider(key)  # real UrllibTransport
    response = provider.complete_structured(request, CallBudget())
    return request, response


def test_the_locked_model_snapshot_is_available(live_response):
    """D-2b-2: availability is confirmed here, or the phase STOPs. Never an automatic substitution."""
    request, response = live_response
    if response.status is ProviderStatus.CLIENT_ERROR:
        pytest.fail(
            f"the locked snapshot {LOCKED_MODEL_ID} may be unavailable (CLIENT_ERROR, "
            f"error_class={response.error_class}); STOP and re-lock D-2b-2 rather than substituting a model"
        )
    if response.status is ProviderStatus.NO_CREDENTIALS:
        pytest.fail("the supplied OPENAI_API_KEY was rejected by the provider")
    assert response.status is ProviderStatus.OK, f"live call returned {response.status}"
    assert response.model_id == LOCKED_MODEL_ID


def test_structured_output_validates_against_the_locked_interpretation_schema(live_response):
    _, response = live_response
    assert response.raw_json is not None and isinstance(response.raw_json, dict)
    parsed = InterpretationV1.model_validate_json(json.dumps(response.raw_json))
    assert str(parsed.intent) in {
        "WILL_PAY_ON_DATE",
        "REQUEST_INSTALLMENTS",
        "DISPUTE_AMOUNT",
        "DISPUTE_DELIVERY",
        "ALREADY_PAID_CLAIM",
        "WRONG_CONTACT",
        "NEEDS_DOCUMENT",
        "UNSUBSCRIBE",
        "NO_CLEAR_INTENT",
    }


def test_latency_and_cost_are_recorded_without_exposing_the_secret(live_response, capsys):
    request, response = live_response
    rec = record_for(
        response,
        correlation_id=request.correlation_id,
        trace_id=request.trace_id,
        prompt_template_id=request.prompt_template_id,
        prompt_hash=request.prompt_hash,
    )
    fields = rec.as_log_fields()
    assert fields["latency_ms"] >= 0 and fields["attempts"] in (1, 2)
    blob = json.dumps(fields)
    assert "sk-" not in blob and "Bearer" not in blob and SMOKE_MESSAGE not in blob
    print(  # the one place the smoke reports numbers; deliberately secret-free
        f"LIVE SMOKE  status={fields['status']} attempts={fields['attempts']} "
        f"latency_ms={fields['latency_ms']} input_tokens={fields['input_tokens']} "
        f"output_tokens={fields['output_tokens']} cost_micro_usd={fields['cost_estimate_micro_usd']}"
    )


def test_the_adapter_refuses_an_unlocked_model_even_with_a_live_key():
    with pytest.raises(ModelIdNotLocked):
        OpenAIProvider(_key(), model_id="gpt-4o-mini")
