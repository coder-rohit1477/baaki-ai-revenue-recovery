"""Money-safety kernel: pure functions only (ARCHITECTURE.md §5). Imports no I/O, no clock, no model."""

from baaki.policy.kernel.decide import decide
from baaki.policy.kernel.target import select_target

__all__ = ["decide", "select_target"]
