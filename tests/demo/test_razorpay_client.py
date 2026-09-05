"""Razorpay Test Mode client — offline only. These tests make ZERO network calls.

What matters here is the substring contract: `record_payment_event` refuses a payment payload that is not a
literal substring of the sweep it came from, so a payment can never be invented after the fact. Parsing a
provider item and re-serialising it would not byte-match, so the client slices spans out of the raw text.
The sample below is shaped like a Razorpay payments listing but is entirely synthetic.
"""

import json

import pytest
from demo.razorpay import (
    RazorpayLiveKeyRefused,
    available,
    captured_for_invoice,
    credentials,
    items_with_exact_spans,
)

INV = "01a07064-91e2-7fc5-8193-9d5e021b70dd"
# Deliberately irregular whitespace: a re-serialised object would not match this text.
RAW = (
    '{"entity":"collection","count":3,"items":[ {"id":"pay_A1","entity":"payment","amount":1000000,'
    '"currency":"INR","status":"captured","created_at":1756960000,"notes":{"invoice_id":"' + INV + '"}},\n'
    '  {"id":"pay_B2","entity":"payment","amount":1500000,"currency":"INR","status":"captured",'
    '"created_at":1756960100,"notes":{"invoice_id":"' + INV + '"}} ,'
    '{"id":"pay_C3","entity":"payment","amount":9900,"currency":"INR","status":"authorized",'
    '"created_at":1756960200,"notes":{"invoice_id":"' + INV + '"}}]}'
)


def test_every_extracted_span_is_a_literal_substring_of_the_raw_response():
    """The ledger's contract. If this breaks, real payments are rejected as unattested."""
    for _obj, span in items_with_exact_spans(RAW):
        assert span in RAW


def test_spans_parse_back_to_the_same_object():
    for obj, span in items_with_exact_spans(RAW):
        assert json.loads(span) == obj


def test_all_items_are_found():
    assert [o["id"] for o, _ in items_with_exact_spans(RAW)] == ["pay_A1", "pay_B2", "pay_C3"]


def test_reserialising_would_have_broken_the_contract():
    """Documents why the slicing exists at all."""
    obj, span = items_with_exact_spans(RAW)[0]
    assert json.dumps(obj) != span
    assert json.dumps(obj) not in RAW


def test_only_captured_inr_payments_for_this_invoice_are_offered():
    hits = captured_for_invoice(RAW, INV)
    assert [o["id"] for o, _ in hits] == ["pay_A1", "pay_B2"]   # the authorized one is excluded
    assert all(span in RAW for _o, span in hits)


def test_payments_for_another_invoice_are_ignored():
    assert captured_for_invoice(RAW, "00000000-0000-0000-0000-000000000000") == []


def test_a_response_without_items_yields_nothing():
    assert items_with_exact_spans('{"count":0}') == []
    assert items_with_exact_spans("not json at all") == []


def test_a_live_key_is_refused(monkeypatch):
    """Live mode is an abort, not a configuration flag — mirroring the application config rule."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_x")  # short on purpose: must not look like real key material
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "whatever")
    with pytest.raises(RazorpayLiveKeyRefused):
        credentials()
    assert available() is False


def test_absent_credentials_mean_the_simulator(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    assert credentials() is None and available() is False


def test_the_client_never_puts_a_secret_in_a_returned_error():
    import pathlib

    body = pathlib.Path("demo/razorpay.py").read_text(encoding="utf-8")
    assert "get_secret_value" in body                    # the secret is unwrapped exactly once
    assert body.count("get_secret_value") == 1           # ...only where the auth header is built
    assert "print(" not in body and "import logging" not in body   # nothing to log a secret with
