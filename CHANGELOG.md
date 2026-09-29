# Changelog

## 0.1.0 (2026-09-30)

First release.

- `songforge` starts a local studio at http://127.0.0.1:7860 and opens the browser.
- Create tab: style prompt with presets, lyrics with section tag chips, Instrumental switch, Fast (8 acoustic steps) or HQ (32 steps), seed, length cap.
- Library: waveform player, FLAC and MP3 download, ABC score viewer, reuse settings, search.
- Cover tab: drop a recording, review the transcribed melody score, sing it in a new style with new lyrics.
- Optional Write lyrics button through a local Ollama model (qwen3:4b by default).
- One worker, SQLite job table, server-sent events for live stage progress.
- `songforge setup` installs mlx-Yue at a pinned commit in its own uv environment and downloads the YuE2 weights.
- `songforge generate`, `cover`, `list`, `doctor` and `bench`, all with `--json`.
- Memory guard tuned for laptops: the per-process budget stays, the warn pressure level no longer aborts a song.
