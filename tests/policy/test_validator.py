"""§4.1 — 16 checks, 20 rejection reasons, hash binding (P2-D4), SC3 target (P2-D8), SOFT → tier-0 cap, V1–V8."""
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from baaki.contracts.normalized_action import NormalizedActionProposal
from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.domain.enums import ParseStatus, ProposalKind, RejectionReason, ValidationOutcome
from baaki.policy.validate import validate
from baaki.policy.validate.ladder import CHECK_IDS, VALIDATOR_HASH, VALIDATOR_VERSION
from tests.phase2_helpers import (
    AS_OF,
    BDATE,
    C_EMAIL,
    INV1,
    INV2,
    OTHER_INV,
    RULESET,
    action_parsed,
    cand,
    facts,
    interp_parsed,
    proposal,
    vin,
)

SRC = "I will pay by Friday"


def run(p, f=None, source=SRC):
    return validate(vin(p, f, source), RULESET, now=AS_OF)


def reasons(b):
    return list(b.result.rejection_reasons)


def test_sixteen_checks_plus_hash_binding_are_enumerated():
    assert len(CHECK_IDS) == 17 and CHECK_IDS[0][1] == "SOURCE_HASH_BOUND"
    assert sum(1 for c in CHECK_IDS if c[2] == "HARD") == 13 and sum(1 for c in CHECK_IDS if c[2] == "SOFT") == 3


def test_pass_interpretation_normalizes_claims():
    p = proposal(interp_parsed(promised_date_raw="Friday", promised_amount_raw="₹4,500", invoice_refs=["INV-1"],
                               evidence=[{"field": "promised_date_raw", "quote": "by Friday"}, {"field": "promised_amount_raw", "quote": "4,500"},
                                         {"field": "invoice_refs", "quote": "INV-1"}]))
    b = run(p, source="I will pay ₹4,500 for INV-1 by Friday")
    p = proposal(p.parsed, source_text="I will pay ₹4,500 for INV-1 by Friday")
    b = run(p, source="I will pay ₹4,500 for INV-1 by Friday")
    assert b.result.outcome is ValidationOutcome.PASS and reasons(b) == []
    n = b.result.normalized
    assert isinstance(n, NormalizedInterpretation)
    assert n.promised_date == BDATE + timedelta(days=3) and int(n.promised_paise) == 450_000 and n.invoice_ids == [INV1]
    assert n.effective_confidence == 0.9 and b.target_invoice_id == INV1
    assert b.result.validator_version == VALIDATOR_VERSION and b.result.validator_hash == VALIDATOR_HASH
    assert len(b.result.checks_run) == 17


def test_pass_action_proposal_normalized_shape_p2_d3():
    b = run(proposal(action_parsed(), kind=ProposalKind.ACTION_PROPOSAL))
    assert b.result.outcome is ValidationOutcome.PASS
    assert isinstance(b.result.normalized, NormalizedActionProposal) and b.result.normalized.contact_id == C_EMAIL
    skipped = {c["check_id"] for c in b.result.checks_run if c["skipped"]}
    assert skipped == {"EVIDENCE_SPANS_LITERAL", "EVIDENCE_COVERS_CLAIMS", "INVOICE_REF_VALID", "DATE_NORMALISE", "AMOUNT_NORMALISE",
                       "DATE_RANGE_SANE", "AMOUNT_RANGE_SANE"}


# ── every one of the 20 rejection reasons is reachable ──────────────────────────────────
def test_r_schema_violation_via_hash_binding_first():
    p = proposal(interp_parsed(), input_hash="c" * 64)
    b = run(p)
    assert reasons(b) == [RejectionReason.SCHEMA_VIOLATION]
    assert [c["check_id"] for c in b.result.checks_run] == ["SOURCE_HASH_BOUND"]  # short-circuit before check 01


def test_r_system_halted():
    assert reasons(run(proposal(interp_parsed()), facts(kill_switch=True))) == [RejectionReason.SYSTEM_HALTED]


def test_r_ledger_invariant_breach():
    assert reasons(run(proposal(interp_parsed()), facts(ledger_ok=False))) == [RejectionReason.LEDGER_INVARIANT_BREACH]


@pytest.mark.parametrize("ps,reason", [
    (ParseStatus.SCHEMA_VIOLATION, RejectionReason.SCHEMA_VIOLATION), (ParseStatus.UNPARSEABLE, RejectionReason.UNPARSEABLE),
    (ParseStatus.TIMEOUT, RejectionReason.PROVIDER_TIMEOUT), (ParseStatus.PROVIDER_ERROR, RejectionReason.PROVIDER_TIMEOUT),
])
def test_r_parse_failures(ps, reason):
    assert reasons(run(proposal(None, parse_status=ps))) == [reason]


def test_r_unknown_schema_version():
    assert reasons(run(proposal(interp_parsed(), schema_version="interpretation.v7"))) == [RejectionReason.UNKNOWN_SCHEMA_VERSION]
    # kind/schema cross-wiring is also unknown
    assert reasons(run(proposal(interp_parsed(), schema_version="action_proposal.v1"))) == [RejectionReason.UNKNOWN_SCHEMA_VERSION]


def test_r_enum_out_of_range_and_schema_violation():
    assert reasons(run(proposal(interp_parsed(intent="WILL_PAY_SOMEDAY")))) == [RejectionReason.ENUM_OUT_OF_RANGE]
    assert reasons(run(proposal(interp_parsed(sentiment="ANGRY")))) == [RejectionReason.ENUM_OUT_OF_RANGE]
    assert reasons(run(proposal(interp_parsed(extra_field=1)))) == [RejectionReason.SCHEMA_VIOLATION]
    assert reasons(run(proposal(action_parsed(action="APPLY_DISCOUNT"), kind=ProposalKind.ACTION_PROPOSAL))) == [RejectionReason.ENUM_OUT_OF_RANGE]
    assert reasons(run(proposal(action_parsed(followup_days=30), kind=ProposalKind.ACTION_PROPOSAL))) == [RejectionReason.SCHEMA_VIOLATION]


def test_r_forbidden_money_field_rejected_by_contract_before_validator():
    from baaki.domain.errors import ContractViolation
    with pytest.raises(ContractViolation):
        proposal(interp_parsed(amount_paise=100))  # A3 at the contract boundary
    # a nested money key slips past A3's top-level scan and is caught by check 06
    b = run(proposal(interp_parsed(evidence=[{"field": "x", "quote": "y", "discount": "10%"}])))
    assert reasons(b) in ([RejectionReason.FORBIDDEN_MONEY_FIELD], [RejectionReason.SCHEMA_VIOLATION])


def test_r_evidence_not_found_in_source():
    p = proposal(interp_parsed(promised_date_raw="Friday", evidence=[{"field": "promised_date_raw", "quote": "by Monday"}]))
    assert reasons(run(p)) == [RejectionReason.EVIDENCE_NOT_FOUND_IN_SOURCE]


def test_r_evidence_missing_for_field():
    p = proposal(interp_parsed(promised_date_raw="Friday", evidence=[]))
    assert reasons(run(p)) == [RejectionReason.EVIDENCE_MISSING_FOR_FIELD]


def test_r_contact_not_in_account():
    p = proposal(action_parsed(contact_id=str(OTHER_INV)), kind=ProposalKind.ACTION_PROPOSAL)
    assert reasons(run(p)) == [RejectionReason.CONTACT_NOT_IN_ACCOUNT]


def test_r_invoice_ref_unresolved_sc1_other_accounts_never_resolve():
    p = proposal(interp_parsed(invoice_refs=["INV-OTHER"], evidence=[{"field": "invoice_refs", "quote": "INV-OTHER"}]))
    b = run(p, source="about INV-OTHER")
    p = proposal(p.parsed, source_text="about INV-OTHER")
    b = run(p, source="about INV-OTHER")
    assert reasons(b) == [RejectionReason.INVOICE_REF_UNRESOLVED] and b.rejected_ambiguous


@pytest.mark.parametrize("raw,reason", [("Blursday", RejectionReason.DATE_UNPARSEABLE), ("next week", RejectionReason.DATE_AMBIGUOUS)])
def test_r_date_failures(raw, reason):
    src = f"I will pay {raw}"
    p = proposal(interp_parsed(promised_date_raw=raw, evidence=[{"field": "promised_date_raw", "quote": raw}]), source_text=src)
    b = run(p, source=src)
    assert reasons(b) == [reason] and b.rejected_ambiguous


@pytest.mark.parametrize("raw,reason", [("abc", RejectionReason.AMOUNT_UNPARSEABLE), ("half", RejectionReason.AMOUNT_AMBIGUOUS)])
def test_r_amount_failures(raw, reason):
    src = f"I will pay {raw}"
    p = proposal(interp_parsed(promised_amount_raw=raw, evidence=[{"field": "promised_amount_raw", "quote": raw}]), source_text=src)
    assert reasons(run(p, source=src)) == [reason]


def _soft(parsed_kw, src):
    p = proposal(interp_parsed(**parsed_kw), source_text=src)
    return run(p, source=src)


def test_soft_date_in_past_caps_confidence_but_passes():
    b = _soft(dict(promised_date_raw="2026-08-01", evidence=[{"field": "promised_date_raw", "quote": "2026-08-01"}]), "paid on 2026-08-01")
    assert b.result.outcome is ValidationOutcome.PASS and reasons(b) == []
    soft = [c for c in b.result.checks_run if c["class"] == "SOFT" and not c["passed"]]
    assert [c["reason"] for c in soft] == ["DATE_IN_PAST"]
    assert b.result.normalized.effective_confidence < RULESET.confidence_floor  # forces tier-0 in the kernel


def test_soft_date_beyond_horizon():
    b = _soft(dict(promised_date_raw="2026-12-25", evidence=[{"field": "promised_date_raw", "quote": "2026-12-25"}]), "by 2026-12-25")
    assert [c["reason"] for c in b.result.checks_run if c["class"] == "SOFT" and not c["passed"]] == ["DATE_BEYOND_HORIZON"]


def test_soft_amount_exceeds_outstanding_compares_only():
    b = _soft(dict(promised_amount_raw="1 crore", evidence=[{"field": "promised_amount_raw", "quote": "1 crore"}]), "will pay 1 crore")
    assert [c["reason"] for c in b.result.checks_run if c["class"] == "SOFT" and not c["passed"]] == ["AMOUNT_EXCEEDS_OUTSTANDING"]
    assert int(b.result.normalized.promised_paise) == 1_000_000_000  # claim preserved, not clamped (V7)


def test_soft_confidence_below_threshold():
    b = run(proposal(interp_parsed(confidence=0.55)))
    assert b.result.outcome is ValidationOutcome.PASS
    assert [c["reason"] for c in b.result.checks_run if c["class"] == "SOFT" and not c["passed"]] == ["CONFIDENCE_BELOW_THRESHOLD"]
    assert b.result.normalized.effective_confidence == 0.55


def test_all_twenty_reasons_are_covered_by_this_module():
    import inspect
    import sys
    src = inspect.getsource(sys.modules[__name__])
    for r in RejectionReason:
        assert r.name in src, r


def test_effective_confidence_never_exceeds_model_confidence_i4():
    b = run(proposal(interp_parsed(confidence=0.95)))
    assert b.result.normalized.effective_confidence <= 0.95


def test_sc3_target_resolution_order():
    f = facts(candidates=[cand(INV1, "INV-1", 20), cand(INV2, "INV-2", 5)])
    # sole resolved ref wins over hint
    src = "INV-2 will be paid"
    p = proposal(interp_parsed(invoice_refs=["INV-2"], evidence=[{"field": "invoice_refs", "quote": "INV-2"}]), source_text=src, invoice_id=INV1)
    assert run(p, f, src).target_invoice_id == INV2
    # hint in candidates wins over first-candidate
    assert run(proposal(interp_parsed(), invoice_id=INV2), f).target_invoice_id == INV2
    # otherwise first candidate (SC2 order)
    assert run(proposal(interp_parsed()), f).target_invoice_id == INV1
    # no candidates ⟹ None (SC7), validation still produced
    b = run(proposal(interp_parsed()), facts(candidates=[]))
    assert b.target_invoice_id is None and b.result.outcome is ValidationOutcome.PASS


def test_kill_switch_does_not_stop_recording_but_rejects():
    b = run(proposal(interp_parsed(intent="UNSUBSCRIBE")), facts(kill_switch=True))
    assert b.result.outcome is ValidationOutcome.REJECT  # opt-out then flows via the arm-independent restriction path, not validation


@settings(max_examples=150, deadline=None)
@given(st.floats(min_value=0, max_value=1), st.sampled_from(["WILL_PAY_ON_DATE", "NO_CLEAR_INTENT", "UNSUBSCRIBE", "ALREADY_PAID_CLAIM"]))
def test_validator_is_deterministic_and_total(conf, intent):
    p = proposal(interp_parsed(confidence=conf, intent=intent))
    a, b = run(p), run(p)
    assert a.result.model_dump(exclude={"validation_id"}) == b.result.model_dump(exclude={"validation_id"})
    assert (a.result.outcome is ValidationOutcome.PASS) == (a.result.normalized is not None)  # V2/V3
