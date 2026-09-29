# songforge

A local Suno-style song studio for Apple Silicon. Lyrics plus a style prompt become a full song with vocals, and any recording can be covered in a new genre. YuE2-3B runs on the Mac through MLX.

{{HOOK}}

[![CI](https://github.com/Arthur031221/songforge/actions/workflows/ci.yml/badge.svg)](https://github.com/Arthur031221/songforge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-orange.svg)](CHANGELOG.md)

**Listen first:** [arthur031221.github.io/songforge](https://arthur031221.github.io/songforge/) has every demo song and cover, each with its prompt, seed and measured time.

![songforge studio](demo/demo.gif)

## Why

Suno is good and costs $8 a month (Pro) or $24 a month (Premier), and every song lives on someone else's server. YuE2-3B is an open song model that its authors report as competitive with Suno v5, but the official release wants Linux and a 24 GB NVIDIA card. The Mac ports that exist are an engine without a UI, or a UI without covers. I wanted one command that turns my MacBook Air into the whole studio.

## Install

```bash
uvx songforge
```

That is the whole install. The first run installs the MLX engine into its own environment under `~/.songforge`, downloads 10.4 GB of weights, verifies their hashes and opens the studio at http://127.0.0.1:7860. Covers download another 2.8 GB the first time you use them.

Needs an Apple Silicon Mac (M1 or later) on macOS 14.2 or later (26.2 or later on M5), [uv](https://docs.astral.sh/uv/), git, `brew install ffmpeg` for MP3 export and covers, and about 15 GB of free disk. The engine process peaked at {{PEAK}} in my runs on a 24 GB Mac. A 16 GB Mac should fit it with other apps closed, but I have not measured one.

From source:

```bash
git clone https://github.com/Arthur031221/songforge && cd songforge
uv run songforge
```

## Quick start

1. Run `uvx songforge`. Your browser opens the studio.
2. Type a style, for example `indie pop, warm female vocal, jangly guitar, 112 BPM`.
3. Paste lyrics with section tags (`[Verse]`, `[Chorus]`, `[Bridge]`), or press **Write lyrics** if Ollama is running.
4. Press **Create**. The card shows each stage live: Planning, Tokenizing, Synth, Render.
5. Play it in the library, download FLAC or MP3, or open the score.

From a terminal instead:

```bash
songforge generate --style "city pop, female vocal, groovy bass" \
  --lyrics-file lyrics.txt --out song.mp3
songforge cover old-recording.mp3 --style "jazz-funk, Rhodes, horns" \
  --lyrics-file new-words.txt --out cover.mp3
```

If the studio is running, these commands queue in it, so only one model ever loads.

## How it works

```
browser (index.html, no build step)
   |  REST + server-sent events
FastAPI server  --  SQLite job table (~/.songforge/songforge.db)
   |  one worker thread, one job at a time
engine child process (mlx-Yue in its own uv env, Python 3.12, MLX 0.32.2)
   Planning    8-bit AR model writes an ABC score for the lyrics
   Tokenizing  8-bit AR model writes 25 codec tokens per second of audio
   Synth       BF16 acoustic model, flow matching, 8 steps (Fast) or 32 (HQ)
   Render      VAE decode to 48 kHz stereo FLAC, then MP3 with ffmpeg
```

- The engine is [mlx-Yue](https://github.com/vanch007/mlx-Yue), a native MLX port of YuE2 with no PyTorch at runtime. songforge pins commit `9253ed1` and installs it with `uv sync --frozen` in `~/.songforge/engine`, so its exact dependency set never touches your other Python environments.
- Each job runs in a fresh child process. All model memory goes back to macOS when the song is done.
- The 8-bit AR model plans and writes tokens. The acoustic stage conditions on the BF16 AR backbone, so the download includes both AR files (2.7 GB and 4.3 GB), the acoustic model (2.9 GB) and the VAE (0.5 GB).
- Weights are hashed once during setup. Later runs check file sizes and dates against that record instead of re-hashing 10 GB, which saves about 30 seconds per song.
- mlx-Yue ships a memory guard that stops a job at the first sign of system memory pressure. On a laptop with a browser open that fires too early, so songforge keeps the per-process budget (16 GiB) and stops only when macOS reports critical pressure for 10 seconds. `SONGFORGE_STRICT_MEMORY=1` restores the original guard.
- Covers use SheetSage2 and MERT-v2 (also MLX) to transcribe the recording to a melody-only ABC score. YuE2 then sings that melody in melody mode with your style and lyrics. You can review and edit the score before rendering.
- Write lyrics calls a local Ollama model (`qwen3:4b` by default) and is disabled while a song renders, so two models never compete for memory.

## Compared to

| Project | Platform | UI | Covers | Queue, MP3, CLI | What it lacks |
|---|---|---|---|---|---|
| [Suno](https://suno.com) | Cloud | Web | Yes | Yes | Local use. $8 or $24 a month, songs live on their servers |
| [YuE2-Studio](https://github.com/timoncool/YuE2-Studio) | Windows, NVIDIA 6 GB+ | Desktop app | Yes | Partly | Mac support |
| [ace-step-ui](https://github.com/fspecii/ace-step-ui) | NVIDIA CUDA | Web | Yes (ACE-Step) | Yes | Mac support. Last commit 2026-06-27. Uses ACE-Step, not YuE2 |
| [YuE2Mac](https://github.com/arinltte/YuE2Mac) | Apple Silicon, MLX | Native macOS app | No | No | Covers, library queue, MP3 export, CLI, published timings |
| [mlx-Yue](https://github.com/vanch007/mlx-Yue) | Apple Silicon, MLX | None (CLI, Python API) | Yes (CLI) | CLI only | A studio. songforge uses it as the engine |
| **songforge** | Apple Silicon, MLX | Web, one command | Yes | Yes | Windows and Linux. Speed is bound by your Mac's GPU |

## Commands

Every command has `--help`. Commands that print results accept `--json`.

| Command | What it does |
|---|---|
| `songforge` | Same as `songforge serve`. Runs setup on first use, starts the studio and opens the browser |
| `songforge serve [--port 7860] [--host 127.0.0.1] [--no-browser] [--yes] [--skip-setup]` | Start the studio |
| `songforge setup [--covers]` | Install the engine, download and verify weights. `--covers` also fetches the transcription models |
| `songforge doctor` | Check the Mac, engine, weights, ffmpeg and Ollama, and show disk use |
| `songforge generate --style S (--lyrics T \| --lyrics-file F \| --instrumental) [--mode fast\|hq] [--seed N] [--max-seconds 240] [--title T] [--out song.mp3]` | Make one song |
| `songforge cover AUDIO --style S [--lyrics-file F] [--task melody-full\|melody-vocal] [--abc score.abc] [--mode fast\|hq] [--out cover.mp3]` | Cover a recording |
| `songforge list [--limit 50]` | Show the library |
| `songforge bench [--mode fast\|hq] [--max-seconds 240] [--seed 42] [--request file.json]` | Time one song: wall time, real-time factor, peak RSS and peak footprint |

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `SONGFORGE_HOME` | `~/.songforge` | Engine, weights, library and uploads |
| `SONGFORGE_OLLAMA_URL` | `http://localhost:11434` | Ollama server for Write lyrics |
| `SONGFORGE_LYRICS_MODEL` | `qwen3:4b` | Ollama model for Write lyrics |
| `SONGFORGE_STRICT_MEMORY` | unset | `1` restores mlx-Yue's strict memory guard |
| `SONGFORGE_ENGINE` | `mlx` | `fake` runs a test engine that needs no weights |

HTTP API, used by the studio and handy for scripts: `POST /api/songs`, `GET /api/songs`, `GET /api/songs/{id}`, `POST /api/songs/{id}/cancel`, `DELETE /api/songs/{id}`, `GET /api/songs/{id}/audio.{flac,mp3}`, `GET /api/songs/{id}/score.abc`, `POST /api/uploads?name=file.mp3`, `POST /api/transcribe`, `POST /api/covers`, `POST /api/lyrics`, `GET /api/events` (server-sent events), `GET /api/status`.

## Benchmark

{{BENCH}}

## Limits and FAQ

- **Apple Silicon only.** The engine is MLX. There is no Intel, Windows or Linux path.
- **It is not fast on an Air.** {{SPEED_LIMIT}}
- **One song at a time.** Jobs queue. Two YuE2 processes would not fit in memory on most Macs.
- **Length is decided by the model.** The length cap stops token generation, it does not stretch a short song. A capped song is marked in the library.
- **Instrumental is a strong hint, not a switch.** songforge adds `instrumental, no vocals` and replaces the lyrics with section tags. YuE2 can still hum.
- **Covers follow the melody, not the voice.** A cover re-sings the transcribed melody. It does not clone the original singer, keep the original backing track or align every syllable.
- **Covers of copyrighted songs.** Transcribing a melody does not change who owns it. Use material you have the rights to.
- **Memory.** Expect a peak process footprint around {{PEAK}} during a song. Quit other local model servers first on a 16 GB Mac.
- **Where are my files?** `~/.songforge/songs/<id>/` has `audio.flac`, `audio.mp3`, `score.abc` and the engine log. `songforge doctor` shows disk use.
- **Uninstall.** `rm -rf ~/.songforge`.

## License

> **The app is MIT. The model is not.** songforge's code is MIT licensed. The YuE2 weights it downloads are licensed CC BY-NC 4.0 by their authors, which means non-commercial use. Songs you make with songforge are outputs of that model and follow its license. mlx-Yue is Apache-2.0. abcjs, bundled for score rendering, is MIT.

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). Benchmarks from other Macs are especially useful: run `songforge bench --json` and open an issue.

{{FOOTNOTES}}
