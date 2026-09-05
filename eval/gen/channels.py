"""Channel-scope classification for opt-out phrasing (Phase 2b-2 G4 migration, P1).

The G4 v1 corpus mislabelled 29 opt-out items because a phrase naming a channel sat in the *general*
core pool, so every item built from it inherited `GENERAL` from its pool rather than from its own wording.
This module makes that judgement explicit, mechanical and testable, so the defect cannot recur.

Two rules matter and both were got wrong in the first attempt:

* **Medium first.** A core naming a medium is that medium even when it also mentions a number:
  `is number pe SMS mat bhejo` is SMS, not NUMBER.
* **Both scripts.** Latin and Devanagari channel words are recognised equally; a Latin-only test is blind
  to `मेल`, `एसएमएस` and `व्हाट्सएप`.

A number on its own does not identify a medium, so `NUMBER` never resolves to a channel by itself: it is
read against the arrival channel, and stays `AMBIGUOUS` wherever that reading is undecidable.
"""

from __future__ import annotations

import re
from typing import Final

EMAIL: Final = re.compile(r"\b(e-?mails?|e-?mailing|mails?|mailbox|inbox)\b|मेल|ईमेल", re.IGNORECASE)
SMS: Final = re.compile(r"\b(sms|texts?|texting|text messages?)\b|एसएमएस", re.IGNORECASE)
WHATSAPP: Final = re.compile(r"\bwhats\s?app\b|व्हाट्सएप", re.IGNORECASE)
VOICE: Final = re.compile(r"\b(calls?|calling|phone)\b|बुलाइए|कॉल|फ़ोन", re.IGNORECASE)
# "don't write on this number" restricts; "remove my number from the list" does not
# Hinglish puts the postposition after the noun ("is number pe"); English puts it before
# ("on this number"). Both restrict; only the removal sense in NUMBER_OBJECT does not.
NUMBER_RESTRICT: Final = re.compile(
    r"\b(is|this)\s+number\s+(pe|par|on)\b"
    r"|\b(on|to|at)\s+(this|that)\s+number\b"
    r"|इस नंबर पर",
    re.IGNORECASE,
)
NUMBER_OBJECT: Final = re.compile(r"\bmera number\b|\bmy number\b|मेरा नंबर", re.IGNORECASE)

REGISTER_CHANNEL: Final[dict[str, str]] = {
    "email": "EMAIL",
    "sms": "SMS",
    "whatsapp": "WHATSAPP",
    "transcript": "VOICE",
}
# A bare number resolves against the arrival channel; on a number-addressed channel it is undecidable.
NUMBER_RESOLUTION: Final[dict[str, str]] = {
    "SMS": "CHANNEL_INBOUND",
    "EMAIL": "CHANNEL_OTHER",
    "WHATSAPP": "AMBIGUOUS",
    "VOICE": "AMBIGUOUS",
}
AMBIGUOUS_REGISTERS: Final[tuple[str, ...]] = ("whatsapp", "transcript")


def named_channel(core: str) -> str | None:
    """The channel a phrase restricts contact to, or None when it restricts nothing.

    Returns one of EMAIL, SMS, WHATSAPP, VOICE, NUMBER, or None for a GENERAL-safe phrase.
    """
    if WHATSAPP.search(core):
        return "WHATSAPP"
    if EMAIL.search(core):
        return "EMAIL"
    if SMS.search(core):
        return "SMS"
    if VOICE.search(core):
        return "VOICE"
    if NUMBER_OBJECT.search(core) and not NUMBER_RESTRICT.search(core):
        return None  # the number is what is being removed, not a limit on scope
    if NUMBER_RESTRICT.search(core):
        return "NUMBER"
    return None


def is_general_safe(core: str) -> bool:
    return named_channel(core) is None


def resolve_scope(named: str, register: str) -> str:
    """Scope an item takes given the channel its text names and the channel it arrived on."""
    arrival = REGISTER_CHANNEL[register]
    if named == "NUMBER":
        return NUMBER_RESOLUTION[arrival]
    return "CHANNEL_INBOUND" if named == arrival else "CHANNEL_OTHER"


__all__ = [
    "AMBIGUOUS_REGISTERS",
    "NUMBER_RESOLUTION",
    "REGISTER_CHANNEL",
    "is_general_safe",
    "named_channel",
    "resolve_scope",
]
