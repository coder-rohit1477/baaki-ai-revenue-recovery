"""Phase 2: opt_out_source enum + opt-out metadata columns with OO1–OO3 CHECKs (ARCHITECTURE.md §6.4B, §6.18).

Revision ID: 0004
Revises: 0003
"""

from alembic import op


def _sql(statement: str) -> None:
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _sql("CREATE TYPE baaki.opt_out_source AS ENUM ('INBOUND_UNSUBSCRIBE', 'INBOUND_RESTRICTION', 'HUMAN')")
    _sql("""
    ALTER TABLE baaki.contact
      ADD COLUMN opted_out_by_role text,
      ADD COLUMN opted_out_source baaki.opt_out_source,
      ADD COLUMN opted_out_note text,
      ADD COLUMN opted_out_validation_id uuid REFERENCES baaki.validation_result(validation_id),
      ADD COLUMN opted_out_at timestamptz,
      ADD CONSTRAINT ck_contact_optout_oo1 CHECK (opted_out = (opted_out_at IS NOT NULL)),
      ADD CONSTRAINT ck_contact_optout_source CHECK ((opted_out_source IS NULL) = (NOT opted_out)),
      ADD CONSTRAINT ck_contact_optout_role CHECK ((opted_out_by_role IS NULL) = (NOT opted_out)),
      ADD CONSTRAINT ck_contact_optout_oo2 CHECK (opted_out_source IS DISTINCT FROM 'INBOUND_UNSUBSCRIBE'
            OR (opted_out_validation_id IS NOT NULL AND opted_out_by_role = 'baaki_app')),
      ADD CONSTRAINT ck_contact_optout_oo3 CHECK (opted_out_source IS DISTINCT FROM 'HUMAN' OR opted_out_by_role = 'baaki_ops'),
      ADD CONSTRAINT ck_contact_optout_restriction CHECK (opted_out_source IS DISTINCT FROM 'INBOUND_RESTRICTION' OR opted_out_by_role = 'baaki_app')
    """)
    _sql("CREATE INDEX ix_contact_optout_validation ON baaki.contact (opted_out_validation_id)")
    _sql("""
    ALTER TABLE baaki.account
      ADD COLUMN opt_out_by_role text,
      ADD COLUMN opt_out_source baaki.opt_out_source,
      ADD COLUMN opt_out_note text,
      ADD COLUMN opt_out_at timestamptz,
      ADD CONSTRAINT ck_account_optout_oo1 CHECK (opt_out = (opt_out_at IS NOT NULL)),
      ADD CONSTRAINT ck_account_optout_source CHECK ((opt_out_source IS NULL) = (NOT opt_out)),
      ADD CONSTRAINT ck_account_optout_role CHECK ((opt_out_by_role IS NULL) = (NOT opt_out)),
      ADD CONSTRAINT ck_account_optout_oo3 CHECK (opt_out_source IS DISTINCT FROM 'HUMAN' OR opt_out_by_role = 'baaki_ops'),
      ADD CONSTRAINT ck_account_optout_human_only CHECK (opt_out_source IS NULL OR opt_out_source = 'HUMAN')
    """)


def downgrade() -> None:
    _sql(
        "ALTER TABLE baaki.account DROP COLUMN opt_out_by_role, DROP COLUMN opt_out_source, DROP COLUMN opt_out_note, DROP COLUMN opt_out_at"
    )
    _sql("DROP INDEX IF EXISTS baaki.ix_contact_optout_validation")
    _sql(
        "ALTER TABLE baaki.contact DROP COLUMN opted_out_by_role, DROP COLUMN opted_out_source, DROP COLUMN opted_out_note, "
        "DROP COLUMN opted_out_validation_id, DROP COLUMN opted_out_at"
    )
    _sql("DROP TYPE baaki.opt_out_source")
