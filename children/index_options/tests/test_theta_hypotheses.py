"""The pre-registered H0-H7 documents (ADR-0265 Amendment 5): pinned to their generator, sealed, wired end to end.

No real data is read and no hypothesis is evaluated on real chains here. The documents run on the synthetic world
of ``theta_synthetic`` (Black-76 chains with planted crushes), whose readers are swapped for static rows served
through each reader's own declared columns and bounds.
"""

import copy
import json
import re
from pathlib import Path

import pytest

import theta_synthetic as synth
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_document
from dskit.pipeline.planner import plan

from index_options import theta_hypotheses as th

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
GENERATED = th.files()
DOCS = sorted(name for name in GENERATED if name.startswith("run-theta-h"))
DEV_DOCS = [n for n in DOCS if not n.endswith("-validation.json")]
VAL_DOCS = [n for n in DOCS if n.endswith("-validation.json")]
DATE = re.compile(r"\b(20\d\d)-\d\d-\d\d\b")


def _doc(name):
    return json.loads((CONFIGS / name).read_text())


# -- the committed files ARE the generator's output ----------------------------------------------------------

@pytest.mark.parametrize("name", sorted(GENERATED))
def test_every_committed_file_is_the_generators_output(name):
    assert (CONFIGS / name).read_text() == th.dump(GENERATED[name])


def test_the_index_panel_document_is_the_stock_panel_document_on_the_index_tier_without_stock_gates():
    index, stock = _doc("run-theta-chain-panel-index.json"), _doc("run-theta-chain-panel.json")
    assert index["pipeline"]["chain"]["params"]["symbols"] == ["SPX"]
    assert "options-index" in index["pipeline"]["chain"]["params"]["directories"][0]
    params = index["pipeline"]["panel"]["params"]
    assert not {"first_quote_file", "census_file", "census_columns", "gate_field"} & set(params)
    assert index["pipeline"]["panel"]["params"]["features"] == stock["pipeline"]["panel"]["params"]["features"]
    assert params["path"] != stock["pipeline"]["panel"]["params"]["path"]


# -- shape: plan, node params, sealing, read-only -----------------------------------------------------------------

def _classes(name):
    planned = plan(load_document(str(CONFIGS / name)))
    return {key: item.cls for key, item in planned.resolved.items()}


@pytest.mark.parametrize("name", DOCS + ["run-theta-chain-panel-index.json"])
def test_the_document_plans_and_every_node_validates_its_params(name):
    classes = _classes(name)
    nodes = _doc(name)["pipeline"]
    for key, cls in classes.items():
        problems = cls.validate_params(nodes[key].get("params", {}))
        assert problems == [], (name, key, problems)


@pytest.mark.parametrize("name", DOCS)
def test_no_date_on_or_after_2026_is_read_and_every_bound_is_the_periods(name):
    text = re.sub(r'"retrieved": "[^"]*"', "", json.dumps(_doc(name)["pipeline"]))    # a retrieval date is provenance, not data read
    sealed = {m.group(0) for m in DATE.finditer(text) if m.group(1) >= "2026"}
    assert sealed <= {"2026-01-01"}                                   # only the sealing date itself
    validation = name.endswith("-validation.json")
    bounds = set(re.findall(r'"end_before": "(\d{4}-\d\d-\d\d)"', text))
    assert bounds == {"2026-01-01" if validation else "2021-01-01"} or bounds == set()
    starts = set(re.findall(r'"start": "(\d{4}-\d\d-\d\d)"', text))
    assert starts <= ({"2021-01-04", "2020-09-01"} if validation else {"2016-01-04"})
    for node in _doc(name)["pipeline"].values():
        if node["uses"].endswith(("ParquetStreamRows", "ParquetChainRows")):
            assert "end_before" in json.dumps(node["params"])


@pytest.mark.parametrize("name", DOCS)
def test_the_document_only_writes_under_its_own_run_root_and_never_under_the_vendor_stores(name):
    doc = _doc(name)
    root = doc["outputs"]["run_root"]
    assert root.startswith(th.RUN_BASE) and not root.startswith("/home/russell/data/thetadata")
    for node in doc["pipeline"].values():
        if node["uses"] == "records-write":
            assert node["params"]["path"].startswith(root + "/")
            assert node["params"]["overwrite"] is True
    assert ("-validation" in root) == name.endswith("-validation.json")


@pytest.mark.parametrize("name", DOCS)
def test_the_notes_carry_the_adr_rule_verbatim_and_the_run_command(name):
    h = "H" + name.split("-h")[1][0]
    notes = _doc(name)["notes"]
    assert th.ADR_RULE[h] in notes and th.COMMON_RULES in notes
    assert f"python -m dskit.pipeline run configs/{name} --asof 2025-12-31" in notes
    assert ("RUN ONCE" in notes) == name.endswith("-validation.json")


def test_the_adr_text_in_the_notes_is_the_decision_logs():
    log = (Path(__file__).resolve().parents[3] / "docs" / "architecture" / "decision-log.md").read_text()
    for h, rule in th.ADR_RULE.items():
        assert rule in log, h


# -- the overlays ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("h", sorted(th.HYPOTHESES))
def test_the_validation_overlay_and_document_differ_from_development_only_in_dates(h):
    args = _doc(th.args_file(h))
    assert (args["start"], args["end_before"]) == ("2021-01-04", "2026-01-01")
    dev, val = _doc(th.doc_file(h, th.DEV)), _doc(th.doc_file(h, th.VAL))

    def scrub(text):
        text = re.sub(r"\b20\d\d-\d\d-\d\d\b", "D", text)
        return text.replace("-validation", "").replace("development", "P").replace("validation", "P")
    a, b = scrub(json.dumps(dev["pipeline"])), scrub(json.dumps(val["pipeline"]))
    if h == "H2":                       # the labelled branch exists only where labels do (development years)
        assert "events_labelled" in a and "events_labelled" not in b
    else:
        assert a == b
    assert th.build(h, th.apply_args(args), th.load_inputs()) == val


def test_an_overlay_past_the_seal_or_with_halves_outside_the_period_is_refused():
    args = th.args_for("H0")
    with pytest.raises(ValueError, match="sealed"):
        th.apply_args({**args, "end_before": "2026-01-02"})
    with pytest.raises(ValueError, match="halves_at"):
        th.apply_args({**args, "halves_at": "2027-01-01"})


def test_unfrozen_inputs_stay_placeholders_so_an_early_run_refuses():
    inputs = th.load_inputs()
    assert inputs["panel"]["snapshot"] == "0" * 64 and inputs["panel_index"]["snapshot"] == "0" * 64
    assert inputs["universe"] == ["__UNIVERSE_NOT_FROZEN__"]
    assert inputs["labels"]["sha256"] == _doc("run-theta-chain-events.json")["pipeline"]["labels"]["params"]["sha256"]
    assert inputs["renames"]["renames_sha256"] == _doc("run-theta-chain-panel.json")["pipeline"]["chain"]["params"]["renames_sha256"]


def test_the_frozen_event_rules_are_the_events_documents():
    rules = _doc("run-theta-chain-events.json")["pipeline"]["events"]["params"]
    params = _doc("run-theta-h2-earnings.json")["pipeline"]["events"]["params"]
    from dskit.pipeline.libs.chain_events import RULE_NAMES
    assert {k: params[k] for k in RULE_NAMES} == {k: rules[k] for k in RULE_NAMES}


def test_set_pin_and_freeze_universe_write_the_inputs_and_regenerate_to_a_consistent_set(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "CONFIGS", tmp_path)
    (tmp_path / th.INPUTS_FILE).write_text((CONFIGS / th.INPUTS_FILE).read_text())
    (tmp_path / th.EVENTS_FILE).write_text((CONFIGS / th.EVENTS_FILE).read_text())
    (tmp_path / th.PANEL_DOC).write_text((CONFIGS / th.PANEL_DOC).read_text())
    assert th.main(["set-pin", "panel", "ab" * 32]) == 0
    assert th.main(["set-pin", "panel", "nope"]) == 1
    monkeypatch.setattr(th, "store_pin", lambda spec: "cd" * 32)
    assert th.main(["set-pin", "panel_index"]) == 0 and th.load_inputs()["panel_index"]["snapshot"] == "cd" * 32
    assert th.main(["set-pin", "bars", "ab" * 32]) == 1
    census = tmp_path / "census.jsonl"
    census.write_text("\n".join(json.dumps(r) for r in [
        {"root": "AAA", "first_bid_date": "2016-01-04", "liquidity_tercile": 2, "median_quoted_development": 9.0},
        {"root": "BBB", "first_bid_date": None},
        {"root": "CCC", "first_bid_date": "2017-02-01", "liquidity_tercile": 1},
        {"root": "DDD", "first_bid_date": "2022-03-01", "liquidity_tercile": 0}]) + "\n")
    assert th.main(["freeze-universe", str(census)]) == 0
    frozen = json.loads((tmp_path / "theta-universe.json").read_text())
    assert sorted(frozen["roots"]) == ["AAA", "CCC", "DDD"] and frozen["roots"]["AAA"]["liquidity_tercile"] == 2
    inputs = th.load_inputs()
    assert inputs["universe"] == ["AAA", "CCC", "DDD"] and inputs["panel"]["snapshot"] == "ab" * 32
    assert inputs["universe_first_bid"] == {"AAA": "2016-01-04", "CCC": "2017-02-01", "DDD": "2022-03-01"}
    assert th.main(["write"]) == 0 and th.main(["check"]) == 0
    doc = json.loads((tmp_path / "run-theta-h3-straddle-sorts.json").read_text())
    assert doc["pipeline"]["chain"]["params"]["symbols"] == ["AAA", "CCC"]          # DDD is first quoted after the development window
    val = json.loads((tmp_path / "run-theta-h3-straddle-sorts-validation.json").read_text())
    assert val["pipeline"]["chain"]["params"]["symbols"] == ["AAA", "CCC", "DDD"]
    assert doc["pipeline"]["panel"]["params"]["sources"][0]["snapshot"] == "ab" * 32
    (tmp_path / "run-theta-h3-straddle-sorts.json").write_text("{}")
    assert th.main(["check"]) == 1


# -- end to end on the synthetic world ----------------------------------------------------------------------------------------

def _swap(obj, tmp, sized=True):
    """The document with its readers replaced by static rows and its outputs in ``tmp``."""
    obj = copy.deepcopy(obj)
    obj["outputs"]["run_root"] = str(tmp / "runs")
    for key, node in obj["pipeline"].items():
        params = node.get("params", {})
        if node["uses"] == th.PARQUET_STREAM:
            sources = params["sources"]
            dataset = {"theta-chain-panel": "panel", "theta-chain-panel-index": "index_panel", "stock-daily-bars": "bars",
                       "benchmark-daily-bars": "bench"}[sources[0]["source"]]
            declared = {"kind": "stream", "sources": [
                {"columns": s["columns"], "read_bounds": s["read_bounds"], "iso_fields": s.get("iso_fields", []), "where": s.get("where", []),
                 "dataset_part": n}
                for n, s in enumerate(sources)]}
            node["uses"], node["params"] = "theta_synthetic:StaticRecords", {"dataset": dataset, "declared": declared}
        elif node["uses"] == th.CHAIN_ROWS:
            dataset = "stock_chain" if "options-tested" in params["directories"][0] else "index_chain"
            declared = {"kind": "chain", "symbols": params["symbols"], "fields": params["fields"],
                        "start": params["start"], "end_before": params["end_before"]}
            node["uses"], node["params"] = "theta_synthetic:StaticRecords", {"dataset": dataset, "declared": declared}
        elif node["uses"] == "records-write":
            params["path"] = str(tmp / Path(params["path"]).name)
        elif node["uses"].endswith("QuantileSortBook"):
            if sized:
                params.update(min_names=6, quantiles=min(params["quantiles"], 3))
            if "bootstrap" in params:
                params["bootstrap"] = {**params["bootstrap"], "block_days": 21, "replicates": 40}
        elif node["uses"] == "filter" and sized:
            for clause in params["where"]:
                if clause == {"field": "n", "op": ">=", "value": 50}:
                    clause["value"] = 10                              # the basket floor, sized to the synthetic 16 names
        elif node["uses"].endswith("CrossSectionIC"):
            if sized:
                params.update(usable_names=5, min_names=5)
        elif node["uses"].endswith("StampSeriesStats"):
            if "bootstrap" in params:
                params["bootstrap"] = {**params["bootstrap"], "block_days": 28, "replicates": 40}
    return obj


@pytest.fixture(scope="module", autouse=True)
def _worlds():
    synth.WORLD.clear()
    yield


def _run(h, period, year, tmp_path, monkeypatch):
    world = synth.build(year)
    synth.WORLD.update(data=world["data"], symbols=world["symbols"])
    inputs = th.load_inputs()
    inputs["universe"] = synth.SYMBOLS
    inputs["index_symbols"] = synth.INDEX
    doc = _swap(th.build(h, period, inputs), tmp_path)
    monkeypatch.chdir(CONFIGS.parent)
    result = run_document(PipelineDocument.from_obj(doc), asof="2025-12-31", journal=False)
    assert result.state == "ran", result.error
    return result


def _summary(result, node):
    return result.outputs[node]["summary"]


@pytest.mark.parametrize("h", sorted(th.HYPOTHESES))
def test_the_development_document_runs_end_to_end_on_the_synthetic_world(h, tmp_path, monkeypatch):
    result = _run(h, th.DEV, 2018, tmp_path, monkeypatch)
    check = CHECKS[h]
    check(result)
    assert list(tmp_path.glob("*.jsonl")), "the audit rows were written under the run's own directory"


def _g(result, node, group="all"):
    return _summary(result, node)["groups"][group]


def _check_h0(r):
    ic = _summary(r, "ic")["measures"]
    assert set(ic) == {"har_beyond_market", "rv22_beyond_market", "har_alone", "implied_alone"}
    assert ic["har_beyond_market"]["n_stamps"] > 30 and ic["har_beyond_market"]["rows_scored"] > 500
    book = _summary(r, "book")["signals"]
    assert book["har"]["n_stamps"] > 10 and book["har"]["net"]["mean"] is not None
    assert book["har"]["dropped"] == {} or sum(book["har"]["dropped"].values()) < book["har"]["rows_scored"]


def _check_h1(r):
    for tag in ("5", "21"):
        ic, fam = _summary(r, f"ic{tag}"), _summary(r, f"ic{tag}")["family"]
        assert set(ic["measures"]) == set(fam["p"]) and len(fam["p"]) == 6
        assert all("signed" in m for m in ic["measures"].values())
        assert _summary(r, f"decile{tag}")["family"]["decisions"]["bh"].keys() == fam["p"].keys()
        assert any(m["rows_scored"] > 300 for m in ic["measures"].values())


def _check_h2(r):
    events = _summary(r, "events")
    assert events["events"] > 3 and _summary(r, "events_labelled")["labels_total"] > 0
    ledger = _summary(r, "ledger")
    assert ledger["orders_opened"] > 3 and ledger["closed"].get("exit", 0) > 3
    pnl = _summary(r, "pnl")
    assert {"event_source=detected", "event_source=labelled"} <= set(pnl["groups"]) and pnl["contrast"]["paired"] is False
    assert _g(r, "gap_primary", "event_source=detected")["n_stamps"] > 2
    source = _summary(r, "gap_source")["groups"]
    assert source["realized_source=bar"]["n_rows"] > 3 and sum(g["n_rows"] for k, g in source.items() if k != "all") == source["all"]["n_rows"]


def _check_h3(r):
    book = _summary(r, "book")
    assert set(book["signals"]) == {"rv_iv_gap", "term_slope", "skew", "idio_vol"}
    assert set(book["family"]["decisions"]) == {"bh", "bonferroni"}
    assert _summary(r, "ledger")["closed"].get("expiry", 0) > 20
    assert all(s["n_stamps"] >= 1 for s in book["signals"].values())


def _check_h4(r):
    assert _summary(r, "ledger")["closed"].get("expiry", 0) > 20
    timing = _summary(r, "short_level")
    assert timing["contrast"]["paired"] is True and timing["contrast"]["n_stamps"] >= 2
    assert _g(r, "vrp_level", "liquidity_tercile=0")["n_stamps"] >= 3 or _g(r, "vrp_level")["n_stamps"] >= 3


def _check_h5(r):
    orders = _summary(r, "orders")
    assert set(orders["orders"]) == {"put_write", "strangle_1dte", "condor_1dte", "put_spread_16_5", "tail_hedge"}
    assert _summary(r, "ledger")["closed"].get("expiry", 0) > 50
    groups = _summary(r, "sharpe")["groups"]
    assert any(name.startswith("structure=put_write|symbol=SPX") for name in groups)
    assert all("hurdle" in g for name, g in groups.items() if g["n_stamps"] >= 2 and name != "all")


def _check_h6(r):
    series = _summary(r, "premium_series")
    assert series["groups"]["all"]["n_stamps"] > 20
    assert {"half=first", "half=second"} <= set(series["groups"])
    assert -1.5 < series["groups"]["all"]["mean"] < 1.5


def _check_h7(r):
    pin = _summary(r, "pin")
    assert pin["groups"]["all"]["n_rows"] > 5
    assert _summary(r, "orders")["orders"]["pin"] > 5
    assert _summary(r, "spread_series")["groups"]["all"]["n_stamps"] > 3


CHECKS = {"H0": _check_h0, "H1": _check_h1, "H2": _check_h2, "H3": _check_h3, "H4": _check_h4, "H5": _check_h5,
          "H6": _check_h6, "H7": _check_h7}


@pytest.mark.parametrize("h", ["H1", "H2", "H5"])
def test_the_validation_document_runs_on_a_world_inside_the_validation_years(h, tmp_path, monkeypatch):
    result = _run(h, th.apply_args(th.args_for(h)), 2022, tmp_path, monkeypatch)
    if h == "H1":
        _check_h1(result)
    elif h == "H2":
        assert "events_labelled" not in result.outputs and _summary(result, "events")["events"] > 3
        assert set(_summary(result, "pnl")["groups"]) >= {"all", "event_source=detected"}
        assert _summary(result, "pnl")["contrast"] is None
    else:
        _check_h5(result)


def test_a_validation_document_run_on_development_years_reads_no_row():
    """The validation bounds exclude the development years: a world of 2018 yields no panel row to the validation document."""
    world = synth.build(2018)
    synth.WORLD.update(data=world["data"], symbols=world["symbols"])
    obj = _swap(th.build("H1", th.apply_args(th.args_for("H1")), {**th.load_inputs(), "universe": synth.SYMBOLS}), Path("/tmp"))
    panel = synth.StaticRecords("panel", obj["pipeline"]["panel"]["params"])
    assert panel.run(None, {})["records"] == []


# -- VolRegimeSignals parity ------------------------------------------------------------------------------------------------------

def test_h4_regime_readings_are_volregimesignals_own_on_a_single_name():
    """The per-name vrp and term ratio of the H4 document equal the node's, so the restatement cannot drift."""
    from dskit.pipeline.kinds_compute import ComputeFields
    from dskit.pipeline.node import NodeContext
    from index_options.nodes import VolRegimeSignals
    nodes = _doc("run-theta-h4-vrp.json")["pipeline"]
    fields = nodes["signals"]["params"]["fields"]
    # one name, per-session rv and a 30-day and a 90-day implied vol (decimals), a prior row to read
    rows = [{"date": f"2020-01-{d:02d}", "atm_iv": 0.30 + 0.01 * d, "term_slope": 0.02, "rv_22": 0.015 + 0.0005 * d} for d in range(1, 6)]
    ctx = NodeContext(name="t", asof="2025-12-31", run_dir="/x")
    out = ComputeFields("signals", {"fields": fields}).run(ctx, {"records": rows})["records"]
    node = VolRegimeSignals("v", {"implied_field": "iv_pts", "realized_field": "rv_22", "min_history": 1, "periods_per_year": 252})
    feed = [{"date": r["date"], "asof_ms": n, "instrument": "N", "iv_pts": 100 * r["atm_iv"], "rv_22": r["rv_22"]} for n, r in enumerate(rows)]
    term = [{"date": r["date"], "close": 100 * (r["atm_iv"] + r["term_slope"])} for r in rows]
    read = node.run(ctx, {"rows": feed, "term": term})["rows"]
    for mine, theirs in zip(out[:-1], read[1:]):                    # the node reads the PREVIOUS row; ours reads the row itself
        assert theirs["vrp"] == pytest.approx(mine["vrp"])
        assert theirs["vix_term_ratio"] == pytest.approx(mine["term_ratio"])


def test_every_document_the_run_book_names_exists_and_every_document_is_in_the_run_book():
    log = (Path(__file__).resolve().parents[3] / "docs" / "architecture" / "decision-log.md").read_text()
    book = log[log.index("**Amendment 5 (2026-10-13"):log.index("## ADR-0266")]
    named = set(re.findall(r"configs/([\w.-]+\.json)", book))
    assert named - {"theta-universe.json"} <= {p.name for p in CONFIGS.iterdir()}
    for name in DEV_DOCS:
        assert name in book or name.removeprefix("run-").removesuffix(".json") in book, name
    assert "run-theta-chain-census-parquet.json" in named and "run-theta-chain-panel.json" in named
    for verb in ("freeze-universe", "set-pin panel", "write", "check"):
        assert verb in book


# -- planted effect and null on the cross-sectional IC path, with the PRE-REGISTERED parameters ---------------------------------------
#
# The 16-name synthetic world above forces the size knobs down (QuantileSortBook min_names/quantiles, CrossSectionIC floors, the basket
# floor n >= 50) and so cannot say whether the real thresholds detect anything. These worlds have 80 to 120 names, so the documents run
# with every statistic, sign, threshold, window and floor as committed. The swap changes ONLY: the Parquet readers (static rows through the
# same declared columns and bounds), the output directory, the audit-row paths and the bootstrap's replicates and block length (speed).

import random as _random
from datetime import date as _date, timedelta as _timedelta



def _sessions(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += _timedelta(days=1)
    return out


def _walk(rng, n, vol_path):
    level, closes = 100.0, []
    for i in range(n):
        level *= 2.718281828 ** (vol_path(i) * rng.gauss(0, 1))
        closes.append(level)
    return closes


def _bar_rows(symbol, days, closes):
    return [{"symbol": symbol, "date": d.isoformat(), "open": c, "high": c, "low": c, "close": c, "adjusted_close": c, "volume": 1}
            for d, c in zip(days, closes)]


def _install(panel, bars, bench):
    synth.WORLD.clear()
    synth.WORLD.update(data={"panel": panel, "bars": bars, "bench": bench}, symbols={})


def _run_registered(h, tmp_path, monkeypatch):
    doc = th.build(h, th.DEV, {**th.load_inputs(), "universe": []})
    swapped = _swap(doc, tmp_path, sized=False)
    changed = []
    for key, node in swapped["pipeline"].items():
        original = doc["pipeline"][key]
        if node["uses"] != original["uses"]:
            assert original["uses"] == th.PARQUET_STREAM and node["uses"] == "theta_synthetic:StaticRecords", key
            changed.append((key, "reader"))
        elif node.get("params") != original.get("params"):
            before, after = copy.deepcopy(original["params"]), copy.deepcopy(node["params"])
            for side in (before, after):
                side.pop("path", None)
                if "bootstrap" in side:
                    side["bootstrap"] = {k: v for k, v in side["bootstrap"].items() if k not in ("replicates", "block_days")}
            assert before == after, (key, "changes more than the audit path or the bootstrap size")
            changed.append((key, "path/bootstrap"))
    assert {k for k, _ in changed} and all(w in ("reader", "path/bootstrap") for _, w in changed)
    monkeypatch.chdir(CONFIGS.parent)
    result = run_document(PipelineDocument.from_obj(swapped), asof="2025-12-31", journal=False)
    assert result.state == "ran", result.error
    return result


def _h1_world(effect, seed, names=120, n=300):
    rng = _random.Random(seed)
    days = _sessions(_date(2018, 2, 1), n + 60)
    panel, bars, feats = [], [], ["put_call_iv_spread", "risk_reversal", "term_slope", "oi_put_call_ratio", "oi_put_call_ratio_change_5d"]
    for i in range(names):
        sym = f"N{i:03d}"
        z = {f: [rng.gauss(0, 1) for _ in days] for f in feats}
        rets = []
        for t in range(len(days)):
            drift = -0.004 * z["put_call_iv_spread"][t - 1] if effect and t else 0.0          # the planted effect: next-session drift
            rets.append(drift + 0.015 * rng.gauss(0, 1))
        closes, level = [], 100.0
        for r in rets:
            level *= 2.718281828 ** r
            closes.append(level)
        bars += _bar_rows(sym, days, closes)
        for t, d in enumerate(days[:n]):
            row = {"symbol": sym, "date": d, "target_dte": 30, "status": "ok", "after_first_quote": True,
                   "volume_call": 10 + rng.random() * 90, "volume_put": 10 + rng.random() * 90, "oi_call": 100 + rng.random() * 900,
                   "oi_put": 100 + rng.random() * 900}
            row.update({f: z[f][t] for f in feats})
            panel.append(row)
    return panel, bars


def _h1_flags(result):
    out = {}
    for tag in ("5", "21"):
        fam = result.outputs[f"ic{tag}"]["summary"]["family"]
        book = result.outputs[f"decile{tag}"]["summary"]["family"]
        out[tag] = (fam["decisions"]["bh"], book["decisions"]["bh"], result.outputs[f"ic{tag}"]["summary"]["measures"])
    return out


def test_h1_with_the_preregistered_parameters_detects_a_planted_effect_and_finds_nothing_in_pure_noise(tmp_path, monkeypatch):
    panel, bars = _h1_world(True, seed=11)
    _install(panel, bars, [])
    planted = _h1_flags(_run_registered("H1", tmp_path / "a", monkeypatch))
    bh_ic, bh_book, measures = planted["5"]
    assert bh_ic["put_call_iv_spread"] is True and bh_book["put_call_iv_spread"] is True
    assert measures["put_call_iv_spread"]["signed"]["ic"] > 0.05 and measures["put_call_iv_spread"]["signed"]["ic_t"] >= 2.5
    assert not any(v for k, v in bh_ic.items() if k != "put_call_iv_spread")          # the noise features stay unrejected
    panel, bars = _h1_world(False, seed=11)
    _install(panel, bars, [])
    null = _h1_flags(_run_registered("H1", tmp_path / "b", monkeypatch))
    for tag in ("5", "21"):
        assert not any(null[tag][0].values()) and not any(null[tag][1].values()), tag
    assert all(m["signed"]["ic_t"] is None or m["signed"]["ic_t"] < 2.5 for m in null["5"][2].values())     # stated tolerance: no t at the 2.5 bar


def _h0_world(effect, seed, names=80, n=330):
    rng = _random.Random(seed)
    days = _sessions(_date(2018, 1, 2), n + 70 + 45)
    panel, bars = [], []
    spy = _walk(rng, len(days), lambda i: 0.01)
    for i in range(names):
        sym = f"N{i:03d}"
        h, vols, closes, level = rng.gauss(0, 0.3) if effect else 0.0, [], [], 100.0
        for t in range(len(days)):
            h = 0.98 * h + (0.08 * rng.gauss(0, 1) if effect else 0.0) if t else h                 # a persistent log-vol state, or a constant vol
            vols.append(0.015 * 2.718281828 ** h)
            level *= 2.718281828 ** (vols[-1] * rng.gauss(0, 1))
            closes.append(level)
        bars += _bar_rows(sym, days, closes)
        for t in range(70, 70 + n):
            implied = 0.8 * vols[t] * 21 ** 0.5 * 2.718281828 ** rng.gauss(0, 0.35) if effect else 0.8 * 0.015 * 21 ** 0.5 * 2.718281828 ** rng.gauss(0, 0.35)
            panel.append({"symbol": sym, "date": days[t], "target_dte": 30, "status": "ok", "after_first_quote": True,
                          "straddle_ratio": implied, "half_spread_over_premium": 0.01})
    return panel, bars, _bar_rows("SPY", days, spy)


def test_h0_with_the_preregistered_parameters_detects_planted_vol_persistence_and_finds_nothing_when_vol_is_constant(tmp_path, monkeypatch):
    panel, bars, bench = _h0_world(True, seed=5)
    _install(panel, bars, bench)
    planted = _run_registered("H0", tmp_path / "a", monkeypatch).outputs["ic"]["summary"]["measures"]["har_beyond_market"]
    assert planted["ic"] > 0.02 and planted["ic_t"] >= 2.5 and planted["usable"] is True
    panel, bars, bench = _h0_world(False, seed=5)
    _install(panel, bars, bench)
    null = _run_registered("H0", tmp_path / "b", monkeypatch).outputs["ic"]["summary"]["measures"]["har_beyond_market"]
    assert null["usable"] is True and null["ic_t"] < 2.5            # stated tolerance: the one-sided pass bar of the ADR


def test_the_documents_the_planted_tests_run_are_the_real_ones_and_still_validate():
    for h in ("H0", "H1"):
        name = th.doc_file(h, th.DEV)
        assert _doc(name) == th.build(h, th.DEV, th.load_inputs())
        assert _classes(name)
