"""Phase 2b-2 G4: the protected split must be disjoint from everything the tuning phase can see.

Violations are listed by identifier pair for review; nothing is silently trimmed or replaced.
"""

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from eval.gen.generate import BANK
from eval.loader import load_corpus, load_corpus_split

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "eval" / "corpus"
JACCARD_MAX = 0.60
COSINE_MAX = 0.80
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍­‮‭﻿"), None)


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.translate(_ZERO_WIDTH).lower()).split()


def _key(text: str) -> str:
    return " ".join(_tokens(text))


def _grams(text: str) -> Counter[str]:
    s = _key(text)
    return Counter(s[i : i + 5] for i in range(max(0, len(s) - 4)))


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    num = sum(v * b.get(k, 0) for k, v in a.items())
    da = sum(v * v for v in a.values()) ** 0.5
    db = sum(v * v for v in b.values()) ** 0.5
    return num / (da * db) if da and db else 0.0


@pytest.fixture(scope="module")
def corpora():
    return {
        "scored": load_corpus_split(C / "heldout.v2.jsonl", C / "heldout.answers.v2.jsonl"),
        "extension": load_corpus_split(C / "heldout.ext.v2.jsonl", C / "heldout.ext.answers.v2.jsonl"),
        "train": load_corpus(C / "train.v1.jsonl"),
        "regression": load_corpus(C / "regression.v1.jsonl"),
    }


def test_id_ranges_are_disjoint_from_every_existing_corpus(corpora):
    """Computed from the corpora themselves, never from a hard-coded assumption."""
    ids = {name: {i.id for i in items} for name, items in corpora.items()}
    names = sorted(ids)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not (ids[a] & ids[b]), (a, b, sorted(ids[a] & ids[b])[:5])
    scored = sorted(int(x.split("-")[1]) for x in ids["scored"])
    ext = sorted(int(x.split("-")[1]) for x in ids["extension"])
    assert scored[0] >= 1000 and scored[-1] <= 1351
    assert ext[0] >= 1500 and ext[-1] <= 1709
    public = sorted(int(x.split("-")[1]) for x in ids["train"] | ids["regression"])
    assert public[-1] < 1000, "a public corpus entered the protected id range"


def test_normalized_text_is_disjoint_across_corpora_and_unique_within_each(corpora):
    keys = {}
    for name, items in corpora.items():
        k = [_key(i.text) for i in items]
        if name in ("scored", "extension"):
            dupes = [x for x, c in Counter(k).items() if c > 1]
            assert not dupes, f"{name} repeats {len(dupes)} normalized text(s)"
        keys[name] = set(k)
    protected = keys["scored"] | keys["extension"]
    public = keys["train"] | keys["regression"]
    assert not (protected & public), sorted(protected & public)[:3]
    assert not (keys["scored"] & keys["extension"])


def _violations(a_items, b_items):
    b_prepared = [(i.id, _tokens(i.text), _grams(i.text)) for i in b_items]
    out = []
    for a in a_items:
        ta, ga = _tokens(a.text), _grams(a.text)
        for bid, tb, gb in b_prepared:
            j, c = _jaccard(ta, tb), _cosine(ga, gb)
            if j >= JACCARD_MAX or c >= COSINE_MAX:
                out.append((a.id, bid, round(j, 3), round(c, 3)))
    return out


def test_protected_items_are_not_near_duplicates_of_any_public_item(corpora):
    public = corpora["train"] + corpora["regression"]
    viol = _violations(corpora["scored"], public) + _violations(corpora["extension"], public)
    assert not viol, f"{len(viol)} near-duplicate pair(s): {viol[:6]}"


def test_the_extension_is_not_a_paraphrase_of_the_scored_corpus(corpora):
    """The final 300 positives must be 300 distinct items, not 100 plus 200 restatements."""
    viol = _violations(corpora["extension"], corpora["scored"])
    assert not viol, f"{len(viol)} near-duplicate pair(s): {viol[:6]}"


@pytest.mark.skipif(not BANK.exists(), reason="protected surface bank absent: authoring-environment check only")
def test_phrase_bank_is_disjoint_from_every_public_split(corpora):
    bank = json.loads(BANK.read_text(encoding="utf-8"))
    phrases = set()
    for group in ("optout_general", "reason", "context"):
        for lang_pool in bank[group].values():
            phrases |= {p for p in lang_pool if len(_tokens(p)) >= 3}
    public = " || ".join(_key(i.text) for i in corpora["train"] + corpora["regression"])
    hits = sorted(p for p in phrases if _key(p) and _key(p) in public)
    assert not hits, f"{len(hits)} bank phrase(s) already appear in a public split"


def test_profiles_are_deliberately_shared_but_texts_are_not(corpora):
    """The profiles are the world model, not the test: sharing them is intended, sharing text is not."""
    protected = {i.profile for i in corpora["scored"]}
    public = {i.profile for i in corpora["train"] + corpora["regression"]}
    assert protected & public, "protected items should exercise the same profile world"
    assert len(protected) >= 6
