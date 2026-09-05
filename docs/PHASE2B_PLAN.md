# Phase 2b Plan — Gated Model-Provider Boundary

**Status:** 2b-1 COMMITTED (`6e0668f`); 2b-2 G1–G4 COMMITTED (`8f57e35`, `e2fcdf7`, `b3df471`, `672718b`); 2b-3 COMMITTED (`2ae6b32`,
live smoke green: 4 passed, `gpt-4o-mini-2024-07-18`); **2b-4 IMPLEMENTED** (composition entrypoint, telemetry emitter, credential
barrier). Corrections 1–11 applied; D-2b-1, D-2b-2, D-2b-3, D-2b-5, D-2b-7, D-2b-9 LOCKED. Held-out live evaluation and G5 not started.
**Architecture basis:** `docs/ARCHITECTURE.md` **v3.3.2** §7 (P2b tag), §1.1, §5.1, §5.3, §12.1–12.2, §13.2–13.3. Where this plan and the
architecture disagree, the plan is wrong.
**Code basis:** commit `48887499b6fd69230ebd8cac2aed0651251314aa` (Phase 2), parent `b10ebe05526264377f35f402c7b32f4f50a1b8e8` (Phase 1).
**Hard boundary of this document:** no OpenAI SDK, no dependency change, no migration, no runtime code. Planning only.

---

## 0. Objective

Let the `TREATMENT` arm obtain a real model-generated proposal while preserving the spine
**LLM proposes → deterministic code validates → deterministic code decides → deterministic executor acts → provider confirms → ledger records.**
Phase 2b adds the model-provider boundary only. The LLM gains no authority: the validator, kernel, W07–W12 and every Phase 1/2 invariant
(I1–I11, `authority_tier(final) <= catalogue_tier(requested_action)`, SC1–SC7, OO1–OO3) are unchanged.

## 1. Current state (from the committed tree)

- `pipeline/run.py` is the only consumer of proposals: `run_decision_pipeline(engine_app, arm, account_id, as_of, ruleset,
  proposals=[(AgentProposal, source_text)], inbound_text, inbound_contact_id)`. For `TREATMENT` it validates (check 00 + 16 checks), records
  W08, applies W11 on a PASS `UNSUBSCRIBE`, selects the target (SC3/SC7), asks `arms/treatment.choose(NormalizedActionProposal)` for an L0
  choice (band D → tree at L1), builds the snapshot, runs the kernel, records W09/W10 — one READ COMMITTED transaction as `baaki_app`.
- A linked decision requires the `agent_proposal` row to exist first (W09 loads it). W07 is executable only by `baaki_agent`; `baaki_agent`
  cannot execute W08–W12. `AgentProposal` enforces A2–A5 at construction; W07 and CHECKs re-enforce A2–A4; `raw_response` is opaque (A6).
- `uq_proposal_daily(invoice_id, business_date, kind, input_hash)` and `PK(proposal_id)` are the only idempotency boundaries for proposals.
- Audit fields already stored per proposal: provider, model_id, prompt_template_id, schema_version, prompt_hash, input_hash, raw_response,
  parsed, parse_status, confidence, evidence, latency_ms.

**Derived insertion point:** upstream of the pipeline, in a different database role. An `agent/` runtime builds a minimal context, calls a
provider-neutral port, maps the response to an `AgentProposal` with the correct `parse_status`, records it via W07 as `baaki_agent`, and
returns `(AgentProposal, source_text)` to the composition point, which invokes the pipeline as `baaki_app`. `policy/`, `pipeline/`, the kernel
and the database are untouched.

## 2. Architecture

```
inbound message ──► agent/context.py        minimal delimited context (no money, no names, no ledger)
                ──► agent/prompts/*.v1.txt  hashed → prompt_hash
                ──► providers/llm/base.py   AiProviderPort.complete_structured(ProviderRequest) → ProviderResponse
                       ├── providers/llm/fixtures.py   CI replay (default everywhere)
                       └── providers/llm/openai.py     live, credential-gated (2b-3)
                ──► agent/mapping.py        ProviderResponse → AgentProposal (parse_status per §3.3)
                ──► db/writers/proposal.py  W07 as baaki_agent
                ──► [composition point, D-2b-3] pipeline.run_decision_pipeline(... proposals=...) as baaki_app
```

### 2.1 Dependency rules (additions to §5.3 — forward AND reverse)

```
agent/          → domain/, contracts/, policy/schemas (types only), providers/llm/, db/writers/proposal      connects as baaki_agent
                ✗ policy/{validate,kernel,arms,snapshot,optout,ruleset}, pipeline/, ledger/, actions/, reconcile/, experiment/,
                  providers/razorpay/, db/writers/{validation,decision,action_auto,ledger,payment,webhook,sweep,operator,optout_evidence}
providers/llm/  → domain/, contracts/ (RawJson only)
                ✗ agent/, policy/, pipeline/, db/, ledger/, actions/, reconcile/, experiment/, providers/razorpay/
providers/llm/openai.py is the ONLY module in src/ permitted to import the vendor SDK.
REVERSE (correction 8): no module under domain/, contracts/, policy/, pipeline/, db/, ledger/, actions/, reconcile/, experiment/, sim/
                imports agent/ or providers/llm/. The only importer of agent/ is the composition entrypoint (D-2b-3).
```
These become `FORBIDDEN_EDGES` entries plus two positive assertions in `tests/arch/test_import_graph.py` (single SDK importer; single
`agent/` importer) — specified here, implemented in 2b-1/2b-3.

## 3. Provider-neutral port (`providers/llm/base.py`)

### 3.1 Contracts
```
class AiProviderPort(Protocol):
    def complete_structured(self, request: ProviderRequest) -> ProviderResponse: ...

ProviderRequest (frozen, strict)
  correlation_id: UUID        # = proposal_id, pre-generated (tracing only — see §3.4)
  trace_id: UUID
  prompt_template_id: str     # "interp.v1" | "propose.v1"
  prompt_hash: str[64]        # sha256(system_text + "\n" + user_text)
  system_text: str
  user_text: str              # delimited untrusted content (§7)
  schema_name: str            # "interpretation.v1" | "action_proposal.v1"
  json_schema: dict           # generated from policy/schemas; additionalProperties=false
  timeout_s: float            # 8.0 (call 1) | 6.0 (call 2) — §7 locked
  max_output_tokens: int
  temperature: Literal[0]
  seed: int | None            # A-L1

ProviderResponse (frozen)
  status: ProviderStatus      # OK | TIMEOUT | RATE_LIMITED | CLIENT_ERROR | SERVER_ERROR | REFUSAL | MALFORMED | UNAVAILABLE |
                              # NO_CREDENTIALS | BUDGET_EXHAUSTED
  raw_json: RawJson | None    # provider output parsed as JSON, untouched (OK only)
  raw_text: str | None        # verbatim body when the output is not JSON (MALFORMED, REFUSAL)
  provider: str; model_id: str
  provider_request_id: str | None
  latency_ms: int; attempts: int (0..2 for this call)
  usage: TokenUsage | None    # input_tokens, output_tokens, cost_estimate_micro_usd | None
  error_class: str | None     # sanitized exception class name only
```
The port never raises for provider faults; it raises only for programming errors (bad request shape, budget misuse).

### 3.2 Global attempt budget — correction 1 (LOCKED)
One `CallBudget(max_attempts=3)` per `(account_id, business_date)` workflow, created by the agent runtime and passed to every port call.
**Every HTTP attempt — initial or retry — consumes one unit; the ceiling of 3 includes retries.** Logical calls remain ≤ 2 (§7).
- Sequence with a message: call 1 attempt (1); optional call 1 retry (2); call 2 attempt (2 or 3); call 2 retry only if a unit remains.
- If call 1 used its retry, call 2 gets exactly one attempt and no retry. If call 1 is absent (§5.3), call 2 may use up to 2 attempts.
- The unit is consumed **before** the request is sent. A call with no unit left is not sent and returns `BUDGET_EXHAUSTED` (attempts = 0).
- A retry is permitted only for `TIMEOUT`, `SERVER_ERROR`, or `RATE_LIMITED` with a `Retry-After` that fits the remaining per-call timeout;
  never for `CLIENT_ERROR`, `REFUSAL`, `MALFORMED`, `NO_CREDENTIALS`, or any schema problem. The per-call timeout bounds the sum of its attempts.
- Exceeding the budget is unreachable by construction; a unit test asserts the counter cannot go negative and that a 4th attempt is never sent.

### 3.3 ProviderStatus → `parse_status` → validator reason — correction 2 (LOCKED; derived from committed check 03)
| ProviderStatus | Output condition | `agent_proposal.parse_status` | `parsed` | Validator check 03 → `rejection_reason` | Pipeline |
|---|---|---|---|---|---|
| OK | JSON object; no A3 money key; no A4 typed-date key | `OK` | stored | passes 03; enum/shape decided by 04–05 (`UNKNOWN_SCHEMA_VERSION`, `ENUM_OUT_OF_RANGE`, `SCHEMA_VIOLATION`) | PASS → L0 (band A/B/C per §4.3); REJECT → L1 |
| OK | JSON object violating A3/A4, or JSON that is not an object (array/scalar) | `SCHEMA_VIOLATION` | NULL | `SCHEMA_VIOLATION` | L1 |
| MALFORMED | body is not valid JSON | `UNPARSEABLE` | NULL | `UNPARSEABLE` | L1 |
| TIMEOUT | no usable response within budget (after ≤1 retry) | `TIMEOUT` | NULL | `PROVIDER_TIMEOUT` | L1 |
| RATE_LIMITED, CLIENT_ERROR, SERVER_ERROR, UNAVAILABLE, NO_CREDENTIALS, REFUSAL, BUDGET_EXHAUSTED | provider fault or no attempt possible | `PROVIDER_ERROR` | NULL | `PROVIDER_TIMEOUT` | L1 |

Notes. (a) The 20-reason enum is locked and has no `PROVIDER_ERROR` label; the committed check 03 maps **both** `TIMEOUT` and
`PROVIDER_ERROR` to `PROVIDER_TIMEOUT` (asserted by `tests/policy/test_validator.py::test_r_parse_failures`). The finer distinction is preserved
in `agent_proposal.parse_status` and in telemetry, not in the rejection reason. (b) A safety **refusal is a provider outcome, not a parse of
model content**: it is `PROVIDER_ERROR`, not `UNPARSEABLE` (this corrects the earlier draft). (c) `confidence` is copied only when `parsed`
is stored and the value is a number in [0, 1]; otherwise NULL (A2). (d) **Row accounting (as implemented):** every call the runtime attempts
produces exactly one `agent_proposal` row whatever its status — call 1 only when an inbound message exists, call 2 in cases A and B — so the
audit trail records every attempt. No row is written when the runtime skips a call (kill switch, empty candidate set, case C, or a provider
disabled by configuration in 2b-3). `BUDGET_EXHAUSTED` is **unreachable through `AgentWorkflow`**: a workflow makes at most one call 1 and one
call 2 (`BudgetMisuse` on a second), so at least one of the three units is always available to call 2; the status exists for the port
contract, is exercised at the port and in the mapping, and maps to `PROVIDER_ERROR` for completeness.

### 3.3.1 ProviderStatus reachability and test accounting (2b-1)
| Status | Produced by | Reachable through `AgentWorkflow` | Row written | Tested at |
|---|---|---|---|---|
| OK | provider returned JSON | yes | yes (`OK` or `SCHEMA_VIOLATION`) | port, fixture, mapping, runtime DB, e2e (L0 flow, band D, money injection) |
| TIMEOUT | attempt(s) exceeded budget | yes | yes (`TIMEOUT`) | port (retry once, two-timeouts terminal), mapping, runtime DB (case C), e2e |
| RATE_LIMITED | 429 | yes | yes (`PROVIDER_ERROR`) | port (retry-after fits / does not fit), mapping, e2e |
| CLIENT_ERROR | other 4xx | yes | yes (`PROVIDER_ERROR`) | port (no retry), mapping, e2e |
| SERVER_ERROR | 5xx | yes | yes (`PROVIDER_ERROR`) | port, fixture (default script), mapping, runtime DB (budget 2+1), e2e |
| REFUSAL | provider refused | yes | yes (`PROVIDER_ERROR`, text envelope) | port (no retry), fixture, mapping (envelope), e2e |
| MALFORMED | non-JSON body | yes | yes (`UNPARSEABLE`, text envelope) | port (no retry), mapping (envelope, 8 KiB cap), e2e (call 1 and call 2) |
| UNAVAILABLE | connect/DNS failure | yes | yes (`PROVIDER_ERROR`) | port (no retry), mapping, e2e |
| NO_CREDENTIALS | 401/403 (live adapter, 2b-3) | yes | yes (`PROVIDER_ERROR`) | port (no retry), mapping, e2e (via fixture) |
| BUDGET_EXHAUSTED | port asked for an attempt with no unit left | **no** (one call of each kind per workflow) | n/a via runtime | port (third call after 3 units; attempt fn may not claim it), mapping (`PROVIDER_ERROR`), e2e asserts unreachability |

### 3.4 Correlation is not idempotency — correction 6 (LOCKED)
`correlation_id` (= `proposal_id`) is sent as client-side request metadata for tracing and support only. **The provider gives no
idempotent-completion guarantee:** a retried request may be executed and billed twice, and two responses may arrive. Idempotency in Baaki is
established exclusively at W07 (`PK(proposal_id)`, `uq_proposal_daily`) and the downstream Phase 1/2 uniques. Therefore: the first response
that arrives is mapped and recorded; any later response for the same `correlation_id` is discarded and logged (`late_duplicate_response`);
cost accounting counts attempts, not proposals; the retry ceiling of §3.2 is the only bound on double execution.

## 4. OpenAI adapter boundary (`providers/llm/openai.py`; 2b-3; not implemented now)

| Aspect | Plan |
|---|---|
| SDK boundary | Official `openai` Python SDK, pinned (D-2b-1, open), one constructor accepting an injected `http_client` for mock-transport tests; sole SDK importer (arch test). |
| API surface | One non-streaming structured-output request per attempt: JSON schema with `strict: true`, `additionalProperties: false`. No tools, no function calling, no assistants/threads, no state, no files. |
| Model — **D-2b-2 LOCKED (correction 3)** | `model_id = "gpt-4o-mini-2024-07-18"` — a **dated snapshot**, not a floating alias: reproducible `model_id` on every proposal row; supports strict structured outputs; the lowest-cost tier at strict-mode launch. The adapter **refuses any model id without a date suffix**. Changing the model is a plan amendment that re-locks this row and bumps `prompt_template_id` (`*.v2`). Snapshot availability is confirmed only by the credential-gated live smoke in 2b-3; if unavailable, **STOP and re-lock** — the adapter never substitutes a different model. |
| Timeout | SDK timeout = `request.timeout_s`; wall-clock guard so initial + retry never exceed the call's timeout. |
| Retry | SDK auto-retries disabled (`max_retries=0`); the single permitted retry is performed by the adapter under `CallBudget` (§3.2) so every attempt is counted and logged. |
| Errors | SDK exceptions → `ProviderStatus`; only the exception class name is retained. 401/403 → `NO_CREDENTIALS`; other 4xx → `CLIENT_ERROR`; 429 → `RATE_LIMITED`; 5xx → `SERVER_ERROR`; connect/read timeout → `TIMEOUT`; content-filter refusal → `REFUSAL`; connection refused/DNS → `UNAVAILABLE`. |
| Auth | `OPENAI_API_KEY` read once into a `SecretStr`, held only by the process running `agent/`; never logged, stored, prompted, or echoed; the pipeline (`baaki_app`) process refuses to start if the key is present in its environment (runtime-leak guard extension). |
| Side effects | None: no DB session, no Razorpay client, no filesystem writes. |
| Provider retention | Request sets the provider's data-retention/training opt-out where offered; asserted at adapter construction (D-2b-10). |
| Transport | HTTPS only; a non-https base URL is a configuration error. |

## 5. Call budget and call semantics

### 5.1 Logical calls (§7, locked) and attempts (§3.2, locked)
≤ 2 logical calls; **≤ 3 HTTP attempts including retries** per `(account_id, business_date)` workflow. Call 3 (copywriter) is cut (§7).

### 5.2 Call 1 — Interpreter (`interpretation.v1`)
Runs only when an inbound message exists, the kill switch is off, and the candidate set is non-empty (both read from facts **before** spending
an attempt). Absent message ⇒ no call 1. Any non-OK outcome ⇒ row per §3.3 ⇒ validator REJECT ⇒ tree at L1, no re-prompt (§4.4).

### 5.3 Call 2 — Proposer (`action_proposal.v1`) — correction 5 (LOCKED)
Three exhaustive cases for a `TREATMENT` decision day with a non-empty candidate set and kill switch off:
| Case | Call 1 state | Call 2 | Context supplied to call 2 | Linkage of the decision |
|---|---|---|---|---|
| A — call 1 **absent** (no inbound message for the day) | not attempted | **runs** (up to 2 attempts within the global 3) | facts only; the context states `inbound_message: none`; the interpretation slot is empty | linked to the ACTION_PROPOSAL's `proposal_id`/`validation_id`; `degradation_level = L0` on PASS, `L1` otherwise |
| B — call 1 attempted and **PASS** | PASS | **runs** | facts + the *normalized* interpretation (intent, resolved invoice ids, promised date/paise as claims) — never the raw response (A6) | as above |
| C — call 1 attempted and **not PASS** (REJECT or any non-OK status) | REJECT | **skipped** | — | decision unlinked (no ACTION_PROPOSAL); INTERPRETATION validation row persists as evidence; `degradation_level = L1`; tree runs on facts (and on the deterministic interpreter of the message text) |
"Absent" and "failed" are therefore distinct: absence permits call 2; failure suppresses it (cost, and no action is proposed on an
interpretation the validator did not accept). In case A the kernel bounds the L0 choice exactly as in Phase 2 (§4.3; P0–P14).

## 6. Structured-output contract
Provider-facing JSON schemas are generated from the **existing** `policy/schemas/interpretation_v1.py` and `action_proposal_v1.py`
(`extra="forbid"` → `additionalProperties: false`), so the model can emit only what the validator already understands. No field can carry
an amount, balance, payment state, settlement, discount, refund, write-off, mark-paid, or ledger fact. A money key in model output ⇒
`SCHEMA_VIOLATION` per §3.3 (A3, W07 CHECK). `promised_amount_raw` becomes `ClaimedPaise` only (compared, never assigned). `invoice_refs`
and `contact_id` are hints pinned to the account by checks 09/10. `rationale` is display-only.

## 7. Prompt-injection boundary
1. Context minimisation (`agent/context.py`): message text (capped, truncation marker), timestamp, invoice numbers with states, enumerated
   `contact_id`/channel pairs, and for call 2 the allowed `template_id` set. Never amounts, ledger, names, other accounts, approval state,
   policy constants, secrets. Untrusted fields sit inside fixed delimiters declared as data; delimiter collisions are escaped.
2. Output alphabet: strict closed schema — instruction overrides, fake system/approval blocks, discount requests are unrepresentable.
3. Contract + W07 CHECKs (A2–A5). 4. Validator (check 00 hash binding; 07/08 literal evidence; 09/10 account pinning; SOFT → tier 0).
5. Kernel + W09 (P2 opt-out, P13 table, CP5, I4). 6. Role: `baaki_agent` executes W07 only.
Trust is established only at a validator PASS, and that yields an `ActionChoice` bounded by 5–6. Worst case of total compromise remains
Appendix C's: one unnecessary templated reminder to an existing contact, within caps and quiet hours. Injection corpus: instruction overrides,
fake system/approval text, embedded JSON, money demands, opt-out negations, foreign invoice/contact ids, unicode and Hinglish variants.

## 8. Failure / fallback matrix
| Provider state | Model output | Validator | Policy | Result |
|---|---|---|---|---|
| OK | valid, conf ≥ 0.85 | PASS | L0; P0–P14 | ALLOW / REQUIRE_APPROVAL per catalogue; one action |
| OK | valid, 0.70–0.85 | PASS | L0; band B | SEND_PAYMENT_LINK → REQUIRE_APPROVAL (PENDING_APPROVAL, no outbox) |
| OK | valid, 0.50–0.70 | PASS | L0; band C | SUPPRESS, tier 0 |
| OK | valid, < 0.50 | PASS | discarded | tree at L1 |
| OK | money / typed-date key | — | — | `SCHEMA_VIOLATION` row, `parsed` NULL; REJECT; L1 |
| OK | unknown enum / extra field | REJECT (`ENUM_OUT_OF_RANGE` / `SCHEMA_VIOLATION`) | — | L1; linked |
| OK | evidence not in source | REJECT `EVIDENCE_NOT_FOUND_IN_SOURCE` | — | L1 |
| OK | foreign invoice / contact | REJECT `INVOICE_REF_UNRESOLVED` / `CONTACT_NOT_IN_ACCOUNT` | — | L1; may set `rejected_ambiguous` |
| OK | UNSUBSCRIBE | PASS | W11 in T2; tree → SUPPRESS | contact opted out; no outbound |
| OK | dispute intent | PASS | REQUEST_DISPUTE_DETAILS / P5 | tier 0 action or BLOCK P5 |
| OK | ALREADY_PAID_CLAIM | PASS | SUPPRESS; later P6 | no pressure ≤ 72 h |
| TIMEOUT (≤1 retry) | none | REJECT `PROVIDER_TIMEOUT` | — | L1; row `TIMEOUT` |
| RATE_LIMITED | none | REJECT `PROVIDER_TIMEOUT` | — | L1; retry only if `Retry-After` fits and a unit remains; row `PROVIDER_ERROR` |
| CLIENT_ERROR / NO_CREDENTIALS | none | REJECT `PROVIDER_TIMEOUT` | — | L1; no retry; row `PROVIDER_ERROR` |
| SERVER_ERROR / UNAVAILABLE | none | REJECT `PROVIDER_TIMEOUT` | — | L1 after ≤1 retry; row `PROVIDER_ERROR` |
| MALFORMED | non-JSON text | REJECT `UNPARSEABLE` | — | L1; verbatim text kept (§11.2) |
| REFUSAL | refusal text | REJECT `PROVIDER_TIMEOUT` | — | L1; row `PROVIDER_ERROR`; text kept (§11.2) |
| BUDGET_EXHAUSTED | none (not sent) | REJECT `PROVIDER_TIMEOUT` | — | port-level only; unreachable through `AgentWorkflow` (§3.3.1); if ever mapped: row `PROVIDER_ERROR`, L1 |
| any | any | any | no candidate (SC7) | `Ineligible`; validations kept; **no attempt spent** |
| any | any | `SYSTEM_HALTED` | P0 | BLOCK; **no attempt spent** (kill switch read first) |
| any | any | PASS | opt-out | P2 BLOCK / SUPPRESS |
| any | any | PASS | quiet hours | DEFER to next window |
Every row ends in an existing Phase 2 state; none creates money, a transition, or a second dispatchable action.

## 9. Testing strategy
- **Unit (default run, no network):** fixture replay; adapter against a mock transport for every `ProviderStatus`; `max_retries=0`;
  budget accounting (a 4th attempt is never sent; units consumed before send; call 2 attempts derived from call 1 usage); header/key
  redaction; JSON-schema generation equals the offline schemas; `ProviderResponse → AgentProposal` for every row of §3.3; late-duplicate
  response discarded (§3.4).
- **Contract:** `AiProviderPort` conformance suite over both providers; fixture PASS reaches `arms/treatment.choose` unchanged; prompt hash
  goldens; `model_id` date-suffix refusal.
- **Security:** injection corpus end-to-end on PostgreSQL 16 asserting §8 terminal states; forged money/typed dates; forged approval text;
  opt-out bypass; foreign ids; authority escalation (tier-2 request at conf 1.0 still REQUIRE_APPROVAL); agent process holds no app DSN.
- **Import graph (correction 8):** forward rules of §2.1; reverse rule (no core module imports `agent/` or `providers/llm/`); exactly one
  SDK importer; exactly one `agent/` importer.
- **Determinism:** same source text + same fixture response ⇒ identical `parsed`, validation outcome, `ActionChoice`, canonical payload and
  decision (ids excluded).
- **Failure:** every non-OK status ⇒ zero actions beyond the L1 tree result; call 2 not attempted after a call-1 failure; call 2 attempted
  when call 1 is absent.
- **Credential gating:** live tests carry `@pytest.mark.network` and `@pytest.mark.live_llm`, run only with `BAAKI_LIVE_LLM=1` and
  `OPENAI_API_KEY`; default `addopts` excludes `network`; socket guard unchanged. CI never consumes credits.

## 10. Evaluation strategy (P2b scope: interpretation quality, offline-first)
Held-out `eval/interpretation_heldout.v1.jsonl`: ≥30% hand-authored (English, Hinglish, code-switched); remainder from a held-out generator
with its own persona file and seed, disjoint from the P5 simulator personas (hashes recorded; disjointness test). Metrics: intent macro-F1
and confusion; **opt-out recall ≥ 0.99** (call 1 alone, restriction detector alone, combined); promise date/amount exact match and
abstention; evidence-span validity; schema compliance; false interpretation rate (harm metric, first); false escalation; latency p50/p95;
cost per attempt. RULES_ONLY/regex baseline on the same set. The report shows **NOT RUN** until a credential-gated live run; no
hand-entered numbers. Recovery outcome and arm comparisons remain P5.

## 11. Observability, retention, cost, latency

### 11.1 Telemetry (structured logs; zero schema change)
Per proposal, keyed by `proposal_id`/`trace_id`: `provider_request_id`, `attempts`, `status`, `input_tokens`, `output_tokens`,
`cost_estimate_micro_usd`, `fallback_reason`, `schema_result`, `validator_outcome`, `degradation_level`, final verdict, `budget_remaining`.
Never logged: API key, Authorization header, prompt text, response text, message text, `raw_response`.

### 11.2 `raw_response` retention and redaction — D-2b-5 LOCKED (correction 4)
| Item | Locked rule |
|---|---|
| What is stored | `raw_response` (`jsonb NOT NULL`) holds the provider's output **verbatim** (A6 audit evidence). Three shapes, exhaustive: (1) `OK` — the JSON output as-is (also when it fails A3/A4 and `parsed` is NULL); (2) a **non-JSON body** (`MALFORMED`, `REFUSAL`) — the envelope `{"non_json_text": "<verbatim, first 8,192 bytes>", "truncated": <bool>, "status": "<ProviderStatus>"}`; (3) a **body-less fault** (`TIMEOUT`, `RATE_LIMITED`, `CLIENT_ERROR`, `SERVER_ERROR`, `UNAVAILABLE`, `NO_CREDENTIALS`, `BUDGET_EXHAUSTED`) — the status-only object `{"status": "<ProviderStatus>"}`. `raw_response` is never NULL. Implemented in `agent/mapping.py::_envelope`. |
| Redaction at write | **None.** Redaction would falsify audit evidence, and `agent_proposal` is immutable (A1; no role holds UPDATE/DELETE), so redaction-in-place is unrepresentable. |
| Prompts | Not stored. Reconstructible from `prompt_template_id` (hashed template file) and the `input_hash` inputs. |
| Access | Existing grants only: `SELECT` for `baaki_app`, `baaki_ops`, `baaki_agent`; `INSERT` via W07 by `baaki_agent`; no UPDATE/DELETE for any project role. No new grant. |
| Egress | `raw_response` never enters logs, telemetry, eval reports, or fixtures recorded from live runs (fixtures store the response but are committed only after manual review and contain synthetic/hand-authored inputs only). |
| Retention | Rows live for the lifetime of the experiment database (P5). Purge, if ever, is an operator-run migration-style script on a retired database — out of Phase 2b scope and never a runtime path. |
| Data class | MVP debtor messages are synthetic or hand-authored. **Real inbound data (P4 ingestion) must re-open this decision** with a redaction-at-source policy before any real message reaches a prompt or a row; this is a P4 gate item, recorded here so it cannot be forgotten. |

### 11.3 Controls
Locked in 2b-1 (D-2b-9): per-call timeout 8 s / 6 s; ≤1 retry per call; **≤3 attempts per workflow including retries**;
`max_output_tokens` 400 (call 1) / 300 (call 2); 2,000-byte input cap with truncation marker; 8,192-byte non-JSON envelope cap.
Deferred to the live adapter, 2b-3 (D-2b-11): per-process failure budget (N consecutive provider faults ⇒ circuit open for the run,
L1 with `fallback_reason=circuit_open`) and daily cost ceiling ⇒ provider disabled for the run. No recursion, no tool calls, no agent loops.

## 12. Threat model
| Threat | Boundary | Mitigation | Test |
|---|---|---|---|
| API key exposure | env / logs / DB | `SecretStr`; never in prompt, `raw_response`, logs; `.env` ignored; pipeline process refuses the key | log-capture assertion; settings tests |
| Provider compromise / tampered response | port | untrusted output; closed schema; validator; kernel; CP5 | corpus; forged-money tests |
| Malicious model output | contract/W07/validator | A2–A5, CHECKs, 16 checks | unit + PG16 e2e |
| Prompt injection | context builder | minimisation, delimiting, no names/amounts, output alphabet | corpus |
| Replay / duplicate provider call / double execution on retry | W07 + budget | `uq_proposal_daily`; first response wins, late duplicate discarded (§3.4); ≤3 attempts | duplicate-run and late-response tests |
| Timeout / rate limit | adapter | single retry within budget; L1 | mock-transport tests |
| Cost abuse | runtime | attempt budget, token caps, failure budget, daily ceiling, SC7/kill-switch pre-checks | budget tests |
| Data leakage to provider | context | no amounts/names/ledger; retention opt-out | context golden test |
| Model overreach / authority escalation | kernel | I4 property; P13; tier-2 approval | property tests with L0 fixtures |
| Unauthorized financial action | roles | agent = W07 only; no money field; W09/W10 app-only | grant matrix; e2e |
| Response tampering in transit | TLS | https-only config | config test |

## 13. Database impact
**Zero** tables, enums, writers, grants, migrations (§13.3 P2b column). Telemetry is logged, not stored (D-2b-4 open for later).

## 14. Implementation phases (each separately gated)
| Phase | Objective | Files | Tests / security | DoD | Rollback |
|---|---|---|---|---|---|
| **2b-1** Port + fixtures + agent runtime (offline) | `AiProviderPort`, `FixtureProvider`, context builder, mapping (§3.3), W07 recording, `CallBudget` (§3.2), forward+reverse import rules | `providers/llm/{__init__,base,fixtures}.py`, `agent/{__init__,context,mapping,runtime,budget}.py`, `agent/prompts/{interp,propose}.v1.txt`, `tests/{agent,providers}/*`, `tests/fixtures/llm/*.json`, `tests/arch/test_import_graph.py` | port conformance; §3.3 rows; §3.2 budget; determinism; import graph; no socket | fixture-driven TREATMENT L0 decision end-to-end on PG16 with zero network | delete packages |
| **2b-2** Injection corpus + offline eval harness | corpus + expected terminal states; held-out set skeleton; metrics; report | `tests/fixtures/injection/*`, `tests/security/test_injection_corpus.py`, `eval/{heldout.v1.jsonl,generator.py,metrics.py,report.py}`, `tests/eval/*` | every corpus row per §8; leakage test | report renders NOT RUN; corpus green | delete files |
| **2b-3** OpenAI adapter (credential-gated) | SDK adapter; settings; markers; dependency | `providers/llm/openai.py`, `config.py`, `.env.example`, `pyproject.toml`, `uv.lock`, `tests/providers/test_openai_adapter.py`, `tests/live/test_openai_live.py` | status mapping; retry once under budget; redaction; `max_retries=0`; https-only; date-suffix model refusal; live smoke with credentials | default suite green without credits; one manual live smoke recorded (or STOP per §4) | remove adapter and dependency |
| **2b-4** Composition entrypoint + telemetry + docs | agent (`baaki_agent`) then pipeline (`baaki_app`) entrypoint per D-2b-3; JSON logging; §5.3/§7/§12.2/App. B–C → v3.4 | per D-2b-3, `agent/telemetry.py`, `docs/ARCHITECTURE.md`, this plan | redaction test; two-role connection test | PG16/18 green; arch tests; docs consistent | revert docs; remove script |

## 15. File-by-file plan
| Path | New/Mod | Purpose | Depends on |
|---|---|---|---|
| `src/baaki/providers/llm/__init__.py`, `base.py` | new | port; request/response/status; `CallBudget` type | domain, contracts (RawJson) |
| `src/baaki/providers/llm/fixtures.py` | new | deterministic replay keyed by `prompt_hash`; scripted statuses | base |
| `src/baaki/providers/llm/openai.py` | new (2b-3) | sole SDK importer | base, `openai` |
| `src/baaki/agent/context.py` | new | minimal delimited context | domain, contracts |
| `src/baaki/agent/prompts/*.v1.txt` | new | prompt templates (hashed) | — |
| `src/baaki/agent/mapping.py` | new | `ProviderResponse → AgentProposal` per §3.3; envelope per §11.2 | contracts, policy.schemas (types) |
| `src/baaki/agent/budget.py` | new | ≤3 attempts incl. retries; failure budget | — |
| `src/baaki/agent/runtime.py` | new | `propose(facts, message) -> list[(AgentProposal, source_text)]`; call 1/2 gating per §5; W07 | providers.llm, db.writers.proposal |
| `src/baaki/agent/telemetry.py` | new (2b-4) | redacted structured logs | — |
| `src/baaki/config.py` | mod (2b-3) | `openai_api_key: SecretStr | None`, `llm_provider`, `llm_model` (must equal the locked id), ceilings; pipeline process refuses the key | — |
| `.env.example`, `pyproject.toml`, `uv.lock` | mod (2b-3) | key placeholder; pinned dependency; `live_llm` marker; `network` marker text | — |
| `tests/arch/test_import_graph.py` | mod (2b-1) | forward + reverse rules; single SDK importer; single `agent/` importer | — |
| `tests/{agent,providers,security,eval,live}/…`, `tests/fixtures/{llm,injection}/…` | new | §9 | — |
| `eval/…` | new (2b-2) | held-out set, generator, metrics, report | — |
| `docs/ARCHITECTURE.md` | mod (2b-4) | §5.3 addenda (agent allowlist incl. `contracts/`, `policy/schemas` types; `providers/llm/` rules; reverse rule), §7 (port, attempt budget, §3.3 table, §5.3 cases, D-2b-2/D-2b-5), §12.2 rows, App. B/C, changelog v3.4 | — |

## 16. Definition of Done
1. `AiProviderPort` exists; no core module imports a vendor type; exactly one module imports the SDK; no core module imports `agent/` or
   `providers/llm/` (forward and reverse arch tests).
2. Fixture provider drives a full `TREATMENT` L0 decision on PostgreSQL 16 with the socket guard active.
3. **(corrected)** For every **non-OK** `ProviderStatus` returned to the runtime for an attempted call, exactly one `agent_proposal` row is
   written with `parse_status ∈ {TIMEOUT, PROVIDER_ERROR, UNPARSEABLE}` per §3.3, the validator rejects with the mapped reason,
   `degradation_level = L1` is recorded, and the only action is the deterministic tree's own result. For `OK`, the row carries `parse_status
   OK` (clean object) or `SCHEMA_VIOLATION` (A3/A4 or non-object), and the validator alone determines PASS or REJECT. Skipped calls (kill
   switch, empty candidate set, case C, provider disabled in 2b-3) send nothing and write nothing. `BUDGET_EXHAUSTED` is unreachable through
   the runtime (§3.3.1) and is proven at the port.
4. ≤2 logical calls and ≤3 HTTP attempts **including retries** per workflow, enforced and tested; no attempt is spent on SC7 or
   kill-switch days; call 2 runs when call 1 is absent and is skipped when call 1 failed (§5.3 cases A/B/C tested).
5. Injection corpus passes end-to-end; forged money, approval, opt-out bypass, foreign ids all terminate safely.
6. Determinism test passes; I4 property re-run with L0 fixtures.
7. Default suite consumes no credits; live tests gated by marker and env; the key never appears in captured logs.
8. `model_id` on every live proposal equals the locked `gpt-4o-mini-2024-07-18`; adapter refuses undated ids.
9. `raw_response` handling matches §11.2 (verbatim; envelope for non-JSON; never in logs/reports/fixtures from live runs).
10. Zero migrations; `uv lock --check` clean; ruff and mypy --strict clean; PG16 and PG18 green; Phase 1/2 suites unchanged in count and outcome.
11. Architecture v3.4 describes the implemented graph; no stale `network`-marker wording; eval report shows NOT RUN until a live run.

## 17. Non-goals
No Razorpay API or webhook receiver; no email/SMS/WhatsApp delivery; no scheduler or daily loop; no production communications; no
settlement, discount, refund, write-off, mark-paid; no Phase 3/4 functionality (PTP, disputes, execution, transitions, W11b, ingestion);
no UI; no autonomous financial action; no tool calling; no multi-agent loops; no copywriting call.

## 18. Decisions
| # | Decision | Status | Value / recommendation |
|---|---|---|---|
| **D-2b-1** | SDK vs stdlib HTTPS | **LOCKED (2b-3)** | **stdlib** — `providers/llm/transport.py` over `urllib`, no vendor SDK, no new dependency. `uv lock` unchanged; the single-socket-module guard replaces `max_retries=0`. |
| **D-2b-2** | Model id | **LOCKED** | `gpt-4o-mini-2024-07-18` (dated snapshot; adapter refuses undated ids; change = plan amendment + `*.v2` prompt id; availability confirmed by the 2b-3 live smoke, else STOP) |
| **D-2b-3** | Composition entrypoint | **LOCKED (2b-4)** | `src/baaki/scripts/run_treatment_day.py` — dev entrypoint; the only importer of `agent/` in `src/`. Production runner stays P4. |
| D-2b-4 | Telemetry store | OPEN | structured logs now; consider generalising P4 `provider_call` later |
| **D-2b-5** | `raw_response` retention/redaction | **LOCKED** | §11.2: verbatim; non-JSON envelope (8 KiB cap); no redaction at write (immutable row); existing grants; never in logs/reports/live fixtures; lifetime of the experiment DB; real-data ingestion re-opens it as a P4 gate |
| D-2b-6 | Fixture recording mode | OPEN | yes, gated like live tests; committed only after manual review |
| D-2b-7 | Call 2 gating | LOCKED by §5.3 | absent call 1 ⇒ run; failed call 1 ⇒ skip; PASS ⇒ run with normalized interpretation |
| D-2b-8 | Seed (A-L1) | OPEN | send when supported; fixtures unaffected |
| **D-2b-9** | Ceilings | **LOCKED (as implemented in 2b-1)** | `CALL1_TIMEOUT_S = 8.0`, `CALL2_TIMEOUT_S = 6.0` (§7); `CALL1_MAX_OUTPUT_TOKENS = 400`, `CALL2_MAX_OUTPUT_TOKENS = 300`; `MESSAGE_CAP_BYTES = 2000` UTF-8 **bytes**, cut on a character boundary, then `TRUNCATION_MARKER = " [TRUNCATED BY BAAKI]"` appended and `message_truncated: true` recorded in the context; `NON_JSON_TEXT_CAP_BYTES = 8192`; `MAX_ATTEMPTS_PER_CALL = 2`; `GLOBAL_MAX_ATTEMPTS = 3`. Source of truth: `agent/context.py`, `agent/mapping.py`, `providers/llm/base.py`. Changing any value is a plan amendment. |
| D-2b-11 | Live-provider controls not in 2b-1: consecutive-failure budget (circuit open) and daily cost ceiling | **DEFERRED past 2b-4** (minimal provider scope, D-2b4-4) | recommended N = 5 consecutive provider faults ⇒ circuit open for the run, `fallback_reason = circuit_open`; daily cost ceiling per run from configuration; both apply only to the live adapter |
| D-2b-10 | Provider retention opt-out mandatory | **DEFERRED past 2b-4** (D-2b4-4) | would change `_payload()` and require a fresh live smoke; not adopted |

## 19. Contradiction scan (against v3.3.2 and the committed tree)
No blocking contradiction. Documentation addenda required in 2b-4: (1) §5.3 must list `agent/ → contracts/` and `agent/ → policy/schemas`
(types only) plus the `providers/llm/` forward rules and the reverse rule of §2.1; (2) `pyproject.toml` `network` marker text ("Phase 4
provider integration only") must be updated in 2b-3; (3) §13.2's "0 tables, 0 enums, 0 writers" stays true because telemetry is logged.
Verified unchanged: money safety (no money field; A3; CP5), authority monotonicity, opt-out safety, idempotency (§3.4 makes explicit that it
is Baaki-side only), deterministic policy authority, provider isolation (single SDK importer; reverse rule).

Housekeeping for 2b-4 (documentation/comment only, no behaviour): the three `# D-2b-9 recommended value, pending lock` comments in
`agent/context.py` should read `# D-2b-9 locked` now that this plan locks the implemented values; runtime code was deliberately not
touched in the correction pass that locked them.

## 20. Recommendation
Proceed with 2b-1 first (port, fixtures, runtime, budget, import rules; no dependency, no network). Approve D-2b-1 and D-2b-3 before 2b-3.


---

## 21. Phase 2b-4 as built (2026-09-05)

| Decision | Value |
|---|---|
| D-2b4-1 | **C** — D-G3-1 amended to `G3 → G4 → 2b-3 → 2b-4 → held-out → G5`; recorded in `PHASE2B2_PLAN.md` |
| D-2b4-2 | **A** — the emitter extends `agent/observability.py`; no `telemetry.py` module was created |
| D-2b4-3 | security-critical only — credential barrier, `.env.example` agent-leg section, stale marker text. **No** `llm_provider`, `llm_model` or ceiling config fields: `LOCKED_MODEL_ID` and the D-2b-9 constants are already the single source of truth, and a config field could only create drift |
| D-2b4-4 | minimal provider scope — the live adapter, its payload, prompts, schema and model id are **byte-identical to 2b-3**, so 2b-4 required no live call |
| Credential mechanism | **(ii)** single-process scoped credential |

### 21.1 Credential separation as built
`take_model_credential()` reads `OPENAI_API_KEY` into a `SecretStr` and **removes it from the environment**;
`assert_no_model_credential()` refuses to run the pipeline leg while it is still reachable. The key is taken before any
engine exists, which is stronger than the plan's ordering: it is absent for the whole process except inside the provider
object. This was required because `assemble_account_facts` reads `organization`, `policy_decision` and `recovery_action`,
on which `baaki_agent` holds no SELECT (§6.3) — facts assembly must therefore run as `baaki_app`, before the agent leg.
No grant was changed.

### 21.2 Idempotency — corrected claim
The plan's gate "re-run converges to the same decision" is **not met, and was not implementable without changing the
pipeline** (out of scope). What holds and is tested:
- the invoice-scoped `ACTION_PROPOSAL` is written **exactly once** per invoice-day (`uq_proposal_daily`); the entrypoint
  absorbs the unique violation instead of failing;
- a re-run **completes safely** and never raises.
A re-run does open a **new decision cycle**: the absorbed proposal leaves the second run with no proposals, so the
pipeline takes its unlinked path, which does not match the first run's linked decision (§5.8 keys linked uniqueness on
`validation_id`). This is committed pipeline behaviour, recorded in
`tests/scripts/test_run_treatment_day.py::test_a_re_run_opens_a_new_decision_cycle_by_design` so it cannot drift.
