"""F. Named constraints exist and behave (positive + negative), exercised in owner context."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import H64, NOW, issue, raises_check, seed_org_account_contact

EXPECTED = {
    "invoice": {"uq_invoice_number", "ck_invoice_issued_positive", "ck_invoice_dates"},
    "ledger_entry": {"ck_ledger_amount_positive", "ck_ledger_account_code", "ck_ledger_ar_has_invoice",
                     "ck_ledger_issuance_no_event", "ck_ledger_source_class"},
    "payment_event": {"uq_payment_provider_id", "ck_payment_amount_positive", "ck_payment_currency",
                      "ck_payment_evidence_xor", "ck_payment_source_matches", "ck_payment_attribution"},
    "agent_proposal": {"uq_proposal_daily", "ck_proposal_arm", "ck_proposal_parse", "ck_proposal_no_money_keys"},
    "validation_result": {"uq_validation_proposal", "ck_validation_pass", "ck_validation_reject"},
    "policy_decision": {"ck_executable_shape", "ck_nonexecutable_shape", "ck_block_has_rules", "ck_defer_has_until",
                        "ck_tier2_approval", "ck_nonllm_no_proposal", "ck_proposal_paired", "ck_tier_domain",
                        "uq_decision_validation_day"},
    "recovery_action": {"uq_action_decision", "ck_action_attempts"},
    "outbox": {"uq_outbox_action"},
    "template_registry": {"ck_template_pair"},
    "webhook_event": {"uq_webhook_dedupe"},
    "sweep_run": {"uq_sweep_response", "ck_sweep_window"},
}


def test_named_constraints_exist(su):
    for table, expected in EXPECTED.items():
        names = {r[0] for r in su.execute(text(
            "select conname from pg_constraint c join pg_class r on r.oid=c.conrelid join pg_namespace n on n.oid=r.relnamespace "
            "where n.nspname='baaki' and r.relname=:t"), {"t": table})}
        assert expected <= names, (table, expected - names)
    idx = {r[0] for r in su.execute(text("select indexname from pg_indexes where schemaname='baaki'"))}
    assert {"uq_payment_webhook_event", "uq_ledger_event_code", "uq_decision_unlinked_day", "uq_action_idempotency"} <= idx


def _ledger_insert(owner, **kw):
    params = dict(e=new_id(), t=new_id(), code="SALES", inv=None, d="CREDIT", amt=100, pe=None, src="ISSUANCE")
    params.update(kw)
    owner.execute(text(
        "INSERT INTO baaki.ledger_entry (entry_id, txn_id, account_code, invoice_id, direction, amount_paise, payment_event_id, source, posted_at) "
        "VALUES (:e, :t, :code, :inv, CAST(:d AS baaki.dr_cr), :amt, :pe, CAST(:src AS baaki.ledger_source), now())"), params)


def test_ledger_checks(owner):
    with raises_check():
        _ledger_insert(owner, amt=0)
    owner.rollback()
    with raises_check():
        _ledger_insert(owner, code="FEES")
    owner.rollback()
    with raises_check():
        _ledger_insert(owner, code="AR:" + str(uuid4()), inv=None)   # AR needs invoice_id
    owner.rollback()
    with raises_check():
        _ledger_insert(owner, src="PAYMENT", pe=None)                  # PAYMENT needs event
    owner.rollback()
    with raises_check():
        _ledger_insert(owner, code="UNAPPLIED_CASH", src="ISSUANCE")   # disallowed (source, class)
    owner.rollback()


def test_template_pair_check(owner):
    with raises_check():
        owner.execute(text(
            "INSERT INTO baaki.template_registry (template_id, channel, action_type, purpose, active, version, body_hash) "
            "VALUES ('bad', 'EMAIL', 'SEND_PAYMENT_LINK', 'REMINDER', true, 1, :h)"), {"h": H64})
    owner.rollback()


def test_payment_event_checks(owner):
    ids = seed_org_account_contact(owner)
    base = dict(p=new_id(), ppid="pay_x", amt=100, cur="INR", st="captured", src="WEBHOOK", w=None, s=None,
                raw="{}", h=H64, inv=None, m="UNATTRIBUTED")

    def ins(**kw):
        d = dict(base); d.update(kw); d["p"] = new_id()
        owner.execute(text(
            "INSERT INTO baaki.payment_event (payment_event_id, provider, provider_payment_id, amount_paise, currency, provider_status, paid_at, "
            "source, webhook_event_id, sweep_run_id, provider_payload_raw, provider_payload_hash, attributed_invoice_id, attribution_method) "
            "VALUES (:p, 'razorpay', :ppid, :amt, :cur, :st, now(), CAST(:src AS baaki.payment_source), :w, :s, :raw, :h, :inv, CAST(:m AS baaki.attribution_method))"), d)
    with raises_check():
        ins()                       # neither evidence id
    owner.rollback()
    with raises_check():
        ins(cur="USD")
    owner.rollback()
    with raises_check():
        ins(m="NOTES_INVOICE_ID")   # attributed method with null invoice
    owner.rollback()


def test_proposal_checks(owner):
    ids = seed_org_account_contact(owner)

    def ins(**kw):
        d = dict(p=new_id(), a=ids["account"], arm="TREATMENT", raw="{}", parsed=None, ps="TIMEOUT", ih=H64)
        d.update(kw)
        owner.execute(text(
            "INSERT INTO baaki.agent_proposal (proposal_id, trace_id, account_id, kind, business_date, arm, provider, model_id, prompt_template_id, "
            "schema_version, prompt_hash, input_hash, raw_response, parsed, parse_status, evidence, latency_ms) VALUES "
            "(:p, :p, :a, 'INTERPRETATION', current_date, CAST(:arm AS baaki.arm), 'x', 'x', 'x', 'x', :ih, :ih, CAST(:raw AS jsonb), "
            "CAST(:parsed AS jsonb), CAST(:ps AS baaki.parse_status), '[]', 1)"), d)
    with raises_check():
        ins(arm="CONTROL")
    owner.rollback()
    with raises_check():
        ins(ps="OK", parsed=None)
    owner.rollback()
    with raises_check():
        ins(ps="OK", parsed='{"amount": 5}')
    owner.rollback()
    with raises_check():
        ins(ps="OK", parsed='{"settlement_offer": 5}')
    owner.rollback()
    with raises_check():
        ins(ps="OK", parsed='{"promised_date": "2026-09-09"}')
    owner.rollback()
    ins(ps="OK", parsed='{"promised_date_raw": "next Tuesday"}')   # legal
    owner.commit()


def test_decision_checks_via_owner(owner, app):
    """Owner-context inserts bypass W09 so the CHECKs themselves are exercised."""
    ids = seed_org_account_contact(owner)
    inv = issue(app, ids)

    def ins(**kw):
        d = dict(d=new_id(), a=ids["account"], inv=inv, v="ALLOW", tier=1, at="SUPPRESS", payload='{"action_type":"SUPPRESS","reason_code":"NO_ELIGIBLE_ACTION"}',
                 du=None, br="[]", arm="CONTROL", h=H64)
        d.update(kw)
        owner.execute(text(
            "INSERT INTO baaki.policy_decision (decision_id, trace_id, account_id, business_date, invoice_id, arm, verdict, tier, action_type, canonical_payload, "
            "defer_until, blocking_rules, policy_version, kernel_version, policy_hash, snapshot_hash, degradation_level, decided_at) VALUES "
            "(:d, :d, :a, current_date, :inv, CAST(:arm AS baaki.arm), CAST(:v AS baaki.verdict), :tier, CAST(:at AS baaki.action_type), CAST(:payload AS jsonb), "
            ":du, CAST(:br AS jsonb), 'p', 'k', :h, :h, 'L1', now())"), d)
    with raises_check():
        ins(v="DEFER", du=NOW + timedelta(hours=1))             # DEFER with payload → nonexecutable shape
    owner.rollback()
    with raises_check():
        ins(v="ALLOW", at=None, payload=None)                  # executable without payload
    owner.rollback()
    with raises_check():
        ins(v="BLOCK", at=None, payload=None, br="[]")         # BLOCK without rules
    owner.rollback()
    with raises_check():
        ins(tier=3)
    owner.rollback()
    with raises_check():
        ins(tier=2, v="ALLOW")
    owner.rollback()
    ins(v="BLOCK", at=None, payload=None, br='[{"rule_id":"x"}]')  # legal
    owner.commit()
