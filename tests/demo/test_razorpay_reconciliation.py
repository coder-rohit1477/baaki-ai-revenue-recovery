"""Reconciliation of a real Razorpay Test Mode listing, end to end through the committed writers.

Offline: every response here is a synthetic string shaped like `GET /v1/payments`. ZERO network calls, no
credentials, no credits.

The bug these cover: a customer paid a Payment Link, the hosted page showed the money as paid, and
"Check for payment" answered "Razorpay reports no new captured payment for this invoice". A Razorpay
payment is `created` -> `authorized` -> `captured`; the link page shows it paid at authorisation, but only
a CAPTURED payment may reach the ledger. `captured_for_invoice` was right to skip it — the demo was wrong
to report that state identically to "no payment exists".

The financial invariant these pin: only `captured_for_invoice` ever produces something a writer sees, and
every payload it produces is a literal substring of the sweep response. `pending_for_invoice` is display
only and yields no spans at all.
"""

import json

import pytest
from demo.seed import seed
from sqlalchemy import text

from baaki.db.writers._call import WriterUniqueViolation
from demo import razorpay, store

INV = "01a07256-d99e-7b0d-a81e-9adf3ba62c8f"
OTHER = "01a07256-0000-0000-0000-000000000000"


def _listing(*items: str) -> str:
    """A payments listing with deliberately irregular whitespace: a re-serialised item would not match."""
    body = " ,\n  ".join(items)
    return f'{{"entity":"collection","count":{len(items)},"items":[ {body} ]}}'


def _payment(pid: str, amount: int, status: str, invoice_id: str = INV, currency: str = "INR") -> str:
    captured = "true" if status == "captured" else "false"
    return (
        f'{{"id":"{pid}","entity":"payment","amount":{amount},"currency":"{currency}",'
        f'"status":"{status}","captured":{captured},"created_at":1756960000,"order_id":"order_X",'
        f'"notes":{{"invoice_id":"{invoice_id}"}}}}'
    )


AUTHORIZED_ONLY = _listing(_payment("pay_TYPvIFLxChcgkT", 1000000, "authorized"))
CAPTURED = _listing(_payment("pay_TYPvIFLxChcgkT", 1000000, "captured"))
BOTH_PARTS = _listing(
    _payment("pay_TYPvIFLxChcgkT", 1000000, "captured"),
    _payment("pay_SECOND1234567", 1500000, "captured"),
)


# ── the exact provider shape that exposed the bug ────────────────────────────────────────


def test_an_authorized_payment_is_not_applicable_but_is_no_longer_invisible():
    """The regression. Before, this state was indistinguishable from "no payment at all"."""
    assert razorpay.captured_for_invoice(AUTHORIZED_ONLY, INV) == []   # never reaches the ledger
    pending = razorpay.pending_for_invoice(AUTHORIZED_ONLY, INV)
    assert [(p["id"], p["status"], p["amount_paise"]) for p in pending] == [
        ("pay_TYPvIFLxChcgkT", "authorized", 1000000)
    ]


def test_once_captured_the_same_payment_becomes_applicable_and_stops_being_pending():
    hits = razorpay.captured_for_invoice(CAPTURED, INV)
    assert [o["id"] for o, _ in hits] == ["pay_TYPvIFLxChcgkT"]
    assert razorpay.pending_for_invoice(CAPTURED, INV) == []


def test_pending_never_offers_a_span_so_it_can_never_reach_a_writer():
    """`pending_for_invoice` returns plain dicts — there is no attested payload to hand to the ledger."""
    for p in razorpay.pending_for_invoice(AUTHORIZED_ONLY, INV):
        assert "span" not in p and "provider_payload_raw" not in p


def test_pending_ignores_another_invoices_payments():
    raw = _listing(_payment("pay_OTHER", 500000, "authorized", invoice_id=OTHER))
    assert razorpay.pending_for_invoice(raw, INV) == []


def test_a_non_inr_capture_is_reported_as_pending_not_applied():
    raw = _listing(_payment("pay_USD", 1000000, "captured", currency="USD"))
    assert razorpay.captured_for_invoice(raw, INV) == []
    assert [p["currency"] for p in razorpay.pending_for_invoice(raw, INV)] == ["USD"]


def test_every_applicable_span_is_a_literal_substring_of_the_sweep_response():
    """The ledger's attestation contract, restated for the listings used here."""
    for raw in (CAPTURED, BOTH_PARTS):
        for obj, span in razorpay.captured_for_invoice(raw, INV):
            assert span in raw
            assert json.loads(span) == obj
            assert json.dumps(obj) not in raw  # re-serialising would not have byte-matched


def test_the_listing_window_is_the_providers_maximum_page():
    """25 was small enough that a busy test key could push a demo payment out of the window."""
    assert razorpay.MAX_LIST_COUNT == 100
    assert razorpay.fetch_payments.__defaults__ == (100,)


# ── through the committed writers ────────────────────────────────────────────────────────


@pytest.fixture
def invoice(db):
    """A seeded demo world; yields (db, scenario-A invoice_id) — Sharma Traders / INV-1042, Rs 25,000."""
    import datetime

    owner, app = db.engine("baaki_migrate"), db.engine("baaki_app")
    try:
        accounts = seed(owner, app, today=datetime.date.today())
        yield db, accounts["A"].invoice_id
    finally:
        owner.dispose(); app.dispose()


def _outstanding(db, invoice_id):
    eng = db.engine("baaki_app")
    try:
        with eng.connect() as c:
            return c.execute(
                text("SELECT outstanding_paise FROM baaki.v_invoice_outstanding WHERE invoice_id = :i"),
                {"i": invoice_id},
            ).scalar_one()
    finally:
        eng.dispose()


def _counts(db, invoice_id):
    eng = db.engine("baaki_app")
    try:
        with eng.connect() as c:
            return (
                c.execute(text("SELECT count(*) FROM baaki.payment_event")).scalar_one(),
                c.execute(text("SELECT count(*) FROM baaki.sweep_run")).scalar_one(),
                c.execute(
                    text("SELECT coalesce(sum(amount_paise),0) FROM baaki.ledger_entry "
                         "WHERE direction='CREDIT' AND invoice_id = :i"), {"i": invoice_id},
                ).scalar_one(),
            )
    finally:
        eng.dispose()


def _reconcile(db, invoice_id, raw):
    app = db.engine("baaki_app")
    try:
        return store.reconcile_provider_payments(
            app, invoice_id=invoice_id, raw_response=raw,
            items=razorpay.captured_for_invoice(raw, str(invoice_id)),
        )
    finally:
        app.dispose()


def _raw_for(invoice_id, *payments):
    return _listing(*[p.replace(INV, str(invoice_id)) for p in payments])


def test_a_captured_partial_payment_moves_the_ledger_and_only_the_ledger(invoice):
    db, invoice_id = invoice
    assert _outstanding(db, invoice_id) == 2500000

    raw = _raw_for(invoice_id, _payment("pay_PART1", 1000000, "captured"))
    out = _reconcile(db, invoice_id, raw)

    assert out["matched"] == 1
    assert [a["amount_paise"] for a in out["applied"]] == [1000000]
    assert out["outstanding_paise"] == 1500000
    assert out["invoice_state"] == "OPEN"          # partial payment is a reduced balance, not a state
    events, sweeps, credited = _counts(db, invoice_id)
    assert (events, sweeps, credited) == (1, 1, 1000000)


def test_rechecking_the_same_listing_is_idempotent(invoice):
    db, invoice_id = invoice
    raw = _raw_for(invoice_id, _payment("pay_PART1", 1000000, "captured"))
    _reconcile(db, invoice_id, raw)
    again = _reconcile(db, invoice_id, raw)

    assert again["applied"] == []
    assert again["already_reconciled"] == ["pay_PART1"]
    assert again["outstanding_paise"] == 1500000
    events, sweeps, credited = _counts(db, invoice_id)
    assert (events, credited) == (1, 1000000)      # nothing double counted
    assert sweeps == 1                             # uq_sweep_response returned the existing sweep


def test_an_authorized_payment_applies_nothing_then_applies_once_when_captured(invoice):
    """The reported bug, end to end: the second check must not double count the first."""
    db, invoice_id = invoice
    pre = _raw_for(invoice_id, _payment("pay_PART1", 1000000, "authorized"))
    out = _reconcile(db, invoice_id, pre)
    assert out["matched"] == 0 and out["outstanding_paise"] == 2500000
    assert _counts(db, invoice_id) == (0, 1, 0)    # a sweep was attested; no money moved

    post = _raw_for(invoice_id, _payment("pay_PART1", 1000000, "captured"))
    out = _reconcile(db, invoice_id, post)
    assert out["outstanding_paise"] == 1500000
    assert _counts(db, invoice_id) == (1, 2, 1000000)


def test_both_parts_settle_the_invoice_and_stop_recovery(invoice):
    db, invoice_id = invoice
    _reconcile(db, invoice_id, _raw_for(invoice_id, _payment("pay_PART1", 1000000, "captured")))
    out = _reconcile(
        db, invoice_id,
        _raw_for(invoice_id, _payment("pay_PART1", 1000000, "captured"),
                 _payment("pay_PART2", 1500000, "captured")),
    )
    assert out["outstanding_paise"] == 0
    assert out["invoice_state"] == "PAID"
    assert sorted(a["provider_payment_id"] for a in out["applied"]) == ["pay_PART2"]
    assert out["already_reconciled"] == ["pay_PART1"]
    events, _sweeps, credited = _counts(db, invoice_id)
    assert (events, credited) == (2, 2500000)


def test_a_payload_that_is_not_a_substring_of_the_sweep_is_refused(invoice):
    """The attestation invariant, proven against the real writer rather than asserted in prose."""
    db, invoice_id = invoice
    raw = _raw_for(invoice_id, _payment("pay_PART1", 1000000, "captured"))
    forged = json.dumps(json.loads(razorpay.captured_for_invoice(raw, str(invoice_id))[0][1]))
    assert forged not in raw
    app = db.engine("baaki_app")
    try:
        with pytest.raises(Exception) as exc:  # noqa: PT011 — writer refusal, not a unique violation
            store.reconcile_provider_payments(
                app, invoice_id=invoice_id, raw_response=raw,
                items=[(json.loads(forged), forged)],
            )
        assert not isinstance(exc.value, WriterUniqueViolation)
    finally:
        app.dispose()
    assert _counts(db, invoice_id)[0] == 0
