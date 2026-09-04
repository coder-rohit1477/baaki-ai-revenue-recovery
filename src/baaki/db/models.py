"""Read-side table definitions mirroring migration 0001 (15 tables + 1 view).

These are for typed SELECTs only. No insert/update helpers exist for F/D-class tables — every
write goes through baaki_write.* (ARCHITECTURE.md I10). tests/schema compares these to
information_schema.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID

from baaki.db.base import metadata


def _enum(name: str) -> ENUM:
    return ENUM(name=name, schema="baaki", create_type=False)


TS = DateTime(timezone=True)

organization = Table(
    "organization", metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("timezone", Text, nullable=False),
    Column("kill_switch", Boolean, nullable=False),
    Column("created_at", TS, nullable=False),
)
account = Table(
    "account", metadata,
    Column("account_id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("external_ref", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("opt_out", Boolean, nullable=False),
    Column("risk_band", SmallInteger, nullable=False),
    Column("created_at", TS, nullable=False),
)
contact = Table(
    "contact", metadata,
    Column("contact_id", UUID(as_uuid=True), primary_key=True),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("channel", _enum("channel"), nullable=False),
    Column("address_hash", String(64), nullable=False),
    Column("address_redacted", Text, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("opted_out", Boolean, nullable=False),
    Column("created_at", TS, nullable=False),
)
template_registry = Table(
    "template_registry", metadata,
    Column("template_id", Text, primary_key=True),
    Column("channel", _enum("channel"), nullable=False),
    Column("action_type", _enum("action_type"), nullable=False),
    Column("purpose", _enum("template_purpose"), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    Column("body_hash", String(64), nullable=False),
    Column("created_at", TS, nullable=False),
)
provider_secret = Table(
    "provider_secret", metadata,
    Column("provider", Text, primary_key=True),
    Column("webhook_secret", Text, nullable=False),
    Column("rotated_at", TS),
)
invoice = Table(
    "invoice", metadata,
    Column("invoice_id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("invoice_number", Text, nullable=False),
    Column("issued_paise", BigInteger, nullable=False),
    Column("issued_date", Date, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("state", _enum("invoice_state"), nullable=False),
    Column("created_at", TS, nullable=False),
)
webhook_event = Table(
    "webhook_event", metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("dedupe_key", Text, nullable=False),
    Column("raw_body", Text, nullable=False),
    Column("signature_header", Text),
    Column("signature_ok", Boolean, nullable=False),
    Column("received_at", TS, nullable=False),
    Column("processed_at", TS),
    Column("created_at", TS, nullable=False),
)
sweep_run = Table(
    "sweep_run", metadata,
    Column("sweep_run_id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("window_from", TS, nullable=False),
    Column("window_to", TS, nullable=False),
    Column("requested_at", TS, nullable=False),
    Column("raw_response", Text, nullable=False),
    Column("raw_response_hash", String(64), nullable=False),
    Column("item_count", Integer, nullable=False),
    Column("created_by_role", Text, nullable=False),
    Column("provider_call_id", UUID(as_uuid=True)),
    Column("created_at", TS, nullable=False),
)
payment_event = Table(
    "payment_event", metadata,
    Column("payment_event_id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("provider_payment_id", Text, nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("provider_status", Text, nullable=False),
    Column("paid_at", TS, nullable=False),
    Column("source", _enum("payment_source"), nullable=False),
    Column("webhook_event_id", UUID(as_uuid=True)),
    Column("sweep_run_id", UUID(as_uuid=True)),
    Column("provider_payload_raw", Text, nullable=False),
    Column("provider_payload_hash", String(64), nullable=False),
    Column("attributed_invoice_id", UUID(as_uuid=True)),
    Column("attribution_method", _enum("attribution_method"), nullable=False),
    Column("applied_at", TS),
    Column("reattributed_at", TS),
    Column("reattributed_by_role", Text),
    Column("reattributed_by_note", Text),
    Column("created_at", TS, nullable=False),
)
ledger_entry = Table(
    "ledger_entry", metadata,
    Column("entry_id", UUID(as_uuid=True), primary_key=True),
    Column("txn_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", UUID(as_uuid=True)),
    Column("account_code", Text, nullable=False),
    Column("invoice_id", UUID(as_uuid=True)),
    Column("direction", _enum("dr_cr"), nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("payment_event_id", UUID(as_uuid=True)),
    Column("source", _enum("ledger_source"), nullable=False),
    Column("posted_at", TS, nullable=False),
)
agent_proposal = Table(
    "agent_proposal", metadata,
    Column("proposal_id", UUID(as_uuid=True), primary_key=True),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("kind", _enum("proposal_kind"), nullable=False),
    Column("invoice_id", UUID(as_uuid=True)),
    Column("business_date", Date, nullable=False),
    Column("arm", _enum("arm"), nullable=False),
    Column("provider", Text, nullable=False),
    Column("model_id", Text, nullable=False),
    Column("prompt_template_id", Text, nullable=False),
    Column("schema_version", Text, nullable=False),
    Column("prompt_hash", String(64), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("raw_response", JSONB, nullable=False),
    Column("parsed", JSONB),
    Column("parse_status", _enum("parse_status"), nullable=False),
    Column("confidence", Numeric(4, 3)),
    Column("evidence", JSONB, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("created_at", TS, nullable=False),
)
validation_result = Table(
    "validation_result", metadata,
    Column("validation_id", UUID(as_uuid=True), primary_key=True),
    Column("proposal_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("business_date", Date, nullable=False),
    Column("outcome", _enum("validation_outcome"), nullable=False),
    Column("rejection_reasons", ARRAY(_enum("rejection_reason")), nullable=False),
    Column("normalized", JSONB),
    Column("checks_run", JSONB, nullable=False),
    Column("validator_version", Text, nullable=False),
    Column("validator_hash", String(64), nullable=False),
    Column("created_at", TS, nullable=False),
)
policy_decision = Table(
    "policy_decision", metadata,
    Column("decision_id", UUID(as_uuid=True), primary_key=True),
    Column("proposal_id", UUID(as_uuid=True)),
    Column("validation_id", UUID(as_uuid=True)),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("business_date", Date, nullable=False),
    Column("invoice_id", UUID(as_uuid=True), nullable=False),
    Column("arm", _enum("arm"), nullable=False),
    Column("verdict", _enum("verdict"), nullable=False),
    Column("tier", SmallInteger, nullable=False),
    Column("action_type", _enum("action_type")),
    Column("canonical_payload", JSONB),
    Column("defer_until", TS),
    Column("matched_rules", ARRAY(Text), nullable=False),
    Column("blocking_rules", JSONB, nullable=False),
    Column("effective_confidence", Numeric(4, 3)),
    Column("policy_version", Text, nullable=False),
    Column("kernel_version", Text, nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("degradation_level", _enum("degradation_level"), nullable=False),
    Column("decided_at", TS, nullable=False),
)
recovery_action = Table(
    "recovery_action", metadata,
    Column("action_id", UUID(as_uuid=True), primary_key=True),
    Column("decision_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("invoice_id", UUID(as_uuid=True), nullable=False),
    Column("arm", _enum("arm"), nullable=False),
    Column("action_type", _enum("action_type"), nullable=False),
    Column("state", _enum("action_state"), nullable=False),
    Column("idempotency_key", String(64), nullable=False),
    Column("attempt_count", SmallInteger, nullable=False),
    Column("max_attempts", SmallInteger, nullable=False),
    Column("next_attempt_at", TS),
    Column("expires_at", TS, nullable=False),
    Column("approved_by_role", Text),
    Column("approved_by_note", Text),
    Column("approved_at", TS),
    Column("provider_ref", Text),
    Column("last_error_code", Text),
    Column("executed_at", TS),
    Column("confirmed_at", TS),
    Column("created_at", TS, nullable=False),
    Column("updated_at", TS, nullable=False),
)
outbox = Table(
    "outbox", metadata,
    Column("outbox_id", UUID(as_uuid=True), primary_key=True),
    Column("action_id", UUID(as_uuid=True), nullable=False),
    Column("claimed_at", TS),
    Column("claimed_by", Text),
    Column("lease_expires_at", TS),
    Column("created_at", TS, nullable=False),
)
v_invoice_outstanding = Table(
    "v_invoice_outstanding", metadata,
    Column("invoice_id", UUID(as_uuid=True)),
    Column("outstanding_paise", BigInteger),
)

P1_TABLES: tuple[Table, ...] = (
    organization, account, contact, template_registry, provider_secret, invoice, webhook_event,
    sweep_run, payment_event, ledger_entry, agent_proposal, validation_result, policy_decision,
    recovery_action, outbox,
)
