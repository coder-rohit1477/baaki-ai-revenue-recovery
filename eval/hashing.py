"""Reproducibility scaffolding (D-2b2-8, LOCKED): canonical hashing of corpora, configuration and the freeze set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# Files whose bytes define "the implementation under evaluation" plus the oracle artefacts (freeze set, D-2b2-3/8).
FREEZE_FILES: Final[tuple[str, ...]] = (
    "src/baaki/rules_agent/interpreter.py",
    "src/baaki/rules_agent/restriction.py",
    "src/baaki/rules_agent/tree.py",
    "src/baaki/policy/validate/normalize.py",
    "src/baaki/agent/prompts/interp.v1.txt",
    "src/baaki/agent/prompts/propose.v1.txt",
    "eval/safety_policy.v1.json",
    "eval/enr.py",
    "eval/config.v1.toml",
    "eval/sut/classify.py",
    "eval/gap_metadata.v1.json",
    "eval/defects.v1.json",
)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def jsonl_hash(path: Path) -> str:
    """Hash of the canonical form of every record (whitespace-insensitive, content-sensitive, order-sensitive)."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    canon = "\n".join(canonical_json(json.loads(ln)) for ln in lines)
    return sha256_bytes(canon.encode("utf-8"))


def config_hash(path: Path) -> str:
    return file_hash(path)


def freeze_manifest(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_hash(root / rel) for rel in FREEZE_FILES}


def freeze_hash(manifest: dict[str, str]) -> str:
    return sha256_bytes(canonical_json(manifest).encode("utf-8"))
