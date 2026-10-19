"""No-fit contracts for the ADR-0270 strike-screen panel-source document.

``configs/strike-screen-panel-source.json`` (the earlier arms study document, repurposed) exists only to
materialize the exact-expiry panel through ``python -m index_options.cdf_study CONFIG --stage panel``. It must
still equal ``configs/strike-screen-protocol.json`` wherever the owners can express the protocol, because the
fit documents read the columns it builds. Nothing here opens a store, reads market data or fits: the real
``CDFHyperparameterStudy`` is constructed on the document (its relative fold-table path resolved against the
configs directory, so the tests do not depend on the working directory), and the feature list is derived from
the owners' own code on a toy frame. The last block pins the named gaps, so an owner change that closes one
fails here.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
DOCUMENT, PROTOCOL = "strike-screen-panel-source.json", "strike-screen-protocol.json"
FOLDS = "strike-screen-folds-screen.jsonl"
PILOT = Path("/home/russell/data/index_options/theta-strike-screen-20261018")
ZERO, ISO = "0" * 64, re.compile(r"\d{4}-\d{2}-\d{2}")
CLASS = {"torch": "dskit.pipeline.libs.predictive_cdf:TorchCDF"}
MLP = "dskit.pipeline.libs.torch_ts:ArchSequenceEncoder"
HF = "dskit.pipeline.libs.transformers:HFSequenceEncoder"
#: The proxy defaults of the market-implied research record, item 6 (cited by the document's data.notes).
RESEARCH = {"proxy_probabilities": [0.01, 0.025, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70,
                                    0.75, 0.80, 0.85, 0.90, 0.95, 0.975, 0.99],
            "min_wing_nodes": 5, "max_inner_gap": 0.05, "max_outer_gap": 0.10}
RECORD = ("docs/research/distribution-modeling/2026-10-18-market-implied-reference-and-baselines.md")


def _json(name):
    return json.loads((CONFIGS / name).read_text())


def _resolved(document):
    """Return a deep copy whose relative fold-table path is resolved against the configs directory.

    The document keeps ``configs/<name>`` (it is run from the child root); the owners open that path relative
    to the working directory, so every construction below goes through this copy.
    """
    document = copy.deepcopy(document)
    spec = document["study"]["fold_table"]
    spec["path"] = str(CONFIGS / Path(spec["path"]).name)
    return document


@pytest.fixture(scope="module")
def doc():
    return _json(DOCUMENT)


@pytest.fixture(scope="module")
def proto():
    return _json(PROTOCOL)


@pytest.fixture()
def fresh(doc):
    return copy.deepcopy(doc)


def _strings(value, skip_notes=True):
    """Yield every string of a JSON value, the values under ``notes`` excluded."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            if not (skip_notes and key == "notes"):
                yield from _strings(inner, skip_notes)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner, skip_notes)


def _losses(text):
    """Parse the protocol's ``"crps 1 + tail_crps 1"`` into TorchCDF loss terms."""
    return [{"kind": kind, "weight": float(weight)}
            for kind, weight in (part.split() for part in text.split(" + "))]


def _parts(candidate):
    arm, encoder, loss = candidate.split("__")
    return arm, encoder, loss


# -- the owner builds it --------------------------------------------------------------------


def test_the_real_owner_constructs_the_document_without_a_read(doc):
    from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy
    study = CDFHyperparameterStudy(_resolved(doc))
    assert study.development == [1] and study.later == [2] and study.cutoff == "2020-01-02"
    assert study.inventory.digest and set(study.partitions) == {"development", "later"}


def test_the_panel_owners_accept_their_blocks(doc):
    from index_options.cdf_study import CondorCDFDiagnostic, RawChainFeatureBuilder, panel_class
    data, holdout = doc["data"], doc["study"]["fold_table"]["holdout_start"]
    assert panel_class(data).__name__ == "ExactExpiryCDFPanel"
    panel_class(data)(data, holdout_start=holdout)
    CondorCDFDiagnostic(**doc["diagnostic"])
    RawChainFeatureBuilder(**data["raw_chain"])


def test_the_document_is_only_a_panel_source(doc):
    """The panel stage refuses a context column, and the notes say the document never searches or fits."""
    assert "decision_regions" not in doc["data"]
    notes = doc["notes"]
    assert "--stage panel" in notes and "ONLY" in notes and "never runs search" in notes
    assert "PARQUET PATH AND SHA256" in notes and "panel_sha256" in notes
    assert "run-strike-screen-arms" not in json.dumps(doc)


def test_the_cached_frame_lands_where_the_fit_documents_read_it(doc):
    """The panel stage writes <experiment.output>/<PANEL_CACHE_DIR>/<ParquetFrameCache.FRAME> (owner constants)."""
    from dskit.pipeline.libs.parquet import ParquetFrameCache
    from index_options.cdf_study import PANEL_CACHE_DIR
    assert doc["experiment"]["output"] == str(PILOT / "panel")
    frame = Path(doc["experiment"]["output"]) / PANEL_CACHE_DIR / ParquetFrameCache.FRAME
    assert str(frame) == str(PILOT / "panel" / "panel-cache" / "frame.parquet")


@pytest.mark.parametrize("change", [
    lambda d: d["study"].update(tail_weight=1.0),                         # trains tail_crps under a decision context
    lambda d: d["experiment"].update(final_seeds=[11, 29, 47]),            # Cartesian-grammar key
    lambda d: d["experiment"].update(screen_seed=11),
    lambda d: d["experiment"].update(axes={}),
    lambda d: d["experiment"].update(candidate_labels={}),
    lambda d: d["experiment"]["candidates"]["price_only__mlp__L1"].update(calibrate=True),   # patience steers the band
    lambda d: d["study"]["fold_table"].update(sha256="1" * 64),             # not the pinned file
    lambda d: d["study"]["fold_table"].update(holdout_start="2023-06-01"),  # validation would reach the holdout
    lambda d: d["experiment"]["search_partitions"].update(pooled=["price_only__mlp__L1"]),
    lambda d: d["experiment"].update(max_candidates=7),
    lambda d: d["experiment"]["resolutions"].update(final_samples=401),
    lambda d: d["experiment"]["candidates"]["price_only__mlp__L1"]["params"].update(unknown_knob=1),
], ids=["tail_weight", "final_seeds", "screen_seed", "axes", "labels", "calibrate", "fold_sha", "holdout",
        "partition", "cap", "resolution", "unknown_param"])
def test_a_bad_document_refuses_at_construction(fresh, change):
    from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy
    change(fresh)
    with pytest.raises((ValueError, TypeError)):
        CDFHyperparameterStudy(_resolved(fresh))


# -- the protocol pins ----------------------------------------------------------------------


def test_formulation_and_panel_values_equal_the_protocol(doc, proto):
    f, data, study = proto["formulation"], doc["data"], doc["study"]
    context = [c.strip() for c in f["inputs"]["context"].split(",")]
    assert data["max_dte"] == f["max_dte"] == 60
    first, last = re.fullmatch(r"ret_lag_(\d+)\.\.(\d+)", f["inputs"]["sequence"]).groups()
    assert (first, data["lags"]) == ("0", int(last) + 1) and data["lags"] == 22
    assert data["windows"] == [int(c[3:]) for c in context if re.fullmatch(r"rv_\d+", c)]
    assert data["reference_floor"] == float(re.search(r"max\(rv_22, ([\d.]+)\)", f["reference_scale"]).group(1))
    assert doc["diagnostic"]["strikes_z"] == f["strike_grid_z"] and len(f["strike_grid_z"]) == 13
    assert doc["diagnostic"]["integration_points"] == 101
    assert (data["feature_gap_days"], data["spot_tolerance"], data["since"]) == (7, 0.02, "2016-01-04")
    assert (study["group"], study["date"], study["end"], study["horizon"], study["target"], study["reference"]) == (
        "symbol", "quote_date", "settlement_date", "actual_calendar_dte", "terminal_return", "reference_scale")
    assert study["identity"] == ["symbol", "quote_date", "expiry"] and study["series_identity"] == ["symbol", "expiry"]
    assert study["decision_context"] == "decision_region_context" and study["wing_metrics"] is True
    assert (study["samples"], study["tail_points"], study["calibration_knots"]) == (201, 101, 21)
    assert study["tail_intervals"] == [[-2.5, -0.5], [0.5, 2.5]]
    assert study["bootstrap"] == {"blocks": [30, 60], "replicates": 1000, "seed": 829}
    assert doc["experiment"]["resolutions"] == {
        "screen_samples": 201, "final_samples": 201, "tail_points": 101, "integration_points": 101,
        "audit_samples": [101, 201, 401], "audit_rows": 16}


def test_the_fold_table_is_the_protocols_screen_rows_and_pinned(doc, proto):
    spec, path = doc["study"]["fold_table"], CONFIGS / FOLDS
    assert spec["path"] == f"configs/{FOLDS}" and path.read_bytes() and hashlib.sha256(path.read_bytes()).hexdigest() == spec["sha256"]
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    screen = proto["fold_tables"]["screen"]
    assert rows == screen and len(rows) == 2 and [r["role"] for r in rows] == ["warmup", "scored"]
    assert spec["holdout_start"] == proto["fold_tables"]["holdout_start"]["screen"] == "2024-01-01"
    assert spec["cal_n"] == proto["fold_tables"]["cal_n"] == proto["windows"]["monitor_band"]["cal_n"] == 82
    assert spec["roles"] == ["warmup", "scored"] and set(spec) == {"path", "sha256", "holdout_start", "cal_n", "roles"}
    dev, val = proto["windows"]["development"]["origins"], proto["windows"]["validation"]["origins"]
    assert (rows[0]["train_start"], rows[1]["train_end"]) == tuple(dev)
    assert (rows[1]["val_start"], rows[1]["val_end"]) == tuple(val)


def test_the_candidates_are_the_declared_grid_and_the_protocol_training(doc, proto):
    t, a, candidates = proto["training"], proto["arms_and_candidates"], doc["experiment"]["candidates"]
    assert len(candidates) == doc["experiment"]["max_candidates"] == 8
    assert "2 arms x 2 encoders x 2 losses x 2 folds = 16 cells" in a["screen_fits"]
    names = {_parts(n) for n in candidates}
    assert names == {(arm, enc, loss) for arm in a["arms"] for enc in a["encoders"] for loss in a["losses"]}
    capacity = int(re.search(r"min\((\d+),", t["capacity"]).group(1))
    for name, spec in candidates.items():
        p, (_, _, loss) = spec["params"], _parts(name)
        assert spec["class"] == CLASS["torch"] and spec["pooled"] is True and spec["calibrate"] is False
        assert p["losses"] == _losses(a["losses"][loss])
        assert p["components"] == a["components"][0] == 3 and p["hidden"] == [8]
        assert p["dropout"] == t["regularization"]["dropout"] and p["weight_decay"] == t["regularization"]["weight_decay"]
        assert p["learning_rate"] == t["learning_rate"] and p["batch_size"] == t["batch_size"]
        assert p["epochs"] == t["early_stopping"]["epoch_ceiling"]["screen"] == 120
        assert p["patience"] == t["early_stopping"]["patience"]["screen"] == 10
        assert p["seeds"] == t["seeds"]["screen"] == [11]
        assert p["max_parameters"] == capacity == 100000
        assert p["training_telemetry"] is True and p["device"] == "cuda" and "head_features" not in p
        assert "one output head per ticker" in t["sharing"]


def test_the_arms_nest_and_the_chain_is_kept_out_of_price_only(doc, proto):
    features, candidates = doc["study"]["features"], doc["experiment"]["candidates"]
    chain, symbols = set(_chain_columns()), set(proto["cohort"]["symbols"])
    onehots = {f"is_{s}" for s in symbols}
    index = {}
    for name, spec in candidates.items():
        arm, _, _ = _parts(name)
        indices = spec["params"]["feature_indices"]
        index.setdefault(arm, indices)
        assert indices == index[arm] and len(set(indices)) == len(indices)
        picked = {features[i] for i in indices}
        assert not picked & onehots, "pooled: the symbol one-hots are task ids, never inputs"
        assert (picked & chain) == (chain if arm == "price_plus_chain" else set())
        assert ("own_iv" in picked) == (arm == "price_plus_chain")
    price, plus = index["price_only"], index["price_plus_chain"]
    assert set(price) < set(plus) and len(price) == 42 and len(plus) == 42 + 1 + len(chain)
    assert [features[i] for i in price] == features[:42]


def test_every_encoder_partitions_its_inputs_oldest_step_first(doc):
    features, candidates = doc["study"]["features"], doc["experiment"]["candidates"]
    template = _json("strike-screen-experiment-template.json")["candidates"]
    grid = _json("run-theta-mixture-grid.json")["pipeline"]["grid"]["params"]["base_config"]["candidates"]
    mlp = next(c for c in template if c["name"] == "mlp")["adapters"]["pooled"][0]
    patch = next(c for c in grid if c["name"] == "PatchTST32")["adapters"]["pooled"][0]
    assert mlp["class"] == MLP and mlp["params"] == {"arch": "mlp", "width": 16, "arch_params": {"hidden_size": 32}}
    assert patch["class"] == HF and patch["params"]["model"] == "PatchTST" and patch["params"]["config"]["d_model"] == 32
    for name, spec in candidates.items():
        _, encoder, _ = _parts(name)
        e, subset = spec["params"]["encoder"], spec["params"]["feature_indices"]
        assert e["kind"] == "external" and e["adapter"] == (mlp if encoder == "mlp" else patch)
        sequence = [row[0] for row in e["sequence_indices"]]
        assert [features[subset[i]] for i in sequence] == [f"ret_lag_{k}" for k in range(21, -1, -1)]
        assert all(len(row) == 1 for row in e["sequence_indices"])
        assert sorted(sequence + e["context_indices"]) == list(range(len(subset)))   # positions within feature_indices
        context = {features[subset[i]] for i in e["context_indices"]}
        assert {"calendar_dte", "reference_scale", "rv_22", "vl_amihud"} <= context


def _chain_columns():
    """The chain columns the owners' code emits, derived on toy frames (no data)."""
    import numpy as np
    import pandas as pd
    from index_options.cdf_study import ExactExpiryCDFPanel as Panel, RawChainFeatureBuilder as Raw
    probs = RESEARCH["proxy_probabilities"]
    frame = pd.DataFrame({
        "symbol": "A", "quote_date": [f"2017-01-0{i}" for i in range(1, 5)], "expiry": "2017-02-03",
        "actual_calendar_dte": 30, "chain_atm_iv": .2, "chain_put25_iv": .22, "chain_call25_iv": .19,
        "chain_rel_spread": .1, "chain_put_call_oi": 1.1, "chain_contracts": 50, "chain_open_interest": 100,
        "chain_quote_depth": 10, "rv_22": .01})
    before = set(frame)
    frame = Panel.add_surface_features(frame)
    scalars = [c for c in frame if c not in before]
    for i, q in enumerate(Raw._qname(p) for p in probs):
        frame[q] = np.linspace(-1, 1, len(frame)) + probs[i]
    before = set(frame)
    frame = Panel.add_surface_dynamics(frame, probs, (1, 5, 22), 22, 252)
    dynamics = [c for c in frame if c not in before]
    rows = [dict(symbol="A", date="2017-01-03", expiration="2017-02-03", strike=float(k), type=t, mark=1.0, bid=.9,
                 ask=1.1, bid_size=5, ask_size=5, open_interest=10, implied_volatility=.2, volume=3, delta=.5,
                 gamma=.1, vega=.2) for k in range(80, 121, 5) for t in ("put", "call")]
    meta = pd.DataFrame({"symbol": ["A"], "quote_date": ["2017-01-03"], "expiry": ["2017-02-03"],
                         "chain_underlying_price": [100.]})
    raw = Raw(9, [-.3, .3], .04, probs, 5, .05, .10).transform(pd.DataFrame(rows), meta)
    raw = [c for c in raw if c not in ("symbol", "quote_date", "expiry")]
    shape = ["rn_median", "rn_iqr", "rn_left_width", "rn_right_width"]    # written by ExactExpiryCDFPanel.read
    return scalars + raw + shape + dynamics


def test_the_feature_list_is_exactly_what_the_owners_emit(doc, proto):
    features = doc["study"]["features"]
    f = proto["formulation"]["inputs"]
    context = [c.strip() for c in f["context"].split(",")]
    price = features[:42]
    assert sorted(price) == sorted([f"ret_lag_{k}" for k in range(22)] + context) and len(price) == 42
    assert features[42] == "own_iv"
    chain = _chain_columns()
    assert features[43:43 + len(chain)] == chain and len(chain) == 118
    assert features[43 + len(chain):] == [f"is_{s}" for s in proto["cohort"]["symbols"]]
    assert len(features) == len(set(features)) == 42 + 1 + 118 + 118 == 279
    assert sum(c.startswith("chain_node_") for c in chain) == 54 and sum(c.startswith("rn_q_") for c in chain) == 19
    models = doc["study"]["models"]["horizon_empirical"]["params"]
    assert features[models["horizon_index"]] == "calendar_dte" and features[models["reference_index"]] == "reference_scale"


def test_references_and_selection(doc):
    study, experiment = doc["study"], doc["experiment"]
    assert study["reference_model"] == "horizon_empirical"
    assert study["comparison_references"] == ["horizon_empirical", "scaled_empirical"]
    assert set(study["models"]) == {"horizon_empirical", "scaled_empirical"}
    assert study["models"]["horizon_empirical"]["params"]["knots"] == 401
    assert all(spec["calibrate"] is False for spec in study["models"].values())
    assert experiment["selection_metric"] == "crps" and "selection_guard" not in experiment
    assert experiment["search_partitions"] == experiment["candidate_groups"] == {"pooled": list(experiment["candidates"])}
    assert set(experiment) == {"output", "max_candidates", "candidates", "candidate_groups", "search_partitions",
                               "task_features", "selection_metric", "resolutions", "expected_cells", "notes"}


# -- the cohort, the sources and the pins -----------------------------------------------------


def test_the_cohort_is_the_protocols_118_with_each_name_on_its_own_series(doc, proto):
    symbols = proto["cohort"]["symbols"]
    assert len(symbols) == proto["cohort"]["count"] == 118 == len(set(symbols))
    assert list(doc["data"]["symbols"]) == symbols
    assert doc["data"]["symbols"] == {s: s for s in symbols}                       # the name's own own_iv series
    assert len(set(doc["data"]["symbols"].values())) == 118
    assert doc["experiment"]["task_features"] == {s: f"is_{s}" for s in symbols}
    assert not set(symbols) & set(proto["cohort"]["quarantined"] + proto["cohort"]["never_exported"])


def test_the_cohort_equals_the_registered_eligible_list():
    path = PILOT / "registration" / "cohorts.json"
    if not path.exists():
        pytest.skip("the cohort registration lives under the machine-local pilot root")
    eligible = json.loads(path.read_text())["eligible"]
    assert list(_json(DOCUMENT)["data"]["symbols"]) == sorted(eligible) and len(eligible) == 118


def test_store_references_carry_zero_pins_and_agree(doc):
    data = doc["data"]
    refs = {name: data[name] for name in ("surface", "lifecycle", "chain_features", "archive_root")}
    assert {r["source"] for r in refs.values()} == {
        "theta-exact-expiry-tables", "theta-raw-chain-features", "theta-exact-expiry-archive"}
    assert all(r["stream"] == "files" and r["manifest_sha256"] == ZERO for r in refs.values())
    assert "relpath" not in data["archive_root"]
    assert (data["surface"]["relpath"], data["lifecycle"]["relpath"]) == ("surface.parquet", "lifecycle.parquet")
    assert data["chain_features"]["relpath"] == "raw_chain_features.parquet"
    assert (data["price_source"], data["iv_source"]) == ("theta-prices-index-daily", "theta-own-iv-index-daily")
    hexes = [s for s in _strings(doc) if re.fullmatch(r"[0-9a-f]{64}", s)]
    assert sorted(set(hexes)) == sorted({ZERO, doc["study"]["fold_table"]["sha256"]})
    assert hexes.count(ZERO) == 4


def test_raw_chain_is_the_risk_neutral_template_plus_the_research_proxy_defaults(doc, child_root):
    raw, template = doc["data"]["raw_chain"], _json("run-predictive-cdf-risk-neutral.json")["data"]["raw_chain"]
    for key in ("nodes", "moneyness_bounds", "max_node_gap"):
        assert raw[key] == template[key]
    for key, value in RESEARCH.items():
        assert raw[key] == value
    record = (child_root / RECORD).read_text()
    assert "min_wing_nodes 5" in record and "max_inner_gap 0.05, max_outer_gap 0.10" in record
    assert "0.85, 0.90, 0.95, 0.975, 0.99" in record
    assert RECORD in doc["data"]["notes"]
    probabilities = raw["proxy_probabilities"]
    assert probabilities[len(probabilities) // 2] == 0.5                        # the panel's rn_median position


def test_the_vl_features_come_from_the_existing_liquidity_source(doc):
    tables = doc["data"]["keyed_tables"]["tables"]
    columns = {c for t in tables.values() for c in t["columns"]}
    assert columns == {f for f in doc["study"]["features"] if f.startswith("vl_")} and len(columns) == 4
    assert {(t["source"], t["stream"]) for t in tables.values()} == {
        ("universe-liquidity-20261008", "volume22"), ("universe-liquidity-20261008", "volume66")}
    assert all(t["max_age_days"] == 0 and t["key_fields"] == ["symbol", "quote_date"] for t in tables.values())


def test_no_date_reaches_the_holdout_except_holdout_start(doc, child_root):
    holdout = doc["study"]["fold_table"]["holdout_start"]
    dates = [d for s in _strings(doc) for d in ISO.findall(s)]
    dates += [d for line in (child_root / "configs" / FOLDS).read_text().splitlines() for d in ISO.findall(line)]
    assert holdout in dates
    assert [d for d in dates if d >= "2024-01-01" and d != holdout] == []
    assert all(d < holdout for d in dates if d != holdout)


def test_expected_cells_are_declared_unresolved(doc):
    cells = doc["experiment"]["expected_cells"]
    assert cells == {"development": {}, "evaluation": {}}                       # a census of the built panel


def test_outputs_stay_under_the_pilot_study_root(doc):
    assert doc["experiment"]["output"].endswith("theta-strike-screen-20261018/panel")
    assert doc["study"]["output"] == doc["experiment"]["output"] + "/unused"
    assert doc["data"]["root"].endswith("theta-strike-screen-20261018/ob")


# -- named gaps: each flips when its owner changes ---------------------------------------------


def test_gap_a_decision_context_and_tail_weight_cannot_share_a_document(fresh):
    """L1 trains tail_crps and L3 needs the decision context; the owner refuses the pair under tail_weight."""
    from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy
    l1 = fresh["experiment"]["candidates"]["price_only__mlp__L1"]
    study = {**_resolved(fresh)["study"], "tail_weight": 1.0, "models": {"price_only__mlp__L1": l1}}
    with pytest.raises(ValueError, match="decision_context"):
        ChronologicalCDFStudy(study)
    assert fresh["study"]["decision_context"] and "tail_weight" not in fresh["study"]


def test_gap_b_the_group_grammar_has_no_seed_or_label_keys(fresh):
    from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy
    fresh["experiment"]["final_seeds"] = [11, 29, 47]
    with pytest.raises(ValueError, match="experiment keys"):
        CDFHyperparameterStudy(_resolved(fresh))


def test_gap_c_a_guard_cannot_state_the_relative_l3_versus_l1_rule(fresh):
    """The guard is absolute and per symbol against a study model; the protocol's 0.5% is relative L3/L1."""
    from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy
    guard = {"reference": "horizon_empirical", "variant": "raw", "metrics": ["decision_strike_brier"],
             "target": 0.0, "tolerance": 0.005}
    fresh["experiment"]["selection_guard"] = {**guard, "relative_to": "same_arm_l1"}
    with pytest.raises(ValueError, match="selection guard"):
        CDFHyperparameterStudy(_resolved(fresh))
    fresh["experiment"]["selection_guard"] = guard
    CDFHyperparameterStudy(_resolved(fresh))           # constructs, but states an absolute per-symbol bound instead
