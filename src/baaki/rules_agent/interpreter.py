"""Deterministic keyword interpreter — the RULES_ONLY baseline and the L1 fallback (no model)."""

from __future__ import annotations

import re
from datetime import date
from typing import Final

from baaki.contracts.validation_result import NormalizedInterpretation
from baaki.policy.schemas.interpretation_v1 import Intent
from baaki.policy.validate.normalize import parse_amount, parse_date
from baaki.rules_agent.restriction import detect

INTERPRETER_VERSION: Final[str] = "keyword.v1"

# Ordered: earlier rules win. Restriction is checked first via the shared detector.
_RULES: Final[tuple[tuple[Intent, re.Pattern[str]], ...]] = tuple(
    (intent, re.compile(rx, re.IGNORECASE))
    for intent, rx in (
        (
            Intent.ALREADY_PAID_CLAIM,
            r"\b(already\s+paid|payment\s+(done|made|sent)|paid\s+(it|already|last)|kar\s+diya|ho\s+gaya|transferred)\b",
        ),
        (
            Intent.DISPUTE_AMOUNT,
            r"\b(wrong\s+amount|overcharg|amount\s+(is\s+)?(wrong|incorrect)|not\s+correct|galat\s+amount|short\s+supply)\b",
        ),
        (
            Intent.DISPUTE_DELIVERY,
            r"\b(not\s+(received|delivered)|never\s+(got|received)|damaged|missing\s+items?|nahi\s+mila)\b",
        ),
        (Intent.REQUEST_INSTALLMENTS, r"\b(instal?lments?|in\s+parts|emi|two\s+parts|three\s+parts|kist)\b"),
        (
            Intent.WRONG_CONTACT,
            r"\b(wrong\s+(number|person|contact)|not\s+(the\s+)?(right|concerned)\s+person|galat\s+number)\b",
        ),
        (
            Intent.NEEDS_DOCUMENT,
            r"\b(send\s+(the\s+)?(invoice|bill|statement|copy)|need\s+(a\s+)?(copy|invoice|statement)|resend)\b",
        ),
        (
            Intent.WILL_PAY_ON_DATE,
            r"\b(will\s+pay|pay\s+(by|on|before)|kar\s+denge|kar\s+dunga|kar\s+doonga|paying\s+(by|on)|clear\s+(it\s+)?by)\b",
        ),
    )
)
_DATE_SPAN: Final[re.Pattern[str]] = re.compile(
    r"\b(today|tomorrow|kal|day after tomorrow|end of month|month end|eom|in \d{1,3} days?|next week"
    r"|(?:next |coming |by |on )?"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b"
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})",
    re.IGNORECASE,
)
_AMOUNT_SPAN: Final[re.Pattern[str]] = re.compile(
    r"(?:rs\.?|inr|₹)?\s*\d[\d,]*(?:\.\d+)?\s*(?:k|thousand|lakhs?|lac|crore|cr|rupees|rs|/-)?", re.IGNORECASE
)


def classify_intent(text: str) -> Intent:
    if detect(text) is not None:
        return Intent.UNSUBSCRIBE
    for intent, rx in _RULES:
        if rx.search(text):
            return intent
    return Intent.NO_CLEAR_INTENT


def interpret(text: str, anchor: date) -> NormalizedInterpretation:
    """Deterministic interpretation; ambiguity yields None fields rather than guesses."""
    intent = classify_intent(text)
    promised_date = None
    promised_paise = None
    if intent is Intent.WILL_PAY_ON_DATE:
        spans = _DATE_SPAN.findall(text)
        if len(spans) == 1:
            dp = parse_date(spans[0], anchor)
            promised_date = dp.value if dp.status == "ok" else None
        amounts = [m.group(0).strip() for m in _AMOUNT_SPAN.finditer(text) if any(ch.isdigit() for ch in m.group(0))]
        if len(amounts) == 1:
            ap = parse_amount(amounts[0])
            promised_paise = ap.value if ap.status == "ok" else None
    return NormalizedInterpretation(
        intent=str(intent),
        promised_date=promised_date,
        promised_paise=promised_paise,
        invoice_ids=[],
        contact_id=None,
        effective_confidence=1.0,
    )
