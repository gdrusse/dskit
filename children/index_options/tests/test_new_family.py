"""A new data family is an args change: steps 1b to 4 on a made-up ticker and family 'zz_family'.

The family is a table keyed by (symbol, date) in an onboarded store.  Nothing here edits a
template or a node: the ``families.keyed`` declaration is the whole change, and removing it
removes the family from every step.  Real pieces run end to end: the store read, the attach,
the availability node, the admission node and the forward-candidate hook.  Only the panel
reader (a fixed set of exact-expiry tables) and step 1's records are served as static rows.
"""

import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import dskit.pipeline.workflow_hooks as hooks  # noqa: F401 - registers the hooks
from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.pipeline import workflow as wf
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.node import Node
from dskit.pipeline.workflow import expand

CHILD = Path(__file__).resolve().parents[1]
CONFIGS = CHILD / "configs"
TEMPLATES = CONFIGS / "templates"
FEATURES = json.loads((CHILD / "tests" / "fixtures" / "args-features.json").read_text())
STUDY = json.loads((CHILD / "tests" / "fixtures" / "args-study.json").read_text())
MANIFEST = json.loads((CONFIGS / "workflow.json").read_text(encoding="utf-8"))
_FO = MANIFEST["args"]["feature_order"]
_SHIPPED_QQQ = [MANIFEST["args"]["feature_limits"]["task_features"]["QQQ"] if n == _FO["slot"] else n
                for n in _FO["names"]]

FAMILY = "zz_family"
TICKER = "ZZT"
N_DAYS = 30
MAX_AGE = 2
#: Store rows exist on every day except those with i % 3 == 2, and none on days 13 to 15: a gap
#: long enough that days 15 and 16 have no row within MAX_AGE days before them.
STORED = [i for i in range(N_DAYS) if i % 3 != 2 and not 13 <= i <= 15]
LAG = 2


def _day(i):
    return (date(2021, 3, 1) + timedelta(days=i)).isoformat()


def _fresh(i):
    """The oracle: a stored row strictly before day i, at most MAX_AGE days old."""
    return any(0 < i - j <= MAX_AGE for j in STORED)


def _template(name):
    return json.loads((TEMPLATES / name).read_text())


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


def _run(doc, monkeypatch, cwd):
    monkeypatch.chdir(cwd)
    result = run_document(PipelineDocument.from_obj(doc), asof="2026-10-02", journal=False)
    assert result.state == "ran", result
    return result


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


# -- the store holding the new family's table ------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    data = tmp_path / "zz-data"
    data.mkdir()
    rows = [{"sym": TICKER, "day": _day(i), "za": float(i), "zb": 100.0 + i} for i in STORED]
    rows.append({"sym": "OTHER", "day": _day(1), "za": -1.0, "zb": -1.0})   # another entity
    with open(data / "zz.jsonl", "w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(r) + "\n" for r in rows)
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "zzsrc", "catalog_source": "zz", "connector": "localfiles",
        "config": {"path": str(data), "effective_field": "day"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "zzsrc", "zz", "backfill")
    return root.root


def _zz(root):
    """The whole declaration of the new family: the only thing a user writes."""
    return {
        "sources": [{"root": root, "source": "zzsrc", "stream": "zz", "key_fields": ["sym", "day"],
                     "columns": {"za": "zz_a", "zb": "zz_b"}}],
        "fields": ["zz_a", "zz_b"], "max_age_days": MAX_AGE,
        "lookback": "latest stored row before the entry date, at most 2 days old",
        "clock_note": "prior-day value; synthetic",
    }


def _families(root=None):
    """A made-up built-in family 'base' (column b1) beside the manifest's step 1b structure."""
    S = FEATURES["step1b"]["S"]
    spec = {
        "availability": {"base": {"fields": ["b1"], "require": []}},
        "documentation": {"base": {"lookback": "none", "clock": "none"}},
        "engineered": {"base": {"fields": ["b1"], "withheld_fields": {}, "clock_note": "test"}},
        "carried": {"density": {"fields": ["expiry_density"], "withheld_fields": {}, "clock_note": "t"}},
        "attach": {"identity": [S["symbol"], S["quote_date"], S["expiry"]],
                   "agree_fields": [S["target"]], "max_absent_fraction": 0.0, "column_prefix": "fe_"},
        "columns": [S["symbol"], S["quote_date"], S["expiry"], S["target"], "b1"],
        "pending": {"x": {"status": "pending", "reason": "test"}},
        "flags": {"base": {"flag": "available_base"}},
        "schema_fields": [S["symbol"], S["quote_date"], S["expiry"], S["target"], "b1"],
        "dates_fields": [S["symbol"], S["quote_date"], "available_base"],
        "keyed_key": [S["symbol"], S["quote_date"]],
        "keyed_naming": {"age": "_age_days", "missing": "_missing"},
        "keyed": {FAMILY: _zz(root)} if root else {},
    }
    return spec, S


# -- the hook derives every list ------------------------------------------------------------------


def test_one_declaration_extends_every_derived_list_and_removing_it_restores_them(store):
    on, _ = _families(store)
    off, _ = _families(None)
    merged_on, merged_off = hooks.FamiliesSpec().apply({"spec": on}), hooks.FamiliesSpec().apply({"spec": off})
    outputs = ["zz_a", "zz_b", "zz_a_age_days", "zz_b_age_days", "zz_a_missing", "zz_b_missing"]
    assert merged_on["engineered"][FAMILY]["fields"] == outputs
    assert merged_on["availability"][FAMILY]["fields"] == ["zz_a", "zz_b"]
    assert {"field": "zz_a_age_days", "op": "<=", "value": MAX_AGE} in merged_on["availability"][FAMILY]["require"]
    assert merged_on["flags"][FAMILY] == {"flag": f"available_{FAMILY}"}
    assert merged_on["dates_fields"][-1] == f"available_{FAMILY}"
    assert merged_on["schema_fields"][-6:] == outputs and FAMILY in merged_on["documentation"]
    (table,) = merged_on["keyed_tables"].values()
    assert table["columns"] == {"za": "zz_a", "zb": "zz_b"} and table["max_age_days"] == MAX_AGE
    assert merged_off == {**off, "keyed_tables": {}}   # nothing keyed: the block passes through
    assert FAMILY not in json.dumps(merged_off)


def test_the_feature_order_and_pool_gain_the_family_and_a_lane_without_an_order_is_derived(store):
    on, _ = _families(store)
    base = {"order": {"slot": "@t", "names": ["b1", "@t", "other"]}, "task": {"AAA": "is_AAA"},
            "lane": "AAA", "spec": on, "start": {"names": ["c0"]}, "labels": {"names": "names"}}
    assert hooks.FeatureOrder().apply(base) == [
        "b1", "is_AAA", "other", "c0", "zz_a", "zz_b", "zz_a_age_days", "zz_b_age_days",
        "zz_a_missing", "zz_b_missing"]
    derived = hooks.FeatureOrder().apply({**base, "order": None})   # no shared list at all
    assert derived[:3] == ["c0", "is_AAA", "b1"] and derived[3:5] == ["zz_a", "zz_b"] and len(derived) == 9
    assert hooks.KeyedPool().apply({"base": ["base"], "spec": on}) == ["base", FAMILY]
    assert hooks.KeyedCap().apply({"base": 2, "pool": ["base"], "spec": on}) == 3
    off, _ = _families(None)
    assert hooks.KeyedPool().apply({"base": ["base"], "spec": off}) == ["base"]
    assert hooks.KeyedCap().apply({"base": 2, "pool": ["base"], "spec": off}) == 2
    assert FAMILY not in " ".join(hooks.FeatureOrder().apply({**base, "spec": off}))


@pytest.mark.parametrize("edit, message", [
    (lambda z: z.update(fields=["nope"]), "not among the sources"),
    (lambda z: z["sources"][0].update(key_fields=["only"]), "one stream field per keyed_key"),
    (lambda z: z.pop("clock_note"), "clock_note is required"),
    (lambda z: z.update(max_age_days=-1), "max_age_days"),
    (lambda z: z.update(typo=1), "unknown key"),
])
def test_a_malformed_declaration_refuses_by_name(store, edit, message):
    spec, _ = _families(store)
    edit(spec["keyed"][FAMILY])
    with pytest.raises(wf.WorkflowError, match=message):
        hooks.FamiliesSpec().apply({"spec": spec})


def test_a_keyed_family_cannot_shadow_a_builtin_one(store):
    spec, _ = _families(store)
    spec["keyed"]["base"] = spec["keyed"].pop(FAMILY)
    with pytest.raises(wf.WorkflowError, match="already a built-in"):
        hooks.FamiliesSpec().apply({"spec": spec})


# -- steps 1b, 2 and 3, run -----------------------------------------------------------------------


def _step1b(tmp_path, merged, S):
    args = copy.deepcopy(FEATURES["step1b"])
    args.update(T=TICKER, families=merged, H=7)
    args["L"] = {"step1b": {"panel": str(tmp_path / "panel.jsonl")}}
    args["W"] = str(tmp_path / "runs1b")
    doc = expand(_template("step1b-feature-engineering.json"), args)
    prepared = [{S["symbol"]: TICKER, S["quote_date"]: _day(i), S["expiry"]: _day(i + 7),
                 S["dte"]: 7, S["planned_dte"]: 7, S["target"]: 0.01} for i in range(N_DAYS)]
    reader = [{**{k: r[k] for k in (S["symbol"], S["quote_date"], S["expiry"], S["target"])}, "b1": 1.0}
              for r in prepared]
    targets = [{S["symbol"]: TICKER, S["quote_date"]: _day(i)} for i in range(N_DAYS)]
    return _serve(doc, source=prepared, panel=reader, step1_targets=targets)


def _step2(tmp_path, merged, S, panel):
    args = copy.deepcopy(FEATURES["step2"])
    args["T"] = TICKER
    args["families"] = {"availability": merged["availability"], "schema_fields": merged["schema_fields"]}
    args["L"] = {"step2": {k: str(tmp_path / f"{k}.jsonl") for k in ("dates", "summary", "combinations", "cohort")}}
    args["W"] = str(tmp_path / "runs2")
    doc = expand(_template("step2-feature-availability.json"), args)
    targets = [{S["symbol"]: TICKER, S["quote_date"]: _day(i)} for i in range(N_DAYS)]
    return _serve(doc, panel=panel, targets=targets)


def _step3(tmp_path, merged, dates):
    args = copy.deepcopy(STUDY["step3"])
    args.update(T=TICKER, H=LAG)
    S = args["S"]
    args["families"] = {"flags": merged["flags"], "dates_fields": merged["dates_fields"]}
    args["split"] = {"tau": 0.75, "holdout_fraction": 0.2,
                     "folds": {"train_n": 4, "val_n": 3, "step_n": 3, "warmup_folds": 1}}
    args["L"] = {"step3": {"fold_table": str(tmp_path / "fold-table.jsonl"),
                           "admission": str(tmp_path / "admission.jsonl"), "run_root": str(tmp_path / "runs3")}}
    doc = expand(_template("step3-holdout-folds.json"), args)
    panel = [{S["symbol"]: TICKER, S["quote_date"]: _day(i), S["dte"]: LAG, S["expiry"]: _day(i + LAG),
              S["settlement"]: _day(i + LAG), S["target"]: 0.01 * (i % 5)} for i in range(N_DAYS)]
    return _serve(doc, step2_dates=dates, source=panel)


def _chain(tmp_path, monkeypatch, root):
    """Run steps 1b, 2 and 3 for one family declaration; return what each wrote."""
    spec, S = _families(root)
    merged = hooks.FamiliesSpec().apply({"spec": spec})
    for step in ("1b", "2", "3"):
        (tmp_path / step).mkdir()
    _run(_step1b(tmp_path / "1b", merged, S), monkeypatch, tmp_path / "1b")
    panel = _jsonl(tmp_path / "1b" / "panel.jsonl")
    _run(_step2(tmp_path / "2", merged, S, panel), monkeypatch, tmp_path / "2")
    dates = _jsonl(tmp_path / "2" / "dates.jsonl")
    summary = _jsonl(tmp_path / "2" / "summary.jsonl")
    _run(_step3(tmp_path / "3", merged, dates), monkeypatch, tmp_path / "3")
    admission = _jsonl(tmp_path / "3" / "admission.jsonl")[0]
    return panel, dates, summary, admission, merged, S


def test_step1b_attaches_the_new_columns_by_key_with_the_as_of_rule(tmp_path, monkeypatch, store):
    panel = _chain(tmp_path, monkeypatch, store)[0]
    by_day = {r["quote_date"]: r for r in panel}
    assert set(by_day) == {_day(i) for i in range(N_DAYS)}
    first = by_day[_day(0)]
    assert first["zz_a"] is None and first["zz_a_missing"] == 1 and first["zz_a_age_days"] is None
    day4 = by_day[_day(4)]   # stored rows: 0, 1, 3, 4, ...; strictly before day 4 is day 3, one day old
    assert (day4["zz_a"], day4["zz_b"], day4["zz_a_age_days"], day4["zz_a_missing"]) == (3.0, 103.0, 1, 0)
    for i in range(N_DAYS):
        assert (by_day[_day(i)]["zz_a_missing"] == 0) is _fresh(i), i
    assert panel[0][f"fe_{FAMILY}_status"] and f"fe_{FAMILY}_reasons" in panel[0]
    assert all(r["zz_a"] != -1.0 for r in panel)   # the other entity's row never leaks in


def test_the_family_shows_in_availability_admission_and_the_summary(tmp_path, monkeypatch, store):
    _, dates, summary, admission, merged, S = _chain(tmp_path, monkeypatch, store)
    flags = {r[S["quote_date"]]: r[f"available_{FAMILY}"] for r in dates}
    assert flags == {_day(i): "yes" if _fresh(i) else "no" for i in range(N_DAYS)}
    assert {r["family"] for r in summary} == {"base", FAMILY}
    dev = range(22)   # the holdout and the purged tail leave 22 dev dates (see test_study_templates)
    assert admission[f"rate_{FAMILY}"] == pytest.approx(sum(_fresh(i) for i in dev) / len(dev))
    assert admission[f"required_{FAMILY}"] == 1 and FAMILY in admission["admitted"]


def test_without_the_declaration_the_family_is_in_none_of_the_outputs(tmp_path, monkeypatch):
    panel, dates, summary, admission, merged, _ = _chain(tmp_path, monkeypatch, None)
    assert FAMILY not in json.dumps([panel, dates, summary, admission, merged["flags"]])
    assert not [k for r in panel for k in r if k.startswith("zz_")]
    assert admission["admitted"] == ["base"]


# -- step 4: the candidate pool and the feature order, from the real manifest -------------------


def _flow(tmp_path, root):
    overlay = {"work_dir": str(tmp_path / "work")}
    if root:
        overlay["families"] = {"keyed": {FAMILY: _zz(root)}}
    return wf.Workflow(copy.deepcopy(MANIFEST), str(CONFIGS), overlay, "QQQ")


def _selection(tmp_path, root):
    flow = _flow(tmp_path, root)
    assert flow.check() is None or not flow.check()
    io, pool = MANIFEST["args"]["study_io"], MANIFEST["args"]["selection"]["pool"]
    record = {io["admit"]["flag"].format(family=n): io["admit"]["value"] for n in [*pool, FAMILY]}
    path = Path(flow.path("step3", "admission"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n")
    values = flow.values("step4", strict=False, only={"selection", "features"})
    return values["selection"], values["features"], io


def test_step4_offers_the_family_as_a_candidate_whose_indices_include_its_columns(tmp_path, store):
    selection, features, io = _selection(tmp_path, store)
    name = io["naming"]["added"].format(family=FAMILY)
    assert name in selection["candidates"]
    indices = selection["candidates"][name]["params"]["feature_indices"]
    assert {features.index(f) for f in ("zz_a", "zz_b")} <= set(indices)
    assert features[:len(_SHIPPED_QQQ)] == _SHIPPED_QQQ
    assert selection["max_candidates"] == len(MANIFEST["args"]["selection"]["pool"]) + 2


def test_step4_without_the_declaration_has_neither(tmp_path):
    selection, features, io = _selection(tmp_path, None)
    assert io["naming"]["added"].format(family=FAMILY) not in selection["candidates"]
    assert features == _SHIPPED_QQQ


# -- the manifest stays consistent --------------------------------------------------------------


def test_the_manifest_keyed_key_equals_the_panel_schema_and_ships_no_family():
    families, schema = MANIFEST["args"]["families"], MANIFEST["args"]["panel_schema"]
    assert families["keyed_key"] == [schema["symbol"], schema["quote_date"]]
    assert families["keyed"] == {}
    assert hooks.FamiliesSpec().apply({"spec": families})["keyed_tables"] == {}
