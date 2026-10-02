# Changelog

## Unreleased

- Reject CLI length caps outside 20 to 360 seconds, matching studio validation.

## 0.1.0 (2026-09-30)

First release.

- `songforge` starts a local studio at http://127.0.0.1:7860 and opens the browser. The first run installs the engine and downloads the weights.
- Create tab: style prompt with presets, lyrics with section tag chips, Instrumental switch, Fast (8 acoustic steps) or HQ (32 steps), seed, length cap.
- Live stage progress (Planning, Tokenizing, Synth, Render) over server-sent events.
- Library: waveform player, FLAC and MP3 download, ABC score viewer, reuse settings, search and filters.
- Cover tab: drop a recording, review and edit the transcribed melody score, sing it in a new style with new lyrics. Sources without a sung line get their melody moved to the vocal voice.
- Optional Write lyrics button through a local Ollama model (qwen3:4b by default), using structured output so thinking models stay on format.
- One worker, SQLite job table, and a cross-process engine lock so the studio, the CLI and the bench never run two engines at once.
- `songforge setup` installs mlx-Yue at a pinned commit in its own uv environment, downloads the weights and verifies their hashes once.
- `songforge generate`, `cover`, `list`, `doctor` and `bench`, all with `--help` and `--json`.
- Memory guard tuned for laptops: the per-process budget stays, a busy machine no longer aborts a song unless memory pressure is critical for 10 seconds.
