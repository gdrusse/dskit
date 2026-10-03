"""ADR-0227 amendment: output pins and per-lane chains in the workflow runner."""

import copy
import json
import pathlib

import pytest

import dskit.pipeline
from dskit.pipeline import workflow as wf

ROOT = pathlib.Path(dskit.pipeline.__file__).parents[2]

EMIT = """
import json, sys
table, meta, who = sys.argv[1:4]
open(table, "w").write(json.dumps({"who": who}) + "\\n" + json.dumps({"who": "later"}) + "\\n")
open(meta, "w").write(json.dumps({"span": {"start": "2020-01-0" + str(len(who))}, "n": 4}))
"""

SEEN = """
import shutil, sys
shutil.copy(sys.argv[1], sys.argv[2])
"""

FIRST = {"name": "first-${T}"}
SECOND = {
    "name": "second-${T}",
    "pin": {"path": "${pin.path}", "sha256": "${pin.sha256}", "start": "${pin.start}",
            "n": "${pin.n}", "notes": "${untouched}"},
}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (tmp_path / "first.json").write_text(json.dumps(FIRST))
    (tmp_path / "second.json").write_text(json.dumps(SECOND))
    (tmp_path / "emit.py").write_text(EMIT)
    (tmp_path / "seen.py").write_text(SEEN)
    py = "{python} "
    manifest = {
        "args": {
            "tag": "a",
            "work": str(tmp_path / "w"),
            "emit": str(tmp_path / "emit.py"),
            "seen": str(tmp_path / "seen.py"),
        },
        "layout": {
            "s1": {"dir": "{W}/s1", "table": "{W}/s1/table-{T}.jsonl",
                   "meta": "{W}/s1/meta-{T}.json"},
            "s2": {"dir": "{W}/s2", "seen": "{W}/s2/seen-{T}.json"},
        },
        "registry": {
            "first": {
                "template": "first.json",
                "extends": None,
                "command": py + "{in.emit} {out.table} {out.meta} {in.T}",
                "pins": {
                    "span": {
                        "file": "table",
                        "sha256": True,
                        "values": {
                            "start": {"from": "meta", "path": "span.start"},
                            "n": {"from": "meta", "path": "n"},
                        },
                    }
                },
            },
            "second": {
                "template": "second.json",
                "extends": None,
                "command": py + "{in.seen} {config} {out.seen}",
            },
        },
        "steps": {
            "s1": {
                "registry": "first",
                "out": ["table", "meta"],
                "in": {"T": "$args.tag", "W": "$args.work", "emit": "$args.emit"},
            },
            "s2": {
                "registry": "second",
                "out": ["seen"],
                "in": {"T": "$args.tag", "W": "$args.work", "seen": "$args.seen",
                       "pin": "$s1.pin.span"},
            },
        },
    }
    return tmp_path, manifest


def write(tmp, manifest):
    path = tmp / "m.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def run(tmp, manifest, **kw):
    lines = kw.pop("lines", [])
    return wf.run_workflow(write(tmp, manifest), out=lines.append, **kw)


def ledger(tmp):
    return json.loads((tmp / "w" / "workflow.json").read_text())["steps"]


# -- pins ----------------------------------------------------------------------


def test_pin_reaches_the_next_step_as_path_hash_and_values(proj):
    tmp, manifest = proj
    assert run(tmp, manifest) == 0
    table = tmp / "w" / "s1" / "table-a.jsonl"
    seen = json.loads((tmp / "w" / "s2" / "seen-a.json").read_text())
    entry = ledger(tmp)["s1"]
    assert seen["pin"] == {
        "path": str(table), "sha256": entry["outputs"]["table"],
        "start": "2020-01-01", "n": 4, "notes": "${untouched}"}


def test_pin_hash_is_the_hash_of_the_file_bytes(proj):
    import hashlib

    tmp, manifest = proj
    run(tmp, manifest)
    table = tmp / "w" / "s1" / "table-a.jsonl"
    seen = json.loads((tmp / "w" / "s2" / "seen-a.json").read_text())
    assert seen["pin"]["sha256"] == hashlib.sha256(table.read_bytes()).hexdigest()


def test_pin_without_sha256_flag_omits_it(proj):
    tmp, manifest = proj
    manifest["registry"]["first"]["pins"]["span"]["sha256"] = False
    lines = []
    assert run(tmp, manifest, lines=lines) == 1
    assert any("pin.sha256" in str(x) for x in lines), lines


def test_a_changed_pinned_file_refuses_and_runs_nothing(proj):
    tmp, manifest = proj
    assert run(tmp, manifest, only="s1") == 0
    (tmp / "w" / "s1" / "table-a.jsonl").write_text('{"who": "tampered"}\n')
    lines = []
    assert run(tmp, manifest, only="s2", lines=lines) == 1
    assert any("differs from the ledger" in str(x) for x in lines), lines
    assert not (tmp / "w" / "s2" / "seen-a.json").exists()


def test_a_pinned_file_the_ledger_never_recorded_refuses(proj):
    tmp, manifest = proj
    assert run(tmp, manifest, only="s1") == 0
    (tmp / "w" / "workflow.json").unlink()
    lines = []
    assert run(tmp, manifest, only="s2", lines=lines) == 1
    assert any("ledger" in str(x) for x in lines), lines


def test_pin_value_path_missing_in_the_record_refuses(proj):
    tmp, manifest = proj
    manifest["registry"]["first"]["pins"]["span"]["values"]["n"]["path"] = "span.ghost"
    lines = []
    assert run(tmp, manifest, lines=lines) == 1
    assert any("span.ghost" in str(x) for x in lines), lines


def test_first_record_of_a_jsonl_output_is_used(proj):
    tmp, manifest = proj
    manifest["registry"]["first"]["pins"]["span"]["values"] = {
        "who": {"from": "table", "path": "who"}}
    (tmp / "second.json").write_text(json.dumps({"w": "${pin.who}"}))
    assert run(tmp, manifest) == 0
    assert json.loads((tmp / "w" / "s2" / "seen-a.json").read_text()) == {"w": "a"}


def test_a_changed_pin_reruns_the_consumer(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    first = ledger(tmp)["s2"]["config_hash"]
    manifest["args"]["tag"] = "ab"
    run(tmp, manifest)
    assert ledger(tmp)["s2"]["config_hash"] != first


def test_plan_defers_a_pin_before_the_file_exists(proj):
    tmp, manifest = proj
    lines = []
    assert run(tmp, manifest, plan=True, lines=lines) == 0, lines
    assert not (tmp / "w").exists()
    assert any("after=s1" in str(x) for x in lines)


@pytest.mark.parametrize("mutate", [
    lambda m: m["registry"]["first"]["pins"]["span"].update(file="ghost"),
    lambda m: m["registry"]["first"]["pins"]["span"].update(colour=1),
    lambda m: m["registry"]["first"]["pins"]["span"]["values"]["n"].update(**{"from": "ghost"}),
    lambda m: m["registry"]["first"]["pins"]["span"]["values"]["n"].pop("path"),
    lambda m: m["registry"]["first"]["pins"]["span"]["values"].update(
        path={"from": "meta", "path": "n"}),
    lambda m: m["steps"]["s2"]["in"].update(pin="$s1.pin.ghost"),
    lambda m: m["steps"]["s2"]["in"].update(pin="$s2.pin.span"),
    lambda m: m["registry"]["first"].update(pins=[]),
], ids=["file", "key", "from", "nopath", "reserved", "unknown_pin", "not_earlier", "shape"])
def test_pin_declaration_refusals(proj, mutate):
    tmp, manifest = proj
    manifest = copy.deepcopy(manifest)
    mutate(manifest)
    assert run(tmp, manifest) == 1
    assert not (tmp / "w").exists()


# -- lanes ---------------------------------------------------------------------


@pytest.fixture
def laned(proj):
    tmp, manifest = proj
    manifest["lanes"] = {"key": "names", "keyed": ["sizes"]}
    manifest["args"].update(names=["x", "yy"], sizes={"x": 1, "yy": 2})
    manifest["steps"]["s1"]["in"]["T"] = "$lane"
    manifest["steps"]["s2"]["in"]["T"] = "$lane"
    manifest["steps"]["s2"]["in"]["size"] = "$args.sizes"
    manifest["steps"]["s2"]["in"]["own"] = "$args.sizes.$lane"
    second = json.loads((tmp / "second.json").read_text())
    second["size"] = "${size}"
    second["own"] = "${own}"
    (tmp / "second.json").write_text(json.dumps(second))
    return tmp, manifest


def test_each_lane_runs_the_whole_chain_with_its_own_outputs(laned):
    tmp, manifest = laned
    assert run(tmp, manifest) == 0
    for lane in ("x", "yy"):
        assert (tmp / "w" / "s1" / f"table-{lane}.jsonl").exists()
        seen = json.loads((tmp / "w" / "s2" / f"seen-{lane}.json").read_text())
        assert seen["name"] == f"second-{lane}"
        assert seen["pin"]["path"].endswith(f"table-{lane}.jsonl")
        assert seen["pin"]["start"] == "2020-01-0" + str(len(lane))


def test_keyed_args_are_narrowed_to_the_lane_and_lane_segment_selects(laned):
    tmp, manifest = laned
    run(tmp, manifest)
    seen = json.loads((tmp / "w" / "s2" / "seen-yy.json").read_text())
    assert seen["size"] == {"yy": 2} and seen["own"] == 2


def test_ledger_records_the_lane_per_step(laned):
    tmp, manifest = laned
    run(tmp, manifest)
    steps = ledger(tmp)
    assert sorted(steps) == ["s1@x", "s1@yy", "s2@x", "s2@yy"]
    assert steps["s2@yy"]["lane"] == "yy" and steps["s2@yy"]["exit_code"] == 0


def test_lane_configs_do_not_overwrite_each_other(laned):
    tmp, manifest = laned
    run(tmp, manifest)
    names = {json.loads(p.read_text())["name"] for p in (tmp / "w" / "s1").glob("s1*.json")}
    assert names == {"first-x", "first-yy"}


def test_rerun_skips_every_unchanged_lane(laned):
    tmp, manifest = laned
    run(tmp, manifest)
    lines = []
    run(tmp, manifest, lines=lines)
    assert [x for x in lines if str(x).startswith("run ")] == []
    assert len([x for x in lines if "unchanged" in str(x)]) == 4


def test_only_filters_by_step_lane_or_both(laned):
    tmp, manifest = laned
    assert run(tmp, manifest, only="s1@yy") == 0
    assert (tmp / "w" / "s1" / "table-yy.jsonl").exists()
    assert not (tmp / "w" / "s1" / "table-x.jsonl").exists()
    assert not (tmp / "w" / "s2").exists()
    assert run(tmp, manifest, only="@x") == 0
    assert (tmp / "w" / "s2" / "seen-x.json").exists()
    assert not (tmp / "w" / "s2" / "seen-yy.json").exists()
    assert run(tmp, manifest, only="s1") == 0
    assert (tmp / "w" / "s1" / "table-x.jsonl").exists()


def test_from_accepts_a_lane(laned):
    tmp, manifest = laned
    assert run(tmp, manifest) == 0
    (tmp / "w" / "s2" / "seen-x.json").unlink()
    (tmp / "w" / "s2" / "seen-yy.json").unlink()
    assert run(tmp, manifest, from_step="s2@yy") == 0
    assert (tmp / "w" / "s2" / "seen-yy.json").exists()
    assert not (tmp / "w" / "s2" / "seen-x.json").exists()


@pytest.mark.parametrize("bad", ["s1@ghost", "ghost@x", "@ghost", "s1@"])
def test_unknown_step_or_lane_in_a_filter_refuses(laned, bad):
    tmp, manifest = laned
    assert run(tmp, manifest, only=bad) == 1
    assert not (tmp / "w").exists()


def test_a_filter_with_a_lane_but_no_lanes_declared_refuses(proj):
    tmp, manifest = proj
    assert run(tmp, manifest, only="s1@a") == 1


def test_plan_lists_every_lane(laned):
    tmp, manifest = laned
    lines = []
    assert run(tmp, manifest, plan=True, lines=lines) == 0, lines
    text = "\n".join(map(str, lines))
    assert "lane x" in text and "lane yy" in text
    assert "table-x.jsonl" in text and "table-yy.jsonl" in text
    assert not (tmp / "w").exists()


def test_a_lane_halting_stops_the_run_with_its_code(laned):
    tmp, manifest = laned
    manifest["registry"]["second"]["command"] = "{python} -c \"import sys; sys.exit(3)\" {in.seen}"
    lines = []
    assert run(tmp, manifest, lines=lines) == 3, lines
    assert "s2@x" in ledger(tmp) and "s1@yy" not in ledger(tmp)


@pytest.mark.parametrize("mutate", [
    lambda m: m["args"].update(names=[]),
    lambda m: m["args"].update(names=["x", "x"]),
    lambda m: m["args"].update(names="x"),
    lambda m: m["args"].update(names=["x", ""]),
    lambda m: m["lanes"].update(key="ghost"),
    lambda m: m["lanes"].update(keyed=["ghost"]),
    lambda m: m["lanes"].update(keyed=["tag"]),
    lambda m: m["args"].update(sizes={"x": 1}),
    lambda m: m["lanes"].update(colour=1),
    lambda m: m["layout"]["s1"].update(table="{W}/s1/table.jsonl"),
    lambda m: m["steps"]["s2"]["in"].update(own="$args.sizes.ghost"),
], ids=["empty", "dup", "notlist", "blank", "nokey", "nokeyed", "keyed_not_object",
        "keyed_lacks_lane", "unknown_param", "lanes_share_output", "bad_segment"])
def test_lane_declaration_refusals(laned, mutate):
    tmp, manifest = laned
    manifest = copy.deepcopy(manifest)
    mutate(manifest)
    assert run(tmp, manifest) == 1
    assert not (tmp / "w").exists()


def test_lane_reference_without_lanes_is_refused(proj):
    tmp, manifest = proj
    manifest["steps"]["s1"]["in"]["T"] = "$lane"
    assert run(tmp, manifest) == 1


def test_a_manifest_without_lanes_keeps_plain_ledger_keys(proj):
    tmp, manifest = proj
    run(tmp, manifest)
    assert sorted(ledger(tmp)) == ["s1", "s2"]
    assert "lane" not in ledger(tmp)["s1"]


def test_pin_check_is_per_lane(laned):
    tmp, manifest = laned
    run(tmp, manifest, only="s1")
    (tmp / "w" / "s1" / "table-yy.jsonl").write_text("{}\n")
    assert run(tmp, manifest, only="s2@x") == 0
    assert run(tmp, manifest, only="s2@yy") == 1


def test_layout_extras_resolve_through_the_L_root(proj):
    tmp, manifest = proj
    manifest["layout"]["s2"]["extra"] = "{L.s2.dir}/extra-{T}"
    (tmp / "second.json").write_text(json.dumps({"x": "${L.s2.extra}", "y": "${L.s1.table}"}))
    manifest["steps"]["s2"]["in"].pop("pin")
    lines = []
    assert run(tmp, manifest, lines=lines) == 0, lines
    seen = json.loads((tmp / "w" / "s2" / "seen-a.json").read_text())
    assert seen == {"x": str(tmp / "w" / "s2" / "extra-a"),
                    "y": str(tmp / "w" / "s1" / "table-a.jsonl")}


def test_a_layout_pattern_cycle_refuses(proj):
    tmp, manifest = proj
    manifest["layout"]["s1"]["table"] = "{L.s2.seen}"
    manifest["layout"]["s2"]["seen"] = "{L.s1.table}"
    assert run(tmp, manifest) == 1
    assert not (tmp / "w").exists()


def test_a_lane_keyed_object_missing_from_keyed_is_a_named_refusal(laned):
    tmp, manifest = laned
    manifest = copy.deepcopy(manifest)
    manifest["args"]["extra"] = {"x": 1, "yy": 2}
    lines = []
    assert run(tmp, manifest, lines=lines) == 1
    assert any("args.extra" in str(x) and "lanes.keyed" in str(x) for x in lines), lines


# -- a failing lane halts only itself ------------------------------------------


FAIL_CODE = 3
FAIL_LANE = "yy"


@pytest.fixture
def one_lane_fails(laned):
    """The ``laned`` manifest with the failing lane first, its step s1 exiting non-zero."""
    tmp, manifest = laned
    (tmp / "emit.py").write_text(
        EMIT.replace("table, meta, who = sys.argv[1:4]",
                     f"table, meta, who = sys.argv[1:4]\nif who == {FAIL_LANE!r}: sys.exit({FAIL_CODE})"))
    manifest["args"].update(names=[FAIL_LANE, "x"], sizes={FAIL_LANE: 2, "x": 1})
    return tmp, manifest


def test_one_lane_per_invocation_lets_the_others_finish_after_a_lane_halts(one_lane_fails):
    tmp, manifest = one_lane_fails
    assert run(tmp, manifest, only=f"@{FAIL_LANE}") == FAIL_CODE
    assert not (tmp / "w" / "s2" / f"seen-{FAIL_LANE}.json").exists()
    assert run(tmp, manifest, only="@x") == 0
    assert (tmp / "w" / "s2" / "seen-x.json").exists()
    steps = ledger(tmp)
    assert steps[f"s1@{FAIL_LANE}"]["exit_code"] == FAIL_CODE and "s2@yy" not in steps
    assert steps["s2@x"]["exit_code"] == 0


def test_a_failed_lane_stops_its_own_chain_at_the_failed_step(one_lane_fails):
    tmp, manifest = one_lane_fails
    lines = []
    assert run(tmp, manifest, only=f"@{FAIL_LANE}", lines=lines) == FAIL_CODE
    assert [x for x in lines if str(x).startswith("halted")] == [
        f"halted at s1@{FAIL_LANE} (exit {FAIL_CODE})"]
    assert not (tmp / "w" / "s2").exists()


def test_a_whole_run_stops_at_the_first_failed_lane_so_the_rest_need_their_own_call(one_lane_fails):
    tmp, manifest = one_lane_fails
    assert run(tmp, manifest) == FAIL_CODE
    assert not (tmp / "w" / "s1" / "table-x.jsonl").exists()
