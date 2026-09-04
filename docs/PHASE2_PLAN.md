# Phase 2 Plan — Domain + Deterministic Policy Kernel

**Status:** PHASE 2 IMPLEMENTED — awaiting final verification and commit approval
**Architecture basis:** `docs/ARCHITECTURE.md` **v3.3.2** (v3.3.1 + documentation-only §5.3/§6.3 reconciliation, §17.0.0) (frozen source of truth; Phase 2 decisions P2-D1…P2-D11 locked in §18.1.1). Where this plan and the
architecture disagree, the plan is wrong.
**Phase 1 baseline:** `b10ebe05526264377f35f402c7b32f4f50a1b8e8` — untouched by this plan.

---

## 0. Objective and boundary

Phase 2 delivers the **deterministic** half of the spine: contracts, validator, kernel, ruleset, snapshot and
target selection, the three arm strategies (TREATMENT as a *strategy boundary only*), the rules agent, opt-out
authority (W11, W12), template registry seed, and the `validate → decide → create action` pipeline.

**Phase 2 creates authoritative action records only. It executes nothing and delivers nothing.**

| In Phase 2 | NOT in Phase 2 |
|---|---|
| Domain contracts (§6), validator (§4), kernel (§3), ruleset (§5), snapshot + SC1–SC7 (§2), `CONTROL`/`RULES_ONLY`/`TREATMENT` strategies, `rules_agent/` (keyword interpreter, date/amount grammar, decision tree, **restriction detector**), W11/W12 + opt-out metadata + `opt_out_source` enum, template seed, pipeline T2, tests | OpenAI SDK · any provider adapter · `providers/llm/*` · `agent/` runtime · network model calls (all → **P2b**, separately gated) · Razorpay · webhook HTTP receiver · communication delivery · `message`/`restriction_event` tables, W11b, ingestion (→ P4) · action transitions W19a/b, approvals, kill-switch writers (P4) · PTP/dispute tables (P3) · scheduler / daily loop / virtual-clock runner · experiment, simulator, dashboard, UI, demo |

---

## 1. Domain model — see ARCHITECTURE §1.3 (snapshot), §6.4B (P2 rows), §6.18/6.18.1

| Entity | P2 use | Authority of P2-relevant fields | Mutability in P2 |
|---|---|---|---|
| `organization` | read `kill_switch`, `timezone` | — | read-only |
| `account` | read; W12 | `opt_out` + new `opt_out_by_role/_source/_note/_at` → W12 (`baaki_ops`) | false → true only |
| `contact` | read; W11/W12 | `opted_out` + new `opted_out_by_role/_source/_note/_at/_validation_id` → W11 (evidence) / W12 (ops); `active` safe | false → true only |
| `invoice` | read | `state`, `due_date`; **`issued_paise` never read by the kernel** | read-only |
| `ledger_entry` / `v_invoice_outstanding` | read | sole money source | read-only |
| `template_registry` | read; seed | catalogue; TPL1–TPL3 | rows by migration only |
| `agent_proposal` | read | validator input | read-only |
| `validation_result` | W08 | validator output | insert-only |
| `policy_decision` | W09 | kernel output | insert-only |
| `recovery_action`, `outbox` | W10 | initial state only | insert-only; **no transitions** |
| `payment_event` | read | `applied_at` = provider-authoritative evidence for §5.6 | **never mutated** |

Invariants added: `OO1` metadata ⟺ opted out · `OO2` `INBOUND_UNSUBSCRIBE ⟹ _validation_id NOT NULL ∧ _by_role='baaki_app'` · `OO3` `HUMAN ⟹ _by_role='baaki_ops'` · `OO4` monotonic · `SN1` snapshot fields derive only from P1 tables/ruleset · `SN2` recorded hashes bind the decision to snapshot and ruleset (I9, P6).

---

## 2. Eligibility, candidates, target (ARCHITECTURE §6.8.3 SC1–SC7, §1.3)

Assembly order: account facts → validator checks 01–12 → `select_target` (SC3) → checks 13–16 against the target → `AccountSnapshot.build()` → kernel.

- Candidates (SC2): `state ≠ PAID ∧ outstanding > 0`, ordered `(days_overdue desc, outstanding desc, invoice_id asc)`.
- Outstanding: `ledger/projection.py` → `v_invoice_outstanding` only.
- Overdue: `business_date − due_date` in `organization.timezone`, floor 0.
- Target (SC3): sole resolved ref ∈ candidates → it; else hint ∈ candidates → it; else first candidate; else `None`.
- **SC7 — no candidates or `None` target ⟹ no snapshot, no decision, no action, no outbox.** Proposal/validation rows remain as audit evidence. Pipeline returns `Ineligible(account_id, business_date, 'no_candidates')`. P13 is not relaxed.

---

## 3. Kernel (ARCHITECTURE §4.2, §4.3, §5, §1.5.1)

`decide(choice: ActionChoice, snapshot: AccountSnapshot, ruleset: Ruleset, ctx: DecisionContext) -> ExecutableDecision | NonExecutableDecision` — pure, no I/O, no clock, no randomness, constructs the decision with `KERNEL_TOKEN`.

- Ladder P0–P14 (15 levels), first match wins, exactly as §4.2. Pressure = `{SEND_REMINDER, SEND_PAYMENT_LINK, PROPOSE_INSTALLMENT_PLAN}`.
- P13 = the §4.3 truth table (bands A/B/C/D — locked constants, §5.4 / P2-D2). Band D discards an `L0` choice → pipeline substitutes the `L1` choice with `degradation_level = L1`. Band C turns any tier ≥ 1 choice into `SUPPRESS`. Band B forces `SEND_PAYMENT_LINK` to `REQUIRE_APPROVAL` (recorded tier 2). Never less human control than the catalogue.
- Payload construction: every money value from `snapshot.outstanding_paise`; installments = deterministic equal split with remainder on the last part; `reason_code`/`assignee_queue` derived per §1.5.1; TPL1–TPL3 checked against `template_catalogue` (P11).
- `matched_rules` = every evaluated level; `blocking_rules` = the blocking level.
- Verdict semantics: `BLOCK`/`DEFER` → `NonExecutableDecision`, no action; `ALLOW`/`REQUIRE_APPROVAL` → `ExecutableDecision` → W10 (`QUEUED` / `PENDING_APPROVAL`). Escalation is an action (tier 2), not a verdict.
- Determinism (P6): identical canonical snapshot + ruleset + choice → identical canonical decision payload; `snapshot_hash`/`policy_hash` **bind** the recorded decision to its inputs.

---

## 4. Recovery policy (per arm; one kernel)

| Concern | Rule (source) |
|---|---|
| Cadence | `CONTROL`: `SEND_REMINDER` iff `days_overdue ∈ control_cadence_days_overdue`, else `SUPPRESS(NO_ELIGIBLE_ACTION)`. `RULES_ONLY` tree: overdue ≥ `link_after` and no link in 24 h → `SEND_PAYMENT_LINK`; overdue ≥ `reminder_after` → `SEND_REMINDER`; intent `REQUEST_INSTALLMENTS` → `PROPOSE_INSTALLMENT_PLAN`; `NEEDS_DOCUMENT`/`WRONG_CONTACT` → `ESCALATE_TO_HUMAN(MANUAL_REVIEW)`; else `SUPPRESS`. `TREATMENT`: the validated call-2 action; fallback to the tree at `L1` |
| Contact caps | P9 on **intent-count** `contacts_7d` / `contacts_invoice_7d` (§1.3, P2-D5); 3 / account, 2 / invoice per 7 d (P2-D2) |
| Quiet hours | P10 `DEFER` to next window open (`09:00 ≤ t < 19:00`, Mon–Sat, Sunday closed, no holidays, `organization.timezone`; P2-D2) |
| Dispute | P5 on `invoice_state = DISPUTED` (P3 adds `open_dispute_ids`) |
| Paid claim | P6 per §5.6 (scope, latest, 72 h, strict `applied_at > claim_at`) |
| PTP | P7 logic against `active_ptp`, which is **`None` in P2** (§5.7); fixture-tested |
| Payment link | P8 against `active_payment_link`, **`None` in P2** (§5.7); fixture-tested |
| Escalation | tier 2 → `REQUIRE_APPROVAL`; approval itself is P4 |
| Opt-out | P2 absolute; W11 (validation evidence), W12 (ops); monotonic; **arm-independent restriction contract** §6.18.1 (detector in P2, W11b/table in P4) |
| Kill switch | P0 + validator check 01; W11 still proceeds under kill switch |

---

## 5. State machines in Phase 2

**5.1 Opt-out (monotonic)** — ARCHITECTURE §6.18: `false → true` via W11 (PASS `UNSUBSCRIBE` validation, same transaction as W08) or W12 (`session_user = 'baaki_ops'`, H17); `true → true` idempotent; `→ false` has no writer and no grant. Forbidden: W11 without a matching validation; W11/W12 by `baaki_agent`/`baaki_sim`; W12 by `baaki_app`; any direct `UPDATE`.

**5.2 Decision pipeline (per invoice-day)** — `UNEVALUATED → SNAPSHOT_BUILT | INELIGIBLE(SC7) → VALIDATED(PASS|REJECT) → DECIDED(verdict) → ACTION_CREATED (executable only)`. `validate → decide → create action` is one transaction (T2, `READ COMMITTED`, §5.8). `INELIGIBLE` writes nothing.

**5.3 RecoveryAction** — creation only (ARCHITECTURE §3.1.1): W10 → `QUEUED` (+outbox) / `PENDING_APPROVAL` / `SUPERSEDED_DUPLICATE`. **Forbidden in P2:** `QUEUED → anything`, `PENDING_APPROVAL → anything`, `SUPERSEDED_DUPLICATE → anything`, any outbox claim, any action from `BLOCK`/`DEFER`. No W19a/W19b exists; no role holds `UPDATE`. Test: after the P2 suite, every action is in its initial state and every `outbox.claimed_at` is `NULL`.

---

## 6. Contracts (new; P1 contracts unchanged)

| Contract | Schema | Producer → Consumer | Serialisation |
|---|---|---|---|
| `InterpretationV1` | `intent: Intent(9)`, `promised_date_raw: str\|None`, `promised_amount_raw: str\|None`, `invoice_refs: list[str]`, `contact_correction: str\|None`, `sentiment: Sentiment(4)`, `confidence: float∈[0,1]`, `evidence: list[{field, quote}]`; frozen/strict/forbid; **no money field**; provider-neutral, offline | (P2b agent) → validator, from `AgentProposal.parsed` | JSONB → model |
| `ActionProposalV1` | `action: ActionType`, `contact_id: UUID\|None`, `channel: Channel`, `template_id: str\|None`, `followup_days: int∈[1,14]\|None`, `rationale: str≤280` (never parsed), `confidence`; no amount/reason/queue | same | same |
| `NormalizedActionProposal` (P2-D3) | `action`, `contact_id`, `channel`, `template_id`, `followup_days`, `effective_confidence` | validator → kernel via `ValidationResult.normalized` (kind `ACTION_PROPOSAL`) | JSONB |
| `Ruleset` | §5.4 keys (locked values, P2-D2) + `policy_version`, `policy_hash`; frozen; fail-closed loader (§5.5); TOML via stdlib (P2-D1) | `policy/ruleset.py` → kernel/validator | TOML bytes → model |
| `ActionChoice` | `action`, `contact_id\|None`, `channel\|None`, `template_id\|None`, `followup_days\|None`, `existing_link_ref\|None`, `confidence\|None`, `origin: L0\|L1\|L2` | arm strategy → kernel | in-memory |
| `DecisionContext` | `trace_id`, `arm`, `degradation_level`, `proposal_id\|None`, `validation_id\|None`, `business_date`, `rejected_ambiguous: bool` | pipeline → kernel | in-memory |
| `CandidateInvoice` | `invoice_id`, `invoice_number`, `state`, `due_date`, `days_overdue`, `outstanding_paise: Paise` | assembler → `select_target`, check 10 | in-memory |
| `ValidationInput` | `proposal`, `source_text` (`sha256 = proposal.input_hash`, P2-D4), account facts | pipeline → validator | in-memory |
| `RestrictionEvent` (§6.18.1) | `restriction_event_id`, `contact_id`, `account_id`, `message_id`, `raw_body_hash`, `matched_pattern_id`, `matcher_version`, `detected_at`, `created_by_role` | P4 ingestion → W11b (P4). **P2: contract + detector only** | JSONB/columns (P4) |

---

## 7. Database changes

| Migration | Contents |
|---|---|
| `0004_opt_out_metadata` | `opt_out_source` enum (`INBOUND_UNSUBSCRIBE`, `INBOUND_RESTRICTION`, `HUMAN`); `contact` += `opted_out_by_role`, `opted_out_source`, `opted_out_note`, `opted_out_validation_id FK`, `opted_out_at`; `account` += `opt_out_by_role`, `opt_out_source`, `opt_out_note`, `opt_out_at`; CHECKs OO1–OO3 |
| `0005_opt_out_writers` | **W11** `opt_out_contact_from_evidence(p_contact_id, p_validation_id)`; **W12** `opt_out_by_operator(p_account_id, p_contact_id, p_actor_note)` with **H17 `session_user='baaki_ops'` first statement**; both hardened H1–H19; `REVOKE FROM PUBLIC`; grants W11 → `baaki_app`, W12 → `baaki_ops` only |
| `0006_seed_templates` | the six registry rows of ARCHITECTURE §6.14 (P2-D10), `body_hash = sha256(config/templates/<id>.txt)` |

Transactions: **T2** `W08 → [W11] → W09 → W10`, one transaction at **`READ COMMITTED`** (deliberate, §5.8: `FOR SHARE` + CP5 + uniques guarantee correctness); assembly in a separate `REPEATABLE READ` read-only transaction; one automatic re-assembly on `cp5_amount_mismatch`. W12 in its own operator transaction.
Idempotency: existing uniques; §3.4 key; opt-out idempotent by state.
Roles: only +EXECUTE W11 (`app`), W12 (`ops`). `baaki_ops` gains its first capability → AC1/AC2/AC11/AC13/O1–O5 go live.
Order: `0004 → 0005 → 0006`.

---

## 8. Failure semantics — ARCHITECTURE §12.2 P2 rows

Duplicate events → uniques, "already decided" · stale snapshot → CP5 mismatch → one retry → `PipelineRetryExhausted` · concurrent decisions → one wins · invalid input → fail-closed `REJECT` → L1 · **ruleset malformed/missing/non-monotone/bad window/bad tz → `RulesetInvalid`, no decisions** · **ruleset hash mismatch → stale, never acted on** · **snapshot/canonicalisation hash mismatch → `ContractViolation`** · **invalid `ActionChoice` → `BLOCK`/`ContractViolation`** · **stale candidate set → retry once** · **cross-account ref → `INVOICE_REF_UNRESOLVED`** · **SC7 → no rows** · **duplicate pipeline replay → existing rows** · **retry after rollback → fresh snapshot, at most one retry**. All with no partial financial state (H10/H11).

---

## 9. Testing

Unit (each of the **16 checks** positive/negative; each of the **20 reasons** by a named fixture; date/amount grammars; `select_target`; each of the **15 ladder levels**; §1.5.1 derivations; §4.3 truth table row-by-row; quiet-hours boundaries incl. Sunday, `09:00` inclusive, `19:00` exclusive, DST; CONTROL cadence; RULES_ONLY tree; restriction detector) · Property (`authority_tier(final) <= catalogue_tier(requested_action)` 10⁴; kernel determinism over random snapshots; ruleset monotonicity; payload money = snapshot outstanding; installment sum) · Invariant (CP5 end-to-end; hash binding; SC7 zero rows) · State machine (opt-out monotonic, OO1–OO3; pipeline outcomes; initial-state-only actions) · Concurrency (two pipelines one invoice-day; concurrent W11) · Security/red-team (AC1, AC2, **AC11**, AC13, O1–O5; agent cannot execute W11/W12; policy import graph; kernel imports no DB; kernel never reads `issued_paise`; `ClaimedPaise` unassignable) · Adversarial (cross-account ref; confidence 1.0 tier-2 auto; injected `amount_paise`; evidence not in source; `UNSUBSCRIBE` under kill switch; wrong-channel template; `PAID` invoice) · Regression (P1 383/383 on PG 16; `0001–0003` untouched; privilege matrices unchanged except two EXECUTE grants).

---

## 10. Judge attack surface — ARCHITECTURE §14 + P2 rows

Forged rupee (no money field; A3; `ClaimedPaise`; CP5) · unauthorized transition (none exists; no `UPDATE`) · opt-out violation (P2 absolute; monotonic; arm-independent contract) · dispute/paid-claim pursuit (P5/P6) · PTP violation (P7 fixtures) · excessive contact (P9 intent-count) · kill-switch bypass (P0 + check 01) · races (uniques + T2 + one retry) · idempotency abuse (IK1) · LLM injection (`rationale` never parsed; ids ∈ supplied sets; account-scoped refs; CP6) · privilege escalation (W12 ops-only + H17; AC11).

---

## 11. Implementation order (after approval)

1. Approval of this plan against architecture v3.3.1 (all Phase 2 decisions P2-D1…P2-D11 are locked; none remains open).
2. `config/policy.v1.toml` + `policy/ruleset.py` (fail-closed loader, hash) + tests.
3. `ledger/projection.py`, `ledger/invariants.py` (read-only) + tests.
4. Contracts (§6) + contract tests.
5. `policy/validate/` (normalisers, 16-check ladder) + unit/property tests (20 reason fixtures).
6. `policy/kernel/target.py` (SC3), `policy/snapshot.py` (assembler, SC7) + tests.
7. `policy/kernel/decide.py` (ladder, §4.3 table, payloads, derivations) + property/determinism tests.
8. `rules_agent/` (keyword interpreter, grammars, tree, **restriction detector**), `policy/arms/{control,rules_only,treatment}.py`.
9. Migrations `0004–0006`; `db/writers/optout_evidence.py`, `db/writers/operator.py`; opt-out + red-team tests.
10. `pipeline/run.py` (T2 orchestrator — its own package `src/baaki/pipeline/`, ARCHITECTURE §5.3; not inside `policy/`) + end-to-end, concurrency, rollback, SC7 tests; `arch/test_phase2_boundary`.
11. Full verification on PG 16 (P1 + P2 suites, ruff, mypy --strict, `uv lock --check`).

## 12. Commit strategy

One commit after DoD passes: `feat(policy): implement phase 2 deterministic policy kernel`. No push until approved; no intermediate commits.

---

## 13. Definition of Done (Phase 2)

| # | Check | Verification |
|---|---|---|
| 1 | `0004–0006` apply on empty PG 16 and reverse; inventory = P1 + 1 enum (3 labels), 9 columns, 2 writers, seed rows | alembic + inventory test |
| 2 | W11/W12 satisfy H1–H19; **W12 asserts `session_user`**; AC11 survives grant misconfiguration | hardening + red-team |
| 3 | EXECUTE matrix = P1 + `{W11: app}`, `{W12: ops}`; nothing else changed | privilege tests |
| 4 | Opt-out monotonic; OO1–OO4; O1–O5, AC1, AC2, AC13 | tests |
| 5 | **16 checks** each with positive + negative coverage; **20 reasons** each with a named fixture; fail-closed on un-evaluable input | ladder tests |
| 6 | Kernel pure (import graph), deterministic (identical inputs → identical canonical payload), 15-level total order, §4.3 truth table reproduced, `authority_tier(final) <= catalogue_tier(requested_action)` 10⁴ | arch + property |
| 7 | Every executable decision's money equals the view at write (CP5/CP2) | end-to-end |
| 8 | SC1–SC7: target ∈ candidates; account-scoped resolution; **empty candidates ⟹ zero decision rows** | tests |
| 9 | Three arms through one kernel; `degradation_level` per P2-D7; P11 holds | arm tests |
| 10 | T2 atomic at `READ COMMITTED`; injected W10 failure leaves nothing | rollback tests |
| 11 | Ruleset: hash = sha256(bytes) on every decision; every §5.5 fault → `RulesetInvalid`; frozen | tests |
| 12 | Templates satisfy TPL4; kernel blocks incompatible templates | tests |
| 13 | Restriction detector pure/versioned; `RestrictionEvent` contract validates; W11b/`restriction_event` absent | tests |
| 14 | **Phase 2 executes nothing**: all actions in initial state, outbox unclaimed, no vendor SDK import, no sockets | `arch/test_phase2_boundary` |
| 15 | P1 383/383 green; ruff; mypy --strict; `uv lock --check`; **no new dependency** | commands |

---

## Decisions

All Phase 2 decisions are locked in ARCHITECTURE §18.1.1 (P2-D1…P2-D11); §18.2 is `NONE`. Nothing in this plan
requires Claude to invent a policy value or semantic during implementation.

**IMPLEMENTATION PERFORMED: NO**
