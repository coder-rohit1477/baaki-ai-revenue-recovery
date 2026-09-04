"""Phase 1 writers W01–W10 — SECURITY DEFINER, owner baaki_owner, hardened per H1–H19.

ARCHITECTURE.md v3.2.1 §6.5, §6.6, §6.8, §6.9, §6.12, §6.20, §6.23, §1.5.1.

Every writer: SECURITY DEFINER · SET search_path = baaki, pg_catalog · LANGUAGE plpgsql ·
VOLATILE · no dynamic SQL · no current_setting() · REVOKE EXECUTE FROM PUBLIC immediately ·
validates before writing · raises named P0001 codes with zero rows written.

Provider JSON paths are function constants [ASSUME A-R8]; changing them is a migration.

Revision ID: 0002
Revises: 0001
"""

from alembic import op


def _sql(statement: str) -> None:
    """Execute raw SQL without SQLAlchemy bind-parameter parsing (PL/pgSQL bodies contain ':=' and JSON paths)."""
    # Raw DBAPI cursor, no parameters: neither SQLAlchemy (":name") nor psycopg ("%") token parsing applies.
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

HDR = "LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path = baaki, pg_catalog AS $$"

# ── H17 template (no P1 writer is human-only; P2+ writers must open with this block) ────
# IF pg_catalog.session_user <> 'baaki_ops' THEN
#   RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized_invoker';
# END IF;

FUNCTIONS: dict[str, tuple[str, str]] = {}  # name -> (signature, body)

FUNCTIONS["issue_invoice"] = (
    "(p_invoice_id uuid, p_org_id uuid, p_account_id uuid, p_invoice_number text, "
    "p_issued_paise bigint, p_issued_date date, p_due_date date, p_trace_id uuid) RETURNS uuid",
    f"""{HDR}
    DECLARE v_txn uuid := pg_catalog.gen_random_uuid(); v_acct_org uuid;
    BEGIN
      IF p_issued_paise IS NULL OR p_issued_paise <= 0 THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'issued_paise_not_positive'; END IF;
      IF p_due_date < p_issued_date THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'due_before_issued'; END IF;
      SELECT org_id INTO v_acct_org FROM baaki.account WHERE account_id = p_account_id;
      IF v_acct_org IS NULL OR v_acct_org <> p_org_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'account_not_in_org'; END IF;
      INSERT INTO baaki.invoice (invoice_id, org_id, account_id, invoice_number, issued_paise, issued_date, due_date, state)
      VALUES (p_invoice_id, p_org_id, p_account_id, p_invoice_number, p_issued_paise, p_issued_date, p_due_date, 'OPEN');
      INSERT INTO baaki.ledger_entry (entry_id, txn_id, trace_id, account_code, invoice_id, direction, amount_paise, payment_event_id, source, posted_at)
      VALUES (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, 'AR:' || p_account_id::text, p_invoice_id, 'DEBIT',  p_issued_paise, NULL, 'ISSUANCE', pg_catalog.now()),
             (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, 'SALES',                        NULL,         'CREDIT', p_issued_paise, NULL, 'ISSUANCE', pg_catalog.now());
      RETURN p_invoice_id;
    END $$""",
)

FUNCTIONS["record_webhook_event"] = (
    "(p_event_id uuid, p_provider text, p_raw_body text, p_signature_header text, p_received_at timestamptz) RETURNS uuid",
    f"""{HDR}
    DECLARE v_secret text; v_json jsonb; v_ok boolean; v_dedupe text; v_id uuid; v_entity text; v_status text; v_event text;
    BEGIN
      SELECT webhook_secret INTO v_secret FROM baaki.provider_secret WHERE provider = p_provider;
      IF v_secret IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'no_secret_for_provider'; END IF;
      BEGIN v_json := p_raw_body::jsonb;
      EXCEPTION WHEN others THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invalid_json'; END;
      -- H19: signature_ok is computed here from the in-database secret; never a parameter.
      v_ok := pg_catalog.encode(baaki.hmac(pg_catalog.convert_to(p_raw_body, 'UTF8'),
                                          pg_catalog.convert_to(v_secret, 'UTF8'), 'sha256'), 'hex')
              = pg_catalog.lower(COALESCE(p_signature_header, ''));
      -- dedupe_key [A-R2/A-R8]: event id if present, else SHA256(event|entity|status), else SHA256(body).
      v_dedupe := v_json #>> '{{id}}';
      IF v_dedupe IS NULL THEN
        v_event  := v_json #>> '{{event}}';
        v_entity := v_json #>> '{{payload,payment,entity,id}}';
        v_status := v_json #>> '{{payload,payment,entity,status}}';
        IF v_entity IS NOT NULL THEN
          v_dedupe := pg_catalog.encode(baaki.digest(pg_catalog.convert_to(
                        COALESCE(v_event,'') || '|' || v_entity || '|' || COALESCE(v_status,''), 'UTF8'), 'sha256'), 'hex');
        ELSE
          v_dedupe := pg_catalog.encode(baaki.digest(pg_catalog.convert_to(p_raw_body, 'UTF8'), 'sha256'), 'hex');
        END IF;
      END IF;
      INSERT INTO baaki.webhook_event (event_id, provider, dedupe_key, raw_body, signature_header, signature_ok, received_at)
      VALUES (p_event_id, p_provider, v_dedupe, p_raw_body, p_signature_header, v_ok, p_received_at)
      ON CONFLICT (provider, dedupe_key) DO NOTHING
      RETURNING event_id INTO v_id;
      IF v_id IS NULL THEN
        SELECT event_id INTO v_id FROM baaki.webhook_event WHERE provider = p_provider AND dedupe_key = v_dedupe;
      END IF;
      RETURN v_id;
    END $$""",
)

FUNCTIONS["record_sweep_run"] = (
    "(p_sweep_run_id uuid, p_provider text, p_window_from timestamptz, p_window_to timestamptz, p_requested_at timestamptz, p_raw_response text) RETURNS uuid",
    f"""{HDR}
    DECLARE v_json jsonb; v_hash char(64); v_count integer; v_id uuid;
    BEGIN
      BEGIN v_json := p_raw_response::jsonb;
      EXCEPTION WHEN others THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invalid_json'; END;
      v_hash  := pg_catalog.encode(baaki.digest(pg_catalog.convert_to(p_raw_response, 'UTF8'), 'sha256'), 'hex');
      -- item_count [A-R8]: items array at {{items}}; absent/non-array -> 0.
      IF pg_catalog.jsonb_typeof(v_json #> '{{items}}') = 'array' THEN
        v_count := pg_catalog.jsonb_array_length(v_json #> '{{items}}');
      ELSE v_count := 0; END IF;
      INSERT INTO baaki.sweep_run (sweep_run_id, provider, window_from, window_to, requested_at, raw_response, raw_response_hash, item_count, created_by_role)
      VALUES (p_sweep_run_id, p_provider, p_window_from, p_window_to, p_requested_at, p_raw_response, v_hash, v_count, session_user::text)
      ON CONFLICT (provider, raw_response_hash) DO NOTHING
      RETURNING sweep_run_id INTO v_id;
      IF v_id IS NULL THEN
        SELECT sweep_run_id INTO v_id FROM baaki.sweep_run WHERE provider = p_provider AND raw_response_hash = v_hash;
      END IF;
      RETURN v_id;
    END $$""",
)

FUNCTIONS["record_payment_event"] = (
    "(p_payment_event_id uuid, p_webhook_event_id uuid, p_sweep_run_id uuid, p_provider_payload_raw text, "
    "p_attributed_invoice_id uuid, p_attribution_method baaki.attribution_method) RETURNS uuid",
    f"""{HDR}
    DECLARE v_provider text; v_body text; v_sig_ok boolean; v_item jsonb; v_source baaki.payment_source;
            v_ppid text; v_amount bigint; v_currency text; v_status text; v_paid_epoch bigint; v_paid_at timestamptz; v_hash char(64);
    BEGIN
      IF (p_webhook_event_id IS NULL) = (p_sweep_run_id IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001',
          MESSAGE = CASE WHEN p_webhook_event_id IS NULL THEN 'evidence_required' ELSE 'evidence_ambiguous' END; END IF;
      IF p_provider_payload_raw IS NULL OR p_provider_payload_raw = '' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_required'; END IF;
      IF p_webhook_event_id IS NOT NULL THEN
        SELECT provider, raw_body, signature_ok INTO v_provider, v_body, v_sig_ok FROM baaki.webhook_event WHERE event_id = p_webhook_event_id;
        IF v_provider IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'evidence_not_found'; END IF;
        IF NOT v_sig_ok THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unverified_evidence'; END IF;
        v_source := 'WEBHOOK';
      ELSE
        SELECT provider, raw_response INTO v_provider, v_body FROM baaki.sweep_run WHERE sweep_run_id = p_sweep_run_id;
        IF v_provider IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'evidence_not_found'; END IF;
        v_source := 'SWEEP';
      END IF;
      -- Containment: the item must be a literal substring of the stored evidence body.
      IF pg_catalog.strpos(v_body, p_provider_payload_raw) = 0 THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_not_in_evidence'; END IF;
      BEGIN v_item := p_provider_payload_raw::jsonb;
      EXCEPTION WHEN others THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invalid_json'; END;
      -- Extraction [A-R8]: payment entity fields. No financial value is a parameter of this function.
      v_ppid     := v_item #>> '{{id}}';
      v_currency := v_item #>> '{{currency}}';
      v_status   := v_item #>> '{{status}}';
      BEGIN
        v_amount     := (v_item #>> '{{amount}}')::bigint;
        v_paid_epoch := (v_item #>> '{{created_at}}')::bigint;
      EXCEPTION WHEN others THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_field_missing'; END;
      IF v_ppid IS NULL OR v_amount IS NULL OR v_currency IS NULL OR v_status IS NULL OR v_paid_epoch IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_field_missing'; END IF;
      v_paid_at := pg_catalog.to_timestamp(v_paid_epoch);
      IF v_status NOT IN ('captured') THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'status_not_accepted'; END IF;  -- [A-R7]
      IF v_currency <> 'INR' THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'currency_not_inr'; END IF;
      IF v_amount <= 0 THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'amount_not_positive'; END IF;
      IF p_attribution_method = 'HUMAN_REATTRIBUTION' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'method_not_allowed'; END IF;
      IF (p_attribution_method = 'UNATTRIBUTED') <> (p_attributed_invoice_id IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'attribution_inconsistent'; END IF;
      IF p_attributed_invoice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM baaki.invoice WHERE invoice_id = p_attributed_invoice_id) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invoice_not_found'; END IF;
      v_hash := pg_catalog.encode(baaki.digest(pg_catalog.convert_to(p_provider_payload_raw, 'UTF8'), 'sha256'), 'hex');
      INSERT INTO baaki.payment_event (payment_event_id, provider, provider_payment_id, amount_paise, currency, provider_status, paid_at,
                                       source, webhook_event_id, sweep_run_id, provider_payload_raw, provider_payload_hash,
                                       attributed_invoice_id, attribution_method)
      VALUES (p_payment_event_id, v_provider, v_ppid, v_amount, v_currency, v_status, v_paid_at,
              v_source, p_webhook_event_id, p_sweep_run_id, p_provider_payload_raw, v_hash,
              p_attributed_invoice_id, p_attribution_method);
      IF p_webhook_event_id IS NOT NULL THEN
        UPDATE baaki.webhook_event SET processed_at = pg_catalog.now() WHERE event_id = p_webhook_event_id AND processed_at IS NULL;
      END IF;
      RETURN p_payment_event_id;
    END $$""",
)

FUNCTIONS["ledger_apply_payment"] = (
    "(p_payment_event_id uuid, p_trace_id uuid) RETURNS uuid",
    f"""{HDR}
    DECLARE v_ev baaki.payment_event%ROWTYPE; v_inv baaki.invoice%ROWTYPE; v_outstanding bigint;
            v_ar_credit bigint; v_excess bigint; v_txn uuid := pg_catalog.gen_random_uuid(); v_ar text; v_bc text;
    BEGIN
      SELECT * INTO v_ev FROM baaki.payment_event WHERE payment_event_id = p_payment_event_id FOR UPDATE;
      IF v_ev.payment_event_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'event_not_found'; END IF;
      IF v_ev.applied_at IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'already_applied'; END IF;
      IF v_ev.attributed_invoice_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'not_attributed'; END IF;
      SELECT * INTO v_inv FROM baaki.invoice WHERE invoice_id = v_ev.attributed_invoice_id FOR UPDATE;
      SELECT COALESCE(outstanding_paise, 0) INTO v_outstanding FROM baaki.v_invoice_outstanding WHERE invoice_id = v_inv.invoice_id;
      v_outstanding := COALESCE(v_outstanding, 0);
      -- §6.12: cap BEFORE any line is written; the projection never passes through a negative value.
      v_ar_credit := LEAST(v_ev.amount_paise, v_outstanding);
      v_excess    := v_ev.amount_paise - v_ar_credit;
      v_ar := 'AR:' || v_inv.account_id::text;
      v_bc := 'BUYER_CREDIT:' || v_inv.account_id::text;
      INSERT INTO baaki.ledger_entry (entry_id, txn_id, trace_id, account_code, invoice_id, direction, amount_paise, payment_event_id, source, posted_at)
      VALUES (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, 'CASH_CLEARING', NULL, 'DEBIT', v_ev.amount_paise, v_ev.payment_event_id, 'PAYMENT', pg_catalog.now());
      IF v_ar_credit > 0 THEN
        INSERT INTO baaki.ledger_entry (entry_id, txn_id, trace_id, account_code, invoice_id, direction, amount_paise, payment_event_id, source, posted_at)
        VALUES (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, v_ar, v_inv.invoice_id, 'CREDIT', v_ar_credit, v_ev.payment_event_id, 'PAYMENT', pg_catalog.now());
      END IF;
      IF v_excess > 0 THEN
        INSERT INTO baaki.ledger_entry (entry_id, txn_id, trace_id, account_code, invoice_id, direction, amount_paise, payment_event_id, source, posted_at)
        VALUES (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, v_bc, NULL, 'CREDIT', v_excess, v_ev.payment_event_id, 'PAYMENT', pg_catalog.now());
      END IF;
      IF v_ar_credit = v_outstanding AND v_outstanding > 0 THEN
        UPDATE baaki.invoice SET state = 'PAID' WHERE invoice_id = v_inv.invoice_id;
      END IF;
      UPDATE baaki.payment_event SET applied_at = pg_catalog.now() WHERE payment_event_id = p_payment_event_id;
      RETURN v_txn;
    END $$""",
)

FUNCTIONS["ledger_post_unapplied"] = (
    "(p_payment_event_id uuid, p_trace_id uuid) RETURNS uuid",
    f"""{HDR}
    DECLARE v_ev baaki.payment_event%ROWTYPE; v_txn uuid := pg_catalog.gen_random_uuid();
    BEGIN
      SELECT * INTO v_ev FROM baaki.payment_event WHERE payment_event_id = p_payment_event_id FOR UPDATE;
      IF v_ev.payment_event_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'event_not_found'; END IF;
      IF v_ev.applied_at IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'already_applied'; END IF;
      IF v_ev.attributed_invoice_id IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'attributed_use_apply'; END IF;
      INSERT INTO baaki.ledger_entry (entry_id, txn_id, trace_id, account_code, invoice_id, direction, amount_paise, payment_event_id, source, posted_at)
      VALUES (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, 'CASH_CLEARING',  NULL, 'DEBIT',  v_ev.amount_paise, v_ev.payment_event_id, 'PAYMENT', pg_catalog.now()),
             (pg_catalog.gen_random_uuid(), v_txn, p_trace_id, 'UNAPPLIED_CASH', NULL, 'CREDIT', v_ev.amount_paise, v_ev.payment_event_id, 'PAYMENT', pg_catalog.now());
      UPDATE baaki.payment_event SET applied_at = pg_catalog.now() WHERE payment_event_id = p_payment_event_id;
      RETURN v_txn;
    END $$""",
)

FUNCTIONS["record_agent_proposal"] = (
    "(p_proposal_id uuid, p_trace_id uuid, p_account_id uuid, p_kind baaki.proposal_kind, p_invoice_id uuid, "
    "p_business_date date, p_provider text, p_model_id text, p_prompt_template_id text, p_schema_version text, "
    "p_prompt_hash char(64), p_input_hash char(64), p_raw_response jsonb, p_parsed jsonb, p_parse_status baaki.parse_status, "
    "p_confidence numeric, p_evidence jsonb, p_latency_ms integer) RETURNS uuid",
    f"""{HDR}
    DECLARE v_inv_account uuid;
    BEGIN
      IF (p_parse_status = 'OK') <> (p_parsed IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'parse_status_mismatch'; END IF;
      IF p_parsed IS NOT NULL AND (
           p_parsed ?| ARRAY['amount','amount_paise','total','balance','discount','interest','fee','outstanding','due_amount','waiver','credit']
           OR baaki.jsonb_has_key_like(p_parsed, 'settle%')) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'forbidden_money_field'; END IF;
      IF p_parsed IS NOT NULL AND baaki.jsonb_has_key_like(p_parsed, '%\\_date') THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'typed_date_forbidden'; END IF;
      IF NOT EXISTS (SELECT 1 FROM baaki.account WHERE account_id = p_account_id) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'account_not_found'; END IF;
      IF p_invoice_id IS NOT NULL THEN
        SELECT account_id INTO v_inv_account FROM baaki.invoice WHERE invoice_id = p_invoice_id;
        IF v_inv_account IS DISTINCT FROM p_account_id THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invoice_not_in_account'; END IF;
      END IF;
      INSERT INTO baaki.agent_proposal (proposal_id, trace_id, account_id, kind, invoice_id, business_date, arm, provider, model_id,
                                        prompt_template_id, schema_version, prompt_hash, input_hash, raw_response, parsed, parse_status,
                                        confidence, evidence, latency_ms)
      VALUES (p_proposal_id, p_trace_id, p_account_id, p_kind, p_invoice_id, p_business_date, 'TREATMENT', p_provider, p_model_id,
              p_prompt_template_id, p_schema_version, p_prompt_hash, p_input_hash, p_raw_response, p_parsed, p_parse_status,
              p_confidence, COALESCE(p_evidence, '[]'::jsonb), p_latency_ms);
      RETURN p_proposal_id;
    END $$""",
)

FUNCTIONS["record_validation_result"] = (
    "(p_validation_id uuid, p_proposal_id uuid, p_outcome baaki.validation_outcome, p_rejection_reasons baaki.rejection_reason[], "
    "p_normalized jsonb, p_checks_run jsonb, p_validator_version text, p_validator_hash char(64)) RETURNS uuid",
    f"""{HDR}
    DECLARE v_p baaki.agent_proposal%ROWTYPE;
    BEGIN
      SELECT * INTO v_p FROM baaki.agent_proposal WHERE proposal_id = p_proposal_id;
      IF v_p.proposal_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'proposal_not_found'; END IF;
      IF (p_outcome = 'PASS') <> (p_normalized IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      IF (p_outcome = 'REJECT') <> (COALESCE(array_length(p_rejection_reasons, 1), 0) > 0) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      -- trace_id / account_id / business_date are DERIVED from the proposal (V8); no parameters exist.
      INSERT INTO baaki.validation_result (validation_id, proposal_id, trace_id, account_id, business_date, outcome, rejection_reasons,
                                           normalized, checks_run, validator_version, validator_hash)
      VALUES (p_validation_id, p_proposal_id, v_p.trace_id, v_p.account_id, v_p.business_date, p_outcome, COALESCE(p_rejection_reasons, '{{}}'),
              p_normalized, p_checks_run, p_validator_version, p_validator_hash);
      RETURN p_validation_id;
    END $$""",
)

FUNCTIONS["record_policy_decision"] = (
    "(p_decision_id uuid, p_proposal_id uuid, p_validation_id uuid, p_trace_id uuid, p_account_id uuid, p_business_date date, "
    "p_invoice_id uuid, p_arm baaki.arm, p_verdict baaki.verdict, p_tier smallint, p_action_type baaki.action_type, "
    "p_canonical_payload jsonb, p_defer_until timestamptz, p_matched_rules text[], p_blocking_rules jsonb, "
    "p_effective_confidence numeric, p_policy_version text, p_kernel_version text, p_policy_hash char(64), "
    "p_snapshot_hash char(64), p_degradation_level baaki.degradation_level, p_candidate_invoice_ids uuid[]) RETURNS uuid",
    f"""{HDR}
    DECLARE v_p baaki.agent_proposal%ROWTYPE; v_v baaki.validation_result%ROWTYPE;
            v_trace uuid; v_account uuid; v_date date; v_inv baaki.invoice%ROWTYPE; v_outstanding bigint;
            v_exec boolean; v_keys text[]; v_allowed text[]; v_required text[]; v_k text;
            v_tpl baaki.template_registry%ROWTYPE; v_contact baaki.contact%ROWTYPE; v_sum bigint; v_reason text; v_queue text;
    BEGIN
      -- ── linkage (§6.8): derive when linked, accept when unlinked ─────────────────
      IF (p_proposal_id IS NULL) <> (p_validation_id IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      IF p_proposal_id IS NOT NULL THEN
        SELECT * INTO v_p FROM baaki.agent_proposal WHERE proposal_id = p_proposal_id;
        SELECT * INTO v_v FROM baaki.validation_result WHERE validation_id = p_validation_id;
        IF v_p.proposal_id IS NULL OR v_v.validation_id IS NULL OR v_v.proposal_id <> p_proposal_id THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'linkage_mismatch'; END IF;
        IF p_arm <> 'TREATMENT' THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
        IF v_v.outcome = 'REJECT' AND p_degradation_level = 'L0' THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'rejected_needs_degradation'; END IF;
        IF v_p.invoice_id IS NOT NULL AND v_p.invoice_id <> p_invoice_id THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invoice_scope_mismatch'; END IF;
        v_trace := v_p.trace_id; v_account := v_p.account_id; v_date := v_p.business_date;   -- caller values ignored
      ELSE
        IF p_trace_id IS NULL OR p_account_id IS NULL OR p_business_date IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unlinked_requires_context'; END IF;
        v_trace := p_trace_id; v_account := p_account_id; v_date := p_business_date;
      END IF;
      -- ── shape (§6.7) ─────────────────────────────────────────────────────────────
      v_exec := p_verdict IN ('ALLOW','REQUIRE_APPROVAL');
      IF v_exec <> (p_action_type IS NOT NULL AND p_canonical_payload IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      IF NOT v_exec AND (p_action_type IS NOT NULL OR p_canonical_payload IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      IF p_verdict = 'BLOCK' AND (p_blocking_rules IS NULL OR p_blocking_rules = '[]'::jsonb) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      IF (p_verdict = 'DEFER') <> (p_defer_until IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      IF p_tier NOT IN (0,1,2) OR (p_tier = 2 AND p_verdict <> 'REQUIRE_APPROVAL') THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'shape_violation'; END IF;
      -- ── P13 / SC4 / SC5 ──────────────────────────────────────────────────────────
      IF p_candidate_invoice_ids IS NULL OR NOT (p_invoice_id = ANY(p_candidate_invoice_ids)) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'invoice_not_candidate'; END IF;
      SELECT * INTO v_inv FROM baaki.invoice WHERE invoice_id = p_invoice_id FOR SHARE;
      IF v_inv.invoice_id IS NULL OR v_inv.account_id <> v_account THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'linkage_violation'; END IF;
      -- ── payload (§6.9, §1.5, §1.5.1, §6.14) ─────────────────────────────────────
      IF v_exec THEN
        IF pg_catalog.jsonb_typeof(p_canonical_payload) <> 'object' THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_shape'; END IF;
        IF (p_canonical_payload ->> 'action_type') IS DISTINCT FROM p_action_type::text THEN
          RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_shape'; END IF;
        SELECT array_agg(k) INTO v_keys FROM pg_catalog.jsonb_object_keys(p_canonical_payload) k;
        CASE p_action_type
          WHEN 'SUPPRESS' THEN
            v_allowed := ARRAY['action_type','reason_code']; v_required := v_allowed;
            PERFORM (p_canonical_payload ->> 'reason_code')::baaki.suppress_reason;
          WHEN 'SCHEDULE_FOLLOWUP' THEN
            v_allowed := ARRAY['action_type','followup_date']; v_required := v_allowed;
            PERFORM (p_canonical_payload ->> 'followup_date')::date;
          WHEN 'REQUEST_DISPUTE_DETAILS' THEN
            v_allowed := ARRAY['action_type','contact_id','channel','template_id']; v_required := v_allowed;
          WHEN 'SEND_REMINDER' THEN
            v_allowed := ARRAY['action_type','contact_id','channel','template_id','existing_link_ref'];
            v_required := ARRAY['action_type','contact_id','channel','template_id'];
          WHEN 'SEND_PAYMENT_LINK' THEN
            v_allowed := ARRAY['action_type','amount_paise','contact_id','channel','template_id','expires_at','notes']; v_required := v_allowed;
          WHEN 'PROPOSE_INSTALLMENT_PLAN' THEN
            v_allowed := ARRAY['action_type','parts','contact_id','channel','template_id']; v_required := v_allowed;
          WHEN 'ESCALATE_TO_HUMAN' THEN
            v_allowed := ARRAY['action_type','reason_code','assignee_queue']; v_required := v_allowed;
        END CASE;
        FOREACH v_k IN ARRAY v_keys LOOP
          IF NOT (v_k = ANY(v_allowed)) THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_extra_key', DETAIL = v_k; END IF;
        END LOOP;
        FOREACH v_k IN ARRAY v_required LOOP
          IF NOT (p_canonical_payload ? v_k) THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_shape', DETAIL = v_k; END IF;
        END LOOP;
        IF p_action_type = 'ESCALATE_TO_HUMAN' THEN
          v_reason := (p_canonical_payload ->> 'reason_code')::baaki.escalation_reason::text;
          v_queue  := (p_canonical_payload ->> 'assignee_queue')::baaki.assignee_queue::text;
          -- §1.5.1 / §6.9: assignee_queue is a pure function of reason_code.
          IF v_queue <> (CASE v_reason WHEN 'DISPUTE_UNRESOLVED' THEN 'DISPUTES' ELSE 'COLLECTIONS' END) THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'queue_reason_mismatch'; END IF;
        END IF;
        IF p_action_type IN ('REQUEST_DISPUTE_DETAILS','SEND_REMINDER','SEND_PAYMENT_LINK','PROPOSE_INSTALLMENT_PLAN') THEN
          SELECT * INTO v_contact FROM baaki.contact WHERE contact_id = (p_canonical_payload ->> 'contact_id')::uuid;
          IF v_contact.contact_id IS NULL OR v_contact.account_id <> v_account OR NOT v_contact.active OR v_contact.opted_out THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'contact_invalid'; END IF;
          SELECT * INTO v_tpl FROM baaki.template_registry WHERE template_id = p_canonical_payload ->> 'template_id';
          IF v_tpl.template_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'template_not_registered'; END IF;
          IF v_tpl.channel::text <> (p_canonical_payload ->> 'channel')
             OR v_tpl.action_type <> p_action_type OR NOT v_tpl.active THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'template_incompatible'; END IF;
          PERFORM (p_canonical_payload ->> 'channel')::baaki.channel;
        END IF;
        IF p_action_type = 'SEND_PAYMENT_LINK' THEN
          SELECT COALESCE(outstanding_paise, 0) INTO v_outstanding FROM baaki.v_invoice_outstanding WHERE invoice_id = p_invoice_id;
          IF (p_canonical_payload ->> 'amount_paise')::bigint <= 0
             OR (p_canonical_payload ->> 'amount_paise')::bigint <> COALESCE(v_outstanding, 0) THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'cp5_amount_mismatch'; END IF;
          IF pg_catalog.jsonb_typeof(p_canonical_payload -> 'notes') <> 'object'
             OR (p_canonical_payload #>> '{{notes,invoice_id}}')::uuid <> p_invoice_id THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'payload_shape', DETAIL = 'notes'; END IF;
        END IF;
        IF p_action_type = 'PROPOSE_INSTALLMENT_PLAN' THEN
          SELECT COALESCE(outstanding_paise, 0) INTO v_outstanding FROM baaki.v_invoice_outstanding WHERE invoice_id = p_invoice_id;
          SELECT SUM((e ->> 'amount_paise')::bigint) INTO v_sum FROM pg_catalog.jsonb_array_elements(p_canonical_payload -> 'parts') e;
          IF v_sum IS NULL OR v_sum <> COALESCE(v_outstanding, 0)
             OR EXISTS (SELECT 1 FROM pg_catalog.jsonb_array_elements(p_canonical_payload -> 'parts') e WHERE (e ->> 'amount_paise')::bigint <= 0) THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'cp2_parts_mismatch'; END IF;
        END IF;
      END IF;
      INSERT INTO baaki.policy_decision (decision_id, proposal_id, validation_id, trace_id, account_id, business_date, invoice_id, arm,
                                         verdict, tier, action_type, canonical_payload, defer_until, matched_rules, blocking_rules,
                                         effective_confidence, policy_version, kernel_version, policy_hash, snapshot_hash,
                                         degradation_level, decided_at)
      VALUES (p_decision_id, p_proposal_id, p_validation_id, v_trace, v_account, v_date, p_invoice_id, p_arm,
              p_verdict, p_tier, p_action_type, p_canonical_payload, p_defer_until, COALESCE(p_matched_rules, '{{}}'),
              COALESCE(p_blocking_rules, '[]'::jsonb), p_effective_confidence, p_policy_version, p_kernel_version,
              p_policy_hash, p_snapshot_hash, p_degradation_level, pg_catalog.now());
      RETURN p_decision_id;
    END $$""",
)

FUNCTIONS["create_recovery_action"] = (
    "(p_action_id uuid, p_decision_id uuid, p_idempotency_key char(64), p_expires_at timestamptz, p_now timestamptz, p_outbox_id uuid, "
    "OUT action_id uuid, OUT superseded boolean) RETURNS record",
    f"""{HDR}
    DECLARE v_d baaki.policy_decision%ROWTYPE; v_state baaki.action_state; v_existing uuid;
    BEGIN
      SELECT * INTO v_d FROM baaki.policy_decision WHERE decision_id = p_decision_id;
      IF v_d.decision_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'decision_not_found'; END IF;
      -- Allowlist (P9): a non-executable verdict never produces an action.
      IF v_d.verdict NOT IN ('ALLOW','REQUIRE_APPROVAL') THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'decision_not_executable'; END IF;
      IF p_expires_at <= p_now THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'expires_before_now'; END IF;
      -- R3: initial state is a pure mapping of the verdict.
      v_state := CASE v_d.verdict WHEN 'REQUIRE_APPROVAL' THEN 'PENDING_APPROVAL'::baaki.action_state ELSE 'QUEUED'::baaki.action_state END;
      -- R5: idempotency collision with a DIFFERENT decision -> record a SUPERSEDED_DUPLICATE, no outbox, return the original.
      SELECT ra.action_id INTO v_existing FROM baaki.recovery_action ra
       WHERE ra.idempotency_key = p_idempotency_key AND ra.state <> 'SUPERSEDED_DUPLICATE' AND ra.decision_id <> p_decision_id;
      IF v_existing IS NOT NULL THEN v_state := 'SUPERSEDED_DUPLICATE'; END IF;
      INSERT INTO baaki.recovery_action (action_id, decision_id, trace_id, account_id, invoice_id, arm, action_type, state,
                                         idempotency_key, expires_at, created_at, updated_at)
      VALUES (p_action_id, v_d.decision_id, v_d.trace_id, v_d.account_id, v_d.invoice_id, v_d.arm, v_d.action_type, v_state,
              p_idempotency_key, p_expires_at, p_now, p_now);
      IF v_state = 'QUEUED' THEN
        INSERT INTO baaki.outbox (outbox_id, action_id) VALUES (p_outbox_id, p_action_id);
      END IF;
      action_id := COALESCE(v_existing, p_action_id);
      superseded := v_existing IS NOT NULL;
      RETURN;
    END $$""",
)

ORDER = [
    "issue_invoice", "record_webhook_event", "record_sweep_run", "record_payment_event",
    "ledger_apply_payment", "ledger_post_unapplied", "record_agent_proposal",
    "record_validation_result", "record_policy_decision", "create_recovery_action",
]


def _argtypes(signature: str) -> str:
    inner = signature[signature.index("(") + 1 : signature.rindex(")")]
    types = []
    for part in inner.split(","):
        toks = part.strip().split()
        if not toks:
            continue
        if toks[0].upper() == "OUT":
            continue
        types.append(" ".join(toks[1:]))
    return ", ".join(types)


def upgrade() -> None:
    for name in ORDER:
        sig, body = FUNCTIONS[name]
        _sql(f"CREATE FUNCTION baaki_write.{name}{sig}\n{body}")
        # H6: PUBLIC EXECUTE is revoked immediately; 0003 grants explicitly.
        _sql(f"REVOKE EXECUTE ON FUNCTION baaki_write.{name}({_argtypes(sig)}) FROM PUBLIC")


def downgrade() -> None:
    for name in reversed(ORDER):
        sig, _ = FUNCTIONS[name]
        _sql(f"DROP FUNCTION IF EXISTS baaki_write.{name}({_argtypes(sig)})")
