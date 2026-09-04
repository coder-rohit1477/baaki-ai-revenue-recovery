"""ENR — the evaluation's INDEPENDENT normalization reference (D-2b2-5, LOCKED).

Implements ENR-1…ENR-14 from the plan text. Deliberately written from the rules, not from `policy/validate/normalize.py`
(arch-tested: this module imports no production module). Used to check that authored oracle values obey the locked
rules and, in G2, to explain parser/oracle disagreements. Never used to create labels automatically.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Status = Literal["value", "abstain", "unparseable"]

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
    "somvar": 0,
    "mangalvar": 1,
    "budhvar": 2,
    "guruvar": 3,
    "shukravar": 4,
    "shanivar": 5,
    "ravivar": 6,
}
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_VAGUE = (
    "next week",
    "soon",
    "later",
    "few days",
    "asap",
    "sometime",
    "this week",
    "next month",
    "jaldi",
    "agle hafte",
)
_FUTURE_MARKERS = (
    "karenge",
    "denge",
    "bhejenge",
    "hoga",
    "kar denge",
    "kar dunga",
    "kar doonga",
    "tak",
    "subah",
    "shaam",
    "will",
    "pay",
)
_PAST_MARKERS = ("kiya", "kar diya", "bhej diya", "ho gaya", "diya", "paid", "transferred", "kal hi")


@dataclass(frozen=True)
class EnrDate:
    status: Status
    value: date | None = None
    rule: str = ""


@dataclass(frozen=True)
class EnrAmount:
    status: Status
    paise: int | None = None
    rule: str = ""


def _next_weekday(anchor: date, wd: int) -> date:
    delta = (wd - anchor.weekday()) % 7
    return anchor + timedelta(days=delta or 7)  # ENR-3: strictly after anchor


def normalize_date(span: str, anchor: date, *, clause: str | None = None) -> EnrDate:
    """ENR-1…ENR-10. `clause` is the surrounding clause used only for the kal/parso tense rule (ENR-2)."""
    s = " ".join(span.strip().lower().split())
    ctx = " ".join((clause or span).lower().split())
    if not s:
        return EnrDate("unparseable", rule="ENR-empty")
    if any(v in s for v in _VAGUE):
        return EnrDate("abstain", rule="ENR-10 vague")
    if len(re.findall(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}", s)) > 1 or " or " in s:
        return EnrDate("abstain", rule="ENR-10 two dates")
    if sum(1 for w in _WEEKDAYS if re.search(rf"\b{w}\b", s)) > 1:
        return EnrDate("abstain", rule="ENR-10 two weekdays")
    if s in ("today", "aaj"):
        return EnrDate("value", anchor, "ENR-1")
    if s == "tomorrow":
        return EnrDate("value", anchor + timedelta(days=1), "ENR-2")
    if s in ("kal", "parso"):
        offset = 1 if s == "kal" else 2
        future = any(m in ctx for m in _FUTURE_MARKERS)
        past = any(m in ctx for m in _PAST_MARKERS)
        if future and not past:
            return EnrDate("value", anchor + timedelta(days=offset), "ENR-2 future-marked")
        if past and not future:
            return EnrDate("abstain", rule="ENR-2 past-marked (not a PTP date)")
        return EnrDate("abstain", rule="ENR-2 bare/conflicting")
    if s in ("end of month", "month end", "eom", "end of the month", "mahine ke end"):
        return EnrDate(
            "value", date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1]), "ENR-6"
        )
    m = re.fullmatch(r"(?:next |coming |by |on |this |is )?([a-z]+)", s)
    if m and m.group(1) in _WEEKDAYS:
        return EnrDate("value", _next_weekday(anchor, _WEEKDAYS[m.group(1)]), "ENR-3")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return EnrDate("value", date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "ENR-5")
        except ValueError:
            return EnrDate("unparseable", rule="ENR-5 impossible")
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        try:
            return EnrDate("value", date(int(m.group(3)), int(m.group(2)), int(m.group(1))), "ENR-4 day-first")
        except ValueError:
            return EnrDate("unparseable", rule="ENR-4 impossible")
    m = re.fullmatch(r"(?:by |on )?(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?", s) or re.fullmatch(
        r"(?:by |on )?([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", s
    )
    if m:
        g = m.groups()
        day_s, mon_s = (g[0], g[1]) if g[0].isdigit() else (g[1], g[0])
        if mon_s in _MONTHS:
            year = int(g[2]) if g[2] else anchor.year
            try:
                d = date(year, _MONTHS[mon_s], int(day_s))
            except ValueError:
                return EnrDate("unparseable", rule="ENR-7 impossible")
            if not g[2] and d <= anchor:
                d = date(year + 1, _MONTHS[mon_s], int(day_s))  # ENR-7 year rollover
                return EnrDate("value", d, "ENR-7 rollover")
            return EnrDate("value", d, "ENR-7")
    return EnrDate("unparseable", rule="ENR-none")


_UNITS = {
    None: 1,
    "k": 1_000,
    "thousand": 1_000,
    "hazaar": 1_000,
    "hazar": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "l": 100_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
    "rupees": 1,
    "rs": 1,
    "/-": 1,
    "inr": 1,
}
_VAGUE_AMOUNTS = ("half", "partial", "some", "rest", "balance", "remaining", "most", "part", "aadha", "thoda", "baaki")


def normalize_amount(span: str) -> EnrAmount:
    """ENR-11…ENR-14. Rupees default; integer paise; ambiguity and ranges abstain."""
    s = " ".join(span.strip().lower().replace(",", "").split())
    if not s:
        return EnrAmount("unparseable", rule="ENR-empty")
    if any(w == s or f" {w} " in f" {s} " for w in _VAGUE_AMOUNTS):
        return EnrAmount("abstain", rule="ENR-13 vague")
    if re.search(r"\d\s*(?:-|to|or)\s*\d", s) or " ya " in s:
        return EnrAmount("abstain", rule="ENR-14 range")
    m = re.fullmatch(
        r"(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(k|thousand|hazaar|hazar|lakhs?|lac|l|crore|cr|rupees|rs|/-|inr)?", s
    )
    if not m:
        return EnrAmount("unparseable", rule="ENR-none")
    num, unit = m.group(1), m.group(2)
    if "." in num and unit is None and not re.match(r"(rs\.?|inr|₹)", s):
        return EnrAmount("abstain", rule="ENR-13 bare decimal")
    paise = round(float(num) * _UNITS[unit] * 100)
    if paise <= 0:
        return EnrAmount("unparseable", rule="ENR-11 non-positive")
    return EnrAmount("value", int(paise), "ENR-11/12")


def validator_flags(expected: date | None, anchor: date, *, horizon_days: int = 30) -> list[str]:
    """Validator POLICY flags implied by an extracted date (not extraction correctness)."""
    if expected is None:
        return []
    if expected <= anchor:
        return ["DATE_IN_PAST"]
    if expected > anchor + timedelta(days=horizon_days):
        return ["DATE_BEYOND_HORIZON"]
    return []
