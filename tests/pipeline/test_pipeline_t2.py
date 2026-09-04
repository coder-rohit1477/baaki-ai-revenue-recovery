"""T2 end-to-end (§5.8): validate → decide → create action in one transaction, per arm; SC7; idempotency; stale snapshot;
rollback; concurrency; opt-out via evidence; paid-claim suppression; kill switch; quiet hours. No executor, no provider."""
import threading
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import text

from baaki.contracts.policy_decision import ExecutableDecision, NonExecutableDecision
from baaki.db.writers._call import WriterUniqueViolation
from baaki.db.writers.operator import opt_out_by_operator
from baaki.domain.enums import ActionType, Arm, DegradationLevel, ProposalKind, Verdict
from baaki.domain.errors import WriterRefused
from baaki.pipeline import run as pipeline_mod
from baaki.pipeline.run import AlreadyDecided, Decided, Ineligible, PipelineRetryExhausted, run_decision_pipeline
from tests.helpers import apply_payment, count, outstanding, record_payment, seed_org_account_contact, webhook_payment
from tests.phase2_helpers import (
    IST,
    RULESET,
    action_parsed,
    interp_parsed,
    issue_due,
    proposal,
    store_proposal,
    workday_as_of,
)

AS_OF = workday_as_of()
BDATE = AS_OF.astimezone(IST).date()
SRC = "I will pay by Friday"


@pytest.fixture
def eng(db):
    e = db.engine("baaki_app")
    yield e
    e.dispose()


def setup(owner, app, *, days_overdue=15, amount=450_000):
    ids = seed_org_account_contact(owner)
    inv = issue_due(app, ids, amount=amount, due=BDATE - timedelta(days=days_overdue))
    return ids, inv


def run(eng, arm, ids, **kw):
    return run_decision_pipeline(eng, arm=arm, account_id=ids["account"], as_of=AS_OF, ruleset=RULESET, **kw)


def rows(su):
    return {t: count(su, t) for t in ("agent_proposal", "validation_result", "policy_decision", "recovery_action", "outbox")}


def action_state(su, action_id):
    return su.execute(text("SELECT state::text FROM baaki.recovery_action WHERE action_id=:a"), {"a": action_id}).scalar_one()


# ── arms ────────────────────────────────────────────────────────────────────────────────
def test_control_day15_reminder_creates_queued_action_and_outbox(owner, app, su, eng):
    ids, inv = setup(owner, app)
    r = run(eng, Arm.CONTROL, ids)
    assert isinstance(r, Decided) and isinstance(r.decision, ExecutableDecision)
    assert r.decision.action_type is ActionType.SEND_REMINDER and r.decision.tier == 1 and r.degradation_level is DegradationLevel.L2
    assert r.decision.proposal_id is None and r.action_id is not None and not r.superseded
    assert action_state(su, r.action_id) == "QUEUED" and rows(su) == {"agent_proposal": 0, "validation_result": 0, "policy_decision": 1, "recovery_action": 1, "outbox": 1}
    d = su.execute(text("SELECT arm::text, degradation_level::text, policy_hash, snapshot_hash, array_length(matched_rules,1), invoice_id FROM baaki.policy_decision")).one()
    assert d == ("CONTROL", "L2", RULESET.policy_hash, r.decision.snapshot_hash, 15, inv)


def test_control_off_cadence_suppresses_without_outbox(owner, app, su, eng):
    ids, _ = setup(owner, app, days_overdue=9)
    r = run(eng, Arm.CONTROL, ids)
    assert r.decision.action_type is ActionType.SUPPRESS and rows(su)["recovery_action"] == 1 and action_state(su, r.action_id) == "QUEUED"
    assert su.execute(text("SELECT count(*) FROM baaki.recovery_action WHERE action_type <> 'SUPPRESS'")).scalar_one() == 0


def test_rules_only_day15_link_amount_is_ledger_outstanding(owner, app, su, eng):
    ids, inv = setup(owner, app, amount=750_000)
    ev, ent = webhook_payment(app, inv, 250_000)
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv); apply_payment(app, pid); app.commit()
    assert outstanding(su, inv) == 500_000
    r = run(eng, Arm.RULES_ONLY, ids)
    p = r.decision.canonical_payload
    assert r.decision.action_type is ActionType.SEND_PAYMENT_LINK and int(p.amount_paise) == 500_000
    assert p.expires_at == AS_OF + timedelta(hours=RULESET.link_active_window_hours) and p.notes.action_id == r.action_id
    assert action_state(su, r.action_id) == "QUEUED" and r.degradation_level is DegradationLevel.L1


def test_rules_only_rejects_proposals(owner, app, eng):
    ids, _ = setup(owner, app)
    with pytest.raises(ValueError):
        run(eng, Arm.RULES_ONLY, ids, proposals=[(proposal(interp_parsed(), account_id=ids["account"], business_date=BDATE), SRC)])


def _action_prop(ids, inv, agent, source_text=SRC, **kw):
    parsed = action_parsed(contact_id=str(ids["contact"]), **kw)
    p = proposal(parsed, kind=ProposalKind.ACTION_PROPOSAL, account_id=ids["account"], business_date=BDATE, invoice_id=inv, source_text=source_text)
    store_proposal(agent, p)
    return p


def test_treatment_band_a_reminder_is_l0_linked(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, confidence=0.9)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert r.degradation_level is DegradationLevel.L0 and r.decision.proposal_id == p.proposal_id and r.decision.validation_id == r.validation_ids[0]
    assert r.decision.action_type is ActionType.SEND_REMINDER and r.decision.verdict is Verdict.ALLOW and r.decision.effective_confidence == 0.9
    v = su.execute(text("SELECT outcome::text, normalized->>'action' FROM baaki.validation_result")).one()
    assert v == ("PASS", "SEND_REMINDER") and rows(su)["outbox"] == 1


def test_treatment_band_b_link_requires_approval(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, action="SEND_PAYMENT_LINK", template_id="tpl.link.email.v1", confidence=0.75)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert r.decision.verdict is Verdict.REQUIRE_APPROVAL and r.decision.tier == 2 and r.decision.action_type is ActionType.SEND_PAYMENT_LINK
    assert action_state(su, r.action_id) == "PENDING_APPROVAL" and rows(su)["outbox"] == 0  # nothing dispatchable until a human approves


def test_treatment_band_c_downgrades_to_suppress(owner, app, agent, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, action="SEND_PAYMENT_LINK", template_id="tpl.link.email.v1", confidence=0.6)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert r.decision.action_type is ActionType.SUPPRESS and r.decision.tier == 0 and r.degradation_level is DegradationLevel.L0


def test_treatment_band_d_discards_and_falls_back_to_rules_l1(owner, app, agent, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, confidence=0.3)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert r.degradation_level is DegradationLevel.L1 and r.decision.proposal_id == p.proposal_id
    assert r.decision.action_type is ActionType.SEND_PAYMENT_LINK and r.decision.effective_confidence is None  # rules tree at day 15


def test_treatment_rejected_proposal_stays_linked_at_l1(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, followup_days=30)  # schema violation
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert su.execute(text("SELECT outcome::text FROM baaki.validation_result")).scalar_one() == "REJECT"
    assert r.degradation_level is DegradationLevel.L1 and r.decision.proposal_id == p.proposal_id


def test_treatment_hash_binding_rejects_mismatched_source(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, "a different message")])
    assert su.execute(text("SELECT rejection_reasons::text[] FROM baaki.validation_result")).scalar_one() == ["SCHEMA_VIOLATION"]
    assert r.degradation_level is DegradationLevel.L1


# ── SC7 ─────────────────────────────────────────────────────────────────────────────────
def test_sc7_no_candidates_records_validations_but_no_decision(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    ev, ent = webhook_payment(app, inv, 450_000)
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv); apply_payment(app, pid); app.commit()
    p = proposal(interp_parsed(), account_id=ids["account"], business_date=BDATE); store_proposal(agent, p)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert isinstance(r, Ineligible) and r.reason == "no_candidates" and len(r.validation_ids) == 1
    assert rows(su) == {"agent_proposal": 1, "validation_result": 1, "policy_decision": 0, "recovery_action": 0, "outbox": 0}
    assert isinstance(run(eng, Arm.CONTROL, ids), Ineligible)


# ── idempotency / uniqueness ────────────────────────────────────────────────────────────
def test_second_unlinked_run_same_day_returns_existing_rows(owner, app, su, eng):
    ids, _ = setup(owner, app)
    first = run(eng, Arm.RULES_ONLY, ids)
    second = run(eng, Arm.RULES_ONLY, ids)
    assert isinstance(second, AlreadyDecided)
    assert second.decision_id == first.decision_id and second.action_id == first.action_id and second.validation_ids == ()
    assert rows(su)["policy_decision"] == 1 and rows(su)["recovery_action"] == 1 and rows(su)["outbox"] == 1


def test_linked_replay_of_same_proposal_returns_existing_rows_and_keeps_validation(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, confidence=0.9)
    first = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    second = run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert isinstance(second, AlreadyDecided) and second.decision_id == first.decision_id and second.action_id == first.action_id
    assert second.validation_ids == first.validation_ids  # the immutable first validation is reused, not re-recorded
    assert rows(su) == {"agent_proposal": 1, "validation_result": 1, "policy_decision": 1, "recovery_action": 1, "outbox": 1}


def test_unrelated_unique_violation_is_not_treated_as_already_decided(owner, app, su, eng, monkeypatch):
    ids, _ = setup(owner, app)
    def boom(*a, **k):
        raise WriterUniqueViolation("unique_violation", 'duplicate key value violates unique constraint "uq_something_else"')
    monkeypatch.setattr(pipeline_mod, "record_policy_decision", boom)
    with pytest.raises(WriterUniqueViolation):
        run(eng, Arm.RULES_ONLY, ids)
    assert rows(su)["policy_decision"] == 0 and rows(su)["recovery_action"] == 0


def test_duplicate_treatment_payload_same_day_is_superseded_not_dispatched_twice(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p1 = _action_prop(ids, inv, agent, confidence=0.9)
    p2 = _action_prop(ids, inv, agent, confidence=0.95, source_text="second message, same conclusion")
    r1 = run(eng, Arm.TREATMENT, ids, proposals=[(p1, SRC)])
    r2 = run(eng, Arm.TREATMENT, ids, proposals=[(p2, "second message, same conclusion")])
    assert not r1.superseded and r2.superseded
    states = sorted(s for (s,) in su.execute(text("SELECT state::text FROM baaki.recovery_action")))
    assert states == ["QUEUED", "SUPERSEDED_DUPLICATE"] and rows(su)["outbox"] == 1 and rows(su)["policy_decision"] == 2


# ── stale snapshot / rollback ───────────────────────────────────────────────────────────
def test_stale_snapshot_reassembles_once_and_uses_fresh_ledger_amount(owner, app, su, eng, monkeypatch):
    ids, inv = setup(owner, app, amount=750_000)
    real = pipeline_mod.assemble_account_facts
    stale = real(eng, ids["account"], AS_OF, RULESET)
    ev, ent = webhook_payment(app, inv, 250_000)
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv); apply_payment(app, pid); app.commit()
    calls = []
    def fake(engine, account_id, as_of, ruleset):
        calls.append(1)
        return stale if len(calls) == 1 else real(engine, account_id, as_of, ruleset)
    monkeypatch.setattr(pipeline_mod, "assemble_account_facts", fake)
    r = run(eng, Arm.RULES_ONLY, ids)
    assert len(calls) == 2 and int(r.decision.canonical_payload.amount_paise) == 500_000
    assert rows(su)["policy_decision"] == 1 and rows(su)["recovery_action"] == 1  # the refused first attempt left nothing


def test_stale_twice_fails_closed_with_no_rows(owner, app, su, eng, monkeypatch):
    ids, inv = setup(owner, app, amount=750_000)
    stale = pipeline_mod.assemble_account_facts(eng, ids["account"], AS_OF, RULESET)
    ev, ent = webhook_payment(app, inv, 250_000)
    pid = record_payment(app, webhook_event_id=ev, item=ent, invoice_id=inv); apply_payment(app, pid); app.commit()
    monkeypatch.setattr(pipeline_mod, "assemble_account_facts", lambda *a, **k: stale)
    with pytest.raises(PipelineRetryExhausted):
        run(eng, Arm.RULES_ONLY, ids)
    assert rows(su)["policy_decision"] == 0 and rows(su)["recovery_action"] == 0


def test_action_writer_failure_rolls_back_decision_and_validation(owner, app, agent, su, eng, monkeypatch):
    ids, inv = setup(owner, app)
    p = _action_prop(ids, inv, agent, confidence=0.9)
    def boom(*a, **k):
        raise WriterRefused("injected_failure")
    monkeypatch.setattr(pipeline_mod, "create_recovery_action", boom)
    with pytest.raises(WriterRefused):
        run(eng, Arm.TREATMENT, ids, proposals=[(p, SRC)])
    assert rows(su) == {"agent_proposal": 1, "validation_result": 0, "policy_decision": 0, "recovery_action": 0, "outbox": 0}


def test_concurrent_runs_never_produce_two_dispatchable_actions(owner, app, su, eng):
    ids, _ = setup(owner, app)
    results, errors = [], []
    def worker():
        try:
            results.append(run(eng, Arm.RULES_ONLY, ids))
        except Exception as e:  # any error is a failure of the contract; collected for the assertion below
            errors.append(e)
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errors == [] and len(results) == 2
    decided = [r for r in results if isinstance(r, Decided)]
    already = [r for r in results if isinstance(r, AlreadyDecided)]
    assert len(decided) == 1 and len(already) == 1
    assert already[0].decision_id == decided[0].decision_id and already[0].action_id == decided[0].action_id
    queued = su.execute(text("SELECT count(*) FROM baaki.recovery_action WHERE state='QUEUED'")).scalar_one()
    assert queued == 1 and rows(su)["outbox"] == 1 and rows(su)["policy_decision"] == 1


# ── blocking facts ──────────────────────────────────────────────────────────────────────
def test_kill_switch_blocks_p0_and_creates_no_action(owner, app, su, eng):
    ids, _ = setup(owner, app)
    owner.execute(text("UPDATE baaki.organization SET kill_switch = true WHERE org_id=:o"), {"o": ids["org"]}); owner.commit()
    r = run(eng, Arm.RULES_ONLY, ids)
    assert isinstance(r.decision, NonExecutableDecision) and r.decision.blocking_rules[0]["rule_id"] == "P0" and r.action_id is None
    assert rows(su)["recovery_action"] == 0 and rows(su)["policy_decision"] == 1


def test_account_opt_out_by_operator_blocks_p2(owner, app, ops, eng):
    ids, _ = setup(owner, app)
    opt_out_by_operator(ops, account_id=ids["account"], actor_note="do not contact"); ops.commit()
    r = run(eng, Arm.CONTROL, ids)
    assert r.decision.verdict is Verdict.BLOCK and r.decision.blocking_rules[0]["rule_id"] == "P2"


def test_quiet_hours_sunday_defers_to_monday_0900_local(owner, app, su, eng):
    sunday = AS_OF.astimezone(IST)
    while sunday.weekday() != 6:
        sunday += timedelta(days=1)
    sunday = datetime.combine(sunday.date(), time(11, 0), tzinfo=IST)
    ids = seed_org_account_contact(owner)
    issue_due(app, ids, amount=450_000, due=sunday.date() - timedelta(days=15))
    r = run_decision_pipeline(eng, arm=Arm.RULES_ONLY, account_id=ids["account"], as_of=sunday.astimezone(UTC), ruleset=RULESET)
    assert r.decision.verdict is Verdict.DEFER
    assert r.decision.defer_until == datetime.combine(sunday.date() + timedelta(days=1), time(9, 0), tzinfo=IST).astimezone(UTC)
    assert rows(su)["recovery_action"] == 0


def test_invalid_organization_timezone_fails_closed_with_no_rows(owner, app, su, eng):
    from zoneinfo import ZoneInfoNotFoundError
    ids, _ = setup(owner, app)
    owner.execute(text("UPDATE baaki.organization SET timezone = 'Mars/Olympus_Mons' WHERE org_id=:o"), {"o": ids["org"]}); owner.commit()
    with pytest.raises(ZoneInfoNotFoundError):
        run(eng, Arm.RULES_ONLY, ids)
    assert rows(su) == {"agent_proposal": 0, "validation_result": 0, "policy_decision": 0, "recovery_action": 0, "outbox": 0}


def test_inbound_unsubscribe_opts_out_contact_via_w11_inside_t2(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = proposal(interp_parsed(intent="UNSUBSCRIBE", evidence=[{"field": "intent", "quote": "STOP"}]), source_text="STOP", account_id=ids["account"], business_date=BDATE)
    store_proposal(agent, p)
    r = run(eng, Arm.TREATMENT, ids, proposals=[(p, "STOP")], inbound_contact_id=ids["contact"])
    c = su.execute(text("SELECT opted_out, opted_out_source::text, opted_out_validation_id FROM baaki.contact WHERE contact_id=:c"), {"c": ids["contact"]}).one()
    assert c[0] is True and c[1] == "INBOUND_UNSUBSCRIBE" and c[2] == r.validation_ids[0]
    assert r.decision.action_type is ActionType.SUPPRESS  # rules tree: UNSUBSCRIBE → SUPPRESS; unlinked (interpretation only)
    assert r.decision.proposal_id is None
    # the next day's CONTROL run finds no contactable contact → SUPPRESS, never a reminder
    r2 = run_decision_pipeline(eng, arm=Arm.CONTROL, account_id=ids["account"], as_of=AS_OF + timedelta(days=1), ruleset=RULESET)
    assert r2.decision.action_type is ActionType.SUPPRESS or r2.decision.verdict is Verdict.BLOCK


def test_paid_claim_suppresses_pressure_until_ttl(owner, app, agent, su, eng):
    ids, inv = setup(owner, app)
    p = proposal(interp_parsed(intent="ALREADY_PAID_CLAIM", evidence=[{"field": "intent", "quote": "already paid"}]), source_text="already paid", account_id=ids["account"], business_date=BDATE)
    store_proposal(agent, p)
    r1 = run(eng, Arm.TREATMENT, ids, proposals=[(p, "already paid")])
    assert r1.decision.action_type is ActionType.SUPPRESS
    r2 = run(eng, Arm.RULES_ONLY, ids)  # a later pass sees the PASS claim within 72h → P6 blocks SEND_PAYMENT_LINK
    assert r2.decision.verdict is Verdict.BLOCK and r2.decision.blocking_rules[0]["rule_id"] == "P6"
    assert su.execute(text("SELECT count(*) FROM baaki.recovery_action WHERE action_type <> 'SUPPRESS'")).scalar_one() == 0
