"""D-2b2-G2-2 SUT × arm matrix, SutArmIncompatible, record contracts, arm blindness of EXPECTED."""
from pathlib import Path

import pytest
from eval.compare import build_expected
from eval.loader import load_corpus
from eval.profiles import load_profiles, to_account_facts
from eval.records import ActualRecord, ExpectedRecord
from eval.sut.base import (
    CHAIN_SUT,
    RULES_SUT,
    VALID_CELLS,
    SutArmIncompatible,
    SutInputs,
    arms_for,
    check_compatible,
    sut_version,
)
from eval.sut.chain import ChainSut
from eval.sut.rules import RulesSut

from baaki.domain.enums import Arm
from baaki.policy.ruleset import DEFAULT_RULESET_PATH, load_ruleset

ROOT = Path(__file__).resolve().parents[2]
ITEMS = load_corpus(ROOT / "eval" / "corpus" / "train.v1.jsonl")
P = load_profiles()
RULESET = load_ruleset(DEFAULT_RULESET_PATH)


def test_matrix_is_exactly_the_locked_one():
    assert VALID_CELLS == {(RULES_SUT, Arm.CONTROL), (RULES_SUT, Arm.RULES_ONLY), (CHAIN_SUT, Arm.TREATMENT)}
    assert arms_for(RULES_SUT) == [Arm.CONTROL, Arm.RULES_ONLY] and arms_for(CHAIN_SUT) == [Arm.TREATMENT]


@pytest.mark.parametrize("sut_id,arm", [(RULES_SUT, Arm.TREATMENT), (CHAIN_SUT, Arm.CONTROL), (CHAIN_SUT, Arm.RULES_ONLY)])
def test_invalid_cells_fail_deterministically_before_any_stage(sut_id, arm):
    with pytest.raises(SutArmIncompatible) as ei:
        check_compatible(sut_id, arm)
    assert (ei.value.sut_id, ei.value.arm) == (sut_id, arm)
    driver = RulesSut() if sut_id == RULES_SUT else ChainSut()
    facts = to_account_facts(P["P-OVERDUE-15"])
    with pytest.raises(SutArmIncompatible):
        driver.run_item(SutInputs(item_id="C-000001", text="x", anchor=facts.business_date, scripted_output={"intent": "NO_CLEAR_INTENT"}), facts, arm, RULESET)


def test_valid_cells_execute():
    facts = to_account_facts(P["P-OVERDUE-15"])
    r = RulesSut()
    for arm in (Arm.CONTROL, Arm.RULES_ONLY):
        a = r.run_item(SutInputs(item_id="C-000001", text="We will pay by Friday.", anchor=facts.business_date), facts, arm, RULESET)
        assert isinstance(a, ActualRecord) and a.arm is arm and a.sut_id == RULES_SUT and a.fault is None and a.kernel is not None
    c = ChainSut()
    a = c.run_item(SutInputs(item_id="C-000001", text="x", anchor=facts.business_date, scripted_output={"intent": "NO_CLEAR_INTENT", "promised_date_raw": None, "promised_amount_raw": None, "invoice_refs": [], "contact_correction": None, "sentiment": "NEUTRAL", "confidence": 0.9, "evidence": []}), facts, Arm.TREATMENT, RULESET)
    assert a.sut_id == CHAIN_SUT and a.fault is None and a.validator is not None and a.kernel is not None


def test_sut_version_is_a_hash_of_production_files_and_stable():
    assert sut_version(RULES_SUT) == sut_version(RULES_SUT) and len(sut_version(CHAIN_SUT)) == 64 and sut_version(RULES_SUT) != sut_version(CHAIN_SUT)


def test_expected_record_is_arm_blind_and_actual_is_oracle_blind():
    assert "arm" not in ExpectedRecord.model_fields and "sut_id" not in ExpectedRecord.model_fields
    for forbidden in ("semantic", "safety", "expected", "oracle", "adversarial"):
        assert forbidden not in ActualRecord.model_fields, forbidden
    exp = build_expected(ITEMS[0], {})
    assert exp.item_id == ITEMS[0].id and exp.semantic == ITEMS[0].semantic and exp.safety == ITEMS[0].safety


def test_oracle_and_compare_never_import_sut_or_production_decision_code():
    import ast
    for name in ("oracle.py", "compare.py", "metrics.py", "stats.py", "records.py", "report.py"):
        tree = ast.parse((ROOT / "eval" / name).read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert not n.module.startswith(("eval.sut", "baaki.policy", "baaki.rules_agent", "baaki.agent", "baaki.providers", "baaki.pipeline", "baaki.db")), (name, n.module)
