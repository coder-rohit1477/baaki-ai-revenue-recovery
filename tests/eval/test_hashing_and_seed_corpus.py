"""Reproducibility scaffolding (D-2b2-8) and the G1 bootstrap seed corpus (BOOTSTRAP grade — never evidence)."""
import json
from pathlib import Path

from eval.hashing import FREEZE_FILES, canonical_json, config_hash, freeze_hash, freeze_manifest, jsonl_hash
from eval.loader import assert_valid, load_corpus, strata
from eval.schema import CORPUS_VERSION, EvidenceGrade, Split

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "eval" / "corpus" / "train.v1.jsonl"


def test_canonical_hashing_is_whitespace_insensitive_and_content_sensitive(tmp_path):
    a = tmp_path / "a.jsonl"; b = tmp_path / "b.jsonl"; c = tmp_path / "c.jsonl"
    a.write_text('{"x": 1, "y": [1, 2]}\n{"z": "q"}\n')
    b.write_text('{"y":[1,2],"x":1}\n\n{"z":"q"}\n')
    c.write_text('{"y":[1,2],"x":2}\n{"z":"q"}\n')
    assert jsonl_hash(a) == jsonl_hash(b) != jsonl_hash(c)
    assert canonical_json({"b": 1, "a": [{"d": 1, "c": 2}]}) == '{"a":[{"c":2,"d":1}],"b":1}'


def test_freeze_manifest_covers_the_implementation_and_oracle_artefacts_and_is_deterministic():
    m1, m2 = freeze_manifest(), freeze_manifest()
    assert m1 == m2 and set(m1) == set(FREEZE_FILES)
    assert {"src/baaki/rules_agent/interpreter.py", "src/baaki/rules_agent/restriction.py", "src/baaki/policy/validate/normalize.py",
            "src/baaki/rules_agent/tree.py", "eval/safety_policy.v1.json", "eval/enr.py"} <= set(m1)
    assert freeze_hash(m1) == freeze_hash(m2) and len(freeze_hash(m1)) == 64
    assert len(config_hash(ROOT / "eval" / "config.v1.toml")) == 64


def test_seed_corpus_validates_and_is_labelled_bootstrap_only():
    items = load_corpus(SEED)
    assert_valid(items)
    assert len(items) == 41  # 40-item bootstrap seed + one Hinglish DISPUTE_AMOUNT so every intent has >= 2 items
    assert all(i.evidence_grade is EvidenceGrade.BOOTSTRAP and i.split is Split.TRAIN and i.corpus_version == CORPUS_VERSION for i in items)
    s = strata(items)
    assert all(s[f"intent:{k}"] >= 2 for k in ("WILL_PAY_ON_DATE", "REQUEST_INSTALLMENTS", "DISPUTE_AMOUNT", "DISPUTE_DELIVERY", "ALREADY_PAID_CLAIM",
                                                  "WRONG_CONTACT", "NEEDS_DOCUMENT", "UNSUBSCRIBE", "NO_CLEAR_INTENT"))
    assert s["scope:GENERAL"] >= 3 and s["scope:CHANNEL_INBOUND"] >= 1 and s["scope:CHANNEL_OTHER"] >= 1 and s["scope:TEMPORARY"] >= 1 and s["scope:AMBIGUOUS"] >= 1
    assert s["pair_members"] >= 8 and s["adversarial"] >= 4 and s["language:hi-Latn"] >= 6 and s["language:hi-Deva"] >= 1
    assert jsonl_hash(SEED) == jsonl_hash(SEED)


def test_seed_corpus_contains_no_pii_patterns_or_secrets():
    text = SEED.read_text(encoding="utf-8")
    import re
    assert not re.search(r"\b[6-9]\d{9}\b", text)  # Indian mobile numbers
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", text)  # e-mail addresses
    assert not re.search(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", text)  # PAN-like
    assert "sk-" not in text and "rzp_" not in text


def test_seed_items_never_claim_evaluation_evidence():
    for line in SEED.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        assert obj["evidence_grade"] == "BOOTSTRAP"
