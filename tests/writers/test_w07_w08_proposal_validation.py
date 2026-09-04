from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import (
    H64,
    count,
    issue,
    raises_unique,
    raises_writer,
    record_proposal,
    record_validation,
    seed_org_account_contact,
)


def test_w07_records_and_forces_treatment(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    pid = record_proposal(agent, ids, inv)
    row = agent.execute(text("select arm, parse_status from baaki.agent_proposal where proposal_id=:p"), {"p": pid}).one()
    assert tuple(row) == ("TREATMENT", "OK")


def test_w07_refusals(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    with raises_writer("forbidden_money_field"):
        record_proposal(agent, ids, inv, parsed={"intent": "X", "amount": 5})
    agent.rollback()
    with raises_writer("forbidden_money_field"):
        record_proposal(agent, ids, inv, parsed={"settlement": 5})
    agent.rollback()
    with raises_writer("typed_date_forbidden"):
        record_proposal(agent, ids, inv, parsed={"promised_date": "2026-09-09"})
    agent.rollback()
    with raises_writer("parse_status_mismatch"):
        record_proposal(agent, ids, inv, parsed=None, parse_status="OK")
    agent.rollback()
    with raises_writer("invoice_not_in_account"):
        record_proposal(agent, ids, new_id())
    agent.rollback()
    assert count(agent, "agent_proposal") == 0


def test_w07_daily_unique(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    record_proposal(agent, ids, inv, input_hash=H64)
    with raises_unique():
        record_proposal(agent, ids, inv, input_hash=H64)
    agent.rollback()


def test_w08_derives_from_proposal_and_enforces_shape(owner, app, agent):
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)
    pid = record_proposal(agent, ids, inv)
    vid = record_validation(app, pid); app.commit()
    p = app.execute(text("select trace_id, account_id, business_date from baaki.agent_proposal where proposal_id=:p"), {"p": pid}).one()
    v = app.execute(text("select trace_id, account_id, business_date from baaki.validation_result where validation_id=:v"), {"v": vid}).one()
    assert tuple(p) == tuple(v)                       # V8 — derived, not supplied
    names = app.execute(text("select proargnames from pg_proc where proname='record_validation_result'")).scalar_one()
    assert not ({"p_trace_id", "p_account_id", "p_business_date"} & set(names))   # LK2
    with raises_unique():
        record_validation(app, pid)
    app.rollback()
    pid2 = record_proposal(agent, ids, inv)
    with raises_writer("shape_violation"):
        record_validation(app, pid2, outcome="PASS", normalized=None)
    app.rollback()
    with raises_writer("shape_violation"):
        record_validation(app, pid2, outcome="REJECT", reasons=[])
    app.rollback()
    with raises_writer("proposal_not_found"):
        record_validation(app, new_id())
    app.rollback()
