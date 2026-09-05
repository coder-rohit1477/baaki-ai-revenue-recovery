"""Phase 4 (partial): W15 approve_recovery_action and W16 reject_recovery_action — human-only, ops-only.

The kernel already parks a tier-2 action at PENDING_APPROVAL and deliberately withholds its outbox row
(W10, §6.6: `IF v_state = 'QUEUED' THEN INSERT INTO baaki.outbox ...`). Until now nothing could move it,
because §6.4A grants no role UPDATE on `recovery_action` — which is also what stops the model approving its
own proposal. These two writers open exactly that seam and nothing wider.

Shape follows W12 (`opt_out_by_operator`), the existing human-only precedent: SECURITY DEFINER with a pinned
search_path, an H17 `session_user` assertion that is independent of the function grant, a mandatory actor
note, and `SELECT ... FOR UPDATE` so the state check and the write are one atomic step.

What these writers deliberately cannot do:
  * create an action, or change its `action_type`, `invoice_id`, `amount` or decision linkage;
  * move an action out of any state other than PENDING_APPROVAL, so terminal states stay terminal;
  * touch the ledger, an invoice, a payment or a balance.
Approval authorises an already-validated deterministic proposal. It is not a way to author one.

Revision ID: 0007
Revises: 0006
"""

from alembic import op


def _sql(statement: str) -> None:
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

HDR = "LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path = baaki, pg_catalog AS $$"

# W15 — approve. PENDING_APPROVAL -> QUEUED, and the outbox row W10 withheld is created now.
# `APPROVED` is not a member of baaki.action_state: in this architecture approval *is* becoming queued,
# and the approval itself is recorded in approved_by_role / approved_at / approved_by_note.
W15 = f"""
CREATE FUNCTION baaki_write.approve_recovery_action(p_action_id uuid, p_actor_note text, p_outbox_id uuid)
RETURNS text
{HDR}
DECLARE v_a baaki.recovery_action%ROWTYPE;
BEGIN
  -- H17: authority is the connection role, verified here independently of the function grant.
  IF session_user <> 'baaki_ops' THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized_invoker'; END IF;
  -- FOR UPDATE makes the state check and the transition one atomic step: a second concurrent approval
  -- blocks here, then sees QUEUED and is refused. Exactly one approval can ever succeed.
  SELECT * INTO v_a FROM baaki.recovery_action WHERE action_id = p_action_id FOR UPDATE;
  IF v_a.action_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'action_not_found'; END IF;
  IF v_a.state <> 'PENDING_APPROVAL' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'not_pending_approval',
      DETAIL = 'state is ' || v_a.state::text; END IF;
  IF v_a.expires_at <= pg_catalog.now() THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'action_expired'; END IF;
  UPDATE baaki.recovery_action
     SET state = 'QUEUED', approved_by_role = session_user::text, approved_by_note = p_actor_note,
         approved_at = pg_catalog.now(), updated_at = pg_catalog.now()
   WHERE action_id = p_action_id;
  -- The outbox row W10 withheld for a pending action. Nothing here dispatches it; a queued row is work
  -- waiting for an executor, which is Phase 4 proper.
  INSERT INTO baaki.outbox (outbox_id, action_id) VALUES (p_outbox_id, p_action_id)
  ON CONFLICT (action_id) DO NOTHING;
  RETURN 'QUEUED';
END $$"""

# W16 — reject. PENDING_APPROVAL -> APPROVAL_REJECTED. No outbox row, so nothing can ever be delivered.
W16 = f"""
CREATE FUNCTION baaki_write.reject_recovery_action(p_action_id uuid, p_actor_note text)
RETURNS text
{HDR}
DECLARE v_a baaki.recovery_action%ROWTYPE;
BEGIN
  IF session_user <> 'baaki_ops' THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized_invoker'; END IF;
  IF p_actor_note IS NULL OR pg_catalog.length(pg_catalog.btrim(p_actor_note)) = 0 THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'actor_note_required'; END IF;
  SELECT * INTO v_a FROM baaki.recovery_action WHERE action_id = p_action_id FOR UPDATE;
  IF v_a.action_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'action_not_found'; END IF;
  IF v_a.state <> 'PENDING_APPROVAL' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'not_pending_approval',
      DETAIL = 'state is ' || v_a.state::text; END IF;
  UPDATE baaki.recovery_action
     SET state = 'APPROVAL_REJECTED', approved_by_role = session_user::text, approved_by_note = p_actor_note,
         approved_at = pg_catalog.now(), updated_at = pg_catalog.now()
   WHERE action_id = p_action_id;
  RETURN 'APPROVAL_REJECTED';
END $$"""

APPROVE_SIG = "baaki_write.approve_recovery_action(uuid, text, uuid)"
REJECT_SIG = "baaki_write.reject_recovery_action(uuid, text)"


def upgrade() -> None:
    _sql(W15)
    _sql(W16)
    for sig in (APPROVE_SIG, REJECT_SIG):
        _sql(f"REVOKE EXECUTE ON FUNCTION {sig} FROM PUBLIC")
        for role in ("baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"):
            _sql(f"REVOKE EXECUTE ON FUNCTION {sig} FROM {role}")
        # Operator authority only. baaki_app — the role the recovery pipeline runs as — is deliberately
        # excluded, so the automated path cannot approve what it proposed.
        _sql(f"GRANT EXECUTE ON FUNCTION {sig} TO baaki_ops")


def downgrade() -> None:
    _sql(f"DROP FUNCTION IF EXISTS {REJECT_SIG}")
    _sql(f"DROP FUNCTION IF EXISTS {APPROVE_SIG}")
