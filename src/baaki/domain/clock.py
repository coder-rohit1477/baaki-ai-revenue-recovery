"""Clock protocol. Nothing in the system reads the wall clock directly (§3.5, §12.2 clock row).
Phase 1 provides the protocol and two implementations; no scheduler exists yet.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime: ...
    def business_date(self, org_timezone: str) -> date: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def business_date(self, org_timezone: str) -> date:
        return self.now().astimezone(ZoneInfo(org_timezone)).date()


class VirtualClock:
    """Deterministic, settable clock for tests and (later) the simulated-day runner."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("VirtualClock requires an aware datetime")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def business_date(self, org_timezone: str) -> date:
        return self._now.astimezone(ZoneInfo(org_timezone)).date()

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("VirtualClock requires an aware datetime")
        self._now = at.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta
