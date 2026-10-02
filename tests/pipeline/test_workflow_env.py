"""ADR-0227 amendment: the manifest ``env`` key reaches every command the runner starts.

Variable names and values below are test inputs restated on purpose; the module
under test must name none.
"""

import pytest

from dskit.pipeline import workflow as wf

from tests.pipeline.test_workflow import ledger, proj, run, write  # noqa: F401,F811

PRINTER = """
import os, sys
open(sys.argv[1], "w").write(os.environ.get("TOY_VAR", "unset"))
"""


@pytest.fixture
def envproj(proj):  # noqa: F811
    tmp, manifest = proj
    (tmp / "printer.py").write_text(PRINTER)
    manifest["args"]["run_env"] = {"TOY_VAR": "one"}
    manifest["args"]["printer"] = str(tmp / "printer.py")
    manifest["layout"]["s1"]["seen"] = "{W}/s1/seen.txt"
    manifest["registry"]["toy"]["before"] = ["{python} {in.printer} {out.seen}"]
    manifest["registry"]["toy2"]["before"] = []
    manifest["steps"]["s1"]["out"] = ["table", "seen"]
    manifest["steps"]["s1"]["in"]["printer"] = "$args.printer"
    manifest["env"] = "$args.run_env"
    return tmp, manifest


def test_env_reaches_the_child_and_ledger(envproj):
    tmp, manifest = envproj
    msgs = []
    code = wf.run_workflow(write(tmp, manifest), out=msgs.append)
    assert code == 0, msgs
    assert (tmp / "w" / "s1" / "seen.txt").read_text() == "one"
    commands = ledger(tmp)["steps"]["s1"]["commands"]
    assert all(c["env"] == {"TOY_VAR": "one"} for c in commands)


def test_changed_value_reruns_the_step(envproj):
    tmp, manifest = envproj
    run(tmp, manifest)
    manifest["args"]["run_env"] = {"TOY_VAR": "two"}
    run(tmp, manifest)
    assert (tmp / "w" / "s1" / "seen.txt").read_text() == "two"


def test_unchanged_env_skips(envproj):
    tmp, manifest = envproj
    run(tmp, manifest)
    run(tmp, manifest)
    calls = tmp / "w" / "s1" / "table-a.txt.calls"
    assert calls.read_text() == "x"


def test_no_env_key_leaves_ledger_unchanged(proj):  # noqa: F811
    tmp, manifest = proj
    run(tmp, manifest)
    assert all("env" not in c for c in ledger(tmp)["steps"]["s1"]["commands"])


@pytest.mark.parametrize(
    "bad",
    [
        {"": "x"},
        {"A": 1},
        {"A": None},
        ["A"],
        "text",
    ],
)
def test_bad_env_values_refuse(envproj, bad):
    tmp, manifest = envproj
    manifest["args"]["run_env"] = bad
    with pytest.raises(wf.WorkflowError):
        wf.validate_manifest(manifest, str(tmp))


@pytest.mark.parametrize("ref", ["$args.missing", "literal", 5])
def test_bad_env_ref_refuses(envproj, ref):
    tmp, manifest = envproj
    manifest["env"] = ref
    with pytest.raises(wf.WorkflowError):
        wf.validate_manifest(manifest, str(tmp))
