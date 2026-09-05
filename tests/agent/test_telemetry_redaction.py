"""Phase 2b-4: the telemetry line is the one place a prompt or a key could plausibly escape. It does not."""

import json
import logging
from uuid import uuid4

import pytest

from baaki.agent.observability import LOGGER_NAME, REDACTED_FIELDS, ProviderCallRecord, emit, record_for
from baaki.providers.llm.base import ProviderResponse, ProviderStatus, TokenUsage

SECRET = "sk-test-not-a-real-key"
PROMPT = "You are a collections analyst. Message: Bhai abhi 10k de sakta hu."


def a_record(**kw) -> ProviderCallRecord:
    base = dict(
        correlation_id=uuid4(),
        trace_id=uuid4(),
        provider="openai",
        model_id="gpt-4o-mini-2024-07-18",
        prompt_template_id="interp.v1",
        prompt_hash="a" * 64,
        status=ProviderStatus.OK,
        attempts=1,
        latency_ms=120,
    )
    base.update(kw)
    return ProviderCallRecord(**base)


def test_the_record_model_cannot_even_hold_a_secret_or_a_prompt():
    for field in sorted(REDACTED_FIELDS):
        with pytest.raises(Exception):  # extra="forbid"
            a_record(**{field: SECRET})


def test_the_emitted_line_is_json_and_carries_no_redacted_field(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        emit(a_record())
    line = caplog.records[-1].getMessage()
    fields = json.loads(line)
    assert not (set(fields) & REDACTED_FIELDS)
    assert SECRET not in line and PROMPT not in line


def test_a_provider_reply_never_contributes_its_body_to_the_record():
    response = ProviderResponse(
        status=ProviderStatus.OK,
        raw_json={"intent": "WILL_PAY_ON_DATE", "note": PROMPT},
        provider="openai",
        model_id="gpt-4o-mini-2024-07-18",
        latency_ms=90,
        attempts=1,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost_estimate_micro_usd=7),
    )
    record = record_for(
        response, correlation_id=uuid4(), trace_id=uuid4(), prompt_template_id="interp.v1", prompt_hash="b" * 64
    )
    dumped = json.dumps(record.as_log_fields())
    assert PROMPT not in dumped and "WILL_PAY_ON_DATE" not in dumped
    assert record.cost_estimate_micro_usd == 7


def test_completion_adds_only_deterministic_verdict_fields():
    record = a_record().completed(
        parse_status="OK", validation_outcome="REJECT", rejection_reasons=["DISCOUNT_NOT_PERMITTED"],
        degradation_level="L1",
    )
    fields = record.as_log_fields()
    assert fields["validation_outcome"] == "REJECT"
    assert fields["rejection_reasons"] == ["DISCOUNT_NOT_PERMITTED"]
    assert not (set(fields) & REDACTED_FIELDS)
