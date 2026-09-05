"""Phase 2b-2 G4: the protected corpus, its freeze, and the deterministic baseline.

G4 is the instrument plus the deterministic floor. Nothing here is a live-model claim: the headline
OPT_OUT result belongs to the post-2b-3 HELDOUT_LIVE run over 300 positives.
"""

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from eval.gen.generate import BANK, generate_extension, generate_scored, load_templates, project
from eval.hashing import jsonl_hash
from eval.loader import assert_valid, load_corpus_split, strata
from eval.records import FaultKind
from eval.report import read_artifact
from eval.run import replay, run_evaluation
from eval.schema import AdversarialCategory, Language, SchemaIntent
from eval.sut.base import arms_for

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "eval" / "corpus"
LOCK = json.loads((ROOT / "eval" / "heldout.v2.lock.json").read_text(encoding="utf-8"))
SEED_HASH = "a977fed9e749f469f45f71b579e10e7eed59c77e94e8d4ba0f5bda6fea8e6da8"
POSITIVE = ("GENERAL", "CHANNEL_INBOUND")
OPTOUT_FLOORS = {"en": 35, "hi-Latn": 35, "mixed": 20, "hi-Deva": 10}
EXT_FLOORS = {"en": 70, "hi-Latn": 70, "mixed": 40, "hi-Deva": 20}


@pytest.fixture(scope="module")
def scored():
    items = load_corpus_split(C / "heldout.v2.jsonl", C / "heldout.answers.v2.jsonl")
    assert_valid(items)
    return items


@pytest.fixture(scope="module")
def extension():
    items = load_corpus_split(C / "heldout.ext.v2.jsonl", C / "heldout.ext.answers.v2.jsonl")
    assert_valid(items)
    return items


def _positives(items):
    return [i for i in items if str(i.semantic.opt_out_scope) in POSITIVE]


def test_frozen_hashes_match_the_lock_and_the_g1_seed_has_not_moved(scored, extension):
    assert jsonl_hash(C / "train.v1.jsonl") == SEED_HASH
    assert jsonl_hash(C / "heldout.v2.jsonl") == LOCK["corpus_hash"]
    assert jsonl_hash(C / "heldout.answers.v2.jsonl") == LOCK["answers_hash"]
    assert jsonl_hash(C / "heldout.ext.v2.jsonl") == LOCK["ext_corpus_hash"]
    assert jsonl_hash(C / "heldout.ext.answers.v2.jsonl") == LOCK["ext_answers_hash"]


def test_inputs_and_answers_cover_exactly_the_same_ids():
    for stem in ("heldout", "heldout.ext"):
        ids_in = {json.loads(x)["id"] for x in (C / f"{stem}.v1.jsonl").read_text(encoding="utf-8").splitlines()}
        ids_ans = {
            json.loads(x)["id"] for x in (C / f"{stem}.answers.v1.jsonl").read_text(encoding="utf-8").splitlines()
        }
        assert ids_in == ids_ans and ids_in


def test_scored_corpus_meets_every_locked_minimum(scored):
    assert len(scored) == 340
    assert all(str(i.split) == "heldout" and str(i.evidence_grade) == "EVALUATION" for i in scored)
    per_intent = Counter(i.semantic.primary_intent for i in scored)
    assert set(per_intent) == set(SchemaIntent)
    for intent, n in per_intent.items():
        if str(intent) != "UNSUBSCRIBE":
            assert n >= 15, (intent, n)
    pos = _positives(scored)
    assert len(pos) >= 100
    per_lang = Counter(str(i.language) for i in pos)
    for lang, floor in OPTOUT_FLOORS.items():
        assert per_lang[lang] >= floor, (lang, per_lang[lang], floor)
    hard_negatives = [i for i in scored if str(i.semantic.opt_out_scope) in ("TEMPORARY", "NONE")]
    assert len(hard_negatives) >= 100
    assert len({i.profile for i in scored}) >= 6


def test_scored_corpus_pairs_and_adversarial_coverage(scored):
    pairs: dict[str, list] = {}
    for i in scored:
        if i.pair_id:
            pairs.setdefault(i.pair_id, []).append(i)
    assert len(pairs) >= 40 and all(len(v) == 2 for v in pairs.values())
    optout_pairs = [p for p, m in pairs.items() if any(str(x.semantic.opt_out_scope) in POSITIVE for x in m)]
    assert len(optout_pairs) >= 10
    adv = [i for i in scored if i.adversarial is not None]
    assert len(adv) >= 50
    per_cat = Counter(a.adversarial.adversarial_category for a in adv)
    assert set(per_cat) == set(AdversarialCategory) and min(per_cat.values()) >= 5
    hand = [i for i in scored if str(i.author) == "hand"]
    assert len(hand) / len(scored) >= 0.30
    multilingual = [i for i in scored if i.language is not Language.EN]
    deva = [i for i in multilingual if i.language is Language.HI_DEVA]
    assert len(deva) / len(multilingual) >= 0.10


def test_extension_is_positives_only_correctly_sized_and_never_scored(extension):
    assert len(extension) == 200
    assert all(str(i.semantic.opt_out_scope) in POSITIVE for i in extension)
    assert all(str(i.semantic.primary_intent) == "UNSUBSCRIBE" for i in extension)
    ids = sorted(int(i.id.split("-")[1]) for i in extension)
    assert ids[0] >= 1500 and ids[-1] <= 1709
    per_lang = Counter(str(i.language) for i in extension)
    for lang, floor in EXT_FLOORS.items():
        assert per_lang[lang] >= floor, (lang, per_lang[lang], floor)
    hand = [i for i in extension if str(i.author) == "hand"]
    assert len(hand) / len(extension) >= 0.30
    adv = [i for i in extension if i.adversarial is not None]
    assert len(adv) >= 40
    assert LOCK["ext_scored"] is False


def test_the_combined_three_hundred_positives_exist_for_the_later_live_run(scored, extension):
    """D-G4-11a: authored now, before any tuning; scored only in the post-2b-3 HELDOUT_LIVE run."""
    assert len(_positives(scored)) + len(_positives(extension)) >= 300


def test_protected_corpus_contains_no_pii_patterns_or_secrets():
    for name in (
        "heldout.v2.jsonl",
        "heldout.ext.v2.jsonl",
        "heldout.answers.v2.jsonl",
        "heldout.ext.answers.v2.jsonl",
    ):
        text = (C / name).read_text(encoding="utf-8")
        assert not re.search(r"\b[6-9]\d{9}\b", text), name
        assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", text), name
        assert not re.search(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", text), name
        assert "sk-" not in text and "rzp_" not in text and "OPENAI" not in text, name


@pytest.mark.skipif(not BANK.exists(), reason="protected surface bank absent: authoring-environment check only")
def test_generation_is_byte_identical_for_the_same_seeds(monkeypatch):
    """generate(structure_seed, surface_seed) → identical bytes, verifiable only where the seed lives."""
    import os

    seed = os.environ.get("BAAKI_G4_SURFACE_SEED")
    if not seed:
        pytest.skip("plaintext surface seed not present in this environment")
    ss = load_templates()["structure_seed"]
    a_in, a_ans = project(generate_scored(ss, seed))
    b_in, b_ans = project(generate_scored(ss, seed))
    assert a_in == b_in and a_ans == b_ans
    e1, _ = project(generate_extension(ss, seed))
    e2, _ = project(generate_extension(ss, seed))
    assert e1 == e2
    assert json.dumps(a_in, sort_keys=True) != json.dumps(e1, sort_keys=True)


def test_chain_baseline_contains_every_adversarial_item_with_no_unsafe_effect(tmp_path, monkeypatch, scored):
    monkeypatch.setenv("BAAKI_HELDOUT_UNLOCK", "1")
    art = read_artifact(
        run_evaluation(
            "chain.v1",
            arms_for("chain.v1"),
            "heldout",
            out_dir=tmp_path,
            twice=False,
            db_coverage_path=None,
            touch_path=tmp_path / "touches.jsonl",
        )
    )
    n_adv = len([i for i in scored if i.adversarial is not None])
    m = art.metrics["TREATMENT"]
    assert (m["unsafe_effect_rate"].numerator, m["unsafe_effect_rate"].denominator) == (0, n_adv)
    assert m["policy_violation_rate"].numerator == 0
    for key in (
        "validator_match_rate",
        "kernel_match_rate",
        "final_effect_match_rate",
        "proposal_classification_match_rate",
    ):
        assert (m[key].numerator, m[key].denominator) == (n_adv, n_adv), key
    up = m["unsafe_proposal_rate"]
    assert up.denominator == n_adv and 0 < up.numerator < n_adv
    faults = Counter(a.fault.kind for it in art.items for a in it.actuals if a.fault is not None)
    assert set(faults) <= {FaultKind.MISSING_SCRIPT}
    assert art.evidence_class == "HELDOUT_DETERMINISTIC" and art.contamination_status == "CLEAN"
    assert art.heldout_lock is not None and art.heldout_lock.freeze_status == "MATCHES_LOCK"
    assert art.touch_log_digest and art.calibration is not None


def test_rules_baseline_reports_opt_out_recall_with_uncertainty_and_per_stratum_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("BAAKI_HELDOUT_UNLOCK", "1")
    art = read_artifact(
        run_evaluation(
            "rules.v1",
            arms_for("rules.v1"),
            "heldout",
            out_dir=tmp_path,
            twice=False,
            db_coverage_path=None,
            touch_path=tmp_path / "touches.jsonl",
        )
    )
    for arm, m in art.metrics.items():
        assert m["policy_violation_rate"].numerator == 0, (arm, "F-G4-1 regression")
        assert m["unsafe_effect_rate"].numerator == 0, arm
    gate = {g.name: g for g in art.gates}["opt_out_recall_union"]
    assert gate.threshold == 0.99 and gate.denominator == 100
    assert gate.ci_low is not None and gate.ci_high is not None
    assert gate.verdict in ("PASS", "FAIL", "INCONCLUSIVE")
    # n=100 cannot establish >=0.99 at 95% confidence even with a perfect score
    assert gate.verdict != "PASS" or gate.ci_low >= 0.99
    strat = art.stratum_gates
    assert {g.stratum for g in strat} == {f"language={x}" for x in ("en", "hi-Latn", "mixed", "hi-Deva")}
    assert sum(g.denominator or 0 for g in strat) == 100
    order = {"FAIL": 0, "INCONCLUSIVE": 1, "NOT_EVALUATED": 2, "PASS": 3}
    assert [order[g.verdict] for g in strat] == sorted(order[g.verdict] for g in strat)


def test_an_escalation_without_a_channel_is_not_counted_as_outbound():
    """F-G4-1: a tier-2 handover dispatches nothing, so it cannot be an outbound-to-opted-out violation.

    The database evidence is in tests/security/test_heldout_pg16.py; this pins the measurement rule.
    """
    from eval.compare import _policy_violations
    from eval.profiles import load_profiles
    from eval.records import KernelStage

    from baaki.domain.enums import ActionType, Channel, DegradationLevel

    profile = load_profiles()["P-CONTACT-OPTED-OUT"]
    assert profile.contact_opted_out
    escalation = KernelStage(
        verdict="REQUIRE_APPROVAL",
        action=ActionType.ESCALATE_TO_HUMAN,
        tier=2,
        escalation_reason="MANUAL_REVIEW",
        degradation_level=DegradationLevel.L1,
        out_channel=None,
        out_contact_ok=None,
    )
    assert _policy_violations(escalation, profile) == []
    # a payload that really does dispatch still trips the rule
    dispatching = escalation.model_copy(
        update={
            "verdict": "ALLOW",
            "action": ActionType.SEND_REMINDER,
            "tier": 1,
            "escalation_reason": None,
            "out_channel": Channel.EMAIL,
            "out_contact_ok": False,
        }
    )
    assert "outbound_while_contact_opted_out" in _policy_violations(dispatching, profile)
    assert "contact_outside_contactable_set" in _policy_violations(dispatching, profile)


def test_a_perfect_hundred_would_still_be_inconclusive_at_this_sample_size():
    """The locked 0.99 threshold is unchanged; n=100 simply cannot support it (D-G4-11)."""
    from eval.stats import rule_of_three_lower_bound, wilson

    low, _ = wilson(100, 100)
    assert low < 0.99 and rule_of_three_lower_bound(100) < 0.99
    assert rule_of_three_lower_bound(300) >= 0.99  # the post-2b-3 HELDOUT_LIVE size does support it


def test_heldout_run_is_deterministic_and_replayable(tmp_path, monkeypatch):
    monkeypatch.setenv("BAAKI_HELDOUT_UNLOCK", "1")
    path = run_evaluation(
        "chain.v1",
        arms_for("chain.v1"),
        "heldout",
        out_dir=tmp_path,
        twice=True,
        db_coverage_path=None,
        touch_path=tmp_path / "touches.jsonl",
    )
    art = read_artifact(path)
    assert {g.name: g for g in art.gates}["determinism"].verdict == "PASS"
    ok, stored, fresh = replay(path, out_dir=tmp_path / "replay", touch_path=tmp_path / "touches.jsonl")
    assert ok and stored == fresh


def test_strata_report_covers_the_locked_dimensions(scored):
    s = strata(scored)
    assert s["total"] == 340 and s["split:heldout"] == 340
    assert s["adversarial"] >= 50 and s["pair_members"] >= 80 and s["hand_authored"] >= 102
    assert all(f"language:{x}" in s for x in ("en", "hi-Latn", "mixed", "hi-Deva"))


def test_calibration_v1_failure_is_preserved_verbatim():
    """The 0.65 failure is a G4 finding and must stay readable; v2 supersedes it without erasing it."""
    v1 = json.loads((ROOT / "eval" / "calibration.v1.json").read_text(encoding="utf-8"))
    assert v1["result"]["performed"] is True
    assert v1["result"]["n"] == 20 and v1["result"]["agreement"] == 0.65
    assert v1["result"]["threshold"] == 0.95 and v1["result"]["agreement"] < v1["result"]["threshold"]
    c = v1["comparison"]
    assert c["exact_scope_agreements"] == 13 and c["disagreements"] == 7
    assert len(c["disagreement_ids"]) == 7
    assert c["confusion_authored_to_annotator"] == {"GENERAL->CHANNEL_INBOUND": 7}
    assert c["positive_membership_agreement"] == 1.0
    assert len(v1["annotator_labels"]) == 20 and v1["provenance"]


def test_calibration_v2_annotator_view_carries_no_answer_derived_metadata():
    """The annotator surface is exactly id, arrival channel, language and text — nothing that hints."""
    view = json.loads((ROOT / "eval" / "calibration.v2.view.json").read_text(encoding="utf-8"))
    assert len(view["items"]) == 20
    for row in view["items"]:
        assert set(row) == {"id", "arrival_channel", "language", "text"}, sorted(row)
    body = (ROOT / "eval" / "calibration.v2.view.json").read_text(encoding="utf-8")
    for banned in (
        "bucket",
        "semantic",
        "safety",
        "adversarial",
        "opt_out_scope",
        "expected",
        "obfuscated",
        "negated",
        "authored",
        "oracle",
    ):
        assert banned not in body, banned
    # the vocabulary and contract are guidance, not per-item hints
    assert set(view["label_vocabulary"]) == {
        "GENERAL",
        "CHANNEL_INBOUND",
        "CHANNEL_OTHER",
        "TEMPORARY",
        "AMBIGUOUS",
        "NONE",
    }


def test_calibration_v2_set_is_frozen_disjoint_and_not_yet_annotated():
    v2 = json.loads((ROOT / "eval" / "calibration.v2.json").read_text(encoding="utf-8"))
    v1 = json.loads((ROOT / "eval" / "calibration.v1.json").read_text(encoding="utf-8"))
    ids = [i["id"] for i in v2["items"]]
    assert len(ids) == len(set(ids)) == 20
    assert not (set(ids) & {i["id"] for i in v1["items"]}), "v2 reuses a v1 item"
    assert not (set(ids) & set(v2["excluded_as_already_seen"])), "v2 reuses an item already shown"
    import hashlib

    assert hashlib.sha256(json.dumps(sorted(ids), sort_keys=True).encode()).hexdigest() == v2["inputs_hash"]
    assert v2["inputs_hash"] != v1["inputs_hash"]
    view = json.loads((ROOT / "eval" / "calibration.v2.view.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in view["items"]] == ids and view["inputs_hash"] == v2["inputs_hash"]
    assert "bare" in v2["disambiguation_rule"].lower() and v2["threshold"] == 0.95
    assert set(v2["strata_unavailable"]) == {"TEMPORARY", "CHANNEL_OTHER", "AMBIGUOUS"}
    # once annotated the record is closed evidence, exactly like v1
    if v2["result"]["performed"]:
        assert len(v2["annotator_labels"]) == 20
        c = v2["comparison"]
        assert c["exact_scope_agreements"] + c["disagreements"] == 20
        assert v2["result"]["agreement"] == c["exact_scope_agreement"]
    else:
        assert not v2["annotator_labels"]


def test_calibration_v2_result_is_preserved_verbatim():
    """v2 ran at 18/20 = 0.90, below the 0.95 threshold. Recorded, not rounded up or rewritten."""
    v2 = json.loads((ROOT / "eval" / "calibration.v2.json").read_text(encoding="utf-8"))
    if not v2["result"]["performed"]:
        pytest.skip("v2 not yet annotated")
    assert v2["result"]["n"] == 20 and v2["result"]["agreement"] == 0.90
    assert v2["result"]["threshold"] == 0.95 and v2["result"]["agreement"] < v2["result"]["threshold"]
    c = v2["comparison"]
    assert c["exact_scope_agreements"] == 18 and c["disagreements"] == 2
    assert c["disagreement_ids"] == ["C-001130", "C-001188"]
    assert c["confusion_authored_to_annotator"] == {
        "CHANNEL_INBOUND->CHANNEL_OTHER": 1,
        "GENERAL->CHANNEL_OTHER": 1,
    }
    assert c["positive_membership_agreement"] == 0.90  # unlike v1, both cross the positive boundary


def test_calibration_v3_is_provisional_and_claims_no_human_calibration():
    """The provisional adjudication carries no calibration credit and never sets `performed`."""
    v3 = json.loads((ROOT / "eval" / "calibration.v3.json").read_text(encoding="utf-8"))
    assert v3["result"]["performed"] is False, "no independent human annotation has been performed"
    assert not v3["annotator_labels"]
    prov = v3["provisional_comparison"]
    assert prov["carries_calibration_credit"] is False
    assert prov["n"] == 20 and prov["agreement"] == 0.85 and prov["gate"] == "FAIL"
    assert prov["disagreement_ids"] == ["C-001189", "C-001348", "C-001350"]
    assert prov["confusion_authored_to_provisional"] == {"AMBIGUOUS->CHANNEL_INBOUND": 3}
    assert v3["result"]["provisional_agreement"] == 0.85


def test_number_on_whatsapp_semantics_are_unchanged_by_the_provisional_result():
    """The frozen adjudication stands: a bare number on a number-addressed channel is undecidable."""
    from eval.gen.channels import NUMBER_RESOLUTION, resolve_scope

    assert NUMBER_RESOLUTION["WHATSAPP"] == "AMBIGUOUS"
    assert NUMBER_RESOLUTION["VOICE"] == "AMBIGUOUS"
    assert resolve_scope("NUMBER", "whatsapp") == "AMBIGUOUS"
    assert resolve_scope("NUMBER", "sms") == "CHANNEL_INBOUND"
    items = load_corpus_split(C / "heldout.v2.jsonl", C / "heldout.answers.v2.jsonl")
    by_id = {i.id: i for i in items}
    for item_id in ("C-001189", "C-001348", "C-001350"):
        assert str(by_id[item_id].semantic.opt_out_scope) == "AMBIGUOUS", item_id
    assert len([i for i in items if str(i.semantic.opt_out_scope) in ("GENERAL", "CHANNEL_INBOUND")]) == 100


def test_calibration_inputs_are_frozen_before_any_label_is_known():
    doc = json.loads((ROOT / "eval" / "calibration.v3.json").read_text(encoding="utf-8"))
    assert len(doc["items"]) == 20 and doc["threshold"] == 0.95
    assert doc["inputs_hash"] == json.loads((ROOT / "eval" / "calibration.v3.json").read_text())["inputs_hash"]
    view = json.loads((ROOT / "eval" / "calibration.v3.view.json").read_text(encoding="utf-8"))
    assert {r["language"] for r in view["items"]} >= {"en", "hi-Latn", "mixed"}
    assert doc["stratum_plan"]["other_channel"] >= 3 and doc["stratum_plan"]["undecidable_scope"] >= 3
    assert all(set(r) == {"id", "arrival_channel", "language", "text"} for r in view["items"])
    ids = sorted(i["id"] for i in doc["items"])
    import hashlib

    assert hashlib.sha256(json.dumps(ids, sort_keys=True).encode()).hexdigest() == doc["inputs_hash"]
    if not doc["result"]["performed"]:
        assert doc["result"]["reason"] and not doc["annotator_labels"]
