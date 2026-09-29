"""Generate the listening-page demos with the real engine and record measured times.

Runs one song at a time through `songforge generate` / `songforge cover`, waits for
normal memory pressure before each run, then writes 128 kbps MP3s and
docs/demos.json with the wall time of every generation.

    uv run python scripts/make_demos.py            # all demos not yet in demos.json
    uv run python scripts/make_demos.py pop-rock   # one demo by id
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
AUDIO = DOCS / "audio"
MANIFEST = DOCS / "demos.json"
ENGINE_EXAMPLES = Path.home() / ".songforge" / "engine" / "mlx-yue" / "examples"

POP_LYRICS = (ROOT / "src" / "songforge" / "bench.py").read_text().split('BENCH_LYRICS = """')[1]
POP_LYRICS = POP_LYRICS.split('"""')[0]

JINGLE_LYRICS = """[Verse]
Dashing through the snow
In a one-horse open sleigh
O'er the fields we go
Laughing all the way
Bells on bobtail ring
Making spirits bright
What fun it is to ride and sing
A sleighing song tonight

[Chorus]
Jingle bells, jingle bells
Jingle all the way
Oh what fun it is to ride
In a one-horse open sleigh
Jingle bells, jingle bells
Jingle all the way
Oh what fun it is to ride
In a one-horse open sleigh
"""

AULD_LYRICS = """[Verse]
Should old acquaintance be forgot
And never brought to mind
Should old acquaintance be forgot
And days of auld lang syne

[Chorus]
For auld lang syne, my dear
For auld lang syne
We'll take a cup of kindness yet
For auld lang syne
"""

NEON_LYRICS = """[Verse]
Paper lanterns on the water
Every light a little prayer
Mother humming to her daughter
Summer thunder in the air

[Chorus]
Carry me home on the river
Carry me home on the tide
Whatever the years deliver
I will have you by my side

[Verse]
Fireflies above the reeds now
Writing letters in the dark
Every wish is all we need now
Every ember is a spark

[Chorus]
Carry me home on the river
Carry me home on the tide
Whatever the years deliver
I will have you by my side
"""


def tonight_awake() -> dict:
    path = ENGINE_EXAMPLES / "full-song.json"
    data = json.loads(path.read_text())
    return {"style": data["style"], "lyrics": data["lyrics"], "seed": data["seed"]}


DEMOS = [
    {
        "id": "pop-rock",
        "kind": "song",
        "title": "Hold On (the night is young)",
        "style": "English, upbeat pop rock, bright female vocal, electric guitar, bass, "
        "punchy drums, catchy chorus, 118 BPM",
        "lyrics": POP_LYRICS,
        "seed": 42,
        "mode": "fast",
        "max_seconds": 240,
        "note": "English pop rock with the lyrics from `songforge bench`.",
    },
    {
        "id": "tonight-awake",
        "kind": "song",
        "title": "Tonight Awake (official YuE2 prompt)",
        "official": True,
        "mode": "fast",
        "note": "The official tonight_awake City Pop prompt and seed from the YuE2 repo, "
        "Mandarin lyrics, so you can compare with the m-a-p demo.",
    },
    {
        "id": "synthwave-instrumental",
        "kind": "song",
        "title": "Night Drive (instrumental)",
        "style": "synthwave, retro 80s, analog synth arpeggios, gated reverb drums, "
        "driving bassline, neon night drive, 100 BPM",
        "instrumental": True,
        "seed": 7,
        "mode": "fast",
        "max_seconds": 120,
        "note": "Instrumental switch on.",
    },
    {
        "id": "river-folk",
        "kind": "song",
        "title": "Carry Me Home",
        "style": "English, acoustic folk, warm male vocal, fingerpicked acoustic guitar, "
        "soft cello, intimate, 84 BPM",
        "lyrics": NEON_LYRICS,
        "seed": 1234,
        "mode": "hq",
        "note": "HQ mode (32 acoustic steps).",
    },
    {
        "id": "jingle-metal",
        "kind": "cover",
        "title": "Jingle Bells (heavy metal cover)",
        "source": "jingle-bells",
        "style": "heavy metal, distorted guitars, double-kick drums, powerful male vocal, "
        "high energy, 160 BPM",
        "lyrics": JINGLE_LYRICS,
        "seed": 99,
        "mode": "fast",
        "note": "Source: Jingle Bells (James Lord Pierpont, 1857, public domain) rendered as "
        "plain piano by scripts/make_source.py.",
    },
    {
        "id": "auld-jazz-funk",
        "kind": "cover",
        "title": "Auld Lang Syne (jazz-funk cover)",
        "source": "auld-lang-syne",
        "style": "jazz-funk, slap bass, Rhodes piano, tight horns, groovy drums, soulful "
        "female vocal, 100 BPM",
        "lyrics": AULD_LYRICS,
        "seed": 5,
        "mode": "fast",
        "note": "Source: Auld Lang Syne (traditional, public domain) rendered as plain piano.",
    },
]


ORDER = [
    "pop-rock",
    "synthwave-instrumental",
    "jingle-metal",
    "tonight-awake",
    "river-folk",
    "auld-jazz-funk",
]


def pressure_ok() -> bool:
    out = subprocess.run(
        ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True
    ).stdout.strip()
    return out == "1"


def wait_for_memory(limit: float = 900) -> None:
    start = time.time()
    while not pressure_ok():
        if time.time() - start > limit:
            print("memory pressure stayed high, starting anyway (the engine guard still applies)")
            return
        print("waiting for normal memory pressure...", flush=True)
        time.sleep(20)


def run(cmd: list[str]) -> dict:
    print("$", " ".join(c if len(c) < 60 else c[:57] + "..." for c in cmd), flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout)


def make(demo: dict) -> dict:
    AUDIO.mkdir(parents=True, exist_ok=True)
    if demo.get("official"):
        demo = {**demo, **tonight_awake()}
    base = ["uv", "run", "songforge"]
    wait_for_memory()
    started = time.perf_counter()
    if demo["kind"] == "cover":
        source_wav = ROOT / "demo" / "raw" / f"{demo['source']}.wav"
        if not source_wav.is_file():
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "make_source.py"),
                    demo["source"],
                    str(source_wav),
                ],
                check=True,
            )
        source_mp3 = AUDIO / f"{demo['source']}-source.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(source_wav), "-b:a", "96k", str(source_mp3)],
            check=True,
        )
        cmd = [
            *base,
            "cover",
            str(source_wav),
            "--style",
            demo["style"],
            "--lyrics",
            demo["lyrics"],
            "--seed",
            str(demo["seed"]),
            "--mode",
            demo["mode"],
            "--title",
            demo["title"],
            "--max-seconds",
            str(demo.get("max_seconds", 180)),
            "--json",
        ]
    else:
        cmd = [
            *base,
            "generate",
            "--style",
            demo["style"],
            "--seed",
            str(demo["seed"]),
            "--mode",
            demo["mode"],
            "--title",
            demo["title"],
            "--max-seconds",
            str(demo.get("max_seconds", 240)),
            "--json",
        ]
        cmd += ["--instrumental"] if demo.get("instrumental") else ["--lyrics", demo["lyrics"]]
    result = run(cmd)
    outer = time.perf_counter() - started
    return finish(demo, result, outer)


def adopt(demo: dict, library_id: str) -> dict:
    """Record a song that already finished in the library instead of generating it again."""
    rows = json.loads(
        subprocess.run(
            ["uv", "run", "songforge", "list", "--json", "--limit", "500"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    row = next(r for r in rows if r["id"] == library_id)
    if row["status"] != "done":
        raise RuntimeError(f"{library_id} is {row['status']}")
    home = Path.home() / ".songforge" / "songs" / library_id
    result = {
        "id": library_id,
        "path": str(home / "audio.flac"),
        "seconds": row["seconds"],
        "wall_seconds": row["wall_ms"] / 1000,
        "peak_rss_bytes": row.get("peak_rss"),
        "peak_footprint_bytes": row.get("peak_footprint"),
        "truncated": row["truncated"],
    }
    if demo.get("official"):
        demo = {**demo, **tonight_awake()}
    if demo["kind"] == "cover":
        source_wav = ROOT / "demo" / "raw" / f"{demo['source']}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(source_wav),
                "-b:a",
                "96k",
                str(AUDIO / f"{demo['source']}-source.mp3"),
            ],
            check=True,
        )
    return finish(demo, result, None)


def finish(demo: dict, result: dict, outer: float | None) -> dict:
    AUDIO.mkdir(parents=True, exist_ok=True)
    mp3 = AUDIO / f"{demo['id']}.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            result["path"],
            "-b:a",
            "128k",
            "-metadata",
            f"title={demo['title']}",
            str(mp3),
        ],
        check=True,
    )
    size = mp3.stat().st_size
    if size > 5 * 1024 * 1024:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", result["path"], "-b:a", "96k", str(mp3)],
            check=True,
        )
        size = mp3.stat().st_size
    return {
        "id": demo["id"],
        "kind": demo["kind"],
        "title": demo["title"],
        "style": demo["style"],
        "lyrics": "" if demo.get("official") else demo.get("lyrics", ""),
        "instrumental": bool(demo.get("instrumental")),
        "seed": demo["seed"],
        "mode": demo["mode"],
        "note": demo["note"],
        "audio": f"audio/{mp3.name}",
        "source_audio": f"audio/{demo['source']}-source.mp3" if demo["kind"] == "cover" else None,
        "audio_seconds": result["seconds"],
        "wall_seconds": round(result["wall_seconds"], 1),
        "cli_wall_seconds": round(outer, 1) if outer else None,
        "peak_rss_bytes": result["peak_rss_bytes"],
        "peak_footprint_bytes": result.get("peak_footprint_bytes"),
        "truncated": result["truncated"],
        "mp3_bytes": size,
        "library_id": result["id"],
        "measured": time.strftime("%Y-%m-%d %H:%M"),
    }


def save(entry: dict) -> None:
    done = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    done = [d for d in done if d["id"] != entry["id"]] + [entry]
    order = [d["id"] for d in DEMOS]
    done.sort(key=lambda d: order.index(d["id"]) if d["id"] in order else 99)
    MANIFEST.write_text(json.dumps(done, indent=2) + "\n")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--adopt"]:
        demo_id, library_id = argv[1].split("=", 1)
        entry = adopt({d["id"]: d for d in DEMOS}[demo_id], library_id)
        save(entry)
        print(f"adopted {library_id} as {demo_id}: {entry['wall_seconds']} s")
        return 0
    done = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    have = {d["id"] for d in done}
    by_id = {d["id"]: d for d in DEMOS}
    wanted = [by_id[i] for i in ORDER if (not argv and i not in have) or i in argv]
    for demo in wanted:
        print(f"== {demo['id']}", flush=True)
        try:
            entry = make(demo)
        except (RuntimeError, subprocess.CalledProcessError, KeyError) as error:
            print(f"failed: {error}", flush=True)
            continue
        save(entry)
        print(
            f"   {entry['audio_seconds']:.0f} s of audio in {entry['wall_seconds']:.0f} s",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
