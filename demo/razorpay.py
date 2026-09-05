"""Razorpay **Test Mode** client for the demo. Stdlib only; no new dependency, no vendor SDK.

Scope is deliberately two calls: create a Payment Link, and list payments. Everything after that is the
committed recovery engine — the same sweep, payment-event and ledger writers the deterministic simulator
uses. Baaki does not become Razorpay-specific: this module lives in `demo/`, and `src/` never imports it.

Credentials come only from the environment (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`), are held in a
`SecretStr`, and are never logged, echoed or returned. A key id that is not `rzp_test_...` is refused
outright — mirroring the rule the application config already enforces: live mode is an abort, not a flag.

The exact-substring rule matters here. `record_payment_event` requires the payment payload it is given to
be a literal substring of the sweep's raw response, so a payment can never be invented after the fact.
`items_with_exact_spans` therefore slices each item straight out of the response text rather than
re-serialising a parsed object, which would not byte-match.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final

from pydantic import SecretStr

API_ROOT: Final[str] = "https://api.razorpay.com/v1"
TEST_KEY_PREFIX: Final[str] = "rzp_test_"
USER_AGENT: Final[str] = "baaki-demo/razorpay-test-mode"
TIMEOUT_S: Final[float] = 12.0
# Razorpay caps a listing page at 100. One page keeps one raw response per sweep (see fetch_payments).
MAX_LIST_COUNT: Final[int] = 100


class RazorpayUnavailable(RuntimeError):
    """No usable test-mode credentials, or the API could not be reached. The demo falls back."""


class RazorpayLiveKeyRefused(RuntimeError):
    """A non-test key was supplied. The demo never transacts with live credentials."""


@dataclass(frozen=True)
class PaymentLink:
    id: str
    short_url: str
    status: str
    amount_paise: int
    amount_paid_paise: int
    reference_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "short_url": self.short_url, "status": self.status,
            "amount_paise": self.amount_paise, "amount_paid_paise": self.amount_paid_paise,
            "reference_id": self.reference_id,
        }


def credentials() -> tuple[str, SecretStr] | None:
    """(key_id, secret) from the environment, or None when the demo should use the simulator."""
    key_id, secret = os.environ.get("RAZORPAY_KEY_ID", ""), os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id or not secret:
        return None
    if not key_id.startswith(TEST_KEY_PREFIX):
        raise RazorpayLiveKeyRefused(f"RAZORPAY_KEY_ID must start with {TEST_KEY_PREFIX!r}; refusing to continue")
    return key_id, SecretStr(secret)


def available() -> bool:
    try:
        return credentials() is not None
    except RazorpayLiveKeyRefused:
        return False


def _auth_header() -> dict[str, str]:
    creds = credentials()
    if creds is None:
        raise RazorpayUnavailable("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set")
    key_id, secret = creds
    token = base64.b64encode(f"{key_id}:{secret.get_secret_value()}".encode()).decode()
    return {"Authorization": f"Basic {token}", "User-Agent": USER_AGENT}


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
    """(status_code, raw_body_text). Never raises on an HTTP status; never logs the request."""
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {**_auth_header()}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API_ROOT}{path}", data=body, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        raise RazorpayUnavailable(f"could not reach Razorpay: {type(e).__name__}") from e


def _api_error(status: int, raw: str) -> str:
    """A short, safe description. Never echoes the request or any credential."""
    try:
        err = json.loads(raw).get("error", {})
        return f"HTTP {status}: {err.get('code') or ''} {err.get('description') or ''}".strip()
    except (json.JSONDecodeError, AttributeError):
        return f"HTTP {status}"


def create_payment_link(
    *, amount_paise: int, first_min_partial_paise: int, reference_id: str, invoice_id: str, description: str
) -> PaymentLink:
    """POST /payment_links — partial payments on, bound to the invoice by `notes.invoice_id`.

    `notes.invoice_id` is what the committed payment writer attributes on (NOTES_INVOICE_ID), so the demo
    adds no new attribution concept. `reference_id` must be unique per link, so the caller makes it
    run-safe; a repeat would otherwise be rejected with HTTP 400.
    """
    status, raw = _request("POST", "/payment_links", {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": True,
        "first_min_partial_amount": first_min_partial_paise,
        "reference_id": reference_id,
        "description": description,
        "notes": {"invoice_id": invoice_id},
        "notify": {"sms": False, "email": False},   # the demo never messages a real person
        "reminder_enable": False,
    })
    if status not in (200, 201):
        raise RazorpayUnavailable(f"payment link not created — {_api_error(status, raw)}")
    d = json.loads(raw)
    return PaymentLink(
        id=str(d["id"]), short_url=str(d["short_url"]), status=str(d.get("status", "created")),
        amount_paise=int(d.get("amount", amount_paise)), amount_paid_paise=int(d.get("amount_paid", 0)),
        reference_id=str(d.get("reference_id", reference_id)),
    )


def fetch_payment_link(link_id: str) -> PaymentLink:
    status, raw = _request("GET", f"/payment_links/{link_id}")
    if status != 200:
        raise RazorpayUnavailable(f"payment link not readable — {_api_error(status, raw)}")
    d = json.loads(raw)
    return PaymentLink(
        id=str(d["id"]), short_url=str(d["short_url"]), status=str(d.get("status", "")),
        amount_paise=int(d.get("amount", 0)), amount_paid_paise=int(d.get("amount_paid", 0)),
        reference_id=str(d.get("reference_id", "")),
    )


def fetch_payments(count: int = MAX_LIST_COUNT) -> str:
    """GET /payments — returns the RAW response text, unparsed.

    The raw text is what gets recorded as the reconciliation sweep, and every payment payload handed to the
    ledger must be a literal substring of it. Parsing and re-serialising here would break that guarantee,
    and so would paginating: a span must belong to the one response the sweep attests. So this stays a
    single call, at the largest page the API allows.

    `count` is the newest N payments across the whole test account, not per invoice. 25 was small enough
    that a busy test key could push a demo payment out of the window; 100 is the documented maximum.
    """
    status, raw = _request("GET", f"/payments?count={count}")
    if status != 200:
        raise RazorpayUnavailable(f"payments not readable — {_api_error(status, raw)}")
    return raw


def items_with_exact_spans(raw: str) -> list[tuple[dict[str, Any], str]]:
    """[(parsed_item, exact_substring)] for each element of `items`, sliced straight out of `raw`.

    Uses the decoder's own end offsets so the substring is byte-identical to what the provider sent. This is
    the difference between the ledger accepting the payload and rejecting it as unattested.
    """
    marker = '"items"'
    i = raw.find(marker)
    if i < 0:
        return []
    i = raw.find("[", i)
    if i < 0:
        return []
    decoder, out, pos = json.JSONDecoder(), [], i + 1
    while pos < len(raw):
        while pos < len(raw) and raw[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(raw) or raw[pos] == "]":
            break
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            out.append((obj, raw[pos:end]))
        pos = end
    return out


def captured_for_invoice(raw: str, invoice_id: str) -> list[tuple[dict[str, Any], str]]:
    """Captured INR payments this invoice owns, by `notes.invoice_id`. The ledger re-checks all of it."""
    hits = []
    for obj, span in items_with_exact_spans(raw):
        notes = obj.get("notes") or {}
        if not isinstance(notes, dict) or str(notes.get("invoice_id", "")) != str(invoice_id):
            continue
        if obj.get("status") != "captured" or obj.get("currency") != "INR":
            continue
        hits.append((obj, span))
    return hits


def pending_for_invoice(raw: str, invoice_id: str) -> list[dict[str, Any]]:
    """This invoice's payments that the provider has NOT made applicable yet. Display only.

    A Razorpay payment is `created` → `authorized` → `captured`. The hosted link page shows the money as
    paid the moment it is authorised, but the ledger may only ever see a *captured* payment — so between
    those two states `captured_for_invoice` correctly returns nothing while the customer is looking at a
    receipt. Reporting that as "no payment" is what made reconciliation look broken; reporting it as
    "authorised, not captured yet" is the truth.

    Nothing here reaches a writer. `captured_for_invoice` remains the only source of payments that are
    applied to the ledger, and this function deliberately returns no spans — there is nothing to attest.
    """
    out: list[dict[str, Any]] = []
    for obj, _span in items_with_exact_spans(raw):
        notes = obj.get("notes") or {}
        if not isinstance(notes, dict) or str(notes.get("invoice_id", "")) != str(invoice_id):
            continue
        if obj.get("status") == "captured" and obj.get("currency") == "INR":
            continue  # this one is applicable; captured_for_invoice owns it
        out.append({
            "id": str(obj.get("id", "")),
            "status": str(obj.get("status", "")),
            "amount_paise": int(obj.get("amount", 0) or 0),
            "currency": str(obj.get("currency", "")),
        })
    return out


__all__ = [
    "PaymentLink", "RazorpayLiveKeyRefused", "RazorpayUnavailable", "available", "captured_for_invoice",
    "create_payment_link", "credentials", "fetch_payment_link", "fetch_payments", "items_with_exact_spans",
    "pending_for_invoice",
]
