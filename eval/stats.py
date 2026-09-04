"""Descriptive statistics (D-2b2-G2-5, LOCKED): Wilson 95% intervals and the rule-of-three bound. Deterministic."""

from __future__ import annotations

import math

from eval.records import MetricValue

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("n must be positive")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three_lower_bound(n: int) -> float:
    """95% one-sided lower bound on a proportion observed as n/n (zero misses)."""
    if n <= 0:
        raise ValueError("n must be positive")
    return max(0.0, 1.0 - 3.0 / n)


def metric(
    k: int,
    n: int,
    *,
    label: str = "measured",
    note: str | None = None,
    zero_miss_bound: bool = False,
) -> MetricValue:
    """Build a MetricValue; `rate` is None when the denominator is 0 (never 0.0 or 1.0 by default)."""
    if n == 0:
        return MetricValue(numerator=0, denominator=0, rate=None, label=label, note=note or "n=0")
    lo, hi = wilson(k, n)
    r3 = rule_of_three_lower_bound(n) if zero_miss_bound and k == n else None
    return MetricValue(
        numerator=k,
        denominator=n,
        rate=round(k / n, 6),
        ci_low=round(lo, 6),
        ci_high=round(hi, 6),
        rule_of_three_lower_bound=(round(r3, 6) if r3 is not None else None),
        label=label,
        note=note,
    )
