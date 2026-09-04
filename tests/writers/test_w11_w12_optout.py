"""W11/W12 opt-out writers, OO1–OO3 CHECKs, H17 invoker assertion, role matrix, monotonicity (§6.18, AC1/AC2/AC11/AC13)."""
import pytest
from sqlalchemy import text

from baaki.db.writers.operator import opt_out_by_operator
from baaki.db.writers.optout_evidence import opt_out_contact_from_evidence
from tests.helpers import (
    exec_decision,
    issue,
    raises_check,
    raises_privilege,
    record_proposal,
    record_validation,
    seed_org_account_contact,
)
from tests.phase2_helpers import add_contact, refused, unauthorized

UNSUB = {"intent": "UNSUBSCRIBE", "effective_confidence": 0.95}


def _contact(su, cid):
    return su.execute(text("SELECT opted_out, opted_out_source::text, opted_out_by_role, opted_out_validation_id, opted_out_at FROM baaki.contact WHERE contact_id=:c"), {"c": cid}).one()


# ── W11 ─────────────────────────────────────────────────────────────────────────────────
def test_w11_pass_unsubscribe_opts_out_with_evidence(owner, app, agent, su):
    ids = seed_org_account_contact(owner)
    pid = record_proposal(agent, ids, None, parsed={"intent": "UNSUBSCRIBE"})
    vid = record_validation(app, pid, normalized=UNSUB); app.commit()
    assert opt_out_contact_from_evidence(app, contact_id=ids["contact"], validation_id=vid) is True
    app.commit()
    row = _contact(su, ids["contact"])
    assert row[0] is True and row[1] == "INBOUND_UNSUBSCRIBE" and row[2] == "baaki_app" and row[3] == vid and row[4] is not None
    # idempotent: second call is a no-op returning false, metadata untouched
    assert opt_out_contact_from_evidence(app, contact_id=ids["contact"], validation_id=vid) is False
    app.commit()
    assert _contact(su, ids["contact"]) == row


@pytest.mark.parametrize("outcome,normalized,code", [
    ("REJECT", None, "validation_not_pass"),
    ("PASS", {"intent": "WILL_PAY_ON_DATE", "effective_confidence": 0.9}, "intent_not_unsubscribe"),
])
def test_w11_refuses_without_unsubscribe_evidence(owner, app, agent, outcome, normalized, code):
    ids = seed_org_account_contact(owner)
    pid = record_proposal(agent, ids, None)
    vid = record_validation(app, pid, outcome=outcome, reasons=["UNPARSEABLE"] if outcome == "REJECT" else None, normalized=normalized); app.commit()
    with refused(code):
        opt_out_contact_from_evidence(app, contact_id=ids["contact"], validation_id=vid)


def test_w11_refuses_cross_account_contact_and_missing_validation(owner, app, agent):
    ids = seed_org_account_contact(owner)
    other = seed_org_account_contact(owner)
    pid = record_proposal(agent, ids, None, parsed={"intent": "UNSUBSCRIBE"})
    vid = record_validation(app, pid, normalized=UNSUB); app.commit()
    with refused("contact_not_in_account"):
        opt_out_contact_from_evidence(app, contact_id=other["contact"], validation_id=vid)
    app.rollback()
    from baaki.domain.ids import new_id
    with refused("validation_not_found"):
        opt_out_contact_from_evidence(app, contact_id=ids["contact"], validation_id=new_id())


def test_w11_contact_in_normalized_must_match(owner, app, agent):
    ids = seed_org_account_contact(owner)
    c2 = add_contact(owner, ids["account"], "SMS", "sms-1")
    pid = record_proposal(agent, ids, None, parsed={"intent": "UNSUBSCRIBE"})
    vid = record_validation(app, pid, normalized=dict(UNSUB, contact_id=str(c2))); app.commit()
    with refused("contact_mismatch"):
        opt_out_contact_from_evidence(app, contact_id=ids["contact"], validation_id=vid)


def test_w11_is_app_only(owner, app, agent, ops, sim):
    ids = seed_org_account_contact(owner)
    pid = record_proposal(agent, ids, None, parsed={"intent": "UNSUBSCRIBE"})
    vid = record_validation(app, pid, normalized=UNSUB); app.commit()
    for conn in (agent, ops, sim):
        with unauthorized():
            opt_out_contact_from_evidence(conn, contact_id=ids["contact"], validation_id=vid)
        conn.rollback()


# ── W12 ─────────────────────────────────────────────────────────────────────────────────
def test_w12_ops_opts_out_contact_and_account_with_note(owner, ops, su):
    ids = seed_org_account_contact(owner)
    assert opt_out_by_operator(ops, contact_id=ids["contact"], actor_note="customer called, asked to stop") is True
    assert opt_out_by_operator(ops, contact_id=ids["contact"], actor_note="again") is False   # idempotent
    ops.commit()
    row = _contact(su, ids["contact"])
    assert row[0] is True and row[1] == "HUMAN" and row[2] == "baaki_ops" and row[3] is None
    assert opt_out_by_operator(ops, account_id=ids["account"], actor_note="legal hold") is True
    ops.commit()
    a = su.execute(text("SELECT opt_out, opt_out_source::text, opt_out_by_role, opt_out_note FROM baaki.account WHERE account_id=:a"), {"a": ids["account"]}).one()
    assert a == (True, "HUMAN", "baaki_ops", "legal hold")


def test_w12_requires_exactly_one_target_and_a_note(owner, ops):
    ids = seed_org_account_contact(owner)
    with refused("exactly_one_target_required"):
        opt_out_by_operator(ops, account_id=ids["account"], contact_id=ids["contact"], actor_note="x")
    ops.rollback()
    with refused("exactly_one_target_required"):
        opt_out_by_operator(ops, actor_note="x")
    ops.rollback()
    with refused("actor_note_required"):
        opt_out_by_operator(ops, contact_id=ids["contact"], actor_note="   ")


def test_w12_is_ops_only_by_grant_and_by_h17(owner, app, agent, sim, su):
    ids = seed_org_account_contact(owner)
    for conn in (app, agent, sim):
        with unauthorized():
            opt_out_by_operator(conn, contact_id=ids["contact"], actor_note="x")
        conn.rollback()
    # a superuser bypasses grants; H17 still refuses because session_user is not baaki_ops
    with unauthorized():
        opt_out_by_operator(su, contact_id=ids["contact"], actor_note="x")
    su.rollback()
    # SET ROLE baaki_owner (session_user = baaki_migrate) is likewise refused
    with unauthorized():
        opt_out_by_operator(owner, contact_id=ids["contact"], actor_note="x")
    owner.rollback()


# ── OO1–OO3 CHECKs and monotonicity ─────────────────────────────────────────────────────
def test_oo1_flag_without_metadata_is_unrepresentable(owner):
    ids = seed_org_account_contact(owner)
    with raises_check():
        owner.execute(text("UPDATE baaki.contact SET opted_out = true WHERE contact_id=:c"), {"c": ids["contact"]})
    owner.rollback()
    with raises_check():
        owner.execute(text("UPDATE baaki.account SET opt_out = true WHERE account_id=:a"), {"a": ids["account"]})
    owner.rollback()


def test_oo2_oo3_source_role_evidence_coupling(owner):
    ids = seed_org_account_contact(owner)
    bad = [
        "opted_out = true, opted_out_at = now(), opted_out_source = 'INBOUND_UNSUBSCRIBE', opted_out_by_role = 'baaki_app'",  # no validation id
        "opted_out = true, opted_out_at = now(), opted_out_source = 'HUMAN', opted_out_by_role = 'baaki_app'",                # HUMAN must be ops
        "opted_out = true, opted_out_at = now(), opted_out_by_role = 'baaki_ops'",                                            # no source
        "opted_out_source = 'HUMAN'",                                                                                          # source without flag
    ]
    for setexpr in bad:
        with raises_check():
            owner.execute(text(f"UPDATE baaki.contact SET {setexpr} WHERE contact_id=:c"), {"c": ids["contact"]})
        owner.rollback()
    with raises_check():
        owner.execute(text("UPDATE baaki.account SET opt_out = true, opt_out_at = now(), opt_out_source = 'INBOUND_UNSUBSCRIBE', opt_out_by_role = 'baaki_app' WHERE account_id=:a"), {"a": ids["account"]})
    owner.rollback()


def test_no_role_can_clear_an_opt_out(owner, app, ops, agent, sim):
    ids = seed_org_account_contact(owner)
    opt_out_by_operator(ops, contact_id=ids["contact"], actor_note="stop"); ops.commit()
    for conn in (app, ops, agent, sim):
        with raises_privilege():
            conn.execute(text("UPDATE baaki.contact SET opted_out = false WHERE contact_id=:c"), {"c": ids["contact"]})
        conn.rollback()
    fns = owner.execute(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='baaki_write' AND p.prosrc ILIKE '%opted_out = false%'")).scalar_one()
    assert fns == 0


def test_opted_out_contact_is_refused_by_w09_p2(owner, app, ops):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    opt_out_by_operator(ops, contact_id=ids["contact"], actor_note="stop"); ops.commit()
    from baaki.contracts.canonical_payload import SendReminderPayload, TemplateId
    from baaki.domain.enums import ActionType, Channel
    d = exec_decision(ids, inv, ActionType.SEND_REMINDER,
                      SendReminderPayload(contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.reminder.email.v1")))
    from baaki.db.writers.decision import record_policy_decision
    with refused("contact_invalid"):
        record_policy_decision(app, d, candidate_invoice_ids=[inv], trace_id=d.trace_id, account_id=ids["account"], business_date=d.business_date)


def test_template_seed_v1_present_active_and_hash_bound(su):
    from hashlib import sha256
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    rows = su.execute(text("SELECT template_id, channel::text, action_type::text, purpose::text, active, body_hash FROM baaki.template_registry WHERE template_id LIKE 'tpl.%.v1' ORDER BY template_id")).all()
    assert [r[0] for r in rows] == ["tpl.dispute.email.v1", "tpl.installment.email.v1", "tpl.link.email.v1", "tpl.nudge.email.v1", "tpl.reminder.email.v1", "tpl.reminder.sms.v1"]
    for tid, ch, at, purpose, active, h in rows:
        assert active and h == sha256((root / "config" / "templates" / f"{tid}.txt").read_bytes()).hexdigest()
    assert dict((r[0], (r[1], r[2], r[3])) for r in rows)["tpl.nudge.email.v1"] == ("EMAIL", "SEND_REMINDER", "COURTESY_NUDGE")
