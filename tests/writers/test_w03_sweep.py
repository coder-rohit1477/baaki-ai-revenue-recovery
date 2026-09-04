from sqlalchemy import text

from baaki.domain.ids import new_id
from tests.helpers import NOW, count, payment_entity, raises_writer, record_sweep, sweep_response


def test_sr_hash_count_role_computed(app):
    raw = sweep_response([payment_entity("pay_a", 100, None), payment_entity("pay_b", 200, None)])
    sid = record_sweep(app, raw)
    row = app.execute(text("select raw_response_hash, item_count, created_by_role from baaki.sweep_run where sweep_run_id=:s"), {"s": sid}).one()
    import hashlib
    assert row[0] == hashlib.sha256(raw.encode()).hexdigest() and row[1] == 2 and row[2] == "baaki_app"


def test_sr3_identical_response_same_run(app):
    raw = sweep_response([payment_entity("pay_a", 100, None)])
    assert record_sweep(app, raw) == record_sweep(app, raw)
    assert count(app, "sweep_run") == 1


def test_invalid_json_refused(app):
    with raises_writer("invalid_json"):
        app.execute(text("SELECT baaki_write.record_sweep_run(:s, 'razorpay', :f, :t, :r, 'nope')"), {"s": new_id(), "f": NOW, "t": NOW, "r": NOW})
    app.rollback()
