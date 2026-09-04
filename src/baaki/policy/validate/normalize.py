"""Deterministic date and amount grammars (ARCHITECTURE.md §4.4). Ambiguity is rejection — never a guess."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from baaki.domain.money import ClaimedPaise, claimed_paise

DateResult = Literal["ok", "unparseable", "ambiguous"]
AmountResult = Literal["ok", "unparseable", "ambiguous"]

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
}
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_AMBIGUOUS_DATE = ("next week", "soon", "later", "few days", "asap", "sometime", "this week", "next month")
_AMBIGUOUS_AMOUNT = ("half", "partial", "some", "rest", "balance", "remaining", "most", "part")


@dataclass(frozen=True)
class DateParse:
    status: DateResult
    value: date | None = None


@dataclass(frozen=True)
class AmountParse:
    status: AmountResult
    value: ClaimedPaise | None = None


def _next_weekday(anchor: date, wd: int) -> date:
    delta = (wd - anchor.weekday()) % 7
    return anchor + timedelta(days=delta or 7)  # strictly future


def parse_date(raw: str, anchor: date) -> DateParse:
    """Resolve a verbatim span to a date relative to `anchor` (message business date). Closed grammar."""
    s = " ".join(raw.strip().lower().split())
    if not s:
        return DateParse("unparseable")
    if any(a in s for a in _AMBIGUOUS_DATE):
        return DateParse("ambiguous")
    # two explicit dates / two weekdays in one span => ambiguous
    if (
        len(re.findall(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}", s)) > 1
        or sum(1 for w in _WEEKDAYS if re.search(rf"\b{w}\b", s)) > 1
    ):
        return DateParse("ambiguous")
    if s in ("today",):
        return DateParse("ok", anchor)
    if s in ("tomorrow", "tmrw", "kal"):
        return DateParse("ok", anchor + timedelta(days=1))
    if s in ("day after tomorrow", "day after"):
        return DateParse("ok", anchor + timedelta(days=2))
    if s in ("end of month", "month end", "eom", "end of the month"):
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        return DateParse("ok", date(anchor.year, anchor.month, last))
    m = re.fullmatch(r"in (\d{1,3}) days?", s)
    if m:
        return DateParse("ok", anchor + timedelta(days=int(m.group(1))))
    m = re.fullmatch(
        r"(?:next |coming |by |on |this )?"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)",
        s,
    )
    if m:
        return DateParse("ok", _next_weekday(anchor, _WEEKDAYS[m.group(1)]))
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return DateParse("ok", date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            return DateParse("unparseable")
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)  # DD/MM/YYYY (Indian convention)
    if m:
        try:
            return DateParse("ok", date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            return DateParse("unparseable")
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
                return DateParse("unparseable")
            if not g[2] and d <= anchor:  # nearest future occurrence across a year boundary
                try:
                    d = date(year + 1, _MONTHS[mon_s], int(day_s))
                except ValueError:
                    return DateParse("unparseable")
            return DateParse("ok", d)
    return DateParse("unparseable")


def parse_amount(raw: str) -> AmountParse:
    """Resolve a verbatim span to ClaimedPaise. Units: rupees by default; k = thousand; lakh/L; crore/cr."""
    s = " ".join(raw.strip().lower().replace(",", "").split())
    if not s:
        return AmountParse("unparseable")
    if any(w == s or f" {w} " in f" {s} " for w in _AMBIGUOUS_AMOUNT):
        return AmountParse("ambiguous")
    if re.search(r"\d.*\b(or|to|-)\b.*\d", s) and not re.fullmatch(r"[\d.]+/-", s):
        return AmountParse("ambiguous")  # a range
    m = re.fullmatch(
        r"(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lakhs|lac|l|crore|cr|rupees|rs|/-|inr)?", s
    )
    if not m:
        return AmountParse("unparseable")
    num, unit = m.group(1), m.group(2)
    has_currency = bool(re.match(r"(rs\.?|inr|₹)", s))
    mult = {
        None: 1,
        "k": 1_000,
        "thousand": 1_000,
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
    }[unit]
    if "." in num and unit is None and not has_currency:
        return AmountParse("ambiguous")  # "1.5" — unit unclear
    rupees = float(num) * mult
    paise_val = round(rupees * 100)
    if paise_val <= 0:
        return AmountParse("unparseable")
    return AmountParse("ok", claimed_paise(int(paise_val)))
