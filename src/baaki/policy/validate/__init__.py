"""Deterministic validator (ARCHITECTURE.md §4.1): 16 checks, 20 reasons, fail-closed."""

from baaki.policy.validate.ladder import ValidationBundle, validate

__all__ = ["ValidationBundle", "validate"]
