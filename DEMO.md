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
| 2 | Scenario A → **Analyse & decide** | Two panes side by side: **AI understanding** (intent, promised amount, promised date, confidence, evidence quotes) vs **Deterministic decision** (validator outcome, action, authority tier, provenance badge) |
| 3 | **Create payment link** *(Razorpay keys set)* | A real Test Mode Payment Link; pay **₹10,000** on it with a test card |
| 3b | *(no Razorpay keys)* **Simulate ₹10,000** | Same outcome through the same writers |
| 4 | **Check for payment** | ₹25,000 → **₹15,000**, recovered ₹10,000, written by the in-database ledger writers |
| 5 | Pay/simulate the remaining ₹15,000, then **Check for payment** | Invoice → **PAID** |
| 6 | **Analyse & decide** again → prove it stops | **AUTOMATIC STOP** — no eligible candidate, outbound action NOT SENT |
| 7 | Scenario B → **Analyse & decide** | A hostile model reply carrying a forged 40% discount, settlement amount and mark-paid flag. Validator: **REJECT**. Financial state **UNCHANGED**. Action **NOT SENT** |
| 8 | Scenario C → **Analyse & decide** | Opt-out recognised → **SUPPRESS**. No reminder created, no escalation |
| 9 | Scenario D → **Analyse & decide** | Tier 2 → **Human approval required**. Recorded at `PENDING_APPROVAL`, no outbox row |
| 10 | **Approvals** tab → note → **Approve** | Action → **QUEUED**, listed under *Decided* with the deciding role (`baaki_ops`) |
| 11 | **Activity** tab | Causal audit trail: proposal → validation → decision → action → approval → payment → ledger |

## The line that wins it

> A discount is not merely refused. `ActionType` has **no** `DISCOUNT`, `WRITE_OFF` or `MARK_PAID` member —
> tier 3 is unrepresentable. The model cannot express the unsafe action in the production schema at all,
> and when it tries, the deterministic validator rejects the whole proposal.

## Honest limits

- **Razorpay is real, but Test Mode only.** With `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` set, the demo
  creates a real Test Mode Payment Link (`accept_partial`), and *Check for payment* polls `GET /payments`
  and reconciles captured payments through the committed writers. No live keys, no real money; a key id
  that is not `rzp_test_…` is refused by both the launcher and the client.
- **Without Razorpay keys, payment confirmation is SIMULATED.** The simulated confirmation goes through the
  same reconciliation-sweep path (W03 → W04 → W05) with a synthetic payload, so the money arithmetic,
  invoice state transition and ledger entries are produced by the real writers either way.
- **Confirmation is polled, not pushed.** No webhook receiver is wired to a public tunnel in this build.
- **Approved actions are QUEUED, not delivered.** No dispatcher exists — nothing sends an SMS or email.
- **All data is synthetic.** No real customer, invoice or payment exists.
- No revenue has actually been recovered — the amounts are demo figures.
- The model is used for **interpretation only**. Balances, payment confirmation, ledger arithmetic, policy
  authority, recovery metrics, invoice state and stopping rules are all deterministic.

## If the live API fails mid-demo

Nothing breaks. The provider fault maps to `degradation_level = L1`, the deterministic tree produces the
action, and the decision still appears. Say so out loud — it is the point of the architecture.
