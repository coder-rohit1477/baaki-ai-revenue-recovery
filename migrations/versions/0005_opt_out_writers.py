"""Phase 2: W11 opt_out_contact_from_evidence (baaki_app) and W12 opt_out_by_operator (baaki_ops, H17).

ARCHITECTURE.md §6.6, §6.18, §6.22, H1–H19. W12 is the first human-only writer: it asserts session_user.

Revision ID: 0005
Revises: 0004
"""

from alembic import op


def _sql(statement: str) -> None:
    with op.get_bind().connection.cursor() as cur:
        cur.execute(statement)


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

HDR = "LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path = baaki, pg_catalog AS $$"

W11 = f"""
CREATE FUNCTION baaki_write.opt_out_contact_from_evidence(p_contact_id uuid, p_validation_id uuid) RETURNS boolean
{HDR}
DECLARE v_v baaki.validation_result%ROWTYPE; v_c baaki.contact%ROWTYPE;
BEGIN
  SELECT * INTO v_v FROM baaki.validation_result WHERE validation_id = p_validation_id;
  IF v_v.validation_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'validation_not_found'; END IF;
  IF v_v.outcome <> 'PASS' THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'validation_not_pass'; END IF;
  IF (v_v.normalized ->> 'intent') IS DISTINCT FROM 'UNSUBSCRIBE' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'intent_not_unsubscribe'; END IF;
  SELECT * INTO v_c FROM baaki.contact WHERE contact_id = p_contact_id FOR UPDATE;
  IF v_c.contact_id IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'contact_not_found'; END IF;
  IF v_c.account_id <> v_v.account_id THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'contact_not_in_account'; END IF;
  IF (v_v.normalized ->> 'contact_id') IS NOT NULL AND (v_v.normalized ->> 'contact_id')::uuid <> p_contact_id THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'contact_mismatch'; END IF;
  IF v_c.opted_out THEN RETURN false; END IF;  -- idempotent, monotonic
  UPDATE baaki.contact SET opted_out = true, opted_out_by_role = session_user::text, opted_out_source = 'INBOUND_UNSUBSCRIBE',
         opted_out_validation_id = p_validation_id, opted_out_at = pg_catalog.now()
   WHERE contact_id = p_contact_id;
  RETURN true;
END $$"""

W12 = f"""
CREATE FUNCTION baaki_write.opt_out_by_operator(p_account_id uuid, p_contact_id uuid, p_actor_note text) RETURNS boolean
{HDR}
DECLARE v_changed boolean := false; v_cur boolean;
BEGIN
  -- H17: authority is the connection role, verified here independently of the function grant.
  IF session_user <> 'baaki_ops' THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'unauthorized_invoker'; END IF;
  IF (p_account_id IS NULL) = (p_contact_id IS NULL) THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'exactly_one_target_required'; END IF;
  IF p_actor_note IS NULL OR pg_catalog.length(pg_catalog.btrim(p_actor_note)) = 0 THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'actor_note_required'; END IF;
  IF p_account_id IS NOT NULL THEN
    SELECT opt_out INTO v_cur FROM baaki.account WHERE account_id = p_account_id FOR UPDATE;
    IF v_cur IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'account_not_found'; END IF;
    IF NOT v_cur THEN
      UPDATE baaki.account SET opt_out = true, opt_out_by_role = session_user::text, opt_out_source = 'HUMAN',
             opt_out_note = p_actor_note, opt_out_at = pg_catalog.now() WHERE account_id = p_account_id;
      v_changed := true;
    END IF;
  ELSE
    SELECT opted_out INTO v_cur FROM baaki.contact WHERE contact_id = p_contact_id FOR UPDATE;
    IF v_cur IS NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'contact_not_found'; END IF;
    IF NOT v_cur THEN
      UPDATE baaki.contact SET opted_out = true, opted_out_by_role = session_user::text, opted_out_source = 'HUMAN',
             opted_out_note = p_actor_note, opted_out_at = pg_catalog.now() WHERE contact_id = p_contact_id;
      v_changed := true;
    END IF;
  END IF;
  RETURN v_changed;
END $$"""


def upgrade() -> None:
    _sql(W11)
    _sql("REVOKE EXECUTE ON FUNCTION baaki_write.opt_out_contact_from_evidence(uuid, uuid) FROM PUBLIC")
    _sql(W12)
    _sql("REVOKE EXECUTE ON FUNCTION baaki_write.opt_out_by_operator(uuid, uuid, text) FROM PUBLIC")
    for role in ("baaki_app", "baaki_ops", "baaki_agent", "baaki_sim"):
        _sql(f"REVOKE EXECUTE ON FUNCTION baaki_write.opt_out_contact_from_evidence(uuid, uuid) FROM {role}")
        _sql(f"REVOKE EXECUTE ON FUNCTION baaki_write.opt_out_by_operator(uuid, uuid, text) FROM {role}")
    _sql("GRANT EXECUTE ON FUNCTION baaki_write.opt_out_contact_from_evidence(uuid, uuid) TO baaki_app")
    _sql("GRANT EXECUTE ON FUNCTION baaki_write.opt_out_by_operator(uuid, uuid, text) TO baaki_ops")


def downgrade() -> None:
    _sql("DROP FUNCTION IF EXISTS baaki_write.opt_out_by_operator(uuid, uuid, text)")
    _sql("DROP FUNCTION IF EXISTS baaki_write.opt_out_contact_from_evidence(uuid, uuid)")
