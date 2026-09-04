"""D-2b2-5 (LOCKED) — the independent ENR reference, incl. the three-way kal/parso rule."""
from datetime import date

import pytest
from eval.enr import normalize_amount, normalize_date, validator_flags

ANCHOR = date(2026, 9, 1)  # Tuesday


@pytest.mark.parametrize("span,clause,expected", [
    ("kal", "kal kar denge", date(2026, 9, 2)),
    ("kal", "payment kal tak ho jayega", date(2026, 9, 2)),
    ("parso", "parso bhejenge", date(2026, 9, 3)),
    ("kal", "kal subah pay karenge", date(2026, 9, 2)),
])
def test_kal_parso_future_marked_yield_future_anchor(span, clause, expected):
    r = normalize_date(span, ANCHOR, clause=clause)
    assert (r.status, r.value) == ("value", expected) and r.rule.startswith("ENR-2")


@pytest.mark.parametrize("span,clause", [("kal", "kal kar diya"), ("kal", "kal hi paid"), ("parso", "parso bhej diya tha")])
def test_kal_parso_past_marked_are_not_ptp_dates(span, clause):
    r = normalize_date(span, ANCHOR, clause=clause)
    assert r.status == "abstain" and "past" in r.rule


@pytest.mark.parametrize("span,clause", [("kal", "kal"), ("kal", "kal ka message"), ("kal", "kal kiya tha, kal karenge"), ("parso", "parso")])
def test_kal_parso_bare_or_conflicting_abstain(span, clause):
    r = normalize_date(span, ANCHOR, clause=clause)
    assert r.status == "abstain" and "bare/conflicting" in r.rule


@pytest.mark.parametrize("span,expected,rule", [
    ("today", date(2026, 9, 1), "ENR-1"), ("aaj", date(2026, 9, 1), "ENR-1"), ("tomorrow", date(2026, 9, 2), "ENR-2"),
    ("Friday", date(2026, 9, 4), "ENR-3"), ("Tuesday", date(2026, 9, 8), "ENR-3"), ("by Monday", date(2026, 9, 7), "ENR-3"),
    ("shukravar", date(2026, 9, 4), "ENR-3"), ("15/09/2026", date(2026, 9, 15), "ENR-4"), ("2026-09-15", date(2026, 9, 15), "ENR-5"),
    ("end of month", date(2026, 9, 30), "ENR-6"), ("5th September", date(2026, 9, 5), "ENR-7"), ("1 Jan", date(2027, 1, 1), "ENR-7"),
    ("31 August", date(2027, 8, 31), "ENR-7"),
])
def test_enr_1_to_7(span, expected, rule):
    r = normalize_date(span, ANCHOR)
    assert (r.status, r.value) == ("value", expected) and r.rule.startswith(rule)


@pytest.mark.parametrize("span", ["next week", "soon", "in a few days", "Friday or Monday", "15/09/2026 or 20/09/2026", "agle hafte"])
def test_enr_10_ambiguous_dates_abstain(span):
    assert normalize_date(span, ANCHOR).status == "abstain"


@pytest.mark.parametrize("span", ["", "Blursday", "32/13/2026", "2026-02-30"])
def test_unparseable_dates(span):
    assert normalize_date(span, ANCHOR).status == "unparseable"


def test_past_and_horizon_are_validator_flags_not_extraction_errors():
    assert normalize_date("2026-08-01", ANCHOR).status == "value" and validator_flags(date(2026, 8, 1), ANCHOR) == ["DATE_IN_PAST"]
    assert validator_flags(date(2026, 12, 25), ANCHOR) == ["DATE_BEYOND_HORIZON"] and validator_flags(date(2026, 9, 20), ANCHOR) == []
    assert validator_flags(None, ANCHOR) == []


@pytest.mark.parametrize("span,paise", [
    ("15000", 1_500_000), ("15k", 1_500_000), ("15,000", 1_500_000), ("₹4,500", 450_000), ("Rs. 4500", 450_000), ("Rs 4500/-", 450_000),
    ("INR 4,500", 450_000), ("4500 rupees", 450_000), ("1.5 lakh", 15_000_000), ("2 lakhs", 20_000_000), ("1.5 lac", 15_000_000),
    ("2 crore", 2_000_000_000), ("5 hazaar", 500_000), ("₹1.5", 150),
])
def test_enr_11_12_amounts(span, paise):
    r = normalize_amount(span)
    assert (r.status, r.paise) == ("value", paise)


@pytest.mark.parametrize("span", ["half", "some", "the rest", "balance", "1.5", "partial", "aadha", "4-5k", "4000 or 5000", "4000 to 5000", "4 ya 5 hazaar"])
def test_enr_13_14_ambiguous_and_ranges_abstain(span):
    assert normalize_amount(span).status == "abstain"


def test_enr_module_is_independent_of_the_production_grammar():
    import eval.enr as enr
    src = open(enr.__file__).read()
    assert "baaki." not in src  # zero production imports
