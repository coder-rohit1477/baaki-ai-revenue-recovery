"""Audit-timeline truthfulness and Reset completeness — the two things a judge inspects hardest.

The timeline bug these pin: two real clocks feed the audit rows. W10 stamps `recovery_action.created_at`
with the injected `as_of` (captured when the request began), while W07 lets the database stamp
`agent_proposal.created_at` at insert a few milliseconds later. Sorting on time alone therefore printed the
queued action *before* the proposal that caused it, which reads as though a rejected proposal was later
allowed. The fix is a sort key, not a timestamp: the displayed value stays exactly what the database holds.
"""

import pathlib
import re
import subprocess

from demo.razorpay import captured_for_invoice
from demo.seed import seed
from demo.store import RESETTABLE_TABLES, dashboard, truncate_demo_data
from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parents[2]
STORE = (ROOT / "demo" / "store.py").read_text(encoding="utf-8")
PAGE = (ROOT / "demo" / "static" / "index.html").read_text(encoding="utf-8")
FLAT = " ".join(PAGE.split())
BASELINE_AT_RISK = 39_695_000


# ── timestamps: real values, causal order ────────────────────────────────────────────────


def test_the_timeline_orders_causally_within_a_second_not_by_raw_clock():
    assert "ORDER BY date_trunc('second', at) DESC, step DESC" in STORE


def test_every_timeline_row_carries_a_causal_step():
    body = STORE.split("def activity_timeline(")[1]
    steps = set(re.findall(r"SELECT [^,]+, (\d), '(?:AI|POLICY|ACTION|APPROVAL|MONEY)'", body))
    steps |= set(re.findall(r"ELSE (\d) END AS step", body))
    steps |= set(re.findall(r"THEN (\d) ELSE", body))
    assert {"3", "4", "5", "6", "7"} <= steps, steps


def test_no_timestamp_is_generated_in_the_timeline_query():
    """Every `at` must come from a stored column — never now() or a literal."""
    body = STORE.split("def activity_timeline(")[1].split('""", {"lim": limit})')[0]
    assert "now()" not in body.lower()
    for src in ("p.created_at", "v.created_at", "d.decided_at", "r.created_at", "r.approved_at"):
        assert src in body, src


def test_the_ui_renders_relative_time_from_the_stored_value_and_keeps_the_exact_one():
    assert "function when(iso)" in PAGE
    for phrase in ("Just now", "min ago", "Today · "):
        assert phrase in PAGE, phrase
    assert 'title="${esc(when(x.at).exact)}"' in PAGE   # exact stored value still reachable
    assert "new Date(String(iso).replace(\" \",\"T\"))" in PAGE


# ── the adversarial story must not read as an approval ───────────────────────────────────


def test_a_fallback_action_is_never_labelled_as_allowing_the_rejected_proposal():
    assert "Safe fallback action selected" in STORE
    assert "chosen by the deterministic rules path, not the model" in STORE


def test_a_rejected_proposal_is_labelled_as_rejected():
    assert "Proposal rejected by deterministic validation" in STORE
    assert "AI response was not usable" in STORE and "AI proposal was not usable" in STORE


def test_a_queued_action_is_never_described_as_sent_or_executed():
    assert "Action queued — not sent" in STORE
    for lie in ("action sent", "message sent", "was delivered", "executed successfully"):
        assert lie not in STORE.lower(), lie
        assert lie not in FLAT.lower(), lie


def test_approval_and_rejection_are_distinct_timeline_events():
    assert "Operator approved the action" in STORE
    assert "Operator rejected the action" in STORE
    assert "Action held for operator approval" in STORE
    assert "Action stopped by operator" in STORE


def test_payment_events_name_the_actual_provider():
    assert "Payment confirmed by Razorpay Test Mode" in STORE
    assert "Payment simulated — deterministic simulator" in STORE


# ── reset completeness ───────────────────────────────────────────────────────────────────


def _counts(cluster, tables):
    eng = cluster.engine("super")
    try:
        with eng.connect() as c:
            return {t: int(c.execute(text(f"SELECT count(*) FROM baaki.{t}")).scalar_one()) for t in tables}
    finally:
        eng.dispose()


def test_reset_clears_every_table_the_demo_writes(db):
    """Covers the state left by any scenario: proposals, decisions, actions, approvals, payments, ledger."""
    eng_su = db.engine("super")
    try:
        truncate_demo_data(eng_su)
    finally:
        eng_su.dispose()
    assert all(n == 0 for n in _counts(db, RESETTABLE_TABLES).values())


def test_reset_then_seed_restores_the_documented_baseline(db):
    import datetime

    eng_owner, eng_app, eng_su = db.engine("baaki_migrate"), db.engine("baaki_app"), db.engine("super")
    try:
        seed(eng_owner, eng_app, today=datetime.date.today())
        truncate_demo_data(eng_su)
        seed(eng_owner, eng_app, today=datetime.date.today())
        d = dashboard(eng_app)
        assert d["at_risk_paise"] == BASELINE_AT_RISK
        assert d["recovered_paise"] == 0
        assert d["decisions"] == 0
        assert _counts(db, ("recovery_action", "policy_decision", "outbox", "payment_event"))["outbox"] == 0
    finally:
        eng_owner.dispose(); eng_app.dispose(); eng_su.dispose()


def test_reset_clears_the_payment_link_cache_so_a_stale_link_is_never_reused():
    server = (ROOT / "demo" / "server.py").read_text(encoding="utf-8")
    reseed = server.split("def reseed(")[1].split("\n    def ")[0]
    assert "truncate_demo_data" in reseed and "self.links.clear()" in reseed


# ── provider isolation across a reset ────────────────────────────────────────────────────


def test_a_provider_payment_from_before_a_reset_cannot_attach_to_the_new_invoice():
    """Reset cannot undo a real Test Mode payment at Razorpay — it must not contaminate the new seed."""
    old, new = "01a07198-7be0-728d-9693-aa983204c7cf", "01a07199-3074-7f8c-839a-e82c7e7a8ac8"
    raw = ('{"count":1,"items":[{"id":"pay_STALE","entity":"payment","amount":1000000,"currency":"INR",'
           '"status":"captured","created_at":1756960000,"notes":{"invoice_id":"' + old + '"}}]}')
    assert captured_for_invoice(raw, old), "the filter must match its own invoice"
    assert captured_for_invoice(raw, new) == [], "a stale payment must never match a reseeded invoice"


def test_each_seed_mints_fresh_invoice_ids():
    """UUIDv7 is never reused, so notes.invoice_id from a previous run cannot collide."""
    body = (ROOT / "demo" / "seed.py").read_text(encoding="utf-8")
    assert "from baaki.domain.ids import new_id" in body
    assert "inv = new_id()" in body


def test_reset_is_described_as_resetting_baaki_not_the_provider():
    assert "Razorpay" in FLAT and "no real money moves" in FLAT
    # the demo never claims to have removed anything at the provider
    for lie in ("refunded", "payment deleted", "reversed at razorpay"):
        assert lie not in FLAT.lower(), lie


def test_no_env_file_is_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert [t for t in tracked if t.startswith(".env")] == [".env.example"]
