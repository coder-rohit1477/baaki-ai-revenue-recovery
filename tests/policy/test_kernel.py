"""§5 kernel — ladder P0–P14, §4.3 truth table, I4 monotonicity, payload derivations, DEFER windows, purity."""
import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from baaki.contracts.account_snapshot import ActivePaymentLink, ActivePtp
from baaki.contracts.policy_decision import ExecutableDecision, NonExecutableDecision
from baaki.domain.enums import (
    ACTION_TIER,
    ActionType,
    Arm,
    AssigneeQueue,
    DegradationLevel,
    EscalationReason,
    InvoiceState,
    SuppressReason,
    Verdict,
)
from baaki.domain.errors import ContractViolation
from baaki.domain.money import paise
from baaki.policy.kernel.decide import authority_tier, catalogue_tier, decide
from baaki.policy.kernel.quiet_hours import in_window, next_window_open
from tests.phase2_helpers import (
    AS_OF,
    BDATE,
    C_EMAIL,
    IST,
    LATE_AS_OF,
    RULESET,
    SUNDAY_AS_OF,
    TZ,
    cand,
    choice,
    ctx,
    facts,
    snap,
)

L0, L1 = DegradationLevel.L0, DegradationLevel.L1


def run(c, s=None, cx=None):
    return decide(c, s or snap(), RULESET, cx or ctx(level=c.origin if c.origin is not L0 else L0, arm=Arm.TREATMENT if c.origin is L0 else Arm.RULES_ONLY), org_timezone=TZ)


def blocked_by(d):
    assert isinstance(d, NonExecutableDecision) and d.verdict is Verdict.BLOCK
    return d.blocking_rules[0]["rule_id"]


# ── ladder levels ────────────────────────────────────────────────────────────────────────
def test_p0_kill_switch_blocks_everything_even_suppress():
    s = snap(facts(kill_switch=True))
    assert blocked_by(run(choice(ActionType.SUPPRESS), s)) == "P0"


def test_p1_ledger_breach_blocks():
    assert blocked_by(run(choice(), snap(facts(ledger_ok=False)))) == "P1"


def test_p2_opt_out_account_and_contact():
    assert blocked_by(run(choice(), snap(facts(opt_out=True)))) == "P2"
    s = snap(facts(contactable=[]))
    assert blocked_by(run(choice(), s)) == "P2"  # chosen contact not contactable
    # a contact-free action still passes P2 when only the contact is opted out
    d = run(choice(ActionType.SUPPRESS), s)
    assert isinstance(d, ExecutableDecision)


def test_p3_p4_paid_and_zero_outstanding():
    assert blocked_by(run(choice(), snap(facts(candidates=[cand(state=InvoiceState.PAID)])))) == "P3"
    # zero outstanding cannot be a candidate (SC2); build_snapshot enforces SC4 so the kernel sees P4 only via snapshot override
    s = snap(outstanding_paise=paise(0))
    assert blocked_by(run(choice(), s)) == "P4"


def test_p5_dispute_allows_only_dispute_details_escalate_suppress():
    s = snap(invoice_state=InvoiceState.DISPUTED)
    assert blocked_by(run(choice(ActionType.SEND_REMINDER), s)) == "P5"
    assert blocked_by(run(choice(ActionType.SEND_PAYMENT_LINK, template_id="tpl.link.email.v1"), s)) == "P5"
    d = run(choice(ActionType.REQUEST_DISPUTE_DETAILS, template_id="tpl.dispute.email.v1"), s)
    assert isinstance(d, ExecutableDecision) and d.tier == 0
    d = run(choice(ActionType.ESCALATE_TO_HUMAN), s)
    assert isinstance(d, ExecutableDecision) and d.canonical_payload.reason_code is EscalationReason.DISPUTE_UNRESOLVED
    assert d.canonical_payload.assignee_queue is AssigneeQueue.DISPUTES and d.verdict is Verdict.REQUIRE_APPROVAL
    d = run(choice(ActionType.SUPPRESS), s)
    assert d.canonical_payload.reason_code is SuppressReason.DISPUTE_OPEN


def test_p6_paid_claim_pending_allows_only_suppress_and_escalate():
    s = snap(unverified_paid_claim_until=AS_OF + timedelta(hours=1))
    assert blocked_by(run(choice(), s)) == "P6"
    assert blocked_by(run(choice(ActionType.REQUEST_DISPUTE_DETAILS, template_id="tpl.dispute.email.v1"), s)) == "P6"
    d = run(choice(ActionType.SUPPRESS), s)
    assert d.canonical_payload.reason_code is SuppressReason.PAID_CLAIM_PENDING
    d = run(choice(ActionType.ESCALATE_TO_HUMAN), s)
    assert d.canonical_payload.reason_code is EscalationReason.PAID_CLAIM_UNVERIFIED
    # expired claim window is not pending
    d = run(choice(), snap(unverified_paid_claim_until=AS_OF))
    assert isinstance(d, ExecutableDecision) and d.action_type is ActionType.SEND_REMINDER


def test_p7_active_ptp_blocks_pressure_except_t2_nudge():
    ptp = ActivePtp(ptp_id=C_EMAIL, due_date=BDATE + timedelta(days=2), promised_paise=paise(1), state="ACTIVE").model_dump()
    s = snap(active_ptp=ptp)
    assert blocked_by(run(choice(ActionType.SEND_PAYMENT_LINK, template_id="tpl.link.email.v1"), s)) == "P7"
    assert blocked_by(run(choice(ActionType.SEND_REMINDER), s)) == "P7"  # plain reminder is pressure
    d = run(choice(ActionType.SEND_REMINDER, template_id="tpl.nudge.email.v1"), s)  # T-2 courtesy nudge
    assert isinstance(d, ExecutableDecision)
    s3 = snap(active_ptp=dict(ptp, due_date=BDATE + timedelta(days=3)))
    assert blocked_by(run(choice(ActionType.SEND_REMINDER, template_id="tpl.nudge.email.v1"), s3)) == "P7"  # not T-2
    assert isinstance(run(choice(ActionType.REQUEST_DISPUTE_DETAILS, template_id="tpl.dispute.email.v1"), s), ExecutableDecision)


def test_p8_active_link_blocks_new_link_only():
    link = ActivePaymentLink(link_id="plink_x", created_at=AS_OF - timedelta(hours=2), amount_paise=paise(450_000))
    s = snap(active_payment_link=link.model_dump())
    assert blocked_by(run(choice(ActionType.SEND_PAYMENT_LINK, template_id="tpl.link.email.v1"), s)) == "P8"
    d = run(choice(ActionType.SEND_REMINDER), s)
    assert isinstance(d, ExecutableDecision) and d.canonical_payload.existing_link_ref == "plink_x"
    stale = dict(link.model_dump(), created_at=AS_OF - timedelta(hours=25))
    assert isinstance(run(choice(ActionType.SEND_PAYMENT_LINK, template_id="tpl.link.email.v1"), snap(active_payment_link=stale)), ExecutableDecision)


def test_p9_frequency_caps_account_and_invoice():
    assert blocked_by(run(choice(), snap(facts(contacts_7d=3)))) == "P9"
    assert blocked_by(run(choice(), snap(facts(contacts_invoice_7d={str(cand().invoice_id): 2})))) == "P9"
    assert isinstance(run(choice(), snap(facts(contacts_7d=2, contacts_invoice_7d={str(cand().invoice_id): 1}))), ExecutableDecision)
    d = run(choice(ActionType.SUPPRESS), snap(facts(contacts_7d=3)))
    assert d.canonical_payload.reason_code is SuppressReason.FREQUENCY_CAP
    # ESCALATE and dispute-details are outbound too (they contact someone)
    assert blocked_by(run(choice(ActionType.ESCALATE_TO_HUMAN), snap(facts(contacts_7d=3)))) == "P9"


def test_p10_quiet_hours_defers_to_next_window_open():
    d = run(choice(), snap(facts(as_of=SUNDAY_AS_OF)))
    assert isinstance(d, NonExecutableDecision) and d.verdict is Verdict.DEFER
    assert d.defer_until == datetime(2026, 9, 7, 9, 0, tzinfo=IST).astimezone(UTC)  # Monday 09:00 IST
    d = run(choice(), snap(facts(as_of=LATE_AS_OF)))  # 19:00 is closed (end exclusive)
    assert d.verdict is Verdict.DEFER and d.defer_until == datetime(2026, 9, 2, 9, 0, tzinfo=IST).astimezone(UTC)
    d = run(choice(ActionType.SUPPRESS), snap(facts(as_of=SUNDAY_AS_OF)))  # non-outbound is not deferred
    assert isinstance(d, ExecutableDecision)
    assert in_window(datetime(2026, 9, 1, 9, 0, tzinfo=IST), TZ, RULESET.quiet_hours)
    assert not in_window(datetime(2026, 9, 1, 8, 59, tzinfo=IST), TZ, RULESET.quiet_hours)
    assert next_window_open(datetime(2026, 9, 5, 20, 0, tzinfo=IST), TZ, RULESET.quiet_hours) == datetime(2026, 9, 7, 9, 0, tzinfo=IST).astimezone(UTC)  # Sat night → Mon


def test_p11_template_compatibility():
    assert blocked_by(run(choice(template_id="tpl.link.email.v1"))) == "P11"           # wrong action
    assert blocked_by(run(choice(channel=None))) == "P11"                              # channel missing
    assert blocked_by(run(choice(template_id="tpl.reminder.email.inactive"))) == "P11"  # inactive
    assert blocked_by(run(choice(template_id="tpl.reminder.sms.v1"))) == "P11"         # channel mismatch (EMAIL contact, SMS template)
    assert blocked_by(run(choice(template_id="tpl.nope"))) == "P11"


def test_ladder_order_first_failure_wins_and_matched_rules_recorded():
    s = snap(facts(kill_switch=True, ledger_ok=False, opt_out=True))
    d = run(choice(), s)
    assert blocked_by(d) == "P0" and d.matched_rules == ["P0"]
    d = run(choice())
    assert d.matched_rules == [f"P{i}" for i in range(15)]


# ── §4.3 truth table and I4 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("conf,action,expect", [
    (0.90, ActionType.SEND_REMINDER, ("ALLOW", ActionType.SEND_REMINDER, 1)),
    (0.90, ActionType.SEND_PAYMENT_LINK, ("ALLOW", ActionType.SEND_PAYMENT_LINK, 1)),
    (0.90, ActionType.PROPOSE_INSTALLMENT_PLAN, ("REQUIRE_APPROVAL", ActionType.PROPOSE_INSTALLMENT_PLAN, 2)),
    (0.75, ActionType.SEND_REMINDER, ("ALLOW", ActionType.SEND_REMINDER, 1)),
    (0.75, ActionType.SEND_PAYMENT_LINK, ("REQUIRE_APPROVAL", ActionType.SEND_PAYMENT_LINK, 2)),
    (0.75, ActionType.PROPOSE_INSTALLMENT_PLAN, ("REQUIRE_APPROVAL", ActionType.PROPOSE_INSTALLMENT_PLAN, 2)),
    (0.60, ActionType.SEND_REMINDER, ("ALLOW", ActionType.SUPPRESS, 0)),
    (0.60, ActionType.SEND_PAYMENT_LINK, ("ALLOW", ActionType.SUPPRESS, 0)),
    (0.60, ActionType.PROPOSE_INSTALLMENT_PLAN, ("ALLOW", ActionType.SUPPRESS, 0)),
    (0.60, ActionType.REQUEST_DISPUTE_DETAILS, ("ALLOW", ActionType.REQUEST_DISPUTE_DETAILS, 0)),
    (0.60, ActionType.SCHEDULE_FOLLOWUP, ("ALLOW", ActionType.SCHEDULE_FOLLOWUP, 0)),
])
def test_tier_cap_truth_table(conf, action, expect):
    tpl = {ActionType.SEND_PAYMENT_LINK: "tpl.link.email.v1", ActionType.PROPOSE_INSTALLMENT_PLAN: "tpl.installment.email.v1",
           ActionType.REQUEST_DISPUTE_DETAILS: "tpl.dispute.email.v1"}.get(action, "tpl.reminder.email.v1")
    d = run(choice(action, origin=L0, confidence=conf, template_id=tpl))
    assert isinstance(d, ExecutableDecision)
    assert (str(d.verdict), d.action_type, d.tier) == expect
    assert d.effective_confidence == conf and d.degradation_level is L0
    assert authority_tier(d.action_type, conf, RULESET) <= catalogue_tier(action)


def test_band_d_must_be_discarded_before_kernel():
    with pytest.raises(ContractViolation):
        run(choice(origin=L0, confidence=0.3))


def test_l1_l2_choices_carry_no_confidence_and_keep_catalogue_tier():
    with pytest.raises(ContractViolation):
        run(choice(confidence=0.9))  # L1 with confidence
    with pytest.raises(ContractViolation):
        run(choice(origin=L0))       # L0 without confidence
    d = run(choice(ActionType.PROPOSE_INSTALLMENT_PLAN, template_id="tpl.installment.email.v1"))
    assert d.verdict is Verdict.REQUIRE_APPROVAL and d.tier == 2 and d.effective_confidence is None


@settings(max_examples=400, deadline=None)
@given(st.floats(min_value=0.5, max_value=1.0), st.sampled_from(list(ActionType)))
def test_i4_authority_monotonicity_property(conf, action):
    tpl = {ActionType.SEND_PAYMENT_LINK: "tpl.link.email.v1", ActionType.PROPOSE_INSTALLMENT_PLAN: "tpl.installment.email.v1",
           ActionType.REQUEST_DISPUTE_DETAILS: "tpl.dispute.email.v1"}.get(action, "tpl.reminder.email.v1")
    d = run(choice(action, origin=L0, confidence=conf, template_id=tpl))
    if isinstance(d, ExecutableDecision):
        assert authority_tier(d.action_type, conf, RULESET) <= ACTION_TIER[action]
        assert ACTION_TIER[d.action_type] <= ACTION_TIER[action]  # never escalates beyond what was requested
        assert d.effective_confidence == conf


# ── payload derivations ──────────────────────────────────────────────────────────────────
def test_send_payment_link_amount_is_snapshot_outstanding_and_expiry_from_ruleset():
    cx = ctx()
    d = decide(choice(ActionType.SEND_PAYMENT_LINK, template_id="tpl.link.email.v1"), snap(), RULESET, cx, org_timezone=TZ)
    p = d.canonical_payload
    assert int(p.amount_paise) == 450_000 and p.expires_at == AS_OF + timedelta(hours=24)
    assert p.notes.action_id == cx.action_id and p.notes.trace_id == cx.trace_id and p.notes.invoice_id == snap().target_invoice_id


def test_installments_sum_to_outstanding_with_remainder_on_last():
    s = snap(facts(candidates=[cand(outstanding=1_000_001)]))
    d = run(choice(ActionType.PROPOSE_INSTALLMENT_PLAN, template_id="tpl.installment.email.v1"), s)
    parts = d.canonical_payload.parts
    assert [int(x.amount_paise) for x in parts] == [333_333, 333_333, 333_335]
    assert [x.due_date for x in parts] == [BDATE + timedelta(days=10), BDATE + timedelta(days=20), BDATE + timedelta(days=30)]


def test_escalation_reason_derivation_and_queue():
    d = run(choice(ActionType.ESCALATE_TO_HUMAN), snap(), ctx(rejected_ambiguous=True))
    assert d.canonical_payload.reason_code is EscalationReason.AMBIGUOUS_INTERPRETATION and d.canonical_payload.assignee_queue is AssigneeQueue.COLLECTIONS
    d = run(choice(ActionType.ESCALATE_TO_HUMAN), snap())
    assert d.canonical_payload.reason_code is EscalationReason.MANUAL_REVIEW


def test_schedule_followup_and_suppress_default_reasons():
    d = run(choice(ActionType.SCHEDULE_FOLLOWUP, followup_days=5))
    assert d.canonical_payload.followup_date == BDATE + timedelta(days=5) and d.tier == 0
    d = run(choice(ActionType.SUPPRESS))
    assert d.canonical_payload.reason_code is SuppressReason.NO_ELIGIBLE_ACTION


def test_decision_binds_policy_and_snapshot_hashes_and_is_deterministic():
    cx = ctx()
    a = decide(choice(), snap(), RULESET, cx, org_timezone=TZ)
    b = decide(choice(), snap(), RULESET, cx, org_timezone=TZ)
    assert a == b and a.policy_hash == RULESET.policy_hash and a.snapshot_hash == snap().snapshot_hash and a.decided_at == AS_OF
    assert a.kernel_version == "kernel.v1" and a.policy_version == "policy.v1"


def test_kernel_modules_are_pure_no_io_no_clock_no_randomness():
    kdir = Path(__file__).resolve().parents[2] / "src" / "baaki" / "policy" / "kernel"
    forbidden = {"sqlalchemy", "psycopg", "os", "random", "time", "socket", "httpx", "requests", "baaki.db", "baaki.ledger", "baaki.rules_agent", "baaki.agent"}
    for f in kdir.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & forbidden, (f, node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module == m or node.module.startswith(m + ".") for m in forbidden), (f, node.module)
        src = f.read_text()
        assert "datetime.now" not in src and "utcnow" not in src and "uuid4" not in src and "new_id(" not in src
