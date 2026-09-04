"""§4.4 grammars — deterministic; ambiguity is a status, never a guess; never raises."""
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from baaki.policy.validate.normalize import parse_amount, parse_date

ANCHOR = date(2026, 9, 1)  # Tuesday


@pytest.mark.parametrize("raw,expected", [
    ("today", date(2026, 9, 1)), ("tomorrow", date(2026, 9, 2)), ("kal", date(2026, 9, 2)), ("day after tomorrow", date(2026, 9, 3)),
    ("Friday", date(2026, 9, 4)), ("next friday", date(2026, 9, 4)), ("by Tuesday", date(2026, 9, 8)),  # strictly future
    ("in 10 days", date(2026, 9, 11)), ("end of month", date(2026, 9, 30)), ("2026-09-15", date(2026, 9, 15)),
    ("15/09/2026", date(2026, 9, 15)), ("5th September", date(2026, 9, 5)), ("Sept 5", date(2026, 9, 5)), ("1 Jan", date(2027, 1, 1)),
])
def test_dates_ok(raw, expected):
    r = parse_date(raw, ANCHOR)
    assert (r.status, r.value) == ("ok", expected)


@pytest.mark.parametrize("raw", ["next week", "soon", "in a few days", "Friday or Monday", "2026-09-10 or 2026-09-12", "this week"])
def test_dates_ambiguous(raw):
    assert parse_date(raw, ANCHOR).status == "ambiguous"


@pytest.mark.parametrize("raw", ["", "whenever", "32/13/2026", "2026-02-30", "Blursday", "the 45th"])
def test_dates_unparseable(raw):
    assert parse_date(raw, ANCHOR).status == "unparseable"


@pytest.mark.parametrize("raw,paise", [
    ("15000", 1_500_000), ("15k", 1_500_000), ("15,000", 1_500_000), ("₹4,500", 450_000), ("Rs. 4500", 450_000), ("Rs 4500/-", 450_000),
    ("1.5 lakh", 15_000_000), ("2 lakhs", 20_000_000), ("1 crore", 1_000_000_000), ("4500 rupees", 450_000), ("₹1.5", 150), ("INR 250", 25_000),
])
def test_amounts_ok(raw, paise):
    r = parse_amount(raw)
    assert (r.status, int(r.value)) == ("ok", paise)


@pytest.mark.parametrize("raw", ["half", "1.5", "some of it", "the rest", "4000 or 5000", "4000-5000", "partial"])
def test_amounts_ambiguous(raw):
    assert parse_amount(raw).status == "ambiguous"


@pytest.mark.parametrize("raw", ["", "nothing", "0", "abc", "₹"])
def test_amounts_unparseable(raw):
    assert parse_amount(raw).status == "unparseable"


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=40))
def test_grammars_never_raise_and_are_deterministic(raw):
    a, b = parse_date(raw, ANCHOR), parse_date(raw, ANCHOR)
    assert a == b and a.status in ("ok", "ambiguous", "unparseable")
    x, y = parse_amount(raw), parse_amount(raw)
    assert x == y and x.status in ("ok", "ambiguous", "unparseable")
    if x.status == "ok":
        assert int(x.value) > 0
