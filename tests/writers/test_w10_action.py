from datetime import timedelta

import pytest
from sqlalchemy import text

from baaki.contracts.canonical_payload import SuppressPayload
from baaki.contracts.recovery_action import RecoveryAction
from baaki.db.idempotency import canonical_payload_hash, idempotency_key
from baaki.db.writers._call import WriterUniqueViolation
from baaki.db.writers.action_auto import create_recovery_action
from baaki.db.writers.decision import record_policy_decision
from baaki.domain.enums import ActionType, Arm, SuppressReason, Verdict
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from tests.helpers import (
    NOW,
    count,
    exec_decision,
    issue,
    nonexec_decision,
    raises_writer,
    seed_org_account_contact,
)


def _decide(app, d, cands):
    did = record_policy_decision(app, d, candidate_invoice_ids=cands, trace_id=d.trace_id, account_id=d.account_id, business_date=d.business_date)
    app.commit()
    return did


def _key(d):
    return idempotency_key(d.invoice_id, d.action_type, canonical_payload_hash(d.canonical_payload.model_dump(mode="json")), d.business_date, d.arm)


def test_l4_allow_creates_queued_with_outbox(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    d = exec_decision(ids, inv, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION), tier=0)
    _decide(app, d, [inv])
    a = RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=1), _key(d))
    res = create_recovery_action(app, a, outbox_id=new_id()); app.commit()
    assert res.superseded is False and res.action_id == a.action_id
    row = app.execute(text("select state, action_type, arm, trace_id, account_id, invoice_id from baaki.recovery_action where action_id=:a"), {"a": a.action_id}).one()
    assert tuple(row) == ("QUEUED", "SUPPRESS", "CONTROL", d.trace_id, d.account_id, d.invoice_id)
    assert count(app, "outbox") == 1


def test_l5_require_approval_pending_no_outbox(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    from baaki.contracts.canonical_payload import EscalateToHumanPayload
    from baaki.domain.enums import AssigneeQueue, EscalationReason
    d = exec_decision(ids, inv, ActionType.ESCALATE_TO_HUMAN, EscalateToHumanPayload(reason_code=EscalationReason.MANUAL_REVIEW, assignee_queue=AssigneeQueue.COLLECTIONS),
                      verdict=Verdict.REQUIRE_APPROVAL, tier=2)
    _decide(app, d, [inv])
    a = RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=1), _key(d))
    create_recovery_action(app, a, outbox_id=new_id()); app.commit()
    assert app.execute(text("select state from baaki.recovery_action")).scalar_one() == "PENDING_APPROVAL"
    assert count(app, "outbox") == 0


def test_x1_x2_p9_and_duplicate_decision(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    for verdict, arm in ((Verdict.BLOCK, Arm.CONTROL), (Verdict.DEFER, Arm.RULES_ONLY)):
        nd = nonexec_decision(ids, inv, verdict, arm=arm)
        _decide(app, nd, [inv])
        with pytest.raises(ContractViolation):                       # type layer: from_decision refuses non-executable
            RecoveryAction.from_decision(nd, NOW, NOW + timedelta(days=1), "0" * 64)  # type: ignore[arg-type]
        with raises_writer("decision_not_executable"):  # writer layer allowlist
            app.execute(text("SELECT * FROM baaki_write.create_recovery_action(:a, :d, :k, :e, :n, :o)"),
                        {"a": new_id(), "d": nd.decision_id, "k": "0" * 64, "e": NOW + timedelta(days=1), "n": NOW, "o": new_id()})
        app.rollback()
    inv2 = issue(app, ids)   # fresh invoice: unlinked decisions are unique per (invoice, day, arm)
    d = exec_decision(ids, inv2, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION), tier=0, arm=Arm.RULES_ONLY)
    _decide(app, d, [inv2])
    a = RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=1), _key(d))
    create_recovery_action(app, a, outbox_id=new_id()); app.commit()
    with pytest.raises(WriterUniqueViolation):  # X2: one action per decision
        create_recovery_action(app, RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=1), _key(d)), outbox_id=new_id())
    app.rollback()


def test_r5_idempotency_collision_records_superseded_and_returns_original(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    pay = SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION)
    d1 = exec_decision(ids, inv, ActionType.SUPPRESS, pay, tier=0, arm=Arm.CONTROL)
    d2 = exec_decision(ids, inv, ActionType.SUPPRESS, pay, tier=0, arm=Arm.RULES_ONLY)
    _decide(app, d1, [inv]); _decide(app, d2, [inv])
    same_key = _key(d1)  # force a collision by reusing d1's key for d2's action
    a1 = RecoveryAction.from_decision(d1, NOW, NOW + timedelta(days=1), same_key)
    a2 = RecoveryAction.from_decision(d2, NOW, NOW + timedelta(days=1), same_key)
    r1 = create_recovery_action(app, a1, outbox_id=new_id()); app.commit()
    r2 = create_recovery_action(app, a2, outbox_id=new_id()); app.commit()
    assert r1.superseded is False and r2.superseded is True and r2.action_id == a1.action_id
    states = dict(app.execute(text("select action_id, state from baaki.recovery_action")).all())
    assert states[a1.action_id] == "QUEUED" and states[a2.action_id] == "SUPERSEDED_DUPLICATE"
    assert count(app, "outbox") == 1                                 # no provider-side effect for the duplicate


def test_from_decision_is_pure(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    d = exec_decision(ids, inv, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION), tier=0)
    before = d.model_dump_json()
    a = RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=1), "1" * 64, action_id=new_id())
    b = RecoveryAction.from_decision(d, NOW, NOW + timedelta(days=1), "1" * 64, action_id=a.action_id)
    assert a == b and d.model_dump_json() == before
    assert count(app, "recovery_action") == 0                        # no DB write happened
    with pytest.raises(ContractViolation):
        RecoveryAction.from_decision(d, NOW, NOW, "1" * 64)
