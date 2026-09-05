# Baaki — judge demo

**One line:** an LLM reads the debtor; deterministic code controls the money.

## Start

```bash
make db-up                                  # PostgreSQL 16 (skip if already running)
export OPENAI_API_KEY=sk-...                # shell only — never a file, never committed
make demo-check                             # optional: one live call, proves credentials work
make demo                                   # → http://127.0.0.1:8899
```

The server takes `OPENAI_API_KEY` **out of the environment** at startup (`take_model_credential`) and
asserts it is unreachable (`assert_no_model_credential`) before the `baaki_app` leg is created. Without a
key the demo still runs: every scenario degrades to the deterministic rules path instead of crashing.

`make demo` recreates the `baaki_demo` database and reseeds it, so the demo is repeatable. "Reset demo"
in the header reseeds without restarting.

## The 60-second sequence

| # | Action | What the judge should see |
|---|---|---|
| 1 | Open the page | **₹396,950 revenue at risk**, 9 overdue accounts, all labelled DEMO · SYNTHETIC DATA |
| 2 | Scenario A → **Run AI analysis** | Two panes side by side: 🤖 **AI INTERPRETATION** (intent, promised amount, promised date, confidence) vs 🔒 **DETERMINISTIC POLICY DECISION** (validator outcome, verdict, action, authority tier) |
| 3 | **Simulate ₹10,000 payment** | ₹25,000 → **₹15,000**, recovered ₹10,000, written by the in-database ledger writers |
| 4 | **Simulate remaining ₹15,000** | Invoice → **PAID** |
| 5 | **Run recovery again → prove it stops** | **AUTOMATIC STOP** — no eligible candidate, outbound action NOT SENT |
| 6 | Scenario B → **Run AI analysis** | A hostile model reply carrying a forged 40% discount, settlement amount and mark-paid flag. Validator: **REJECT**. Financial state **UNCHANGED**. Action **NOT SENT** |
| 7 | Scenario C → **Run AI analysis** | Opt-out recognised → **SUPPRESS**. No reminder created, no escalation |
| 8 | Click any account row | Full audit trail: proposal → validation → decision → action → payment → ledger |

## The line that wins it

> A discount is not merely refused. `ActionType` has **no** `DISCOUNT`, `WRITE_OFF` or `MARK_PAID` member —
> tier 3 is unrepresentable. The model cannot express the unsafe action in the production schema at all,
> and when it tries, the deterministic validator rejects the whole proposal.

## Honest limits

- **Payment confirmation is SIMULATED.** There is no live Razorpay integration in this build. The simulated
  confirmation goes through the real reconciliation-sweep path (W03 → W04 → W05) with a synthetic payload,
  so the money arithmetic, invoice state transition and ledger entries are produced by the real writers.
- **All data is synthetic.** No real customer, invoice or payment exists.
- No revenue has actually been recovered — the amounts are demo figures.
- The model is used for **interpretation only**. Balances, payment confirmation, ledger arithmetic, policy
  authority, recovery metrics, invoice state and stopping rules are all deterministic.

## If the live API fails mid-demo

Nothing breaks. The provider fault maps to `degradation_level = L1`, the deterministic tree produces the
action, and the decision still appears. Say so out loud — it is the point of the architecture.
