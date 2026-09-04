"""E. The five triggers hold independently of the writers (owner-context inserts)."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text

from baaki.contracts.canonical_payload import SuppressPayload
from baaki.domain.enums import ActionType, Arm, SuppressReason, Verdict
from baaki.domain.ids import new_id
from tests.helpers import (
    H64,
    NOW,
    TODAY,
    exec_decision,
    issue,
    nonexec_decision,
    raises_writer,
    record_proposal,
    record_validation,
    seed_org_account_contact,
)


def test_e1_ledger_balanced_deferred(owner, app):
    ids = seed_org_account_contact(owner)
    owner.execute(text(
        "INSERT INTO baaki.ledger_entry (entry_id, txn_id, account_code, direction, amount_paise, source, posted_at) "
        "VALUES (:e, :t, 'SALES', 'CREDIT', 500, 'ISSUANCE', now())"), {"e": new_id(), "t": new_id()})
    with raises_writer("ledger_unbalanced"):
        owner.commit()
    owner.rollback()
    assert owner.execute(text("select count(*) from baaki.ledger_entry")).scalar_one() == 0


def test_e2_one_invoice_per_txn(owner, app):
    ids = seed_org_account_contact(owner)
    inv1, inv2 = issue(app, ids), issue(app, ids)
    txn = new_id()
    ar = "AR:" + str(ids["account"])
    owner.execute(text(
        "INSERT INTO baaki.ledger_entry (entry_id, txn_id, account_code, invoice_id, direction, amount_paise, source, posted_at) "
        "VALUES (:e, :t, :c, :i, 'DEBIT', 100, 'ISSUANCE', now())"), {"e": new_id(), "t": txn, "c": ar, "i": inv1})
    with raises_writer("cross_invoice_txn"):
        owner.execute(text(
            "INSERT INTO baaki.ledger_entry (entry_id, txn_id, account_code, invoice_id, direction, amount_paise, source, posted_at) "
            "VALUES (:e, :t, :c, :i, 'DEBIT', 100, 'ISSUANCE', now())"), {"e": new_id(), "t": txn, "c": ar, "i": inv2})
    owner.rollback()


def _insert_action(owner, decision_id, action_type, ids, inv):
    owner.execute(text(
        "INSERT INTO baaki.recovery_action (action_id, decision_id, trace_id, account_id, invoice_id, arm, action_type, state, idempotency_key, expires_at, created_at, updated_at) "
        "VALUES (:a, :d, :a, :acc, :inv, 'CONTROL', CAST(:at AS baaki.action_type), 'QUEUED', :k, :exp, :n, :n)"),
        {"a": new_id(), "d": decision_id, "acc": ids["account"], "inv": inv, "at": action_type, "k": H64, "exp": NOW + timedelta(days=1), "n": NOW})


def _record(app, decision, candidates):
    from baaki.db.writers.decision import record_policy_decision
    did = record_policy_decision(app, decision, candidate_invoice_ids=candidates, trace_id=decision.trace_id,
                                 account_id=decision.account_id, business_date=decision.business_date)
    app.commit()
    return did


def test_e3_action_requires_executable_decision(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    blocked = _record(app, nonexec_decision(ids, inv, Verdict.BLOCK), [inv])
    deferred = _record(app, nonexec_decision(ids, inv, Verdict.DEFER, arm=Arm.RULES_ONLY), [inv])
    for did in (blocked, deferred):
        with raises_writer("decision_not_executable"):
            _insert_action(owner, did, "SUPPRESS", ids, inv)
        owner.rollback()
    with raises_writer("decision_not_executable"):
        _insert_action(owner, uuid4(), "SUPPRESS", ids, inv)       # unknown decision
    owner.rollback()


def test_e3b_allowlist_refuses_a_synthetic_future_verdict(su, owner, app):
    """Add a verdict label in a savepoint-free session, insert a decision with it, assert refusal, then drop the test DB state via TRUNCATE."""
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    su.execute(text("ALTER TYPE baaki.verdict ADD VALUE IF NOT EXISTS 'FUTURE_VERDICT'")); su.commit()
    # Relax the two shape CHECKs temporarily so the synthetic verdict can be inserted at all.
    su.execute(text("ALTER TABLE baaki.policy_decision DROP CONSTRAINT ck_executable_shape, DROP CONSTRAINT ck_nonexecutable_shape")); su.commit()
    try:
        did = new_id()
        su.execute(text(
            "INSERT INTO baaki.policy_decision (decision_id, trace_id, account_id, business_date, invoice_id, arm, verdict, tier, action_type, canonical_payload, "
            "policy_version, kernel_version, policy_hash, snapshot_hash, degradation_level, decided_at) VALUES "
            "(:d, :d, :a, current_date, :inv, 'CONTROL', 'FUTURE_VERDICT', 1, 'SUPPRESS', '{\"action_type\":\"SUPPRESS\",\"reason_code\":\"NO_ELIGIBLE_ACTION\"}', 'p','k',:h,:h,'L1',now())"),
            {"d": did, "a": ids["account"], "inv": inv, "h": H64})
        su.commit()
        with raises_writer("decision_not_executable"):
            _insert_action(owner, did, "SUPPRESS", ids, inv)
        owner.rollback()
    finally:
        su.execute(text("TRUNCATE baaki.policy_decision CASCADE"))
        su.execute(text(
            "ALTER TABLE baaki.policy_decision ADD CONSTRAINT ck_executable_shape CHECK ((verdict IN ('ALLOW','REQUIRE_APPROVAL')) = (action_type IS NOT NULL AND canonical_payload IS NOT NULL)), "
            "ADD CONSTRAINT ck_nonexecutable_shape CHECK ((verdict IN ('BLOCK','DEFER')) = (action_type IS NULL AND canonical_payload IS NULL))"))
        su.commit()


def test_e4_action_type_must_match_decision(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    did = _record(app, exec_decision(ids, inv, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION), tier=0), [inv])
    with raises_writer("action_type_mismatch"):
        _insert_action(owner, did, "SEND_REMINDER", ids, inv)
    owner.rollback()


def test_e5_decision_linkage(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    p1 = record_proposal(agent, ids, inv)
    p2 = record_proposal(agent, ids, inv)
    v1 = record_validation(app, p1); v2 = record_validation(app, p2); app.commit()

    def ins(proposal, validation, account=None, trace=None):
        owner.execute(text(
            "INSERT INTO baaki.policy_decision (decision_id, proposal_id, validation_id, trace_id, account_id, business_date, invoice_id, arm, verdict, tier, "
            "blocking_rules, policy_version, kernel_version, policy_hash, snapshot_hash, degradation_level, decided_at) VALUES "
            "(:d, :p, :v, :t, :a, :bd, :inv, 'TREATMENT', 'BLOCK', 0, '[{\"r\": 1}]', 'p','k',:h,:h,'L1',now())"),
            {"d": new_id(), "p": proposal, "v": validation, "t": trace or new_id(), "a": account or ids["account"], "bd": TODAY, "inv": inv, "h": H64})
    with raises_writer("linkage_violation"):
        ins(p1, v2)                               # validation belongs to another proposal
    owner.rollback()
    with raises_writer("linkage_violation"):
        ins(p1, v1, account=uuid4())              # account copy mismatch
    owner.rollback()
    # invoice of another account
    other_acct = new_id()
    owner.execute(text("INSERT INTO baaki.account (account_id, org_id, external_ref, name) VALUES (:a, :o, 'ACC-2', 'Other')"), {"a": other_acct, "o": ids["org"]})
    owner.commit()
    with raises_writer("linkage_violation"):
        owner.execute(text(
            "INSERT INTO baaki.policy_decision (decision_id, trace_id, account_id, business_date, invoice_id, arm, verdict, tier, blocking_rules, "
            "policy_version, kernel_version, policy_hash, snapshot_hash, degradation_level, decided_at) VALUES "
            "(:d, :d, :a, :bd, :inv, 'CONTROL', 'BLOCK', 0, '[{\"r\": 1}]', 'p','k',:h,:h,'L1',now())"),
            {"d": new_id(), "a": other_acct, "bd": TODAY, "inv": inv, "h": H64})
    owner.rollback()
