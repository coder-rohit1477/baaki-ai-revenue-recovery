"""run_decision_pipeline — one invoice-day for one account. No scheduler, no loop, no clock reads (as_of injected)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from baaki.contracts.action_choice import ActionChoice, DecisionContext
from baaki.contracts.agent_proposal import AgentProposal
from baaki.contracts.candidate import AccountFacts
from baaki.contracts.normalized_action import NormalizedActionProposal
from baaki.contracts.policy_decision import ExecutableDecision, NonExecutableDecision
from baaki.contracts.recovery_action import RecoveryAction
from baaki.contracts.validation_input import ValidationInput
from baaki.contracts.validation_result import NormalizedInterpretation, ValidationResult
from baaki.db.idempotency import canonical_payload_hash, idempotency_key
from baaki.db.writers._call import WriterUniqueViolation
from baaki.db.writers.action_auto import create_recovery_action
from baaki.db.writers.decision import record_policy_decision
from baaki.db.writers.validation import record_validation_result
from baaki.domain.enums import Arm, DegradationLevel, ProposalKind, ValidationOutcome
from baaki.domain.errors import WriterRefused
from baaki.domain.ids import new_id
from baaki.policy.arms import control, rules_only, treatment
from baaki.policy.kernel.decide import decide
from baaki.policy.optout import apply_inbound_opt_out
from baaki.policy.ruleset import Ruleset
from baaki.policy.snapshot import assemble_account_facts, build_snapshot
from baaki.policy.validate import ValidationBundle, validate
from baaki.rules_agent.interpreter import interpret

ACTION_TTL = timedelta(days=1)
RETRY_CODES = frozenset({"cp5_amount_mismatch", "invoice_not_candidate", "cp2_parts_mismatch"})
# The two per-day decision uniques (§5.8). Only these mean "already decided"; any other unique violation propagates.
DUPLICATE_DECISION_CONSTRAINTS = ("uq_decision_unlinked_day", "uq_decision_validation_day")


class PipelineRetryExhausted(Exception):
    """A second stale-snapshot mismatch: no partial state was written."""


@dataclass(frozen=True)
class Ineligible:
    account_id: UUID
    business_date: date
    reason: str
    validation_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class AlreadyDecided:
    """Duplicate replay of an invoice-day (§12.2): the existing rows are returned; nothing new was written."""

    decision_id: UUID
    action_id: UUID | None
    account_id: UUID
    business_date: date
    validation_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class Decided:
    decision: ExecutableDecision | NonExecutableDecision
    decision_id: UUID
    action_id: UUID | None
    superseded: bool
    validation_ids: tuple[UUID, ...]
    degradation_level: DegradationLevel


def _record_validation_or_existing(conn: Connection, v: ValidationResult) -> UUID:
    """W08 inside a savepoint: a proposal validated by an earlier call keeps its first (immutable) result."""
    n = v.normalized
    norm = n if n is None or isinstance(n, dict) else n.model_dump(mode="json")
    sp = conn.begin_nested()
    try:
        vid = record_validation_result(
            conn,
            validation_id=v.validation_id,
            proposal_id=v.proposal_id,
            outcome=v.outcome,
            rejection_reasons=list(v.rejection_reasons),
            normalized=norm,
            checks_run=v.checks_run,
            validator_version=v.validator_version,
            validator_hash=v.validator_hash,
        )
        sp.commit()
        return vid
    except WriterUniqueViolation:
        sp.rollback()
        row = conn.execute(
            text("SELECT validation_id FROM baaki.validation_result WHERE proposal_id = :p"), {"p": v.proposal_id}
        ).scalar_one()
        return UUID(str(row))


def _existing_decision(
    conn: Connection, decision: ExecutableDecision | NonExecutableDecision, ctx: DecisionContext
) -> AlreadyDecided:
    """Resolve the row that won the per-day unique (§5.8): linked ⇒ validation_id; unlinked ⇒ (invoice, day, arm)."""
    if ctx.validation_id is not None:
        row = conn.execute(
            text("SELECT decision_id FROM baaki.policy_decision WHERE validation_id = :v AND business_date = :d"),
            {"v": ctx.validation_id, "d": decision.business_date},
        ).one()
    else:
        row = conn.execute(
            text(
                "SELECT decision_id FROM baaki.policy_decision WHERE invoice_id = :i AND business_date = :d "
                "AND arm = CAST(:a AS baaki.arm) AND proposal_id IS NULL"
            ),
            {"i": decision.invoice_id, "d": decision.business_date, "a": str(decision.arm)},
        ).one()
    existing_id = UUID(str(row[0]))
    action = conn.execute(
        text("SELECT action_id FROM baaki.recovery_action WHERE decision_id = :d"), {"d": existing_id}
    ).scalar_one_or_none()
    return AlreadyDecided(
        existing_id, UUID(str(action)) if action is not None else None, decision.account_id, decision.business_date, ()
    )


def run_decision_pipeline(
    engine: Engine,
    *,
    arm: Arm,
    account_id: UUID,
    as_of: datetime,
    ruleset: Ruleset,
    proposals: Sequence[tuple[AgentProposal, str]] = (),
    inbound_text: str | None = None,
    inbound_contact_id: UUID | None = None,
) -> Ineligible | Decided | AlreadyDecided:
    """validate → decide → create action, one transaction (T2, READ COMMITTED); one re-assembly on a stale snapshot."""
    facts = assemble_account_facts(engine, account_id, as_of, ruleset)
    try:
        return _run_once(
            engine,
            facts,
            arm=arm,
            as_of=as_of,
            ruleset=ruleset,
            proposals=proposals,
            inbound_text=inbound_text,
            inbound_contact_id=inbound_contact_id,
        )
    except WriterRefused as e:
        if e.code not in RETRY_CODES:
            raise
    facts = assemble_account_facts(engine, account_id, as_of, ruleset)  # one re-assembly, then fail closed
    try:
        return _run_once(
            engine,
            facts,
            arm=arm,
            as_of=as_of,
            ruleset=ruleset,
            proposals=proposals,
            inbound_text=inbound_text,
            inbound_contact_id=inbound_contact_id,
        )
    except WriterRefused as e:
        if e.code in RETRY_CODES:
            raise PipelineRetryExhausted(e.code) from e
        raise


def _run_once(
    engine: Engine,
    facts: AccountFacts,
    *,
    arm: Arm,
    as_of: datetime,
    ruleset: Ruleset,
    proposals: Sequence[tuple[AgentProposal, str]],
    inbound_text: str | None,
    inbound_contact_id: UUID | None,
) -> Ineligible | Decided | AlreadyDecided:
    with engine.connect() as conn:  # READ COMMITTED (§5.8)
        validation_ids: list[UUID] = []
        bundles: dict[ProposalKind, ValidationBundle] = {}
        if arm is Arm.TREATMENT:
            for proposal, source_text in proposals:
                bundle = validate(
                    ValidationInput(proposal=proposal, source_text=source_text, facts=facts), ruleset, now=as_of
                )
                vid = _record_validation_or_existing(conn, bundle.result)
                validation_ids.append(vid)
                bundles[proposal.kind] = bundle
                if proposal.kind is ProposalKind.INTERPRETATION:
                    apply_inbound_opt_out(
                        conn, bundle.result.model_copy(update={"validation_id": vid}), contact_id=inbound_contact_id
                    )
        elif proposals:
            raise ValueError("only the TREATMENT arm consumes proposals")

        # SC7 — no candidate ⟹ no decision; validations already recorded remain as audit evidence.
        interp_bundle = bundles.get(ProposalKind.INTERPRETATION)
        action_bundle = bundles.get(ProposalKind.ACTION_PROPOSAL)
        target = None
        for b in (action_bundle, interp_bundle):
            if b is not None and b.target_invoice_id is not None:
                target = b.target_invoice_id
                break
        if target is None and facts.candidates:
            target = facts.candidates[0].invoice_id
        if target is None or facts.candidate(target) is None:
            conn.commit()
            return Ineligible(facts.account_id, facts.business_date, "no_candidates", tuple(validation_ids))
        target_c = facts.candidate(target)
        assert target_c is not None

        # Arm strategy → ActionChoice + degradation level (P2-D7)
        interpretation: NormalizedInterpretation | None = None
        if interp_bundle is not None and isinstance(interp_bundle.result.normalized, NormalizedInterpretation):
            interpretation = interp_bundle.result.normalized
        elif inbound_text is not None:
            interpretation = interpret(inbound_text, facts.business_date)
        rejected_ambiguous = any(b.rejected_ambiguous for b in bundles.values())
        choice: ActionChoice
        if arm is Arm.CONTROL:
            choice, level = control.choose(facts, target_c, ruleset), DegradationLevel.L2
        elif arm is Arm.RULES_ONLY:
            choice, level = rules_only.choose(facts, target_c, ruleset, interpretation), DegradationLevel.L1
        else:
            l0 = None
            if (
                action_bundle is not None
                and action_bundle.result.outcome is ValidationOutcome.PASS
                and isinstance(action_bundle.result.normalized, NormalizedActionProposal)
            ):
                l0 = treatment.choose(action_bundle.result.normalized, ruleset)
            if l0 is not None:
                choice, level = l0, DegradationLevel.L0
            else:
                choice, level = rules_only.choose(facts, target_c, ruleset, interpretation), DegradationLevel.L1

        snapshot = build_snapshot(facts, target, ruleset)
        action_id = new_id()
        linked = action_bundle.result if action_bundle is not None else None  # only TREATMENT ever has one
        ctx = DecisionContext(
            trace_id=linked.trace_id if linked is not None else new_id(),
            arm=arm,
            degradation_level=level,
            proposal_id=linked.proposal_id if linked is not None else None,
            validation_id=validation_ids[list(bundles).index(ProposalKind.ACTION_PROPOSAL)]
            if linked is not None
            else None,
            business_date=facts.business_date,
            rejected_ambiguous=rejected_ambiguous,
            action_id=action_id,
        )
        decision = decide(choice, snapshot, ruleset, ctx, org_timezone=facts.timezone)
        # savepoint: a duplicate-replay refusal must not abort the validations recorded in this transaction
        sp = conn.begin_nested()
        try:
            decision_id = record_policy_decision(
                conn,
                decision,
                candidate_invoice_ids=facts.candidate_ids,
                trace_id=ctx.trace_id,
                account_id=facts.account_id,
                business_date=facts.business_date,
            )
            sp.commit()
        except WriterUniqueViolation as e:
            if not any(f'constraint "{c}"' in e.detail for c in DUPLICATE_DECISION_CONSTRAINTS):
                raise  # an unrelated uniqueness failure is never "already decided"
            sp.rollback()
            existing = _existing_decision(conn, decision, ctx)
            conn.commit()
            return AlreadyDecided(
                existing.decision_id,
                existing.action_id,
                existing.account_id,
                existing.business_date,
                tuple(validation_ids),
            )
        created_action_id: UUID | None = None
        superseded = False
        if isinstance(decision, ExecutableDecision):
            key = idempotency_key(
                decision.invoice_id,
                decision.action_type,
                canonical_payload_hash(decision.canonical_payload.model_dump(mode="json")),
                decision.business_date,
                decision.arm,
            )
            ra = RecoveryAction.from_decision(decision, as_of, as_of + ACTION_TTL, key, action_id=action_id)
            res = create_recovery_action(conn, ra, outbox_id=new_id())
            created_action_id, superseded = res.action_id, res.superseded
        conn.commit()
        return Decided(decision, decision_id, created_action_id, superseded, tuple(validation_ids), level)
