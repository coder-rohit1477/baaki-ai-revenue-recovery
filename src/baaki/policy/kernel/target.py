"""SC3 — deterministic target selection (ARCHITECTURE.md §6.8.3). Pure."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID


def select_target(candidates: Sequence[UUID], resolved_ids: Sequence[UUID], hint: UUID | None) -> UUID | None:
    """sole resolved id ∈ candidates → it; else hint ∈ candidates → it; else first candidate; else None (SC7)."""
    cset = set(candidates)
    in_cands = [r for r in resolved_ids if r in cset]
    if len(in_cands) == 1 and len(resolved_ids) == 1:
        return in_cands[0]
    if hint is not None and hint in cset:
        return hint
    return candidates[0] if candidates else None
