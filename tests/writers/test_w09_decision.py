from datetime import timedelta

import pytest
from sqlalchemy import text

from baaki.contracts.canonical_payload import (
    EscalateToHumanPayload,
    InstallmentPart,
    LinkNotes,
    ProposeInstallmentPlanPayload,
    SendPaymentLinkPayload,
    SendReminderPayload,
    SuppressPayload,
    TemplateId,
)
from baaki.db.writers.decision import record_policy_decision
from baaki.domain.enums import (
    ActionType,
    Arm,
    AssigneeQueue,
    Channel,
    DegradationLevel,
    EscalationReason,
    SuppressReason,
    Verdict,
)
from baaki.domain.errors import ContractViolation, WriterRefused
from baaki.domain.ids import new_id
from baaki.domain.money import Paise
from tests.helpers import (
    NOW,
    TODAY,
    count,
    exec_decision,
    issue,
    nonexec_decision,
    record_proposal,
    record_validation,
    seed_org_account_contact,
)


def _rec(app, d, cands, **ctx):
    did = record_policy_decision(app, d, candidate_invoice_ids=cands, trace_id=d.trace_id, account_id=d.account_id,
                                 business_date=d.business_date, **ctx)
    app.commit()
    return did


def _refused(app, code, d, cands):
    with pytest.raises(WriterRefused) as ei:
        record_policy_decision(app, d, candidate_invoice_ids=cands, trace_id=d.trace_id, account_id=d.account_id, business_date=d.business_date)
    app.rollback()
    assert ei.value.code == code, ei.value


def test_l6_l7_l8_unlinked_decisions(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    _rec(app, nonexec_decision(ids, inv, Verdict.BLOCK), [inv])
    _rec(app, exec_decision(ids, inv, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.PTP_ACTIVE), tier=0, arm=Arm.RULES_ONLY), [inv])
    assert count(app, "policy_decision") == 2
    # partial unique for unlinked arms: one decision per (invoice, day, arm)
    _refused_unique = None
    from baaki.db.writers._call import WriterUniqueViolation
    with pytest.raises(WriterUniqueViolation):
        record_policy_decision(app, nonexec_decision(ids, inv, Verdict.DEFER), candidate_invoice_ids=[inv],
                               trace_id=new_id(), account_id=ids["account"], business_date=TODAY)
    app.rollback()


def test_linked_decision_derives_and_links(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    pid = record_proposal(agent, ids, inv)
    vid = record_validation(app, pid); app.commit()
    d = exec_decision(ids, inv, ActionType.SUPPRESS, SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION), tier=0,
                      proposal_id=pid, validation_id=vid, arm=Arm.TREATMENT, degradation=DegradationLevel.L0)
    # caller passes a wrong trace_id deliberately: W09 must ignore it and derive from the proposal
    did = record_policy_decision(app, d, candidate_invoice_ids=[inv], trace_id=new_id()); app.commit()
    p_trace = app.execute(text("select trace_id from baaki.agent_proposal where proposal_id=:p"), {"p": pid}).scalar_one()
    d_trace = app.execute(text("select trace_id from baaki.policy_decision where decision_id=:d"), {"d": did}).scalar_one()
    assert p_trace == d_trace and d_trace != d.trace_id


def test_lk1_lk3_lk5_lk7(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids); inv2 = issue(app, ids)
    p1 = record_proposal(agent, ids, inv); p2 = record_proposal(agent, ids, inv)
    v1 = record_validation(app, p1); v2 = record_validation(app, p2); app.commit()
    sup = SuppressPayload(reason_code=SuppressReason.NO_ELIGIBLE_ACTION)
    _refused(app, "linkage_mismatch", exec_decision(ids, inv, ActionType.SUPPRESS, sup, tier=0, proposal_id=p1, validation_id=v2, arm=Arm.TREATMENT), [inv])
    with pytest.raises(ContractViolation):   # P7 at the contract layer: CONTROL cannot carry a proposal
        exec_decision(ids, inv, ActionType.SUPPRESS, sup, tier=0, proposal_id=p1, validation_id=v1, arm=Arm.CONTROL)
    _refused(app, "invoice_not_candidate", exec_decision(ids, inv, ActionType.SUPPRESS, sup, tier=0), [inv2])
    # P11: rejected validation cannot yield an L0 decision
    p3 = record_proposal(agent, ids, inv, parse_status="TIMEOUT")
    v3 = record_validation(app, p3, outcome="REJECT", reasons=["PROVIDER_TIMEOUT"]); app.commit()
    _refused(app, "rejected_needs_degradation", exec_decision(ids, inv, ActionType.SUPPRESS, sup, tier=0, proposal_id=p3, validation_id=v3,
                                                             arm=Arm.TREATMENT, degradation=DegradationLevel.L0), [inv])
    # invoice scope mismatch (proposal scoped to inv, decision for inv2)
    p4 = record_proposal(agent, ids, inv); v4 = record_validation(app, p4); app.commit()
    _refused(app, "invoice_scope_mismatch", exec_decision(ids, inv2, ActionType.SUPPRESS, sup, tier=0, proposal_id=p4, validation_id=v4, arm=Arm.TREATMENT), [inv2])


def _link_payload(ids, inv, amount, template="tpl.link.email.v1", channel=Channel.EMAIL):
    return SendPaymentLinkPayload(amount_paise=Paise(amount), contact_id=ids["contact"], channel=channel, template_id=TemplateId(template),
                                  expires_at=NOW + timedelta(days=3), notes=LinkNotes(invoice_id=inv, action_id=new_id(), trace_id=new_id()))


def test_cp5_template_contact_rules(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids, amount=450_000)
    _rec(app, exec_decision(ids, inv, ActionType.SEND_PAYMENT_LINK, _link_payload(ids, inv, 450_000)), [inv])        # ✓ CP5 exact
    _refused(app, "cp5_amount_mismatch", exec_decision(ids, inv, ActionType.SEND_PAYMENT_LINK, _link_payload(ids, inv, 400_000)), [inv])
    _refused(app, "template_incompatible", exec_decision(ids, inv, ActionType.SEND_REMINDER,
             SendReminderPayload(contact_id=ids["contact"], channel=Channel.SMS, template_id=TemplateId("tpl.reminder.email.v1"))), [inv])   # T1
    _refused(app, "template_incompatible", exec_decision(ids, inv, ActionType.SEND_REMINDER,
             SendReminderPayload(contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.reminder.email.inactive"))), [inv])  # T2
    _refused(app, "template_not_registered", exec_decision(ids, inv, ActionType.SEND_REMINDER,
             SendReminderPayload(contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.nope"))), [inv])   # T3
    _refused(app, "template_incompatible", exec_decision(ids, inv, ActionType.SEND_REMINDER,
             SendReminderPayload(contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.link.email.v1"))), [inv])  # T4 wrong action type
    _refused(app, "contact_invalid", exec_decision(ids, inv, ActionType.SEND_REMINDER,
             SendReminderPayload(contact_id=new_id(), channel=Channel.EMAIL, template_id=TemplateId("tpl.reminder.email.v1"))), [inv])
    owner.execute(text("update baaki.contact set opted_out = true where contact_id=:c"), {"c": ids["contact"]}); owner.commit()
    _refused(app, "contact_invalid", exec_decision(ids, inv, ActionType.SEND_REMINDER,
             SendReminderPayload(contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.reminder.email.v1"))), [inv])


def test_cp2_installments_and_escalation_queue(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids, amount=300_000)
    parts_ok = [InstallmentPart(amount_paise=Paise(100_000), due_date=TODAY + timedelta(days=d)) for d in (10, 20, 30)]
    _rec(app, exec_decision(ids, inv, ActionType.PROPOSE_INSTALLMENT_PLAN,
         ProposeInstallmentPlanPayload(parts=parts_ok, contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.installment.email.v1")),
         verdict=Verdict.REQUIRE_APPROVAL, tier=2), [inv])
    parts_bad = parts_ok[:2]
    _refused(app, "cp2_parts_mismatch", exec_decision(ids, inv, ActionType.PROPOSE_INSTALLMENT_PLAN,
             ProposeInstallmentPlanPayload(parts=parts_bad, contact_id=ids["contact"], channel=Channel.EMAIL, template_id=TemplateId("tpl.installment.email.v1")),
             verdict=Verdict.REQUIRE_APPROVAL, tier=2), [inv])
    # GAP-1: reason -> queue mapping at the contract layer and (bypassing Pydantic) at W09
    with pytest.raises(ContractViolation):
        EscalateToHumanPayload(reason_code=EscalationReason.DISPUTE_UNRESOLVED, assignee_queue=AssigneeQueue.COLLECTIONS)
    good = EscalateToHumanPayload(reason_code=EscalationReason.DISPUTE_UNRESOLVED, assignee_queue=AssigneeQueue.DISPUTES)
    inv_b = issue(app, ids, amount=300_000)   # a second invoice: one unlinked decision per (invoice, day, arm)
    _rec(app, exec_decision(ids, inv_b, ActionType.ESCALATE_TO_HUMAN, good, verdict=Verdict.REQUIRE_APPROVAL, tier=2), [inv_b])
    # W09 assertion reached directly with a raw payload
    from tests.helpers import H64, raises_writer
    with raises_writer("queue_reason_mismatch"):
        app.execute(text(
            "SELECT baaki_write.record_policy_decision(:d, NULL, NULL, :t, :a, :bd, :inv, 'RULES_ONLY', 'REQUIRE_APPROVAL', CAST(2 AS smallint), 'ESCALATE_TO_HUMAN', "
            "'{\"action_type\":\"ESCALATE_TO_HUMAN\",\"reason_code\":\"MANUAL_REVIEW\",\"assignee_queue\":\"DISPUTES\"}'::jsonb, NULL, '{}', '[]', NULL, "
            "'p','k',:h,:h,'L1', ARRAY[:inv]::uuid[])"), {"d": new_id(), "t": new_id(), "a": ids["account"], "bd": TODAY, "inv": inv, "h": H64})
    app.rollback()
    with raises_writer("payload_extra_key"):
        app.execute(text(
            "SELECT baaki_write.record_policy_decision(:d, NULL, NULL, :t, :a, :bd, :inv, 'RULES_ONLY', 'ALLOW', CAST(0 AS smallint), 'SUPPRESS', "
            "'{\"action_type\":\"SUPPRESS\",\"reason_code\":\"NO_ELIGIBLE_ACTION\",\"amount_paise\": 5}'::jsonb, NULL, '{}', '[]', NULL, "
            "'p','k',:h,:h,'L1', ARRAY[:inv]::uuid[])"), {"d": new_id(), "t": new_id(), "a": ids["account"], "bd": TODAY, "inv": inv, "h": H64})
    app.rollback()


def test_shape_refusals_and_tier3(owner, app):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    from tests.helpers import H64, raises_any_db_error, raises_writer
    base = {"d": new_id(), "t": new_id(), "a": ids["account"], "bd": TODAY, "inv": inv, "h": H64}
    sql = ("SELECT baaki_write.record_policy_decision(:d, NULL, NULL, :t, :a, :bd, :inv, 'RULES_ONLY', CAST(:v AS baaki.verdict), CAST(:tier AS smallint), "
           "CAST(:at AS baaki.action_type), CAST(:pl AS jsonb), :du, '{}', CAST(:br AS jsonb), NULL, 'p','k',:h,:h,'L1', ARRAY[:inv]::uuid[])")
    sup = '{"action_type":"SUPPRESS","reason_code":"NO_ELIGIBLE_ACTION"}'
    for kw in [dict(v="DEFER", tier=0, at="SUPPRESS", pl=sup, du=NOW, br="[]"),      # D1
               dict(v="ALLOW", tier=0, at=None, pl=None, du=None, br="[]"),           # D2
               dict(v="BLOCK", tier=0, at=None, pl=None, du=None, br="[]"),           # D3
               dict(v="ALLOW", tier=3, at="SUPPRESS", pl=sup, du=None, br="[]")]:     # D4
        with raises_writer("shape_violation"):
            app.execute(text(sql), {**base, **kw})
        app.rollback()
    with raises_any_db_error():   # F1–F7: no such action_type label
        app.execute(text(sql), {**base, "v": "ALLOW", "tier": 1, "at": "REFUND", "pl": sup, "du": None, "br": "[]"})
    app.rollback()
    assert count(app, "policy_decision") == 0
