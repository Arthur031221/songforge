from __future__ import annotations

import pytest

from songforge.config import Paths


@pytest.fixture
def paths(tmp_path, monkeypatch) -> Paths:
    home = tmp_path / "home"
    monkeypatch.setenv("SONGFORGE_HOME", str(home))
    # Keep tests away from any real Ollama server.
    monkeypatch.setenv("SONGFORGE_OLLAMA_URL", "http://127.0.0.1:9")
    p = Paths(home)
    p.ensure()
    return p


@pytest.fixture
def fake_engine(paths):
    from songforge.engine.fake import FakeEngine

    return FakeEngine(paths, seconds=1.0, delay=0)


@pytest.fixture
def app_client(paths, fake_engine):
    from fastapi.testclient import TestClient

    from songforge.server import create_app

    app = create_app(paths, fake_engine, start_worker=False)
    with TestClient(app) as client:
        client.app_state = app.state
        yield client
