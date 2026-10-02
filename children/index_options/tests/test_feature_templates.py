"""Neutral templates for step 1b (feature engineering) and step 2 (feature availability).

Golden: expanding the step 1b template with the fixture reproduces the checked-in config for every
node step 1's new input does not touch; the touched nodes are asserted one by one.  Behaviour: both
expansions run on tiny synthetic panels and are checked against hand-counted oracles.  The expander
is the production one, ``dskit.pipeline.workflow.expand``.
"""

import copy
import json
from pathlib import Path

import pytest

from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node
from dskit.pipeline.planner import plan
from dskit.pipeline.workflow import expand, placeholder_names

CHILD = Path(__file__).resolve().parents[1]
TEMPLATES = CHILD / "configs" / "templates"
FIXTURE = CHILD / "tests" / "fixtures" / "args-features.json"
STEPS = {"step1b": "step1b-feature-engineering", "step2": "step2-feature-availability"}


def _template(step):
    return json.loads((TEMPLATES / f"{STEPS[step]}.json").read_text())


def _args(step):
    return copy.deepcopy(json.loads(FIXTURE.read_text())[step])


def _expanded(step):
    return expand(_template(step), _args(step))


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k != "notes"}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def _strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key != "notes":
                yield key
                yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def _words(text):
    import re

    return set(re.findall(r"[A-Za-z0-9_]+", text))


# -- shared template hygiene -------------------------------------------------------------------


@pytest.mark.parametrize("step", STEPS)
def test_every_fixture_key_is_used_and_every_placeholder_filled(step):
    assert placeholder_names(_template(step)) == set(_args(step))
    assert "${" not in json.dumps(_strip(_expanded(step)))


@pytest.mark.parametrize("step", STEPS)
def test_no_project_literal_survives_in_the_template(step):
    args = _args(step)
    forbidden = {args["T"]} | set(args["S"].values())
    forbidden |= set(args.get("V", {}).values()) | set(args.get("V", {}))
    forbidden |= set(args.get("M", {}))
    for fam in (args["families"].get("availability") or args["families"].get("engineered") or {}).values():
        forbidden |= set(fam["fields"])
    forbidden -= {"symbol", "rows", "dates"}  # structural names the template owns as literals
    for text in _strings(_template(step)):
        bare = text.replace("$each", "")
        import re

        bare = re.sub(r"\$\{[^}]*\}", "", bare)
        assert not (_words(bare) & forbidden), text


@pytest.mark.parametrize("step", STEPS)
def test_a_missing_value_refuses(step):
    args = _args(step)
    args.pop("S")
    with pytest.raises(Exception, match="no value"):
        expand(_template(step), args)


# -- step 1b golden -----------------------------------------------------------------------------

#: Nodes whose wiring changes because step 1's records replace selected.jsonl, with the reason.
STEP1B_PIPELINE_CHANGED = {
    "panel": "symbols is the lane's one ticker -> volatility index (the runner narrows args.vol_index)",
    "step1_selection": "renamed step1_targets: reads step 1's target-date records",
    "expected_rows": "totals the verified-cohort counts, not step 1's listed_forecasts",
    "expected_sum": "sums those counts",
}
STEP1B_FOREACH_CHANGED = {
    "selection": "filters step 1's target dates",
    "selection_group": "dropped: the cohort count carries the group",
    "selected_dte": "dropped: the horizon H is an input",
    "cohort": "dte compared with H",
    "horizon_rows": "bound is H",
    "features": "attaches from panel_keyed (the reader's rows plus any keyed family), not the reader",
}


def test_step1b_unchanged_nodes_equal_the_checked_in_config():
    got = _strip(_expanded("step1b"))
    want = _strip(json.loads((CHILD / "configs" / "run-step1b-feature-engineering.json").read_text()))
    assert got["name"] == want["name"]
    assert got["foreach"]["keys"] == [_args("step1b")["T"]]   # one ticker per document: the lane
    for name, node in want["pipeline"].items():
        if name not in STEP1B_PIPELINE_CHANGED:
            assert got["pipeline"][name] == node, name
    for name, node in want["foreach"]["pipeline"].items():
        if name not in STEP1B_FOREACH_CHANGED:
            assert got["foreach"]["pipeline"][name] == node, name
    assert got["outputs"] == want["outputs"]
    ours, theirs = (dict(doc["pipeline"]["panel"]["params"]) for doc in (got, want))
    lane = _args("step1b")["T"]
    assert ours.pop("symbols") == {lane: theirs.pop("symbols")[lane]}
    assert ours == theirs


def test_step1b_changed_nodes_read_step1_and_take_the_horizon_as_input():
    out = _expanded("step1b")
    args = _args("step1b")
    old = json.loads((CHILD / "configs" / "run-step1b-feature-engineering.json").read_text())
    t = out["pipeline"]["step1_targets"]["params"]
    assert (t["root"], t["source"], t["stream"]) == (
        args["target_root"], args["handoff"]["source"], args["handoff"]["streams"]["target_dates"])
    assert t["key_fields"] == ["symbol", "quote_date"]
    f = out["foreach"]["pipeline"]
    old_f = old["foreach"]["pipeline"]
    assert f["selection"]["inputs"] == {"records": "$step1_targets.records"}
    assert f["cohort"]["params"]["where"][1]["value"] == args["H"]
    assert f["cohort"]["params"]["where"][1] == {"field": "actual_calendar_dte", "op": "==", "value": 7}
    assert f["horizon_rows"]["params"]["where"][2]["value"] == args["H"]
    wanted = old_f["horizon_rows"]["params"]["where"]
    assert f["horizon_rows"]["params"]["where"][:2] + f["horizon_rows"]["params"]["where"][3:] == (
        wanted[:2] + wanted[3:])
    assert "selected_dte" not in f
    assert f["cohort_check"]["params"] == {"key": "quote_date", "how": "strict"}
    assert out["pipeline"]["panel_evidence"]["params"]["path"] == args["L"]["step1b"]["panel"]
    assert out["pipeline"]["panel_evidence"]["params"]["expect"] == "$expected_value.table.all"


def test_step1b_expansion_plans_with_the_proposed_reader_resolved(monkeypatch, tmp_path):
    monkeypatch.chdir(CHILD)
    path = tmp_path / "step1b.json"
    path.write_text(json.dumps(_expanded("step1b")))
    order = plan(load_document(str(path))).order
    stems = {name.split("__")[0] for name in order}
    assert {"source", "step1_targets", "panel", "cohort_check", "features", "panel_evidence"} <= stems


# -- test double for the two onboarding readers ------------------------------------------------


class StaticRows(Node):
    """Serve ``params.rows`` as ``records`` in place of a store-backed reader."""

    role = "data"
    outputs = ("records",)
    _PARAMS = ("rows",)

    @classmethod
    def validate_params(cls, params):
        return [] if "rows" in params else ["rows is required"]

    def run(self, ctx, inputs):
        return {"records": [dict(r) for r in self.params["rows"]]}


def _serve(doc, **rows_by_node):
    for name, rows in rows_by_node.items():
        doc["pipeline"][name] = {"uses": f"{__name__}:StaticRows", "params": {"rows": rows}}
    return doc


def _run(doc, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return run_document(PipelineDocument.from_obj(doc), asof="2026-10-02", journal=False)


def _failed(doc, monkeypatch, tmp_path):
    try:
        result = _run(doc, monkeypatch, tmp_path)
    except Exception as err:  # noqa: BLE001 - any refusal counts
        return str(err)
    return None if result.state == "ran" else str(result)


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _day(i):
    return f"2021-03-{i + 1:02d}"


# -- step 1b behaviour --------------------------------------------------------------------------


def _small_1b_args(out):
    args = _args("step1b")
    S = args["S"]
    args["T"] = "ZZA"
    args["families"] = {
        "engineered": {"fam": {"fields": ["f1"], "withheld_fields": {}, "clock_note": "test"}},
        "carried": {"density": {"fields": ["expiry_density"], "withheld_fields": {}, "clock_note": "test"}},
        "attach": {"identity": [S["symbol"], S["quote_date"], S["expiry"]],
                   "agree_fields": [S["target"]], "max_absent_fraction": 0.0, "column_prefix": "fe_"},
        "columns": [S["symbol"], S["quote_date"], S["expiry"], S["target"], "f1"],
        "pending": {"x": {"status": "pending", "reason": "test"}},
        "keyed_key": [S["symbol"], S["quote_date"]],
        "keyed_naming": {"age": "_age_days", "missing": "_missing"},
        "keyed_tables": {},
    }
    args["L"] = {"step1b": {"panel": str(out / "panel.jsonl")}}
    args["W"] = str(out / "runs")
    return args, S


def _small_1b_doc(out, target_days):
    args, S = _small_1b_args(out)
    doc = expand(_template("step1b"), args)

    def row(i, expiry, dte, planned, target):
        return {S["symbol"]: "ZZA", S["quote_date"]: _day(i), S["expiry"]: _day(expiry),
                S["dte"]: dte, S["planned_dte"]: planned, S["target"]: target}

    prepared = [
        row(0, 7, 7, 7, 0.01), row(0, 3, 3, 3, 0.02),   # d0: cohort row plus a nearer expiry
        row(1, 8, 7, 7, 0.03),                          # d1: cohort row
        row(2, 9, 7, 7, None),                          # d2: unsettled, not in the cohort
        row(3, 9, 6, 6, 0.04),                          # d3: other horizon, not in the cohort
    ]
    keep = (S["symbol"], S["quote_date"], S["expiry"], S["target"])
    reader = [{**{k: r[k] for k in keep}, "f1": 100 + i} for i, r in enumerate(prepared)]
    targets = [{S["symbol"]: "ZZA", S["quote_date"]: _day(i)} for i in target_days]
    return _serve(doc, source=prepared, panel=reader, step1_targets=targets), S


def test_step1b_keeps_the_horizon_cohort_attaches_features_and_counts_expiries(monkeypatch, tmp_path):
    doc, S = _small_1b_doc(tmp_path, target_days=[0, 1, 2, 3])
    result = _run(doc, monkeypatch, tmp_path)
    assert result.state == "ran", result
    rows = _jsonl(tmp_path / "panel.jsonl")
    assert [(r[S["quote_date"]], r["f1"], r["expiry_density"]) for r in rows] == [
        (_day(0), 100, 2), (_day(1), 102, 1)]


def test_step1b_refuses_a_cohort_date_step1_does_not_list(monkeypatch, tmp_path):
    doc, _ = _small_1b_doc(tmp_path, target_days=[0])
    assert _failed(doc, monkeypatch, tmp_path) is not None
    assert not (tmp_path / "panel.jsonl").exists()


# -- step 2 -------------------------------------------------------------------------------------

#: Three families over a small schema.  a: two fields; b: one field plus a require; c: one field
#: plus a missing-flag companion (the contract carries the flag as an explicit require).
SPEC = {
    "a": {"fields": ["a1", "a2"]},
    "b": {"fields": ["b1"], "require": [{"field": "bq", "op": "==", "value": 1}]},
    "c": {"fields": ["c1"], "require": [{"field": "c1_missing", "op": "==", "value": 0}]},
}
SCHEMA_EXTRA = ["a1", "a2", "b1", "bq", "c1", "c1_missing"]

GOOD = {"a1": 1.0, "a2": 2.0, "b1": 3.0, "bq": 1, "c1": 4.0, "c1_missing": 0}


def _panel_row(S, symbol, i, expiry=0, target=0.01, **over):
    return {S["symbol"]: symbol, S["quote_date"]: _day(i), S["expiry"]: _day(i + 7 + expiry),
            S["target"]: target, **GOOD, **over}


def _step2_rows(S):
    za = [
        _panel_row(S, "ZZA", 0),                                    # d0: abc
        _panel_row(S, "ZZA", 1), _panel_row(S, "ZZA", 1, 1, bq=0),  # d1: one bad row -> b no -> ac
        _panel_row(S, "ZZA", 2, a2=None),                           # d2: bc
        _panel_row(S, "ZZA", 3, c1=None),                           # d3: ab
        _panel_row(S, "ZZA", 4, a1=None, bq=0, c1_missing=1),       # d4: none
        _panel_row(S, "ZZA", 5, target=None),                       # d5: unsettled, not in the cohort
    ]
    zb = [_panel_row(S, "ZZB", 0), _panel_row(S, "ZZB", 1, b1=None), _panel_row(S, "ZZB", 2)]
    return za + zb


def _step2_doc(out, symbol, step1_days):
    """The expanded step-2 document for one lane, served a small panel and ``step1_days`` as step 1."""
    args = _args("step2")
    S = args["S"]
    args["T"] = symbol
    args["families"] = {"availability": SPEC,
                        "schema_fields": [S["symbol"], S["quote_date"], S["expiry"], S["target"],
                                          *SCHEMA_EXTRA]}
    args["L"] = {"step2": {k: str(out / f"{k}.jsonl") for k in ("dates", "summary", "combinations", "cohort")}}
    args["W"] = str(out / "runs")
    doc = expand(_template("step2"), args)
    targets = [{S["symbol"]: sym, S["quote_date"]: _day(i)} for sym, days in step1_days.items() for i in days]
    return _serve(doc, panel=_step2_rows(S), targets=targets), S


def _run_lane(tmp_path, monkeypatch, symbol, step1_days):
    out = tmp_path / symbol
    out.mkdir()
    doc, S = _step2_doc(out, symbol, step1_days)
    result = _run(doc, monkeypatch, out)
    assert result.state == "ran", result
    return {name: _jsonl(out / f"{name}.jsonl") for name in ("dates", "summary", "combinations", "cohort")}, S


def test_step2_flags_summary_cohort_and_combination_counts_match_the_hand_oracle(monkeypatch, tmp_path):
    # Step 1 lists a day more than each cohort (d5 for ZZA is also unsettled; d4 for ZZB has no
    # expiry at the horizon): the cohort is a subset of step 1's dates, never required to equal it.
    za, S = _run_lane(tmp_path, monkeypatch, "ZZA", {"ZZA": range(6), "ZZB": range(3)})
    zb, _ = _run_lane(tmp_path, monkeypatch, "ZZB", {"ZZA": range(5), "ZZB": range(4)})

    def flags(rows):
        return {r[S["quote_date"]]: "".join("1" if r[f"available_{n}"] == "yes" else "0" for n in "abc")
                for r in rows}

    assert flags(za["dates"]) == {_day(0): "111", _day(1): "101", _day(2): "011", _day(3): "110",
                                  _day(4): "000"}
    assert flags(zb["dates"]) == {_day(0): "111", _day(1): "101", _day(2): "111"}

    def combos(lane):
        return {tuple(r["combination"]): r["dates"] for r in lane["combinations"]}

    assert len(za["combinations"]) == len(zb["combinations"]) == 2 ** 3
    assert combos(za) == {(): 5, ("a",): 3, ("b",): 3, ("c",): 3, ("a", "b"): 2, ("a", "c"): 2,
                          ("b", "c"): 2, ("a", "b", "c"): 1}
    assert combos(zb) == {(): 3, ("a",): 3, ("b",): 2, ("c",): 3, ("a", "b"): 2, ("a", "c"): 3,
                          ("b", "c"): 2, ("a", "b", "c"): 2}

    summary = {r["family"]: r for r in za["summary"]}
    a = summary["a"]
    assert (a["rows"], a["dates_yes"], a["dates_no"]) == (6, 3, 2)
    assert (a["first_date"], a["last_date"]) == (_day(0), _day(3))
    assert summary["b"]["dates_yes"] == 3 and {r["family"]: r for r in zb["summary"]}["b"]["dates_yes"] == 2

    (cohort,) = za["cohort"]
    assert (cohort["dates"], cohort["rows"]) == (5, 6)
    assert (cohort["first_date"], cohort["last_date"]) == (_day(0), _day(4))
    assert (zb["cohort"][0]["dates"], zb["cohort"][0]["rows"]) == (3, 3)


def test_step2_refuses_a_cohort_date_step1_does_not_list(monkeypatch, tmp_path):
    doc, _ = _step2_doc(tmp_path, "ZZA", {"ZZA": range(4)})   # the panel holds ZZA's d4
    assert _failed(doc, monkeypatch, tmp_path) is not None
    assert not (tmp_path / "dates.jsonl").exists()


def test_step2_refuses_a_ticker_missing_from_step1(monkeypatch, tmp_path):
    doc, _ = _step2_doc(tmp_path, "ZZA", {"ZZB": range(3)})
    assert _failed(doc, monkeypatch, tmp_path) is not None


def test_step2_expansion_plans_without_per_family_nodes(monkeypatch, tmp_path):
    monkeypatch.chdir(CHILD)
    path = tmp_path / "step2.json"
    path.write_text(json.dumps(_expanded("step2")))
    order = plan(load_document(str(path))).order
    assert "availability" in {name.split("__")[0] for name in order}
    assert len(order) < 40
    assert not [n for n in order if n.startswith(("check_", "summary_family", "expand_"))]


def test_the_fixture_availability_spec_is_the_manifests():
    manifest = json.loads((CHILD / "configs" / "workflow.json").read_text())["args"]["families"]
    spec = _args("step2")["families"]
    assert spec["availability"] == manifest["availability"]
    assert spec["schema_fields"] == manifest["schema_fields"]
    assert set(spec["availability"]) == set(manifest["flags"]) == set(manifest["documentation"])
