"""FixtureProvider: deterministic, offline, keyed by prompt_hash, scripted statuses, file loading."""
import socket
from pathlib import Path

import pytest

from baaki.agent.context import InboundMessage, build_interpretation_request
from baaki.providers.llm.base import CallBudget, ProviderStatus
from baaki.providers.llm.fixtures import (
    FIXTURE_MODEL_ID,
    FIXTURE_PROVIDER_NAME,
    FixtureMissing,
    FixtureProvider,
    Script,
    fault,
    ok,
)
from tests.conftest import _guarded_connect
from tests.phase2_helpers import AS_OF, C_EMAIL, facts

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "llm"
BODY = {"intent": "WILL_PAY_ON_DATE", "promised_date_raw": "Friday", "promised_amount_raw": None, "invoice_refs": [],
        "contact_correction": None, "sentiment": "NEUTRAL", "confidence": 0.9, "evidence": [{"field": "promised_date_raw", "quote": "by Friday"}]}


def request():
    r, _ = build_interpretation_request(facts(), InboundMessage(text="We will pay by Friday", received_at=AS_OF), correlation_id=C_EMAIL, trace_id=C_EMAIL)
    return r


def test_keyed_by_prompt_hash_and_byte_deterministic():
    r = request()
    p1 = FixtureProvider({r.prompt_hash: Script(outcomes=(ok(BODY, latency_ms=7, provider_request_id="fx-1"),))})
    p2 = FixtureProvider({r.prompt_hash: Script(outcomes=(ok(BODY, latency_ms=7, provider_request_id="fx-1"),))})
    a, b = p1.complete_structured(r, CallBudget()), p2.complete_structured(r, CallBudget())
    assert a == b and a.status is ProviderStatus.OK and a.raw_json == BODY and a.latency_ms == 7 and a.attempts == 1
    assert (a.provider, a.model_id) == (FIXTURE_PROVIDER_NAME, FIXTURE_MODEL_ID) and p1.requests == [r]


def test_unscripted_prompt_is_a_programming_error_not_a_provider_fault():
    with pytest.raises(FixtureMissing):
        FixtureProvider({}).complete_structured(request(), CallBudget())


def test_default_script_answers_unknown_hashes():
    p = FixtureProvider(default=Script(outcomes=(fault(ProviderStatus.SERVER_ERROR, error_class="InternalServerError"),
                                                 fault(ProviderStatus.SERVER_ERROR, error_class="InternalServerError"))))
    r = p.complete_structured(request(), CallBudget())
    assert (r.status, r.attempts, r.error_class) == (ProviderStatus.SERVER_ERROR, 2, "InternalServerError")


def test_every_status_is_scriptable_and_never_raises():
    for s in ProviderStatus:
        if s in (ProviderStatus.OK, ProviderStatus.BUDGET_EXHAUSTED):
            continue
        p = FixtureProvider(default=Script(outcomes=(fault(s, text="refused" if s is ProviderStatus.REFUSAL else None),)))
        r = p.complete_structured(request(), CallBudget())
        assert r.status is s and r.raw_json is None
    with pytest.raises(Exception):
        fault(ProviderStatus.OK)


def test_from_dir_loads_default_and_keyed_scripts():
    p = FixtureProvider.from_dir(FIX)
    r = request()
    resp = p.complete_structured(r, CallBudget())  # keyed script: TIMEOUT then OK
    assert (resp.status, resp.attempts, resp.latency_ms) == (ProviderStatus.OK, 2, 730)  # 700 ms timeout leaves room for the retry
    other = request().model_copy(update={"user_text": request().user_text + "x", "prompt_hash": "f" * 64})
    resp2 = p.complete_structured(other, CallBudget())  # default script
    assert resp2.status is ProviderStatus.OK and resp2.provider_request_id == "fixture-req-0001"


def test_fixture_provider_opens_no_socket():
    assert socket.socket.connect is _guarded_connect  # guard active during the default run
    FixtureProvider(default=Script(outcomes=(ok(BODY),))).complete_structured(request(), CallBudget())
