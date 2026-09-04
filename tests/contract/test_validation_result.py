import pytest

from baaki.contracts.validation_result import NormalizedInterpretation, ValidationResult
from baaki.domain.enums import RejectionReason, ValidationOutcome
from baaki.domain.errors import ContractViolation
from baaki.domain.ids import new_id
from baaki.domain.money import Paise, claim_within, claimed_paise
from tests.helpers import H64, NOW, TODAY


def _v(**kw):
    base = dict(validation_id=new_id(), proposal_id=new_id(), trace_id=new_id(), account_id=new_id(), business_date=TODAY,
                outcome=ValidationOutcome.PASS, rejection_reasons=[], normalized=NormalizedInterpretation(intent="WILL_PAY_ON_DATE", effective_confidence=0.8),
                checks_run=[], validator_version="v", validator_hash=H64, created_at=NOW)
    base.update(kw)
    return ValidationResult(**base)


def test_v2_v3():
    with pytest.raises(ContractViolation):
        _v(outcome=ValidationOutcome.PASS, rejection_reasons=[RejectionReason.DATE_AMBIGUOUS])
    with pytest.raises(ContractViolation):
        _v(outcome=ValidationOutcome.REJECT, rejection_reasons=[], normalized=None)
    with pytest.raises(ContractViolation):
        _v(outcome=ValidationOutcome.REJECT, rejection_reasons=[RejectionReason.DATE_AMBIGUOUS])   # normalized must be None
    _v(outcome=ValidationOutcome.REJECT, rejection_reasons=[RejectionReason.DATE_AMBIGUOUS], normalized=None)


def test_v7_claimed_paise_is_not_authority():
    n = NormalizedInterpretation(intent="WILL_PAY_ON_DATE", promised_paise=claimed_paise(1_500_000), effective_confidence=0.9)
    assert claim_within(n.promised_paise, Paise(4_500_000)) is True
    # The only sanctioned interaction is comparison; there is no conversion function.
    import baaki.domain.money as money
    assert not any(name.startswith("to_paise") or name == "as_paise" for name in dir(money))
    with pytest.raises(ValueError):
        claimed_paise(0)
