"""The evidence-attribution contract, stated in interp.v2 and enforced by validator check 08.

Why this file exists: the live model returned `evidence=[{"field": "message", ...}]` and check 08 rejected it
with EVIDENCE_MISSING_FOR_FIELD. The model was not at fault — `Evidence.field` is an unconstrained `str` and
interp.v1 said nothing about it. The hand-authored fixtures happened to use the right convention, so no test
ever exercised a model-chosen value. interp.v2 states the rule; these tests pin both halves of it.

The validator is NOT relaxed here. Check 07 (literal spans) and check 08 (per-claim coverage) are asserted to
behave exactly as before — the fix is that the model is now told what they require.
"""

from baaki.agent.context import INTERP_TEMPLATE_ID, load_template, template_hash
from baaki.contracts.validation_input import ValidationInput
from baaki.domain.enums import RejectionReason
from baaki.policy.schemas.interpretation_v1 import InterpretationV1
from baaki.policy.validate import validate
from tests.phase2_helpers import AS_OF, BDATE, INV1, RULESET, cand, facts, proposal

CLAIM_FIELDS = ("promised_date_raw", "promised_amount_raw", "invoice_refs", "contact_correction")
SOURCE = "We will pay 15k for INV-1 on 25 September."


# ── the prompt states the contract ───────────────────────────────────────────────────────


def test_the_active_template_id_is_interp_v2():
    assert INTERP_TEMPLATE_ID == "interp.v2"


def test_the_active_template_names_every_claim_field_and_forbids_other_values():
    body = load_template(INTERP_TEMPLATE_ID)
    for field in CLAIM_FIELDS:
        assert field in body, field
    assert '"field" is that claim field\'s name' in body
    assert '"quote" is an exact literal substring' in body
    assert '"message"' in body  # the exact wrong value the live model chose is called out


def test_the_retired_template_is_retained_and_unchanged():
    """A proposal stamped interp.v1 must stay reconstructible (§11.2), so v1 is kept, not edited."""
    v1 = load_template("interp.v1")
    assert "5. evidence quotes must be exact substrings of the message." in v1
    assert '"field" is that claim field' not in v1


def test_template_hashes_are_deterministic():
    assert template_hash(INTERP_TEMPLATE_ID) == template_hash(INTERP_TEMPLATE_ID)
    assert template_hash("interp.v1") != template_hash("interp.v2")


def test_the_claim_field_list_in_the_prompt_matches_the_schema():
    """If CLAIM_FIELDS ever changes, the prompt must change with it or check 08 silently starts rejecting."""
    assert set(InterpretationV1.CLAIM_FIELDS) == set(CLAIM_FIELDS)
    body = load_template(INTERP_TEMPLATE_ID)
    for field in InterpretationV1.CLAIM_FIELDS:
        assert field in body, field


# ── the validator still behaves exactly as before ────────────────────────────────────────


def _run(parsed):
    """Check 00 binds the proposal to its source bytes, so the helper must be given the same SOURCE."""
    f = facts(candidates=[cand(invoice_id=INV1, number="INV-1", business_date=BDATE)])
    p = proposal(parsed, source_text=SOURCE, invoice_id=None)
    return validate(ValidationInput(proposal=p, source_text=SOURCE, facts=f), RULESET, now=AS_OF)


def _interp(**kw):
    base = {"intent": "WILL_PAY_ON_DATE", "sentiment": "COOPERATIVE", "confidence": 0.9,
            "promised_date_raw": None, "promised_amount_raw": None, "invoice_refs": [],
            "contact_correction": None, "evidence": []}
    base.update(kw)
    return base


def _reasons(bundle):
    return list(bundle.result.rejection_reasons)


def test_the_live_shaped_mis_attribution_is_still_rejected():
    """The exact shape the live model produced. Nothing here is relaxed."""
    bundle = _run(_interp(promised_date_raw="25 September",
                          evidence=[{"field": "message", "quote": SOURCE}]))
    assert RejectionReason.EVIDENCE_MISSING_FOR_FIELD in _reasons(bundle)


def test_correct_attribution_clears_check_08():
    bundle = _run(_interp(promised_date_raw="25 September",
                          evidence=[{"field": "promised_date_raw", "quote": "25 September"}]))
    assert RejectionReason.EVIDENCE_MISSING_FOR_FIELD not in _reasons(bundle)


def test_a_non_literal_quote_is_still_rejected_by_check_07():
    """Check 07 is untouched: attribution does not buy you an invented quote."""
    bundle = _run(_interp(promised_date_raw="25 September",
                          evidence=[{"field": "promised_date_raw", "quote": "the twenty fifth"}]))
    assert RejectionReason.EVIDENCE_NOT_FOUND_IN_SOURCE in _reasons(bundle)


def test_every_populated_claim_field_still_needs_its_own_entry():
    bundle = _run(_interp(promised_date_raw="25 September", promised_amount_raw="15k",
                          evidence=[{"field": "promised_date_raw", "quote": "25 September"}]))
    assert RejectionReason.EVIDENCE_MISSING_FOR_FIELD in _reasons(bundle)


def test_an_unpopulated_claim_field_needs_no_evidence():
    bundle = _run(_interp(evidence=[]))
    assert RejectionReason.EVIDENCE_MISSING_FOR_FIELD not in _reasons(bundle)
