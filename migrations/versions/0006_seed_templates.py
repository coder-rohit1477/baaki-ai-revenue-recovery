"""Phase 2: template registry seed v1 (ARCHITECTURE.md §6.14, P2-D10). body_hash = sha256(config/templates/<id>.txt).

Revision ID: 0006
Revises: 0005
"""

import hashlib
from pathlib import Path

from alembic import op


def _sql(statement: str) -> None:
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TEMPLATES = (
    ("tpl.reminder.email.v1", "EMAIL", "SEND_REMINDER", "REMINDER"),
    ("tpl.reminder.sms.v1", "SMS", "SEND_REMINDER", "REMINDER"),
    ("tpl.nudge.email.v1", "EMAIL", "SEND_REMINDER", "COURTESY_NUDGE"),
    ("tpl.link.email.v1", "EMAIL", "SEND_PAYMENT_LINK", "PAYMENT_LINK"),
    ("tpl.dispute.email.v1", "EMAIL", "REQUEST_DISPUTE_DETAILS", "DISPUTE_DETAILS_REQUEST"),
    ("tpl.installment.email.v1", "EMAIL", "PROPOSE_INSTALLMENT_PLAN", "INSTALLMENT_PROPOSAL"),
)
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "config" / "templates"


def upgrade() -> None:
    for tid, channel, action, purpose in TEMPLATES:
        body = (TEMPLATE_DIR / f"{tid}.txt").read_bytes()
        h = hashlib.sha256(body).hexdigest()
        _sql(
            "INSERT INTO baaki.template_registry (template_id, channel, action_type, purpose, active, version, body_hash) "
            f"VALUES ('{tid}', '{channel}', '{action}', '{purpose}', true, 1, '{h}') ON CONFLICT (template_id) DO NOTHING"
        )


def downgrade() -> None:
    ids = ", ".join(f"'{t[0]}'" for t in TEMPLATES)
    _sql(f"DELETE FROM baaki.template_registry WHERE template_id IN ({ids})")
