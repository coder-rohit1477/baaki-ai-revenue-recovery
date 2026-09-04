from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from baaki.domain.money import Paise, add_paise, claimed_paise, min_paise, paise, positive_paise, sub_paise


@pytest.mark.parametrize("bad", [1.5, True, "100", Decimal("1"), None])
def test_rejects_non_int(bad):
    with pytest.raises((TypeError, ValueError)):
        paise(bad)


def test_ranges():
    assert paise(0) == 0
    with pytest.raises(ValueError):
        paise(-1)
    with pytest.raises(ValueError):
        positive_paise(0)
    with pytest.raises(ValueError):
        sub_paise(Paise(1), Paise(2))
    assert claimed_paise(5) == 5


@given(st.integers(0, 10**12), st.integers(0, 10**12))
def test_arithmetic_closed_over_int(a, b):
    r = add_paise(Paise(a), Paise(b))
    assert isinstance(r, int) and r == a + b
    assert min_paise(Paise(a), Paise(b)) == min(a, b)
    if a >= b:
        assert sub_paise(Paise(a), Paise(b)) == a - b
