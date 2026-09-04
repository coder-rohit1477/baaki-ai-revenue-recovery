# Baaki — AI Revenue Recovery
## Architecture Specification — v3.2.2 (Reconciled · Source of Truth)

**Track 03 — AI Revenue Recovery (Razorpay AI Buildathon)**
**Status:** Reconciled architecture. **This document governs implementation.** Any Phase plan
must conform to it; where a plan and this document disagree, the plan is wrong.
**Supersedes:** v1 (2026-09-03), v2, v3, v3.1, v3.2, v3.2.1 (2026-09-04). v3.2.1 added the GAP-1 enum members
(§1.5.1). v3.2.2 clarifies the idempotency-key uniqueness invariant to describe the implemented partial unique
index (§17.0). No other change.
**Date:** 2026-09-04
**Repository state at this revision:** this file only. No code, dependencies, database, or git.

---

## How to Read This Document

| Marker | Meaning |
|---|---|
| **[DD]** | Design decision. Locked. |
| **[IMPL]** | Implementation detail. May change during build without review. |
| **[FUTURE]** | Explicitly not built. Recorded so it is not re-litigated. |
| **[ASSUME]** | Unverified. Listed in §16 with its fallback. |
| **[P1]…[P5]** | Phase in which the item is built (§13). |
| **W01…W25** | Writer-function ids (§6.6; 29 functions — some split a/b by authority class). Every authority-sensitive mutation names one. |

**No Razorpay API behaviour has been verified against Razorpay documentation** — this environment
has no network access. Every provider claim is `[ASSUME]`-tagged; §9.7 defines the safe degradation.

### Section map

| Topic | § | Topic | § |
|---|---|---|---|
| Authority model & table classes | 6.1 | Ledger authority | 6.11 |
| PostgreSQL roles | 6.2 | Ledger writer restrictions & over-credit | 6.12 |
| Table privileges | 6.3 | Outstanding projection | 6.13 |
| Column / update capabilities (full matrix) | 6.4 | Template registry + compatibility | 6.14 |
| SECURITY DEFINER hardening | 6.5 | Tier-3 forbidden capabilities F1–F7 | 6.15 |
| Writer catalogue (W01–W25) + transaction contract | 6.6 | Red-team attack surface | 6.16, 14 |
| **Trusted actor authority** | **6.22** | **Sweep-run provenance** | **6.23** |
| PolicyDecision partition | 1.4, 6.7 | What PostgreSQL / application / tests prove | 6.17 |
| Proposal → validation → decision linkage | 6.8 | Opt-out authority | 6.18 |
| CanonicalPayload restrictions | 1.5, 6.9 | Kill-switch authority | 6.19 |
| RecoveryAction creation boundary | 1.6, 6.10 | PaymentEvent provenance | 6.20 |
| RecoveryAction state machine (11 states) | 3 | Reattribution authority | 6.21 |
| Phase boundaries & object counts | 13 | Definition of Done | 15 |

---

## 0. Thesis, Spine, and Non-Negotiable Invariants

### 0.1 Thesis

> Baaki proves incremental revenue recovery while keeping financial authority behind
> deterministic controls that the LLM cannot bypass.

1. **Incremental** — measured against a real baseline with a pre-registered endpoint, CI, n, MDE.
2. **Cannot bypass** — one deterministic path to money, enforced at type, import, database-constraint
   and database-role levels simultaneously.

### 0.2 The Spine

```
  untrusted text (buyer reply)
        │
        ▼
┌──────────────────────┐  JSON only · closed enums · no tools · no network
│  MODEL LAYER agent/  │  INTERPRETS / PROPOSES         connects as baaki_agent:
└──────────┬───────────┘                                 no privilege on any F- or D-class table
           │ AgentProposal  (immutable · scope only · never authority)
           ▼
┌──────────────────────┐  pure · no I/O · raw spans → typed values, or nothing
│  VALIDATOR           │  NORMALISES / REJECTS
└──────────┬───────────┘
           │ ValidationResult  (immutable · claim evidence, ClaimedPaise)
           ▼
┌──────────────────────┐  pure · versioned · replayable · all money from the ledger
│  MONEY SAFETY KERNEL │  DECIDES
└──────────┬───────────┘
           │ PolicyDecision  (immutable · Executable ⊕ NonExecutable)
           ▼
┌──────────────────────┐  accepts ExecutableDecision only · every write is a
│  EXECUTOR actions/   │  ACTS      SECURITY DEFINER writer · no direct DML
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  RAZORPAY (test)     │  CONFIRMS  — sole authority on payment status
└──────────┬───────────┘
           │ verified webhook_event  OR  sweep_run   →  payment_event  (provenance-bound)
           ▼
┌──────────────────────┐  append-only · double-entry · BIGINT paise
│  LEDGER              │  RECORDS   — written only by 4 narrow ledger writers
└──────────────────────┘
```

### 0.3 The Eleven Non-Negotiable Invariants **[DD]**

| # | Invariant | Enforced by |
|---|---|---|
| **I1** | An `AgentProposal` is never an input to the executor, ledger, or provider adapter. | Type · import graph · FK |
| **I2** | Every monetary value in an executed action is kernel-computed from the ledger, never copied from model output. | No money field in model schemas · denylist · `ClaimedPaise` ≠ `Paise` |
| **I3** | Every `RecoveryAction` references exactly one `PolicyDecision` with verdict ∈ `{ALLOW, REQUIRE_APPROVAL}`. | `NOT NULL UNIQUE` FK · allowlist trigger · W10 |
| **I4** | Model confidence may only *reduce* authority. | `min()` guard · property test |
| **I5** | `outstanding_paise` is a projection over the ledger; no such stored column exists. | `v_invoice_outstanding` |
| **I6** | Payment status originates only from provider-authoritative evidence — never model, debtor, caller, or amount matching. | `payment_event` requires a verified `webhook_event` or a `sweep_run`; **W04 extracts all financial fields from evidence bytes — it has no financial parameters**; ledger writers take an event id, never an amount |
| **I7** | Every financial effect is idempotent at four independent boundaries. | Four `UNIQUE` constraints (§8.3) |
| **I8** | The debtor simulator cannot observe arm assignment. | `baaki_sim` has no grant on `experiment_assignment` · import graph |
| **I9** | Every decision records the ruleset hash and snapshot hash used. | `NOT NULL` · replay test |
| **I10** | **No application role holds `INSERT`/`UPDATE`/`DELETE` on any F-class or D-class table (§6.1). On M-class tables, application roles may `INSERT` and may `UPDATE` only Safe columns (§6.4A). C-class tables have no runtime DML. Every authority-sensitive mutation is a named writer W01–W25.** | Role grants · `information_schema` tests · red-team matrix |
| **I11** | **Human-only operations are authorised by the connection role (`baaki_ops`, verified as `session_user` inside the writer), never by a caller-supplied actor string (§6.22).** | `EXECUTE` grants · H17 · AC1–AC14 |

---

# 1. Cross-Boundary Contracts

Conventions: `TIMESTAMPTZ` UTC; money `BIGINT` paise, never float/`NUMERIC`/`MONEY` **[DD]**;
`UUID v7` **[IMPL]**; Pydantic v2 `frozen=True, strict=True, extra='forbid'`.

| Money type | Meaning | May become a payment amount? |
|---|---|---|
| `Paise` | Deterministic financial authority — from the ledger projection or a `payment_event` | Yes |
| `ClaimedPaise` | What a debtor *said*, parsed deterministically from a model span | **Never.** Comparison only; no arithmetic with `Paise`; no coercion |

## 1.1 `AgentProposal` — raw model output; **scope, not authority**

**Creator:** `agent/` via **W07** (`baaki_agent`). **Consumer:** `policy/validate/` only.
**Prohibited consumers:** `actions/`, `ledger/`, `reconcile/`, `providers/razorpay/`, `experiment/`.

| Field | Type | Null | Notes |
|---|---|---|---|
| `proposal_id` | UUID | NO | **PK — the only identity** |
| `trace_id`, `account_id` | UUID | NO | |
| `kind` | `proposal_kind` | NO | `INTERPRETATION` \| `ACTION_PROPOSAL` |
| `invoice_id` | UUID | YES | **Scope hint only** (§6.8.3). NULL = account-level |
| `business_date` | DATE | NO | |
| `arm` | `arm` | NO | CHECK `= 'TREATMENT'` |
| `provider`, `model_id`, `prompt_template_id`, `schema_version` | TEXT | NO | |
| `prompt_hash`, `input_hash` | CHAR(64) | NO | |
| `raw_response` | `RawJson` | NO | **Opaque** (A6) |
| `parsed` | JSONB | YES | NULL ⟺ parse failed |
| `parse_status` | `parse_status` | NO | |
| `confidence` | NUMERIC(4,3) | YES | |
| `evidence` | JSONB | NO | `[{field, quote}]` |
| `latency_ms`, `created_at` | | NO | |

**Invariants:** `A1` immutable · `A2` `parse_status=OK ⟺ parsed NOT NULL` · `A3` `parsed` has no
money key (`amount, amount_paise, total, balance, discount, interest, fee, outstanding, due_amount,
settle*, waiver, credit`) — Pydantic + W07 + CHECK · `A4` no typed date in `parsed` · `A5`
`arm = TREATMENT` · **`A6` `raw_response` is opaque audit evidence**: no component in `policy/`,
`actions/`, `ledger/`, `reconcile/`, `providers/` reads a semantic field from it; typed `RawJson`
so access needs an explicit unwrap the AST test detects · **`A7` `invoice_id` and `invoice_refs`
are scope hints**: nothing downstream treats them as authoritative (§6.8.3).

## 1.2 `ValidationResult`

**Creator:** `policy/validate/` via **W08**. **Consumer:** `policy/kernel/`.

| Field | Type | Null | Notes |
|---|---|---|---|
| `validation_id` | UUID | NO | PK |
| `proposal_id` | UUID | NO | FK → `agent_proposal`, **UNIQUE** (1:1) |
| `trace_id`, `account_id`, `business_date` | | NO | **Derived by W08 from the proposal row — not caller-supplied** |
| `outcome` | `validation_outcome` | NO | |
| `rejection_reasons` | `rejection_reason[]` | NO | Non-empty ⟺ `REJECT` |
| `normalized` | JSONB | YES | NOT NULL ⟺ `PASS` |
| `checks_run` | JSONB | NO | |
| `validator_version`, `validator_hash` | | NO | |
| `created_at` | | NO | |

`normalized` (INTERPRETATION): `{ intent, promised_date: DATE|null, promised_paise: ClaimedPaise|null,
invoice_ids: UUID[] (resolved ⊆ account's invoices, §6.8.3), contact_id: UUID|null, effective_confidence }`

**Invariants:** `V1` immutable · `V2`/`V3` PASS/REJECT biconditionals · `V4` `checks_run` complete
to first HARD failure · `V5` `effective_confidence ≤ model_confidence` · `V6` pure · **`V7`
normalised money is `ClaimedPaise` — claim evidence, never authority** · **`V8` `trace_id`,
`account_id`, `business_date` equal the proposal's — guaranteed because W08 copies them**.

## 1.3 `AccountSnapshot` — the kernel's only view **[DD]**

`snapshot_hash` · `as_of` · `business_date` · `account_id` · **`candidate_invoice_ids: UUID[]`**
(open invoices of the account, deterministic — §6.8.3) · `target_invoice_id` (kernel-selected) ·
`outstanding_paise: Paise` **(from `v_invoice_outstanding` only)** · `invoice_state` ·
`days_overdue` · `opt_out` · `kill_switch` · `ledger_invariant_ok` · `open_dispute_ids` ·
`unverified_paid_claim_until` · `active_ptp {…, promised_paise: ClaimedPaise}` ·
`active_payment_link {…, amount_paise: Paise}` · `contacts_7d` · `contacts_invoice_7d` ·
`last_contact_at` · `contactable_contact_ids` (`active ∧ ¬opted_out`) · `template_catalogue`.

`S1` assembled in one `REPEATABLE READ` transaction · `S2` `outstanding_paise` has no other source ·
`S3` hash covers every field.

## 1.4 `PolicyDecision` — the only financial authority object

**Creator:** `policy/kernel/` exclusively (token, §6.10) via **W09**. **Consumer:** `actions/`,
approval UI, audit viewer. `baaki_agent` has no `SELECT`.

```
EXECUTABLE     = {ALLOW, REQUIRE_APPROVAL}   → action_type, canonical_payload REQUIRED
NON_EXECUTABLE = {BLOCK, DEFER}              → action_type, canonical_payload NULL
PolicyDecision = ExecutableDecision | NonExecutableDecision   (discriminated on verdict)
```

| Field | Type | Null | Notes |
|---|---|---|---|
| `decision_id` | UUID | NO | PK |
| `proposal_id` | UUID | YES | FK → `agent_proposal`. NULL for `CONTROL`/`RULES_ONLY` |
| `validation_id` | UUID | YES | FK → `validation_result`. NULL ⟺ `proposal_id` NULL |
| `trace_id`, `account_id`, `business_date` | | NO | **When `proposal_id` NOT NULL: derived by W09 from the proposal — not caller-supplied.** Otherwise caller-supplied |
| `invoice_id` | UUID | NO | Must belong to `account_id` (trigger) and ∈ `candidate_invoice_ids` (W09) |
| `arm` | `arm` | NO | |
| `verdict` | `verdict` | NO | |
| `tier` | SMALLINT | NO | CHECK `IN (0,1,2)` |
| `action_type` | `action_type` | YES | NOT NULL ⟺ executable |
| `canonical_payload` | JSONB | YES | NOT NULL ⟺ executable |
| `defer_until` | TIMESTAMPTZ | YES | NOT NULL ⟺ `DEFER` |
| `matched_rules` | TEXT[] | NO | |
| `blocking_rules` | JSONB | NO | Non-empty ⟺ `BLOCK` |
| `effective_confidence` | NUMERIC(4,3) | YES | |
| `policy_version`, `kernel_version` | TEXT | NO | |
| `policy_hash`, `snapshot_hash` | CHAR(64) | NO | |
| `degradation_level` | `degradation_level` | NO | |
| `decided_at` | | NO | |

**Invariants P1–P13**

| # | Statement | Enforced |
|---|---|---|
| P1 | Immutable | Frozen · no `UPDATE` grant |
| P2 | `BLOCK ⟹ blocking_rules ≠ []` | Pydantic · CHECK · W09 |
| P3a | `EXECUTABLE ⟹ action_type ∧ canonical_payload NOT NULL` | Union · CHECK · W09 |
| P3b | `NON_EXECUTABLE ⟹ action_type ∧ canonical_payload NULL` | Union · CHECK · W09 |
| P4 | No payload money value traceable to `AgentProposal` or `normalized` | A3 · V7 |
| P5 | `tier=2 ⟹ REQUIRE_APPROVAL` | CHECK · W09 |
| P6 | Replayable byte-for-byte from `(validation, snapshot, policy_hash)` | Property test [P2] |
| P7 | `arm ∈ {CONTROL, RULES_ONLY} ⟹ proposal_id NULL` | CHECK |
| P8 | `DEFER ⟺ defer_until NOT NULL` | Union · CHECK |
| **P9** | **Non-executable decision never produces a `RecoveryAction`** | `from_decision` type · allowlist trigger · W10 |
| P10 | `proposal_id NOT NULL ⟹ validation.proposal_id = proposal_id` and `trace_id`, `account_id`, `business_date` equal the proposal's | W09 derives · `trg_decision_linkage` verifies |
| P11 | `validation.outcome = REJECT ⟹ degradation_level ≠ L0` | W09 |
| P12 | Executable payload with `template_id` satisfies TPL1–TPL5 (§6.14) | Kernel · W09 · FK |
| **P13** | **`invoice_id` belongs to `account_id` and ∈ `snapshot.candidate_invoice_ids`; model-supplied invoice references never select it directly (§6.8.3)** | `trg_decision_linkage` (account match) · W09 (candidate set) |

## 1.5 `CanonicalPayload`

| Variant | Fields |
|---|---|
| `SuppressPayload` | `reason_code: SuppressReason` |
| `ScheduleFollowupPayload` | `followup_date: date` |
| `RequestDisputeDetailsPayload` | `contact_id`, `channel: Channel`, `template_id: TemplateId` |
| `SendReminderPayload` | `contact_id`, `channel`, `template_id`, `existing_link_ref: str\|None` |
| `SendPaymentLinkPayload` | **`amount_paise: Paise`**, `contact_id`, `channel`, `template_id`, `expires_at`, `notes: LinkNotes` |
| `ProposeInstallmentPlanPayload` | `parts: list[{amount_paise: Paise, due_date}]`, `contact_id`, `channel`, `template_id` |
| `EscalateToHumanPayload` | `reason_code: EscalationReason`, `assignee_queue: AssigneeQueue` |

`LinkNotes = {invoice_id, action_id, trace_id}`. **CP1** all money `Paise` · **CP2** installment
parts sum to outstanding · **CP3** no free text the executor parses (enums + FK) · **CP4** no variant
for any F1–F7 capability · **CP5** `amount_paise = outstanding_paise` at decision time · **CP6**
`reason_code` and `assignee_queue` are **kernel-derived** per §1.5.1 — the proposer (call 2, §7.3)
has no such fields and cannot influence them.

### 1.5.1 Closed identifier values — `suppress_reason`, `escalation_reason`, `assignee_queue` **[DD]**

Each value below has a named consumer in this document. No value introduces a capability that does
not already exist. Derivation is a pure function of the kernel's precedence evaluation (§4.2) and
the validation outcome, so it is replayable (P6).

**`suppress_reason`** — set by the kernel on `SuppressPayload` whenever the decided action is
`SUPPRESS`. Derivation: the **highest-precedence pressure-blocking condition that holds** in the
snapshot, else `NO_ELIGIBLE_ACTION`.

| Value | Derived when | Consumer |
|---|---|---|
| `DISPUTE_OPEN` | P5 holds | §4.2 P5 permits `SUPPRESS` under an open dispute |
| `PAID_CLAIM_PENDING` | P6 holds | §4.2 P6 permits `SUPPRESS` while an unverified paid claim is pending |
| `PTP_ACTIVE` | P7 holds | §4.2 P7 permits `SUPPRESS` while a live promise runs |
| `FREQUENCY_CAP` | P9 holds | §4.2 P9 permits `SUPPRESS` when the contact cap is reached |
| `NO_ELIGIBLE_ACTION` | none of P5–P9 holds and `SUPPRESS` is the decided action | `CONTROL` on a non-cadence day (§10.2); `RULES_ONLY` tree or proposer choosing "do nothing today" (Appendix A) |

Quiet hours (P10) is **not** a suppress reason: its verdict is `DEFER`, and a `SUPPRESS` decided
during quiet hours takes its reason from the rows above.

**`escalation_reason`** — set by the kernel on `EscalateToHumanPayload`. Derivation in order:

| Value | Derived when | Consumer |
|---|---|---|
| `DISPUTE_UNRESOLVED` | P5 holds | §4.2 P5 permits `ESCALATE_TO_HUMAN` under an open dispute; resolution is W15 |
| `PAID_CLAIM_UNVERIFIED` | P6 holds | §4.2 P6 permits `ESCALATE_TO_HUMAN` while a paid claim is unverified |
| `AMBIGUOUS_INTERPRETATION` | the day's validation returned `REJECT` with a reason in `{INVOICE_REF_UNRESOLVED, DATE_UNPARSEABLE, DATE_AMBIGUOUS, AMOUNT_UNPARSEABLE, AMOUNT_AMBIGUOUS}` and the L1 path decides escalation | §4.1 "09–12 → human queue, no PTP"; §4.4 "ambiguity is rejection … → human queue" |
| `MANUAL_REVIEW` | none of the above and `ESCALATE_TO_HUMAN` is the decided action | Proposer or `RULES_ONLY` tree selecting escalation without a ladder condition (e.g. `NEEDS_DOCUMENT`, `WRONG_CONTACT` intents, which have no automated action) |

**`assignee_queue`** — set by the kernel; a pure function of `escalation_reason`:

| Value | Derived when | Consumer |
|---|---|---|
| `DISPUTES` | `escalation_reason = DISPUTE_UNRESOLVED` | The distinct human dispute-resolution path (W14b/W15, §18.1 D1) |
| `COLLECTIONS` | every other `escalation_reason` | The single AR-analyst persona (Appendix A) operating as `baaki_ops` |

W09 casts both fields to their enums and additionally asserts the `reason_code → assignee_queue`
mapping above (`RAISE queue_reason_mismatch`); see §6.9.

## 1.6 `RecoveryAction`

**Creator:** `actions/` via `from_decision` then **W10**. **Consumer:** executor [P4], UI.

| Field | Null | Mutability |
|---|---|---|
| `action_id` | NO | Immutable PK |
| **`decision_id`** | **NO** | **Immutable. FK UNIQUE (I3)** |
| `trace_id`, `account_id`, `invoice_id`, `arm`, `action_type` | NO | Immutable; **copied from the decision by W10** |
| `state` | NO | **W19a** (automatic rows) / **W19b** (approval rows) only [P4] |
| `idempotency_key` | NO | Immutable. **Unique among live actions** — `uq_action_idempotency` is a partial unique index `WHERE state <> 'SUPERSEDED_DUPLICATE'` (§3.4 IK1) |
| `attempt_count`, `max_attempts` | NO | W19a |
| `next_attempt_at` | YES | W19a |
| `expires_at` | NO | Immutable |
| `approved_by_role` (TEC), `approved_by_note` (META), `approved_at` | YES | **W19b only** (`baaki_ops`) |
| `provider_ref`, `last_error_code` | YES | W19a |
| `executed_at`, `confirmed_at` | YES | W19a |
| `created_at`, `updated_at` | NO | `updated_at` by W19a/b |

`R1` `decision_id NOT NULL UNIQUE` · `R2` referenced verdict ∈ EXECUTABLE (allowlist trigger + W10)
· `R3` initial `state` = `PENDING_APPROVAL` iff `REQUIRE_APPROVAL`, else `QUEUED` — pure mapping ·
`R4` transitions only per §3 via W19a (automatic) and W19b (approval, `baaki_ops` only); **no role holds `UPDATE`** · `R5` `idempotency_key` collision with a *different* decision ⟹ the original action is returned, **no provider
call is made, and the colliding request is retained as a `SUPERSEDED_DUPLICATE` audit row carrying the same key**
(excluded from the uniqueness set by design, §3.4 IK1) · `R6` `action_type = decision.action_type` (trigger).

**`from_decision(decision: ExecutableDecision, now, expires_at) -> RecoveryAction` is pure [DD]:**
no policy evaluation, no transition, no provider call, no I/O, no clock read; copies ids/arm/type,
derives initial state (R3) and key (§3.4). Persistence is a separate W10 call.

## 1.7 `LedgerEntry`

**Creator:** ledger writers W01/W05/W06/W20 only. No role — and no writer — issues `UPDATE`/`DELETE`.

| Field | Null | Notes |
|---|---|---|
| `entry_id` | NO | PK |
| `txn_id` | NO | Balanced group |
| `trace_id` | YES | |
| `account_code` | NO | Closed set (§6.11); CHECK pattern |
| `invoice_id` | YES | NOT NULL ⟺ `account_code LIKE 'AR:%'` (CHECK) |
| `direction` | NO | `DEBIT` \| `CREDIT` |
| `amount_paise` | NO | CHECK `> 0` |
| `payment_event_id` | YES | FK. NOT NULL ⟺ `source ≠ ISSUANCE` (CHECK) |
| `source` | NO | `ISSUANCE` \| `PAYMENT` \| `REATTRIBUTION` |
| `posted_at` | NO | |

`L1` Σ DEBIT = Σ CREDIT per `txn_id` (writer pre-check + deferred trigger) · `L2` append-only by
grants · **`L3` no correction or reversal capability exists** · `L4` outstanding derived from
`AR:*` only · `L5` outstanding ≥ 0; excess to `BUYER_CREDIT` in the same transaction (§6.12).

## 1.8 `PaymentEvent` — the provider fact the ledger consumes **[DD]**

**Creator:** **W04** only (`baaki_app`, from `reconcile/`). **W04 has no financial parameters**: every ✓ field below is extracted inside the function from `provider_payload_raw`, which must be a literal substring of the referenced evidence's stored raw body. Full contract §6.20.

| Field | Null | Provider-authoritative? | Notes |
|---|---|---|---|
| `payment_event_id` | NO | — | PK |
| `provider` | NO | ✓ | `'razorpay'` |
| `provider_payment_id` | NO | ✓ | **UNIQUE** |
| `amount_paise` | NO | ✓ | CHECK `> 0` |
| `currency` | NO | ✓ | CHECK `= 'INR'` |
| `provider_status` | NO | ✓ | Provider's status string, e.g. `captured` [ASSUME A-R7] |
| `paid_at` | NO | ✓ | Provider timestamp |
| `source` | NO | — | `WEBHOOK` \| `SWEEP` — **derived by W04 from which evidence FK is present** |
| `webhook_event_id` | YES | evidence | FK → `webhook_event` with `signature_ok = true`. NOT NULL ⟺ `source = WEBHOOK` |
| `sweep_run_id` | YES | evidence | FK → `sweep_run`. NOT NULL ⟺ `source = SWEEP` |
| `provider_payload_raw` | NO | ✓ | The payment item's raw JSON text; W04 verifies containment in the evidence raw body |
| `provider_payload_hash` | NO | ✓ | `sha256(provider_payload_raw)` — **computed by W04** |
| `attributed_invoice_id` | YES | Baaki | Set by W04 (from `notes`/`reference_id`) or W20 (human reattribution) |
| `attribution_method` | NO | Baaki | `NOTES_INVOICE_ID` \| `REFERENCE_ACTION_ID` \| `UNATTRIBUTED` \| `HUMAN_REATTRIBUTION` |
| `applied_at` | YES | — | Set by W05/W06/W20 |
| `reattributed_at`, `reattributed_by_role` (TEC), `reattributed_by_note` (META) | YES | — | W20 only, once, `baaki_ops` |
| `created_at` | NO | — | |

Everything marked ✓ is extracted from provider evidence by W04 and immutable. Ledger writers read amount
and identity from this row — **they accept no amount**, and W04 accepts none either.

## 1.9 Supporting contracts

| Contract | Writer | Phase | Key invariant |
|---|---|---|---|
| `WebhookEvent` | W02 | P1 | `UNIQUE(provider, dedupe_key)`; raw body stored before parsing; **`signature_ok` computed inside W02** via `pgcrypto` HMAC against `provider_secret` — never a parameter (§6.20, H19) |
| `SweepRun` | W03 | P1 | One row per POS-3 fetch: `raw_response` verbatim; hash and item count **computed by W03**; `created_by_role = session_user` (§6.23) |
| `OutboxEntry` | W10 (insert) / W19a (claim) / W19b (enqueue on approval) | P1 / P4 | `UNIQUE(action_id)` |
| `TemplateRegistry` | Migration only | P1 | No runtime writer (§6.14) |
| `PromiseToPay` | W16, W17a/b, W18 | P3 | Terminal verdicts via W18 only; human review via W17b (`baaki_ops`) |
| `Dispute` | W14a (evidence) / W14b, W15 (`baaki_ops`) | P3 | §6.4 |
| `ProviderCall`, `Message`, `AuditEvent` | W22–W24 | P4 | Append-only; `audit_event.actor_role = session_user` |
| `ProviderSecret` | `bootstrap/secrets.sql` (as owner) | P1 | C-class; **no `SELECT` for any application role**; read only inside W02 |
| `IdempotencyRecord` | direct `INSERT` (M-class) | P4 | PK on key |
| `ExperimentAssignment` | W25 | P5 | Immutable; no `SELECT` for `baaki_sim` |

---

# 2. Promise-to-Pay State Machine **[P3]**

## 2.1 Governing rule **[DD]**

> The model may propose that a promise exists. Deterministic code decides whether it is valid.
> Only the reconciler, reading the ledger, decides whether it was kept.

Terminal verdicts (`KEPT`, `PARTIALLY_KEPT`, `BROKEN`) have one writer: **W18
`ptp_settle_verdict`**, which accepts only `payment_event_id[]` and computes the verdict itself
from ledger facts. Trigger `trg_ptp_verdict_writer` [P3] rejects any terminal-verdict write not
made through W18.

## 2.2 States

| State | Terminal | Meaning |
|---|---|---|
| `EXTRACTED` | No | Candidate from a validated interpretation. Not a commitment |
| `PENDING_REVIEW` | No | Failed a soft auto-confirm guard. Human queue |
| `ACTIVE` | No | Confirmed; `due_date` in future; suppresses pressure (§4.4 P7) |
| `DUE` | No | `due_date` reached; grace running |
| `KEPT` | **Yes** | Ledger-settled ≥ promised within grace (or early) |
| `PARTIALLY_KEPT` | **Yes** | `0 < settled < promised` at grace expiry |
| `BROKEN` | **Yes** | `settled = 0` at grace expiry; raises `risk_band` |
| `EXPIRED` | **Yes** | Never confirmed before `promised_date` — **our** failure |
| `CANCELLED` | **Yes** | Human, opt-out, or invoice `PAID` by unrelated payment |
| `SUPERSEDED` | **Yes** | Newer valid PTP on the same invoice |
| `SUSPENDED_DISPUTE` | No | Dispute opened while live; clock paused |

## 2.3 Transition table — via W16 (`ptp_create`), W17a (`ptp_transition`, `baaki_app`), W17b (`ptp_review`, **`baaki_ops`**), W18 (`ptp_settle_verdict`)

| # | From → To | Writer | Trigger | Guards |
|---|---|---|---|---|
| 1 | — → `EXTRACTED` | W16 | Validated `WILL_PAY_ON_DATE` | `outcome=PASS`; `promised_date NOT NULL`; W16 verifies the `validation_id` |
| 2 | `EXTRACTED` → `ACTIVE` | W17a | Auto-confirm | `today < promised_date ≤ today+30d`; `promised_paise ≤ outstanding` (comparison); confidence ≥ 0.70; no live PTP; no open dispute; not opted out |
| 3 | `EXTRACTED` → `PENDING_REVIEW` | W17a | Soft guard fails | horizon/amount/confidence |
| 4 | `EXTRACTED` → `CANCELLED` | W17a | Hard guard fails | opt-out ∨ past date ∨ invoice `PAID` |
| 5 | `PENDING_REVIEW` → `ACTIVE` | **W17b** | Human | invoker `baaki_ops` (H17); `reviewed_by_role=session_user`; re-runs #2 guards |
| 6 | `PENDING_REVIEW` → `CANCELLED` | **W17b** | Human | invoker `baaki_ops` (H17) |
| 7 | `EXTRACTED`\|`PENDING_REVIEW` → `EXPIRED` | W17a | Clock | `business_date > promised_date` |
| 8 | `ACTIVE` → `DUE` | W17a | Clock | `business_date ≥ due_date` |
| 9 | `ACTIVE` → `KEPT` | **W18** | Reconciler | `settled_since_ptp ≥ promised` before due; `settled_early=TRUE` |
| 10 | `DUE` → `KEPT` | **W18** | Reconciler | within grace |
| 11 | `DUE` → `PARTIALLY_KEPT` | **W18** | Reconciler, grace expiry | `0 < settled < promised` |
| 12 | `DUE` → `BROKEN` | **W18** | Reconciler, grace expiry | `settled = 0` |
| 13 | `ACTIVE`\|`DUE` → `SUSPENDED_DISPUTE` | W14a/W14b (via W17a) | Dispute opened | — |
| 14 | `SUSPENDED_DISPUTE` → `ACTIVE` | W15 (via W17a) | `RESOLVED_INVALID` | `due_date += suspension` |
| 15 | `SUSPENDED_DISPUTE` → `CANCELLED` | W15 (via W17a) | `RESOLVED_VALID` | invoice stays `DISPUTED` (§2.5) |
| 16 | live → `SUPERSEDED` | W17a | Newer PTP reaches `ACTIVE` | — |
| 17 | live → `CANCELLED` | W17a | Opt-out, unrelated `PAID`, human | — |

`settled_since_ptp` counts `payment_event`s with `posted_at ≥ ptp.created_at`. Grace = due + 2
business days **[IMPL]**. Anything not listed raises `IllegalTransition`.

## 2.4 Invalid transitions (tested)

Terminal verdict by any writer but W18 · `EXTRACTED → KEPT` · terminal → anything (corrections
create a new PTP via `corrects_ptp_id`) · `BROKEN → KEPT` on late payment · in-place `due_date`
change · any write from `agent/`.

## 2.5 `RESOLVED_VALID` freezes; it does not adjust **[DD]**

Baaki has no capability to reduce a receivable (F1–F7). A dispute the seller upholds leaves the
invoice `DISPUTED` permanently with collection blocked (P5); the correction happens in the seller's
system of record. Stated in the UI.

---

# 3. Recovery-Action State Machine **[P1 schema · P4 behaviour]**

## 3.1 Pipeline outcomes vs. action rows

| Outcome | Recorded on | Action row? |
|---|---|---|
| Parse failure | `agent_proposal.parse_status` | No |
| Validation `REJECT` | `validation_result` | No |
| `BLOCK` | `policy_decision` | **No** (P9) |
| `DEFER` | `policy_decision` + re-decision scheduled at `defer_until` | **No** (P9) |
| `ALLOW` / `REQUIRE_APPROVAL` | `policy_decision` + `recovery_action` | Yes |

`action_state` holds only states a row can occupy. There is no `SENT` state; the equivalent is
`EXECUTED`, which requires a `provider_ref` from a successful provider submission.

## 3.2 The 11 states — complete definition

Writers: **W10** `create_recovery_action` (insert) · **W19a** `transition_recovery_action` (every
automatic/provider-driven change, `baaki_app`) · **W19b** `approve_recovery_action` (rows 3–4 only,
**`baaki_ops` only**, H17) [P4]. No role holds `UPDATE`; arbitrary jumps through SQL are impossible
by grant; W19a holds §3.3 minus rows 3–4 as its allowlist, so approval is unreachable from the
automated role.

| State | Meaning | Entry condition | Predecessors | Successors | Owner | Writer | Human? | Provider? | Terminal |
|---|---|---|---|---|---|---|---|---|---|
| `PENDING_APPROVAL` | Awaiting human (tier 2) | Decision `verdict = REQUIRE_APPROVAL` | — (initial) | `QUEUED`, `APPROVAL_REJECTED`, `EXPIRED` | `actions/` | W10 | Required to leave | No | No |
| `APPROVAL_REJECTED` | Human declined | invoker `baaki_ops` | `PENDING_APPROVAL` | — | Human via ops | **W19b** | Yes | No | **Yes** |
| `QUEUED` | Cleared; outbox row exists | Decision `ALLOW` (initial) **or** approval recorded (`baaki_ops`) **or** retry due | — (initial), `PENDING_APPROVAL`, `FAILED_RETRYABLE` | `EXECUTING`, `EXPIRED` | `actions/` | W10 (initial) / **W19b** (from approval) / W19a (retry) | Only from `PENDING_APPROVAL` | No | No |
| `EXECUTING` | Claimed by one worker | `FOR UPDATE SKIP LOCKED` claim; `attempt_count < max`; `now < expires_at`; **P0–P4 re-checked** | `QUEUED` | `EXECUTED`, `FAILED_RETRYABLE`, `FAILED_TERMINAL`, `EXPIRED` | Outbox worker | W19a | No | About to | No |
| `EXECUTED` | Provider **accepted** the request | Provider 2xx with `provider_ref` **or** idempotent replay returned the existing resource | `EXECUTING` | `CONFIRMED`, `COMPENSATED` | Outbox worker | W19a | No | **Yes — required** | No |
| `CONFIRMED` | Provider-authoritative fact observed | A `payment_event` (W04) referencing this action's `provider_ref`/`notes.action_id` exists, **or** POS-2 fetch confirms terminal link status | `EXECUTED` | — | `reconcile/` | W19a (W19a verifies the `payment_event`/fetch evidence id passed in `ctx`) | No | **Yes — required** | **Yes** |
| `FAILED_RETRYABLE` | Transient failure | Provider `RETRYABLE`/`AMBIGUOUS`; `attempt_count < max` | `EXECUTING` | `QUEUED`, `FAILED_TERMINAL`, `EXPIRED` | Worker | W19a | No | Yes (failed) | No |
| `FAILED_TERMINAL` | Non-retryable | Provider 4xx, or `attempt_count ≥ max` | `EXECUTING`, `FAILED_RETRYABLE` | — | Worker | W19a | No | Yes (failed) | **Yes** |
| `EXPIRED` | Deadline passed | `now > expires_at` | `PENDING_APPROVAL`, `QUEUED`, `EXECUTING`, `FAILED_RETRYABLE` | — | Reaper | W19a | No | No | **Yes** |
| `SUPERSEDED_DUPLICATE` | Idempotency collision — an **audit row that retains the colliding `idempotency_key`** | a live action with the same key already exists at insert | — (insert-time) | — | `actions/` | W10 | No | **No — no provider call is made** | **Yes** |
| `COMPENSATED` | Side effect neutralised | POS-4 cancel succeeded after downstream failure | `EXECUTED` | — | Worker | W19a | No | Yes | **Yes** |

## 3.3 Legal transitions — the W19a / W19b allowlists

| # | From → To | Columns the writer may write | Guard inside the writer |
|---|---|---|---|
| 1 | insert → `PENDING_APPROVAL` | (W10) | decision `REQUIRE_APPROVAL` |
| 2 | insert → `QUEUED` (+ outbox) | (W10) | decision `ALLOW` |
| 3 | `PENDING_APPROVAL` → `QUEUED` (+ outbox) | **W19b only**: `approved_by_role := session_user`, `approved_by_note`, `approved_at` | **`session_user = 'baaki_ops'` (H17)**; decision `snapshot_hash` still current (stale ⟹ raise; caller re-decides). **Not in W19a's allowlist** |
| 4 | `PENDING_APPROVAL` → `APPROVAL_REJECTED` | **W19b only**: `approved_by_role := session_user`, `approved_by_note` | **`session_user = 'baaki_ops'` (H17)**. **Not in W19a's allowlist** |
| 5 | `PENDING_APPROVAL` → `EXPIRED` | — | `now > expires_at`. **A timeout is not consent** |
| 6 | `QUEUED` → `EXECUTING` | `attempt_count += 1`, outbox `claimed_at`, `claimed_by`, `lease_expires_at` | `attempt_count < max`; `now < expires_at`; P0–P4 re-check against a fresh read |
| 7 | `EXECUTING` → `EXECUTED` | `provider_ref`, `executed_at` | `provider_ref NOT NULL` |
| 8 | `EXECUTING` → `FAILED_RETRYABLE` | `next_attempt_at`, `last_error_code`, outbox released | `attempt_count < max` |
| 9 | `EXECUTING` → `FAILED_TERMINAL` | `last_error_code` | error class `TERMINAL_CLIENT`/`AUTH` |
| 10 | `EXECUTING` → `EXPIRED` | — | `now > expires_at` |
| 11 | `FAILED_RETRYABLE` → `QUEUED` | — (same key) | `now ≥ next_attempt_at ∧ now < expires_at` |
| 12 | `FAILED_RETRYABLE` → `FAILED_TERMINAL` | `last_error_code` | `attempt_count ≥ max` |
| 13 | `FAILED_RETRYABLE` → `EXPIRED` | — | `now > expires_at` |
| 14 | `QUEUED` → `EXPIRED` | — | `now > expires_at` |
| 15 | `EXECUTED` → `CONFIRMED` | `confirmed_at` | `ctx.payment_event_id` exists and references this action **or** `ctx.fetch_evidence` is a recorded `provider_call` with terminal status. **No evidence ⟹ raise** |
| 16 | `EXECUTED` → `COMPENSATED` | `last_error_code` | `ctx.provider_call_id` for the successful POS-4 call |
| 17 | insert → `SUPERSEDED_DUPLICATE` | (W10) | key collision |

**Explicitly impossible:** `BLOCK`/`DEFER` → any action (P9) · `PENDING_APPROVAL` → `EXECUTING`
(skipping approval — not in either allowlist) · **approval by `baaki_app` — rows 3–4 are absent from W19a and W19b is not executable by `baaki_app`** · **approval authorised by a supplied `approved_by` string — the column is `approved_by_role := session_user`, never a parameter** · `EXECUTING` → `CONFIRMED` (skipping `EXECUTED`) ·
`CONFIRMED` without evidence in `ctx` · `EXECUTED` without `provider_ref` · any terminal → anything
· any `UPDATE recovery_action` by any role.

## 3.4 Idempotency key **[DD]**

```
idempotency_key = SHA256(invoice_id ‖ action_type ‖ canonical_payload_hash ‖ business_date ‖ arm)
```
Deterministic; includes payload hash; **excludes `attempt_count` and every timestamp**.

**Uniqueness invariant IK1 [DD]:** *at most one live (non-`SUPERSEDED_DUPLICATE`) recovery action exists for a
given `idempotency_key`.* Enforced by the partial unique index `uq_action_idempotency ON recovery_action
(idempotency_key) WHERE state <> 'SUPERSEDED_DUPLICATE'`. A `SUPERSEDED_DUPLICATE` row deliberately carries the
colliding key so the collision is auditable; it is terminal, never enqueued, and never reaches a provider. A
plain `UNIQUE(idempotency_key)` would make that audit row impossible and is therefore **not** the design.

## 3.5 Crash and duplicate semantics

Same proposal twice/day → proposal `UNIQUE`, no model call · crash after provider call → lease
expiry → `QUEUED` → same key → provider returns existing resource → #7 · two workers → impossible
(`SKIP LOCKED`) · provider timeout → `FAILED_RETRYABLE`, fetch-by-reference before retry [ASSUME
A-R5] · approval unactioned → `EXPIRED`, never auto-approved · payment lands between decision and
execution → #6 re-check → `EXPIRED`.

---

# 4. The Deterministic Validator **[P2]**

`validate(proposal, snapshot) → ValidationResult`. Pure. **Fail-closed [DD].**

## 4.1 Order (short-circuits at first HARD)

| Seq | Check | Class | Reason |
|---|---|---|---|
| 01 | `KILL_SWITCH_OFF` | HARD | `SYSTEM_HALTED` |
| 02 | `LEDGER_INVARIANT_OK` | HARD | `LEDGER_INVARIANT_BREACH` |
| 03 | `PROPOSAL_PARSE_OK` | HARD | `SCHEMA_VIOLATION` / `UNPARSEABLE` / `PROVIDER_TIMEOUT` |
| 04 | `SCHEMA_VERSION_KNOWN` | HARD | `UNKNOWN_SCHEMA_VERSION` |
| 05 | `ENUM_CLOSURE` | HARD | `ENUM_OUT_OF_RANGE` |
| 06 | `NO_MONEY_KEYS` | HARD | `FORBIDDEN_MONEY_FIELD` |
| 07 | `EVIDENCE_SPANS_LITERAL` | HARD | `EVIDENCE_NOT_FOUND_IN_SOURCE` |
| 08 | `EVIDENCE_COVERS_CLAIMS` | HARD | `EVIDENCE_MISSING_FOR_FIELD` |
| 09 | `CONTACT_REF_VALID` | HARD | `CONTACT_NOT_IN_ACCOUNT` |
| 10 | `INVOICE_REF_VALID` | HARD | `INVOICE_REF_UNRESOLVED` — resolves `invoice_refs` to ids **⊆ account's invoices**; unresolvable ⟹ reject (§6.8.3) |
| 11 | `DATE_NORMALISE` | HARD | `DATE_UNPARSEABLE` / `DATE_AMBIGUOUS` |
| 12 | `AMOUNT_NORMALISE` | HARD | `AMOUNT_UNPARSEABLE` / `AMOUNT_AMBIGUOUS` |
| 13 | `DATE_RANGE_SANE` | SOFT | `DATE_IN_PAST` / `DATE_BEYOND_HORIZON` |
| 14 | `AMOUNT_RANGE_SANE` | SOFT | `AMOUNT_EXCEEDS_OUTSTANDING` |
| 15 | `CONFIDENCE_FLOOR` | SOFT | `CONFIDENCE_BELOW_THRESHOLD` |
| 16 | `CONFIDENCE_MONOTONIC_CAP` | — | applies I4 |

20 rejection reasons as listed. HARD 03–08 → L1 fallback (06, 09 also alert) · 09–12 → human
queue, no PTP · SOFT → PTP `PENDING_REVIEW`/`CANCELLED`, authority capped to tier 0.

## 4.2 Precedence ladder — total order **[DD]**

| P | Condition | Verdict | Exceptions |
|---|---|---|---|
| P0 | `kill_switch` | `BLOCK` | none |
| P1 | `ledger_invariant_ok=false` | `BLOCK` | none |
| **P2** | **`opt_out`** (account) **or target contact `opted_out`** | `BLOCK` | **none** |
| P3 | `invoice_state = PAID` | `BLOCK` | none |
| P4 | `outstanding_paise = 0` | `BLOCK` | none |
| P5 | Open dispute / `DISPUTED` | `BLOCK` pressure | `REQUEST_DISPUTE_DETAILS`, `ESCALATE_TO_HUMAN`, `SUPPRESS` |
| P6 | Unverified paid claim | `BLOCK` pressure | `SUPPRESS`, `ESCALATE_TO_HUMAN`; sweep triggered |
| P7 | Live PTP before due+grace | `BLOCK` pressure | `SUPPRESS`, `SCHEDULE_FOLLOWUP`, T−2 nudge |
| P8 | Active link < 24h | `BLOCK` `SEND_PAYMENT_LINK` | reminders citing existing link |
| P9 | Frequency cap | `BLOCK` outbound | `SUPPRESS`, `SCHEDULE_FOLLOWUP` |
| P10 | Quiet hours | `DEFER` | `SUPPRESS`, `SCHEDULE_FOLLOWUP` |
| P11 | Template incompatible | `BLOCK` `template.incompatible` | — |
| P12 | Tier 2 | `REQUIRE_APPROVAL` | — |
| P13 | Confidence cap | downgrade | — |
| P14 | pass | `ALLOW` | — |

Executor re-checks P0–P4 at claim (§3.3 #6). Interaction matrix as v3: opt-out dominates; dispute
suspends PTP; paid claim suspends for 72h **[IMPL]** and triggers a sweep, never accepts.

## 4.3 Confidence monotonicity (I4)

`authority_tier = min(catalogue_tier(action_type), tier_cap(effective_confidence))`, `tier_cap`
non-increasing. Property-tested over 10⁴ inputs.

## 4.4 Ambiguity is rejection **[DD]** — no LLM re-prompt; "next week", "1.5", "half", two dates
→ human queue.

---

# 5. Money-Safety Kernel **[P2]**

`kernel.decide(validation | None, snapshot, ruleset) → PolicyDecision`. Pure; arm-agnostic;
byte-identical across arms **[DD]**.

## 5.1 Model-influence paths → controls

| # | Path | Control |
|---|---|---|
| 1 | Model amount → payment amount | No money field in schemas; `amount_paise` from `snapshot.outstanding_paise`; W09 asserts CP5 |
| 2 | Model output → executor | Executor accepts `ExecutableDecision` only |
| 3 | Tools / function calling | None exist |
| 4 | Network / DB from `agent/` | No session but `baaki_agent`; no HTTP but LLM adapter |
| 5 | Free-text escape | Closed enums; strict; unknown = HARD reject |
| 6 | Arbitrary recipient | `contact_id ∈ contactable_contact_ids` |
| 7 | Confidence uplift | `min()` |
| 8 | Free-text fallback parse | Absent (grep test) |
| 9 | Re-prompt loops | ≤1 transport retry |
| 10 | Prompt injection | Bounded by 1–9 |
| 11 | Model → PTP verdict | W18 only, from `payment_event_id[]` |
| 12 | Model → invoice state | W01/W05/W13/W14a/W14b/W15 only |
| 13 | Model → outbound copy | Copywriting cut; templates from registry |
| 14 | Model → metrics/arm | `experiment/` reads ledger + assignment only |
| 15 | Model → idempotency key | From `canonical_payload_hash` |
| 16 | Rubber-stamp approval | Diff view; 2 tier-2 types; timeout ≠ consent |
| 17 | Model → ruleset | Hashed file |
| 18 | Compromised app issues SQL DML | I10 — no DML on F/D tables; W01–W25 |
| 19 | Claimed amount assigned as authority | `ClaimedPaise` ≠ `Paise` |
| 20 | `raw_response` side channel | A6 |
| **21** | **Model invoice reference selects the target invoice** | Check 10 resolves against the account's invoices; kernel selects from `candidate_invoice_ids`; P13 |
| **22** | **Model opt-out / dispute claim mutates state directly** | W11/W14a require a `validation_id` with `PASS` and matching intent; `agent/` cannot execute them (§6.18) |
| **24** | **Any caller forges a human actor** | Authority = connection role (`baaki_ops`) verified as `session_user` inside the writer; `actor_note` is metadata (§6.22) |
| **25** | **Fabricated sweep or webhook evidence** | W02 computes the HMAC itself from a secret the app never holds; W03/W04 compute hashes and extract fields; containment checks (§6.20, §6.23) |
| **23** | **Fabricated `payment_event`** | W04 requires provenance evidence + payload hash (§6.20) |

## 5.2 Tiers

0 `SUPPRESS`, `SCHEDULE_FOLLOWUP`, `REQUEST_DISPUTE_DETAILS` · 1 `SEND_REMINDER`,
`SEND_PAYMENT_LINK` · 2 `PROPOSE_INSTALLMENT_PLAN`, `ESCALATE_TO_HUMAN` · F1–F7 unrepresentable.

## 5.3 Module and import boundaries **[DD]**

```
agent/       → domain/, providers/llm/, db/writers/proposal            connects as baaki_agent
             ✗ ledger/ actions/ reconcile/ experiment/ providers/razorpay/ db/writers/{decision,action,ledger,payment,lifecycle}
policy/      → domain/, ledger/(read), db/writers/{validation,decision,optout_evidence}
             ✗ providers/ agent/ actions/ db/writers/{ledger,payment,action}
actions/     → domain/, policy/(types), providers/razorpay/, db/writers/{action}
             ✗ agent/ db/writers/{ledger,payment,decision}
reconcile/   → domain/, ledger/, providers/razorpay/, db/writers/{webhook,sweep,payment,ledger,ptp_verdict,action_auto}
             ✗ agent/ policy/kernel
sim/         → domain/(read), providers/razorpay/fake (drives the fake provider only)   connects as baaki_sim
             ✗ experiment/ policy/ agent/ db/writers/*
experiment/  → domain/, ledger/(read), db/writers/experiment
             ✗ agent/ sim/
scripts/ops  → db/writers/{operator}   (W12, W14b, W15, W17b, W19b, W20, W21a/b)   connects as baaki_ops
             ✗ nothing else imports db/writers/operator (import-graph test)
```
`KERNEL_TOKEN` importable only by `policy.kernel` and `tests/`.

---


# 6. Authority Model

Everything in §6 is **[DD]** unless marked.

## 6.1 Principle and table authority classes

> A compromised application must not be able to bypass the deterministic
> policy → executor → provider → ledger chain by issuing SQL — **and a caller-supplied identity
> string must never confer authority.**

| Class | Definition | Direct app `INSERT`? | Direct app `UPDATE`? | Tables |
|---|---|---|---|---|
| **F — Financial-authoritative** | Rows that *are* money or *cause* money | No | No | `invoice`, `ledger_entry`, `payment_event` |
| **D — Decision/evidence-authoritative** | What the system decided, did, or observed | No | No | `agent_proposal`, `validation_result`, `policy_decision`, `recovery_action`, `outbox`, `webhook_event`, `sweep_run` [P1] · `promise_to_pay`, `dispute` [P3] · `provider_call`, `message`, `audit_event` [P4] · `experiment_assignment` [P5] |
| **M — Application-owned domain** | Master data; a few writer-gated columns | **Yes** (`baaki_app`) | Safe columns only (§6.4A) | `account`, `contact` [P1] · `idempotency_record` [P4] |
| **C — Configuration** | Seeded by migration/bootstrap; no runtime DML | No | No (except W21a/b on `kill_switch`) | `organization`, `template_registry`, **`provider_secret`** (no `SELECT` for any application role) [P1] |
| **R — Derived** | Views | — | — | `v_invoice_outstanding` |

**I10, precisely:** (1) F, D: no application role holds `INSERT`/`UPDATE`/`DELETE`; all writes are
W01–W25 (29 functions, §6.6). (2) M: `baaki_app` may `INSERT`; `UPDATE` exactly §6.4A; no `DELETE`.
(3) C: no runtime DML; `kill_switch` via W21a/b only; `provider_secret` unreadable by any
application role. (4) R: `SELECT`. (5) **No role holds `DELETE` on any table.**

Schemas: `baaki` (tables, views), `baaki_write` (writers). `public` stripped. Extension: `pgcrypto`
**[IMPL]** for HMAC inside W02.

## 6.2 PostgreSQL roles

| Role | Exists because | Attributes | Connects |
|---|---|---|---|
| `baaki_owner` | Owns all objects; `SECURITY DEFINER` target | **`NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS`** | Never |
| `baaki_migrate` | Creates objects owned by `baaki_owner` | `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`; `GRANT baaki_owner TO baaki_migrate`; env does `SET ROLE baaki_owner` | Alembic + `bootstrap/secrets.sql` only. Credential in the migration step's env only |
| `baaki_app` | **Automated** application: validator, kernel, executor worker, reconciler, scheduler, API | `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT` | Runtime |
| **`baaki_ops`** | **Human-operator authority.** The only role that can execute human-only writers. Its credential is held by operators and is **absent from the application runtime environment** | same | `scripts/ops`, ops UI process |
| `baaki_agent` | Model isolation | same | `agent/` |
| `baaki_sim` | Simulator blinding | same | `sim/` |
| `baaki_readonly` [P5] | Dashboards, `experiment/` | same | `experiment/`, `ui/` |

No role is a member of any other except `baaki_migrate ∈ baaki_owner`. Therefore `SET ROLE` cannot
cross roles (`baaki_app` cannot `SET ROLE baaki_ops`), and `SET SESSION AUTHORIZATION` requires
superuser. **`session_user` — the authenticated login role of the connection — is therefore a
trusted execution context inside every writer.** Roles are created by `bootstrap/roles.sql`
(superuser, once). P1 roles: 6. MVP: 7.

## 6.3 Table privileges

`S`/`I`/`U(col)`; `—` none; **no role holds `DELETE`.**

| Table | Class | `owner` | `app` | **`ops`** | `agent` | `sim` |
|---|---|---|---|---|---|---|
| `organization` | C | ALL | S | S | — | — |
| `account` | M | ALL | S, I, U(`risk_band`) | S | S | S |
| `contact` | M | ALL | S, I, U(`active`) | S | S | — |
| `template_registry` | C | ALL | S | S | S | — |
| **`provider_secret`** | C | ALL | **—** | **—** | — | — |
| `invoice` | F | ALL | S | S | S | S |
| `ledger_entry` | F | ALL | S | S | — | — |
| `payment_event` | F | ALL | S | S | — | — |
| `webhook_event`, `sweep_run` | D | ALL | S | S | — | — |
| `agent_proposal` | D | ALL | S | S | S | — |
| `validation_result`, `outbox` | D | ALL | S | S | — | — |
| `policy_decision`, `recovery_action` | D | ALL | S | S | — | — |
| `v_invoice_outstanding` | R | ALL | S | S | — | — |
| `promise_to_pay`, `dispute` [P3] | D | ALL | S | S | — | — |
| `provider_call`, `message`, `audit_event` [P4] | D | ALL | S | S | — | — |
| `idempotency_record` [P4] | M | ALL | S, I | — | — | — |
| `experiment_assignment` [P5] | D | ALL | S | S | — | **—** |

`baaki_ops` holds **no DML on any table**; its authority is expressed entirely through `EXECUTE`
on the seven human-only writers (§6.6). `baaki_migrate` acts only via `SET ROLE baaki_owner`.

## 6.4 Column and update capabilities — complete matrix

### A. Safe — direct column-level `UPDATE` for `baaki_app`

`account.risk_band` (ranking only) · `contact.active` (operational; `opted_out` is the gated half).
Test: `information_schema.column_privileges` set-equals exactly these two.

### B. Authority-sensitive — every column has exactly one writer

Mode: **A** automatic · **P** provider-driven · **H** human (invoker must be `baaki_ops`) · **E**
evidence-gated (`validation_id` with `PASS` + matching intent). *Actor* column: how the actor is
established — **TEC** = trusted execution context (`session_user`), **META** = caller-supplied
`actor_note`, audit metadata only.

| Field | Mutation | Writer | Invoker role | Ph | Mode | Actor | P1 behaviour |
|---|---|---|---|---|---|---|---|
| `organization.kill_switch` | →TRUE | **W21a** | `app` or `ops` | P4 | H / A | TEC + META | Seeded FALSE; immutable P1–P3 |
| `organization.kill_switch` | →FALSE | **W21b** | **`ops` only** | P4 | **H** | TEC + META | — |
| `account.opt_out` | →TRUE | **W12** | **`ops` only** | P2 | **H** | TEC + META | Immutable in P1 |
| `account.opt_out`, `contact.opted_out` | →FALSE | **none** | — | — | **Monotonic** | — | — |
| `contact.opted_out` | →TRUE (inbound) | **W11** | `app` (`policy/`) | P2 | **E** | `validation_id` | Immutable in P1 |
| `contact.opted_out` | →TRUE (human) | **W12** | **`ops`** | P2 | **H** | TEC + META | — |
| `*.opted_out_by_role`, `opted_out_source`, `opted_out_note` | set once | W11/W12 | as above | P2 | — | TEC/META | — |
| `invoice.state` | insert `OPEN` | W01 | `app` | P1 | A | — | — |
| `invoice.state` | →`PAID` | W05 / W20 | `app` / `ops` | P1 / P4 | P | — | — |
| `invoice.state` | `OPEN→DUE→OVERDUE` | W13 | `app` | P3 | A | — | Only `OPEN`/`PAID` in P1 |
| `invoice.state` | →`DISPUTED` (inbound) | **W14a** | `app` | P3 | **E** | `validation_id` | — |
| `invoice.state` | →`DISPUTED` (human) | **W14b** | **`ops`** | P3 | **H** | TEC + META | — |
| `invoice.state` | `DISPUTED→aging` | **W15** | **`ops`** | P3 | **H** | TEC + META | — |
| `invoice.issued_paise`, dates, number, ids | — | **immutable** | | | | | |
| `payment_event.applied_at` | once | W05/W06/W20 | `app`/`ops` | P1/P4 | P | — | — |
| `payment_event.attributed_invoice_id`, `attribution_method`, `reattributed_at`, `reattributed_by_role`, `reattributed_by_note` | once | **W20** | **`ops`** | P4 | **H** | TEC + META | Suspense in P1 |
| `payment_event` provider fields | — | **immutable** (extracted by W04) | | | | | |
| `recovery_action.state` (automatic rows of §3.3) | per allowlist | **W19a** | `app` | P4 | A / P | — | Initial state only in P1 |
| `recovery_action.state` (`PENDING_APPROVAL→QUEUED`, `→APPROVAL_REJECTED`) | approval | **W19b** | **`ops` only** | P4 | **H** | TEC + META | — |
| `recovery_action.approved_by_role`, `approved_by_note`, `approved_at` | once | **W19b** | **`ops`** | P4 | H | TEC / META | — |
| `recovery_action.attempt_count`, `next_attempt_at`, `last_error_code`, `provider_ref`, `executed_at`, `confirmed_at`, `updated_at` | per §3.3 | W19a | `app` | P4 | A / P | — | — |
| `outbox.claimed_*` | claim/release | W19a | `app` | P4 | A | — | Never claimed in P1 |
| `webhook_event.processed_at` | once | W04 | `app` | P1 | P | — | — |
| `webhook_event.signature_ok` | **computed at insert by W02 from `provider_secret`** | W02 | `app` | P1 | P | — | Tests seed a known secret |
| `sweep_run.*` | insert only; hash/count computed by W03 | W03 | `app` | P1 | P | TEC (`created_by_role`) | — |
| `promise_to_pay.state` (automatic rows) | §2.3 | W16/W17a/W18 | `app` | P3 | A / P | — | — |
| `promise_to_pay.state` (#5, #6 human review) | review | **W17b** | **`ops`** | P3 | **H** | TEC + META | — |
| `dispute.state` | open / resolve | W14a/W14b / W15 | `app`/`ops` / `ops` | P3 | E / H | TEC/META | — |
| `account.risk_band`, `contact.active` | any | direct (Safe) | `app` | P1/P3 | A/H | — | — |

Test `db/test_column_capabilities`: non-PK − immutable − Safe = columns written by W01–W25 (parsed
from function bodies); every human-only column's writer has `EXECUTE` for `baaki_ops` only.

## 6.5 SECURITY DEFINER hardening contract — H1–H19

| # | Rule | Verified by |
|---|---|---|
| H1 | Owner `baaki_owner` | `pg_proc.proowner` |
| H2 | Owner `NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS` | `pg_roles` |
| H3 | `SECURITY DEFINER` | `prosecdef` |
| H4 | `SET search_path = baaki, pg_catalog` | `proconfig` |
| H5 | All references schema-qualified | body grep |
| H6 | `REVOKE EXECUTE FROM PUBLIC`, then explicit grants per §6.6 | `has_function_privilege` |
| H7 | No dynamic SQL (`EXECUTE`, `format(`) | body grep |
| H8 | `plpgsql` | `pg_language` |
| H9 | `FUNCTION`; no `COMMIT`/`ROLLBACK`; caller's txn | `prokind`; grep |
| H10 | Multiple writers per txn | E2E |
| H11 | `RAISE` aborts enclosing txn | E2E |
| H12 | Zero rows on any failure path | row counts |
| H13 | **No `current_setting()`-based authority** | body grep |
| H14 | `VOLATILE`, never `LEAKPROOF` | `pg_proc` |
| H15 | App roles `USAGE` only; no `CREATE`; `public` stripped | `has_schema_privilege` |
| H16 | Migrations as `baaki_migrate` + `SET ROLE baaki_owner`; credential absent from runtime | env structure test; membership |
| **H17** | **Every human-only writer (W12, W14b, W15, W17b, W19b, W20, W21b) asserts `session_user = 'baaki_ops'` as its first statement and raises `unauthorized_invoker` otherwise — independent of, and in addition to, the `EXECUTE` grant.** `session_user` is not a GUC; it is the connection's authenticated login role | body grep + AC11 |
| **H18** | **Every writer that records an actor stores `session_user` in a `*_by_role` column (trusted) and the caller-supplied `actor_note` in a separate `*_note` column (metadata). No writer reads authority from a text parameter** | body grep; AC12 |
| **H19** | **`provider_secret` is readable only inside writers** (owner-only table, no grants). W02 computes `signature_ok` from it; no writer accepts `signature_ok` as a parameter | grants test; WS1 |

## 6.6 Writer catalogue — 29 functions W01–W25 (a/b splits) — capability and transaction contract

`✓` = `EXECUTE` granted; else revoked incl. `PUBLIC`. **H** rows additionally assert `session_user`
(H17). All run in the caller's txn, never commit/rollback, raise with zero rows on failure.

| W | Function | Writes | `app` | **`ops`** | `agent` | `sim` | Ph | Idempotency / retry |
|---|---|---|---|---|---|---|---|---|
| W01 | `issue_invoice(org, account, number, issued_paise, issued_date, due_date)` | invoice + 2 `ISSUANCE` lines | ✓ | — | — | — | P1 | `UNIQUE(org, number)` raises; 0 ledger rows |
| W02 | `record_webhook_event(provider, raw_body, signature_header, received_at)` | `webhook_event` with **computed** `dedupe_key`, `signature_ok` | ✓ | — | — | — | P1 | `ON CONFLICT (provider, dedupe_key) DO NOTHING RETURNING` — retry-safe |
| W03 | `record_sweep_run(provider, window_from, window_to, raw_response[, provider_call_id P4])` | `sweep_run` with **computed** `raw_response_hash`, `item_count`, `created_by_role = session_user` | ✓ | — | — | — | P1 | `ON CONFLICT (provider, raw_response_hash) DO NOTHING RETURNING` — retry-safe |
| W04 | `record_payment_event(webhook_event_id\|sweep_run_id, provider_payload_raw, attributed_invoice_id, attribution_method)` | `payment_event` with **all financial fields extracted inside** (§6.20); `webhook_event.processed_at` | ✓ | — | — | — | P1 | `UNIQUE(provider_payment_id)` raises → caller proceeds to W05/W06 (idempotent) |
| W05 | `ledger_apply_payment(payment_event_id)` | `PAYMENT` lines; `applied_at`; `PAID` | ✓ | — | — | — | P1 | once (`applied_at` + `UNIQUE(event, code)`) |
| W06 | `ledger_post_unapplied(payment_event_id)` | suspense lines; `applied_at` | ✓ | — | — | — | P1 | once |
| W07 | `record_agent_proposal(...)` | `agent_proposal` | — | — | **✓** | — | P1 | `UNIQUE(invoice, date, kind, input_hash)` |
| W08 | `record_validation_result(proposal_id, …)` | `validation_result` (derives trace/account/date) | ✓ | — | — | — | P1 | `UNIQUE(proposal_id)` |
| W09 | `record_policy_decision(...)` | `policy_decision` (derives when linked) | ✓ | — | — | — | P1 | `UNIQUE(validation_id, date)` / partial unique for unlinked |
| W10 | `create_recovery_action(decision_id, action_id, idempotency_key, expires_at, now)` | `recovery_action` (+ `outbox` if `ALLOW`) | ✓ | — | — | — | P1 | `UNIQUE(decision_id)`; key collision with another decision → inserts a `SUPERSEDED_DUPLICATE` row **carrying the same key** (allowed by the partial unique index, IK1), returns the original `action_id`, no outbox row |
| W11 | `opt_out_contact_from_evidence(contact_id, validation_id)` | `contact.opted_out=TRUE` + `_by_role='baaki_app'`, `_source='INBOUND_UNSUBSCRIBE'` | ✓ | — | — | — | P2 | idempotent |
| **W12** | `opt_out_by_operator(account_id\|contact_id, actor_note)` **H** | `account.opt_out` or `contact.opted_out` = TRUE; `_by_role=session_user`, `_note` | — | **✓** | — | — | P2 | idempotent |
| W13 | `advance_invoice_aging(business_date)` | aging states only | ✓ | — | — | — | P3 | idempotent per date |
| W14a | `open_dispute_from_evidence(invoice_id, validation_id)` | `dispute`; `invoice→DISPUTED`; PTP suspend | ✓ | — | — | — | P3 | partial unique → raises |
| **W14b** | `open_dispute_by_operator(invoice_id, reason, actor_note)` **H** | same; `opened_by_role=session_user` | — | **✓** | — | — | P3 | same |
| **W15** | `resolve_dispute(dispute_id, resolution, actor_note)` **H** | `dispute.state`; `invoice.state`; PTP | — | **✓** | — | — | P3 | terminal → raises |
| W16 | `ptp_create(validation_id, …)` | PTP `EXTRACTED` | ✓ | — | — | — | P3 | `UNIQUE(validation_id)` |
| W17a | `ptp_transition(ptp_id, expected_from, to, ctx)` | §2.3 rows 2–4, 7, 8, 13–17 | ✓ | — | — | — | P3 | optimistic `expected_from` |
| **W17b** | `ptp_review(ptp_id, decision, corrected_values, actor_note)` **H** | §2.3 rows 5, 6; `reviewed_by_role=session_user` | — | **✓** | — | — | P3 | from `PENDING_REVIEW` only |
| W18 | `ptp_settle_verdict(ptp_id, payment_event_ids[])` | terminal verdict computed inside | ✓ | — | — | — | P3 | terminal → raises |
| W19a | `transition_recovery_action(action_id, expected_from, to, ctx)` | §3.3 rows **except 3, 4** | ✓ | — | — | — | P4 | optimistic `expected_from` |
| **W19b** | `approve_recovery_action(action_id, approve BOOLEAN, actor_note)` **H** | §3.3 rows 3, 4; `approved_by_role=session_user`, `approved_by_note`, `approved_at`; outbox on approve | — | **✓** | — | — | P4 | from `PENDING_APPROVAL` only; snapshot re-hash check |
| **W20** | `ledger_reattribute_unapplied(payment_event_id, invoice_id, justification, actor_note)` **H** | `REATTRIBUTION` lines; attribution cols; `reattributed_by_role=session_user` | — | **✓** | — | — | P4 | once |
| W21a | `kill_switch_activate(reason, actor_note)` | `kill_switch=TRUE`; audit with `session_user` | ✓ | ✓ | — | — | P4 | idempotent |
| **W21b** | `kill_switch_deactivate(reason, actor_note)` **H** | `kill_switch=FALSE`; audit | — | **✓** | — | — | P4 | idempotent; `reason` and `actor_note` non-null |
| W22 | `record_provider_call(...)` | `provider_call` | ✓ | — | — | — | P4 | insert per attempt |
| W23 | `record_message(...)` | `message` | ✓ | — | — | — | P4 | unique outbound per action |
| W24 | `record_audit_event(...)` | `audit_event` with `actor_role=session_user` | ✓ | ✓ | ✓ | — | P4 | insert |
| W25 | `record_experiment_assignment(...)` | `experiment_assignment` | ✓ | — | — | **—** | P5 | `UNIQUE(account, experiment)` |

**Human-only set (7):** W12, W14b, W15, W17b, W19b, W20, W21b — `EXECUTE` for `baaki_ops` only **and**
H17 assertion. **Unauthorized callers fail twice over:** `InsufficientPrivilege` at `EXECUTE` for
roles without the grant; `unauthorized_invoker` from the body if a grant were ever misconfigured.

### Chained operations — transaction boundaries

| Chain | Boundary | Atomic? |
|---|---|---|
| T1 W07 | own txn (`baaki_agent`) | n/a |
| T2 W08 → W09 → W10 | **one txn, required** | **yes** |
| T5 W02 | **own txn, intentionally** — commit + 200 before processing | n/a |
| T6 W04 → (W05\|W06) → W18 → W19a(→`CONFIRMED`) | **one `SERIALIZABLE` txn, required** | **yes** |
| T7 W03, then per item T6 | W03 own txn; **each item's T6 own txn, intentionally** | per item |
| W19a claim / result | **separate txns, intentionally** — never across a provider call | no |
| W19b approve | one txn: approval + outbox | yes |

## 6.7 PolicyDecision partition — database enforcement

```sql
ck_executable_shape:    (verdict IN ('ALLOW','REQUIRE_APPROVAL')) = (action_type IS NOT NULL AND canonical_payload IS NOT NULL)
ck_nonexecutable_shape: (verdict IN ('BLOCK','DEFER'))            = (action_type IS NULL     AND canonical_payload IS NULL)
ck_block_has_rules:     verdict <> 'BLOCK' OR blocking_rules <> '[]'::jsonb
ck_defer_has_until:     (verdict = 'DEFER') = (defer_until IS NOT NULL)
ck_tier2_approval:      tier <> 2 OR verdict = 'REQUIRE_APPROVAL'
ck_nonllm_no_proposal:  arm = 'TREATMENT' OR proposal_id IS NULL
ck_proposal_paired:     (proposal_id IS NULL) = (validation_id IS NULL)
ck_tier_domain:         tier IN (0,1,2)
```

## 6.8 Proposal → validation → decision — single identity strategy

**Keys:** `agent_proposal PK(proposal_id)` · `validation_result PK(validation_id), FK proposal_id UNIQUE`
· `policy_decision PK(decision_id), FK proposal_id?, FK validation_id?, UNIQUE(validation_id,
business_date), partial UNIQUE(invoice_id, business_date, arm) WHERE proposal_id IS NULL` ·
`recovery_action PK(action_id), FK decision_id UNIQUE`. **No composite superkeys.**

**Consistency — derived, then verified:** W08 copies `trace_id`, `account_id`, `business_date` from
the proposal (no such parameters). W09, when linked, loads proposal + validation, asserts
`validation.proposal_id = proposal_id`, copies the three fields, asserts `arm=TREATMENT`, P11, P13;
when unlinked, accepts them. `trg_decision_linkage` (BEFORE INSERT) independently re-verifies all of
it plus `invoice.account_id = NEW.account_id`. W10 copies from the decision;
`trg_action_type_matches_decision` verifies.

**Account-level proposal → invoice-level decision (SC1–SC6):** the proposal is scope only. Check 10
resolves `invoice_refs` by exact number against the **account's own** invoices (else
`INVOICE_REF_UNRESOLVED`). `candidate_invoice_ids` = the account's invoices with `state ≠ PAID` and
outstanding > 0, read from the DB. Kernel selects deterministically: sole resolved ref ∈ candidates →
it; else `proposal.invoice_id ∈ candidates` → it; else greatest `days_overdue` (ties: outstanding,
then id); else `BLOCK no_target_invoice`. W09 asserts `invoice_id ∈ candidates` (P13); trigger
asserts account membership. A model reference can only narrow among invoices the account already
owns.

## 6.9 CanonicalPayload restrictions — W09 enforcement

Shape per `action_type`; money integers > 0; `SEND_PAYMENT_LINK.amount_paise = v_invoice_outstanding`
(CP5); `contact_id` ∈ account, active, not opted out; TPL1–TPL5; `reason_code`/`assignee_queue` cast to
their enums and **`assignee_queue = CASE reason_code WHEN 'DISPUTE_UNRESOLVED' THEN 'DISPUTES' ELSE
'COLLECTIONS' END`** (`RAISE queue_reason_mismatch`, §1.5.1); no extra keys.

## 6.10 RecoveryAction creation boundary

1 runtime token (`KERNEL_TOKEN`) · 2 import graph · 3 `from_decision(ExecutableDecision, …)` and
`as_executable()` · **4 DB constraint**: `decision_id NOT NULL UNIQUE`, allowlist trigger, type-match
trigger · **5 DB role**: only W10 inserts; `baaki_agent` cannot execute it; no role has `UPDATE`.

## 6.11 Ledger authority

Account codes (closed, constructed inside writers): `AR:<account_id>`, `SALES`, `CASH_CLEARING`,
`UNAPPLIED_CASH`, `BUYER_CREDIT:<account_id>`; CHECK pattern. **Callers never pass codes.**

```
AR DEBIT  = increase in receivable   — only ISSUANCE (W01), once per invoice
AR CREDIT = decrease in receivable   — only PAYMENT (W05) or REATTRIBUTION (W20), bound to one payment_event
```
**Structural** (CHECK/enum/trigger/grant): amount > 0; direction; Σdr=Σcr per txn; closed codes;
`(source, class)` allowlist; `AR ⟺ invoice_id`; `payment_event_id ⟺ source ≠ ISSUANCE`; one invoice
per txn; append-only. **Business** (writer logic): payment reduces only its attributed invoice;
AR credit requires a provenance-bound event (§6.20); attribution has no amount-based member;
outstanding never negative (§6.12); receivable decreases only by confirmed money; no reversal exists.

## 6.12 Ledger writer restrictions and the over-credit algorithm

`post_ledger_transaction(lines[])` **does not exist.** W01/W05/W06/W20 accept no code, no line list,
no amount for payments.

**W05, one transaction:** lock event (`applied_at IS NULL`, attributed) → lock invoice → `outstanding`
from the view → `ar_credit = LEAST(amount, outstanding)`; `excess = amount − ar_credit` → DR
`CASH_CLEARING` amount; CR `AR` ar_credit (if > 0); CR `BUYER_CREDIT` excess (if > 0), one `txn_id` →
`PAID` if `ar_credit = outstanding > 0` → `applied_at`. Balanced by construction; **never negative
because the cap is computed before any line is written**; once. **Payment against an already-`PAID`
invoice: `outstanding = 0` ⟹ `ar_credit = 0`, whole amount → `BUYER_CREDIT` (locked, §18.1 D2).**
W06: DR `CASH_CLEARING` / CR `UNAPPLIED_CASH`. W20: DR `UNAPPLIED_CASH` / CR `AR` capped / CR
`BUYER_CREDIT` excess. Allowed `(source, class)` pairs: `(ISSUANCE, AR|SALES)`,
`(PAYMENT, CASH_CLEARING|AR|BUYER_CREDIT|UNAPPLIED_CASH)`, `(REATTRIBUTION, UNAPPLIED_CASH|AR|BUYER_CREDIT)`.

## 6.13 Outstanding projection

`v_invoice_outstanding` = Σ AR debits − Σ AR credits per `invoice_id` over `ledger_entry`. Sole source
of `outstanding_paise`; `issued_paise` not an input; nothing cached; "partially paid" is derived.

## 6.14 Template registry and compatibility

`template_registry(template_id PK, channel, action_type, purpose, active, version, body_hash)`;
migration-only. TPL1 channel match · TPL2 action_type match · TPL3 active · TPL4 `(action_type,
purpose)` allowed pairs (registry CHECK) · TPL5 registered; **executor resolves only by registry
lookup, no interpretation, unregistered ⟹ `FAILED_TERMINAL`**. Kernel → `BLOCK template.incompatible`;
W09 → `RAISE`; DB → FK/CHECK.

## 6.15 Tier-3 forbidden capabilities — F1–F7

| # | Capability | Why forbidden | Structural absence | Test |
|---|---|---|---|---|
| F1 | `DISCOUNT` | Proposal could reduce a receivable | No enum/payload; no AR writer outside W01/W05/W20 | `test_tier3::test_discount` |
| F2 | `SETTLEMENT` | F1 renamed | `PAID` only at outstanding 0 in W05 | `::test_settlement` |
| F3 | `WRITE_OFF` | Erases AR without money | No `WRITTEN_OFF` state; no AR credit without event | `::test_write_off` |
| F4 | `REFUND` | Reverses recovery; invalidates metric | Not in POS-4 | `::test_refund` |
| F5 | `MARK_PAID` | Claim becomes money | `PAID` only inside W05/W20; AR credit needs provenance | `::test_mark_paid` |
| F6 | `ADJUST_AMOUNT` | Silent manipulation | `issued_paise` immutable; no `CANCELLED`; unique invoice number | `::test_adjust_amount` |
| F7 | `REVERSE_LEDGER` | Arbitrary accounting state | No reversal writer; append-only; no `MANUAL_CORRECTION` | `::test_reverse_ledger` |

Absent from `ActionType`, every `CanonicalPayload` variant, both model schemas, every decision
verdict/payload, W01–W25, the executor interface, POS-4, account codes, `ledger_source`, `invoice_state`.

## 6.16 Red-team attack surface — final matrix

Rows fail unless marked ✓. **New in v3.2: AC (actor forgery), SR (sweep_run), WS (webhook signature).**

| # | Attack | Role | Expected | Ph |
|---|---|---|---|---|
| **Direct INSERT** | | | | |
| R1–R3 | `INSERT` into `invoice`, `ledger_entry`, `payment_event` | `app`, `ops` | `InsufficientPrivilege` | P1 |
| R4–R10 | `INSERT` into each D-class P1 table | `app`, `ops` | `InsufficientPrivilege` | P1 |
| R11 | `INSERT` into `template_registry`, `organization`, `provider_secret` | `app`, `ops` | `InsufficientPrivilege` | P1 |
| R12 | `INSERT` into `account`, `contact` | `app` | **✓** | P1 |
| R13 | `INSERT` into `account`, `contact` | `ops` | `InsufficientPrivilege` | P1 |
| **Direct UPDATE** | | | | |
| U1–U14 | `invoice.state/issued_paise`; `recovery_action.state/provider_ref/confirmed_at/attempt_count/approved_by_role`; `account.opt_out`; `contact.opted_out`; `payment_event.*`; `ledger_entry.*`; `kill_switch`; `outbox`; `webhook_event.signature_ok` | `app`, `ops` | `InsufficientPrivilege` | P1 |
| U15 | `account.risk_band`, `contact.active` | `app` | **✓** | P1 |
| U16 | `DELETE` any table | every role | `InsufficientPrivilege` | P1 |
| U17 | `SELECT provider_secret` | `app`, `ops`, `agent`, `sim` | `InsufficientPrivilege` | P1 |
| **Agent / sim** | | | | |
| A1–A7 | as v3.1 (financial inserts, decision select, W01–W06/W08–W10 execute, proposal update; W07 ✓ for agent, ✗ for app; W11/W14a ✗) | `agent` | `InsufficientPrivilege` | P1–P3 |
| S1–S3 | `contact` select; `experiment_assignment` select [P5]; any writer | `sim` | `InsufficientPrivilege` | P1/P5 |
| **AC — Trusted actor forgery** | | | | |
| AC1 | W12 `opt_out_by_operator` | `app` | `InsufficientPrivilege` | P2 |
| AC2 | W12 with `actor_note='cfo@seller.com'` | `app` | `InsufficientPrivilege` — **note confers nothing** | P2 |
| AC3 | W19b `approve_recovery_action` | `app` | `InsufficientPrivilege` | P4 |
| AC4 | W19a `PENDING_APPROVAL → QUEUED` | `app` | `RAISE` — rows 3, 4 are **not in W19a's allowlist** | P4 |
| AC5 | W20 | `app` | `InsufficientPrivilege` | P4 |
| AC6 | W21b | `app` | `InsufficientPrivilege` | P4 |
| AC7 | W21b with null `reason` or `actor_note` | `ops` | `RAISE` | P4 |
| AC8 | `SET ROLE baaki_ops` | `app` | `permission denied` (not a member) | P1 |
| AC9 | `SET ROLE baaki_owner` / `baaki_app` | `ops` | `permission denied` | P1 |
| AC10 | `SET SESSION AUTHORIZATION baaki_ops` | `app` | `permission denied` (not superuser) | P1 |
| **AC11** | Test harness (as owner) grants `EXECUTE` W12 to `app`, then `app` calls W12 | `app` | **`unauthorized_invoker`** from H17 — defence in depth survives grant misconfiguration; harness revokes after | P2 |
| AC12 | W19b by `ops` with `actor_note='someone else'` | `ops` | ✓ succeeds; `approved_by_role = 'baaki_ops'` (TEC), `approved_by_note = 'someone else'` (META) — recorded, not trusted | P4 |
| AC13 | `ops` executes any automatic writer (W01–W11, W13, W14a, W16–W18, W19a, W22, W23, W25) | `ops` | `InsufficientPrivilege` | P1+ |
| AC14 | W14b / W15 / W17b | `app` | `InsufficientPrivilege` | P3 |
| **PE — Payment event** | | | | |
| PE1 | W04 with neither evidence id | `app` | `RAISE` | P1 |
| PE2 | W04 with both evidence ids | `app` | `RAISE` | P1 |
| PE3 | W04 referencing `webhook_event.signature_ok=false` | `app` | `RAISE` | P1 |
| PE4 | W04 with a `provider_payload_hash` parameter | — | **no such parameter** (computed inside) | P1 |
| PE5 | W04 with an `amount_paise` / `provider_payment_id` parameter | — | **no such parameter** (extracted inside) | P1 |
| PE6 | W04 `provider_payload_raw` not a substring of the evidence raw body | `app` | `RAISE` | P1 |
| PE7 | W04 duplicate extracted `provider_payment_id` | `app` | `UniqueViolation` | P1 |
| PE8 | W04 `attribution_method='AMOUNT_MATCH'` | `app` | enum cast fails | P1 |
| PE9 | W04 payload with non-INR currency | `app` | CHECK | P1 |
| PE10 | W04 payload with `provider_status` outside accepted set | `app` | `RAISE` [A-R7] | P1 |
| **SR — sweep_run** | | | | |
| SR1 | W03 | `agent`, `sim`, `ops` | `InsufficientPrivilege` | P1 |
| SR2 | W03 with a `raw_response_hash` or `item_count` parameter | — | **no such parameter** (computed inside) | P1 |
| SR3 | W03 same `raw_response` twice | `app` | `ON CONFLICT` returns existing id; one row | P1 |
| SR4 | W04(sweep) item not a substring of `sweep_run.raw_response` | `app` | `RAISE` | P1 |
| SR5 | W04(sweep) item already recorded (same `provider_payment_id`) | `app` | `UniqueViolation` | P1 |
| SR6 | W04 referencing a nonexistent `sweep_run_id` | `app` | FK | P1 |
| SR7 | `agent/` or `policy/` importing `db/writers/payment` | — | import-graph failure | P1 |
| SR8 | W03 without `provider_call_id`, or whose `provider_call.request_host ≠` pinned provider host | `app` | `RAISE` | **P4** |
| **SR9** | **`baaki_app` fabricates a complete `raw_response` and records it via W03** | `app` | **Accepted by the database.** Documented residual (§6.23): sweep origin authenticity is application-enforced (TLS + API credential + host pin). Compensating controls: SR8, `webhook_missing` flag on every sweep-only payment, ops-panel alert | P4 |
| **WS — webhook signature** | | | | |
| WS1 | W02 with a `signature_ok` parameter | — | **no such parameter** (computed inside from `provider_secret`) | P1 |
| WS2 | W02 with a wrong signature | `app` | row stored `signature_ok=false`; W04 refuses it (PE3) | P1 |
| WS3 | W02 with tampered `raw_body` and the original signature | `app` | `signature_ok=false` | P1 |
| WS4 | Fabricated webhook without the secret | `app` | Cannot produce `signature_ok=true` — **the app process does not hold the webhook secret** | P1 |
| **LW / LK / D / T / O / K / X / H / TX / L** | as v3.1 (§6.16 v3.1 rows), with W19→W19a/W19b and W21→W21a/W21b substituted | | | |
| O4 | W11 with a `validation_id` whose intent ≠ `UNSUBSCRIBE` | `app` | `RAISE` | P2 |
| O5 | W12 / W11 by `agent` | `agent` | `InsufficientPrivilege` | P2 |
| K4 | W21a by `app` (activation — safe direction) | `app` | **✓** | P4 |
| X11 | W19b on an action not in `PENDING_APPROVAL` | `ops` | `RAISE` | P4 |
| X12 | W19b with stale `snapshot_hash` | `ops` | `RAISE` (re-decide) | P4 |

## 6.17 What is proven, and by what

**PostgreSQL proves:** role capabilities incl. that `session_user` cannot be forged without a role's
credential (no cross-membership; `SET SESSION AUTHORIZATION` needs superuser) · table/column
privileges · `EXECUTE` capabilities · every CHECK/FK/UNIQUE/trigger · append-only F/D tables ·
every invariant re-validated inside a writer (P2–P13, TPL, CP5, over-credit, W04 extraction and
containment, W19a/W17a allowlists, **H17 invoker assertions, W02 HMAC computation**).

**The application proves:** import boundaries · kernel token · `ExecutableDecision`/`ClaimedPaise`/
`RawJson` types · model parsing · policy semantics · which module invokes a permitted writer ·
**that the bytes handed to W03 came from the provider over TLS with the API credential** (§6.23).

**Red-team tests prove** the boundaries hold in the implemented system.

**PostgreSQL does not prove Python module identity, and does not prove the network origin of a
sweep response.** Those residuals are named, bounded (SR9), and compensated.

## 6.18 Opt-out authority

**Inbound (evidence-gated, `baaki_app`):** Interpreter `UNSUBSCRIBE` → W07 → validator (checks
01–09) → W08 `PASS` → **W11** `opt_out_contact_from_evidence(contact_id, validation_id)`, which
verifies the validation exists, `outcome=PASS`, `normalized.intent='UNSUBSCRIBE'`,
`normalized.contact_id = p_contact_id`, contact ∈ validation.account. Records
`opted_out_by_role='baaki_app'`, `opted_out_source='INBOUND_UNSUBSCRIBE'`, `validation_id`.
**Bypasses the kernel by design**: a restriction, not an authority grant, and it must apply even
under kill switch.

**Human (`baaki_ops`):** **W12** `opt_out_by_operator(account_id|contact_id, actor_note)` — H17
asserts `session_user='baaki_ops'`; records `opted_out_by_role='baaki_ops'` (TEC) and `actor_note`
(META). **Account-level opt-out is human-only**: an inbound message identifies a contact, not an
account.

**The LLM never mutates opt-out:** `baaki_agent` cannot execute W11/W12; `agent/` cannot import them;
W11 needs a validator-produced `validation_id`.

**Clearing: monotonic. No writer, no role, no path sets `opt_out`/`opted_out` back to FALSE.**
`UNIQUE(account_id, channel, address_hash)` prevents re-adding the address. System behaviour, not a
compliance claim. **P1:** columns seeded, immutable at runtime.

## 6.19 Kill-switch authority

| Aspect | Contract |
|---|---|
| Activate | **W21a** `kill_switch_activate(reason, actor_note)`. Invokers: `baaki_app` (ledger-invariant checker, `reason='LEDGER_INVARIANT_BREACH'`) or `baaki_ops` (human). Safe direction — anyone with either role may halt. Audit records `actor_role=session_user` |
| Deactivate | **W21b** `kill_switch_deactivate(reason, actor_note)` — **`EXECUTE` for `baaki_ops` only + H17**. `reason`, `actor_note` non-null. **No automated caller exists**: `baaki_app` holds no grant and fails H17 even if it did |
| Direct SQL | No role holds `UPDATE` on `organization` |
| Phase | P4 writers; P1 seeded FALSE, immutable; P2 kernel reads it (P0) |
| Effect | **Immediate**: P0 `BLOCK` in every new snapshot; W19a #6 re-checks P0 at claim → **queued actions not claimed**; validator check 01 rejects new proposals |
| In-flight | A provider request already sent completes and its result is recorded via W19a #7–#9 (cannot be un-sent); no new call starts; executor at L4 |

## 6.20 PaymentEvent provenance — closed

**Only W04 creates a `payment_event`; only `baaki_app` may execute W04; W04 is invoked only from
`reconcile/`.** Signature: `record_payment_event(webhook_event_id | sweep_run_id, provider_payload_raw,
attributed_invoice_id, attribution_method)`. **No financial field is a parameter.**

W04, inside the function:
1. Exactly one evidence id (`(webhook_event_id IS NULL) <> (sweep_run_id IS NULL)`), else `RAISE`.
2. Webhook: `webhook_event.signature_ok = TRUE` else `RAISE`; `provider_payload_raw` must be a
   literal substring of `webhook_event.raw_body` (`position(...) > 0`) else `RAISE`.
   Sweep: `provider_payload_raw` must be a literal substring of `sweep_run.raw_response` else `RAISE`.
3. `provider_payload_hash := encode(sha256(provider_payload_raw::bytea),'hex')` — computed, stored.
4. **Extract** `provider_payment_id`, `amount_paise`, `currency`, `provider_status`, `paid_at` from
   `provider_payload_raw::jsonb` using **constant JSON paths compiled into the function body**
   [ASSUME A-R8]. Missing/unparseable ⟹ `RAISE`. Static SQL (`#>>` with constant arrays) — H7 holds.
5. `source := WEBHOOK | SWEEP` derived from which id is present.
6. `provider_status` ∈ accepted set [A-R7]; `currency = 'INR'`; `amount_paise > 0`.
7. `attribution_method = UNATTRIBUTED ⟺ attributed_invoice_id IS NULL`; invoice exists.
8. `UNIQUE(provider_payment_id)`; `UNIQUE(webhook_event_id) WHERE NOT NULL`; set
   `webhook_event.processed_at`.

**Provider-authoritative and immutable:** `provider`, `provider_payment_id`, `amount_paise`,
`currency`, `provider_status`, `paid_at`, `provider_payload_raw`, `provider_payload_hash`, `source`.
**Baaki-owned:** attribution columns (W04 sets from `notes`/`reference_id` resolution done by the
parser; W20 changes once).

| Prohibited origin | Why impossible |
|---|---|
| LLM / `AgentProposal` | `baaki_agent` has no `EXECUTE`; `agent/` cannot import `db/writers/payment`; no schema field maps to a payment; **W04 has no financial parameters to influence** |
| `PolicyDecision` / kernel | `policy/` cannot import `db/writers/payment` |
| Frontend / client | No endpoint reaches W04; the client cannot create a `webhook_event` with `signature_ok=true` (W02 computes it from a secret the client does not hold) |
| Simulator | `baaki_sim` no `EXECUTE`; `sim/` cannot import writers; drives the fake provider, which emits **signed** webhooks (fake holds the test secret) |
| Debtor self-report | `ALREADY_PAID_CLAIM` → P6 + sweep; no writer reachable |
| Caller-supplied amount | **No parameter exists.** Amount is extracted from evidence bytes that must be contained in a verified/recorded raw body |

## 6.21 Reattribution authority — W20

Invoker **`baaki_ops` only** (`EXECUTE` + H17); `actor_note` is META, `reattributed_by_role =
session_user` is TEC. Requires `attribution_method = UNATTRIBUTED`, an `UNAPPLIED_CASH` credit for the
event, `reattributed_at IS NULL`, non-empty `justification`, target invoice exists. **Human-only; no
automated caller** (import graph: not imported by `reconcile/`). Once. Original provider fields
untouched. Cross-account permitted (money is in suspense); **cross-invoice impossible** (would need
reversal, F7). Ledger: DR `UNAPPLIED_CASH`; CR `AR` capped; CR `BUYER_CREDIT` excess;
`source=REATTRIBUTION`. Counts as recovery in the metric.

## 6.22 Trusted actor authority — the mechanism

**Problem:** "human-only" cannot mean "the caller passed `actor='alice'`". A string is not authority.

**Mechanism [DD]:** authority is the **connection role**, established by the credential used to
connect. Inside every writer, `session_user` is the authenticated login role — not a GUC (H13), not
`current_user` (which `SECURITY DEFINER` changes to the owner), not changeable by `SET ROLE` (no
cross-membership) or `SET SESSION AUTHORIZATION` (superuser only).

| Actor class | PostgreSQL role | How established | Holds |
|---|---|---|---|
| **Automated system** | `baaki_app` | App runtime credential | `EXECUTE` on automatic writers; W21a |
| **Human operator** | `baaki_ops` | Operator credential (`BAAKI_OPS_DSN`), held by operators, **absent from the app runtime env** | `EXECUTE` on the 7 human-only writers + W21a + W24; **no DML** |
| **Model process** | `baaki_agent` | Agent runtime credential | W07, W24 |
| **Simulator** | `baaki_sim` | Sim credential | nothing |

**For every sensitive writer:**

| Writer | Invoker role | Allowed actor class | Actor identity | Unauthorized caller fails by |
|---|---|---|---|---|
| W12 opt-out by operator | `baaki_ops` | Human | `opted_out_by_role = session_user` (TEC); `actor_note` (META) | `EXECUTE` denied → `InsufficientPrivilege`; H17 → `unauthorized_invoker` |
| W14b open dispute by operator | `baaki_ops` | Human | `opened_by_role` TEC; note META | same |
| W15 resolve dispute | `baaki_ops` | Human | `resolved_by_role` TEC; note META | same |
| W17b PTP review | `baaki_ops` | Human | `reviewed_by_role` TEC; note META | same |
| **W19b approve/reject action** | `baaki_ops` | Human | `approved_by_role` TEC; `approved_by_note` META | same; **W19a's allowlist excludes rows 3–4**, so `baaki_app` cannot reach them through the automatic writer either |
| W20 reattribution | `baaki_ops` | Human | `reattributed_by_role` TEC; note META | same |
| W21b kill-switch deactivate | `baaki_ops` | Human | audit `actor_role` TEC; note META | same |
| W21a kill-switch activate | `baaki_app`, `baaki_ops` | System or human | audit `actor_role` TEC | denied for `agent`/`sim` |
| W11 opt-out from evidence | `baaki_app` | System, evidence-gated | `validation_id` (verified in body) | `EXECUTE` denied; evidence check `RAISE` |
| W14a open dispute from evidence | `baaki_app` | System, evidence-gated | `validation_id` | same |
| W18 PTP verdict | `baaki_app` | System, ledger-gated | `payment_event_id[]` (verified) | `EXECUTE` denied |
| W03 sweep run | `baaki_app` | System | `created_by_role` TEC | denied for `ops`/`agent`/`sim` |
| All other writers | per §6.6 | System / model | — | `EXECUTE` denied |

**Stated limitation:** MVP has no per-person authentication. `baaki_ops` is a shared operator
credential; `actor_note` records *which* operator and is an attestation. What is non-forgeable is
*that an operator credential was used*. [FUTURE] per-operator roles or an authenticated ops service
issuing short-lived role credentials.

## 6.23 Sweep-run provenance contract

| Aspect | Contract |
|---|---|
| Creator | **W03** `record_sweep_run`, `EXECUTE` for `baaki_app` only, invoked only from `reconcile/sweep` |
| Authorizing operation | One **POS-3 `ListPayments`** call by the Razorpay adapter over TLS to the pinned provider base URL, authenticated with the API key [ASSUME A-R9: basic auth] |
| Raw storage | `raw_response TEXT NOT NULL` — the complete response body, verbatim |
| Hash | `raw_response_hash := sha256(raw_response)` — **computed by W03, not a parameter** |
| Provider identifiers | `provider`; `window_from`, `window_to`; `item_count` computed by W03 from a constant JSON path [A-R8]; each item's `provider_payment_id` is extracted later by W04 |
| Verification | **REST responses carry no provider signature.** Authenticity therefore rests on: the adapter's pinned host + TLS + API credential (application), **[P4] W03 requires `provider_call_id` referencing the adapter's recorded call with `request_host = pinned host` and `status = 2xx`** (database checks the record exists and matches), and `created_by_role = session_user` (TEC). This is weaker than the webhook path's HMAC, and the document says so (SR9) |
| Uniqueness / idempotency | `UNIQUE(provider, raw_response_hash)`; `ON CONFLICT DO NOTHING RETURNING` — a repeated identical fetch yields the same run id; items dedupe at W04 (`provider_payment_id`) |
| Role | `baaki_app` only; `ops`, `agent`, `sim` → `InsufficientPrivilege` (SR1) |
| Fabrication by app | **Possible at the database level (SR9)** — the residual. Compensating controls: SR8 host pin [P4]; every sweep-sourced `payment_event` without a matching webhook is flagged `webhook_missing` on the ops panel and counted; sweep cadence bounded to once per business day per window |
| Fabrication by agent / sim | Impossible: no `EXECUTE`, no import path |
| LLM influence on financial fields | **None possible**: W03 takes no financial fields; W04 extracts them from bytes that must be contained in `raw_response`; `agent/` cannot reach either writer |
| Transaction boundary | **T7:** W03 commits in its own transaction; then **each item** runs its own T6 (W04 → W05\|W06 → …) so one bad item cannot roll back the run or other items |
| Replay / failure | Re-running the sweep for the same window re-fetches; identical bytes → same run (no-op); different bytes (new payments) → new run; previously recorded items → `UniqueViolation` at W04, treated as already-recorded; a failing item is logged with its `sweep_run_id` and retried on the next sweep |

The webhook and sweep paths are **equal** in: role restriction, writer-computed hashes, containment
of every payment item in stored raw evidence, function-side extraction of financial fields, and
uniqueness. They **differ** in origin authentication: webhook = HMAC computed inside W02 from a
secret the app never holds (database-verified); sweep = TLS + API credential + host pin
(application-verified). Claiming otherwise would be an overstatement.

---

# 7. Model-Provider Boundary **[P2]**

**Two calls [DD].** Call 1 Interpreter — required; deterministic logic is genuinely insufficient
for code-switched contextual replies; measured against a regex baseline (§11). Call 2 Proposer —
hypothesis under test; `RULES_ONLY` does this deterministically. ~~Call 3 Copywriter~~ — cut:
un-evaluable in a self-authored simulator and the sole path for model numerals into outbound
text. All copy is `template_registry` templates with DB-substituted slots.

**Call 1 `interpretation.v1`:** `intent ∈ {WILL_PAY_ON_DATE, REQUEST_INSTALLMENTS, DISPUTE_AMOUNT,
DISPUTE_DELIVERY, ALREADY_PAID_CLAIM, WRONG_CONTACT, NEEDS_DOCUMENT, UNSUBSCRIBE, NO_CLEAR_INTENT}` ·
`promised_date_raw`, `promised_amount_raw` **verbatim spans, never typed** · `invoice_refs: string[]`
(hints; §6.8.3) · `contact_correction` · `sentiment` · `confidence` · `evidence[{field, quote}]`
(literal substrings). Input: message, timestamp, invoice number+status, enumerated `contact_id`s.
**No amounts, no ledger, no other accounts, no keys.** `temperature=0`; seed [ASSUME A-L1];
8 s timeout → L1; ≤1 transport retry; never on schema violation.

**Call 2 `action_proposal.v1`:** `action ∈ ActionType` · `contact_id ∈ supplied set` · `channel` ·
`template_id ∈ supplied set` · `followup_days 1..14` · `rationale ≤280` (display only, never parsed)
· `confidence`. **No amount field. No `OTHER`. No free-text recipient. No `reason_code` or
`assignee_queue` — both are kernel-derived (§1.5.1).** 6 s timeout → `RULES_ONLY` heuristic.

**Provider neutrality:** `providers/llm/base.py` `complete_structured(prompt, schema, timeout) ->
RawResponse` — no tools, no streaming, no state. `openai.py`, `fixtures.py` (CI replay). The
provider has **no financial tools, no Razorpay client, no database session, no network authority
beyond `providers/llm/`**. `agent/` connects as `baaki_agent`. CI blocks all sockets except the
test database.

---

# 8. Database Architecture — PostgreSQL 16

**[DD]** Postgres, not SQLite: `SKIP LOCKED`, `SERIALIZABLE`, column-level and per-role grants,
`SECURITY DEFINER`, deferred constraint triggers, native enums — I3, I8, I10 depend on them.

## 8.1 Money

`BIGINT` paise. `CREATE DOMAIN baaki.paise AS BIGINT CHECK (VALUE > 0)` for amounts; view output
plain `BIGINT` (may be 0). No float/`NUMERIC`/`MONEY`; `tests/arch/test_no_float_money.py`.

## 8.2 Tables by phase

| Table | Class | PK | Key constraints | Ph |
|---|---|---|---|---|
| `organization` | C | `org_id` | | P1 |
| `account` | M | `account_id` | `UNIQUE(org_id, external_ref)`; `IDX(org_id, opt_out)` | P1 |
| `contact` | M | `contact_id` | `UNIQUE(account_id, channel, address_hash)` | P1 |
| `template_registry` | C | `template_id` | `CHECK` allowed `(action_type, purpose)` | P1 |
| **`provider_secret`** | C | `provider` | `webhook_secret TEXT NOT NULL`, `rotated_at`; **no grants to any application role**; seeded by `bootstrap/secrets.sql` | P1 |
| `invoice` | F | `invoice_id` | `UNIQUE(org_id, invoice_number)`; `IDX(state, due_date)`; `IDX(account_id, state)`; **no `outstanding_paise`** | P1 |
| `ledger_entry` | F | `entry_id` | `UNIQUE(payment_event_id, account_code) WHERE NOT NULL`; account-code CHECK; `(source, class)` CHECK; `AR ⟺ invoice_id` CHECK; `(source=ISSUANCE) = (payment_event_id IS NULL)` CHECK; `IDX(invoice_id)`, `IDX(txn_id)`; deferred balance trigger; one-invoice-per-txn trigger | P1 |
| `payment_event` | F | `payment_event_id` | `UNIQUE(provider_payment_id)`; `UNIQUE(webhook_event_id) WHERE NOT NULL`; `CHECK currency='INR'`; `CHECK (webhook_event_id IS NULL) <> (sweep_run_id IS NULL)`; `CHECK (attribution_method='UNATTRIBUTED') = (attributed_invoice_id IS NULL)`; `provider_payload_raw TEXT NOT NULL`; `provider_payload_hash CHAR(64) NOT NULL` | P1 |
| `webhook_event` | D | `event_id` | `UNIQUE(provider, dedupe_key)`; `raw_body TEXT NOT NULL`; `signature_header TEXT`; `signature_ok BOOLEAN NOT NULL` (**computed by W02**) | P1 |
| `sweep_run` | D | `sweep_run_id` | `raw_response TEXT NOT NULL`; `raw_response_hash CHAR(64) NOT NULL` (**computed by W03**); `item_count INT`; `created_by_role TEXT NOT NULL` (`session_user`); `provider_call_id UUID NULL` (FK added P4, required by W03 from P4); **`UNIQUE(provider, raw_response_hash)`** | P1 |
| `agent_proposal` | D | `proposal_id` | `UNIQUE(invoice_id, business_date, kind, input_hash)`; `CHECK arm='TREATMENT'`; parse CHECK; money-key CHECK; `IDX(trace_id)` | P1 |
| `validation_result` | D | `validation_id` | `UNIQUE(proposal_id)`; PASS/REJECT CHECKs | P1 |
| `policy_decision` | D | `decision_id` | 8 CHECKs (§6.7); `UNIQUE(validation_id, business_date)`; partial `UNIQUE(invoice_id, business_date, arm) WHERE proposal_id IS NULL`; `trg_decision_linkage`; `IDX(trace_id)`, `IDX(invoice_id, business_date)` | P1 |
| `recovery_action` | D | `action_id` | `decision_id NOT NULL UNIQUE`; **`uq_action_idempotency`: `UNIQUE(idempotency_key) WHERE state <> 'SUPERSEDED_DUPLICATE'`** (IK1); allowlist trigger; type-match trigger; `approved_by_role TEXT`, `approved_by_note TEXT`; `IDX(state, next_attempt_at)` | P1 |
| `outbox` | D | `outbox_id` | `UNIQUE(action_id)`; `IDX(claimed_at NULLS FIRST, outbox_id)` | P1 |
| `promise_to_pay` | D | `ptp_id` | partial `UNIQUE(invoice_id) WHERE state IN ('ACTIVE','DUE')`; `UNIQUE(validation_id)`; `trg_ptp_verdict_writer` | P3 |
| `dispute` | D | `dispute_id` | partial `UNIQUE(invoice_id) WHERE state IN ('RAISED','UNDER_REVIEW')` | P3 |
| `provider_call` | D | `call_id` | append-only | P4 |
| `message` | D | `message_id` | `UNIQUE(action_id, direction) WHERE direction='OUTBOUND'` | P4 |
| `idempotency_record` | M | `key` | | P4 |
| `audit_event` | D | `audit_id` | `IDX(trace_id)`; append-only | P4 |
| `experiment_assignment` | D | `assignment_id` | `UNIQUE(account_id, experiment_id)`; no grant to `sim` | P5 |

## 8.3 Idempotency boundaries (I7)

| Boundary | Constraint | Ph |
|---|---|---|
| Client → API | `idempotency_record(key)` | P4 |
| Scheduler → proposal | `agent_proposal UNIQUE(invoice_id, business_date, kind, input_hash)` | P1 |
| Executor → provider | `recovery_action` partial `UNIQUE(idempotency_key) WHERE state <> 'SUPERSEDED_DUPLICATE'` — one live action per key (IK1) | P1 |
| Provider → us | `webhook_event UNIQUE(provider, dedupe_key)` · `payment_event UNIQUE(provider_payment_id)` · `ledger_entry UNIQUE(payment_event_id, account_code)` | P1 |

## 8.4 Concurrency

Reconcile (T6): `SERIALIZABLE`, invoice `FOR UPDATE` inside W05 · outbox claim: `READ COMMITTED`,
`FOR UPDATE SKIP LOCKED` inside W19a · snapshot: `REPEATABLE READ` · approval: `FOR UPDATE` on action
inside W19b. Lock order `account → invoice → promise_to_pay`; invoices ascending **[DD]**.
Serialisation failures retry ≤3× **[IMPL]** — safe because writers are idempotent by constraint.

## 8.5 Transaction boundaries — see §6.6 (T1, T2, T5, T6, T7, W19a claim/result split, W19b).

## 8.6 Extensions — `pgcrypto` **[IMPL]** for `hmac()` and `digest()` inside W02/W03/W04.

---

# 9. Razorpay Boundary **[P4]**

**[ASSUME] Nothing here is verified against Razorpay documentation.**

## 9.1 Authority split **[DD]**

Razorpay: whether a payment occurred, amount, timestamp, status. Baaki: attribution (by
reference), ledger, kernel decisions, PTP verdicts. A debtor's "I already paid" → P6 + sweep,
never a payment (I6).

## 9.2 Provider Operation Set — POS-4 **[DD]**

| # | Operation | Sent | Purpose |
|---|---|---|---|
| POS-1 | `CreatePaymentLink` | `amount_paise` (kernel-computed), `INR`, `reference_id=action_id`, `notes={invoice_id, action_id, trace_id}`, expiry, contact ref | Execute `SEND_PAYMENT_LINK` |
| POS-2 | `FetchPaymentLink` | link id | Confirm terminal status (§3.3 #15 evidence) |
| POS-3 | `ListPayments` | time window | Sweep (T7) |
| POS-4 | `CancelPaymentLink` | link id | Compensation (#16) |

Nothing else has an adapter method: no refunds (F4), settlements, orders, subscriptions, customers.
**Test mode only** — `RAZORPAY_KEY_ID ~ '^rzp_test_'` asserted at boot; live mode aborts.

## 9.3 Attribution — by reference, never by amount **[DD]**

`notes.invoice_id` → `NOTES_INVOICE_ID` · else `reference_id` → action → `REFERENCE_ACTION_ID` ·
else `UNATTRIBUTED` → W06 → human queue → possibly W20. `attribution_method` has no amount-based
member; `tests/arch/` greps `reconcile/` for amount lookups.

## 9.4 Webhook receive and process

```
T5  1 raw body bytes + signature header                                        [ASSUME A-R1]
    2 W02 record_webhook_event(provider, raw_body, signature_header, received_at)
         inside W02: secret := provider_secret[provider]; signature_ok := hmac(raw_body, secret, sha256) = header
                     dedupe_key := event id path | SHA256(type‖entity‖status)   [ASSUME A-R2]
         ON CONFLICT DO NOTHING; COMMIT; return 200 if signature_ok else 400
T6  3 parser locates the payment item substring in raw_body and resolves attribution (notes / reference_id)
    4 W04 record_payment_event(webhook_event_id, provider_payload_raw, attributed_invoice_id, method)
         inside W04: containment check; hash; extract id/amount/currency/status/paid_at   [ASSUME A-R8]
      → (W05 | W06) → W18 if live PTP → W19a(→CONFIRMED)                          one SERIALIZABLE txn
```
**The application process never holds the webhook secret**; it lives only in `provider_secret`, readable
only inside W02. An unverified signature is recorded `signature_ok=false`, answered 400, and **never
processed** (W04 refuses it).

## 9.5 Error taxonomy

`RETRYABLE` (429/5xx/connect timeout) · `AMBIGUOUS` (read timeout after send → fetch-by-reference
first) · `TERMINAL_CLIENT` (400) · `AUTH` (401/403 → L3) · `NOT_FOUND` on fetch → "not yet
visible", retried by sweep.

## 9.6 Exceptions — nothing dropped

Unknown invoice → `UNAPPLIED_CASH` + queue · overpayment → §6.12 · underpayment → outstanding
reduced, PTP `PARTIALLY_KEPT` at grace · invalid signature → rejected, alerted.

## 9.7 The sweep **[DD]**

> Webhooks are an optimisation. The sweep is the guarantee.

Every simulated business day and on demand at P6: the adapter calls POS-3 against the pinned host; W03
records the run (raw response verbatim; hash and item count computed inside; `created_by_role`); then
per item T6 through the identical path (§6.23). State converges
with 100% webhook loss. If A-R1/A-R2/A-R3 are all wrong, the sweep — needing only a payments read —
still reconciles.

---

# 10. Evaluation Methodology **[P5]**

## 10.1 Permitted claims **[DD]**

Permitted: "In a synthetic environment with pre-registered, arm-blind debtor parameters, the
treatment arm recovered X% more of at-risk AR than control, 95% CI [a,b], n accounts, N seeds" ·
"LLM marginal contribution over rules-only was Y%, CI [c,d]" · "Interpretation accuracy on held-out
was Z% vs W% regex". **Not permitted:** "Baaki recovers X% more revenue" · DSO claims ·
generalisation to real receivables · "significant" when the CI crosses zero · any regulatory or
compliance claim. The result is evidence of mechanism and measurement discipline.

## 10.2 Arms

`CONTROL` (static D+3/7/15) · `RULES_ONLY` (deterministic interpreter + engineered decision tree)
· `TREATMENT` (calls 1+2). Kernel, executor, ledger, reconciler, simulator **byte-identical**.

## 10.3–10.7 Design

Unit **account** **[DD]**; stratified 1:1:1; immutable assignment via W25. Primary metric:
`Σ recovered/Σ at_risk` difference, `recovered` = AR credits with `source ∈ {PAYMENT, REATTRIBUTION}`
in window, `at_risk` fixed at t₀; **ITT on every assigned account**; 45 business days; primary
`TREATMENT` vs `CONTROL`, secondary vs `RULES_ONLY`. Inference: **across-seed t-interval (S ≥ 20)
primary**, within-run cluster bootstrap over accounts secondary; no per-invoice CIs.
**`MDE ≈ 2.80 × SE_AA`** measured from the A/A run; 500 accounts/arm [ASSUME A-E1], target ≤5pp
[ASSUME A-E2] reported as achieved or not. **A/A in CI must show zero lift. SRM failure blocks the
result.** TRAIN 60% / DEV 20% / HELD-OUT 20% **touched once**, touch count displayed.

## 10.8 Simulator blinding (I8)

`simulate_response(message_features, persona, invoice_state, rng(seed, account_id, day))` — `arm`
not a parameter · `baaki_sim` has no `SELECT` on `experiment_assignment` · `sim/` ↛ `experiment/`.
The simulator interacts with Baaki **only through the fake provider** (`providers/razorpay/fake`),
which emits signed webhooks and serves POS-3 — never through writers. Live privilege assertion is
**P5**; P1 establishes the role and the import rule and does not claim the live test. The fake provider
holds the **test** webhook secret so its emitted webhooks verify inside W02 exactly as real ones would.

## 10.9 Freeze **[DD]** — `simulator_personas.yaml` hashed before any tuning; hash on dashboard.

---

# 11. LLM Ablation **[P5]**

Comparison 1: `TREATMENT` vs `RULES_ONLY`, pre-committed null-reporting rule **[DD]**. Comparison
2 on held-out vs regex baseline: intent macro-F1 + confusion matrix · PTP date/amount exact match ·
evidence span validity · ambiguous-date accuracy **and abstention** · Hinglish subset · **false
interpretation rate** (harm metric, reported as prominently as accuracy). ≥30% hand-authored
held-out, reported separately. Falsification conditions stated in advance and displayed
regardless of outcome.

---

# 12. Failure Handling

## 12.1 Ladder **[DD]** — `L0` full agent · `L1` deterministic agent (LLM failure/rejection) · `L2`
static cadence · `L3` read-only (provider AUTH failure) · `L4` halted (kill switch / ledger
breach). Level recorded on every decision. Never blocks on the model; never fails open.

## 12.2 Matrix

| Failure | Response | Financial safety | Audit |
|---|---|---|---|
| LLM timeout / 5xx / bad JSON / schema | ≤1 transport retry → L1; **zero re-prompts** | No action without a decision | proposal row, raw verbatim |
| Missing evidence / ambiguous date or amount | Discard / human queue; **no guess** | No PTP | reason code |
| Low confidence | Authority capped (I4) | Cannot raise | `effective_confidence` |
| Razorpay unavailable | `FAILED_RETRYABLE`, same key | No duplicate | `provider_call` |
| Razorpay AUTH | `FAILED_TERMINAL` + L3 | Execution stops | alert |
| Duplicate / out-of-order webhook | No-op (3 layers; convergent reconciler) | No double count | dedupe counter |
| Missing webhook | Sweep via identical T6 | Converges | `source=SWEEP` |
| Unknown payment | `UNAPPLIED_CASH` + queue; **never dropped** | Money always in the ledger | suspense balance |
| Overpayment | §6.12 | Never negative | flag |
| Unverified webhook signature | Recorded `signature_ok=false`, 400; W04 refuses | Cannot become money | rejected event row |
| DB unavailable | Webhook 5xx (provider retries); executor stops claiming | Nothing executes without a committed decision | redelivery + sweep |
| Serialisation failure | Retry ≤3× | Idempotent by constraint | counter |
| Executor crash mid-flight | Lease expiry → `QUEUED`; same key; fetch-by-reference | No duplicate | attempt history |
| Writer raises mid-T2 / mid-T6 | **Whole transaction rolls back** (H11) | No decision-without-action; no event-without-ledger | — |
| Kill switch activated | P0 `BLOCK`; claims refused; in-flight calls complete and record | No new provider call | `audit_event` |
| Ledger invariant breach | **L4 via W21a (`reason='LEDGER_INVARIANT_BREACH'`, `actor_role='baaki_app'`)**; reads remain | Stop acting when truth is in question | alert |
| Clock / DST | UTC + explicit org-tz business dates | Caps not evadable | business date on rows |

---

# 13. Scope, Phase Boundaries, and Object Counts

## 13.1 Spine **[DD]** — Promise State Machine → Money Safety Kernel → Razorpay Verification → Evaluation Harness

## 13.2 Phase boundaries

**Rule:** a phase may create a schema, type, or writer that a later phase will use. It may not
create the code that *decides* or *orchestrates* on it.

| Phase | Builds | Does not build |
|---|---|---|
| **P1 Foundation** | Types (`Paise`, `ClaimedPaise`, `RawJson`, enums, ids, `Clock`, errors); contracts §1 + union + token; **15 tables, 19 enums, 1 view, 5 triggers, 10 writers W01–W10, 6 roles, 1 extension**, grants; `from_decision`; idempotency key; arch/contract/db/redteam tests | Validator ladder, kernel rules, W11–W25, any transition, outbox worker, providers, reconciler orchestration, PTP, dispute, experiment, simulator, API, UI. (HMAC computation itself is inside W02 and therefore P1; the *receiver* that calls W02 is P4) |
| **P2 Policy** | Validator (16 checks, 20 reasons), ladder P0–P14, `min()` cap, ruleset hashing, snapshot assembler incl. `candidate_invoice_ids`, SC1–SC4, TPL in kernel, kernel wired to token; LLM adapter + fixtures; call-1/2 schemas; `RULES_ONLY` interpreter + tree; **W11, W12; +1 enum** | Execution |
| **P3 Promise** | **2 tables, 3 enums, 8 writers (W13, W14a/b, W15, W16, W17a/b, W18), 1 trigger**; PTP + dispute + aging behaviour; reconciler PTP evaluation | Provider calls |
| **P4 Execution & Reconciliation** | **4 tables, 2 enums, 8 writers (W19a/b, W20, W21a/b, W22–W24)**; outbox worker; Razorpay adapter POS-4; HMAC verifier; parser (PE5); attribution; sweep orchestration; ops scripts (kill switch, reattribution, approvals); API | Experiment |
| **P5 Evaluation** | **1 table, 1 writer W25, 1 role**; arm-blind simulator + fake provider; virtual-clock runner; metrics, bootstrap, A/A, SRM, MDE, pre-registration; dashboard; live sim-blind test | — |

## 13.3 Object counts — phase-qualified

| Object | P1 | +P2 | +P3 | +P4 | +P5 | **Final MVP** |
|---|---|---|---|---|---|---|
| Tables | **15** | 0 | 2 | 4 | 1 | **22** |
| Enums | **19** | 1 (`opt_out_source`) | 3 (`ptp_state`, `dispute_state`, `dispute_resolution`) | 2 (`provider_call_status`, `message_direction`) | 0 | **25** |
| Views | **1** | 0 | 0 | 0 | 0 | **1** |
| Writer functions | **10** (W01–W10) | 2 (W11, W12) | 8 (W13, W14a/b, W15, W16, W17a/b, W18) | 8 (W19a/b, W20, W21a/b, W22–W24) | 1 (W25) | **29** |
| — of which human-only (`baaki_ops`) | 0 | 1 (W12) | 3 (W14b, W15, W17b) | 3 (W19b, W20, W21b) | 0 | **7** |
| Triggers | **5**: `trg_ledger_balanced`, `trg_ledger_one_invoice_per_txn`, `trg_action_requires_executable_decision`, `trg_action_type_matches_decision`, `trg_decision_linkage` | 0 | 1 (`trg_ptp_verdict_writer`) | 0 | 0 | **6** |
| Roles | **6** (`owner`, `migrate`, `app`, **`ops`**, `agent`, `sim`) | 0 | 0 | 0 | 1 (`readonly`) | **7** |
| Extensions | **1** (`pgcrypto`) | 0 | 0 | 0 | 0 | **1** |
| Schemas | **2** (`baaki`, `baaki_write`) | 0 | 0 | 0 | 0 | **2** |

P1 tables (15): `organization`, `account`, `contact`, `template_registry`, **`provider_secret`**, `invoice`,
`ledger_entry`, `payment_event`, `webhook_event`, `sweep_run`, `agent_proposal`, `validation_result`,
`policy_decision`, `recovery_action`, `outbox`.
P1 enums (19): `proposal_kind`, `parse_status`, `arm`, `validation_outcome`, `rejection_reason`,
`verdict`, `action_type`, `action_state`, `invoice_state`, `dr_cr`, `ledger_source`, `channel`,
`degradation_level`, `template_purpose`, `suppress_reason` (5 values), `escalation_reason` (4 values),
`assignee_queue` (2 values) — members in §1.5.1 —,
`payment_source`, `attribution_method`.

## 13.4 MUST / SHOULD / CUT

**MUST:** §13.2. **SHOULD** (first to go): installment plan end-to-end; dispute workflow beyond
open/close; ops panel; covariate balance table; compensation path; >5 personas; approval diff view.
**CUT:** F1–F7 · LLM copywriting · real email/SMS delivery · auth/RBAC · multi-currency · OCR ·
ERP/CRM · voice · credit-scoring ML · **agent frameworks, RAG, vector DB, multi-agent orchestration**
(each adds an uncontrolled model→effect path; their absence *is* the contribution) · microservices ·
live-mode Razorpay.

---

# 14. Judge Attack Surface

**Q1 Why an LLM?** ~70% is deterministic and we say so first. The irreducible surface is
comprehension of code-switched contextual replies — **measured** against `RULES_ONLY` and a regex
baseline, null result displayed in the same font. §10.2, §11.

**Q2 How do you know the LLM did not control money?** The model's process connects as
`baaki_agent`: no privilege on any F- or D-class table but its own proposal insert, no `EXECUTE`
on any financial writer. Above that: no money field in any schema; `ClaimedPaise` cannot become
`Paise`; executor takes `ExecutableDecision`; every write re-validates inside a writer; F1–F7 are
not enum members anywhere. §6.3, §6.6, §6.16.

**Q3 Is the lift real?** Pre-registered endpoint + hash; account-level randomization; ITT; ≥20
seeds; SRM blocks; A/A in CI; MDE from measured null. §10.

**Q4 Simulator bias?** Cannot observe the arm (parameter, grant, import); frozen before tuning;
≥30% hand-authored held-out reported separately; interacts only via the fake provider. §10.8–10.9.

**Q5 Isn't this deterministic automation?** Mostly, by design; the one irreducible capability is
measured. §11.

**Q6 When OpenAI fails?** Nothing financial; L1. ≤1 transport retry; never on schema violation. §12.

**Q7 When webhooks fail?** The sweep reconciles via the identical T6. §9.7.

**Q8 Duplicate financial effects?** Four constraints; key excludes attempts/timestamps; three
stacked layers on revenue. §3.4, §8.3.

**Q9 Disputes?** P5 blocks pressure; PTP suspends; `RESOLVED_VALID` **freezes** — Baaki cannot
adjust a receivable. §2.5, §6.15.

**Q10 Opt-out?** P2 absolute; set only via evidence-gated or human writer; **monotonic — no path
clears it**. §6.18.

**Q11 Why trust the evaluation?** It is built to catch us: hash, A/A, SRM, touch counter, provable
blinding, falsification conditions, permitted-claims table. §10.1, §11.

**Q12 Can a compromised app `UPDATE` past the kernel?** `baaki_app` holds `UPDATE` on two columns
in the whole schema, neither carrying authority. §6.4, U1–U16.

**Q12b Can a compromised app approve its own actions or clear the kill switch by passing an actor
name?** No. Human-only writers are executable only by `baaki_ops` and assert `session_user` inside
the body; `approved_by_role` is written from `session_user`, never from a parameter. `baaki_app` is
not a member of `baaki_ops` and cannot `SET ROLE` into it. §6.22, AC1–AC14.

**Q13 Can someone fabricate a payment?** W04 has no financial parameters: it extracts amount and id
from bytes that must be contained in a `webhook_event` whose HMAC W02 verified against a secret the
app never holds, or in a `sweep_run` recorded by the adapter. Ledger writers accept no amount. The one
residual — an app process fabricating a sweep response — is named, bounded, and compensated (SR9,
§6.23). §6.20, PE1–PE10, SR1–SR9, WS1–WS4.

**Q14 What does Postgres actually prove?** Role/table/column/function capability, constraints,
writer-internal invariants. **Not** Python module identity — that is import graph + token + type,
and tested. §6.17.

**Q15 Why Postgres for a hackathon?** I3, I8, I10 degrade to convention without it. §8.

---

# 15. Definition of Done

| # | Statement | Artefact | Pass criterion | Ph |
|---|---|---|---|---|
| 1 | Every ledger rupee traces to its originating event | `e2e/test_trace_completeness` | `PAYMENT`/`REATTRIBUTION` lines → `payment_event` → verified evidence; `ISSUANCE` → invoice | P1 |
| 2 | Every payment traces to a decision or is explicitly unattributed | same | `attributed_invoice_id` → action → decision, or `UNATTRIBUTED` + suspense line | P4 |
| 3 | Every decision traces to the interpretation when AI was involved | same | `arm=TREATMENT ⟹ proposal_id NOT NULL`; `raw_response` retrievable | P1 |
| 4 | Every decision carries ruleset + snapshot hashes; replay reproduces | schema + `property/test_replay` | non-null; byte-identical replay | P1 / P2 |
| 5 | Red-team rows of §6.16 hold | `pytest tests/redteam/` | every P1-tagged row passes | P1 |
| 6 | Import boundaries hold | `arch/test_import_graph` | 0 forbidden edges | P1 |
| 7 | **I10 table privileges** | `db/test_table_grants` | app roles hold no DML on F/D tables; `INSERT` only on `account`, `contact` (`baaki_app`); `baaki_ops` holds no DML at all; `provider_secret` unreadable by every application role | P1 |
| 8 | **Column grants exact** | `db/test_column_grants` | `{account.risk_band, contact.active}` | P1 |
| 9 | **Every authority-sensitive column has a writer** | `db/test_column_capabilities` | non-PK − immutable − Safe = columns written by W01–W25 | P1 |
| 10 | **H1–H19** | `db/test_writer_hardening` | owner NOLOGIN; `prosecdef`; `search_path`; no dynamic SQL; PUBLIC revoked; EXECUTE matrix = §6.6 incl. the 7 human-only writers granted to `baaki_ops` only; every human-only body opens with the `session_user` assertion; no writer has a `signature_ok`, hash, amount, or `provider_payment_id` parameter | P1 (structural) / per writer phase |
| 11 | Writers write nothing on failure | `db/test_writer_atomicity` | row counts equal on every raising path | P1 |
| 12 | Linkage (§6.8) | `db/test_linkage` | LK1–LK5, LK7 | P1 |
| 13 | Executability partition | contract + db | P2, P3a/b, P5, P7, P8 at both layers | P1 |
| 14 | P9 | `db/test_action_decision_constraints` | `BLOCK`, `DEFER`, unknown verdict all refused | P1 |
| 15 | `from_decision` pure | `contract/test_recovery_action` | 0 queries, 0 sockets, deterministic | P1 |
| 16 | Ledger writers narrow; over-credit atomic | `db/test_ledger_writers` | LW1–LW10 | P1 |
| 17 | Projection correct; `issued_paise` not an input | `db/test_outstanding_projection` | issue/partial/full/over; view references only `ledger_entry` | P1 |
| 18 | Template compatibility | `db/test_template_compat` | T1–T4 | P1 |
| 19 | **F1–F7 unrepresentable** | `redteam/test_tier3_unrepresentable` | all seven absent from every surface | P1 |
| 20 | **PaymentEvent provenance** | `db/test_payment_provenance` | PE1–PE10; W04 signature has no financial parameters | P1 |
| 20b | **Sweep-run provenance** | `db/test_sweep_provenance` | SR1–SR7; W03 computes hash/count; `UNIQUE(provider, raw_response_hash)`; `created_by_role='baaki_app'` | P1 |
| 20c | **Webhook signature inside the database** | `db/test_webhook_signature` | WS1–WS4; correct HMAC → `signature_ok=true`; tampered body or wrong signature → `false`; `provider_secret` unreadable | P1 |
| 20d | **Trusted actor authority** | `db/test_trusted_actor` | AC1–AC14 for every human-only writer that exists in the phase; AC8–AC10 role isolation in P1 | P1 (roles) / P2–P4 (writers) |
| 21 | Simulator cannot access arm | `arch/test_sim_blind` | import + signature P1; **live privilege P5** | P1 / P5 |
| 22 | Webhook idempotent | `e2e/test_webhook_idempotency` | 10 deliveries → one event, one txn | P4 |
| 23 | Financial effects unique | `e2e/test_executor_idempotency` | 10 executions incl. crash → one provider resource | P4 |
| 24 | A/A shows no lift | `e2e/test_aa_null` | CI contains 0 over ≥20 seeds | P5 |
| 25 | Primary eval reports n, effect, CI, MDE | dashboard snapshot | all four; hash shown | P5 |
| 26 | LLM failure → deterministic fallback | `e2e/test_llm_outage` | 0 unauthorised effects; `L1` recorded | P2 |
| 27 | `OPT_OUT` hard stop; monotonic | `golden/test_optout` + O1–O3 | all types × combinations ⟹ `BLOCK`; no clearing writer | P2 |
| 28 | `DISPUTE` stops collection | `golden/test_dispute` | P5 exceptions only; PTP suspended | P2 / P3 |
| 29 | Payment confirmation from provider state only | `arch/test_no_amount_matching` + `e2e/test_paid_claim` | no amount attribution; claim → 0 ledger rows | P1 / P4 |
| 30 | Kill switch contract | `db/test_kill_switch` + K1–K4 | W21b executable by `baaki_ops` only and asserts `session_user`; `reason`+`actor_note` required; claims refused when on; W21a succeeds for `baaki_app` | P4 |
| 31 | Reattribution contract | `db/test_reattribution` | `baaki_ops` only; once; cross-invoice impossible; `reattributed_by_role=session_user`; justification required | P4 |
| 32 | No float money | `arch/test_no_float_money` | 0 in guarded packages | P1 |
| 33 | No network in default run | `arch/test_no_network` | static + runtime guard; `@network` excluded | P1 |
| 34 | Dependencies exact and reproducible | `arch/test_dependencies` + lock check | pyproject = approved list; forbidden absent; lockfile hashed, unchanged | P1 |
| 35 | Credential handling | structural + detector | env-only config (AST); fixtures `^<TEST_[A-Z_]+>$`; `.env` gitignored/absent; `.env.example` placeholders; **runtime section has no `MIGRATE`, `OWNER`, or `OPS` DSN and no webhook secret** (the secret lives only in `provider_secret`); known-prefix detector clean **(detector proves only known formats)** | P1 |

---

# 16. Assumption Register

| ID | Assumption | If false |
|---|---|---|
| A-R1 | HMAC-SHA256 over raw body, named header | Verification changes; receive-path shape unchanged |
| A-R2 | Unique event id per delivery | Composite `dedupe_key`; two more unique layers |
| A-R3 | Payment Links `notes` round-trip | `reference_id`, then local link↔invoice map; never amount |
| A-R4 | Payments list supports time windows | Sweep iterates known link ids |
| A-R5 | Fetch-by-reference for links | `GET` before every retry |
| A-R6 | Test mode can simulate a payment | Demo injects at the fake-provider boundary; ledger path unchanged |
| A-R7 | Provider status vocabulary (e.g. `captured`) | Accepted set adjusted in W04; contract unchanged |
| A-R8 | JSON paths for payment id / amount / currency / status / timestamp / event id / items array | **Constant paths inside W02/W03/W04 change via migration**; contract and containment checks unchanged |
| A-R9 | POS-3 authenticates with the API key over TLS; responses are unsigned | If responses carry a signature, W03 verifies it like W02 and SR9 closes; if not, the stated residual stands |
| A-L1 | LLM seed parameter | `temperature=0` only; fixtures unaffected |
| A-E1 | 500 accounts/arm | n set after A/A |
| A-E2 | MDE ≤ 5pp achievable | Reported as not achieved |
| A-D1 | `uuid6` available | Plan deviation requiring approval |

---

# 17. Changes From v3.1 — Reconciliation Log

## 17.0 v3.2.1 → v3.2.2 — idempotency uniqueness clarification only

| # | v3.2.1 | v3.2.2 | Reason |
|---|---|---|---|
| 1 | `recovery_action UNIQUE(idempotency_key)` (§1.6, §8.2, §8.3) while §6.6 W10 also required a `SUPERSEDED_DUPLICATE` row carrying the colliding key — mutually unsatisfiable | **IK1 (§3.4):** uniqueness applies to *live* actions; `uq_action_idempotency` is a partial unique index `WHERE state <> 'SUPERSEDED_DUPLICATE'`; W10 retains the audit row with the same key | Describe the implemented behaviour exactly; preserve "at most one live action per key" |

No other section changed. The implementation was **not** changed to a plain unique constraint.

## 17.0.1 v3.2 → v3.2.1 — GAP-1 addendum only

| # | v3.2 | v3.2.1 | Reason |
|---|---|---|---|
| 1 | `suppress_reason`, `escalation_reason`, `assignee_queue` named; members undefined | **Members defined in §1.5.1** with per-value consumer and kernel derivation rules; CP6 added; W09 asserts the reason→queue mapping (§6.9); call-2 schema note (§7.3) | Phase 1 migration `0001` could not create the enums |
| 2 | Candidate `QUIET_HOURS` (suppress) | **Not adopted** — P10's verdict is `DEFER`; quiet hours never causes a suppression | No consumer |
| 3 | Candidates `PTP_BROKEN`, `INSTALLMENT_REQUEST`, `HOSTILE_CONTACT`, `NO_TARGET_INVOICE` (escalation) | **Not adopted** — `BROKEN` only raises `risk_band` (§2.2); no installment-limit rule exists; `sentiment` has no consuming rule; `no_target_invoice` is a `BLOCK` rule id (§6.8.3) | No consumer / wrong verdict |
| 4 | — | `PAID_CLAIM_UNVERIFIED`, `AMBIGUOUS_INTERPRETATION` adopted | Required by P6's permitted exception and by §4.1/§4.4 "human queue" routing, which has no other action in the catalogue |
| 5 | Candidate `FINANCE_OPS` (queue) | **Not adopted** — no escalation reason routes there; unapplied cash is a suspense/ops-panel item, not an `ESCALATE_TO_HUMAN` action | No consumer; single persona (Appendix A) |

Table count (15), enum count (19), writer count (29 / P1 10), trigger count, role model, phase
boundaries, and security model are unchanged.


| # | v3.1 | v3.2 | Item |
|---|---|---|---|
| 1 | "actor required" as a text parameter | **Trusted actor authority**: new `baaki_ops` role; 7 human-only writers executable by `baaki_ops` only and asserting `session_user` (H17); `*_by_role` (TEC) vs `*_note` (META) columns (H18); I11 | 1, 4 |
| 2 | W19 single writer incl. approvals | **W19a** (automatic, `baaki_app`) / **W19b** (approval, `baaki_ops`); rows 3–4 absent from W19a | 4 |
| 3 | W11/W12, W14, W17, W21 mixed human/automatic | Split by authority class: W11/W12, W14a/b, W17a/b, W21a/b | 1 |
| 4 | `sweep_run` lightly specified | **§6.23 full contract**: W03 computes hash/count, `created_by_role`, `UNIQUE(provider, raw_response_hash)`, containment, P4 `provider_call` host pin, residual SR9 stated | 2 |
| 5 | W02 accepted `signature_ok`; W04 accepted hash and financial fields | **W02 computes HMAC from `provider_secret`** (new C-class table, owner-only, `pgcrypto`); **W04 extracts all financial fields from evidence bytes** — no financial parameters exist | 2 |
| 6 | D1, D2 open | **Locked** (§18.1) | 3 |
| 7 | Red-team ~110 rows | **+ AC1–AC14, SR1–SR9, WS1–WS4, PE10, O4–O5, K4, X11–X12; R13, U17** | 5 |
| 8 | Counts 14/19/1/5/10/5 | **P1 = 15 tables / 19 enums / 1 view / 5 triggers / 10 writers / 6 roles / 1 extension; MVP = 22 / 25 / 1 / 6 / 29 / 7** | 5 |
| 9 | Ten invariants | **Eleven** (I11) | 1 |

## 17.1 Changes From v3 (retained for history)

| # | v3 | v3.1 | Fix |
|---|---|---|---|
| 1 | "authoritative table" undefined; app held `INSERT` on account/contact under an "all authoritative" claim | **Table classes F/D/M/C/R** with a precise privilege invariant | 1 |
| 2 | Partial field→writer list | **Complete matrix** with mutation, writer, role, phase, mode, P1 behaviour | 2 |
| 3 | `baaki_owner LOGIN` | **`NOLOGIN`**; `baaki_migrate` + `SET ROLE`; migrate credential absent from runtime | 3 |
| 4 | W04 accepted caller fields with no provenance | **Evidence FK (verified webhook or sweep run) + payload hash binding**; prohibited creators enumerated | 4 |
| 5 | Composite superkeys + global PKs | **Single global identity**; derived denormalised columns; `trg_decision_linkage` | 5 |
| 6 | Account-level proposal → invoice decision implicit | **SC1–SC6**; `candidate_invoice_ids`; P13 | 6 |
| 7 | 11 states listed | **Full per-state definition** + allowlist with evidence requirements for `CONFIRMED` | 7 |
| 8 | Opt-out writers named only | **Evidence-gated path; kernel bypass rationale; monotonic** | 8 |
| 9 | Kill switch writer named only | **Full contract** incl. deactivation, timing, in-flight | 9 |
| 10 | Aging/dispute writers referenced | **W13–W15 defined with phases**; P1 behaviour stated | 10 |
| 11 | Over-credit described | **Exact algorithm**; never-negative by construction | 11 |
| 12 | "actor required" | **Full reattribution contract** | 12 |
| 13 | TPL1–TPL4 | **TPL5** executor rule; per-layer table | 13 |
| 14 | F1–F7 count | **Each named with reason, absence, test** | 14 |
| 15 | Mixed counts | **Phase-qualified table**; P1 = 14/19/1/5/10/5; MVP = 21/25/1/6/25/6 | 15 |
| 16 | Transaction contract implicit | **Per-writer idempotency + chain boundaries with intentional splits** | 16 |
| 17 | §6.17 two-way | **Three-way**: Postgres / application / red-team | 17 |
| 18 | Red-team ~60 rows | **~110 rows** incl. provenance, search_path, forgery, transaction, opt-out, kill switch | 18 |
| 19 | `webhook_event` P4 | **P1** (structural evidence table), verifier P4 | 4 |
| 20 | — | **`sweep_run`** table added (P1) | 4 |
| 21 | `PARTIALLY_PAID`/`WRITTEN_OFF`/`CANCELLED`/`DRAFT` removed in v3 | unchanged | — |

---

# 18. Decisions

## 18.1 Locked

Table classes and I10 · `baaki_owner NOLOGIN` + `baaki_migrate` · W04 provenance · single-identity
keys · SC1–SC6 · 11 action states · opt-out evidence-gated and **monotonic** · account-level opt-out
human-only · kill switch deactivation human-only via ops · reattribution human-only, once,
cross-account permitted, cross-invoice impossible · over-credit algorithm · all phase counts · **trusted actor model** (§6.22) · **sweep-run provenance and its stated residual** (§6.23) · **HMAC inside W02** ·

**D1 (locked):** an inbound `DISPUTE_AMOUNT`/`DISPUTE_DELIVERY` interpretation that passes validation
**auto-opens** a dispute via W14a, moving the invoice to `DISPUTED` and blocking automated collection
(P5), with `REQUEST_DISPUTE_DETAILS`, `ESCALATE_TO_HUMAN`, `SUPPRESS` still permitted. Dispute rate is
tracked as an exploratory metric; a debtor using disputes to stall is visible there and resolved by a
human via W15. Rationale: a buyer who says the invoice is wrong should not receive a payment link
while we check.

**D2 (locked):** an attributed payment against an invoice that is already `PAID` (`outstanding = 0`)
is posted entirely to `BUYER_CREDIT:<account_id>` by W05's capping step. Rationale: attribution was
explicit; the buyer's money should sit against the buyer, not in general suspense.

## 18.2 Requiring approval

`NONE`. Implementation-plan choices (lock tooling, exact pins) belong to the Phase 1 plan, not to this
document. The MVP limitation that `baaki_ops` is a shared operator credential (no per-person
authentication) is a locked, stated limitation (§6.22), not an open decision.

---

# Appendix A — User Workflow
Single persona: the AR analyst. Worklist by expected incremental recovery → invoice detail (fact
left, inference right) → today's proposal with evidence + verdict badge → decide (tier 2 shows
proposal↔canonical diff) → execute or **`SUPPRESS`** → buyer responds → buyer pays → evidence tab.

# Appendix B — Repository Structure
```
├── bootstrap/roles.sql                 (superuser, once: 6 roles)  ·  bootstrap/secrets.sql (as owner: provider_secret)
├── migrations/  0001_schema · 0002_write_functions · 0003_grants   (baaki_migrate → SET ROLE baaki_owner)
├── src/baaki/  domain/ contracts/ db/{models,writers/{proposal,validation,decision,action,ledger,payment,lifecycle,experiment}}
│              ledger/ policy/{validate,kernel,rules} agent/ rules_agent/ actions/ providers/{llm,razorpay{,fake}}
│              reconcile/ experiment/ sim/ api/ ui/ scripts/ops (connects as baaki_ops; sole importer of db/writers/operator)
└── tests/  arch/ contract/ runtime/ db/ property/ golden/ idempotency/ redteam/ e2e/ integration/(@network)
```

# Appendix C — Security and Secrets
`.env` only; `.gitignore` first; placeholders in `.env.example`, whose **runtime section contains no
owner, migrate, or ops DSN and no webhook secret** — the secret lives only in `provider_secret`, readable
only inside W02; operators hold `BAAKI_OPS_DSN` separately. `rzp_test_` asserted at boot. HMAC constant-time over raw body. PII hashed/
redacted. Prompt receives minimum context. Prompt injection defended by output alphabet + role
model. **No regulatory or compliance claim is made anywhere.** Worst outcome of total
prompt-injection compromise: an unnecessary reminder to a contact we already had.

# Appendix D — Five-Minute Demo
0:00 spine · 0:30 injection defeated (`MARK_PAID` not in the alphabet) · 1:30 link → payment →
verified webhook → replay deduped → ledger → `PAID` → PTP `KEPT` · 2:30 "20% discount" unrepresentable;
`UPDATE invoice SET state='PAID'` as `baaki_app` → `permission denied`; `SELECT approve_recovery_action(…)`
as `baaki_app` → `permission denied`; `pytest tests/redteam/` ·
3:00 three arms, across-seed CI, A/A, SRM, confusion matrix, false-interpretation rate · 4:15
permitted-claims table aloud · 4:45 any rupee → model output + policy hash. *"Every rupee has a
receipt, and the model's name is on it as a witness, never as the signer."*

---

**This document is the source of truth. No implementation exists. No dependencies installed. No git.**
