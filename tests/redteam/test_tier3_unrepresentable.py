"""J. F1–F7 absent from every surface (§6.15)."""
import inspect

import pytest
from sqlalchemy import text

from baaki.contracts import canonical_payload as cp
from baaki.domain.enums import FORBIDDEN_CAPABILITIES, ActionType, InvoiceState, LedgerSource

VERBS = sorted(FORBIDDEN_CAPABILITIES)


@pytest.mark.parametrize("verb", VERBS)
def test_absent_from_python_enums_and_payloads(verb):
    with pytest.raises(ValueError):
        ActionType(verb)
    assert verb not in {m.value for m in InvoiceState} | {m.value for m in LedgerSource}
    src = inspect.getsource(cp)
    assert verb not in src


@pytest.mark.parametrize("verb", VERBS)
def test_absent_from_postgres(su, verb):
    labels = {r[0] for r in su.execute(text(
        "select e.enumlabel from pg_enum e join pg_type t on t.oid=e.enumtypid join pg_namespace n on n.oid=t.typnamespace where n.nspname='baaki'"))}
    assert verb not in labels
    fns = {r[0].upper() for r in su.execute(text("select proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='baaki_write'"))}
    assert not any(verb in f for f in fns)
    with pytest.raises(Exception):
        su.execute(text(f"select '{verb}'::baaki.action_type"))
    su.rollback()


def test_no_reversal_or_correction_capability(su):
    labels = {r[0] for r in su.execute(text("select unnest(enum_range(null::baaki.ledger_source))::text"))}
    assert labels == {"ISSUANCE", "PAYMENT", "REATTRIBUTION"}
    states = {r[0] for r in su.execute(text("select unnest(enum_range(null::baaki.invoice_state))::text"))}
    assert states == {"OPEN", "DUE", "OVERDUE", "DISPUTED", "PAID"}
    codes_ok = su.execute(text("select 'FEES' ~ '^(AR|BUYER_CREDIT):[0-9a-f-]{36}$' or 'FEES' in ('SALES','CASH_CLEARING','UNAPPLIED_CASH')")).scalar_one()
    assert codes_ok is False
