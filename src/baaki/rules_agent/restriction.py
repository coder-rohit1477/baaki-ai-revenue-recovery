"""Arm-independent restriction detector (ARCHITECTURE.md §6.18.1). Pure, versioned, closed pattern set."""

from __future__ import annotations

import re
from typing import Final

from baaki.contracts.restriction_event import RestrictionMatch

MATCHER_VERSION: Final[str] = "restriction.v1"

# (pattern_id, compiled regex). Word-bounded, case-insensitive. Hinglish variants included.
PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (pid, re.compile(rx, re.IGNORECASE))
    for pid, rx in (
        ("STOP", r"(?<![a-z])stop(?![a-z])"),
        ("UNSUBSCRIBE", r"\bunsubscribe\b"),
        ("DO_NOT_CONTACT", r"\bdo\s+not\s+(contact|call|message|msg|text|email|mail)\b"),
        ("DONT_CONTACT", r"\bdon'?t\s+(contact|call|message|msg|text|email|mail)\b"),
        ("REMOVE_ME", r"\bremove\s+me\b"),
        ("OPT_OUT", r"\bopt[\s-]?out\b"),
        ("NO_MORE_MESSAGES", r"\bno\s+more\s+(messages?|calls?|reminders?|mails?)\b"),
        ("HI_MAT_KARO", r"\b(mat|na)\s+(karo|kariye|karna|bhejo|bhejiye|bhejna|call\s+karo)\b"),
        ("HI_BAND_KARO", r"\b(band|bandh)\s+(karo|kariye|kar\s+do|kardo)\b"),
        ("HI_PARESHAN", r"\bpareshan\s+(mat|na)\b"),
    )
)


def detect(text: str) -> RestrictionMatch | None:
    """Return the first matching closed pattern, or None. Deterministic in pattern order."""
    for pid, rx in PATTERNS:
        m = rx.search(text)
        if m:
            return RestrictionMatch(matched_pattern_id=pid, matcher_version=MATCHER_VERSION, span=m.group(0))
    return None
