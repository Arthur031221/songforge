import json
import wave

import httpx
import pytest

from songforge import audio, cli, config, install, lyrics
from songforge.bench import fmt_bytes, fmt_duration, render_table, run_bench


# lyrics ------------------------------------------------------------------------
def test_clean_normalizes_tags_and_strips_thinking():
    raw = "<think>plan</think>\n**Verse 1:**\nLine one\nLine two\n(Chorus)\nHook line\n```"
    assert lyrics.clean(raw) == "[Verse]\nLine one\nLine two\n\n[Chorus]\nHook line\n"


def test_status_reports_missing_model(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "qwen3:1.7b"}]}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    st = lyrics.status()
    assert st["available"] is False and "ollama pull qwen3:4b" in st["reason"]
    monkeypatch.setenv("SONGFORGE_LYRICS_MODEL", "qwen3:1.7b")
    assert lyrics.status()["available"] is True


def test_status_when_ollama_is_down(paths):
    assert lyrics.status(timeout=0.2)["available"] is False


def test_write_uses_ollama_structured_output(monkeypatch):
    seen = {}
    reply = {
        "sections": [
            {"tag": "Verse", "lines": ["Rain on the window pane", "I hear you call my name"]},
            {"tag": "chorus", "lines": ["Hold on", "  ", "Hold on tight"]},
        ]
    }

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": json.dumps(reply)}}

    def post(url, json, timeout):
        seen.update(url=url, body=json)
        return R()

    monkeypatch.setattr(httpx, "post", post)
    text = lyrics.write("rain", "indie pop")
    assert text == (
        "[Verse]\nRain on the window pane\nI hear you call my name\n\n[Chorus]\nHold on\nHold on tight\n"
    )
    assert seen["url"].endswith("/api/chat")
    assert seen["body"]["format"]["required"] == ["sections"]
    assert seen["body"]["model"] == config.lyrics_model()


def test_from_json_falls_back_to_text():
    chatty = "Sure, here you go.\n[Verse]\nLine one\nLine two\n[Chorus]\nHook\n"
    assert lyrics.from_json(chatty) == "[Verse]\nLine one\nLine two\n\n[Chorus]\nHook\n"
    assert lyrics.from_json("no tags at all") == ""


def test_write_rejects_empty(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    with pytest.raises(lyrics.LyricsError):
        lyrics.write("x", "y")


# audio -------------------------------------------------------------------------
def write_wav(path, seconds=0.5, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((b"\x00\x20\x00\x20" + b"\x00\x00\x00\x00") * int(seconds * rate / 2))


def test_peaks_and_duration_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "ffmpeg", lambda: None)
    wav = tmp_path / "a.wav"
    write_wav(wav)
    assert audio.duration(wav) == pytest.approx(0.5, abs=0.01)
    peaks = audio.peaks(wav, buckets=10)
    assert 1 <= len(peaks) <= 10 and max(peaks) == 1.0
    with pytest.raises(audio.AudioError, match="brew install ffmpeg"):
        audio.to_mp3(wav, tmp_path / "a.mp3")


# install -----------------------------------------------------------------------
def test_preflight_rejects_non_apple(paths, monkeypatch):
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    problems = install.preflight(paths)
    assert any("Apple Silicon" in p for p in problems)


def test_engine_not_installed(paths):
    assert install.engine_installed(paths) is False
    assert install.disk_usage(paths)["models"] == 0


# bench -------------------------------------------------------------------------
def test_bench_with_fake_engine(paths, fake_engine):
    report = run_bench(paths, fake_engine, echo=lambda m: None)
    assert report["audio_seconds"] == 1.0
    assert report["rtf"] is not None and report["ode_steps"] == 8
    table = render_table(report)
    assert "real-time factor" in table and "peak RSS" in table
    saved = list((paths.home / "bench").glob("*/bench.json"))
    assert saved and json.loads(saved[0].read_text())["mode"] == "fast"


def test_formatters():
    assert fmt_duration(185) == "3m 05s"
    assert fmt_duration(42) == "42s"
    assert fmt_duration(None) == "n/a"
    assert fmt_bytes(10 * 2**30) == "10.0 GiB"
    assert fmt_bytes(None) == "n/a"


# cli ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command", ["serve", "setup", "doctor", "generate", "cover", "list", "bench"]
)
def test_every_command_has_help(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main([command, "--help"])
    assert exit_info.value.code == 0
    assert "usage: songforge" in capsys.readouterr().out


def test_list_empty(paths, capsys):
    assert cli.main(["list"]) == 0
    assert "empty" in capsys.readouterr().out
    assert cli.main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_generate_and_list_with_fake_engine(paths, capsys, tmp_path):
    out = tmp_path / "song.flac"
    code = cli.main(
        [
            "generate",
            "--engine",
            "fake",
            "--style",
            "indie pop",
            "--lyrics",
            "[Verse]\\nHello there",
            "--seed",
            "3",
            "--json",
            "--port",
            "9",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["title"] == "Hello there" and data["seed"] == 3
    assert cli.main(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["id"] == data["id"] and rows[0]["status"] == "done"
    if data["path"] and data["path"].endswith(".flac"):
        assert (
            cli.main(
                [
                    "generate",
                    "--engine",
                    "fake",
                    "--style",
                    "x",
                    "--instrumental",
                    "--out",
                    str(out),
                    "--json",
                    "--port",
                    "9",
                ]
            )
            == 0
        )
        assert out.is_file()


def test_generate_requires_lyrics(paths, capsys):
    assert cli.main(["generate", "--engine", "fake", "--style", "x", "--port", "9"]) == 1
    assert "--lyrics" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command_args",
    [
        ["generate", "--style", "folk", "--lyrics", "hello"],
        ["cover", "missing.mp3", "--style", "folk"],
        ["bench"],
    ],
)
@pytest.mark.parametrize("seconds", [0, 19, 361])
def test_max_seconds_rejects_values_outside_engine_limits(command_args, seconds, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main([*command_args, "--max-seconds", str(seconds)])
    assert error.value.code == 2
    assert "between 20 and 360 seconds" in capsys.readouterr().err


@pytest.mark.parametrize("seconds", [20, 360])
def test_length_cap_accepts_engine_limits(seconds):
    assert cli.length_cap(str(seconds)) == seconds


def test_doctor_json_with_fake_engine(paths, capsys):
    assert cli.main(["doctor", "--engine", "fake", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["engine"]["ready"] is True
    assert report["engine_commit"] == config.MLX_ENGINE_COMMIT


def test_doctor_reports_missing_weights(paths, capsys):
    assert cli.main(["doctor"]) == 1
    assert "songforge setup" in capsys.readouterr().out


def test_bench_json_with_fake_engine(paths, capsys):
    assert cli.main(["bench", "--engine", "fake", "--json", "--port", "9"]) == 0
    assert json.loads(capsys.readouterr().out)["engine"] == "fake"


def test_cover_missing_file(paths, capsys):
    assert cli.main(["cover", "/nope.mp3", "--style", "metal", "--engine", "fake"]) == 1
    assert "No such file" in capsys.readouterr().err
