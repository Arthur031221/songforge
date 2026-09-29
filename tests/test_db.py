from songforge.db import Store


def make(store, **kw):
    base = {
        "kind": "song",
        "title": "t",
        "style": "pop",
        "lyrics": "[Verse]\nla",
        "mode": "fast",
        "seed": 1,
        "max_seconds": 60,
    }
    return store.create(**{**base, **kw})


def test_create_get_list(paths):
    store = Store(paths.db)
    a = make(store, title="first")
    b = make(store, title="second", kind="cover")
    assert store.get(a["id"])["title"] == "first"
    assert a["status"] == "queued"
    assert a["instrumental"] is False
    assert [s["id"] for s in store.list()] == [b["id"], a["id"]]
    assert [s["id"] for s in store.list(kind="cover")] == [b["id"]]
    assert store.get("missing") is None


def test_queue_order_and_position(paths):
    store = Store(paths.db)
    a, b, c = make(store), make(store), make(store)
    assert store.next_queued()["id"] == a["id"]
    assert store.queue_position(c["id"]) == 3
    store.update(a["id"], status="running")
    assert store.next_queued()["id"] == b["id"]
    assert store.queue_position(a["id"]) == 0


def test_update_rejects_unknown_fields(paths):
    store = Store(paths.db)
    a = make(store)
    store.update(a["id"], peaks=[0.1, 0.5], truncated=1)
    row = store.get(a["id"])
    assert row["peaks"] == [0.1, 0.5]
    assert row["truncated"] is True
    try:
        store.update(a["id"], id="x")
    except ValueError as error:
        assert "id" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_recover_marks_running_as_failed(paths):
    store = Store(paths.db)
    a = make(store)
    store.update(a["id"], status="running")
    assert store.recover() == 1
    row = store.get(a["id"])
    assert row["status"] == "failed"
    assert "Interrupted" in row["error"]


def test_delete(paths):
    store = Store(paths.db)
    a = make(store)
    assert store.delete(a["id"])
    assert not store.delete(a["id"])
