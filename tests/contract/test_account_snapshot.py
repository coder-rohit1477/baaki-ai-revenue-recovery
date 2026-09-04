import pytest

from baaki.contracts.account_snapshot import AccountSnapshot
from baaki.domain.enums import InvoiceState
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from baaki.domain.money import Paise
from tests.helpers import NOW, TODAY


def _fields():
    inv = new_id()
    return dict(as_of=NOW, business_date=TODAY, account_id=new_id(), candidate_invoice_ids=[inv], target_invoice_id=inv,
                outstanding_paise=Paise(1000), invoice_state=InvoiceState.OVERDUE, days_overdue=12, opt_out=False, kill_switch=False,
                ledger_invariant_ok=True, contacts_7d=1, contacts_invoice_7d=1, contactable_contact_ids=[new_id()], template_catalogue=[])


def test_s3_hash_covers_fields():
    s = AccountSnapshot.build(**_fields())
    assert len(s.snapshot_hash) == 64
    with pytest.raises(ContractViolation):
        AccountSnapshot(snapshot_hash="0" * 64, **_fields())


def test_sc4_target_must_be_candidate():
    f = _fields(); f["target_invoice_id"] = new_id()
    with pytest.raises(ContractViolation):
        AccountSnapshot.build(**f)
