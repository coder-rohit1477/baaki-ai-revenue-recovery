"""Result artefact assembly and I/O (PHASE2B2_PLAN §15–§16). Hashes exclude wall-clock and latency."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.hashing import canonical_json, freeze_hash, freeze_manifest, sha256_bytes
from eval.records import (
    ActualRecord,
    Calibration,
    ComparisonRecord,
    DatabaseCoverage,
    GapMeta,
    HeldoutLock,
    MetricValue,
    RunArtifact,
    RunIdentity,
)


def load_gap_metadata(path: Path) -> dict[str, GapMeta]:
    data = json.loads(path.read_bytes().decode("utf-8"))
    entries = [GapMeta.model_validate_json(json.dumps(e)) for e in data["entries"]]
    return {e.item_id: e for e in entries}


def load_defects(path: Path) -> dict[str, dict[str, Any]]:
    """D-G3-4: known authored-label defects keyed by item id (annotation only)."""
    data = json.loads(path.read_bytes().decode("utf-8"))
    return {e["item_id"]: e for e in data["entries"]}


def load_db_coverage(path: Path | None, *, n_adversarial: int, per_category: dict[str, int]) -> DatabaseCoverage:
    """D-G3-7: read the coverage record written by the PostgreSQL security suite; absent ⇒ not executed."""
    if path is None or not path.exists():
        return DatabaseCoverage(
            executed=False,
            selection_rule="NOT_EXECUTED",
            n_executed=0,
            n_adversarial_in_corpus=n_adversarial,
            per_category_in_corpus=per_category,
            note="no database-level run recorded for this corpus; chain-SUT coverage only",
        )
    raw = json.loads(path.read_bytes().decode("utf-8"))
    return DatabaseCoverage.model_validate_json(
        json.dumps(
            {
                **raw,
                "n_adversarial_in_corpus": n_adversarial,
                "per_category_in_corpus": per_category,
                "source": str(path),
            }
        )
    )


def load_heldout_lock(path: Path) -> HeldoutLock | None:
    """The frozen identity of the protected split, or None when no freeze has happened yet."""
    if not path.exists():
        return None
    return HeldoutLock.model_validate_json(path.read_text(encoding="utf-8"))


def verify_lock(lock: HeldoutLock | None, *, corpus_hash: str, answers_hash: str) -> HeldoutLock | None:
    """Recompute the freeze identity and record whether it still matches the lock (drift detection)."""
    if lock is None:
        return None
    observed_freeze = freeze_hash(freeze_manifest())
    matches = (
        lock.corpus_hash == corpus_hash and lock.answers_hash == answers_hash and lock.freeze_hash == observed_freeze
    )
    return lock.model_copy(
        update={
            "observed_corpus_hash": corpus_hash,
            "observed_answers_hash": answers_hash,
            "observed_freeze_hash": observed_freeze,
            "freeze_status": "MATCHES_LOCK" if matches else "DRIFTED",
        }
    )


TOUCH_KINDS = ("CORPUS_INPUTS", "CORPUS_ANSWERS", "EXTENSION_READ", "SCORED_RUN", "RESULT_READ")


def append_touch(path: Path, **fields: Any) -> None:
    """Append one audit record. The log is evidence, not access control (D-G4-2)."""
    if fields.get("kind") not in TOUCH_KINDS:
        raise ValueError(f"unknown touch kind {fields.get('kind')!r}")
    record = {"ts_utc": now_utc(), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(record) + "\n")


def touch_digest(path: Path) -> str | None:
    """SHA-256 of the audit log as it stood at run time, binding a result to its trail."""
    if not path.exists():
        return None
    return sha256_bytes(path.read_bytes())


def contamination_status(path: Path) -> str:
    """CONTAMINATED once any protected read is logged against the tuning phase (D-G4-3/D-G4-12)."""
    if not path.exists():
        return "CLEAN"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("kind") in ("CORPUS_ANSWERS", "EXTENSION_READ", "RESULT_READ") and rec.get("phase") == "2b-3":
            return "CONTAMINATED"
    return "CLEAN"


def load_calibration(path: Path) -> Calibration | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Calibration.model_validate_json(json.dumps(raw["result"]))


def comparison_hash(comparisons: list[ComparisonRecord]) -> str:
    ordered = sorted(comparisons, key=lambda r: (r.item_id, str(r.arm), r.sut_id))
    return sha256_bytes("\n".join(canonical_json(r.model_dump(mode="json")) for r in ordered).encode("utf-8"))


def actuals_hash(actuals: list[ActualRecord]) -> str:
    """Hash of ACTUAL records with latency removed (latency is stored, never hashed)."""
    ordered = sorted(actuals, key=lambda a: (a.item_id, str(a.arm), a.sut_id))
    return sha256_bytes(
        "\n".join(canonical_json(a.model_dump(mode="json", exclude={"latency"})) for a in ordered).encode("utf-8")
    )


def run_id_for(identity_fields: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(identity_fields).encode("utf-8"))


def banner_for(evidence_grade: str, status: str) -> str:
    if status != "COMPLETE":
        return "INVALID RUN — evaluation schema validation failed; no number in this artefact may be cited"
    if evidence_grade == "BOOTSTRAP":
        return "BOOTSTRAP / INFRASTRUCTURE — NOT EVALUATION EVIDENCE (seed corpus; G1/G2 plumbing check)"
    return "EVALUATION — labels: measured / report-only / gated / not-run"


def write_artifact(artifact: RunArtifact, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{artifact.run.run_id[:16]}.{artifact.run.sut_id}.{artifact.run.split}.json"
    path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def read_artifact(path: Path) -> RunArtifact:
    return RunArtifact.model_validate_json(path.read_bytes())


def schema_validation_metric(records: list[Any]) -> MetricValue:
    """Every record is re-validated from its JSON form; the count that survives is the numerator (D-2b2-G2-10)."""
    ok = 0
    failed: list[str] = []
    for r in records:
        try:
            type(r).model_validate_json(r.model_dump_json())
            ok += 1
        except Exception as e:  # noqa: BLE001 — counted, never raised
            failed.append(type(e).__name__)
    return MetricValue(
        numerator=ok,
        denominator=len(records),
        rate=(round(ok / len(records), 6) if records else None),
        label="gated",
        note="harness-integrity gate, distinct from provider_schema_closure"
        + (f"; failures: {sorted(set(failed))}" if failed else ""),
    )


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


__all__ = [
    "RunIdentity",
    "actuals_hash",
    "banner_for",
    "comparison_hash",
    "load_gap_metadata",
    "read_artifact",
    "run_id_for",
    "schema_validation_metric",
    "write_artifact",
]
