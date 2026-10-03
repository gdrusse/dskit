"""Lane-keyed args may be derived from one pattern over the lane (ADR-0232 amendment)."""

import copy
import json

import pytest

from dskit.pipeline import workflow as wf

MANIFEST = {
    "args": {"names": ["Ab", "Cd"], "paths": {"*": "{lane_lower}/p.parquet"}},
    "lanes": {"key": "names", "keyed": ["paths"]},
    "layout": {"s": {"dir": "{T}", "o": "{T}/o"}},
    "registry": {"r": {"template": "t.json", "command": "{python} {config}", "extends": None}},
    "steps": {"s": {"registry": "r", "in": {"T": "$lane", "p": "$args.paths.$lane"},
                    "out": ["o"]}},
}


@pytest.fixture(autouse=True)
def base(tmp_path, monkeypatch):
    (tmp_path / "t.json").write_text(json.dumps({"name": "${T}", "p": "${p}"}))
    monkeypatch.chdir(tmp_path)


def lanes_args(manifest):
    flows = wf.lane_flows(manifest, ".")
    return {flow.lane: flow.args["paths"] for flow in flows}


def test_the_pattern_fills_every_lane_with_both_spellings():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"] = {"*": "{lane}:{lane_lower}"}
    assert lanes_args(manifest) == {"Ab": {"Ab": "Ab:ab"}, "Cd": {"Cd": "Cd:cd"}}


def test_an_explicit_entry_wins_over_the_pattern():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"]["Cd"] = "special"
    assert lanes_args(manifest) == {"Ab": {"Ab": "ab/p.parquet"}, "Cd": {"Cd": "special"}}


def test_a_non_string_pattern_value_is_copied_unchanged():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"] = {"*": [["2020-01-01", "2020-02-01"]]}
    assert lanes_args(manifest)["Ab"] == {"Ab": [["2020-01-01", "2020-02-01"]]}


def test_an_unknown_placeholder_is_refused_by_name():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"] = {"*": "{ghost}"}
    with pytest.raises(wf.WorkflowError, match="ghost"):
        wf.lane_flows(manifest, ".")


def test_without_a_pattern_a_missing_lane_is_still_refused():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"] = {"Ab": "x"}
    with pytest.raises(wf.WorkflowError, match="no entry for lane Cd"):
        wf.lane_flows(manifest, ".")


def test_an_object_holding_only_the_pattern_is_not_taken_for_an_unlisted_keyed_arg():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["other"] = {"*": "{lane}"}
    manifest["lanes"]["keyed"] = ["paths", "other"]
    assert set(lanes_args(manifest)) == {"Ab", "Cd"}


def write_overlays(tmp_path):
    (tmp_path / "m.json").write_text(json.dumps(MANIFEST))
    (tmp_path / "one.json").write_text(json.dumps({"names": ["Ab"], "paths": {"*": "one/{lane}"}}))
    (tmp_path / "two.json").write_text(json.dumps({"paths": {"*": "two/{lane}"}}))


def test_overlays_merge_in_order_and_the_last_wins(tmp_path):
    write_overlays(tmp_path)
    got = wf.load_overlays([str(tmp_path / "one.json"), str(tmp_path / "two.json")])
    assert got == {"names": ["Ab"], "paths": {"*": "two/{lane}"}}
    assert wf.load_overlays(str(tmp_path / "one.json"))["paths"] == {"*": "one/{lane}"}
    assert wf.load_overlays(None) == {} and wf.load_overlays([]) == {}


def test_an_unreadable_overlay_is_refused_by_path(tmp_path):
    with pytest.raises(wf.WorkflowError, match="nope.json"):
        wf.load_overlays([str(tmp_path / "nope.json")])


def test_the_workflow_command_plans_with_two_overlays(tmp_path):
    write_overlays(tmp_path)
    lines = []
    code = wf.run_workflow(str(tmp_path / "m.json"),
                           [str(tmp_path / "one.json"), str(tmp_path / "two.json")],
                           plan=True, out=lines.append)
    assert code == 0, lines
    assert any("Ab" in x for x in lines) and not any("Cd" in x for x in lines)


def test_a_key_beside_the_pattern_that_names_no_lane_is_refused():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"]["cd"] = "override"
    with pytest.raises(wf.WorkflowError, match="'cd'"):
        wf.lane_flows(manifest, ".")


def test_an_entry_for_a_lane_not_in_the_run_is_kept_beside_the_pattern():
    manifest = copy.deepcopy(MANIFEST)
    manifest["args"]["paths"]["Zz"] = "inherited"
    assert lanes_args(manifest)["Ab"] == {"Ab": "ab/p.parquet"}
