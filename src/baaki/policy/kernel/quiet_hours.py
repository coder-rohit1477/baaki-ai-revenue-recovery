"""P10 quiet hours — pure calendar arithmetic in the organization timezone (§5.4: 09:00 <= t < 19:00, Sunday closed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from baaki.policy.ruleset import QuietHours


def in_window(as_of: datetime, tz: str, qh: QuietHours) -> bool:
    local = as_of.astimezone(ZoneInfo(tz))
    return local.weekday() in qh.days and qh.start <= local.time() < qh.end


def next_window_open(as_of: datetime, tz: str, qh: QuietHours) -> datetime:
    """First instant t > as_of with in_window(t); returned in UTC."""
    z = ZoneInfo(tz)
    local = as_of.astimezone(z)
    for delta in range(0, 8):
        day = (local + timedelta(days=delta)).date()
        if day.weekday() not in qh.days:
            continue
        candidate = datetime.combine(day, qh.start, tzinfo=z)
        if delta == 0 and local.time() >= qh.start:
            if local.time() < qh.end:
                return as_of  # already inside — caller should not have deferred
            continue
        return candidate.astimezone(UTC)
    raise ValueError("quiet_hours.days is empty")  # unreachable: ruleset validation forbids it
