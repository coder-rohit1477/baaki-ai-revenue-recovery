"""§5.4/§5.5 — ruleset constants, exact-byte hashing, fail-closed loading (P2-D1, P2-D2)."""
import hashlib
from datetime import time

import pytest

from baaki.domain.enums import ActionType
from baaki.policy.ruleset import DEFAULT_RULESET_PATH, RulesetInvalid, load_ruleset, parse_ruleset

BASE = DEFAULT_RULESET_PATH.read_text()


def _mut(old: str, new: str) -> bytes:
    assert old in BASE, old
    return BASE.replace(old, new, 1).encode()


def test_locked_constants_match_architecture_5_4():
    r = load_ruleset(DEFAULT_RULESET_PATH)
    assert r.policy_version == "policy.v1"
    assert (r.contact_cap_account_7d, r.contact_cap_invoice_7d) == (3, 2)
    assert (r.quiet_hours.start, r.quiet_hours.end) == (time(9, 0), time(19, 0))
    assert r.quiet_hours.days == frozenset({0, 1, 2, 3, 4, 5}) and 6 not in r.quiet_hours.days
    assert r.paid_claim_ttl_hours == 72 and r.link_active_window_hours == 24
    assert (r.ptp_horizon_days, r.ptp_grace_business_days, r.ptp_nudge_days_before_due) == (30, 2, 2)
    assert r.control_cadence_days_overdue == (3, 7, 15)
    assert (r.rules_only.link_after_days_overdue, r.rules_only.reminder_after_days_overdue) == (15, 3)
    names = [(b.name, b.lo, b.hi, b.cap) for b in r.tier_cap.bands]
    assert names == [("A", 0.85, 1.0, 1), ("B", 0.70, 0.85, 1), ("C", 0.50, 0.70, 0), ("D", 0.0, 0.50, None)]
    assert r.tier_cap.bands[1].force_approval == frozenset({ActionType.SEND_PAYMENT_LINK})
    assert r.confidence_floor == 0.70


def test_policy_hash_is_sha256_of_exact_bytes():
    r = load_ruleset(DEFAULT_RULESET_PATH)
    assert r.policy_hash == hashlib.sha256(DEFAULT_RULESET_PATH.read_bytes()).hexdigest()
    # a comment-only edit is a different policy_hash (exact bytes, not semantic)
    r2 = parse_ruleset(_mut("# Baaki policy constants v1", "# Baaki policy constants v1 (edited)"), expected_version="policy.v1")
    assert r2.policy_hash != r.policy_hash


def test_band_boundaries_half_open_a_includes_one():
    tc = load_ruleset(DEFAULT_RULESET_PATH).tier_cap
    assert tc.band_for(1.0).name == "A" and tc.band_for(0.85).name == "A"
    assert tc.band_for(0.8499).name == "B" and tc.band_for(0.70).name == "B"
    assert tc.band_for(0.6999).name == "C" and tc.band_for(0.50).name == "C"
    assert tc.band_for(0.4999).name == "D" and tc.band_for(0.0).name == "D"


@pytest.mark.parametrize("old,new,why", [
    ("policy_version = \"policy.v1\"", "policy_version = \"policy.v2\"", "version mismatch"),
    ("contact_cap_account_7d = 3", "contact_cap_account_7d = 3\nsurprise_key = 1", "unknown top-level key"),
    ("contact_cap_account_7d = 3\n", "", "missing key"),
    ("contact_cap_account_7d = 3", "contact_cap_account_7d = 0", "cap must be > 0"),
    ("contact_cap_account_7d = 3", "contact_cap_account_7d = \"3\"", "cap wrong type"),
    ("paid_claim_ttl_hours = 72", "paid_claim_ttl_hours = -1", "negative ttl"),
    ("start = \"09:00\"", "start = \"19:30\"", "quiet start after end"),
    ("start = \"09:00\"", "start = \"9am\"", "quiet time format"),
    ("days = [\"MON\", \"TUE\", \"WED\", \"THU\", \"FRI\", \"SAT\"]", "days = [\"MON\", \"FUNDAY\"]", "unknown day"),
    ("days = [\"MON\", \"TUE\", \"WED\", \"THU\", \"FRI\", \"SAT\"]", "days = []", "no open days"),
    ("end = \"19:00\"", "end = \"19:00\"\ntz = \"UTC\"", "unknown quiet_hours key"),
    ("control_cadence_days_overdue = [3, 7, 15]", "control_cadence_days_overdue = [7, 3, 15]", "cadence not increasing"),
    ("control_cadence_days_overdue = [3, 7, 15]", "control_cadence_days_overdue = []", "empty cadence"),
    ("lo = 0.70\nhi = 0.85", "lo = 0.60\nhi = 0.85", "overlapping bands"),
    ("lo = 0.70\nhi = 0.85", "lo = 0.75\nhi = 0.85", "gap between bands"),
    ("lo = 0.00\nhi = 0.50", "lo = 0.10\nhi = 0.50", "bands do not cover 0"),
    ("hi = 1.00", "hi = 0.99", "bands do not cover 1"),
    ("cap = 0\n", "cap = 2\n", "cap increases as confidence falls"),
    ("cap = \"discard\"", "cap = \"drop\"", "unknown cap token"),
    ("cap = \"discard\"", "cap = 3", "cap out of range"),
    ("force_approval = [\"SEND_PAYMENT_LINK\"]", "force_approval = [\"SEND_MONEY\"]", "unknown action"),
    ("name = \"C\"\nlo = 0.50\nhi = 0.70\ncap = 0", "name = \"C\"\nlo = 0.50\nhi = 0.70\ncap = 0\nforce_approval = [\"SEND_REMINDER\"]", "force_approval on cap-0 band"),
    ("name = \"A\"", "name = \"A\"\nbonus = 1", "unknown band key"),
    ("link_after_days_overdue = 15", "link_after_days_overdue = 15\nextra = 1", "unknown rules_only key"),
    ("[quiet_hours]", "[quiet_hours", "malformed TOML"),
])
def test_fail_closed(old, new, why):
    with pytest.raises(RulesetInvalid):
        parse_ruleset(_mut(old, new), expected_version="policy.v1")


def test_missing_file_and_stem_mismatch_fail_closed(tmp_path):
    with pytest.raises(RulesetInvalid):
        load_ruleset(tmp_path / "nope.toml")
    p = tmp_path / "policy.v9.toml"
    p.write_bytes(BASE.encode())
    with pytest.raises(RulesetInvalid):
        load_ruleset(p)  # stem policy.v9 != policy_version policy.v1


def test_ruleset_is_frozen():
    r = load_ruleset(DEFAULT_RULESET_PATH)
    with pytest.raises(Exception):
        r.contact_cap_account_7d = 99  # type: ignore[misc]
