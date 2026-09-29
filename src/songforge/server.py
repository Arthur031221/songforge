"""HTTP API and the single-page studio."""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, audio, config, lyrics
from .db import Store
from .engine import Engine
from .events import Broker, format_sse
from .worker import Worker, public

STATIC = Path(__file__).with_name("static")
UPLOAD_TYPES = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".aif", ".aiff", ".opus"}
MAX_UPLOAD = 200 * 1024 * 1024


class SongIn(BaseModel):
    title: str = Field("", max_length=120)
    style: str = Field(..., min_length=1, max_length=600)
    lyrics: str = Field("", max_length=6000)
    instrumental: bool = False
    mode: str = "fast"
    seed: int | None = None
    max_seconds: int = Field(240, ge=20, le=360)


class CoverIn(BaseModel):
    upload_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field("", max_length=120)
    style: str = Field(..., min_length=1, max_length=600)
    lyrics: str = Field("", max_length=6000)
    mode: str = "fast"
    seed: int | None = None
    max_seconds: int = Field(240, ge=20, le=360)
    task: str = "melody-full"
    abc: str | None = Field(None, max_length=60000)


class TranscribeIn(BaseModel):
    upload_id: str = Field(..., min_length=1, max_length=64)
    task: str = "melody-full"


class LyricsIn(BaseModel):
    topic: str = Field("", max_length=400)
    style: str = Field("", max_length=600)


def default_title(style: str, lyric_text: str) -> str:
    for line in lyric_text.splitlines():
        line = line.strip()
        if line and not line.startswith("["):
            return line[:60]
    return (style.split(",")[0].strip() or "Untitled").title()[:60]


def pick_seed(seed: int | None) -> int:
    if seed is None or seed < 0:
        return random.randint(1, 2**31 - 1)
    if seed >= 2**63:
        raise HTTPException(422, "Seed must be below 2^63")
    return seed


def check_mode(mode: str) -> str:
    if mode not in config.MODES:
        raise HTTPException(422, f"Mode must be one of: {', '.join(config.MODES)}")
    return mode


def check_task(task: str) -> str:
    if task not in ("melody-full", "melody-vocal"):
        raise HTTPException(422, "Task must be melody-full or melody-vocal")
    return task


def create_app(paths, engine: Engine, *, start_worker: bool = True) -> FastAPI:
    paths.ensure()
    store = Store(paths.db)
    store.recover()
    broker = Broker()
    worker = Worker(store, engine, paths, broker)
    lyric_cache: dict = {"at": 0.0, "value": None}

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            worker.start()
        yield
        worker.stop()
        store.close()

    app = FastAPI(title="songforge", version=__version__, lifespan=lifespan)
    app.state.store, app.state.worker, app.state.broker = store, worker, broker
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def lyric_status() -> dict:
        if time.time() - lyric_cache["at"] > 30 or lyric_cache["value"] is None:
            lyric_cache["value"] = lyrics.status()
            lyric_cache["at"] = time.time()
        return lyric_cache["value"]

    def get_song(song_id: str) -> dict:
        song = store.get(song_id)
        if not song:
            raise HTTPException(404, "No such song")
        return song

    def enqueue(**fields) -> dict:
        song = store.create(**fields)
        worker.wake()
        data = public(song)
        data["queue_position"] = store.queue_position(song["id"])
        broker.publish("song", data)
        return data

    def upload_path(upload_id: str) -> tuple[Path, str]:
        if not re.fullmatch(r"[a-f0-9]{8,32}", upload_id):
            raise HTTPException(422, "Bad upload id")
        matches = list(paths.uploads.glob(f"{upload_id}.*"))
        if not matches:
            raise HTTPException(404, "Upload not found. Drop the file again.")
        meta = paths.uploads / f"{upload_id}.name"
        name = meta.read_text() if meta.is_file() else matches[0].name
        files = [m for m in matches if m.suffix != ".name"]
        if not files:
            raise HTTPException(404, "Upload not found. Drop the file again.")
        return files[0], name

    # Pages -------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text())

    # Status ------------------------------------------------------------------
    @app.get("/api/status")
    def status() -> dict:
        queued = [s for s in store.list(limit=500) if s["status"] == "queued"]
        return {
            "version": __version__,
            "engine": engine.status(),
            "queue": len(queued),
            "running": worker.current,
            "lyrics": lyric_status(),
            "modes": config.MODES,
        }

    # Songs -------------------------------------------------------------------
    @app.get("/api/songs")
    def list_songs(kind: str | None = None) -> list[dict]:
        return [public(s) for s in store.list(kind=kind)]

    @app.post("/api/songs", status_code=201)
    def create_song(body: SongIn) -> dict:
        if not body.instrumental and not body.lyrics.strip():
            raise HTTPException(422, "Add lyrics or switch on Instrumental")
        return enqueue(
            kind="song",
            title=body.title.strip() or default_title(body.style, body.lyrics),
            style=body.style.strip(),
            lyrics=body.lyrics,
            instrumental=int(body.instrumental),
            mode=check_mode(body.mode),
            seed=pick_seed(body.seed),
            max_seconds=body.max_seconds,
        )

    @app.get("/api/songs/{song_id}")
    def read_song(song_id: str) -> dict:
        data = public(get_song(song_id))
        data["queue_position"] = store.queue_position(song_id)
        return data

    @app.delete("/api/songs/{song_id}")
    def delete_song(song_id: str) -> dict:
        song = get_song(song_id)
        if song["status"] == "running":
            raise HTTPException(409, "Cancel the job before deleting it")
        store.delete(song_id)
        shutil.rmtree(paths.songs / song_id, ignore_errors=True)
        broker.publish("deleted", {"id": song_id})
        return {"deleted": song_id}

    @app.post("/api/songs/{song_id}/cancel")
    def cancel_song(song_id: str) -> dict:
        get_song(song_id)
        if not worker.cancel(song_id):
            raise HTTPException(409, "Only queued or running jobs can be cancelled")
        return {"cancelled": song_id}

    @app.get("/api/songs/{song_id}/audio.{fmt}")
    def song_audio(song_id: str, fmt: str, download: int = 0):
        song = get_song(song_id)
        if not song.get("path"):
            raise HTTPException(404, "This job has no audio yet")
        base = Path(song["path"])
        path = base if fmt == base.suffix.lstrip(".") else base.with_suffix(f".{fmt}")
        if fmt not in ("flac", "mp3", "wav") or not path.is_file():
            raise HTTPException(404, f"No {fmt} file for this song")
        media = {"flac": "audio/flac", "mp3": "audio/mpeg", "wav": "audio/wav"}[fmt]
        name = f"{slug(song['title'])}.{fmt}" if download else None
        return FileResponse(path, media_type=media, filename=name)

    @app.get("/api/songs/{song_id}/score.abc")
    def song_score(song_id: str, download: int = 0):
        song = get_song(song_id)
        if not song.get("abc"):
            raise HTTPException(404, "This song has no score")
        headers = {}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{slug(song["title"])}.abc"'
        return PlainTextResponse(song["abc"], headers=headers)

    # Covers ------------------------------------------------------------------
    @app.post("/api/uploads", status_code=201)
    async def upload(request: Request, name: str = "audio.mp3") -> dict:
        suffix = Path(name).suffix.lower()
        if suffix not in UPLOAD_TYPES:
            raise HTTPException(415, f"Unsupported file type {suffix or '(none)'}")
        upload_id = uuid.uuid4().hex[:16]
        dest = paths.uploads / f"{upload_id}{suffix}"
        size = 0
        with dest.open("wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, "File is larger than 200 MB")
                fh.write(chunk)
        if size == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(422, "The file is empty")
        (paths.uploads / f"{upload_id}.name").write_text(Path(name).name[:200])
        seconds = None
        with contextlib.suppress(Exception):
            seconds = round(audio.duration(dest), 2)
        return {"id": upload_id, "name": Path(name).name, "bytes": size, "seconds": seconds}

    @app.get("/api/uploads/{upload_id}")
    def read_upload(upload_id: str):
        path, _name = upload_path(upload_id)
        return FileResponse(path)

    @app.post("/api/transcribe", status_code=201)
    def transcribe(body: TranscribeIn) -> dict:
        path, name = upload_path(body.upload_id)
        return enqueue(
            kind="transcribe",
            title=f"Score of {name}",
            style="",
            lyrics="",
            mode="fast",
            seed=0,
            source_name=name,
            source_path=str(path),
            task=check_task(body.task),
        )

    @app.post("/api/covers", status_code=201)
    def create_cover(body: CoverIn) -> dict:
        path, name = upload_path(body.upload_id)
        return enqueue(
            kind="cover",
            title=body.title.strip() or f"{Path(name).stem} ({body.style.split(',')[0].strip()})",
            style=body.style.strip(),
            lyrics=body.lyrics,
            mode=check_mode(body.mode),
            seed=pick_seed(body.seed),
            max_seconds=body.max_seconds,
            source_name=name,
            source_path=str(path),
            task=check_task(body.task),
            abc=body.abc,
        )

    # Lyrics ------------------------------------------------------------------
    @app.get("/api/lyrics/status")
    def lyrics_status() -> dict:
        return lyric_status()

    @app.post("/api/lyrics")
    def write_lyrics(body: LyricsIn) -> dict:
        if worker.current:
            raise HTTPException(409, "A song is rendering. Write lyrics when the engine is idle.")
        try:
            return {"lyrics": lyrics.write(body.topic, body.style)}
        except lyrics.LyricsError as error:
            raise HTTPException(502, str(error)) from error

    # Events ------------------------------------------------------------------
    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = broker.subscribe()

        async def stream():
            try:
                yield format_sse("hello", {"version": __version__, "running": worker.current})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15)
                        yield format_sse(event, data)
                    except TimeoutError:
                        yield ": ping\n\n"
            finally:
                broker.unsubscribe(queue)

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    return app


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:60] or "song"
