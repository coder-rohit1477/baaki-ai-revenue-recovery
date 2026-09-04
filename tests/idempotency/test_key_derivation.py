from datetime import date, timedelta
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from baaki.db.idempotency import idempotency_key
from baaki.domain.enums import ActionType, Arm

uuids = st.uuids()
hashes = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
dates = st.dates(min_value=date(2025, 1, 1), max_value=date(2027, 12, 31))


@settings(max_examples=300)
@given(uuids, st.sampled_from(list(ActionType)), hashes, dates, st.sampled_from(list(Arm)), st.integers(0, 50))
def test_stable_across_attempts_and_time(inv, at, h, d, arm, attempts):
    k1 = idempotency_key(inv, at, h, d, arm)
    k2 = idempotency_key(inv, at, h, d, arm)
    assert k1 == k2 and len(k1) == 64 and set(k1) <= set("0123456789abcdef")


@settings(max_examples=200)
@given(uuids, st.sampled_from(list(ActionType)), hashes, dates, st.sampled_from(list(Arm)))
def test_each_input_changes_key(inv, at, h, d, arm):
    base = idempotency_key(inv, at, h, d, arm)
    assert idempotency_key(UUID(int=inv.int ^ 1), at, h, d, arm) != base
    other_at = next(a for a in ActionType if a is not at)
    assert idempotency_key(inv, other_at, h, d, arm) != base
    assert idempotency_key(inv, at, ("f" if h[0] != "f" else "0") + h[1:], d, arm) != base
    assert idempotency_key(inv, at, h, d + timedelta(days=1), arm) != base
    other_arm = next(a for a in Arm if a is not arm)
    assert idempotency_key(inv, at, h, d, other_arm) != base
