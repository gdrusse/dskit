"""The registry ``before`` / ``after`` command lists and ``files`` templates of the workflow runner.

A registry entry may list commands the runner runs around a step's own command (``before`` ahead of
it, ``after`` once it succeeded) and name ``files``: templates expanded like the step's own and
written beside its config, so a command can take a generated file. The runner records every command
in the ledger, and re-runs an unchanged step only when a file's content moved.
"""

import json
import pathlib

import pytest

import dskit.pipeline
from dskit.pipeline import workflow as wf

ROOT = pathlib.Path(dskit.pipeline.__file__).parents[2]

WRITE = """
import sys
with open(sys.argv[1], "a") as fh:
    fh.write(" ".join(sys.argv[2:]) + "\\n")
"""

FAIL = """
import sys
sys.exit(int(sys.argv[1]))
"""


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "step.json").write_text(json.dumps({"name": "s-${T}"}))
    (tmp_path / "side.json").write_text(
        json.dumps({"who": "${T}", "where": "${L.s.dir}", "notes": "${kept}"})
    )
    (tmp_path / "write.py").write_text(WRITE)
    (tmp_path / "fail.py").write_text(FAIL)
    py = "{python} {in.write} "
    manifest = {
        "args": {
            "tag": "a",
            "work": str(tmp_path / "w"),
            "write": str(tmp_path / "write.py"),
            "fail": str(tmp_path / "fail.py"),
        },
        "layout": {"s": {"dir": "{W}/s", "log": "{W}/s/log.txt", "marker": "{W}/s/marker"}},
        "registry": {
            "r": {
                "template": "step.json",
                "extends": None,
                "command": py + "{out.log} main",
                "files": {"side": "side.json"},
                "before": [py + "{out.log} before {file.side}"],
                "after": [py + "{out.log} after {dir}", py + "{out.marker} done"],
            }
        },
        "steps": {
            "s": {
                "registry": "r",
                "out": ["log", "marker"],
                "in": {
                    "T": "$args.tag",
                    "W": "$args.work",
                    "write": "$args.write",
                },
            }
        },
    }
    return tmp_path, manifest


def run(tmp, manifest, **kw):
    path = tmp / "m.json"
    path.write_text(json.dumps(manifest))
    lines = []
    code = wf.run_workflow(str(path), out=lines.append, **kw)
    return code, lines


def ledger(tmp):
    return json.loads((tmp / "w" / "workflow.json").read_text())["steps"]["s"]


def test_commands_run_in_order_and_are_recorded(proj):
    tmp, manifest = proj
    code, _ = run(tmp, manifest)
    assert code == 0
    lines = (tmp / "w" / "s" / "log.txt").read_text().splitlines()
    assert [line.split()[0] for line in lines] == ["before", "main", "after"]
    assert lines[0].endswith("s.side.json")
    assert lines[2].endswith(str(tmp / "w" / "s"))
    entry = ledger(tmp)
    assert [(c["phase"], c["exit_code"]) for c in entry["commands"]] == [
        ("before", 0),
        ("main", 0),
        ("after", 0),
        ("after", 0),
    ]
    assert all(isinstance(c["argv"], list) for c in entry["commands"])


def test_file_template_is_expanded_and_written_beside_the_config(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    side = json.loads((tmp / "w" / "s" / "s.side.json").read_text())
    assert side == {"who": "a", "where": str(tmp / "w" / "s"), "notes": "${kept}"}
    assert ledger(tmp)["files"]["side"]


def test_after_output_counts_as_a_declared_output(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    assert (tmp / "w" / "s" / "marker").read_text() == "done\n"
    assert "marker" in ledger(tmp)["outputs"]


def test_unchanged_step_skips_its_commands(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    code, lines = run(tmp, manifest)
    assert code == 0 and any("skip" in line for line in lines)
    assert len((tmp / "w" / "s" / "log.txt").read_text().splitlines()) == 3


def test_a_failing_before_command_halts_before_the_step(proj):
    tmp, manifest = proj
    manifest["registry"]["r"]["before"] = ["{python} {in.fail} 7"]
    manifest["steps"]["s"]["in"]["fail"] = "$args.fail"
    code, lines = run(tmp, manifest)
    assert code == 7 and any("halted" in line for line in lines)
    assert not (tmp / "w" / "s" / "log.txt").exists()
    assert ledger(tmp)["commands"][0]["exit_code"] == 7


def test_a_failing_after_command_marks_the_step_failed(proj):
    tmp, manifest = proj
    manifest["registry"]["r"]["after"] = ["{python} {in.fail} 4"]
    manifest["steps"]["s"]["in"]["fail"] = "$args.fail"
    code, _ = run(tmp, manifest)
    assert code == 4
    assert ledger(tmp)["exit_code"] == 4


def test_after_does_not_run_when_the_step_fails(proj):
    tmp, manifest = proj
    manifest["registry"]["r"]["command"] = "{python} {in.fail} 3"
    manifest["steps"]["s"]["in"]["fail"] = "$args.fail"
    code, _ = run(tmp, manifest)
    assert code == 3
    assert [c["phase"] for c in ledger(tmp)["commands"]] == ["before", "main"]


def test_extending_entry_inherits_the_lists(proj):
    tmp, manifest = proj
    manifest["registry"]["child"] = {"extends": "r"}
    manifest["steps"]["s"]["registry"] = "child"
    code, _ = run(tmp, manifest)
    assert code == 0
    assert len((tmp / "w" / "s" / "log.txt").read_text().splitlines()) == 3


@pytest.mark.parametrize(
    "mutate, text",
    [
        (lambda r: r.update(before="x"), "before must be a list of command strings"),
        (lambda r: r.update(after=[1]), "after must be a list of command strings"),
        (lambda r: r.update(files=["a"]), "files must map names to template paths"),
        (lambda r: r.update(files={"bad name": "side.json"}), "files must map names"),
        (lambda r: r.update(after=["{python} {file.nope}"]), "command field {file.nope}"),
        (lambda r: r.update(before=["{python} {in.nope}"]), "command field {in.nope}"),
        (lambda r: r.update(files={"side": "missing.json"}), "missing.json"),
    ],
)
def test_malformed_lists_are_refused_with_a_reason(proj, mutate, text):
    tmp, manifest = proj
    mutate(manifest["registry"]["r"])
    code, lines = run(tmp, manifest, plan=True)
    assert code == 1
    assert any(text in line for line in lines), lines


def test_file_template_placeholder_without_a_value_is_refused(proj):
    tmp, manifest = proj
    (tmp / "side.json").write_text(json.dumps({"who": "${ghost}"}))
    code, lines = run(tmp, manifest, plan=True)
    assert code == 1
    assert any("placeholder ghost has no value" in line for line in lines), lines


def test_an_in_key_used_only_by_a_hook_command_or_file_is_used(proj):
    tmp, manifest = proj
    code, lines = run(tmp, manifest, plan=True)
    assert code == 0, lines
    manifest["steps"]["s"]["in"]["spare"] = "$args.tag"
    code, lines = run(tmp, manifest, plan=True)
    assert code == 1 and any("in key spare is used by no" in line for line in lines)


def test_plan_lists_the_commands_without_running_them(proj):
    tmp, manifest = proj
    code, lines = run(tmp, manifest, plan=True)
    assert code == 0
    assert sum(line.startswith("    before ") for line in lines) == 1
    assert sum(line.startswith("    after ") for line in lines) == 2
    assert not (tmp / "w").exists()


def test_a_changed_file_template_reruns_the_step(proj):
    tmp, manifest = proj
    manifest["registry"]["r"]["before"] = []
    manifest["registry"]["r"]["after"] = []
    manifest["registry"]["r"]["command"] = "{python} {in.write} {out.log} {file.side}"
    manifest["registry"]["r"]["command"] += " "
    manifest["layout"]["s"].pop("marker")
    manifest["steps"]["s"]["out"] = ["log"]
    run(tmp, manifest)
    assert any("skip" in line for line in run(tmp, manifest)[1])
    (tmp / "side.json").write_text(json.dumps({"who": "${T}-changed"}))
    code, lines = run(tmp, manifest)
    assert code == 0 and not any("skip" in line for line in lines)
    side = json.loads((tmp / "w" / "s" / "s.side.json").read_text())
    assert side == {"who": "a-changed"}


def test_in_field_descends_into_an_object_value(proj):
    tmp, manifest = proj
    manifest["args"]["meta"] = {"word": "hello"}
    manifest["steps"]["s"]["in"]["meta"] = "$args.meta"
    manifest["registry"]["r"]["after"] = ["{python} {in.write} {out.marker} {in.meta.word}"]
    assert run(tmp, manifest)[0] == 0
    assert (tmp / "w" / "s" / "marker").read_text() == "hello\n"


def test_in_field_path_with_no_value_is_refused_at_plan_time(proj):
    tmp, manifest = proj
    manifest["args"]["meta"] = {"word": "hello"}
    manifest["steps"]["s"]["in"]["meta"] = "$args.meta"
    manifest["registry"]["r"]["after"] = ["{python} {in.write} {out.marker} {in.meta.nope}"]
    code, lines = run(tmp, manifest, plan=True)
    assert code == 1 and any("meta.nope" in line for line in lines), lines
