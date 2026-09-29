"""The runner executes inside the engine venv. Its pure parts are tested here without lyra."""

import io
import json

import pytest

from songforge.engine import mlx_runner


@pytest.fixture
def captured(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(mlx_runner, "_OUT", buf)
    return buf


def lines(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines()]


@pytest.mark.parametrize(
    ("label", "stage"),
    [
        ("Verifying model files", "planning"),
        ("Loading 8bit AR model", "planning"),
        ("Planning score", "planning"),
        ("Using provided score", "planning"),
        ("Generating song", "tokenizing"),
        ("Loading BF16 acoustic conditioning", "synth"),
        ("Synthesizing audio", "synth"),
        ("Loading MLX audio decoder", "render"),
        ("Decoding audio", "render"),
        ("Something else", None),
    ],
)
def test_classify(label, stage):
    assert mlx_runner.classify(label) == stage


def test_reporter_throttles_and_finishes(captured):
    rep = mlx_runner.Reporter("tokenizing", "Generating song", 6000)
    for _ in range(100):
        rep.token("semantic", 1)
    rep.finish()
    out = lines(captured)
    assert out[-1] == {
        "event": "progress",
        "stage": "tokenizing",
        "label": "Generating song",
        "done": 100,
        "total": 6000,
    }
    assert len(out) <= 3


def test_reporter_update_forces_final(captured):
    rep = mlx_runner.Reporter("synth", "Synthesizing audio", None)
    rep.update(8, total=8)
    assert lines(captured)[-1]["done"] == 8


def test_stamp_roundtrip(tmp_path):
    model = tmp_path / "yue2-mlx"
    model.mkdir()
    (model / "a.safetensors").write_text("abc")
    stamp = mlx_runner.stamp_path(model)
    assert stamp.parent == tmp_path and stamp.name == "yue2-mlx.songforge-verified.json"
    stamp.write_text(json.dumps(mlx_runner.file_stamp(model)))
    assert json.loads(stamp.read_text()) == mlx_runner.file_stamp(model)
    (model / "a.safetensors").write_text("changed!")
    assert json.loads(stamp.read_text()) != mlx_runner.file_stamp(model)


def test_skip_rehash_requires_matching_stamp(tmp_path):
    model = tmp_path / "m"
    model.mkdir()
    assert mlx_runner.skip_rehash_when_verified(model) is False


def test_main_reports_errors_as_json(tmp_path, captured):
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "out": str(tmp_path),
                "model_dir": str(tmp_path / "none"),
                "vae_dir": str(tmp_path / "none"),
            }
        )
    )
    code = mlx_runner.main(["verify", "--job", str(job)])
    assert code == 1
    last = lines(captured)[-1]
    assert last["event"] == "error"
    assert last["message"]
