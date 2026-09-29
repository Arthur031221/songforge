import asyncio
import io
import wave

import pytest

from songforge import lyrics
from songforge.events import Broker, format_sse


def run_queue(client):
    worker = client.app_state.worker
    while worker.run_once():
        pass


def wav_bytes(seconds=0.5, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate))
    return buf.getvalue()


def test_index_and_static(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    assert "songforge" in r.text and "EventSource" in r.text
    assert app_client.get("/static/abcjs-basic-min.js").status_code == 200


def test_status(app_client):
    data = app_client.get("/api/status").json()
    assert data["engine"]["ready"] is True
    assert data["queue"] == 0
    assert data["lyrics"]["available"] is False
    assert set(data["modes"]) == {"fast", "hq"}


def test_create_song_validation(app_client):
    assert app_client.post("/api/songs", json={"style": "pop", "lyrics": ""}).status_code == 422
    assert app_client.post("/api/songs", json={"lyrics": "x"}).status_code == 422
    r = app_client.post("/api/songs", json={"style": "pop", "lyrics": "x", "mode": "turbo"})
    assert r.status_code == 422
    r = app_client.post("/api/songs", json={"style": "pop", "lyrics": "x", "max_seconds": 5})
    assert r.status_code == 422


def test_song_lifecycle(app_client):
    r = app_client.post(
        "/api/songs",
        json={
            "style": "city pop, female vocal",
            "lyrics": "[Verse]\nNeon rain on the avenue\n",
            "mode": "hq",
            "seed": 5,
        },
    )
    assert r.status_code == 201
    song = r.json()
    assert song["status"] == "queued"
    assert song["title"] == "Neon rain on the avenue"
    assert song["queue_position"] == 1
    assert "path" not in song

    run_queue(app_client)
    done = app_client.get(f"/api/songs/{song['id']}").json()
    assert done["status"] == "done"
    assert done["has_audio"]
    assert done["mode"] == "hq" and done["seed"] == 5

    listed = app_client.get("/api/songs").json()
    assert [s["id"] for s in listed] == [song["id"]]

    fmt = "flac" if done["has_flac"] else "wav"
    audio = app_client.get(f"/api/songs/{song['id']}/audio.{fmt}?download=1")
    assert audio.status_code == 200
    assert "neon-rain-on-the-avenue" in audio.headers["content-disposition"]
    assert app_client.get(f"/api/songs/{song['id']}/audio.ogg").status_code == 404

    score = app_client.get(f"/api/songs/{song['id']}/score.abc?download=1")
    assert score.status_code == 200 and score.text.startswith("X:1")

    assert app_client.delete(f"/api/songs/{song['id']}").status_code == 200
    assert app_client.get(f"/api/songs/{song['id']}").status_code == 404


def test_instrumental_needs_no_lyrics_and_random_seed(app_client):
    r = app_client.post("/api/songs", json={"style": "lofi hip hop", "instrumental": True})
    assert r.status_code == 201
    song = r.json()
    assert song["instrumental"] is True
    assert 0 < song["seed"] < 2**31
    assert song["title"] == "Lofi Hip Hop"


def test_cancel_queued_song(app_client):
    song = app_client.post("/api/songs", json={"style": "pop", "lyrics": "la"}).json()
    assert app_client.post(f"/api/songs/{song['id']}/cancel").status_code == 200
    assert app_client.get(f"/api/songs/{song['id']}").json()["status"] == "cancelled"
    assert app_client.post(f"/api/songs/{song['id']}/cancel").status_code == 409
    assert app_client.post("/api/songs/nope/cancel").status_code == 404


def test_upload_transcribe_and_cover(app_client):
    r = app_client.post("/api/uploads?name=jingle.wav", content=wav_bytes())
    assert r.status_code == 201
    up = r.json()
    assert up["name"] == "jingle.wav" and up["seconds"] == pytest.approx(0.5, abs=0.01)
    assert app_client.get(f"/api/uploads/{up['id']}").status_code == 200

    job = app_client.post("/api/transcribe", json={"upload_id": up["id"]}).json()
    assert job["kind"] == "transcribe" and job["upload_id"] == up["id"]
    run_queue(app_client)
    score = app_client.get(f"/api/songs/{job['id']}").json()
    assert score["status"] == "done" and score["abc"]

    cover = app_client.post(
        "/api/covers",
        json={
            "upload_id": up["id"],
            "style": "heavy metal, male vocal",
            "lyrics": "[Verse]\nDashing",
            "abc": score["abc"],
            "task": "melody-full",
        },
    ).json()
    assert cover["kind"] == "cover"
    assert cover["title"] == "jingle (heavy metal)"
    run_queue(app_client)
    done = app_client.get(f"/api/songs/{cover['id']}").json()
    assert done["status"] == "done" and done["has_audio"]


def test_upload_errors(app_client):
    assert app_client.post("/api/uploads?name=a.exe", content=b"x").status_code == 415
    assert app_client.post("/api/uploads?name=a.mp3", content=b"").status_code == 422
    r = app_client.post("/api/covers", json={"upload_id": "deadbeefdeadbeef", "style": "x"})
    assert r.status_code == 404
    r = app_client.post("/api/covers", json={"upload_id": "../../etc", "style": "x"})
    assert r.status_code == 422
    up = app_client.post("/api/uploads?name=a.wav", content=wav_bytes()).json()
    r = app_client.post("/api/transcribe", json={"upload_id": up["id"], "task": "full"})
    assert r.status_code == 422


def test_lyrics_endpoint(app_client, monkeypatch):
    assert app_client.get("/api/lyrics/status").json()["available"] is False
    monkeypatch.setattr(lyrics, "write", lambda topic, style: "[Verse]\nhello\n")
    r = app_client.post("/api/lyrics", json={"topic": "rain", "style": "pop"})
    assert r.status_code == 200 and r.json()["lyrics"].startswith("[Verse]")

    def fail(topic, style):
        raise lyrics.LyricsError("Ollama request failed")

    monkeypatch.setattr(lyrics, "write", fail)
    assert app_client.post("/api/lyrics", json={"topic": "x"}).status_code == 502


def test_broker_delivers_to_subscribers():
    async def scenario():
        broker = Broker()
        queue = broker.subscribe()
        broker.publish("song", {"id": "a"})
        event, data = await asyncio.wait_for(queue.get(), 1)
        broker.unsubscribe(queue)
        return event, data, broker.subscribers

    assert asyncio.run(scenario()) == ("song", {"id": "a"}, 0)
    assert format_sse("x", {"a": 1}) == 'event: x\ndata: {"a": 1}\n\n'


def test_recover_on_startup(paths, fake_engine):
    from fastapi.testclient import TestClient

    from songforge.db import Store
    from songforge.server import create_app

    store = Store(paths.db)
    row = store.create(
        kind="song", title="t", style="s", lyrics="l", mode="fast", seed=1, max_seconds=60
    )
    store.update(row["id"], status="running")
    store.close()
    app = create_app(paths, fake_engine, start_worker=False)
    with TestClient(app) as client:
        assert client.get(f"/api/songs/{row['id']}").json()["status"] == "failed"
