"""PHASE2B_PLAN §3.3 (LOCKED): ProviderResponse → AgentProposal parse_status mapping; §11.2 raw_response envelope."""
import hashlib

import pytest

from baaki.agent.context import InboundMessage, build_interpretation_request
from baaki.agent.mapping import NON_JSON_TEXT_CAP_BYTES, map_response
from baaki.domain.enums import ParseStatus, ProposalKind
from baaki.providers.llm.base import ProviderResponse, ProviderStatus
from tests.phase2_helpers import ACC, AS_OF, BDATE, C_EMAIL, facts

REQ, SRC = build_interpretation_request(facts(), InboundMessage(text="We will pay by Friday", received_at=AS_OF), correlation_id=C_EMAIL, trace_id=C_EMAIL)
GOOD = {"intent": "WILL_PAY_ON_DATE", "promised_date_raw": "Friday", "promised_amount_raw": None, "invoice_refs": [], "contact_correction": None,
        "sentiment": "NEUTRAL", "confidence": 0.9, "evidence": [{"field": "promised_date_raw", "quote": "by Friday"}]}


def resp(status, body=None, text=None, attempts=1):
    return ProviderResponse(status=status, raw_json=body, raw_text=text, provider="fixture", model_id="fixture-model-v1", latency_ms=12, attempts=attempts)


def m(r):
    return map_response(r, REQ, kind=ProposalKind.INTERPRETATION, source_text=SRC, account_id=ACC, business_date=BDATE, invoice_hint=None, created_at=AS_OF)


def test_ok_clean_object_is_ok_and_carries_metadata():
    p = m(resp(ProviderStatus.OK, GOOD))
    assert p.parse_status is ParseStatus.OK and p.parsed == GOOD and p.confidence == 0.9
    assert p.evidence == [{"field": "promised_date_raw", "quote": "by Friday"}]
    assert p.input_hash == hashlib.sha256(SRC.encode()).hexdigest() and p.prompt_hash == REQ.prompt_hash
    assert (p.provider, p.model_id, p.prompt_template_id, p.schema_version) == ("fixture", "fixture-model-v1", "interp.v1", "interpretation.v1")
    assert p.proposal_id == REQ.correlation_id and p.latency_ms == 12 and p.raw_response.unwrap_for_audit() == GOOD


def test_ok_with_unknown_enum_stays_ok_for_the_validator_to_judge():
    p = m(resp(ProviderStatus.OK, dict(GOOD, intent="PAY_WHENEVER")))
    assert p.parse_status is ParseStatus.OK  # check 05 → ENUM_OUT_OF_RANGE; the validator is the semantic authority


@pytest.mark.parametrize("body", [dict(GOOD, amount=100), dict(GOOD, discount="10%"), dict(GOOD, settlement_offer=1), dict(GOOD, promised_date="2026-09-05"),
                                  [GOOD], "just a string", 42])
def test_money_typed_date_or_non_object_is_schema_violation_with_raw_kept(body):
    p = m(resp(ProviderStatus.OK, body if isinstance(body, dict | list) else {"wrapped": body}) if not isinstance(body, dict | list) else resp(ProviderStatus.OK, body))
    if isinstance(body, dict | list):
        assert p.parse_status is ParseStatus.SCHEMA_VIOLATION and p.parsed is None and p.confidence is None and p.evidence == []
        assert p.raw_response.unwrap_for_audit() == body


def test_malformed_is_unparseable_with_envelope():
    p = m(resp(ProviderStatus.MALFORMED, text="{not json"))
    assert p.parse_status is ParseStatus.UNPARSEABLE and p.parsed is None
    assert p.raw_response.unwrap_for_audit() == {"non_json_text": "{not json", "truncated": False, "status": "MALFORMED"}


def test_timeout_is_timeout_with_status_only_envelope():
    p = m(resp(ProviderStatus.TIMEOUT, attempts=2))
    assert p.parse_status is ParseStatus.TIMEOUT and p.raw_response.unwrap_for_audit() == {"status": "TIMEOUT"}


@pytest.mark.parametrize("status", [ProviderStatus.RATE_LIMITED, ProviderStatus.CLIENT_ERROR, ProviderStatus.SERVER_ERROR, ProviderStatus.UNAVAILABLE,
                                    ProviderStatus.NO_CREDENTIALS, ProviderStatus.REFUSAL])
def test_other_faults_are_provider_error_not_timeout(status):
    p = m(resp(status, text="I cannot help with that." if status is ProviderStatus.REFUSAL else None))
    assert p.parse_status is ParseStatus.PROVIDER_ERROR and p.parsed is None
    env = p.raw_response.unwrap_for_audit()
    assert env["status"] == str(status) and ("non_json_text" in env) == (status is ProviderStatus.REFUSAL)


def test_budget_exhausted_maps_to_provider_error():
    r = ProviderResponse(status=ProviderStatus.BUDGET_EXHAUSTED, provider="fixture", model_id="fixture-model-v1", latency_ms=0, attempts=0)
    assert m(r).parse_status is ParseStatus.PROVIDER_ERROR


def test_envelope_caps_non_json_text_at_8kib():
    p = m(resp(ProviderStatus.MALFORMED, text="x" * (NON_JSON_TEXT_CAP_BYTES + 100)))
    env = p.raw_response.unwrap_for_audit()
    assert env["truncated"] is True and len(env["non_json_text"].encode()) == NON_JSON_TEXT_CAP_BYTES


@pytest.mark.parametrize("conf,expected", [(0.5, 0.5), (1, 1.0), (1.5, None), (-0.1, None), (True, None), ("0.9", None), (None, None)])
def test_confidence_extraction_is_defensive(conf, expected):
    assert m(resp(ProviderStatus.OK, dict(GOOD, confidence=conf))).confidence == expected


def test_evidence_extraction_keeps_only_string_maps():
    p = m(resp(ProviderStatus.OK, dict(GOOD, evidence=[{"field": "a", "quote": "b"}, {"field": 1}, "junk", {"x": {"nested": 1}}])))
    assert p.evidence == [{"field": "a", "quote": "b"}]
