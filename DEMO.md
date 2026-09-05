# Baaki — judge demo

**One line:** an LLM reads the debtor; deterministic code controls the money.

## Start

```bash
make db-up                       # PostgreSQL 16 (skip if already running)
cp .env.example .env.local       # once — then paste your keys into .env.local
./start-demo.sh                  # every time  →  http://127.0.0.1:8899
```

`.env.local` is gitignored (`.gitignore: .env.*`) and never committed. `./start-demo.sh` loads it **in the
shell** and exports the values into the demo process — no application code reads a file, so `Settings`
keeps `env_file=None` and the runtime still sees only the environment. The launcher validates before it
starts: it masks every secret it prints, refuses a Razorpay key that is not `rzp_test_…`, refuses a
half-configured Razorpay pair, and tells you plainly when AI or Razorpay is unavailable rather than
pretending otherwise.

Missing credentials are a warning, not an error — the demo still runs end to end on the deterministic
path. To demonstrate AI fallback deliberately, start without `OPENAI_API_KEY`.

The keys the launcher understands (all optional except the database):

| Variable | Effect if absent |
|---|---|
| `BAAKI_DEMO_SUPERUSER_DSN` | defaults to the local PostgreSQL 16 container |
| `OPENAI_API_KEY` | AI shows **offline**; recovery uses the deterministic rules path |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Razorpay shows **unavailable**; payment collection uses the simulator |

Pre-flight (optional, one live model call): `make demo-check` — safe to run while the demo is up.
Do **not** run `pytest` while the demo is running; both bootstrap the same cluster roles.

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
