"""The 16-check validator (ARCHITECTURE.md §4.1). Pure. Short-circuits at the first HARD failure.

Order: 00 hash binding (P2-D4, reported as SCHEMA_VIOLATION) → 01–12 → SC3 target selection (P2-D8) → 13–16.
SOFT failures never reject: they are recorded in `checks_run` and cap `effective_confidence` into band C so the
kernel's P13 downgrades authority to tier 0 (§4.1 "SOFT → authority capped to tier 0").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError

from baaki.contracts.agent_proposal import money_key_violations, typed_date_violations
from baaki.contracts.normalized_action import NormalizedActionProposal
from baaki.contracts.validation_input import ValidationInput
from baaki.contracts.validation_result import NormalizedInterpretation, ValidationResult
from baaki.domain.enums import ParseStatus, ProposalKind, RejectionReason, ValidationOutcome
from baaki.domain.ids import new_id
from baaki.domain.money import ClaimedPaise
from baaki.policy.kernel.target import select_target
from baaki.policy.ruleset import Ruleset
from baaki.policy.schemas import action_proposal_v1, interpretation_v1
from baaki.policy.schemas.action_proposal_v1 import ActionProposalV1
from baaki.policy.schemas.interpretation_v1 import InterpretationV1
from baaki.policy.validate.normalize import parse_amount, parse_date

VALIDATOR_VERSION: Final[str] = "validator.v1"
CHECK_IDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("00", "SOURCE_HASH_BOUND", "HARD"),
    ("01", "KILL_SWITCH_OFF", "HARD"),
    ("02", "LEDGER_INVARIANT_OK", "HARD"),
    ("03", "PROPOSAL_PARSE_OK", "HARD"),
    ("04", "SCHEMA_VERSION_KNOWN", "HARD"),
    ("05", "ENUM_CLOSURE", "HARD"),
    ("06", "NO_MONEY_KEYS", "HARD"),
    ("07", "EVIDENCE_SPANS_LITERAL", "HARD"),
    ("08", "EVIDENCE_COVERS_CLAIMS", "HARD"),
    ("09", "CONTACT_REF_VALID", "HARD"),
    ("10", "INVOICE_REF_VALID", "HARD"),
    ("11", "DATE_NORMALISE", "HARD"),
    ("12", "AMOUNT_NORMALISE", "HARD"),
    ("13", "DATE_RANGE_SANE", "SOFT"),
    ("14", "AMOUNT_RANGE_SANE", "SOFT"),
    ("15", "CONFIDENCE_FLOOR", "SOFT"),
    ("16", "CONFIDENCE_MONOTONIC_CAP", "CAP"),
)
VALIDATOR_HASH: Final[str] = hashlib.sha256(
    (VALIDATOR_VERSION + "|" + "|".join(c[1] for c in CHECK_IDS)).encode()
).hexdigest()
AMBIGUOUS_REASONS: Final[frozenset[RejectionReason]] = frozenset(
    {
        RejectionReason.INVOICE_REF_UNRESOLVED,
        RejectionReason.DATE_UNPARSEABLE,
        RejectionReason.DATE_AMBIGUOUS,
        RejectionReason.AMOUNT_UNPARSEABLE,
        RejectionReason.AMOUNT_AMBIGUOUS,
    }
)
SCHEMA_FOR_KIND: Final[dict[ProposalKind, str]] = {
    ProposalKind.INTERPRETATION: interpretation_v1.SCHEMA_VERSION,
    ProposalKind.ACTION_PROPOSAL: action_proposal_v1.SCHEMA_VERSION,
}


@dataclass
class _Run:
    checks: list[dict[str, Any]] = field(default_factory=list)
    hard: RejectionReason | None = None
    soft: list[RejectionReason] = field(default_factory=list)

    def record(
        self,
        seq: str,
        check_id: str,
        cls: str,
        passed: bool,
        reason: RejectionReason | None = None,
        *,
        skipped: bool = False,
        detail: str | None = None,
    ) -> None:
        self.checks.append(
            {
                "seq": seq,
                "check_id": check_id,
                "class": cls,
                "passed": passed,
                "skipped": skipped,
                "reason": None if reason is None else str(reason),
                "detail": detail,
            }
        )
        if not passed and not skipped:
            if cls == "HARD":
                self.hard = reason
            elif cls == "SOFT" and reason is not None:
                self.soft.append(reason)


@dataclass(frozen=True)
class ValidationBundle:
    result: ValidationResult
    target_invoice_id: UUID | None  # SC3 outcome (None ⟹ no candidates / SC7)
    resolved_invoice_ids: list[UUID]
    rejected_ambiguous: bool


def validate(inp: ValidationInput, ruleset: Ruleset, *, now: datetime) -> ValidationBundle:
    p, facts = inp.proposal, inp.facts
    run = _Run()
    parsed_interp: InterpretationV1 | None = None
    parsed_action: ActionProposalV1 | None = None
    resolved: list[UUID] = []
    promised_date = None
    promised_paise: ClaimedPaise | None = None
    target: UUID | None = None
    effective = float(p.confidence) if p.confidence is not None else 0.0

    def finish() -> ValidationBundle:
        if run.hard is not None:
            res = ValidationResult(
                validation_id=new_id(),
                proposal_id=p.proposal_id,
                trace_id=p.trace_id,
                account_id=p.account_id,
                business_date=p.business_date,
                outcome=ValidationOutcome.REJECT,
                rejection_reasons=[run.hard],
                normalized=None,
                checks_run=run.checks,
                validator_version=VALIDATOR_VERSION,
                validator_hash=VALIDATOR_HASH,
                created_at=now,
            )
            return ValidationBundle(res, target, resolved, run.hard in AMBIGUOUS_REASONS)
        normalized: NormalizedInterpretation | NormalizedActionProposal
        if parsed_interp is not None:
            normalized = NormalizedInterpretation(
                intent=str(parsed_interp.intent),
                promised_date=promised_date,
                promised_paise=promised_paise,
                invoice_ids=resolved,
                contact_id=None,
                effective_confidence=effective,
            )
        else:
            assert parsed_action is not None
            normalized = NormalizedActionProposal(
                action=parsed_action.action,
                contact_id=parsed_action.contact_id,
                channel=parsed_action.channel,
                template_id=parsed_action.template_id,
                followup_days=parsed_action.followup_days,
                effective_confidence=effective,
            )
        res = ValidationResult(
            validation_id=new_id(),
            proposal_id=p.proposal_id,
            trace_id=p.trace_id,
            account_id=p.account_id,
            business_date=p.business_date,
            outcome=ValidationOutcome.PASS,
            rejection_reasons=[],
            normalized=normalized,
            checks_run=run.checks,
            validator_version=VALIDATOR_VERSION,
            validator_hash=VALIDATOR_HASH,
            created_at=now,
        )
        return ValidationBundle(res, target, resolved, False)

    # 00 — hash binding (P2-D4)
    ok = inp.source_hash_matches()
    run.record(
        "00",
        "SOURCE_HASH_BOUND",
        "HARD",
        ok,
        None if ok else RejectionReason.SCHEMA_VIOLATION,
        detail=None if ok else "sha256(source_text) != input_hash",
    )
    if run.hard:
        return finish()
    # 01
    run.record(
        "01",
        "KILL_SWITCH_OFF",
        "HARD",
        not facts.kill_switch,
        None if not facts.kill_switch else RejectionReason.SYSTEM_HALTED,
    )
    if run.hard:
        return finish()
    # 02
    run.record(
        "02",
        "LEDGER_INVARIANT_OK",
        "HARD",
        facts.ledger_invariant_ok,
        None if facts.ledger_invariant_ok else RejectionReason.LEDGER_INVARIANT_BREACH,
    )
    if run.hard:
        return finish()
    # 03
    parse_reason = {
        ParseStatus.SCHEMA_VIOLATION: RejectionReason.SCHEMA_VIOLATION,
        ParseStatus.UNPARSEABLE: RejectionReason.UNPARSEABLE,
        ParseStatus.TIMEOUT: RejectionReason.PROVIDER_TIMEOUT,
        ParseStatus.PROVIDER_ERROR: RejectionReason.PROVIDER_TIMEOUT,
    }
    ok = p.parse_status is ParseStatus.OK and p.parsed is not None
    run.record(
        "03",
        "PROPOSAL_PARSE_OK",
        "HARD",
        ok,
        None if ok else parse_reason.get(p.parse_status, RejectionReason.UNPARSEABLE),
    )
    if run.hard:
        return finish()
    assert p.parsed is not None
    # 04
    expected = SCHEMA_FOR_KIND[p.kind]
    ok = p.schema_version == expected
    run.record(
        "04",
        "SCHEMA_VERSION_KNOWN",
        "HARD",
        ok,
        None if ok else RejectionReason.UNKNOWN_SCHEMA_VERSION,
        detail=None if ok else p.schema_version,
    )
    if run.hard:
        return finish()
    # 05 — enum closure / strict shape via the offline schema
    try:
        if p.kind is ProposalKind.INTERPRETATION:
            parsed_interp = InterpretationV1.model_validate_json(json.dumps(p.parsed))
        else:
            parsed_action = ActionProposalV1.model_validate_json(json.dumps(p.parsed))
        run.record("05", "ENUM_CLOSURE", "HARD", True)
    except ValidationError as e:
        enum_err = any(err.get("type") in ("enum", "literal_error") for err in e.errors())
        run.record(
            "05",
            "ENUM_CLOSURE",
            "HARD",
            False,
            RejectionReason.ENUM_OUT_OF_RANGE if enum_err else RejectionReason.SCHEMA_VIOLATION,
            detail=str(e.errors()[0].get("loc")),
        )
        return finish()
    # 06
    bad = money_key_violations(p.parsed) + typed_date_violations(p.parsed)
    run.record(
        "06",
        "NO_MONEY_KEYS",
        "HARD",
        not bad,
        None if not bad else RejectionReason.FORBIDDEN_MONEY_FIELD,
        detail=",".join(bad) or None,
    )
    if run.hard:
        return finish()
    # 07 / 08 — interpretation only
    if parsed_interp is not None:
        missing = [ev.quote for ev in parsed_interp.evidence if ev.quote not in inp.source_text]
        run.record(
            "07",
            "EVIDENCE_SPANS_LITERAL",
            "HARD",
            not missing,
            None if not missing else RejectionReason.EVIDENCE_NOT_FOUND_IN_SOURCE,
            detail=missing[0] if missing else None,
        )
        if run.hard:
            return finish()
        evidenced = {ev.field for ev in parsed_interp.evidence}
        claims = [f for f in InterpretationV1.CLAIM_FIELDS if getattr(parsed_interp, f)]
        uncovered = [f for f in claims if f not in evidenced]
        run.record(
            "08",
            "EVIDENCE_COVERS_CLAIMS",
            "HARD",
            not uncovered,
            None if not uncovered else RejectionReason.EVIDENCE_MISSING_FOR_FIELD,
            detail=uncovered[0] if uncovered else None,
        )
        if run.hard:
            return finish()
    else:
        run.record("07", "EVIDENCE_SPANS_LITERAL", "HARD", True, skipped=True)
        run.record("08", "EVIDENCE_COVERS_CLAIMS", "HARD", True, skipped=True)
    # 09 — contact reference (action proposal)
    contactable = {c.contact_id for c in facts.contactable}
    if parsed_action is not None and parsed_action.contact_id is not None:
        ok = parsed_action.contact_id in contactable
        run.record("09", "CONTACT_REF_VALID", "HARD", ok, None if ok else RejectionReason.CONTACT_NOT_IN_ACCOUNT)
        if run.hard:
            return finish()
    else:
        run.record("09", "CONTACT_REF_VALID", "HARD", True, skipped=True)
    # 10 — invoice refs resolve only within the account (SC1)
    if parsed_interp is not None and parsed_interp.invoice_refs:
        by_number = {r.invoice_number: r.invoice_id for r in facts.all_invoices}
        unresolved = [ref for ref in parsed_interp.invoice_refs if ref not in by_number]
        resolved = [by_number[ref] for ref in parsed_interp.invoice_refs if ref in by_number]
        run.record(
            "10",
            "INVOICE_REF_VALID",
            "HARD",
            not unresolved,
            None if not unresolved else RejectionReason.INVOICE_REF_UNRESOLVED,
            detail=unresolved[0] if unresolved else None,
        )
        if run.hard:
            return finish()
    else:
        run.record("10", "INVOICE_REF_VALID", "HARD", True, skipped=True)
    # 11 — date
    if parsed_interp is not None and parsed_interp.promised_date_raw is not None:
        dp = parse_date(parsed_interp.promised_date_raw, p.business_date)
        reason = {"unparseable": RejectionReason.DATE_UNPARSEABLE, "ambiguous": RejectionReason.DATE_AMBIGUOUS}.get(
            dp.status
        )
        run.record("11", "DATE_NORMALISE", "HARD", dp.status == "ok", reason, detail=parsed_interp.promised_date_raw)
        if run.hard:
            return finish()
        promised_date = dp.value
    else:
        run.record("11", "DATE_NORMALISE", "HARD", True, skipped=True)
    # 12 — amount
    if parsed_interp is not None and parsed_interp.promised_amount_raw is not None:
        ap = parse_amount(parsed_interp.promised_amount_raw)
        reason = {"unparseable": RejectionReason.AMOUNT_UNPARSEABLE, "ambiguous": RejectionReason.AMOUNT_AMBIGUOUS}.get(
            ap.status
        )
        run.record(
            "12", "AMOUNT_NORMALISE", "HARD", ap.status == "ok", reason, detail=parsed_interp.promised_amount_raw
        )
        if run.hard:
            return finish()
        promised_paise = ap.value
    else:
        run.record("12", "AMOUNT_NORMALISE", "HARD", True, skipped=True)
    # SC3 — target (P2-D8)
    target = select_target(facts.candidate_ids, resolved, p.invoice_id)
    target_c = facts.candidate(target) if target is not None else None
    # 13 — date range (SOFT)
    if promised_date is not None:
        if promised_date <= p.business_date:
            run.record("13", "DATE_RANGE_SANE", "SOFT", False, RejectionReason.DATE_IN_PAST)
        elif promised_date > p.business_date + timedelta(days=ruleset.ptp_horizon_days):
            run.record("13", "DATE_RANGE_SANE", "SOFT", False, RejectionReason.DATE_BEYOND_HORIZON)
        else:
            run.record("13", "DATE_RANGE_SANE", "SOFT", True)
    else:
        run.record("13", "DATE_RANGE_SANE", "SOFT", True, skipped=True)
    # 14 — amount range (SOFT) — comparison only (V7)
    if promised_paise is not None and target_c is not None:
        ok = int(promised_paise) <= int(target_c.outstanding_paise)
        run.record("14", "AMOUNT_RANGE_SANE", "SOFT", ok, None if ok else RejectionReason.AMOUNT_EXCEEDS_OUTSTANDING)
    else:
        run.record("14", "AMOUNT_RANGE_SANE", "SOFT", True, skipped=True)
    # 15 — confidence floor (SOFT)
    conf = float(parsed_interp.confidence if parsed_interp is not None else parsed_action.confidence)  # type: ignore[union-attr]
    ok = conf >= ruleset.confidence_floor
    run.record("15", "CONFIDENCE_FLOOR", "SOFT", ok, None if ok else RejectionReason.CONFIDENCE_BELOW_THRESHOLD)
    # 16 — monotonic cap (I4): effective ≤ model confidence; SOFT failures push into band C (tier-0 authority)
    effective = conf
    if run.soft:
        band_c_top = ruleset.confidence_floor
        effective = min(conf, max(0.0, round(band_c_top - 0.001, 3)))
    run.record("16", "CONFIDENCE_MONOTONIC_CAP", "CAP", True, detail=f"{conf}->{effective}")
    return finish()
