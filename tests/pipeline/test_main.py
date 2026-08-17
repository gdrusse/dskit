"""The runnable commands: ``python -m dskit.pipeline {demo,synthetic}``."""

from dskit.pipeline.__main__ import demo_config, main


def test_demo_prints_hash_and_roundtrips(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "round-trip verified" in out
    assert demo_config().hash[:16] in out


def test_demo_is_the_default_command(capsys):
    assert main([]) == 0
    assert "round-trip verified" in capsys.readouterr().out


def test_validate_command_on_the_example_input(capsys):
    import pathlib

    example = pathlib.Path(__file__).parents[2] / "examples/pipeline/synthetic.json"
    assert main(["validate", str(example)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK — ") and "hash:" in out


def test_validate_command_fails_loud_on_broken_files(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x"}')
    assert main(["validate", str(bad)]) == 1
    assert "required key" in capsys.readouterr().out


def test_synthetic_runs_end_to_end(capsys):
    assert main(["synthetic"]) == 0
    out = capsys.readouterr().out
    # report.md is printed verbatim: verdict first, then the stages
    assert out.lstrip().startswith("**")
    for stage in ("train", "validate", "stat_test", "optimize", "backtest"):
        assert f"## {stage}" in out
