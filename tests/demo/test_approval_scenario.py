"""Scenario D ("Needs human approval") must reach tier 2 whatever the wall clock says.

Two independent faults used to make this scenario collapse to `action = none, tier = 0`:

  1. `provider_for("approval", ...)` scripted ONE transport reply, but `AgentWorkflow` makes TWO provider
     calls. Call 2 hit an exhausted transport, recorded `ACTION_PROPOSAL / PROVIDER_ERROR`, and every run
     fell through to the L1 tree — so the scenario never exercised the model-proposes → kernel-classifies
     path it claims to demonstrate.

  2. `PROPOSE_INSTALLMENT_PLAN` is an OUTBOUND action, so P10 quiet hours (09:00–19:00 org-local, Sunday
     closed) legitimately DEFERs it. Run the demo at 20:45 and the approval row never existed. The scenario
     had been "verified" earlier the same day, inside the window, which is why it looked fine.

Neither is fixed by relaxing policy: P10 still runs, in full, on every decision here. The demo instead
decides as of the most recent instant inside the organisation's own window, and the kernel does the rest.

These assertions are all about committed state — the decision, action, approval and outbox rows — never
about anything the UI renders.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest
from demo.seed import ORG_TIMEZONE, seed
from sqlalchemy import text

from baaki.domain.errors import WriterRefused
from baaki.domain.ids import new_id
from baaki.policy.kernel.quiet_hours import in_window
from demo import scenarios, store

IST = ZoneInfo(ORG_TIMEZONE)


# ── the clock guard: pure, no database ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "local",
    [
        datetime.datetime(2026, 9, 5, 20, 45, tzinfo=IST),  # Saturday, after close — the reported bug
        datetime.datetime(2026, 9, 5, 23, 59, tzinfo=IST),  # Saturday, near midnight
        datetime.datetime(2026, 9, 6, 3, 0, tzinfo=IST),    # Sunday, small hours — closed all day
        datetime.datetime(2026, 9, 6, 12, 0, tzinfo=IST),   # Sunday midday — still closed
        datetime.datetime(2026, 9, 7, 6, 30, tzinfo=IST),   # Monday, before opening
        datetime.datetime(2026, 9, 7, 9, 0, tzinfo=IST),    # Monday, exactly at open
        datetime.datetime(2026, 9, 7, 18, 59, tzinfo=IST),  # Monday, one minute before close
        datetime.datetime(2026, 9, 7, 19, 0, tzinfo=IST),   # Monday, exactly at close (window is half-open)
    ],
)
def test_the_decision_clock_always_lands_inside_the_organisations_window(local):
    """Whatever hour a judge runs the demo, the instant handed to the kernel is one P10 accepts."""
    as_of, snapped = scenarios.decision_clock(local.astimezone(datetime.UTC))
    assert in_window(as_of, ORG_TIMEZONE, scenarios.RULESET.quiet_hours), (local, as_of)
    assert as_of <= local.astimezone(datetime.UTC)  # never invents a moment in the future
    assert snapped is not in_window(local.astimezone(datetime.UTC), ORG_TIMEZONE, scenarios.RULESET.quiet_hours)


def test_an_in_window_run_is_left_alone():
    """Inside business hours nothing is adjusted, and the UI is told so."""
    now = datetime.datetime(2026, 9, 7, 11, 30, tzinfo=IST).astimezone(datetime.UTC)
    as_of, snapped = scenarios.decision_clock(now)
    assert (as_of, snapped) == (now, False)


def test_the_demos_window_predicate_agrees_with_the_kernels_everywhere():
    """The demo may not import `baaki.policy.kernel` (arch guard), so equivalence is pinned here instead.

    A full week at 15-minute resolution, which covers both edges of the window and the closed day.
    """
    start = datetime.datetime(2026, 9, 5, 0, 0, tzinfo=IST)
    for step in range(7 * 24 * 4):
        at = (start + datetime.timedelta(minutes=15 * step)).astimezone(datetime.UTC)
        assert scenarios._organisation_is_open(at, ORG_TIMEZONE) == in_window(
            at, ORG_TIMEZONE, scenarios.RULESET.quiet_hours
        ), at


def test_the_approval_scenario_scripts_both_provider_calls():
    """One reply meant call 2 got UNAVAILABLE. The workflow makes two calls, so two replies are scripted."""
    provider, live = scenarios.provider_for("approval", None, contact_id=new_id())
    assert live is False                                   # scripted output is never labelled live
    assert len(provider._transport.outcomes) == 2          # type: ignore[attr-defined]


# ── end to end, against the real writers ─────────────────────────────────────────────────


@pytest.fixture
def demo_db(db):
    """A seeded demo world on the throwaway test cluster."""
    owner, app = db.engine("baaki_migrate"), db.engine("baaki_app")
    try:
        accounts = seed(owner, app, today=datetime.date.today())
        yield db, accounts
    finally:
        owner.dispose(); app.dispose()


def _run_d(db, accounts):
    app, agent = db.engine("baaki_app"), db.engine("baaki_agent")
    try:
        d = accounts["D"]
        return scenarios.run(
            engine_app=app, engine_agent=agent, account_id=d.account_id,
            contact_id=d.contact_id, scenario="D", credential=None,
        )
    finally:
        app.dispose(); agent.dispose()


def _scalar(db, sql):
    eng = db.engine("baaki_app")
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).scalar_one()
    finally:
        eng.dispose()


def test_scenario_d_reaches_tier_two_and_parks_for_a_human(demo_db):
    """The regression this file exists for: D must never silently become tier 0 / action none."""
    db, accounts = demo_db
    report = _run_d(db, accounts)

    # what the model produced — both calls, not one
    assert report.parse_status == "OK"
    assert report.interpretation["intent"] == "REQUEST_INSTALLMENTS"
    assert report.validation_outcome == "PASS"
    assert _scalar(db, "SELECT count(*) FROM baaki.agent_proposal WHERE parse_status <> 'OK'") == 0

    # what the kernel decided — the whole point
    assert report.action_type == "PROPOSE_INSTALLMENT_PLAN"
    assert report.verdict == "REQUIRE_APPROVAL"
    assert report.tier == 2
    assert report.degradation_level == "L0"  # the model's proposal drove it, not the fallback tree

    # what was committed
    assert _scalar(db, "SELECT state::text FROM baaki.recovery_action") == "PENDING_APPROVAL"
    assert _scalar(db, "SELECT verdict::text FROM baaki.policy_decision") == "REQUIRE_APPROVAL"
    assert _scalar(db, "SELECT tier FROM baaki.policy_decision") == 2
    assert _scalar(db, "SELECT count(*) FROM baaki.outbox") == 0  # nothing queued before a human decides

    app = db.engine("baaki_app")
    try:
        pending = store.pending_approvals(app)
    finally:
        app.dispose()
    assert len(pending) == 1
    assert pending[0]["action_type"] == "PROPOSE_INSTALLMENT_PLAN"
    assert pending[0]["tier"] == 2


def test_scenario_d_is_not_at_the_mercy_of_the_wall_clock(demo_db):
    """P10 still runs — the run just happens at an instant the organisation is open."""
    db, accounts = demo_db
    report = _run_d(db, accounts)
    as_of = datetime.datetime.fromisoformat(report.as_of)
    assert in_window(as_of, ORG_TIMEZONE, scenarios.RULESET.quiet_hours)
    # P10 was evaluated, not skipped: the ladder ran past it to the executable end of the ladder.
    matched = _scalar(db, "SELECT array_to_string(matched_rules, ',') FROM baaki.policy_decision")
    assert "P10" in matched and matched.endswith("P14")


def test_approving_queues_exactly_one_unit_of_work_and_records_the_operator(demo_db):
    db, accounts = demo_db
    _run_d(db, accounts)
    app, ops = db.engine("baaki_app"), db.engine("baaki_ops")
    try:
        action_id = store.pending_approvals(app)[0]["action_id"]
        out = store.decide_approval(ops, action_id=action_id, approve=True, note="plan acceptable")
        assert out["state"] == "QUEUED"
        assert _scalar(db, "SELECT count(*) FROM baaki.outbox") == 1
        assert _scalar(db, "SELECT approved_by_role FROM baaki.recovery_action") == "baaki_ops"
        assert _scalar(db, "SELECT approved_by_note FROM baaki.recovery_action") == "plan acceptable"
        assert _scalar(db, "SELECT count(*) FROM baaki.recovery_action WHERE approved_at IS NOT NULL") == 1
        assert store.pending_approvals(app) == []

        # a second approval cannot queue the action twice
        with pytest.raises(WriterRefused):
            store.decide_approval(ops, action_id=action_id, approve=True, note="again")
        assert _scalar(db, "SELECT count(*) FROM baaki.outbox") == 1
        assert _scalar(db, "SELECT state::text FROM baaki.recovery_action") == "QUEUED"
    finally:
        app.dispose(); ops.dispose()


def test_rejecting_stops_it_dead_and_never_queues_anything(demo_db):
    db, accounts = demo_db
    _run_d(db, accounts)
    app, ops = db.engine("baaki_app"), db.engine("baaki_ops")
    try:
        action_id = store.pending_approvals(app)[0]["action_id"]

        with pytest.raises(WriterRefused):  # a rejection must carry a reason
            store.decide_approval(ops, action_id=action_id, approve=False, note="")
        assert _scalar(db, "SELECT state::text FROM baaki.recovery_action") == "PENDING_APPROVAL"

        out = store.decide_approval(ops, action_id=action_id, approve=False, note="already on a plan")
        assert out["state"] == "APPROVAL_REJECTED"
        assert _scalar(db, "SELECT count(*) FROM baaki.outbox") == 0
        assert _scalar(db, "SELECT approved_by_role FROM baaki.recovery_action") == "baaki_ops"

        with pytest.raises(WriterRefused):  # and it cannot be approved afterwards
            store.decide_approval(ops, action_id=action_id, approve=True, note="override")
        assert _scalar(db, "SELECT state::text FROM baaki.recovery_action") == "APPROVAL_REJECTED"
        assert _scalar(db, "SELECT count(*) FROM baaki.outbox") == 0
    finally:
        app.dispose(); ops.dispose()
