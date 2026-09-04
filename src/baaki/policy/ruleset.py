"""Ruleset — policy constants v1, loaded fail-closed from exact bytes (ARCHITECTURE.md §5.4, §5.5, P2-D1/P2-D2)."""

from __future__ import annotations

import hashlib
import tomllib
from datetime import time
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from baaki.domain.enums import ActionType

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")

DAY_NAMES: Final[dict[str, int]] = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
DISCARD: Final[str] = "discard"


class RulesetInvalid(Exception):
    """Fail-closed: the policy file cannot be used. No partial or defaulted configuration exists."""


class QuietHours(BaseModel):
    model_config = _STRICT
    start: time
    end: time
    days: frozenset[int]  # 0=Mon … 6=Sun; Sunday (6) is closed in v1


class Band(BaseModel):
    model_config = _STRICT
    name: str
    lo: float
    hi: float
    hi_inclusive: bool
    cap: int | None  # None == discard
    force_approval: frozenset[ActionType]

    def contains(self, c: float) -> bool:
        if self.hi_inclusive:
            return self.lo <= c <= self.hi
        return self.lo <= c < self.hi


class TierCap(BaseModel):
    model_config = _STRICT
    bands: tuple[Band, ...]

    def band_for(self, confidence: float) -> Band:
        for b in self.bands:
            if b.contains(confidence):
                return b
        raise RulesetInvalid(f"confidence {confidence} outside every band")  # unreachable if coverage validated


class RulesOnly(BaseModel):
    model_config = _STRICT
    link_after_days_overdue: int
    reminder_after_days_overdue: int


class Ruleset(BaseModel):
    model_config = _STRICT
    policy_version: str
    policy_hash: str = Field(min_length=64, max_length=64)
    contact_cap_account_7d: int
    contact_cap_invoice_7d: int
    paid_claim_ttl_hours: int
    link_active_window_hours: int
    ptp_horizon_days: int
    ptp_grace_business_days: int
    ptp_nudge_days_before_due: int
    control_cadence_days_overdue: tuple[int, ...]
    quiet_hours: QuietHours
    rules_only: RulesOnly
    tier_cap: TierCap

    @property
    def confidence_floor(self) -> float:
        """Validator check 15 threshold = lower bound of the lowest band with cap >= 1 (0.70 in v1)."""
        return min(b.lo for b in self.tier_cap.bands if b.cap is not None and b.cap >= 1)


_TOP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "policy_version",
        "contact_cap_account_7d",
        "contact_cap_invoice_7d",
        "paid_claim_ttl_hours",
        "link_active_window_hours",
        "ptp_horizon_days",
        "ptp_grace_business_days",
        "ptp_nudge_days_before_due",
        "control_cadence_days_overdue",
        "quiet_hours",
        "rules_only",
        "tier_cap",
    }
)


def _int(raw: dict[str, Any], key: str, *, positive: bool = True) -> int:
    v = raw.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise RulesetInvalid(f"{key}: expected integer")
    if positive and v <= 0:
        raise RulesetInvalid(f"{key}: must be > 0")
    return v


def _time(s: Any, key: str) -> time:
    if not isinstance(s, str) or len(s) != 5 or s[2] != ":":
        raise RulesetInvalid(f"{key}: expected HH:MM")
    try:
        hh, mm = int(s[:2]), int(s[3:])
        return time(hh, mm)
    except ValueError as e:
        raise RulesetInvalid(f"{key}: invalid time") from e


def parse_ruleset(data: bytes, *, expected_version: str) -> Ruleset:
    """Parse exact bytes. Every fault raises RulesetInvalid (§5.5)."""
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise RulesetInvalid(f"malformed TOML: {e}") from e
    unknown = set(raw) - _TOP_KEYS
    missing = _TOP_KEYS - set(raw)
    if unknown:
        raise RulesetInvalid(f"unknown keys: {sorted(unknown)}")
    if missing:
        raise RulesetInvalid(f"missing keys: {sorted(missing)}")
    if raw["policy_version"] != expected_version:
        raise RulesetInvalid(f"policy_version {raw['policy_version']!r} does not match file {expected_version!r}")

    cadence = raw["control_cadence_days_overdue"]
    if (
        not isinstance(cadence, list)
        or not cadence
        or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 for d in cadence)
    ):
        raise RulesetInvalid("control_cadence_days_overdue: non-empty list of non-negative integers")
    if sorted(cadence) != cadence or len(set(cadence)) != len(cadence):
        raise RulesetInvalid("control_cadence_days_overdue: must be strictly increasing")

    qh = raw["quiet_hours"]
    if not isinstance(qh, dict) or set(qh) != {"start", "end", "days"}:
        raise RulesetInvalid("quiet_hours: keys must be exactly start, end, days")
    start, end = _time(qh["start"], "quiet_hours.start"), _time(qh["end"], "quiet_hours.end")
    if start >= end:
        raise RulesetInvalid("quiet_hours: start must be before end")
    if not isinstance(qh["days"], list) or not qh["days"] or any(d not in DAY_NAMES for d in qh["days"]):
        raise RulesetInvalid("quiet_hours.days: list of MON..SUN")
    days = frozenset(DAY_NAMES[d] for d in qh["days"])

    ro = raw["rules_only"]
    if not isinstance(ro, dict) or set(ro) != {"link_after_days_overdue", "reminder_after_days_overdue"}:
        raise RulesetInvalid("rules_only: keys must be exactly link_after_days_overdue, reminder_after_days_overdue")

    tc = raw["tier_cap"]
    if not isinstance(tc, dict) or set(tc) != {"bands"} or not isinstance(tc["bands"], list) or not tc["bands"]:
        raise RulesetInvalid("tier_cap.bands: non-empty list required")
    bands: list[Band] = []
    for i, b in enumerate(tc["bands"]):
        allowed = {"name", "lo", "hi", "cap", "force_approval"}
        if not isinstance(b, dict) or not {"name", "lo", "hi", "cap"} <= set(b) or set(b) - allowed:
            raise RulesetInvalid(f"tier_cap.bands[{i}]: keys must be name, lo, hi, cap[, force_approval]")
        lo, hi = b["lo"], b["hi"]
        if (
            isinstance(lo, bool)
            or isinstance(hi, bool)
            or not isinstance(lo, int | float)
            or not isinstance(hi, int | float)
        ):
            raise RulesetInvalid(f"tier_cap.bands[{i}]: lo/hi must be numbers")
        if not (0.0 <= lo < hi <= 1.0):
            raise RulesetInvalid(f"tier_cap.bands[{i}]: need 0 <= lo < hi <= 1")
        cap_raw = b["cap"]
        if cap_raw == DISCARD:
            cap: int | None = None
        elif isinstance(cap_raw, int) and not isinstance(cap_raw, bool) and 0 <= cap_raw <= 2:
            cap = cap_raw
        else:
            raise RulesetInvalid(f"tier_cap.bands[{i}].cap: 0..2 or 'discard'")
        fa_raw = b.get("force_approval", [])
        if not isinstance(fa_raw, list):
            raise RulesetInvalid(f"tier_cap.bands[{i}].force_approval: list")
        try:
            fa = frozenset(ActionType(x) for x in fa_raw)
        except ValueError as e:
            raise RulesetInvalid(f"tier_cap.bands[{i}].force_approval: unknown action") from e
        if fa and (cap is None or cap < 1):
            raise RulesetInvalid(f"tier_cap.bands[{i}]: force_approval requires cap >= 1")
        bands.append(
            Band(
                name=str(b["name"]),
                lo=float(lo),
                hi=float(hi),
                hi_inclusive=(float(hi) == 1.0),
                cap=cap,
                force_approval=fa,
            )
        )
    bands.sort(key=lambda x: -x.lo)  # highest confidence first
    # coverage, contiguity, no overlap
    if bands[0].hi != 1.0 or bands[-1].lo != 0.0:
        raise RulesetInvalid("tier_cap.bands must cover [0, 1]")
    for upper, lower in zip(bands, bands[1:], strict=False):
        if lower.hi != upper.lo:
            raise RulesetInvalid("tier_cap.bands must be contiguous and non-overlapping")
    # authority non-increasing as confidence decreases (discard == -1)
    caps = [(-1 if b.cap is None else b.cap) for b in bands]
    if any(a < b for a, b in zip(caps, caps[1:], strict=False)):
        raise RulesetInvalid("tier_cap.bands: cap must be non-increasing as confidence decreases")

    try:
        return Ruleset(
            policy_version=raw["policy_version"],
            policy_hash=hashlib.sha256(data).hexdigest(),
            contact_cap_account_7d=_int(raw, "contact_cap_account_7d"),
            contact_cap_invoice_7d=_int(raw, "contact_cap_invoice_7d"),
            paid_claim_ttl_hours=_int(raw, "paid_claim_ttl_hours"),
            link_active_window_hours=_int(raw, "link_active_window_hours"),
            ptp_horizon_days=_int(raw, "ptp_horizon_days"),
            ptp_grace_business_days=_int(raw, "ptp_grace_business_days"),
            ptp_nudge_days_before_due=_int(raw, "ptp_nudge_days_before_due"),
            control_cadence_days_overdue=tuple(cadence),
            quiet_hours=QuietHours(start=start, end=end, days=days),
            rules_only=RulesOnly(
                link_after_days_overdue=_int(ro, "link_after_days_overdue"),
                reminder_after_days_overdue=_int(ro, "reminder_after_days_overdue"),
            ),
            tier_cap=TierCap(bands=tuple(bands)),
        )
    except ValidationError as e:
        raise RulesetInvalid(str(e)) from e


def load_ruleset(path: Path) -> Ruleset:
    """Load from a file; the file stem (e.g. `policy.v1`) must equal `policy_version`."""
    if not path.is_file():
        raise RulesetInvalid(f"ruleset file not found: {path}")
    return parse_ruleset(path.read_bytes(), expected_version=path.stem)


DEFAULT_RULESET_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "config" / "policy.v1.toml"
