"""Regressions for the three demo blockers found in judge-demo verification (2026-09-05).

These cover demo scaffolding only. They exercise the throwaway test cluster, not the demo database, so they
stay fast and need no provisioning.
"""

import datetime
import pathlib
import re

from demo.seed import BACKGROUND, seed
from demo.store import RESETTABLE_TABLES, dashboard, provider_payment_id, truncate_demo_data
from sqlalchemy import text

EXPECTED_ACCOUNTS = 9  # four scenario accounts + five dashboard background accounts
EXPECTED_AT_RISK_PAISE = 39_695_000  # Rs 396,950 — the documented baseline the judge first sees


# ── FIX #2: simulated provider payment ids ───────────────────────────────────────────────


def test_two_rapid_payment_ids_are_distinct():
    """The old id was the first 40 bits of a UUIDv7 — a millisecond stamp — so a double-click collided."""
    a, b = provider_payment_id(), provider_payment_id()
    assert a != b


def test_a_burst_of_payment_ids_is_entirely_unique():
    ids = [provider_payment_id() for _ in range(2000)]
    assert len(set(ids)) == len(ids)


def test_the_payment_id_keeps_its_demo_prefix_and_is_provider_shaped():
    """Prefix retained so a judge (and the audit trail) can see the payment is synthetic."""
    assert re.fullmatch(r"demo_pay_[0-9a-f]{32}", provider_payment_id())


def test_the_payment_id_carries_no_timestamp_prefix():
    """Two ids generated back to back must not share a leading run — that was the collision mechanism."""
    a, b = provider_payment_id()[9:], provider_payment_id()[9:]
    assert a[:10] != b[:10]


# ── FIX #1: reset restores the baseline instead of appending ─────────────────────────────


def _counts(cluster):
    """Short-lived connection on purpose: a held one would block TRUNCATE on its table locks."""
    eng = cluster.engine("super")
    try:
        with eng.connect() as c:
            return {t: int(c.execute(text(f"SELECT count(*) FROM baaki.{t}")).scalar_one()) for t in
                    ("organization", "account", "invoice", "ledger_entry")}
    finally:
        eng.dispose()


def _scalar(cluster, sql):
    eng = cluster.engine("super")
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def test_seed_produces_the_documented_baseline(db):
    eng_owner, eng_app = db.engine("baaki_migrate"), db.engine("baaki_app")
    try:
        made = seed(eng_owner, eng_app, today=datetime.date.today())
        assert set(made) == {"A", "B", "C", "D"}   # D is the human-approval scenario
        c = _counts(db)
        assert c["organization"] == 1
        assert c["account"] == EXPECTED_ACCOUNTS == 4 + len(BACKGROUND)
        assert dashboard(eng_app)["at_risk_paise"] == EXPECTED_AT_RISK_PAISE
    finally:
        eng_owner.dispose(); eng_app.dispose()


def test_reset_restores_the_baseline_and_never_duplicates(db):
    """The blocker: seeding twice doubled revenue at risk and produced two 'Sharma Traders' rows."""
    eng_owner, eng_app, eng_su = db.engine("baaki_migrate"), db.engine("baaki_app"), db.engine("super")
    try:
        seed(eng_owner, eng_app, today=datetime.date.today())
        baseline = _counts(db)
        at_risk = dashboard(eng_app)["at_risk_paise"]

        # what "Reset demo" now does: clear, then seed once
        truncate_demo_data(eng_su)
        assert _counts(db)["account"] == 0  # actually cleared, not appended to
        seed(eng_owner, eng_app, today=datetime.date.today())

        assert _counts(db) == baseline
        assert dashboard(eng_app)["at_risk_paise"] == at_risk == EXPECTED_AT_RISK_PAISE
        dupes = _scalar(db, "SELECT name, count(*) FROM baaki.account GROUP BY name HAVING count(*) > 1")
        assert dupes == [], dupes
    finally:
        eng_owner.dispose(); eng_app.dispose(); eng_su.dispose()


def test_reset_clears_decisions_actions_and_outbox(db):
    """A reset must also drop the recovery/outbox state left by a previous scenario run."""
    eng_su = db.engine("super")
    try:
        truncate_demo_data(eng_su)
    finally:
        eng_su.dispose()
    for t in ("policy_decision", "recovery_action", "outbox", "agent_proposal",
              "validation_result", "payment_event", "ledger_entry"):
        assert _scalar(db, f"SELECT count(*) FROM baaki.{t}")[0][0] == 0, t


def test_resettable_tables_cover_everything_the_demo_writes():
    """If the demo ever writes a new table, reset must clear it too or the baseline drifts."""
    written = {"organization", "account", "contact", "invoice", "sweep_run", "payment_event",
               "ledger_entry", "agent_proposal", "validation_result", "policy_decision",
               "recovery_action", "outbox"}
    assert written <= set(RESETTABLE_TABLES), written - set(RESETTABLE_TABLES)


# ── FIX #3: the Scenario B claim matches what the system actually does ───────────────────


def test_scenario_b_distinguishes_action_creation_from_delivery():
    page = pathlib.Path("demo/static/index.html").read_text(encoding="utf-8")
    assert "External delivery: NOT SENT" in page
    assert "Outbound action: NOT SENT" not in page  # the misleading claim is gone
    assert "Financial state: UNCHANGED" in page
    assert "deterministic policy tree" in page  # says who chose the action
    assert "queued only" in page


# ── demo-check isolation: the pre-flight must never disturb a running demo ────────────────


def test_the_preflight_targets_a_different_database_than_the_demo_server():
    """`provision(recreate=True)` drops its target WITH (FORCE), closing every connection to it.

    Regression for a P0 found in judge rehearsal: running `make demo-check` while `make demo` was up
    killed the live server's pooled connections and it could not recover.
    """
    from demo.provision import CHECK_DB_NAME, DB_NAME

    assert CHECK_DB_NAME != DB_NAME


def test_the_preflight_asks_for_the_isolated_database_explicitly():
    import pathlib

    body = pathlib.Path("demo/check.py").read_text(encoding="utf-8")
    assert "State(db=CHECK_DB_NAME)" in body


def test_dsns_follow_the_requested_database():
    from demo.provision import CHECK_DB_NAME, DB_NAME, dsns

    assert all(f"/{DB_NAME}" in v for v in dsns().values())
    assert all(f"/{CHECK_DB_NAME}" in v for v in dsns(CHECK_DB_NAME).values())
