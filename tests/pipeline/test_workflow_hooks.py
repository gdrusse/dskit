"""ADR-0229: candidate generators, stage sequence, collectors and the no-gain rule.

The study's output is faked on disk; the names below (families, files, fields) are
restated here on purpose, as inputs, because the module under test must name none.
"""

import ast
import json
import pathlib
import sys
import textwrap

import pytest

from dskit.pipeline import workflow as wf
from dskit.pipeline import workflow_hooks as wh

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORDER = ["r1", "r2", "ctx", "a1", "a2", "b1", "c1"]
CONTRACTS = {
    "ret": {"fields": ["r1", "r2"]},
    "fam_a": {"fields": ["a1", "a2"]},
    "fam_b": {"fields": ["b1"]},
    "fam_c": {"fields": ["c1"]},
}
LABELS = {
    "names": "names", "families": "families", "by_family": "by_family", "fields": "fields",
    "other": "context", "winner": "winner", "name": "name", "spec": "spec", "round": "round",
    "candidates": "candidates", "scores": "scores", "added": "added",
}
PATHS = {
    "features": "params.idx", "encoder": "params.enc", "sequence_names": "seq",
    "sequence_indices": "seq_idx", "context_indices": "ctx_idx",
}
NAMING = {"incumbent": "core", "added": "core+{family}", "group": "{group_name}_{round}"}
SELECT = {
    "dir": "sel", "file": "rows.jsonl", "candidate": "cand", "partition": "part",
    "score": "skill", "winner": "won", "variant": "var",
}
SPEC = {"class": "M", "params": {"lr": 0.1}}


def forward_inputs(tmp_path, rnd=0, flags=None):
    admission = tmp_path / "admission.jsonl"
    flags = flags or {"required_fam_a": 1, "required_fam_b": 1, "required_fam_c": 0}
    admission.write_text(json.dumps(flags) + "\n")
    return {
        "base": {"metric": "m", "pool": ["fam_a", "fam_b", "fam_c"]},
        "spec": SPEC, "paths": PATHS, "order": ORDER, "contracts": CONTRACTS,
        "labels": LABELS, "start": {"families": ["ret"], "names": ["ctx"]},
        "current": str(tmp_path / "core.json"), "pool": ["fam_a", "fam_b", "fam_c"],
        "cap": 12, "admission": str(admission),
        "admit": {"flag": "required_{family}", "value": 1}, "naming": NAMING,
        "group_name": "fwd", "round": rnd,
    }


# -- SpecBuilder -------------------------------------------------------------


def test_subset_writes_sorted_positions_at_the_declared_path():
    spec = wh.SpecBuilder(SPEC, PATHS, ORDER).subset(["b1", "r1", "r1"])
    assert spec["params"]["idx"] == [0, 5] and spec["params"]["lr"] == 0.1
    assert SPEC["params"] == {"lr": 0.1}


def test_encoder_sequence_rows_become_subset_positions_and_context_the_rest():
    names = ["r1", "r2", "ctx", "b1"]
    spec = wh.SpecBuilder(SPEC, PATHS, ORDER).with_encoder(
        names, {"kind": "seq", "seq": [["r2"], ["r1"]], "size": 4})
    assert spec["params"]["enc"] == {
        "kind": "seq", "size": 4, "seq_idx": [[1], [0]], "ctx_idx": [2, 3]}


def test_encoder_without_a_sequence_is_copied_whole():
    spec = wh.SpecBuilder(SPEC, PATHS, ORDER).with_encoder(["r1"], {"kind": "flat"})
    assert spec["params"]["enc"] == {"kind": "flat"}


def test_unknown_feature_names_refuse():
    builder = wh.SpecBuilder(SPEC, PATHS, ORDER)
    with pytest.raises(wf.WorkflowError, match="not in the ordered list"):
        builder.subset(["zzz"])
    with pytest.raises(wf.WorkflowError, match="not in the feature set"):
        builder.with_encoder(["r1"], {"seq": [["r2"]]})


def test_spec_paths_must_be_complete():
    with pytest.raises(wf.WorkflowError, match="lack"):
        wh.SpecBuilder(SPEC, {"features": "x"}, ORDER)


# -- forward generator -------------------------------------------------------


def test_forward_round0_is_the_incumbent_plus_each_admitted_family(tmp_path):
    value = wh.ForwardCandidates().apply(forward_inputs(tmp_path))
    assert list(value["candidates"]) == ["core", "core+fam_a", "core+fam_b"]
    index = {n: c["params"]["idx"] for n, c in value["candidates"].items()}
    assert index["core"] == [0, 1, 2]
    assert index["core+fam_a"] == [0, 1, 2, 3, 4] and index["core+fam_b"] == [0, 1, 2, 5]
    assert value["groups"] == {"fwd_0": list(index)} == value["partitions"]
    assert value["max_candidates"] == 3 and value["metric"] == "m"


def test_forward_later_round_starts_from_the_core_file(tmp_path):
    inputs = forward_inputs(tmp_path, rnd=1)
    (tmp_path / "core.json").write_text(json.dumps(
        {"names": ["r1", "r2", "ctx", "b1"], "families": ["ret", "fam_b"]}))
    value = wh.ForwardCandidates().apply(inputs)
    assert list(value["candidates"]) == ["core", "core+fam_a"]
    assert value["candidates"]["core"]["params"]["idx"] == [0, 1, 2, 5]
    assert list(value["groups"]) == ["fwd_1"]


def test_forward_missing_inputs_defer_and_bad_ones_refuse(tmp_path):
    inputs = forward_inputs(tmp_path)
    inputs["admission"] = str(tmp_path / "absent.jsonl")
    with pytest.raises(wf.MissingInput):
        wh.ForwardCandidates().apply(inputs)
    with pytest.raises(wf.MissingInput):
        wh.ForwardCandidates().apply(forward_inputs(tmp_path, rnd=1))
    with pytest.raises(wf.WorkflowError, match="no required_fam_b"):
        wh.ForwardCandidates().apply(forward_inputs(tmp_path, flags={"required_fam_a": 1}))
    capped = forward_inputs(tmp_path)
    capped["cap"] = 2
    with pytest.raises(wf.WorkflowError, match="exceed the cap"):
        wh.ForwardCandidates().apply(capped)


def test_slot_names_are_overridable(tmp_path):
    inputs = forward_inputs(tmp_path)
    inputs["slots"] = {"candidates": "c", "groups": "g", "partitions": "p", "max_candidates": "n"}
    value = wh.ForwardCandidates().apply(inputs)
    assert {"c", "g", "p", "n"} <= set(value) and "candidates" not in value


# -- zoo and grid generators ---------------------------------------------------


def final_core(tmp_path, names=("r1", "r2", "ctx", "b1")):
    path = tmp_path / "final.json"
    path.write_text(json.dumps({"names": list(names), "families": ["ret"]}))
    return str(path)


ENCODERS = {"flat": {"kind": "flat"}, "seq": {"kind": "seq", "seq": [["r2"], ["r1"]]}}


def test_zoo_makes_one_group_per_encoder_over_the_final_core(tmp_path):
    value = wh.ZooCandidates().apply({
        "base": {"keep": 1}, "encoders": ENCODERS, "spec": SPEC, "paths": PATHS,
        "order": ORDER, "labels": LABELS, "final": final_core(tmp_path)})
    assert value["groups"] == {"flat": ["flat"], "seq": ["seq"]} and value["keep"] == 1
    assert value["candidates"]["flat"]["params"]["idx"] == [0, 1, 2, 5]
    assert value["candidates"]["seq"]["params"]["enc"]["ctx_idx"] == [2, 3]


def grid_inputs(tmp_path):
    winner = tmp_path / "winner.json"
    winner.write_text(json.dumps({"winner": "flat"}))
    return {
        "base": {"x": 1}, "encoders": ENCODERS, "spec": SPEC, "paths": PATHS,
        "order": ORDER, "labels": LABELS, "final": final_core(tmp_path),
        "axes": {"hidden": [[16], [32, 16]], "lr": [0.01, 0.1]},
        "axis_paths": {"hidden": "params.hidden", "lr": "params.lr"},
        "naming": {"pattern": "{enc}_h{hidden}_lr{lr}", "winner_var": "enc", "list_join": "x"},
        "winner": str(winner)}


def test_grid_is_the_axes_product_over_the_chosen_encoder(tmp_path):
    value = wh.GridCandidates().apply(grid_inputs(tmp_path))
    assert list(value["candidates"]) == [
        "flat_h16_lr0.01", "flat_h16_lr0.1", "flat_h32x16_lr0.01", "flat_h32x16_lr0.1"]
    last = value["candidates"]["flat_h32x16_lr0.1"]["params"]
    assert last["hidden"] == [32, 16] and last["lr"] == 0.1 and last["enc"] == {"kind": "flat"}
    assert value["groups"] == {"flat": list(value["candidates"])}


def test_grid_refuses_an_undeclared_chosen_encoder(tmp_path):
    inputs = grid_inputs(tmp_path)
    pathlib.Path(inputs["winner"]).write_text(json.dumps({"winner": "ghost"}))
    with pytest.raises(wf.WorkflowError, match="not declared"):
        wh.GridCandidates().apply(inputs)


# -- stage sequence -----------------------------------------------------------


def sequence_inputs(records):
    return {
        "records": records, "flags": {"stage": "-s", "part": "-p"}, "expand": "part",
        "wildcard": "*", "partitions_at": "doc.parts",
        "config": {"doc": {"parts": {"p1": [], "p2": []}}}}


def test_wildcard_expands_to_every_partition_in_order():
    tails = wh.DeclaredSequence().apply(sequence_inputs(
        [{"stage": "go", "part": "*"}, {"stage": "end"}, {"stage": "one", "part": "p2"}]))
    assert tails == [["-s", "go", "-p", "p1"], ["-s", "go", "-p", "p2"], ["-s", "end"],
                     ["-s", "one", "-p", "p2"]]


def test_sequence_refuses_a_field_with_no_flag_and_a_missing_partition_path():
    with pytest.raises(wf.WorkflowError, match="no flag"):
        wh.DeclaredSequence().apply(sequence_inputs([{"stage": "go", "extra": 1}]))
    bad = sequence_inputs([{"part": "*"}])
    bad["partitions_at"] = "doc.nope"
    with pytest.raises(wf.WorkflowError, match="no doc.nope"):
        wh.DeclaredSequence().apply(bad)


# -- collectors ----------------------------------------------------------------


def study(tmp_path, rows, name="study"):
    out = tmp_path / name
    (out / "sel").mkdir(parents=True)
    (out / "sel" / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(out)


def row(cand, skill, won=False, part="p", var="raw"):
    return {"cand": cand, "skill": skill, "won": won, "part": part, "var": var}


def config_for(output, names=("core", "core+fam_a", "core+fam_b")):
    idx = {"core": [0, 1, 2], "core+fam_a": [0, 1, 2, 3, 4], "core+fam_b": [0, 1, 2, 5]}
    return {"out": output, "cands": {n: {"params": {"idx": idx[n]}} for n in names}}


def collector_inputs(tmp_path, output, rnd=0, direction="max", margin=0.0):
    return {
        "select": SELECT, "direction": direction, "output_at": "out", "candidates_at": "cands",
        "labels": LABELS, "rounds": str(tmp_path / "rounds.jsonl"),
        "result": str(tmp_path / "core.json"), "order": ORDER, "contracts": CONTRACTS,
        "start": {"families": ["ret"], "names": ["ctx"]}, "current": str(tmp_path / "core.json"),
        "pool": ["fam_a", "fam_b", "fam_c"], "paths": PATHS, "naming": NAMING,
        "margin": margin, "config": config_for(output), "round": rnd}


def forward_rows(best="core+fam_b"):
    return [{**row(n, s), "won": n == best}
            for n, s in (("core", 1.0), ("core+fam_a", 2.0), ("core+fam_b", 3.0))]


def test_forward_collector_moves_the_core_and_records_the_round(tmp_path):
    inputs = collector_inputs(tmp_path, study(tmp_path, forward_rows()))
    result = wh.ForwardCollector().apply(inputs)
    core = json.loads((tmp_path / "core.json").read_text())
    assert core["names"] == ["r1", "r2", "ctx", "b1"] and core["families"] == ["ret", "fam_b"]
    assert core["by_family"] == {"ret": 2, "fam_b": 1, "context": 1}
    rounds = [json.loads(line) for line in (tmp_path / "rounds.jsonl").read_text().splitlines()]
    assert rounds == [{"round": 0, "candidates": ["core", "core+fam_a", "core+fam_b"],
                       "scores": {"core": 1.0, "core+fam_a": 2.0, "core+fam_b": 3.0},
                       "winner": "core+fam_b", "added": "fam_b"}]
    assert result.improved and not result.exhausted


def test_forward_collector_keeps_the_core_when_the_incumbent_wins(tmp_path):
    inputs = collector_inputs(tmp_path, study(tmp_path, forward_rows(best="core")))
    result = wh.ForwardCollector().apply(inputs)
    core = json.loads((tmp_path / "core.json").read_text())
    assert core["families"] == ["ret"] and not result.improved
    assert json.loads((tmp_path / "rounds.jsonl").read_text())["added"] is None


def test_margin_and_direction_decide_what_counts_as_a_gain(tmp_path):
    out = study(tmp_path, forward_rows())
    held = wh.ForwardCollector().apply(collector_inputs(tmp_path, out, margin=5.0))
    assert not held.improved
    low = [{**r, "won": r["cand"] == "core"} for r in forward_rows()]
    out2 = study(tmp_path, low, name="study2")
    fresh = collector_inputs(tmp_path, out2, direction="min")
    fresh["config"] = config_for(out2)
    assert not wh.ForwardCollector().apply(fresh).improved
    with pytest.raises(wf.WorkflowError, match="direction"):
        wh.ForwardCollector().apply(collector_inputs(tmp_path, out, direction="up"))


def test_rounds_append_after_round_zero_and_restart_at_zero(tmp_path):
    out = study(tmp_path, forward_rows())
    wh.ForwardCollector().apply(collector_inputs(tmp_path, out))
    names = ["core", "core+fam_a"]
    out1 = study(tmp_path, [row("core", 3.0, won=True), row("core+fam_a", 2.0)], name="s1")
    second = collector_inputs(tmp_path, out1, rnd=1)
    second["config"] = config_for(out1, names)
    wh.ForwardCollector().apply(second)
    assert len((tmp_path / "rounds.jsonl").read_text().splitlines()) == 2
    wh.ForwardCollector().apply(collector_inputs(tmp_path, out, rnd=0))
    assert len((tmp_path / "rounds.jsonl").read_text().splitlines()) == 1


def test_exhausted_when_the_winner_used_the_last_remaining_family(tmp_path):
    out = study(tmp_path, [row("core", 1.0), row("core+fam_b", 3.0, won=True)])
    inputs = collector_inputs(tmp_path, out)
    inputs["config"] = config_for(out, ["core", "core+fam_b"])
    assert wh.ForwardCollector().apply(inputs).exhausted


def test_collector_refuses_a_missing_study_file_and_a_round_with_no_winner(tmp_path):
    inputs = collector_inputs(tmp_path, str(tmp_path / "nothing"))
    with pytest.raises(wf.WorkflowError, match="wrote no"):
        wh.ForwardCollector().apply(inputs)
    out = study(tmp_path, [row("core", 1.0)])
    with pytest.raises(wf.WorkflowError, match="no winner"):
        wh.ForwardCollector().apply(collector_inputs(tmp_path, out))


def test_pick_collectors_choose_the_best_partition_winner(tmp_path):
    rows = [row("flat", 1.0, won=True, part="a"), row("flat", 0.5, part="a", var="cal"),
            row("seq", 2.0, won=True, part="b")]
    out = study(tmp_path, rows)
    inputs = collector_inputs(tmp_path, out)
    inputs["config"] = {"out": out, "cands": {"flat": {"p": 1}, "seq": {"p": 2}}}
    wh.ZooCollector().apply(inputs)
    assert json.loads((tmp_path / "core.json").read_text()) == {"winner": "seq"}
    assert len((tmp_path / "rounds.jsonl").read_text().splitlines()) == 3
    wh.GridCollector().apply(inputs)
    assert json.loads((tmp_path / "core.json").read_text()) == {"name": "seq", "spec": {"p": 2}}


def test_grid_collector_finds_a_candidate_whose_name_contains_a_dot(tmp_path):
    name = "enc_h16_lr0.001"
    out = study(tmp_path, [row(name, 1.0, won=True, part="a")])
    inputs = collector_inputs(tmp_path, out)
    inputs["config"] = {"out": out, "cands": {name: {"p": 7}}}
    wh.GridCollector().apply(inputs)
    assert json.loads((tmp_path / "core.json").read_text()) == {"name": name, "spec": {"p": 7}}


def test_ties_keep_the_first_winner(tmp_path):
    rows = [row("flat", 1.0, won=True, part="a"), row("seq", 1.0, won=True, part="b")]
    out = study(tmp_path, rows)
    inputs = collector_inputs(tmp_path, out)
    inputs["config"] = {"out": out, "cands": {"flat": {}, "seq": {}}}
    wh.ZooCollector().apply(inputs)
    assert json.loads((tmp_path / "core.json").read_text()) == {"winner": "flat"}


# -- stop rule ------------------------------------------------------------------


def test_no_gain_rule_stops_on_a_flat_or_exhausted_round():
    rule = wh.NoGainRule()
    assert not rule.verdict([], [])
    assert not rule.verdict([], [wh.RoundResult(True)])
    assert rule.verdict([], [wh.RoundResult(True), wh.RoundResult(False)])
    assert rule.verdict([], [wh.RoundResult(True, exhausted=True)])
    assert wf.STOP_RULES["no_gain"] is not None and not rule.stop(["a", "a"])


def test_default_verdict_defers_to_stop():
    assert wf.STOP_RULES["unchanged"].verdict(["a", "a"], []) is True


# -- registration and purity ---------------------------------------------------


def test_strategies_are_registered_with_their_roles():
    assert wf.HOOKS["forward_candidates"].role == wf.ROLE_VALUE
    assert wf.HOOKS["declared_sequence"].role == wf.ROLE_STAGES
    assert wf.HOOKS["zoo_collector"].role == wf.ROLE_COLLECT


def test_abstract_hooks_refuse_to_construct_incomplete():
    class Partial(wh.CandidateGenerator):
        def candidates(self, inputs):
            return {}

    with pytest.raises(TypeError):
        Partial()
    with pytest.raises(TypeError):
        wh.Collector()


#: Restated on purpose: values the module must read from args, never name in code.
BANNED = {"search", "stage", "--stage", "--partition", "mlp", "gru", "QQQ", "IWM", "forward",
          "selection", "candidates.jsonl", "raw", "calibrated", "warmup_mean_skill",
          "decision_wing_twcrps", "feature_indices", "core", "hidden", "learning_rate"}


def _code_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(getattr(node.body[0], "value", None), ast.Constant)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_the_module_names_no_project_value():
    found = set(_code_strings(ROOT / "dskit/pipeline/workflow_hooks.py"))
    assert not found & BANNED, sorted(found & BANNED)


def test_importing_the_module_pulls_in_only_the_standard_library():
    source = (ROOT / "dskit/pipeline/workflow_hooks.py").read_text(encoding="utf-8")
    roots = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.level == 0
             else None for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom)}
    imports = {a.name.split(".")[0] for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.Import) for a in n.names}
    assert (imports | roots) - {None} <= set(sys.stdlib_module_names) | {"__future__"}


# -- end to end through the runner ----------------------------------------------

FAKE_STUDY = textwrap.dedent("""
    import json, os, sys
    config = json.load(open(sys.argv[1]))
    tail = sys.argv[2:]
    out = config["output"]
    with open(os.path.join(os.path.dirname(out), "calls.log"), "a") as log:
        log.write(os.path.basename(out) + " " + " ".join(tail) + "\\n")
    if tail[:2] == ["-s", "end"]:
        scores = json.load(open(os.environ["FAKE_SCORES"]))[os.path.basename(out)]
        os.makedirs(os.path.join(out, "sel"), exist_ok=True)
        winner = max(scores, key=scores.get)
        with open(os.path.join(out, "sel", "rows.jsonl"), "w") as handle:
            for name, skill in scores.items():
                row = {"cand": name, "skill": skill, "won": name == winner,
                       "part": "p", "var": "raw"}
                handle.write(json.dumps(row) + "\\n")
""")


def study_manifest(tmp_path):
    (tmp_path / "fake.py").write_text(FAKE_STUDY)
    (tmp_path / "doc.json").write_text(json.dumps({
        "output": "${L.fwd.output}", "cands": "${sel.candidates}",
        "parts": "${sel.partitions}", "metric": "${sel.metric}"}))
    (tmp_path / "admission.jsonl").write_text(
        json.dumps({"required_fam_a": 1, "required_fam_b": 1, "required_fam_c": 0}) + "\n")
    ref = lambda path: "$args." + path  # noqa: E731
    hook = {
        "hook": "forward_candidates", "base": ref("sel"), "spec": ref("spec"),
        "paths": ref("paths"), "order": ref("order"), "contracts": ref("contracts"),
        "labels": ref("labels"), "start": ref("start"), "current": "$self.out.core",
        "pool": ref("sel.pool"), "cap": ref("sel.cap"), "admission": ref("admission"),
        "admit": ref("admit"), "naming": ref("naming"), "group_name": ref("sel.group_name"),
    }
    collect = {
        "hook": "forward_collector", "select": ref("select"), "direction": ref("direction"),
        "output_at": ref("at.output"), "candidates_at": ref("at.candidates"),
        "labels": ref("labels"), "rounds": "$self.out.rounds", "result": "$self.out.core",
        "order": ref("order"), "contracts": ref("contracts"), "start": ref("start"),
        "current": "$self.out.core", "pool": ref("sel.pool"), "paths": ref("paths"),
        "naming": ref("naming"), "margin": ref("margin"),
    }
    stages = {
        "hook": "declared_sequence", "records": ref("run"), "flags": ref("flags"),
        "expand": ref("expand"), "wildcard": ref("wildcard"), "partitions_at": ref("at.parts"),
    }
    args = {
        "tag": "t", "work": str(tmp_path / "w"), "fake": str(tmp_path / "fake.py"),
        "admission": str(tmp_path / "admission.jsonl"),
        "sel": {"metric": "m", "pool": ["fam_a", "fam_b", "fam_c"], "cap": 12,
                "group_name": "fwd", "stop": {"rule": "no_gain"}, "max_rounds": 5,
                "group": "core"},
        "spec": SPEC, "paths": PATHS, "order": ORDER, "contracts": CONTRACTS,
        "labels": LABELS, "start": {"families": ["ret"], "names": ["ctx"]},
        "admit": {"flag": "required_{family}", "value": 1}, "naming": NAMING,
        "select": SELECT, "direction": "max", "margin": 0.0, "run": [
            {"stage": "go", "part": "*"}, {"stage": "end"}],
        "flags": {"stage": "-s", "part": "-p"}, "expand": "part", "wildcard": "*",
        "at": {"output": "output", "candidates": "cands", "parts": "parts"},
    }
    manifest = {
        "args": args,
        "layout": {"fwd": {
            "dir": "{W}/fwd", "core": "{L.fwd.dir}/core_{T}.json",
            "rounds": "{L.fwd.dir}/rounds_{T}.jsonl", "output": "{L.fwd.dir}/study_{T}_{round}"}},
        "registry": {"study": {
            "template": "doc.json", "extends": None,
            "command": "{python} {in.fake} {config}"}},
        "steps": {"fwd": {
            "registry": "study", "out": ["core", "rounds"],
            "loop": {"until": "$args.sel.stop", "max_rounds": "$args.sel.max_rounds",
                     "group": "$args.sel.group"},
            "in": {"T": "$args.tag", "W": "$args.work", "fake": "$args.fake", "sel": hook},
            "stages": stages, "collect": collect}},
    }
    return manifest


@pytest.fixture
def study_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    scores = {
        "study_t_0": {"core": 1.0, "core+fam_a": 2.0, "core+fam_b": 3.0},
        "study_t_1": {"core": 3.0, "core+fam_a": 2.5},
    }
    (tmp_path / "scores.json").write_text(json.dumps(scores))
    monkeypatch.setenv("FAKE_SCORES", str(tmp_path / "scores.json"))
    manifest = study_manifest(tmp_path)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest))
    return tmp_path, manifest, str(path)


def test_the_manifest_validates_and_plans_before_any_file_exists(study_run):
    tmp, manifest, path = study_run
    (tmp / "admission.jsonl").unlink()
    wf.validate_manifest(manifest, str(tmp))
    assert wf.run_workflow(path, plan=True, out=lambda *a: None) == 0
    assert not (tmp / "w").exists()


def test_a_forward_loop_runs_stages_collects_and_stops_without_a_gain(study_run):
    tmp, manifest, path = study_run
    assert wf.run_workflow(path, out=lambda *a: None) == 0
    work = tmp / "w" / "fwd"
    core = json.loads((work / "core_t.json").read_text())
    assert core["families"] == ["ret", "fam_b"] and core["names"][-1] == "b1"
    rounds = [json.loads(x) for x in (work / "rounds_t.jsonl").read_text().splitlines()]
    assert [r["round"] for r in rounds] == [0, 1] and [r["added"] for r in rounds] == ["fam_b", None]
    assert (work / "calls.log").read_text().splitlines() == [
        "study_t_0 -s go -p fwd_0", "study_t_0 -s end",
        "study_t_1 -s go -p fwd_1", "study_t_1 -s end"]
    entry = json.loads((tmp / "w" / "workflow.json").read_text())["steps"]["fwd"]
    assert entry["rounds"] == 2 and entry["stopped"] == "rule"


def test_a_rerun_with_unchanged_inputs_is_skipped(study_run):
    tmp, manifest, path = study_run
    assert wf.run_workflow(path, out=lambda *a: None) == 0
    lines = []
    assert wf.run_workflow(path, out=lines.append) == 0
    assert any("skip" in str(x) for x in lines)


def test_a_collector_failure_stops_the_chain(study_run):
    tmp, manifest, path = study_run
    manifest["args"]["at"]["output"] = "nope"
    pathlib.Path(path).write_text(json.dumps(manifest))
    assert wf.run_workflow(path, out=lambda *a: None) == wf.EXIT_ERROR


def test_validation_refuses_a_hook_in_the_wrong_role_and_a_foreign_self_output(study_run):
    tmp, manifest, _ = study_run
    wrong = json.loads(json.dumps(manifest))
    wrong["steps"]["fwd"]["stages"] = wrong["steps"]["fwd"]["collect"]
    with pytest.raises(wf.WorkflowError, match="cannot be used here"):
        wf.validate_manifest(wrong, str(tmp))
    foreign = json.loads(json.dumps(manifest))
    foreign["steps"]["fwd"]["in"]["sel"]["current"] = "$self.out.nothing"
    with pytest.raises(wf.WorkflowError, match="not an output of this step"):
        wf.validate_manifest(foreign, str(tmp))
    literal = json.loads(json.dumps(manifest))
    literal["steps"]["fwd"]["stages"]["flags"] = {"stage": "-s"}
    with pytest.raises(wf.WorkflowError, match="literal"):
        wf.validate_manifest(literal, str(tmp))


# -- the lane-derived inputs: feature order, horizon limits, market series, reference indices ----------

FSPEC = {"availability": {"fam_a": {"fields": ["a1", "a2"], "require": [{"field": "mk_x_missing"}]},
                         "fam_b": {"fields": ["b1"], "require": []}}}
FSTART = {"names": ["c1", "c2"]}
FSHARED = {"slot": "@task", "names": ["b1", "c1", "@task", "a1", "mk_x", "mk_x_age"]}


def _order(**given):
    base = {"order": FSHARED, "task": {"T": "is_T"}, "lane": "T", "spec": FSPEC, "start": FSTART,
            "labels": {"names": "names"}}
    return wh.FeatureOrder().apply({**base, **given})


def test_the_shared_order_takes_the_lanes_task_feature_in_its_slot_and_gains_missing_fields():
    got = _order()
    assert got[:6] == ["b1", "c1", "is_T", "a1", "mk_x", "mk_x_age"]
    assert got[6:] == ["c2", "a2"]                       # declared fields the list lacks, appended
    assert _order(task={"U": "is_U"}, lane="U")[2] == "is_U"


def test_a_lane_without_a_task_feature_is_a_named_refusal():
    with pytest.raises(wf.WorkflowError, match="task feature.*lane 'Z'"):
        _order(task={"T": "is_T"}, lane="Z")


def test_without_a_shared_list_the_order_is_core_task_then_availability():
    assert _order(order=None) == ["c1", "c2", "is_T", "a1", "a2", "b1"]
    assert _order(order={}, task=None) == ["c1", "c2", "a1", "a2", "b1"]


FLIMITS = {"max_dte": 45, "x": 1}


def _limits(**given):
    base = {"limits": FLIMITS, "horizon": 7, "lane": "T",
            "contract": {"roles": ["dev", "evl"], "cells": "expected_cells", "bound": "max_dte"}}
    return wh.HorizonLimits().apply({**base, **given})


def test_expected_cells_are_derived_from_the_horizon_per_role_and_lane():
    got = _limits()
    assert got["expected_cells"] == {"dev": {"T": [7]}, "evl": {"T": [7]}}
    assert got["max_dte"] == 45 and got["x"] == 1 and "expected_cells" not in FLIMITS


def test_a_declared_cell_table_must_equal_the_derived_one():
    ok = {"dev": {"T": [7]}, "evl": {"T": [7]}}
    assert _limits(limits={**FLIMITS, "expected_cells": ok})["expected_cells"] == ok
    bad = {"dev": {"T": [5]}, "evl": {"T": [7]}}
    with pytest.raises(wf.WorkflowError, match="expected_cells.*horizon 7"):
        _limits(limits={**FLIMITS, "expected_cells": bad})


@pytest.mark.parametrize("bound", [6, None, "x"])
def test_a_max_dte_below_the_horizon_or_unusable_is_refused(bound):
    with pytest.raises(wf.WorkflowError, match="max_dte"):
        _limits(limits={**FLIMITS, "max_dte": bound})


FSERIES = {"mk_x": {"symbol": "XV"}, "mk_y": {"symbol": "YV"}}


def _series(**given):
    base = {"series": FSERIES, "own": {"T": "XV"}, "lane": "T", "drop": True, "spec": FSPEC,
            "order": ["a1"]}
    return wh.MarketSeries().apply({**base, **given})


def test_the_market_series_equal_to_the_lanes_own_index_is_dropped_when_nothing_requires_it():
    spec = {"availability": {"fam_b": {"fields": ["b1"], "require": []}}}
    assert _series(spec=spec) == {"mk_y": {"symbol": "YV"}}
    assert _series(spec=spec, drop=False) == FSERIES
    assert _series(spec=spec, own={"T": "other"}) == FSERIES


def test_dropping_a_series_a_family_or_the_order_requires_is_refused():
    with pytest.raises(wf.WorkflowError, match="mk_x.*fam_a"):
        _series()                                        # fam_a requires mk_x_missing
    spec = {"availability": {"fam_b": {"fields": ["b1"], "require": []}}}
    with pytest.raises(wf.WorkflowError, match="mk_x.*feature order"):
        _series(spec=spec, order=["a1", "mk_x_age"])


FREFS = {"model": "m", "resolve": {"pos": "position", "poss": "positions"},
        "models": {"m": {"class": "k", "params": {"pos": "b", "poss": ["c", "a"], "knots": 5},
                         "calibrate": False}}}


def test_reference_names_resolve_to_positions_against_the_order():
    got = wh.ReferenceIndices().apply({"references": FREFS, "order": ["a", "b", "c"]})
    assert got["models"]["m"]["params"] == {"position": 1, "positions": [2, 0], "knots": 5}
    assert list(got["models"]["m"]["params"]) == ["position", "positions", "knots"]
    assert "resolve" not in got and got["model"] == "m" and "resolve" in FREFS


def test_a_reference_name_missing_from_the_order_or_given_twice_is_refused():
    with pytest.raises(wf.WorkflowError, match="not in the ordered list"):
        wh.ReferenceIndices().apply({"references": FREFS, "order": ["a", "b"]})
    both = {**FREFS, "models": {"m": {"params": {"pos": "a", "position": 0}}}}
    with pytest.raises(wf.WorkflowError, match="both"):
        wh.ReferenceIndices().apply({"references": both, "order": ["a"]})
