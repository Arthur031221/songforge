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


PIANO_ABC = """X:1
M:4/4
L:1/16
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% verse
V: Vocal
Z4|
V: Ins
G4e4d4c4|G12G2G2|G4e4d4c4|A16|
% chorus
V: Vocal
Z4|
V: Ins
e4e4e8|e4e4e8|e4g4c6d2|e16|
"""


def test_vocalize_moves_instrument_melody_to_vocal():
    out = mlx_runner.vocalize(PIANO_ABC)
    lines = out.split("\n")
    assert lines[lines.index("% verse") + 2] == "G4e4d4c4|G12G2G2|G4e4d4c4|A16|"
    assert lines[lines.index("% verse") + 4] == "Z4|"
    assert lines[lines.index("% chorus") + 2] == "e4e4e8|e4e4e8|e4g4c6d2|e16|"
    # Header voice declarations stay where they are.
    assert lines[3].startswith("V: Vocal clef=treble")
    assert mlx_runner.vocalize(out) == out


def test_vocalize_keeps_sung_scores():
    sung = PIANO_ABC.replace("V: Vocal\nZ4|\n", 'V: Vocal\n"C"E2G2A2G2|\n', 1)
    assert mlx_runner.vocalize(sung) == sung
    assert mlx_runner.vocalize("no score here") == "no score here"


def test_vocalize_handles_blocks_of_different_length():
    abc = PIANO_ABC.replace("G4e4d4c4|G12G2G2|G4e4d4c4|A16|", "G4e4d4c4|G12G2G2|\nG4e4d4c4|A16|")
    out = mlx_runner.vocalize(abc).split("\n")
    verse = out.index("% verse")
    assert out[verse + 1 : verse + 6] == [
        "V: Vocal",
        "G4e4d4c4|G12G2G2|",
        "G4e4d4c4|A16|",
        "V: Ins",
        "Z4|",
    ]
    assert out[out.index("% chorus") + 2] == "e4e4e8|e4e4e8|e4g4c6d2|e16|"
