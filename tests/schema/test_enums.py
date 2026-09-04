"""A. Enum labels in PostgreSQL equal the Python single source (§13.3, GAP-1 values)."""
from sqlalchemy import text

from baaki.domain.enums import FORBIDDEN_CAPABILITIES, POSTGRES_ENUMS


def test_enum_labels_match_python(su):
    for pg_name, py_enum in POSTGRES_ENUMS.items():
        labels = [r[0] for r in su.execute(text(
            "select e.enumlabel from pg_enum e join pg_type t on t.oid=e.enumtypid join pg_namespace n on n.oid=t.typnamespace "
            "where n.nspname='baaki' and t.typname=:n order by e.enumsortorder"), {"n": pg_name})]
        assert labels == [m.value for m in py_enum], pg_name


def test_exactly_nineteen(su):
    names = {r[0] for r in su.execute(text(
        "select t.typname from pg_type t join pg_namespace n on n.oid=t.typnamespace where n.nspname='baaki' and t.typtype='e'"))}
    assert names == set(POSTGRES_ENUMS) and len(names) == 20


def test_gap1_values_exact(su):
    def labels(n):
        return {r[0] for r in su.execute(text("select unnest(enum_range(null::baaki." + n + "))::text"))}
    assert labels("suppress_reason") == {"DISPUTE_OPEN", "PAID_CLAIM_PENDING", "PTP_ACTIVE", "FREQUENCY_CAP", "NO_ELIGIBLE_ACTION"}
    assert labels("escalation_reason") == {"DISPUTE_UNRESOLVED", "PAID_CLAIM_UNVERIFIED", "AMBIGUOUS_INTERPRETATION", "MANUAL_REVIEW"}
    assert labels("assignee_queue") == {"DISPUTES", "COLLECTIONS"}


def test_no_forbidden_capability_in_any_enum(su):
    labels = {r[0] for r in su.execute(text(
        "select e.enumlabel from pg_enum e join pg_type t on t.oid=e.enumtypid join pg_namespace n on n.oid=t.typnamespace where n.nspname='baaki'"))}
    assert not (labels & FORBIDDEN_CAPABILITIES)
    assert not ({"WRITTEN_OFF", "CANCELLED", "PARTIALLY_PAID", "DRAFT", "MANUAL_CORRECTION", "SEED"} & labels)
