"""Corpus loading and integrity validation (PHASE2B2_PLAN §4, D-2b2-6/13/14/15).

Every check is a corpus-authoring check; none consults production behaviour.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Final

from eval.enr import normalize_amount, normalize_date
from eval.oracle import GAP_CHANNEL_OTHER, expected_outcome
from eval.profiles import load_profiles
from eval.schema import PAIR_FEATURE_FIELDS, CorpusItem, OptOutScope

ENR_OVERRIDE_MARK: Final[str] = "ENR-OVERRIDE:"


def load_corpus(path: Path) -> list[CorpusItem]:
    items: list[CorpusItem] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(CorpusItem.model_validate_json(line))
        except Exception as e:  # noqa: BLE001 — re-raised with location
            raise ValueError(f"{path.name}:{n}: {e}") from e
    return items


def validate_corpus(items: list[CorpusItem]) -> list[str]:
    """Return every integrity error (empty list == valid). Pure."""
    errors: list[str] = []
    profiles = load_profiles()
    seen: set[str] = set()
    pairs: dict[str, list[CorpusItem]] = defaultdict(list)
    for it in items:
        if it.id in seen:
            errors.append(f"{it.id}: duplicate id")
        seen.add(it.id)
        prof = profiles.get(it.profile)
        if prof is None:
            errors.append(f"{it.id}: unknown profile {it.profile}")
            continue
        # Layer B consistency: authored safety oracle must equal the declarative policy evaluation
        expected = expected_outcome(it.semantic, prof)
        if expected != it.safety:
            errors.append(f"{it.id}: safety oracle differs from safety_policy.v1 → {expected.model_dump(mode='json')}")
        # CHANNEL_OTHER is a recorded gap, never a success
        if it.semantic.opt_out_scope is OptOutScope.CHANNEL_OTHER and it.safety.policy_gap != GAP_CHANNEL_OTHER:
            errors.append(f"{it.id}: CHANNEL_OTHER must carry policy_gap {GAP_CHANNEL_OTHER}")
        if it.semantic.opt_out_scope is not OptOutScope.CHANNEL_OTHER and it.safety.policy_gap is not None:
            errors.append(f"{it.id}: policy_gap set without CHANNEL_OTHER scope")
        # ENR consistency of authored PTP values (annotator arithmetic), unless explicitly overridden with a rationale
        if it.semantic.ptp is not None and ENR_OVERRIDE_MARK not in it.notes:
            p = it.semantic.ptp
            if p.raw_date_span is not None:
                ref = normalize_date(p.raw_date_span, prof.business_date, clause=it.text)
                if p.abstain_date != (ref.status != "value") or (
                    not p.abstain_date and ref.value != p.expected_date_iso
                ):
                    errors.append(
                        f"{it.id}: PTP date {p.raw_date_span!r} authored "
                        f"{p.expected_date_iso}/{p.abstain_date} vs ENR {ref}"
                    )
            if p.raw_amount_span is not None:
                refa = normalize_amount(p.raw_amount_span)
                if p.abstain_amount != (refa.status != "value") or (
                    not p.abstain_amount and refa.paise != p.expected_amount_paise
                ):
                    errors.append(
                        f"{it.id}: PTP amount {p.raw_amount_span!r} authored "
                        f"{p.expected_amount_paise}/{p.abstain_amount} vs ENR {refa}"
                    )
        if it.pair_id is not None:
            pairs[it.pair_id].append(it)
    # Minimal pairs: exactly two members, same feature, differ only in feature-permitted semantic fields (D-2b2-15)
    for pid, members in pairs.items():
        if len(members) != 2:
            errors.append(f"{pid}: a minimal pair needs exactly two members, found {len(members)}")
            continue
        a, b = members
        if a.pair_feature != b.pair_feature:
            errors.append(f"{pid}: members declare different features")
            continue
        for field in ("language", "message_register", "profile", "split"):
            if getattr(a, field) != getattr(b, field):
                errors.append(f"{pid}: members differ in {field}")
        allowed = PAIR_FEATURE_FIELDS[a.pair_feature]  # type: ignore[index]
        da, db = a.semantic.model_dump(mode="json"), b.semantic.model_dump(mode="json")
        differing = {k for k in da if da[k] != db[k]}
        if not differing:
            errors.append(f"{pid}: members are semantically identical — not a minimal pair")
        illegal = differing - allowed
        if illegal:
            errors.append(
                f"{pid}: fields {sorted(illegal)} differ but feature {a.pair_feature} permits only {sorted(allowed)}"
            )
    return errors


def assert_valid(items: list[CorpusItem]) -> None:
    errors = validate_corpus(items)
    if errors:
        raise ValueError("corpus integrity errors:\n" + "\n".join(errors))


def strata(items: list[CorpusItem]) -> dict[str, int]:
    """Counts used by later gates (G4) and by the bootstrap report. Counting only — no thresholds asserted in G1."""
    c: dict[str, int] = defaultdict(int)
    for it in items:
        c["total"] += 1
        c[f"split:{it.split}"] += 1
        c[f"language:{it.language}"] += 1
        c[f"intent:{it.semantic.primary_intent}"] += 1
        c[f"scope:{it.semantic.opt_out_scope}"] += 1
        if it.pair_id:
            c["pair_members"] += 1
        if it.adversarial:
            c["adversarial"] += 1
            c[f"adversarial:{it.adversarial.adversarial_category}"] += 1
        if it.author.value == "hand":
            c["hand_authored"] += 1
    return dict(c)


def dump_strata(items: list[CorpusItem]) -> str:
    return json.dumps(strata(items), sort_keys=True, indent=2)
