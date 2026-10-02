"""ADR-0227: the workflow runner -- manifest validation, templates, chain, loop, hooks."""

import ast
import copy
import json
import pathlib
import re
import shlex

import pytest

import dskit.pipeline
from dskit.pipeline import workflow as wf

ROOT = pathlib.Path(dskit.pipeline.__file__).parents[2]

HELPER = """
import json, sys
from dskit.pipeline.__main__ import main
cfg, out = sys.argv[1], sys.argv[2]
up = sys.argv[3] if len(sys.argv) > 3 else None
import os
calls = out + ".calls"
n = len(open(calls).read()) if os.path.exists(calls) else 0
with open(calls, "a") as fh:
    fh.write("x")
rc = main(["run", cfg, "--asof", "2026-01-%02d" % (n + 1)])
if rc:
    sys.exit(rc)
name = json.load(open(cfg))["name"]
text = (open(up).read() if up else "") + name + "\\n"
open(out, "w").write(text)
"""

LOOPER = """
import sys
out, rnd = sys.argv[1], int(sys.argv[2])
open(out, "w").write(str(min(rnd, 2)))
"""

TOY = {
    "name": "toy-${T}-${seed}",
    "notes": "keeps ${not_substituted} and $$ verbatim",
    "pipeline": {
        "dataset": {
            "uses": "dskit.pipeline.synthetic_nodes:SynthEvents",
            "params": {"n_events": 104, "n_instruments": 2, "seed": "${seed}"},
        },
        "labels": {
            "uses": "dskit.pipeline.synthetic_nodes:SynthLabels",
            "inputs": {"events": "$dataset.events"},
        },
    },
    "splits": {"kind": "random", "train_frac": 0.8, "val_frac": 0.2, "seed": 7},
    "outputs": {"run_root": "${runs}"},
}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (tmp_path / "toy.json").write_text(json.dumps(TOY))
    (tmp_path / "loop.json").write_text(json.dumps({"name": "loop-${T}-${round}"}))
    (tmp_path / "helper.py").write_text(HELPER)
    (tmp_path / "looper.py").write_text(LOOPER)
    py = "{python} "
    manifest = {
        "args": {
            "tag": "a",
            "seed": 3,
            "work": str(tmp_path / "w"),
            "runs": str(tmp_path / "runs"),
            "runs2": str(tmp_path / "runs2"),
            "helper": str(tmp_path / "helper.py"),
            "looper": str(tmp_path / "looper.py"),
            "sel": {"stop": {"rule": "unchanged"}, "max_rounds": 10},
            "names": ["c", "a"],
            "order": ["a", "b", "c"],
        },
        "layout": {
            "s1": {"dir": "{W}/s1", "table": "{W}/s1/table-{T}.txt"},
            "s2": {"dir": "{W}/s2", "table": "{W}/s2/table-{T}.txt"},
            "lp": {"dir": "{W}/lp", "state": "{W}/lp/state-{T}.txt"},
        },
        "registry": {
            "toy": {
                "template": "toy.json",
                "extends": None,
                "command": py + "{in.helper} {config} {out.table}",
            },
            "toy2": {
                "extends": "toy",
                "command": py + "{in.helper} {config} {out.table} {in.upstream}",
            },
            "bad": {
                "template": "toy.json",
                "extends": None,
                "command": py + "-c " + shlex.quote("import sys; sys.exit(3)"),
            },
            "count": {
                "template": "loop.json",
                "extends": None,
                "command": py + "{in.looper} {out.state} {round}",
            },
        },
        "steps": {
            "s1": {
                "registry": "toy",
                "out": ["table"],
                "in": {
                    "T": "$args.tag",
                    "W": "$args.work",
                    "seed": "$args.seed",
                    "runs": "$args.runs",
                    "helper": "$args.helper",
                },
            },
            "s2": {
                "registry": "toy2",
                "out": ["table"],
                "in": {
                    "T": "$args.tag",
                    "W": "$args.work",
                    "seed": "$args.seed",
                    "runs": "$args.runs2",
                    "helper": "$args.helper",
                    "upstream": "$s1.out.table",
                },
            },
        },
    }
    (tmp_path / "m.json").write_text(json.dumps(manifest))
    return tmp_path, manifest


def write(tmp, manifest):
    path = tmp / "m.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def run(tmp, manifest, **kw):
    return wf.run_workflow(write(tmp, manifest), out=lambda *a: None, **kw)


def ledger(tmp):
    return json.loads((tmp / "w" / "workflow.json").read_text())


# -- placeholder expansion ---------------------------------------------------

VALUES = {"T": ["x", "y"], "S": {"symbol": "ABC", "n": 4, "z": None}, "k": 7}


def test_whole_string_keeps_json_type():
    doc = {"a": "${T}", "b": "${S.n}", "c": "${S.z}", "d": "${S}", "e": "${k}"}
    assert wf.expand(doc, VALUES) == {
        "a": ["x", "y"],
        "b": 4,
        "c": None,
        "d": VALUES["S"],
        "e": 7,
    }


def test_embedded_placeholder_interpolates_text():
    assert wf.expand({"a": "pre-${S.symbol}-${k}"}, VALUES) == {"a": "pre-ABC-7"}


def test_dollar_escape_and_pipeline_refs_untouched():
    doc = {"a": "$$5", "b": "$each", "c": "$node.port", "d": "$prev", "e": "$$${k}"}
    assert wf.expand(doc, VALUES) == {
        "a": "$5",
        "b": "$each",
        "c": "$node.port",
        "d": "$prev",
        "e": "$7",
    }


def test_notes_copied_verbatim_at_any_depth():
    doc = {"notes": "${nope} $$", "n": {"notes": "${nope}", "v": "${k}"}}
    assert wf.expand(doc, VALUES) == {
        "notes": "${nope} $$",
        "n": {"notes": "${nope}", "v": 7},
    }


def test_lists_and_nested_values_expand():
    assert wf.expand({"l": ["${k}", {"q": "${S.symbol}"}]}, VALUES) == {
        "l": [7, {"q": "ABC"}]
    }


@pytest.mark.parametrize("bad", ["${nope}", "${S.missing}", "${k.deeper}"])
def test_placeholder_without_value_refuses(bad):
    with pytest.raises(wf.WorkflowError):
        wf.expand({"a": bad}, VALUES)


def test_placeholder_names_lists_roots_outside_notes():
    doc = {"a": "${T}-${S.symbol}", "notes": "${zz}", "b": ["${k}"]}
    assert wf.placeholder_names(doc) == {"T", "S", "k"}


# -- validation refusals ------------------------------------------------------


def _drop_layout_out(m):
    del m["layout"]["s1"]["table"]


def _literal_in(m):
    m["steps"]["s1"]["in"]["seed"] = 5


def _literal_layout(m):
    m["layout"]["s1"]["table"] = "fixed/table.txt"


def _dangling_args(m):
    m["steps"]["s1"]["in"]["seed"] = "$args.missing"


def _dangling_step(m):
    m["steps"]["s1"]["in"]["seed"] = "$s2.out.table"


def _dangling_out(m):
    m["steps"]["s2"]["in"]["upstream"] = "$s1.out.nope"


def _no_registry(m):
    m["steps"]["s1"]["registry"] = "ghost"


def _unused_in(m):
    m["steps"]["s1"]["in"]["extra"] = "$args.tag"


def _no_value(m):
    del m["steps"]["s1"]["in"]["runs"]


def _unknown_step_param(m):
    m["steps"]["s1"]["colour"] = "red"


def _unknown_registry_param(m):
    m["registry"]["toy"]["colour"] = "red"


def _unknown_layout_var(m):
    m["layout"]["s1"]["table"] = "{Q}/table.txt"


def _unknown_top(m):
    m["colour"] = 1


def _extends_cycle(m):
    m["registry"]["toy"]["extends"] = "toy2"


def _unknown_hook(m):
    m["steps"]["s1"]["in"]["seed"] = {"hook": "ghost"}


def _command_field(m):
    m["registry"]["toy"]["command"] = "{python} {in.ghost}"


REFUSALS = [
    _drop_layout_out,
    _literal_in,
    _literal_layout,
    _dangling_args,
    _dangling_step,
    _dangling_out,
    _no_registry,
    _unused_in,
    _no_value,
    _unknown_step_param,
    _unknown_registry_param,
    _unknown_layout_var,
    _unknown_top,
    _extends_cycle,
    _unknown_hook,
    _command_field,
]


@pytest.mark.parametrize("mutate", REFUSALS, ids=lambda f: f.__name__)
def test_validation_refuses_and_runs_nothing(proj, mutate):
    tmp, manifest = proj
    manifest = copy.deepcopy(manifest)
    mutate(manifest)
    assert run(tmp, manifest) == 1
    assert not (tmp / "w").exists()
    assert run(tmp, manifest, plan=True) == 1


def test_validate_reports_every_problem(proj):
    _, manifest = proj
    manifest = copy.deepcopy(manifest)
    _literal_in(manifest)
    _no_registry(manifest)
    with pytest.raises(wf.WorkflowError) as exc:
        wf.validate_manifest(manifest)
    assert len(exc.value.problems) >= 2


def test_unfilled_arg_refuses_run_but_plan_notes_it(proj):
    tmp, manifest = proj
    manifest = copy.deepcopy(manifest)
    manifest["args"]["seed"] = "<seed>"
    assert run(tmp, manifest) == 1
    assert not (tmp / "w").exists()


# -- plan --------------------------------------------------------------------


def test_plan_prints_dag_and_writes_nothing(proj):
    tmp, manifest = proj
    lines = []
    code = wf.run_workflow(write(tmp, manifest), plan=True, out=lines.append)
    assert code == 0, lines
    text = "\n".join(map(str, lines))
    assert "s1" in text and "s2" in text
    assert text.index("s1") < text.index("s2")
    assert not (tmp / "w").exists()


# -- chain / ledger / skip / halt ---------------------------------------------


def test_two_step_chain_passes_output_file(proj):
    tmp, manifest = proj
    assert run(tmp, manifest) == 0
    first = (tmp / "w" / "s1" / "table-a.txt").read_text()
    second = (tmp / "w" / "s2" / "table-a.txt").read_text()
    assert first == "toy-a-3\n"
    assert second == first + "toy-a-3\n"
    cfg = json.loads((tmp / "w" / "s1" / "s1.json").read_text())
    assert cfg["pipeline"]["dataset"]["params"]["seed"] == 3
    assert cfg["outputs"]["run_root"] == str(tmp / "runs")
    assert cfg["notes"] == TOY["notes"]


def test_ledger_records_hashes_and_exit_codes(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    led = ledger(tmp)
    s1, s2 = led["steps"]["s1"], led["steps"]["s2"]
    assert s1["exit_code"] == 0 and s2["exit_code"] == 0
    assert re.fullmatch(r"[0-9a-f]{64}", s1["config_hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", s1["outputs"]["table"])
    assert s2["inputs"]["upstream"] == s1["outputs"]["table"]


def calls(tmp, step):
    return len((tmp / "w" / step / "table-a.txt.calls").read_text())


def test_rerun_skips_unchanged_steps(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    run(tmp, manifest)
    assert calls(tmp, "s1") == 1 and calls(tmp, "s2") == 1


def test_changed_config_reruns_it_and_its_consumer(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    manifest = copy.deepcopy(manifest)
    manifest["args"]["seed"] = 4
    run(tmp, manifest)
    assert calls(tmp, "s1") == 2 and calls(tmp, "s2") == 2
    assert (tmp / "w" / "s1" / "table-a.txt").read_text() == "toy-a-4\n"


def test_changed_input_alone_reruns_consumer(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    manifest = copy.deepcopy(manifest)
    manifest["steps"]["s2"]["in"]["seed"] = "$args.seed2"
    manifest["args"]["seed2"] = 3
    run(tmp, manifest)  # s2 config unchanged, input unchanged -> skipped
    assert calls(tmp, "s2") == 1
    (tmp / "w" / "s1" / "table-a.txt").write_text("tampered\n")
    run(tmp, manifest)  # s1 output no longer matches ledger -> s1 reruns
    assert calls(tmp, "s1") == 2
    assert (tmp / "w" / "s1" / "table-a.txt").read_text() == "toy-a-3\n"


def test_halt_on_nonzero_stops_chain_and_propagates_code(proj):
    tmp, manifest = proj
    manifest = copy.deepcopy(manifest)
    manifest["steps"]["s1"]["registry"] = "bad"
    manifest["steps"]["s1"]["in"].pop("helper")
    assert run(tmp, manifest) == 3
    assert not (tmp / "w" / "s2").exists()
    assert ledger(tmp)["steps"]["s1"]["exit_code"] == 3


def test_missing_declared_output_is_an_error(proj):
    tmp, manifest = proj
    manifest = copy.deepcopy(manifest)
    manifest["registry"]["toy"]["command"] = "{python} -c pass"
    manifest["steps"]["s1"]["in"].pop("helper")
    assert run(tmp, manifest) == 1


def test_from_and_only(proj):
    tmp, manifest = proj
    assert run(tmp, manifest, only="s1") == 0
    assert not (tmp / "w" / "s2").exists()
    assert run(tmp, manifest, from_step="s2") == 0
    assert (tmp / "w" / "s2" / "table-a.txt").exists()
    assert run(tmp, manifest, only="ghost") == 1
    assert run(tmp, manifest, only="s1", from_step="s2") == 1


def test_from_step_with_missing_upstream_output_errors(proj):
    tmp, manifest = proj
    assert run(tmp, manifest, from_step="s2") == 1


def test_args_file_overlays_manifest_args(proj):
    tmp, manifest = proj
    argf = tmp / "a.json"
    argf.write_text(json.dumps({"seed": 9}))
    assert run(tmp, manifest, args_path=str(argf)) == 0
    assert (tmp / "w" / "s1" / "table-a.txt").read_text() == "toy-a-9\n"


# -- loop --------------------------------------------------------------------


def loop_manifest(manifest, max_rounds=None):
    m = copy.deepcopy(manifest)
    m["steps"] = {
        "lp": {
            "registry": "count",
            "out": ["state"],
            "in": {"T": "$args.tag", "W": "$args.work", "looper": "$args.looper"},
            "loop": {
                "until": "$args.sel.stop",
                "max_rounds": "$args.sel.max_rounds",
                "group": "$args.sel.group",
            },
        }
    }
    m["args"]["sel"]["group"] = "state"
    if max_rounds:
        m["args"]["sel"]["max_rounds"] = max_rounds
    return m


def test_loop_stops_when_rule_holds(proj):
    tmp, manifest = proj
    assert run(tmp, loop_manifest(manifest)) == 0
    entry = ledger(tmp)["steps"]["lp"]
    assert entry["rounds"] == 4 and entry["stopped"] == "rule"
    assert (tmp / "w" / "lp" / "state-a.txt").read_text() == "2"


def test_loop_caps_at_max_rounds(proj):
    tmp, manifest = proj
    assert run(tmp, loop_manifest(manifest, max_rounds=2)) == 0
    entry = ledger(tmp)["steps"]["lp"]
    assert entry["rounds"] == 2 and entry["stopped"] == "max_rounds"


def test_loop_rerun_skips_when_unchanged(proj):
    tmp, manifest = proj
    m = loop_manifest(manifest)
    run(tmp, m)
    (tmp / "w" / "lp" / "state-a.txt").write_text("marker")
    run(tmp, m)
    assert (tmp / "w" / "lp" / "state-a.txt").read_text() == "2"  # tampered: reran
    state = tmp / "w" / "lp" / "state-a.txt"
    before = state.stat().st_mtime_ns
    run(tmp, m)
    assert state.stat().st_mtime_ns == before  # unchanged: skipped


def test_loop_rejects_unknown_rule_and_group(proj):
    tmp, manifest = proj
    m = loop_manifest(manifest)
    m["args"]["sel"]["stop"] = {"rule": "ghost"}
    assert run(tmp, m) == 1
    m = loop_manifest(manifest)
    m["args"]["sel"]["group"] = "nope"
    assert run(tmp, m) == 1
    m = loop_manifest(manifest)
    m["args"]["sel"]["stop"] = {"rule": "unchanged", "extra": 1}
    assert run(tmp, m) == 1


def test_stop_rule_is_abstract_and_extensible():
    with pytest.raises(TypeError):
        wf.StopRule()

    class Never(wf.StopRule):
        def stop(self, history):
            return False

    assert Never().stop(["a", "a"]) is False
    assert wf.UnchangedRule().stop(["a", "b", "b"]) is True
    assert wf.UnchangedRule().stop(["a", "b"]) is False
    assert wf.UnchangedRule().stop(["a"]) is False


# -- hooks: names_to_indices ---------------------------------------------------


def test_names_to_indices_round_trip():
    hook = wf.NamesToIndices()
    order = ["a", "b", "c"]
    idx = hook.apply({"names": ["c", "a"], "order": order})
    assert idx == [2, 0]
    assert hook.invert(idx, order) == ["c", "a"]


def test_names_to_indices_reads_names_from_a_json_file(tmp_path):
    f = tmp_path / "n.json"
    f.write_text(json.dumps(["b"]))
    assert wf.NamesToIndices().apply({"names": str(f), "order": ["a", "b"]}) == [1]


def test_names_to_indices_unknown_name_refuses():
    with pytest.raises(wf.WorkflowError):
        wf.NamesToIndices().apply({"names": ["z"], "order": ["a"]})


def test_hook_in_manifest_feeds_template(proj):
    tmp, manifest = proj
    m = copy.deepcopy(manifest)
    (tmp / "toy.json").write_text(json.dumps({**TOY, "name": "toy-${T}-${idx}"}))
    m["steps"]["s1"]["in"].pop("seed")
    m["steps"]["s1"]["in"]["idx"] = {
        "hook": "names_to_indices",
        "names": "$args.names",
        "order": "$args.order",
    }
    m["steps"]["s2"]["in"].pop("seed")
    m["steps"]["s2"]["in"]["idx"] = "$args.tag"
    (tmp / "toy.json").write_text(
        json.dumps(
            {
                **TOY,
                "name": "toy-${T}-${idx}",
                "pipeline": {
                    **TOY["pipeline"],
                    "dataset": {
                        **TOY["pipeline"]["dataset"],
                        "params": {"n_events": 104, "n_instruments": 2, "seed": 1},
                    },
                },
            }
        )
    )
    assert run(tmp, m, only="s1") == 0
    assert (tmp / "w" / "s1" / "table-a.txt").read_text() == "toy-a-2_0\n"


def test_hook_registry_is_open():
    class Twice(wf.Hook):
        def apply(self, inputs):
            return inputs["v"] * 2

    wf.register_hook("twice_test", Twice())
    assert wf.HOOKS["twice_test"].apply({"v": 2}) == 4
    with pytest.raises(TypeError):
        wf.Hook()
    del wf.HOOKS["twice_test"]


# -- gates --------------------------------------------------------------------

SRC = pathlib.Path(wf.__file__)

DOMAIN = re.compile(
    r"ticker|option|spx|vix|equity|stock|index_options|fold_table|step\d|feature",
    re.IGNORECASE,
)
PATHLIKE = re.compile(r"[/\\]|\.(json|csv|py|md|txt)\b|\d")


def _function_strings(tree):
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(fn, clean=False)
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value != doc:
                    yield node.value


def test_runner_source_names_no_domain():
    text = SRC.read_text()
    code = re.sub(r'""".*?"""', "", text, flags=re.S)
    assert not DOMAIN.search(code), DOMAIN.search(code)


def test_runner_has_no_path_or_number_literals_in_function_bodies():
    tree = ast.parse(SRC.read_text())
    bad = sorted({s for s in _function_strings(tree) if PATHLIKE.search(s)})
    assert bad == []


def test_runner_declares_public_api():
    assert {
        "run_workflow",
        "validate_manifest",
        "expand",
        "WorkflowError",
        "StopRule",
        "Hook",
        "NamesToIndices",
    } <= set(wf.__all__)
    assert not [n for n in wf.__all__ if n.startswith("_")]


def test_cli_dispatches_workflow(proj, capsys):
    from dskit.pipeline.__main__ import main

    tmp, manifest = proj
    assert main(["workflow", write(tmp, manifest), "--plan"]) == 0
    assert "s1" in capsys.readouterr().out
    assert main(["workflow", str(tmp / "nope.json")]) == 1
