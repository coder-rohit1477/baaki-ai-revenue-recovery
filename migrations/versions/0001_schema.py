"""Phase 1 schema: 2 schemas, pgcrypto, 19 enums, 15 tables, 1 view, 5 triggers.

ARCHITECTURE.md v3.2.1 §1, §6.1, §6.7, §6.11–6.14, §8.2, §13.3.
Runs as baaki_owner (SET ROLE from baaki_migrate — see env.py).

Revision ID: 0001
Revises: None
"""

from alembic import op


def _sql(statement: str) -> None:
    """Execute raw SQL without SQLAlchemy bind-parameter parsing (PL/pgSQL bodies contain ':=' and JSON paths)."""
    # Raw DBAPI cursor, no parameters: neither SQLAlchemy (":name") nor psycopg ("%") token parsing applies.
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ENUMS: dict[str, list[str]] = {
    "proposal_kind": ["INTERPRETATION", "ACTION_PROPOSAL"],
    "parse_status": ["OK", "SCHEMA_VIOLATION", "UNPARSEABLE", "TIMEOUT", "PROVIDER_ERROR"],
    "arm": ["CONTROL", "RULES_ONLY", "TREATMENT"],
    "validation_outcome": ["PASS", "REJECT"],
    "rejection_reason": [
        "SYSTEM_HALTED", "LEDGER_INVARIANT_BREACH", "SCHEMA_VIOLATION", "UNPARSEABLE",
        "PROVIDER_TIMEOUT", "UNKNOWN_SCHEMA_VERSION", "ENUM_OUT_OF_RANGE", "FORBIDDEN_MONEY_FIELD",
        "EVIDENCE_NOT_FOUND_IN_SOURCE", "EVIDENCE_MISSING_FOR_FIELD", "CONTACT_NOT_IN_ACCOUNT",
        "INVOICE_REF_UNRESOLVED", "DATE_UNPARSEABLE", "DATE_AMBIGUOUS", "AMOUNT_UNPARSEABLE",
        "AMOUNT_AMBIGUOUS", "DATE_IN_PAST", "DATE_BEYOND_HORIZON", "AMOUNT_EXCEEDS_OUTSTANDING",
        "CONFIDENCE_BELOW_THRESHOLD",
    ],
    "verdict": ["ALLOW", "REQUIRE_APPROVAL", "BLOCK", "DEFER"],
    "action_type": [
        "SUPPRESS", "SCHEDULE_FOLLOWUP", "REQUEST_DISPUTE_DETAILS", "SEND_REMINDER",
        "SEND_PAYMENT_LINK", "PROPOSE_INSTALLMENT_PLAN", "ESCALATE_TO_HUMAN",
    ],
    "action_state": [
        "PENDING_APPROVAL", "APPROVAL_REJECTED", "QUEUED", "EXECUTING", "EXECUTED", "CONFIRMED",
        "FAILED_RETRYABLE", "FAILED_TERMINAL", "EXPIRED", "SUPERSEDED_DUPLICATE", "COMPENSATED",
    ],
    "invoice_state": ["OPEN", "DUE", "OVERDUE", "DISPUTED", "PAID"],
    "dr_cr": ["DEBIT", "CREDIT"],
    "ledger_source": ["ISSUANCE", "PAYMENT", "REATTRIBUTION"],
    "channel": ["EMAIL", "SMS", "WHATSAPP"],
    "degradation_level": ["L0", "L1", "L2", "L3", "L4"],
    "template_purpose": [
        "REMINDER", "COURTESY_NUDGE", "PAYMENT_LINK", "DISPUTE_DETAILS_REQUEST", "INSTALLMENT_PROPOSAL",
    ],
    "suppress_reason": [
        "DISPUTE_OPEN", "PAID_CLAIM_PENDING", "PTP_ACTIVE", "FREQUENCY_CAP", "NO_ELIGIBLE_ACTION",
    ],
    "escalation_reason": [
        "DISPUTE_UNRESOLVED", "PAID_CLAIM_UNVERIFIED", "AMBIGUOUS_INTERPRETATION", "MANUAL_REVIEW",
    ],
    "assignee_queue": ["DISPUTES", "COLLECTIONS"],
    "payment_source": ["WEBHOOK", "SWEEP"],
    "attribution_method": [
        "NOTES_INVOICE_ID", "REFERENCE_ACTION_ID", "UNATTRIBUTED", "HUMAN_REATTRIBUTION",
    ],
}

TABLES_IN_DROP_ORDER = [
    "outbox", "recovery_action", "policy_decision", "validation_result", "agent_proposal",
    "ledger_entry", "payment_event", "sweep_run", "webhook_event", "invoice", "provider_secret",
    "template_registry", "contact", "account", "organization",
]


def upgrade() -> None:
    _sql("CREATE SCHEMA baaki")
    _sql("CREATE SCHEMA baaki_write")
    _sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    _sql("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA baaki")

    for name, values in ENUMS.items():
        labels = ", ".join(f"'{v}'" for v in values)
        _sql(f"CREATE TYPE baaki.{name} AS ENUM ({labels})")

    # ── CHECK helper functions (IMMUTABLE, SQL). Not writers; they live in schema baaki. ──
    _sql("""
    CREATE FUNCTION baaki.jsonb_has_key_like(j jsonb, pat text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT SET search_path = baaki, pg_catalog AS $$
      SELECT CASE WHEN pg_catalog.jsonb_typeof(j) = 'object'
                  THEN EXISTS (SELECT 1 FROM pg_catalog.jsonb_object_keys(j) k WHERE k LIKE pat)
                  ELSE false END
    $$""")
    _sql("""
    CREATE FUNCTION baaki.account_code_class(code text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT SET search_path = baaki, pg_catalog AS $$
      SELECT CASE WHEN code LIKE 'AR:%' THEN 'AR'
                  WHEN code LIKE 'BUYER_CREDIT:%' THEN 'BUYER_CREDIT'
                  ELSE code END
    $$""")

    # ── C-class ─────────────────────────────────────────────────────────────────────
    _sql("""
    CREATE TABLE baaki.organization (
      org_id       uuid PRIMARY KEY,
      name         text NOT NULL,
      timezone     text NOT NULL,
      kill_switch  boolean NOT NULL DEFAULT false,
      created_at   timestamptz NOT NULL DEFAULT now()
    )""")
    _sql("""
    CREATE TABLE baaki.template_registry (
      template_id  text PRIMARY KEY,
      channel      baaki.channel NOT NULL,
      action_type  baaki.action_type NOT NULL,
      purpose      baaki.template_purpose NOT NULL,
      active       boolean NOT NULL DEFAULT true,
      version      integer NOT NULL,
      body_hash    char(64) NOT NULL,
      created_at   timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_template_pair CHECK (
        (action_type, purpose) IN (
          ('SEND_REMINDER'::baaki.action_type,           'REMINDER'::baaki.template_purpose),
          ('SEND_REMINDER'::baaki.action_type,           'COURTESY_NUDGE'::baaki.template_purpose),
          ('SEND_PAYMENT_LINK'::baaki.action_type,       'PAYMENT_LINK'::baaki.template_purpose),
          ('REQUEST_DISPUTE_DETAILS'::baaki.action_type, 'DISPUTE_DETAILS_REQUEST'::baaki.template_purpose),
          ('PROPOSE_INSTALLMENT_PLAN'::baaki.action_type,'INSTALLMENT_PROPOSAL'::baaki.template_purpose)))
    )""")
    _sql("""
    CREATE TABLE baaki.provider_secret (
      provider        text PRIMARY KEY,
      webhook_secret  text NOT NULL,
      rotated_at      timestamptz
    )""")

    # ── M-class ─────────────────────────────────────────────────────────────────────
    _sql("""
    CREATE TABLE baaki.account (
      account_id    uuid PRIMARY KEY,
      org_id        uuid NOT NULL REFERENCES baaki.organization(org_id),
      external_ref  text NOT NULL,
      name          text NOT NULL,
      opt_out       boolean NOT NULL DEFAULT false,
      risk_band     smallint NOT NULL DEFAULT 0,
      created_at    timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_account_org_ref UNIQUE (org_id, external_ref)
    )""")
    _sql("CREATE INDEX ix_account_org_optout ON baaki.account (org_id, opt_out)")
    _sql("""
    CREATE TABLE baaki.contact (
      contact_id        uuid PRIMARY KEY,
      account_id        uuid NOT NULL REFERENCES baaki.account(account_id),
      channel           baaki.channel NOT NULL,
      address_hash      char(64) NOT NULL,
      address_redacted  text NOT NULL,
      active            boolean NOT NULL DEFAULT true,
      opted_out         boolean NOT NULL DEFAULT false,
      created_at        timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_contact_address UNIQUE (account_id, channel, address_hash)
    )""")

    # ── F-class ─────────────────────────────────────────────────────────────────────
    _sql("""
    CREATE TABLE baaki.invoice (
      invoice_id      uuid PRIMARY KEY,
      org_id          uuid NOT NULL REFERENCES baaki.organization(org_id),
      account_id      uuid NOT NULL REFERENCES baaki.account(account_id),
      invoice_number  text NOT NULL,
      issued_paise    bigint NOT NULL,
      issued_date     date NOT NULL,
      due_date        date NOT NULL,
      state           baaki.invoice_state NOT NULL,
      created_at      timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_invoice_number UNIQUE (org_id, invoice_number),
      CONSTRAINT ck_invoice_issued_positive CHECK (issued_paise > 0),
      CONSTRAINT ck_invoice_dates CHECK (due_date >= issued_date)
    )""")
    _sql("CREATE INDEX ix_invoice_state_due ON baaki.invoice (state, due_date)")
    _sql("CREATE INDEX ix_invoice_account_state ON baaki.invoice (account_id, state)")

    # ── D-class evidence ────────────────────────────────────────────────────────────
    _sql("""
    CREATE TABLE baaki.webhook_event (
      event_id          uuid PRIMARY KEY,
      provider          text NOT NULL,
      dedupe_key        text NOT NULL,
      raw_body          text NOT NULL,
      signature_header  text,
      signature_ok      boolean NOT NULL,
      received_at       timestamptz NOT NULL,
      processed_at      timestamptz,
      created_at        timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_webhook_dedupe UNIQUE (provider, dedupe_key)
    )""")
    _sql("""
    CREATE TABLE baaki.sweep_run (
      sweep_run_id        uuid PRIMARY KEY,
      provider            text NOT NULL,
      window_from         timestamptz NOT NULL,
      window_to           timestamptz NOT NULL,
      requested_at        timestamptz NOT NULL,
      raw_response        text NOT NULL,
      raw_response_hash   char(64) NOT NULL,
      item_count          integer NOT NULL,
      created_by_role     text NOT NULL,
      provider_call_id    uuid,
      created_at          timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_sweep_response UNIQUE (provider, raw_response_hash),
      CONSTRAINT ck_sweep_window CHECK (window_to >= window_from)
    )""")

    _sql("""
    CREATE TABLE baaki.payment_event (
      payment_event_id       uuid PRIMARY KEY,
      provider               text NOT NULL,
      provider_payment_id    text NOT NULL,
      amount_paise           bigint NOT NULL,
      currency               char(3) NOT NULL,
      provider_status        text NOT NULL,
      paid_at                timestamptz NOT NULL,
      source                 baaki.payment_source NOT NULL,
      webhook_event_id       uuid REFERENCES baaki.webhook_event(event_id),
      sweep_run_id           uuid REFERENCES baaki.sweep_run(sweep_run_id),
      provider_payload_raw   text NOT NULL,
      provider_payload_hash  char(64) NOT NULL,
      attributed_invoice_id  uuid REFERENCES baaki.invoice(invoice_id),
      attribution_method     baaki.attribution_method NOT NULL,
      applied_at             timestamptz,
      reattributed_at        timestamptz,
      reattributed_by_role   text,
      reattributed_by_note   text,
      created_at             timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_payment_provider_id UNIQUE (provider_payment_id),
      CONSTRAINT ck_payment_amount_positive CHECK (amount_paise > 0),
      CONSTRAINT ck_payment_currency CHECK (currency = 'INR'),
      CONSTRAINT ck_payment_evidence_xor CHECK ((webhook_event_id IS NULL) <> (sweep_run_id IS NULL)),
      CONSTRAINT ck_payment_source_matches CHECK ((source = 'WEBHOOK') = (webhook_event_id IS NOT NULL)),
      CONSTRAINT ck_payment_attribution CHECK ((attribution_method = 'UNATTRIBUTED') = (attributed_invoice_id IS NULL))
    )""")
    _sql("CREATE UNIQUE INDEX uq_payment_webhook_event ON baaki.payment_event (webhook_event_id) WHERE webhook_event_id IS NOT NULL")

    _sql("""
    CREATE TABLE baaki.ledger_entry (
      entry_id          uuid PRIMARY KEY,
      txn_id            uuid NOT NULL,
      trace_id          uuid,
      account_code      text NOT NULL,
      invoice_id        uuid REFERENCES baaki.invoice(invoice_id),
      direction         baaki.dr_cr NOT NULL,
      amount_paise      bigint NOT NULL,
      payment_event_id  uuid REFERENCES baaki.payment_event(payment_event_id),
      source            baaki.ledger_source NOT NULL,
      posted_at         timestamptz NOT NULL,
      CONSTRAINT ck_ledger_amount_positive CHECK (amount_paise > 0),
      CONSTRAINT ck_ledger_account_code CHECK (
        account_code ~ '^(AR|BUYER_CREDIT):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        OR account_code IN ('SALES','CASH_CLEARING','UNAPPLIED_CASH')),
      CONSTRAINT ck_ledger_ar_has_invoice CHECK ((account_code LIKE 'AR:%') = (invoice_id IS NOT NULL)),
      CONSTRAINT ck_ledger_issuance_no_event CHECK ((source = 'ISSUANCE') = (payment_event_id IS NULL)),
      CONSTRAINT ck_ledger_source_class CHECK (
        (source::text, baaki.account_code_class(account_code)) IN (
          ('ISSUANCE','AR'), ('ISSUANCE','SALES'),
          ('PAYMENT','CASH_CLEARING'), ('PAYMENT','AR'), ('PAYMENT','BUYER_CREDIT'), ('PAYMENT','UNAPPLIED_CASH'),
          ('REATTRIBUTION','UNAPPLIED_CASH'), ('REATTRIBUTION','AR'), ('REATTRIBUTION','BUYER_CREDIT')))
    )""")
    _sql("CREATE UNIQUE INDEX uq_ledger_event_code ON baaki.ledger_entry (payment_event_id, account_code) WHERE payment_event_id IS NOT NULL")
    _sql("CREATE INDEX ix_ledger_invoice ON baaki.ledger_entry (invoice_id)")
    _sql("CREATE INDEX ix_ledger_txn ON baaki.ledger_entry (txn_id)")

    # ── D-class decision chain ──────────────────────────────────────────────────────
    _sql("""
    CREATE TABLE baaki.agent_proposal (
      proposal_id         uuid PRIMARY KEY,
      trace_id            uuid NOT NULL,
      account_id          uuid NOT NULL REFERENCES baaki.account(account_id),
      kind                baaki.proposal_kind NOT NULL,
      invoice_id          uuid REFERENCES baaki.invoice(invoice_id),
      business_date       date NOT NULL,
      arm                 baaki.arm NOT NULL,
      provider            text NOT NULL,
      model_id            text NOT NULL,
      prompt_template_id  text NOT NULL,
      schema_version      text NOT NULL,
      prompt_hash         char(64) NOT NULL,
      input_hash          char(64) NOT NULL,
      raw_response        jsonb NOT NULL,
      parsed              jsonb,
      parse_status        baaki.parse_status NOT NULL,
      confidence          numeric(4,3),
      evidence            jsonb NOT NULL DEFAULT '[]'::jsonb,
      latency_ms          integer NOT NULL,
      created_at          timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_proposal_daily UNIQUE (invoice_id, business_date, kind, input_hash),
      CONSTRAINT ck_proposal_arm CHECK (arm = 'TREATMENT'),
      CONSTRAINT ck_proposal_parse CHECK ((parse_status = 'OK') = (parsed IS NOT NULL)),
      CONSTRAINT ck_proposal_confidence CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
      CONSTRAINT ck_proposal_no_money_keys CHECK (
        parsed IS NULL OR (
          NOT (parsed ?| ARRAY['amount','amount_paise','total','balance','discount','interest','fee','outstanding','due_amount','waiver','credit'])
          AND NOT baaki.jsonb_has_key_like(parsed, 'settle%')
          AND NOT baaki.jsonb_has_key_like(parsed, '%\\_date')))
    )""")
    _sql("CREATE INDEX ix_proposal_trace ON baaki.agent_proposal (trace_id)")
    _sql("CREATE INDEX ix_proposal_account_date ON baaki.agent_proposal (account_id, business_date)")

    _sql("""
    CREATE TABLE baaki.validation_result (
      validation_id      uuid PRIMARY KEY,
      proposal_id        uuid NOT NULL REFERENCES baaki.agent_proposal(proposal_id),
      trace_id           uuid NOT NULL,
      account_id         uuid NOT NULL,
      business_date      date NOT NULL,
      outcome            baaki.validation_outcome NOT NULL,
      rejection_reasons  baaki.rejection_reason[] NOT NULL DEFAULT '{}',
      normalized         jsonb,
      checks_run         jsonb NOT NULL,
      validator_version  text NOT NULL,
      validator_hash     char(64) NOT NULL,
      created_at         timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_validation_proposal UNIQUE (proposal_id),
      CONSTRAINT ck_validation_pass CHECK ((outcome = 'PASS') = (normalized IS NOT NULL)),
      CONSTRAINT ck_validation_reject CHECK ((outcome = 'REJECT') = (COALESCE(array_length(rejection_reasons, 1), 0) > 0))
    )""")

    _sql("""
    CREATE TABLE baaki.policy_decision (
      decision_id           uuid PRIMARY KEY,
      proposal_id           uuid REFERENCES baaki.agent_proposal(proposal_id),
      validation_id         uuid REFERENCES baaki.validation_result(validation_id),
      trace_id              uuid NOT NULL,
      account_id            uuid NOT NULL REFERENCES baaki.account(account_id),
      business_date         date NOT NULL,
      invoice_id            uuid NOT NULL REFERENCES baaki.invoice(invoice_id),
      arm                   baaki.arm NOT NULL,
      verdict               baaki.verdict NOT NULL,
      tier                  smallint NOT NULL,
      action_type           baaki.action_type,
      canonical_payload     jsonb,
      defer_until           timestamptz,
      matched_rules         text[] NOT NULL DEFAULT '{}',
      blocking_rules        jsonb NOT NULL DEFAULT '[]'::jsonb,
      effective_confidence  numeric(4,3),
      policy_version        text NOT NULL,
      kernel_version        text NOT NULL,
      policy_hash           char(64) NOT NULL,
      snapshot_hash         char(64) NOT NULL,
      degradation_level     baaki.degradation_level NOT NULL,
      decided_at            timestamptz NOT NULL,
      CONSTRAINT ck_executable_shape CHECK ((verdict IN ('ALLOW','REQUIRE_APPROVAL')) = (action_type IS NOT NULL AND canonical_payload IS NOT NULL)),
      CONSTRAINT ck_nonexecutable_shape CHECK ((verdict IN ('BLOCK','DEFER')) = (action_type IS NULL AND canonical_payload IS NULL)),
      CONSTRAINT ck_block_has_rules CHECK (verdict <> 'BLOCK' OR blocking_rules <> '[]'::jsonb),
      CONSTRAINT ck_defer_has_until CHECK ((verdict = 'DEFER') = (defer_until IS NOT NULL)),
      CONSTRAINT ck_tier2_approval CHECK (tier <> 2 OR verdict = 'REQUIRE_APPROVAL'),
      CONSTRAINT ck_nonllm_no_proposal CHECK (arm = 'TREATMENT' OR proposal_id IS NULL),
      CONSTRAINT ck_proposal_paired CHECK ((proposal_id IS NULL) = (validation_id IS NULL)),
      CONSTRAINT ck_tier_domain CHECK (tier IN (0,1,2)),
      CONSTRAINT uq_decision_validation_day UNIQUE (validation_id, business_date)
    )""")
    _sql("CREATE UNIQUE INDEX uq_decision_unlinked_day ON baaki.policy_decision (invoice_id, business_date, arm) WHERE proposal_id IS NULL")
    _sql("CREATE INDEX ix_decision_trace ON baaki.policy_decision (trace_id)")
    _sql("CREATE INDEX ix_decision_invoice_date ON baaki.policy_decision (invoice_id, business_date)")

    _sql("""
    CREATE TABLE baaki.recovery_action (
      action_id          uuid PRIMARY KEY,
      decision_id        uuid NOT NULL REFERENCES baaki.policy_decision(decision_id),
      trace_id           uuid NOT NULL,
      account_id         uuid NOT NULL REFERENCES baaki.account(account_id),
      invoice_id         uuid NOT NULL REFERENCES baaki.invoice(invoice_id),
      arm                baaki.arm NOT NULL,
      action_type        baaki.action_type NOT NULL,
      state              baaki.action_state NOT NULL,
      idempotency_key    char(64) NOT NULL,
      attempt_count      smallint NOT NULL DEFAULT 0,
      max_attempts       smallint NOT NULL DEFAULT 5,
      next_attempt_at    timestamptz,
      expires_at         timestamptz NOT NULL,
      approved_by_role   text,
      approved_by_note   text,
      approved_at        timestamptz,
      provider_ref       text,
      last_error_code    text,
      executed_at        timestamptz,
      confirmed_at       timestamptz,
      created_at         timestamptz NOT NULL,
      updated_at         timestamptz NOT NULL,
      CONSTRAINT uq_action_decision UNIQUE (decision_id),
      CONSTRAINT ck_action_attempts CHECK (attempt_count >= 0 AND max_attempts >= 1)
    )""")
    # One live action per idempotency key. A SUPERSEDED_DUPLICATE row records a collision and
    # therefore carries the same key (§6.6 W10); it is excluded from the uniqueness set.
    _sql("CREATE UNIQUE INDEX uq_action_idempotency ON baaki.recovery_action (idempotency_key) WHERE state <> 'SUPERSEDED_DUPLICATE'")
    _sql("CREATE INDEX ix_action_state_next ON baaki.recovery_action (state, next_attempt_at)")
    _sql("CREATE INDEX ix_action_invoice ON baaki.recovery_action (invoice_id)")

    _sql("""
    CREATE TABLE baaki.outbox (
      outbox_id         uuid PRIMARY KEY,
      action_id         uuid NOT NULL REFERENCES baaki.recovery_action(action_id),
      claimed_at        timestamptz,
      claimed_by        text,
      lease_expires_at  timestamptz,
      created_at        timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_outbox_action UNIQUE (action_id)
    )""")
    _sql("CREATE INDEX ix_outbox_claim ON baaki.outbox (claimed_at NULLS FIRST, outbox_id)")

    # ── R-class ─────────────────────────────────────────────────────────────────────
    _sql("""
    CREATE VIEW baaki.v_invoice_outstanding AS
    SELECT invoice_id,
           (COALESCE(SUM(CASE WHEN direction = 'DEBIT'  THEN amount_paise ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount_paise ELSE 0 END), 0))::bigint AS outstanding_paise
    FROM baaki.ledger_entry
    WHERE account_code LIKE 'AR:%' AND invoice_id IS NOT NULL
    GROUP BY invoice_id""")

    # ── Triggers (5) ────────────────────────────────────────────────────────────────
    _sql("""
    CREATE FUNCTION baaki.trgf_ledger_balanced() RETURNS trigger
    LANGUAGE plpgsql SET search_path = baaki, pg_catalog AS $$
    DECLARE v_delta bigint;
    BEGIN
      SELECT COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount_paise ELSE -amount_paise END), 0)
        INTO v_delta FROM baaki.ledger_entry WHERE txn_id = NEW.txn_id;
      IF v_delta <> 0 THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'ledger_unbalanced',
          DETAIL = 'txn ' || NEW.txn_id::text || ' delta ' || v_delta::text;
      END IF;
      RETURN NULL;
    END $$""")
    _sql("""
    CREATE CONSTRAINT TRIGGER trg_ledger_balanced AFTER INSERT ON baaki.ledger_entry
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION baaki.trgf_ledger_balanced()""")

    _sql("""
    CREATE FUNCTION baaki.trgf_ledger_one_invoice_per_txn() RETURNS trigger
    LANGUAGE plpgsql SET search_path = baaki, pg_catalog AS $$
    BEGIN
      IF NEW.invoice_id IS NOT NULL AND EXISTS (
           SELECT 1 FROM baaki.ledger_entry
           WHERE txn_id = NEW.txn_id AND invoice_id IS NOT NULL AND invoice_id <> NEW.invoice_id) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'cross_invoice_txn';
      END IF;
      RETURN NEW;
    END $$""")
    _sql("""
    CREATE TRIGGER trg_ledger_one_invoice_per_txn BEFORE INSERT ON baaki.ledger_entry
    FOR EACH ROW EXECUTE FUNCTION baaki.trgf_ledger_one_invoice_per_txn()""")

    _sql("""
    CREATE FUNCTION baaki.trgf_action_requires_executable_decision() RETURNS trigger
    LANGUAGE plpgsql SET search_path = baaki, pg_catalog AS $$
    DECLARE v_verdict baaki.verdict;
    BEGIN
      SELECT verdict INTO v_verdict FROM baaki.policy_decision WHERE decision_id = NEW.decision_id;
      -- Allowlist: anything not ALLOW / REQUIRE_APPROVAL (including a future verdict) is refused.
      IF v_verdict IS NULL OR v_verdict NOT IN ('ALLOW', 'REQUIRE_APPROVAL') THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'decision_not_executable';
      END IF;
      RETURN NEW;
    END $$""")
    _sql("""
    CREATE TRIGGER trg_action_requires_executable_decision BEFORE INSERT ON baaki.recovery_action
    FOR EACH ROW EXECUTE FUNCTION baaki.trgf_action_requires_executable_decision()""")

    _sql("""
    CREATE FUNCTION baaki.trgf_action_type_matches_decision() RETURNS trigger
    LANGUAGE plpgsql SET search_path = baaki, pg_catalog AS $$
    DECLARE v_type baaki.action_type;
    BEGIN
      SELECT action_type INTO v_type FROM baaki.policy_decision WHERE decision_id = NEW.decision_id;
      IF v_type IS DISTINCT FROM NEW.action_type THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'action_type_mismatch';
      END IF;
      RETURN NEW;
    END $$""")
    _sql("""
    CREATE TRIGGER trg_action_type_matches_decision BEFORE INSERT OR UPDATE OF action_type ON baaki.recovery_action
    FOR EACH ROW EXECUTE FUNCTION baaki.trgf_action_type_matches_decision()""")

    _sql("""
    CREATE FUNCTION baaki.trgf_decision_linkage() RETURNS trigger
    LANGUAGE plpgsql SET search_path = baaki, pg_catalog AS $$
    DECLARE v_p baaki.agent_proposal%ROWTYPE; v_v baaki.validation_result%ROWTYPE; v_inv_account uuid;
    BEGIN
      IF NEW.proposal_id IS NOT NULL THEN
        SELECT * INTO v_p FROM baaki.agent_proposal WHERE proposal_id = NEW.proposal_id;
        SELECT * INTO v_v FROM baaki.validation_result WHERE validation_id = NEW.validation_id;
        IF v_p.proposal_id IS NULL OR v_v.validation_id IS NULL
           OR v_v.proposal_id <> NEW.proposal_id
           OR v_p.trace_id <> NEW.trace_id
           OR v_p.account_id <> NEW.account_id
           OR v_p.business_date <> NEW.business_date THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'linkage_violation';
        END IF;
      END IF;
      SELECT account_id INTO v_inv_account FROM baaki.invoice WHERE invoice_id = NEW.invoice_id;
      IF v_inv_account IS DISTINCT FROM NEW.account_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'linkage_violation',
          DETAIL = 'invoice does not belong to account';
      END IF;
      RETURN NEW;
    END $$""")
    _sql("""
    CREATE TRIGGER trg_decision_linkage BEFORE INSERT ON baaki.policy_decision
    FOR EACH ROW EXECUTE FUNCTION baaki.trgf_decision_linkage()""")


def downgrade() -> None:
    _sql("DROP VIEW IF EXISTS baaki.v_invoice_outstanding")
    for t in TABLES_IN_DROP_ORDER:
        _sql(f"DROP TABLE IF EXISTS baaki.{t} CASCADE")
    for fn in [
        "trgf_ledger_balanced", "trgf_ledger_one_invoice_per_txn",
        "trgf_action_requires_executable_decision", "trgf_action_type_matches_decision",
        "trgf_decision_linkage", "jsonb_has_key_like(jsonb, text)", "account_code_class(text)",
    ]:
        name = fn if "(" in fn else f"{fn}()"
        _sql(f"DROP FUNCTION IF EXISTS baaki.{name}")
    for name in ENUMS:
        _sql(f"DROP TYPE IF EXISTS baaki.{name}")
    _sql("DROP EXTENSION IF EXISTS pgcrypto")
    _sql("DROP SCHEMA IF EXISTS baaki_write CASCADE")
    _sql("DROP SCHEMA IF EXISTS baaki CASCADE")
