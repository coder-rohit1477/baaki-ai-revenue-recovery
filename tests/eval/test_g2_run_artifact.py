"""CLI, artefact schema, determinism (two runs → equal hashes), live refusal, invariant probes, gates."""
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from eval.hashing import jsonl_hash
from eval.report import actuals_hash, read_artifact
from eval.run import LIVE_REFUSED, main, run_evaluation
from eval.sut.base import SutArmIncompatible
from eval.sut.probes import money_in_prompt, provider_schema_closure

from baaki.domain.enums import Arm
from tests.conftest import _guarded_connect

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "eval" / "corpus" / "train.v1.jsonl"
SEED_HASH_G1 = "a977fed9e749f469f45f71b579e10e7eed59c77e94e8d4ba0f5bda6fea8e6da8"


def test_g1_seed_corpus_hash_is_unchanged_by_g2():
    assert jsonl_hash(SEED) == SEED_HASH_G1


def test_rules_run_produces_a_valid_bootstrap_artifact(tmp_path):
    path = run_evaluation("rules.v1", None, "train", out_dir=tmp_path)
    art = read_artifact(path)
    assert art.status == "COMPLETE" and art.banner.startswith("BOOTSTRAP") and art.run.evidence_grade.value == "BOOTSTRAP"
    assert art.run.arms == [Arm.CONTROL, Arm.RULES_ONLY] and art.run.sut_id == "rules.v1" and len(art.items) == 41
    assert all(len(i.actuals) == 2 and len(i.comparisons) == 2 for i in art.items)
    assert art.primary_arm == "RULES_ONLY" and set(art.metrics) == {"CONTROL", "RULES_ONLY"}
    names = set(art.metrics["RULES_ONLY"])
    for req in ("intent_accuracy_9", "correct_substantive_rate", "false_substantive_interpretation_rate", "missed_interpretation_rate", "fault_share_sub",
                "correct_abstention_rate", "false_positive_interpretation_rate", "fault_share_nci", "optout_recall_union", "optout_false_positive_rate_detector",
                "ambiguous_conservative_review_rate", "policy_gap_count", "gap_exposure_count", "gap_unmeasured_count", "stopping_rule_accuracy",
                "false_escalation_rate", "policy_violation_rate", "unsafe_effect_rate", "ptp_date_match", "minimal_pair_accuracy", "pair_flip_rate", "wrong_contact_recall"):
        assert req in names, req
    assert "false_interpretation_rate" not in names  # withdrawn definition must not reappear
    g = {x.name: x for x in art.gates}
    assert g["policy_violation_rate"].verdict == "PASS" and g["provider_schema_closure"].verdict == "PASS" and g["money_in_prompt"].verdict == "PASS"
    assert g["determinism"].verdict == "PASS" and g["evaluation_schema_validation"].verdict == "PASS" and g["corpus_schema_validation"].verdict == "PASS"
    assert g["opt_out_recall_union"].verdict == "NOT_EVALUATED" and "heldout/regression" in g["opt_out_recall_union"].reason  # locked gate is not judged on train
    assert all(x.verdict == "NOT_EVALUATED" for x in art.gates if x.status == "CANDIDATE")
    assert art.metrics["RULES_ONLY"]["policy_gap_count"].numerator == 1 and art.metrics["RULES_ONLY"]["gap_unmeasured_count"].numerator == 1
    assert art.not_available_offline["live_sut"] == "Phase 2b-3" and art.evaluation_schema_validation.rate == 1.0
    assert set(art.strata["RULES_ONLY"]) == {"language", "intent", "adversarial_category", "pair_feature", "optout_scope"} and set(art.strata["RULES_ONLY"]["language"]) == {"en", "hi-Latn", "hi-Deva"}


def test_chain_run_has_zero_unsafe_effect_and_records_missing_scripts_as_faults(tmp_path):
    path = run_evaluation("chain.v1", None, "train", out_dir=tmp_path)
    art = read_artifact(path)
    assert art.run.arms == [Arm.TREATMENT]
    m = art.metrics["TREATMENT"]
    assert (m["unsafe_effect_rate"].numerator, m["unsafe_effect_rate"].denominator) == (0, 5)
    # classify.v1 finds 3 of the 5 seed attacks outside the contract; the two text-level deceptions (C-000036 fooled paid-claim,
    # C-000040 zero-width STOP) are schema-valid proposals and are measured as interpretation errors instead. The authored
    # seed labels say UNSAFE for those two — a documented bootstrap-corpus disagreement, visible in proposal_classification_match_rate.
    assert m["unsafe_proposal_rate"].numerator == 3 and m["proposal_classification_match_rate"].numerator == 3
    assert m["policy_violation_rate"].numerator == 0
    assert m["fault_rate"].numerator == 36  # 41 items − 5 with a scripted output
    g = {x.name: x for x in art.gates}
    assert g["unsafe_effect_rate"].verdict == "PASS" and g["policy_violation_rate"].verdict == "PASS" and g["determinism"].verdict == "PASS"


def test_two_separate_runs_are_byte_identical_modulo_time_and_latency(tmp_path):
    p1 = run_evaluation("rules.v1", None, "train", out_dir=tmp_path / "a", twice=False)
    p2 = run_evaluation("rules.v1", None, "train", out_dir=tmp_path / "b", twice=False)
    a1, a2 = read_artifact(p1), read_artifact(p2)
    # Determinism invariant: comparison_hash, actuals_hash (latency excluded) and run_id are identical across independent runs.
    assert a1.comparison_hash == a2.comparison_hash and a1.run.run_id == a2.run.run_id
    assert actuals_hash([a for it in a1.items for a in it.actuals]) == actuals_hash([a for it in a2.items for a in it.actuals])
    # The ONLY fields permitted to differ are the documented volatile ones: created_at_utc and each actual's latency record.
    d1, d2 = a1.model_dump(mode="json"), a2.model_dump(mode="json")
    volatile_seen = set()
    for d in (d1, d2):
        volatile_seen.add("created_at_utc"); d.pop("created_at_utc")
        for it in d["items"]:
            for a in it["actuals"]:
                volatile_seen.add("items[].actuals[].latency"); a.pop("latency")
    assert d1 == d2 and volatile_seen == {"created_at_utc", "items[].actuals[].latency"}
    assert p1.name == p2.name  # run id drives the file name


def test_invalid_arm_for_sut_is_refused_before_running(tmp_path):
    with pytest.raises(SutArmIncompatible):
        run_evaluation("rules.v1", [Arm.TREATMENT], "train", out_dir=tmp_path)
    with pytest.raises(SutArmIncompatible):
        run_evaluation("chain.v1", [Arm.CONTROL], "train", out_dir=tmp_path)
    assert not any(tmp_path.iterdir()) if tmp_path.exists() else True
    assert main(["--sut", "rules.v1", "--arm", "treatment", "--split", "train", "--out", str(tmp_path)]) == 3


def test_live_sut_is_refused(tmp_path, capsys):
    assert main(["--sut", "live", "--out", str(tmp_path)]) == 2
    assert LIVE_REFUSED in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run_evaluation("live", None, "train", out_dir=tmp_path)


def test_cli_module_entry_point_writes_an_artifact(tmp_path):
    r = subprocess.run([sys.executable, "-m", "eval.run", "--sut", "rules.v1", "--split", "train", "--once", "--out", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    out = Path(r.stdout.strip())
    assert out.exists() and json.loads(out.read_text())["status"] == "COMPLETE"


def test_probes_provider_schema_closure_and_money_in_prompt():
    from eval.loader import load_corpus
    from eval.profiles import load_profiles, to_account_facts
    assert provider_schema_closure() == (2, 2)
    items = load_corpus(SEED)
    facts = {pid: to_account_facts(s) for pid, s in load_profiles().items()}
    hits, prompts = money_in_prompt(items, facts)
    assert (hits, prompts) == (0, 2 * len(items))


def test_run_opens_no_network_socket(tmp_path, monkeypatch):
    assert socket.socket.connect is _guarded_connect
    opened = []
    real = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (opened.append(addr), real(self, addr))[1])
    run_evaluation("chain.v1", None, "train", out_dir=tmp_path, twice=False)
    assert opened == []
