from songforge.lock import EngineLock, engine_busy


def test_lock_is_exclusive(tmp_path):
    path = tmp_path / "engine.lock"
    a, b = EngineLock(path), EngineLock(path)
    assert not engine_busy(path)
    assert a.try_acquire()
    assert a.try_acquire()
    assert not b.try_acquire()
    assert engine_busy(path)
    a.release()
    assert b.try_acquire()
    b.release()
    assert not engine_busy(path)


def test_worker_waits_for_lock_and_can_be_cancelled(paths, fake_engine):
    import threading
    import time

    from songforge.db import Store
    from songforge.worker import Worker

    holder = EngineLock(paths.home / "engine.lock")
    assert holder.try_acquire()
    store = Store(paths.db)
    worker = Worker(store, fake_engine, paths)
    row = store.create(
        kind="song", title="t", style="s", lyrics="l", mode="fast", seed=1, max_seconds=60
    )
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and "Waiting" not in (store.get(row["id"])["detail"] or ""):
        time.sleep(0.05)
    assert "Waiting for another songforge job" in store.get(row["id"])["detail"]
    worker.cancel(row["id"])
    thread.join(timeout=5)
    assert store.get(row["id"])["status"] == "cancelled"
    holder.release()


def test_studio_start_does_not_fail_a_job_running_elsewhere(paths, fake_engine):
    from fastapi.testclient import TestClient

    from songforge.db import Store
    from songforge.server import create_app

    store = Store(paths.db)
    row = store.create(
        kind="song", title="t", style="s", lyrics="l", mode="fast", seed=1, max_seconds=60
    )
    store.update(row["id"], status="running")
    holder = EngineLock(paths.home / "engine.lock")
    assert holder.try_acquire()
    with TestClient(create_app(paths, fake_engine, start_worker=False)) as client:
        assert client.get(f"/api/songs/{row['id']}").json()["status"] == "running"
    holder.release()


def test_bench_refuses_while_engine_is_busy(paths, fake_engine):
    import pytest

    from songforge.bench import run_bench
    from songforge.engine.base import EngineError

    holder = EngineLock(paths.home / "engine.lock")
    assert holder.try_acquire()
    with pytest.raises(EngineError, match="Another songforge job"):
        run_bench(paths, fake_engine, echo=lambda m: None)
    holder.release()
