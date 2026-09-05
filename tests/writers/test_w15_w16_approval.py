"""W15/W16 — human approval of a tier-2 action (ARCHITECTURE.md §6.6, H17).

The kernel parks a REQUIRE_APPROVAL action at PENDING_APPROVAL and withholds its outbox row. These writers
are the only way that state ever moves. What matters here is not that approval works, but that it cannot be
abused: only an operator may call it, only PENDING_APPROVAL may transition, terminal states stay terminal,
and a second approval can never produce a second unit of work.
"""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import text

from baaki.contracts.canonical_payload import EscalateToHumanPayload
from baaki.contracts.recovery_action import RecoveryAction
from baaki.db.idempotency import canonical_payload_hash, idempotency_key
from baaki.db.writers.action_auto import create_recovery_action
from baaki.db.writers.decision import record_policy_decision
from baaki.db.writers.operator import approve_recovery_action, reject_recovery_action
from baaki.domain.enums import (
    ActionState,
    ActionType,
    AssigneeQueue,
    EscalationReason,
    Verdict,
)
from baaki.domain.errors import WriterRefused
from baaki.domain.ids import new_id
from tests.helpers import NOW, count, exec_decision, issue, seed_org_account_contact

NOTE = "reviewed by collections lead"


@contextmanager
def refused(code: str):
    """The writer layer maps a named DB refusal onto WriterRefused; assert on the code it carries."""
    with pytest.raises(WriterRefused) as ei:
        yield
    assert ei.value.code == code, f"expected {code!r}, got {ei.value.code!r}"


def _pending(owner, app):
    """A genuine tier-2 action sitting at PENDING_APPROVAL, created by the committed W09/W10 path."""
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    d = exec_decision(ids, inv, ActionType.ESCALATE_TO_HUMAN, EscalateToHumanPayload(reason_code=EscalationReason.MANUAL_REVIEW,
                                            assignee_queue=AssigneeQueue.COLLECTIONS),
                      verdict=Verdict.REQUIRE_APPROVAL, tier=2)
    record_policy_decision(app, d, candidate_invoice_ids=[inv], trace_id=d.trace_id,
                           account_id=d.account_id, business_date=d.business_date)
    app.commit()
    key = idempotency_key(d.invoice_id, d.action_type,
                          canonical_payload_hash(d.canonical_payload.model_dump(mode="json")),
                          d.business_date, d.arm)
    # NOW is a fixed past instant in the helpers; give the action a live horizon so the
    # writer's expiry guard is not what is under test here.
    ra = RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=3650), key)
    created = create_recovery_action(app, ra, outbox_id=new_id())
    app.commit()
    return ids, created.action_id


def _state(conn, action_id):
    return conn.execute(text("SELECT state::text FROM baaki.recovery_action WHERE action_id = :a"),
                        {"a": action_id}).scalar_one()


def _row(conn, action_id):
    return conn.execute(text("SELECT state::text, approved_by_role, approved_by_note, approved_at "
                             "FROM baaki.recovery_action WHERE action_id = :a"), {"a": action_id}).one()


# ── the pending action is genuinely pending, with no work queued ─────────────────────────


def test_a_tier_two_action_starts_pending_with_no_outbox(owner, app, su):
    _ids, aid = _pending(owner, app)
    assert _state(su, aid) == ActionState.PENDING_APPROVAL.value
    assert count(su, "outbox") == 0


# ── approve ──────────────────────────────────────────────────────────────────────────────


def test_pending_to_approved_queues_the_action_and_records_the_operator(owner, app, ops, su):
    _ids, aid = _pending(owner, app)
    assert approve_recovery_action(ops, action_id=aid, actor_note=NOTE, outbox_id=new_id()) == "QUEUED"
    ops.commit()
    state, role, note, at = _row(su, aid)
    assert state == ActionState.QUEUED.value          # approval *is* becoming executable
    assert role == "baaki_ops"                        # actor recorded
    assert note == NOTE                               # note recorded
    assert at is not None                             # timestamp recorded
    assert count(su, "outbox") == 1                   # the row W10 withheld now exists


def test_approval_creates_exactly_one_unit_of_work(owner, app, ops, su):
    _ids, aid = _pending(owner, app)
    approve_recovery_action(ops, action_id=aid, actor_note=NOTE, outbox_id=new_id())
    ops.commit()
    with refused("not_pending_approval"):
        approve_recovery_action(ops, action_id=aid, actor_note="second attempt", outbox_id=new_id())
    ops.rollback()
    assert count(su, "outbox") == 1                   # never two
    assert _state(su, aid) == ActionState.QUEUED.value


# ── reject ───────────────────────────────────────────────────────────────────────────────


def test_pending_to_rejected_records_the_operator_and_queues_nothing(owner, app, ops, su):
    _ids, aid = _pending(owner, app)
    assert reject_recovery_action(ops, action_id=aid, actor_note="not appropriate") == "APPROVAL_REJECTED"
    ops.commit()
    state, role, note, at = _row(su, aid)
    assert state == ActionState.APPROVAL_REJECTED.value
    assert (role, note) == ("baaki_ops", "not appropriate")
    assert at is not None
    assert count(su, "outbox") == 0                   # nothing can ever be delivered


def test_a_rejection_requires_a_reason(owner, app, ops):
    _ids, aid = _pending(owner, app)
    for bad in ("", "   "):
        with refused("actor_note_required"):
            reject_recovery_action(ops, action_id=aid, actor_note=bad)
        ops.rollback()


# ── terminal states stay terminal ────────────────────────────────────────────────────────


@pytest.mark.parametrize("first,second,msg", [
    ("approve", "approve", "not_pending_approval"),
    ("approve", "reject", "not_pending_approval"),
    ("reject", "approve", "not_pending_approval"),
    ("reject", "reject", "not_pending_approval"),
])
def test_a_decided_action_cannot_be_decided_again(owner, app, ops, su, first, second, msg):
    _ids, aid = _pending(owner, app)
    do = {"approve": lambda: approve_recovery_action(ops, action_id=aid, actor_note=NOTE, outbox_id=new_id()),
          "reject": lambda: reject_recovery_action(ops, action_id=aid, actor_note=NOTE)}
    do[first]()
    ops.commit()
    before = _state(su, aid)
    with refused(msg):
        do[second]()
    ops.rollback()
    assert _state(su, aid) == before                  # the first decision stands


# ── authorization ────────────────────────────────────────────────────────────────────────


def test_the_app_role_cannot_approve_what_it_proposed(owner, app, su):
    """baaki_app runs the recovery pipeline; it holds no EXECUTE on either writer."""
    _ids, aid = _pending(owner, app)
    with pytest.raises(Exception):
        approve_recovery_action(app, action_id=aid, actor_note=NOTE, outbox_id=new_id())
    app.rollback()
    assert _state(su, aid) == ActionState.PENDING_APPROVAL.value


def test_the_agent_role_cannot_approve(owner, app, agent, su):
    _ids, aid = _pending(owner, app)
    with pytest.raises(Exception):
        approve_recovery_action(agent, action_id=aid, actor_note=NOTE, outbox_id=new_id())
    agent.rollback()
    assert _state(su, aid) == ActionState.PENDING_APPROVAL.value


def test_an_unknown_action_is_refused(ops):
    with refused("action_not_found"):
        approve_recovery_action(ops, action_id=new_id(), actor_note=NOTE, outbox_id=new_id())
    ops.rollback()


# ── approval authorises, it never authors ────────────────────────────────────────────────


def test_approval_does_not_touch_money_or_the_action_itself(owner, app, ops, su):
    _ids, aid = _pending(owner, app)
    before = su.execute(text("SELECT action_type::text, invoice_id, decision_id, idempotency_key "
                             "FROM baaki.recovery_action WHERE action_id = :a"), {"a": aid}).one()
    ledger_before = count(su, "ledger_entry")
    approve_recovery_action(ops, action_id=aid, actor_note=NOTE, outbox_id=new_id())
    ops.commit()
    after = su.execute(text("SELECT action_type::text, invoice_id, decision_id, idempotency_key "
                            "FROM baaki.recovery_action WHERE action_id = :a"), {"a": aid}).one()
    assert after == before                            # nothing about the action was rewritten
    assert count(su, "ledger_entry") == ledger_before  # and no money moved
    assert count(su, "payment_event") == 0
