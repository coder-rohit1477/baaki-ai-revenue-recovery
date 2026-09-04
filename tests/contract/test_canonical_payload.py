import pytest
from pydantic import TypeAdapter

from baaki.contracts.canonical_payload import (
    CanonicalPayload,
    EscalateToHumanPayload,
    LinkNotes,
    SendPaymentLinkPayload,
    SuppressPayload,
    TemplateId,
)
from baaki.domain.enums import AssigneeQueue, Channel, EscalationReason
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from baaki.domain.money import Paise
from tests.helpers import NOW


def test_discriminator_and_closure():
    ta = TypeAdapter(CanonicalPayload)
    p = ta.validate_python({"action_type": "SUPPRESS", "reason_code": "PTP_ACTIVE"}, strict=False)
    assert isinstance(p, SuppressPayload)
    with pytest.raises(Exception):
        ta.validate_python({"action_type": "REFUND"}, strict=False)
    with pytest.raises(Exception):
        ta.validate_python({"action_type": "SUPPRESS", "reason_code": "QUIET_HOURS"}, strict=False)   # rejected candidate, not a member


def test_cp1_money_is_int_and_positive():
    with pytest.raises(Exception):
        SendPaymentLinkPayload(amount_paise=12.5, contact_id=new_id(), channel=Channel.EMAIL, template_id=TemplateId("t"), expires_at=NOW,  # type: ignore[arg-type]
                               notes=LinkNotes(invoice_id=new_id(), action_id=new_id(), trace_id=new_id()))
    with pytest.raises(ContractViolation):
        SendPaymentLinkPayload(amount_paise=Paise(0), contact_id=new_id(), channel=Channel.EMAIL, template_id=TemplateId("t"), expires_at=NOW,
                               notes=LinkNotes(invoice_id=new_id(), action_id=new_id(), trace_id=new_id()))


def test_gap1_queue_is_function_of_reason():
    for reason in EscalationReason:
        expected = AssigneeQueue.DISPUTES if reason is EscalationReason.DISPUTE_UNRESOLVED else AssigneeQueue.COLLECTIONS
        EscalateToHumanPayload(reason_code=reason, assignee_queue=expected)
        other = AssigneeQueue.COLLECTIONS if expected is AssigneeQueue.DISPUTES else AssigneeQueue.DISPUTES
        with pytest.raises(ContractViolation):
            EscalateToHumanPayload(reason_code=reason, assignee_queue=other)


def test_cp3_no_free_text_fields():
    for cls in (SuppressPayload, EscalateToHumanPayload):
        for name, field in cls.model_fields.items():
            assert field.annotation is not str, (cls, name)
