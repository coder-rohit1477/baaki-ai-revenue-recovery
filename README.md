# Baaki — AI Revenue Recovery

> Baaki proves incremental revenue recovery while keeping financial authority behind
> deterministic controls that the LLM cannot bypass.

**Architecture:** `docs/ARCHITECTURE.md` (v3.2.1, frozen) is the source of truth.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| **P1 Foundation** | 15 tables · 19 enums · 1 view · 5 triggers · 10 `SECURITY DEFINER` writers · 6 roles · `pgcrypto` · frozen contracts · red-team/arch tests | **implemented** |
| P2 Policy | validator ladder, kernel, LLM adapter, RULES_ONLY | not started |
| P3 Promise | PTP, dispute, aging | not started |
| P4 Execution & Reconciliation | executor, Razorpay POS-4, webhook receiver, sweep | not started |
| P5 Evaluation | experiment, simulator, dashboard | not started |

Nothing in P1 calls an LLM, Razorpay, or the network. Writers W02/W03 exist structurally; only
tests call them.

## Local setup

```
uv sync --frozen                       # Python 3.11 env from uv.lock
make db-up                             # PostgreSQL 16 (Docker) — or point DSNs at any PG ≥ 16
BAAKI_SUPERUSER_DSN=... BAAKI_*_PW=... make bootstrap    # roles (once, superuser)
BAAKI_MIGRATE_DSN=... make migrate                        # schema, writers, grants (as baaki_migrate)
BAAKI_MIGRATE_DSN=... BAAKI_WEBHOOK_SECRET=... make secrets
make verify                            # ruff + mypy --strict + pytest + uv lock --check
```

Tests create their own throwaway database from `BAAKI_TEST_SUPERUSER_DSN`
(default `postgresql://postgres@127.0.0.1:5432/postgres`) and never touch a shared one.

## Authority model in one paragraph

Every financial or decision table is written only through `baaki_write.*` functions owned by a
`NOLOGIN` role. The application connects as `baaki_app` (automatic operations), the model
process as `baaki_agent` (proposal insert only), humans as `baaki_ops` (approvals, opt-out,
reattribution — none exist yet in P1). Payment amounts are extracted inside the database from
provider evidence whose HMAC is verified inside the database. `outstanding_paise` is a view over
the ledger. See `docs/ARCHITECTURE.md` §6.
