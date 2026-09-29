"""Command line: `songforge` starts the studio, the subcommands script it."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx

from . import __version__, config
from .config import get_paths

HELP = """Local song studio for Apple Silicon. YuE2-3B through MLX.

Run with no command to start the studio at http://127.0.0.1:7860
"""


def engine_name(args) -> str:
    return getattr(args, "engine", None) or os.environ.get("SONGFORGE_ENGINE", "mlx")


def out(args, data, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def fail(message: str, code: int = 1) -> int:
    print(f"songforge: {message}", file=sys.stderr)
    return code


def server_url(port: int) -> str | None:
    url = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(f"{url}/api/status", timeout=0.8)
        if r.status_code == 200 and "engine" in r.json():
            return url
    except (httpx.HTTPError, ValueError):
        return None
    return None


# serve -------------------------------------------------------------------------
def cmd_serve(args) -> int:
    import uvicorn

    from .engine import get_engine
    from .install import SetupError, run_setup
    from .server import create_app

    paths = get_paths()
    paths.ensure()
    name = engine_name(args)
    engine = get_engine(name, paths)
    if name == "mlx" and not engine.ready() and not args.skip_setup:
        from .install import gib

        print(
            f"First run: songforge downloads the engine and about "
            f"{gib(config.SONG_DOWNLOAD_BYTES)} of weights to {paths.home}"
        )
        if not args.yes and sys.stdin.isatty():
            answer = input("Continue? [Y/n] ").strip().lower()
            if answer not in ("", "y", "yes"):
                print("Skipped. The studio opens without an engine. Run `songforge setup` later.")
                args.skip_setup = True
        if not args.skip_setup:
            try:
                run_setup(paths)
            except SetupError as error:
                print(f"Setup failed: {error}", file=sys.stderr)
                print(
                    "The studio still opens. Fix the problem and run `songforge setup`.",
                    file=sys.stderr,
                )
    if server_url(args.port):
        url = f"http://127.0.0.1:{args.port}"
        print(f"songforge is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    app = create_app(paths, engine)
    url = f"http://{args.host}:{args.port}"
    print(f"songforge {__version__} studio: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


# setup and doctor ------------------------------------------------------------------
def cmd_setup(args) -> int:
    from .install import SetupError, run_setup

    try:
        info = run_setup(
            get_paths(), echo=(lambda _m: None) if args.json else print, covers=args.covers
        )
    except SetupError as error:
        return fail(str(error))
    out(args, {"ready": True, **info}, "Done. Start the studio with: songforge")
    return 0


def cmd_doctor(args) -> int:
    from .engine import get_engine
    from .install import disk_usage, engine_installed, gib
    from .lyrics import status as lyric_status

    paths = get_paths()
    engine = get_engine(engine_name(args), paths)
    st = engine.status()
    usage = disk_usage(paths)
    report = {
        "version": __version__,
        "home": str(paths.home),
        "engine": st,
        "engine_commit": config.MLX_ENGINE_COMMIT,
        "engine_pinned": engine_installed(paths),
        "ffmpeg": shutil.which("ffmpeg"),
        "lyrics": lyric_status(),
        "disk_bytes": usage,
    }
    ok = st["ready"]

    def mark(flag: bool) -> str:
        return "ok " if flag else "no "

    lines = [
        f"songforge {__version__}  home {paths.home}",
        f"{mark(st['apple_silicon'])} Apple Silicon Mac",
        f"{mark(st['installed'])} engine installed (mlx-Yue {config.MLX_ENGINE_COMMIT[:7]})",
        f"{mark(st['models'])} song weights"
        + ("" if st["models"] else f"  missing: {', '.join(st['missing'])}"),
        f"{mark(st['covers'])} cover weights (downloaded on first cover)",
        f"{mark(bool(report['ffmpeg']))} ffmpeg (MP3 export and covers)",
        f"{mark(report['lyrics']['available'])} lyric writer ({report['lyrics']['model']} via Ollama)"
        + ("" if report["lyrics"]["available"] else f"  {report['lyrics'].get('reason', '')}"),
        f"disk: models {gib(usage['models'])}, engine {gib(usage['engine'])}, "
        f"songs {gib(usage['songs'])}",
    ]
    if not ok:
        lines.append("Run `songforge setup` to finish installing.")
    out(args, report, "\n".join(lines))
    return 0 if ok else 1


# jobs from the command line -----------------------------------------------------------
def _read_lyrics(args) -> str:
    if getattr(args, "lyrics_file", None):
        return Path(args.lyrics_file).read_text()
    return (args.lyrics or "").replace("\\n", "\n")


def _run_local(song: dict, args) -> dict:
    """Run one job in this process with the same worker the studio uses."""
    from .db import Store
    from .engine import get_engine
    from .worker import Worker

    paths = get_paths()
    paths.ensure()
    engine = get_engine(engine_name(args), paths)
    if not engine.ready():
        raise SystemExit(fail("The engine is not ready. Run `songforge setup` first."))
    store = Store(paths.db)
    worker = Worker(store, engine, paths)
    row = store.create(**song)
    shown = {"detail": ""}

    def watch():
        while True:
            current = store.get(row["id"])
            if not current or current["status"] not in ("queued", "running"):
                return
            if current["detail"] and current["detail"] != shown["detail"] and not args.json:
                shown["detail"] = current["detail"]
                print(f"  {int(current['progress'] * 100):3d}%  {current['detail']}", flush=True)
            time.sleep(1)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        worker._process(store.get(row["id"]))
    except KeyboardInterrupt:
        worker.cancel(row["id"])
        raise
    watcher.join(timeout=2)
    result = store.get(row["id"])
    store.close()
    return result


def _run_remote(url: str, endpoint: str, body: dict, args) -> dict:
    r = httpx.post(f"{url}{endpoint}", json=body, timeout=30)
    if r.status_code >= 400:
        raise SystemExit(fail(r.json().get("detail", r.text)))
    song = r.json()
    if not args.json:
        print(f"Queued in the running studio at {url} as {song['id']}")
    shown = ""
    while True:
        song = httpx.get(f"{url}/api/songs/{song['id']}", timeout=10).json()
        if song["detail"] and song["detail"] != shown and not args.json:
            shown = song["detail"]
            print(f"  {int(song['progress'] * 100):3d}%  {shown}", flush=True)
        if song["status"] not in ("queued", "running"):
            break
        time.sleep(1)
    paths = get_paths()
    song["path"] = str(paths.songs / song["id"] / "audio.flac") if song.get("has_flac") else None
    return song


def _finish_job(song: dict, args) -> int:
    if song["status"] != "done":
        return fail(song.get("error") or f"Job ended with status {song['status']}")
    target = None
    if args.out and song.get("path"):
        src = Path(song["path"])
        dest = Path(args.out)
        if dest.suffix.lower() == ".mp3":
            src = src.with_suffix(".mp3")
        if not src.is_file():
            return fail(f"No {dest.suffix} file was produced (is ffmpeg installed?)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        target = str(dest)
    data = {
        "id": song["id"],
        "title": song["title"],
        "seconds": song.get("seconds"),
        "wall_seconds": (song.get("wall_ms") or 0) / 1000,
        "seed": song["seed"],
        "mode": song["mode"],
        "peak_rss_bytes": song.get("peak_rss"),
        "truncated": song.get("truncated"),
        "path": song.get("path"),
        "out": target,
    }
    from .bench import fmt_bytes, fmt_duration

    text = (
        f"Done: {song['title']}  {fmt_duration(song.get('seconds'))} of audio in "
        f"{fmt_duration(data['wall_seconds'])}, peak RSS {fmt_bytes(song.get('peak_rss'))}\n"
        f"  {target or song.get('path')}"
    )
    out(args, data, text)
    return 0


def cmd_generate(args) -> int:
    lyric_text = _read_lyrics(args)
    if not lyric_text.strip() and not args.instrumental:
        return fail("Pass --lyrics, --lyrics-file or --instrumental")
    body = {
        "title": args.title or "",
        "style": args.style,
        "lyrics": lyric_text,
        "instrumental": args.instrumental,
        "mode": args.mode,
        "seed": args.seed,
        "max_seconds": args.max_seconds,
    }
    url = server_url(args.port) if engine_name(args) == "mlx" else None
    if url:
        return _finish_job(_run_remote(url, "/api/songs", body, args), args)
    from .server import default_title, pick_seed

    song = {
        **body,
        "kind": "song",
        "instrumental": int(args.instrumental),
        "title": args.title or default_title(args.style, lyric_text),
        "seed": pick_seed(args.seed),
    }
    return _finish_job(_run_local(song, args), args)


def cmd_cover(args) -> int:
    source = Path(args.audio)
    if not source.is_file():
        return fail(f"No such file: {source}")
    if not shutil.which("ffmpeg"):
        return fail("Covers need ffmpeg. Install it with: brew install ffmpeg")
    lyric_text = _read_lyrics(args)
    paths = get_paths()
    paths.ensure()
    import uuid

    upload_id = uuid.uuid4().hex[:16]
    stored = paths.uploads / f"{upload_id}{source.suffix.lower()}"
    shutil.copyfile(source, stored)
    (paths.uploads / f"{upload_id}.name").write_text(source.name)
    abc = Path(args.abc).read_text() if args.abc else None
    from .server import pick_seed

    song = {
        "kind": "cover",
        "title": args.title or f"{source.stem} ({args.style.split(',')[0]})",
        "style": args.style,
        "lyrics": lyric_text,
        "mode": args.mode,
        "seed": pick_seed(args.seed),
        "max_seconds": args.max_seconds,
        "source_name": source.name,
        "source_path": str(stored),
        "task": args.task,
        "abc": abc,
    }
    url = server_url(args.port) if engine_name(args) == "mlx" else None
    if url:
        body = {
            k: song[k]
            for k in ("title", "style", "lyrics", "mode", "seed", "max_seconds", "task", "abc")
        }
        body["upload_id"] = upload_id
        return _finish_job(_run_remote(url, "/api/covers", body, args), args)
    return _finish_job(_run_local(song, args), args)


def cmd_list(args) -> int:
    from .bench import fmt_duration
    from .db import Store
    from .worker import public

    paths = get_paths()
    if not paths.db.exists():
        out(args, [], "The library is empty. Start the studio with: songforge")
        return 0
    store = Store(paths.db)
    songs = [public(s) for s in store.list(limit=args.limit)]
    store.close()
    if not songs:
        out(args, [], "The library is empty. Start the studio with: songforge")
        return 0
    lines = [f"{'id':12}  {'status':9}  {'kind':10}  {'length':7}  {'took':8}  title"]
    for s in songs:
        took = fmt_duration((s.get("wall_ms") or 0) / 1000) if s.get("wall_ms") else ""
        length = fmt_duration(s.get("seconds")) if s.get("seconds") else ""
        lines.append(
            f"{s['id']:12}  {s['status']:9}  {s['kind']:10}  {length:7}  {took:8}  {s['title']}"
        )
    out(args, songs, "\n".join(lines))
    return 0


def cmd_bench(args) -> int:
    from .bench import BENCH_LYRICS, BENCH_STYLE, load_request, render_table, run_bench
    from .engine import get_engine
    from .engine.base import EngineError

    paths = get_paths()
    if server_url(args.port):
        return fail("The studio is running. Stop it first so the bench runs alone.")
    engine = get_engine(engine_name(args), paths)
    if not engine.ready():
        return fail("The engine is not ready. Run `songforge setup` first.")
    request = {"style": BENCH_STYLE, "lyrics": BENCH_LYRICS, "seed": args.seed}
    if args.request:
        request = load_request(Path(args.request))
    if not args.json:
        print(
            f"Benchmarking one song ({args.mode}, up to {args.max_seconds} s). "
            "This loads the full model."
        )
    try:
        report = run_bench(
            paths,
            engine,
            mode=args.mode,
            max_seconds=args.max_seconds,
            seed=request["seed"],
            style=request["style"],
            lyrics=request["lyrics"],
            echo=(lambda _m: None) if args.json else print,
        )
    except EngineError as error:
        return fail(str(error))
    out(args, report, render_table(report))
    return 0


# parser ------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="songforge", description=HELP, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"songforge {__version__}")
    sub = parser.add_subparsers(dest="command")

    def common(p, json_flag=True, engine=True):
        if json_flag:
            p.add_argument("--json", action="store_true", help="print machine-readable JSON")
        if engine:
            p.add_argument(
                "--engine",
                choices=("mlx", "fake"),
                default=None,
                help="engine to use (default mlx, or SONGFORGE_ENGINE)",
            )
        p.add_argument(
            "--port",
            type=int,
            default=config.DEFAULT_PORT,
            help=f"studio port (default {config.DEFAULT_PORT})",
        )

    serve = sub.add_parser("serve", help="start the studio in the browser (default command)")
    common(serve, json_flag=False)
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    serve.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    serve.add_argument("--yes", "-y", action="store_true", help="download weights without asking")
    serve.add_argument(
        "--skip-setup", action="store_true", help="start even if weights are missing"
    )
    serve.set_defaults(func=cmd_serve)

    setup = sub.add_parser("setup", help="install the engine and download the weights")
    setup.add_argument(
        "--covers", action="store_true", help="also download the cover models now (about 2.5 GB)"
    )
    setup.add_argument("--json", action="store_true", help="print machine-readable JSON")
    setup.set_defaults(func=cmd_setup)

    doctor = sub.add_parser("doctor", help="check the engine, weights, ffmpeg and Ollama")
    common(doctor)
    doctor.set_defaults(func=cmd_doctor)

    def song_args(p):
        p.add_argument(
            "--style", required=True, help='style prompt, e.g. "indie pop, female vocal"'
        )
        group = p.add_mutually_exclusive_group()
        group.add_argument("--lyrics", help="lyrics text, use \\n for new lines")
        group.add_argument("--lyrics-file", help="read lyrics from a file")
        p.add_argument("--title", help="title shown in the library")
        p.add_argument(
            "--mode",
            choices=tuple(config.MODES),
            default="fast",
            help="fast = 8 acoustic steps, hq = 32 steps (default fast)",
        )
        p.add_argument("--seed", type=int, default=None, help="random seed (default random)")
        p.add_argument(
            "--max-seconds",
            type=int,
            default=240,
            help="length cap in seconds, 20 to 360 (default 240)",
        )
        p.add_argument("--out", help="copy the result here (.flac or .mp3)")

    gen = sub.add_parser("generate", help="make one song from a style and lyrics")
    song_args(gen)
    gen.add_argument("--instrumental", action="store_true", help="no vocals")
    common(gen)
    gen.set_defaults(func=cmd_generate)

    cover = sub.add_parser("cover", help="cover an audio file in a new style")
    cover.add_argument("audio", help="source audio (mp3, wav, flac, m4a)")
    song_args(cover)
    cover.add_argument(
        "--task",
        choices=("melody-full", "melody-vocal"),
        default="melody-full",
        help="melody-full keeps vocal and instrumental themes (default)",
    )
    cover.add_argument("--abc", help="skip transcription and use this ABC score")
    common(cover)
    cover.set_defaults(func=cmd_cover)

    lst = sub.add_parser("list", help="list the library")
    lst.add_argument("--limit", type=int, default=50, help="rows to show (default 50)")
    lst.add_argument("--json", action="store_true", help="print machine-readable JSON")
    lst.set_defaults(func=cmd_list)

    bench = sub.add_parser("bench", help="time one song: wall time, real-time factor, peak RSS")
    bench.add_argument(
        "--mode", choices=tuple(config.MODES), default="fast", help="fast or hq (default fast)"
    )
    bench.add_argument(
        "--max-seconds", type=int, default=240, help="length cap in seconds (default 240)"
    )
    bench.add_argument("--seed", type=int, default=42, help="seed (default 42)")
    bench.add_argument("--request", help="JSON file with style, lyrics and seed to bench instead")
    common(bench)
    bench.set_defaults(func=cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["serve", *(argv if argv is not None else sys.argv[1:])])
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
