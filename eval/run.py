"""`python -m eval.run` — the only G2 entry point (D-2b2-G2-7). Offline; `--sut live` is refused (Phase 2b-3).

Flow: corpus (G1) → ExpectedRecord (oracle side) → SUT driver → ActualRecord → compare → metrics → artefact.
The oracle never sees the SUT; the SUT never sees an expectation; the run executes twice to prove determinism.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from baaki.domain.enums import Arm
from baaki.policy.ruleset import DEFAULT_RULESET_PATH, load_ruleset
from eval.compare import build_expected, compare
from eval.hashing import ROOT, config_hash, file_hash, freeze_hash, freeze_manifest, jsonl_hash
from eval.loader import load_corpus, validate_corpus
from eval.metrics import compute_metrics, defect_candidates, gates, strata
from eval.profiles import PROFILES_PATH, load_profiles, to_account_facts
from eval.records import ActualRecord, ComparisonRecord, ExpectedRecord, ItemResult, RunArtifact, RunIdentity
from eval.report import (
    actuals_hash,
    banner_for,
    comparison_hash,
    load_gap_metadata,
    now_utc,
    run_id_for,
    schema_validation_metric,
    write_artifact,
)
from eval.schema import CorpusItem, EvidenceGrade, SchemaIntent
from eval.sut.base import CHAIN_SUT, RULES_SUT, SutArmIncompatible, SutDriver, SutInputs, arms_for, check_compatible
from eval.sut.chain import ChainSut
from eval.sut.probes import money_in_prompt, provider_schema_closure
from eval.sut.rules import RulesSut

EVAL_DIR = ROOT / "eval"
DEFAULT_CONFIG = EVAL_DIR / "config.v1.toml"
DEFAULT_GAP = EVAL_DIR / "gap_metadata.v1.json"
DEFAULT_OUT = EVAL_DIR / "results"
LIVE_REFUSED = "NOT_AVAILABLE_IN_2b-2: live provider evaluation is Phase 2b-3"


def _driver(sut_id: str) -> SutDriver:
    if sut_id == RULES_SUT:
        return RulesSut()
    if sut_id == CHAIN_SUT:
        return ChainSut()
    raise ValueError(f"unknown SUT {sut_id!r}")


def _git_commit() -> str | None:
    try:
        git = shutil.which("git")
        if git is None:
            return None
        return subprocess.run(  # noqa: S603 — fixed argv, resolved git binary, no user input
            [git, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — identity field is optional
        return None


def _inputs(item: CorpusItem, anchor: date) -> SutInputs:
    ptp = item.semantic.ptp
    return SutInputs(
        item_id=item.id,
        text=item.text,
        anchor=anchor,
        date_span=ptp.raw_date_span if ptp else None,
        amount_span=ptp.raw_amount_span if ptp else None,
        scripted_output=item.adversarial.malicious_model_output if item.adversarial else None,
    )


def _execute(
    items: list[CorpusItem], driver: SutDriver, arms: list[Arm], gaps: dict[str, Any]
) -> tuple[list[ExpectedRecord], list[ActualRecord], list[ComparisonRecord]]:
    profiles = load_profiles()
    ruleset = load_ruleset(DEFAULT_RULESET_PATH)
    facts_by_profile = {pid: to_account_facts(spec) for pid, spec in profiles.items()}
    expected = [build_expected(it, gaps) for it in items]  # oracle side, before any SUT runs
    actuals: list[ActualRecord] = []
    comparisons: list[ComparisonRecord] = []
    for it, exp in zip(items, expected, strict=True):
        facts = facts_by_profile[it.profile]
        for arm in arms:
            act = driver.run_item(_inputs(it, facts.business_date), facts, arm, ruleset)
            actuals.append(act)
            comparisons.append(compare(exp, act, profiles[it.profile]))
    return expected, actuals, comparisons


def run_evaluation(
    sut_id: str,
    arms: list[Arm] | None,
    split: str,
    *,
    corpus_dir: Path = EVAL_DIR / "corpus",
    config_path: Path = DEFAULT_CONFIG,
    gap_path: Path = DEFAULT_GAP,
    out_dir: Path = DEFAULT_OUT,
    twice: bool = True,
) -> Path:
    if sut_id == "live":
        raise SystemExit(LIVE_REFUSED)
    arms = arms or arms_for(sut_id)
    for arm in arms:
        check_compatible(sut_id, arm)  # deterministic refusal before anything runs
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    corpus_path = corpus_dir / f"{split}.v1.jsonl"
    items = [it for it in load_corpus(corpus_path) if str(it.split) == split]
    corpus_errors = validate_corpus(items)
    gaps = load_gap_metadata(gap_path)
    driver = _driver(sut_id)
    expected, actuals, comparisons = _execute(items, driver, arms, gaps)
    det: float | None = None
    if twice:
        _, actuals2, comparisons2 = _execute(items, driver, arms, gaps)
        det = (
            1.0
            if comparison_hash(comparisons2) == comparison_hash(comparisons)
            and actuals_hash(actuals2) == actuals_hash(actuals)
            else 0.0
        )
    items_by_id = {it.id: it for it in items}
    actual_intents: dict[tuple[str, str], SchemaIntent | None] = {
        (a.item_id, str(a.arm)): (a.interpretation.intent if a.interpretation else None) for a in actuals
    }
    primary = Arm.RULES_ONLY if sut_id == RULES_SUT else Arm.TREATMENT
    if primary not in arms:
        primary = arms[0]
    by_arm = {str(arm): [c for c in comparisons if c.arm is arm] for arm in arms}
    core_by_arm = {arm: compute_metrics(rows, items_by_id, actual_intents) for arm, rows in by_arm.items()}
    all_rows = compute_metrics(comparisons, items_by_id, actual_intents)["metrics"]
    gate_metrics = dict(core_by_arm[str(primary)]["metrics"])
    gate_metrics["policy_violation_rate"] = all_rows["policy_violation_rate"]  # any arm violating fails the gate
    gate_metrics["unsafe_effect_rate"] = all_rows["unsafe_effect_rate"]
    profiles = load_profiles()
    facts_by_profile = {pid: to_account_facts(spec) for pid, spec in profiles.items()}
    psc = provider_schema_closure()
    mip = money_in_prompt(items, facts_by_profile)
    records: list[Any] = [*expected, *actuals, *comparisons]
    eval_schema = schema_validation_metric(records)
    corpus_schema = (len(items) - len({e.split(":")[0] for e in corpus_errors}), len(items))
    gate_rows = gates(
        gate_metrics,
        cfg,
        split,
        provider_schema_closure=psc,
        money_in_prompt=mip,
        determinism=det,
        eval_schema=eval_schema,
        corpus_schema=corpus_schema,
    )
    grade = (
        EvidenceGrade.BOOTSTRAP
        if any(it.evidence_grade is EvidenceGrade.BOOTSTRAP for it in items)
        else EvidenceGrade.EVALUATION
    )
    fm = freeze_manifest()
    identity = dict(
        harness_version="harness.g2.v1",
        git_commit=_git_commit(),
        split=split,
        corpus_path=str(corpus_path.relative_to(ROOT)),
        corpus_hash=jsonl_hash(corpus_path),
        profiles_hash=file_hash(PROFILES_PATH),
        config_hash=config_hash(config_path),
        safety_policy_hash=file_hash(EVAL_DIR / "safety_policy.v1.json"),
        ruleset_policy_hash=file_hash(DEFAULT_RULESET_PATH),
        gap_metadata_hash=file_hash(gap_path),
        freeze_manifest=fm,
        freeze_hash=freeze_hash(fm),
        sut_id=driver.sut_id,
        sut_version=driver.version,
        arms=[str(a) for a in arms],
        evidence_grade=str(grade),
    )
    run = RunIdentity(run_id=run_id_for(identity), **{**identity, "arms": arms, "evidence_grade": grade})
    status = "COMPLETE" if eval_schema.rate == 1.0 and not corpus_errors else "INVALID"
    by_item: dict[str, ItemResult] = {}
    for exp in expected:
        by_item[exp.item_id] = ItemResult(
            expected=exp,
            actuals=[a for a in actuals if a.item_id == exp.item_id],
            comparisons=[c for c in comparisons if c.item_id == exp.item_id],
        )
    artifact = RunArtifact(
        run=run,
        created_at_utc=now_utc(),
        status=status,
        banner=banner_for(str(grade), status),
        items=[by_item[k] for k in sorted(by_item)],
        primary_arm=str(primary),
        metrics={arm: core["metrics"] for arm, core in core_by_arm.items()},
        strata={arm: strata(rows, items_by_id, actual_intents) for arm, rows in by_arm.items()},
        confusion_9={arm: core["confusion_9"] for arm, core in core_by_arm.items()},
        confusion_7={arm: core["confusion_7"] for arm, core in core_by_arm.items()},
        gates=gate_rows,
        defect_candidates=defect_candidates(comparisons, items_by_id),
        evaluation_schema_validation=eval_schema,
        not_available_offline={
            "live_sut": "Phase 2b-3",
            "token_usage": "NOT_AVAILABLE_OFFLINE",
            "cost": "NOT_AVAILABLE_OFFLINE",
            "live_latency": "NOT_AVAILABLE_OFFLINE",
        },
        comparison_hash=comparison_hash(comparisons),
    )
    return write_artifact(artifact, out_dir)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m eval.run", description="Baaki offline evaluation harness (Phase 2b-2 G2)"
    )
    p.add_argument("--sut", required=True, choices=[RULES_SUT, CHAIN_SUT, "live"])
    p.add_argument("--arm", default="all", choices=["all", "control", "rules_only", "treatment"])
    p.add_argument("--split", default="train", choices=["train", "dev", "regression", "heldout"])
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--once", action="store_true", help="skip the second determinism pass")
    a = p.parse_args(argv)
    if a.sut == "live":
        sys.stderr.write(LIVE_REFUSED + "\n")
        return 2
    arms = None if a.arm == "all" else [Arm(a.arm.upper())]
    try:
        path = run_evaluation(a.sut, arms, a.split, config_path=a.config, out_dir=a.out, twice=not a.once)
    except SutArmIncompatible as e:
        sys.stderr.write(f"SUT_ARM_INCOMPATIBLE: {e}\n")
        return 3
    sys.stdout.write(str(path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
