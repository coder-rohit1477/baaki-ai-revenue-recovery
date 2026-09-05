"""Phase 2b-2 G4: the protected held-out set's machine-enforced guards.

These are the parts of the protection model that do not depend on discipline: what production can reach,
what the generator may import, what must never be committed, and what a scored run demands before it will
load the protected split.
"""

import ast
import json
import subprocess
from pathlib import Path

import pytest
from eval.gen.generate import ANSWER_FIELDS, INPUT_FIELDS
from eval.hashing import FREEZE_FILES
from eval.run import EXT_UNLOCK_ENV, HELDOUT_UNLOCK_ENV, ProtectedSplitLocked, load_heldout, load_heldout_extension

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
EVAL = ROOT / "eval"
GEN = EVAL / "gen"
CORPUS = EVAL / "corpus"
LOCK = EVAL / "heldout.v2.lock.json"
BANK = GEN / "bank.v1.json"
INPUT_FILES = (CORPUS / "heldout.v2.jsonl", CORPUS / "heldout.ext.v2.jsonl")
SUT_FORBIDDEN_FOR_GEN = (
    "baaki.rules_agent",
    "baaki.policy",
    "baaki.agent",
    "baaki.providers",
    "baaki.pipeline",
    "baaki.db",
    "eval.sut",
)


def _imports(f: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def _tracked(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)], cwd=ROOT, capture_output=True)
    return r.returncode == 0


def test_no_production_module_references_the_protected_split():
    for f in SRC.rglob("*.py"):
        assert "heldout" not in f.read_text(encoding="utf-8").lower(), f


def test_no_sut_module_reads_the_heldout_corpus():
    """The SUT must be blind to which split it is running: it receives inputs, never a corpus path."""
    for f in (EVAL / "sut").glob("*.py"):
        body = f.read_text(encoding="utf-8").lower()
        assert "heldout" not in body and "answers.v1" not in body, f


def test_generator_never_imports_the_system_under_test():
    for f in GEN.glob("*.py"):
        for imp in _imports(f):
            assert not imp.startswith(SUT_FORBIDDEN_FOR_GEN), (f, imp)
            assert imp.split(".")[0] not in {"openai", "httpx", "requests", "aiohttp", "sqlalchemy", "psycopg"}


def test_generator_stays_on_the_oracle_side_of_the_boundary():
    allowed = (
        "eval.schema",
        "eval.oracle",
        "eval.profiles",
        "eval.enr",
        "eval.hashing",
        "eval.loader",
        "eval.gen",
        "baaki.domain",
        "baaki.contracts",
    )
    for f in GEN.glob("*.py"):
        for imp in _imports(f):
            if imp.startswith(("eval", "baaki")):
                assert imp.startswith(allowed), (f, imp)


def test_protected_answers_are_data_not_an_importable_module():
    for name in ("heldout.answers.v2.jsonl", "heldout.ext.answers.v2.jsonl"):
        assert (CORPUS / name).exists()
    assert not list(CORPUS.glob("*.py"))  # nothing under eval/corpus is importable


def test_heldout_run_requires_explicit_unlock(monkeypatch):
    monkeypatch.delenv(HELDOUT_UNLOCK_ENV, raising=False)
    with pytest.raises(ProtectedSplitLocked):
        load_heldout()
    monkeypatch.setenv(HELDOUT_UNLOCK_ENV, "1")
    assert len(load_heldout()) == 340


def test_extension_requires_its_own_unlock_and_is_never_enabled_by_a_heldout_run(monkeypatch):
    monkeypatch.setenv(HELDOUT_UNLOCK_ENV, "1")
    monkeypatch.delenv(EXT_UNLOCK_ENV, raising=False)
    with pytest.raises(ProtectedSplitLocked):
        load_heldout_extension()  # the scored unlock does not open the extension
    monkeypatch.setenv(EXT_UNLOCK_ENV, "1")
    assert len(load_heldout_extension()) == 200


def test_heldout_result_artifacts_are_git_ignored():
    r = subprocess.run(["git", "check-ignore", "eval/results/x.heldout.json"], cwd=ROOT, capture_output=True)
    assert r.returncode == 0
    assert (
        subprocess.run(
            ["git", "check-ignore", "eval/results/pg16_coverage.json"], cwd=ROOT, capture_output=True
        ).returncode
        == 0
    )
    # the audit log is the single deliberate exception: it is committed evidence, so it must not be ignored
    kept = subprocess.run(["git", "check-ignore", "eval/results/heldout_touches.jsonl"], cwd=ROOT, capture_output=True)
    assert kept.returncode != 0, "the touch log must remain committable"


def test_plaintext_surface_seed_is_never_committed():
    """Only the commitment may be published; the seed itself stays in the authoring environment."""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert len(lock["surface_seed_hash"]) == 64 and "surface_seed" not in lock
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    guards = {Path(__file__).relative_to(ROOT).as_posix()}  # this file names the pattern it searches for
    for rel in tracked:
        if rel in guards or not rel.endswith((".json", ".toml", ".md", ".py", ".txt")):
            continue
        body = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        # the risk is a committed key/value pair, not a mention of the word in a guard or a document
        assert '"surface_seed":' not in body, rel
        assert "SURFACE_SEED=" not in body, rel


def test_the_surface_bank_is_not_committed_and_is_absent_from_the_freeze_set():
    assert "eval/gen/bank.v1.json" not in FREEZE_FILES  # a public clone could not compute the manifest
    assert "eval/gen/templates.v1.json" in FREEZE_FILES
    r = subprocess.run(["git", "check-ignore", "eval/gen/bank.v1.json"], cwd=ROOT, capture_output=True)
    assert r.returncode == 0, "the protected bank must be git-ignored"
    assert not _tracked(BANK)
    assert json.loads(LOCK.read_text(encoding="utf-8"))["bank_hash"]  # pinned in the lock instead


def test_input_files_carry_no_answer_fields():
    answer_only = set(ANSWER_FIELDS) - {"id"}
    for path in INPUT_FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert set(row) == set(INPUT_FIELDS), (path.name, sorted(set(row) ^ set(INPUT_FIELDS)))
            assert not (set(row) & answer_only), path.name


def test_the_freeze_lock_records_every_protected_hash():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    for key in (
        "corpus_hash",
        "answers_hash",
        "ext_corpus_hash",
        "ext_answers_hash",
        "generator_hash",
        "templates_hash",
        "bank_hash",
        "surface_seed_hash",
        "freeze_hash",
        "calibration_inputs_hash",
    ):
        assert len(lock[key]) == 64, key
    assert lock["ext_scored"] is False and lock["structure_seed"] == 20260905
