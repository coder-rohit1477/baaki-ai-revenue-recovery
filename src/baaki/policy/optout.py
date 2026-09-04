"""Inbound opt-out (W11) — evidence-gated, deterministic, arm-independent (ARCHITECTURE.md §6.18).

Opt-out semantics: a contact opt-out is established only from a validator ``PASS`` whose normalized intent is
``UNSUBSCRIBE``; it is monotonic and the model never mutates it. It is not a policy verdict, so it does not pass
through the kernel ladder.

W11 authorization: ``baaki_write.opt_out_contact_from_evidence`` executes as ``baaki_app`` and re-verifies the
``validation_id`` (exists, ``PASS``, intent ``UNSUBSCRIBE``, contact ∈ validation.account) inside the writer.

Kill switch: validator check 01 rejects every new proposal with ``SYSTEM_HALTED`` while the kill switch is on, so no
``PASS`` validation exists and this W11 path cannot execute from a kill-switched proposal. The kill-switch-independent
route is the arm-independent restriction path (detector in ``rules_agent.restriction``; W11b in Phase 4).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection

from baaki.contracts.validation_result import NormalizedInterpretation, ValidationResult
from baaki.db.writers.optout_evidence import opt_out_contact_from_evidence
from baaki.domain.enums import ValidationOutcome


def apply_inbound_opt_out(conn: Connection, validation: ValidationResult, *, contact_id: UUID | None) -> bool:
    """If the validation is a PASS UNSUBSCRIBE and a contact is identified, record the opt-out via W11."""
    if validation.outcome is not ValidationOutcome.PASS or not isinstance(
        validation.normalized, NormalizedInterpretation
    ):
        return False
    if validation.normalized.intent != "UNSUBSCRIBE":
        return False
    cid = validation.normalized.contact_id or contact_id
    if cid is None:
        return False
    return opt_out_contact_from_evidence(conn, contact_id=cid, validation_id=validation.validation_id)
