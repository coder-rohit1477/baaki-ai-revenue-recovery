"""Money as integer paise. Two non-interoperating types (ARCHITECTURE.md §1).

`Paise`        — deterministic financial authority (ledger projection or payment_event).
`ClaimedPaise` — what a debtor said, parsed deterministically. Comparison only. Never authority.

There is deliberately NO function that converts ClaimedPaise -> Paise (V7).
"""

from __future__ import annotations

from typing import Final, NewType

Paise = NewType("Paise", int)
ClaimedPaise = NewType("ClaimedPaise", int)

_FORBIDDEN_TYPES: Final = (float, bool)


def _strict_int(value: object, name: str) -> int:
    if isinstance(value, _FORBIDDEN_TYPES) or not isinstance(value, int):
        raise TypeError(f"{name} requires int paise, got {type(value).__name__}")
    return value


def paise(value: object) -> Paise:
    """Non-negative authority amount (0 is legal for an outstanding balance)."""
    v = _strict_int(value, "paise")
    if v < 0:
        raise ValueError("paise cannot be negative")
    return Paise(v)


def positive_paise(value: object) -> Paise:
    """Strictly positive authority amount (ledger lines, issued amounts, payments)."""
    v = paise(value)
    if v == 0:
        raise ValueError("amount must be > 0 paise")
    return v


def claimed_paise(value: object) -> ClaimedPaise:
    """Strictly positive claim amount extracted from a debtor message."""
    v = _strict_int(value, "claimed_paise")
    if v <= 0:
        raise ValueError("claimed amount must be > 0 paise")
    return ClaimedPaise(v)


def add_paise(a: Paise, b: Paise) -> Paise:
    return Paise(int(a) + int(b))


def sub_paise(a: Paise, b: Paise) -> Paise:
    """a - b; refuses to go negative (a receivable never goes below zero, L5)."""
    r = int(a) - int(b)
    if r < 0:
        raise ValueError("paise subtraction would be negative")
    return Paise(r)


def min_paise(a: Paise, b: Paise) -> Paise:
    return Paise(min(int(a), int(b)))


def claim_within(claim: ClaimedPaise, authority: Paise) -> bool:
    """The only sanctioned interaction between the two types: a comparison (PTP guard, §2.3 #2)."""
    return int(claim) <= int(authority)
