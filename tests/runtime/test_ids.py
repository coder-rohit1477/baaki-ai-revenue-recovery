from baaki.domain.ids import new_id


def test_uuid7_version_and_monotonic():
    ids = [new_id() for _ in range(200)]
    assert all(i.version == 7 for i in ids)
    assert [i.int for i in ids] == sorted(i.int for i in ids)
    assert len(set(ids)) == 200
