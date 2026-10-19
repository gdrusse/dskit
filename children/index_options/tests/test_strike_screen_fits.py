"""No-fit contracts for the ADR-0270 strike-screen CDFExperiment fit documents.

``configs/run-strike-screen-fits-{L1,L3}.json`` are the two screen documents (one per loss) and
``run-strike-screen-fits-timing.json`` is the L1 timing pilot; ``run-strike-region-scores*.json`` score them.
Nothing here reads market data or trains: the real ``CDFExperiment`` is constructed on each document with a
placeholder panel file standing in for the cached frame (the constructor hashes the panel), and the queue's own
``_settings``/``_choose`` are asked for each arm x candidate on a toy admission with the device forced to CPU.
Every value is compared with ``strike-screen-protocol.json`` and the panel-source document, never restated.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
PILOT = Path("/home/russell/data/index_options/theta-strike-screen-20261018")
FOLDS, PROTOCOL, SOURCE = "strike-screen-folds-screen.jsonl", "strike-screen-protocol.json", "strike-screen-panel-source.json"
FITS = {"L1": "run-strike-screen-fits-L1.json", "L3": "run-strike-screen-fits-L3.json"}
TIMING, CONTROL = "run-strike-screen-fits-timing.json", "run-strike-screen-fits-control.json"
SCORES = {"L1": "run-strike-region-scores.json", "L3": "run-strike-region-scores-L3.json"}
ZERO, ISO = "0" * 64, re.compile(r"\d{4}-\d{2}-\d{2}")
HOLDOUT = "2024-01-01"
MLP = "dskit.pipeline.libs.torch_ts:ArchSequenceEncoder"
HF = "dskit.pipeline.libs.transformers:HFSequenceEncoder"
ARMS = ("price_only", "price_plus_chain")


def _json(name):
    return json.loads((CONFIGS / name).read_text())


def _losses(text):
    """Parse the protocol's ``"crps 1 + tail_crps 1"`` into TorchCDF loss terms."""
    return [{"kind": kind, "weight": float(weight)}
            for kind, weight in (part.split() for part in text.split(" + "))]


def _strings(value, skip_notes=False):
    """Yield every string of a JSON value (the values under ``notes`` excluded on request)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            if not (skip_notes and key == "notes"):
                yield from _strings(inner, skip_notes)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner, skip_notes)


@pytest.fixture(scope="module")
def docs():
    return {tag: _json(name) for tag, name in {**FITS, "timing": TIMING, "control": CONTROL}.items()}


@pytest.fixture(scope="module")
def proto():
    return _json(PROTOCOL)


@pytest.fixture(scope="module")
def source():
    return _json(SOURCE)


@pytest.fixture()
def l1(docs):
    return copy.deepcopy(docs["L1"])


def _constructible(document, tmp_path):
    """Return a copy the constructor accepts: a stand-in panel file and the repo's fold table (both hashed)."""
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore
    document = copy.deepcopy(document)
    stand_in = tmp_path / "panel.parquet"
    stand_in.write_bytes(b"placeholder, never read")
    document["panel"], document["panel_sha256"] = str(stand_in), AtomicFitStore.file_hash(stand_in)
    document["study"]["fold_table"]["path"] = str(CONFIGS / FOLDS)
    return document


# -- shape: the owners' exact key sets -------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["L1", "L3", "timing", "control"])
def test_key_sets_are_exactly_the_owners(docs, tag):
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment
    doc = docs[tag]
    assert set(doc) == CDFExperiment.KEYS
    assert all(set(arm) == CDFExperiment.ARM_KEYS for arm in doc["arms"].values())
    assert all(set(c) == CDFExperiment.CANDIDATE_KEYS and set(c["adapters"]) == {"pooled", "unpooled"}
               for c in doc["candidates"])
    assert set(doc["capacity"]) == {"pooled", "unpooled"}
    assert set(doc["capacity"]["pooled"]) == {"absolute", "per_row", "head_widths", "head_per_row", "batch_size", "device"}
    assert set(doc["capacity"]["unpooled"]) == {"absolute", "per_row", "head_widths", "batch_size", "device"}
    assert list(doc["arms"]) == list(ARMS) and [c["name"] for c in doc["candidates"]] == ["mlp", "patchtst_hf32"]


@pytest.mark.parametrize("tag", ["L1", "L3", "timing", "control"])
def test_the_real_owner_constructs_each_document(docs, tag, tmp_path):
    pytest.importorskip("torch")
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment
    experiment = CDFExperiment(_constructible(docs[tag], tmp_path))
    assert experiment.study.plan.ids() == ([1] if tag == "timing" else [1, 2])
    assert experiment.study.config["fold_table"]["holdout_start"] == HOLDOUT


def test_the_unresolved_documents_refuse_to_run_until_registered(docs):
    """Zero panel_sha256 and empty pins are placeholders: the owner refuses the unresolved copy (no panel file yet)."""
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment
    pytest.importorskip("torch")
    for doc in docs.values():
        assert doc["panel_sha256"] == ZERO and doc["source_hashes"] == {} and doc["dependencies"] == {}
        if not Path(doc["panel"]).exists():
            with pytest.raises(Exception):
                CDFExperiment(doc)


# -- the documents differ only where they must -------------------------------------------------------------------


def _without(doc, *paths):
    doc = copy.deepcopy(doc)
    for path in paths:
        node = doc
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return doc


def test_l1_and_l3_differ_only_in_losses_and_output(docs):
    a, b = docs["L1"], docs["L3"]
    cut = [("training", "losses"), ("output",), ("study", "output"), ("study", "notes")]
    assert _without(a, *cut) == _without(b, *cut)
    assert a["training"]["losses"] != b["training"]["losses"]
    assert a["output"] != b["output"] and a["study"]["output"] != b["study"]["output"]
    for key in ("study", "arms", "candidates", "capacity", "tickers", "panel"):
        if key != "study":
            assert a[key] == b[key]


def test_the_control_document_is_the_l1_document_on_the_block_shifted_panel(docs):
    """Block-shift label control: a real arm's budget (epochs 120, patience 10, both folds, 8 cells) on misaligned labels."""
    l1, c = docs["L1"], docs["control"]
    cut = [("panel",), ("output",), ("study", "output"), ("study", "notes")]
    assert _without(l1, *cut) == _without(c, *cut)
    assert c["panel"] == str(PILOT / "control" / "block-shift" / "frame.parquet") and c["panel"] != l1["panel"]
    assert c["output"] == str(PILOT / "fits" / "control") and c["panel_sha256"] == ZERO
    assert (c["training"]["epochs"], c["training"]["patience"]) == (120, 10)
    assert c["study"]["fold_table"]["roles"] == ["warmup", "scored"]
    notes = c["study"]["notes"]
    assert "no skill" in notes and "120 sessions" in notes and "0.5%" in notes and "weighted_crps" in notes and "halt" in notes


def test_the_timing_document_is_the_l1_document_with_three_epochs_on_the_warmup_fold(docs):
    a, t = docs["L1"], docs["timing"]
    cut = [("training", "epochs"), ("training", "patience"), ("study", "fold_table", "roles"), ("study", "fold_table", "notes"),
           ("output",), ("study", "output"), ("study", "notes")]
    assert _without(a, *cut) == _without(t, *cut)
    assert (t["training"]["epochs"], t["training"]["patience"]) == (3, None)
    assert t["study"]["fold_table"]["roles"] == ["warmup"] and a["study"]["fold_table"]["roles"] == ["warmup", "scored"]
    assert t["output"] != a["output"]


# -- features, arms and indices -----------------------------------------------------------------------------------


def test_the_study_features_are_the_panel_sources_279_in_order(docs, source):
    for doc in docs.values():
        assert doc["study"]["features"] == source["study"]["features"] and len(doc["study"]["features"]) == 279


def test_the_arms_nest_and_keep_the_chain_out_of_price_only(docs, proto, source):
    doc = docs["L1"]
    features = doc["study"]["features"]
    price, plus = doc["arms"]["price_only"]["features"], doc["arms"]["price_plus_chain"]["features"]
    assert price == features[:42] and plus == features[:161] and set(price) < set(plus)
    assert len(price) == 42 and len(plus) == 42 + 1 + 118
    context = [c.strip() for c in proto["formulation"]["inputs"]["context"].split(",")]
    assert sorted(price) == sorted([f"ret_lag_{k}" for k in range(22)] + context)
    assert "own_iv" not in price and plus[42] == "own_iv"
    assert not any(f.startswith("is_") for f in plus), "the queue appends the symbol one-hots itself"
    assert not set(features[161:]) & set(plus) and features[161:] == [f"is_{s}" for s in proto["cohort"]["symbols"]]
    assert all(len(set(a["features"])) == len(a["features"]) for a in doc["arms"].values())
    assert {f for f in plus if f.startswith("chain_node_")} and sum(f.startswith("rn_q_") for f in plus) == 19


@pytest.mark.parametrize("arm", ARMS)
def test_sequence_and_context_indices_partition_the_arm_oldest_step_first(docs, arm):
    spec = docs["L1"]["arms"][arm]
    features = spec["features"]
    sequence = [row[0] for row in spec["sequence_indices"]]
    assert all(len(row) == 1 for row in spec["sequence_indices"])
    assert [features[i] for i in sequence] == [f"ret_lag_{k}" for k in range(21, -1, -1)]
    assert sequence == list(range(27, 5, -1))
    assert sorted(sequence + spec["context_indices"]) == list(range(len(features)))
    assert not set(sequence) & set(spec["context_indices"])
    context = {features[i] for i in spec["context_indices"]}
    assert {"calendar_dte", "reference_scale", "rv_22", "vl_amihud"} <= context
    assert not any(name.startswith("ret_lag_") for name in context)
    assert (spec["observed_columns"], spec["min_fit"], spec["min_cal"], spec["min_disjoint"]) == ([], 40, 1, 0)


def test_the_chain_arm_context_is_the_price_context_plus_everything_after_it(docs):
    price, plus = docs["L1"]["arms"]["price_only"], docs["L1"]["arms"]["price_plus_chain"]
    assert plus["sequence_indices"] == price["sequence_indices"]
    assert plus["context_indices"] == price["context_indices"] + list(range(42, 161))
    assert len(price["context_indices"]) == 20 and len(plus["context_indices"]) == 139


def test_the_horizon_and_reference_indices_name_the_right_columns(docs):
    features = docs["L1"]["study"]["features"]
    params = docs["L1"]["study"]["models"]["horizon_empirical"]["params"]
    assert features[params["horizon_index"]] == "calendar_dte" and features[params["reference_index"]] == "reference_scale"
    assert params["knots"] == 401 and docs["L1"]["study"]["models"]["horizon_empirical"]["calibrate"] is False
    assert docs["L1"]["study"]["reference_model"] == "horizon_empirical"
    assert docs["L1"]["study"]["comparison_references"] == ["horizon_empirical"]


# -- the cohort, panel, pins and placement -------------------------------------------------------------------------


def test_the_tickers_are_the_protocols_118_and_the_registered_eligible_list(docs, proto, source):
    symbols = proto["cohort"]["symbols"]
    assert len(symbols) == 118 == len(set(symbols)) and docs["L1"]["tickers"] == symbols
    assert list(source["data"]["symbols"]) == symbols
    assert not set(symbols) & set(proto["cohort"]["quarantined"] + proto["cohort"]["never_exported"])
    registered = PILOT / "registration" / "cohorts.json"
    if registered.exists():
        eligible = json.loads(registered.read_text())["eligible"]
        assert docs["L1"]["tickers"] == sorted(eligible) and len(eligible) == 118


def test_the_panel_is_the_panel_sources_cached_frame(docs, source):
    from dskit.pipeline.libs.parquet import ParquetFrameCache
    from index_options.cdf_study import PANEL_CACHE_DIR
    frame = Path(source["experiment"]["output"]) / PANEL_CACHE_DIR / ParquetFrameCache.FRAME
    for tag, doc in docs.items():
        assert doc["panel"] == (str(PILOT / "control" / "block-shift" / "frame.parquet") if tag == "control" else str(frame))
        assert doc["panel_sha256"] == ZERO


def test_outputs_are_distinct_roots_under_the_pilot_fits(docs):
    outputs = {tag: doc["output"] for tag, doc in docs.items()}
    assert outputs == {tag: str(PILOT / "fits" / tag) for tag in ("L1", "L3", "timing", "control")}
    for doc in docs.values():
        assert doc["study"]["output"] == doc["output"] + "/unused"
        assert Path(doc["study"]["output"]).is_relative_to(PILOT) and Path(doc["panel"]).is_relative_to(PILOT)


def test_the_fold_table_is_the_protocols_screen_rows_pinned_by_content(docs, proto):
    repo = (CONFIGS / FOLDS).read_bytes()
    rows = [json.loads(line) for line in repo.decode().splitlines() if line]
    assert rows == proto["fold_tables"]["screen"] and [r["role"] for r in rows] == ["warmup", "scored"]
    for tag, doc in docs.items():
        spec = doc["study"]["fold_table"]
        assert spec["sha256"] == hashlib.sha256(repo).hexdigest() == _json(SOURCE)["study"]["fold_table"]["sha256"]
        assert set(spec) == {"path", "sha256", "holdout_start", "cal_n", "roles", "notes"}
        assert spec["path"] == str(PILOT / "registration" / FOLDS) and Path(spec["path"]).is_absolute()
        assert spec["holdout_start"] == proto["fold_tables"]["holdout_start"]["screen"] == HOLDOUT
        assert spec["cal_n"] == proto["fold_tables"]["cal_n"] == proto["windows"]["monitor_band"]["cal_n"] == 82
        assert spec["roles"] == (["warmup"] if tag == "timing" else ["warmup", "scored"])
    copy_ = PILOT / "registration" / FOLDS
    if copy_.exists():
        assert copy_.read_bytes() == repo


def test_the_study_block_equals_the_protocol_and_the_panel_source(docs, proto, source):
    study, f = docs["L1"]["study"], proto["formulation"]
    assert (study["group"], study["date"], study["end"], study["horizon"], study["target"], study["reference"]) == (
        "symbol", "quote_date", "settlement_date", "actual_calendar_dte", "terminal_return", "reference_scale")
    assert study["identity"] == ["symbol", "quote_date", "expiry"] and study["series_identity"] == ["symbol", "expiry"]
    for key in ("samples", "tail_intervals", "tail_points", "calibration_knots", "bootstrap", "group", "date", "end",
                "horizon", "target", "reference", "identity", "series_identity"):
        assert study[key] == source["study"][key]
    assert (study["samples"], study["tail_points"], study["calibration_knots"]) == (201, 101, 21)
    assert study["tail_intervals"] == [[-2.5, -0.5], [0.5, 2.5]]
    assert study["bootstrap"] == {"blocks": [30, 60], "replicates": 1000, "seed": 829}
    assert (study["tail_weight"], study["wing_metrics"], study["min_task_fit_rows"], study["min_task_cal_rows"]) == (1.0, False, 40, 1)
    assert "decision_context" not in study and "years" not in study
    # the wings the queue trains on are the strike-grid edges the protocol scores
    assert all(edge in f["strike_grid_z"] for pair in study["tail_intervals"] for edge in pair)


def test_training_and_capacity_equal_the_protocol(docs, proto):
    t, a = proto["training"], proto["arms_and_candidates"]
    for tag, doc in docs.items():
        p = doc["training"]
        assert set(p) == {"components", "deterministic", "dropout", "epochs", "learning_rate", "losses", "patience",
                          "seeds", "weight_decay"}
        assert p["components"] == a["components"][0] == 3 and p["deterministic"] is True
        assert p["dropout"] == t["regularization"]["dropout"] == 0.1
        assert t["regularization"]["weight_decay"] == t["weight_decay"]["screen"] == p["weight_decay"] == 0.01
        assert t["weight_decay"]["promotion_axis"] == [0.01, 0.3] and p["learning_rate"] == t["learning_rate"] == 0.0003
        assert p["seeds"] == t["seeds"]["screen"] == [11]
        epochs, patience = (3, None) if tag == "timing" else (t["early_stopping"]["epoch_ceiling"]["screen"],
                                                              t["early_stopping"]["patience"]["screen"])
        assert (p["epochs"], p["patience"]) == (epochs, patience)
        pooled = doc["capacity"]["pooled"]
        assert pooled["absolute"] == int(re.search(r"min\((\d+),", t["capacity"]).group(1)) == 100000
        assert pooled["per_row"] == float(re.search(r"([\d.]+) \* fit rows", t["capacity"]).group(1)) == 0.25
        assert pooled["batch_size"] == t["batch_size"] == 512 and pooled["device"] == "cuda"
        assert pooled["head_widths"] == t["head_widths"] == [8] and pooled["head_per_row"] == 4
    assert docs["L1"]["training"]["losses"] == _losses(a["losses"]["L1"])
    assert docs["L3"]["training"]["losses"] == _losses(a["losses"]["L3"])
    assert docs["timing"]["training"]["losses"] == docs["L1"]["training"]["losses"] == docs["control"]["training"]["losses"]


def test_the_unpooled_capacity_is_the_mixture_grids_and_never_run(docs):
    grid = _json("run-theta-mixture-grid.json")["pipeline"]["grid"]["params"]["base_config"]
    assert docs["L1"]["capacity"]["unpooled"] == grid["capacity"]["unpooled"]
    assert "never run" in docs["L1"]["study"]["notes"]


def test_the_candidates_are_the_templates_two_encoders(docs):
    template = {c["name"]: c for c in _json("strike-screen-experiment-template.json")["candidates"]}
    grid = _json("run-theta-mixture-grid.json")["pipeline"]["grid"]["params"]["base_config"]["candidates"]
    by_name = {c["name"]: c for c in docs["L1"]["candidates"]}
    assert by_name["mlp"]["adapters"]["pooled"] == by_name["mlp"]["adapters"]["unpooled"] == template["mlp"]["adapters"]["pooled"]
    mlp = by_name["mlp"]["adapters"]["pooled"][0]
    assert mlp["class"] == MLP and mlp["params"] == {"arch": "mlp", "width": 16, "arch_params": {"hidden_size": 32}}
    patch = by_name["patchtst_hf32"]["adapters"]
    assert patch["pooled"] == grid[0]["adapters"]["pooled"] and patch["unpooled"] == grid[0]["adapters"]["unpooled"]
    assert patch["pooled"][0]["class"] == HF and patch["pooled"][0]["params"]["model"] == "PatchTST"
    assert patch["pooled"][0]["params"]["config"]["d_model"] == 32
    assert all(c["skip_reason"] is None for c in docs["L1"]["candidates"])


def test_both_bounds_are_the_holdout_and_no_later_date_appears(docs):
    for doc in docs.values():
        assert doc["date_upper_exclusive"] == doc["end_upper_exclusive"] == HOLDOUT == doc["study"]["fold_table"]["holdout_start"]
        dates = [d for s in _strings(doc) for d in ISO.findall(s)]
        assert HOLDOUT in dates
        assert [d for d in dates if d >= HOLDOUT and d != HOLDOUT] == []
        assert all(d < HOLDOUT for d in dates if d != HOLDOUT)
    fold_dates = [d for line in (CONFIGS / FOLDS).read_text().splitlines() for d in ISO.findall(line)]
    assert max(fold_dates) < HOLDOUT


def test_the_only_hex_values_are_the_panel_placeholder_and_the_fold_pin(docs):
    for doc in docs.values():
        hexes = [s for s in _strings(doc, skip_notes=True) if re.fullmatch(r"[0-9a-f]{64}", s)]
        assert sorted(hexes) == sorted([ZERO, doc["study"]["fold_table"]["sha256"]])


def test_the_notes_say_what_the_registration_step_must_fill(docs):
    notes = docs["L1"]["study"]["notes"]
    for phrase in ("panel_sha256", "source_hashes", "dependencies", "R6 prepare", "cdf_experiment", "parquet", "predictive_cdf",
                   "torch_ts", "transformers", "EMPTY placeholders", "observed_columns is empty", "one output head per ticker", "protocol revision 3",
                   "tail_weight 1.0", "wing_metrics false", "StrikeRegionScores"):
        assert phrase in notes, phrase
    assert "shared-head intent" not in notes


# -- the queue's own arithmetic: capacity, encoders, and the contexts each loss trains on -------------------------


@pytest.fixture(scope="module")
def experiments(docs, tmp_path_factory):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment
    built = {}
    for tag in ("L1", "L3"):
        document = _constructible(docs[tag], tmp_path_factory.mktemp(tag))
        document["capacity"]["pooled"]["device"] = "cpu"        # the document says cuda; the test must not need it
        built[tag] = CDFExperiment(document)
    return built


@pytest.mark.parametrize("tag", ["L1", "L3"])
@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("candidate", ["mlp", "patchtst_hf32"])
def test_each_arm_candidate_pair_fits_the_budget_and_validates_its_encoder(experiments, tag, arm, candidate):
    from dskit.pipeline.libs.predictive_cdf import TorchCDF
    exp = experiments[tag]
    spec = next(c for c in exp.config["candidates"] if c["name"] == candidate)
    groups = exp.config["tickers"]
    admission = {g: {"fit_n": 3000} for g in groups}          # ~354k fit rows: a toy census, the real one is Stage 0's
    params, count = exp._choose(exp.config["arms"][arm], spec, "pooled", groups, admission)
    assert params["max_parameters"] == min(100000, int(3000 * len(groups) * 0.25)) and count <= params["max_parameters"]
    assert params["hidden"] == [8] and params["batch_size"] == 512
    assert params["head_features"] == list(range(len(exp.config["arms"][arm]["features"]),
                                                 len(exp.config["arms"][arm]["features"]) + 118))
    assert params["feature_indices"] == list(range(len(exp.config["arms"][arm]["features"])))
    assert params["losses"] == exp.config["training"]["losses"] and params["epochs"] == 120 and params["patience"] == 10
    TorchCDF(**params).validate_encoder(len(exp.config["arms"][arm]["features"]))


def test_the_budget_is_refused_not_overrun_on_a_small_census(experiments):
    exp = experiments["L1"]
    groups = exp.config["tickers"]
    admission = {g: {"fit_n": 40} for g in groups}              # 4,720 fit rows: 0.25 x rows is far below any recipe
    with pytest.raises(ValueError, match="no configuration meets parameter budget"):
        exp._choose(exp.config["arms"]["price_only"], exp.config["candidates"][0], "pooled", groups, admission)


def test_the_baseline_the_queue_builds_accepts_the_documents_params(experiments):
    from dskit.pipeline.libs.predictive_cdf import HorizonEmpiricalCDF
    exp = experiments["L1"]
    params = exp.config["study"]["models"]["horizon_empirical"]["params"]
    assert "condition_indices" not in params
    HorizonEmpiricalCDF(**dict(params, horizon_index=0, reference_index=1))


def test_each_loss_trains_on_the_fixed_wing_contexts_the_documents_describe(experiments):
    """No decision_context: every row's context is the tail_intervals wings and their edges, equally weighted."""
    import pandas as pd
    from dskit.pipeline.libs.predictive_cdf import TorchCDF
    frame = pd.DataFrame({"symbol": ["AAPL", "AAPL", "MSFT"], "quote_date": ["2019-01-02", "2019-01-03", "2019-01-02"],
                          "expiry": ["2019-01-18", "2019-01-18", "2019-01-18"]})
    for tag, exp in experiments.items():
        context = exp.study.decision_context_rows(frame)
        assert [c["identity"] for c in context] == [tuple(r) for r in frame.itertuples(index=False, name=None)]
        assert all(c["thresholds"] == [-2.5, -0.5, 0.5, 2.5] and c["weights"] == [0.25] * 4
                   and c["intervals"] == [[-2.5, -0.5], [0.5, 2.5]] for c in context)
        arm = exp.config["arms"]["price_only"]
        params, _ = exp._choose(arm, exp.config["candidates"][0], "pooled", exp.config["tickers"],
                                {g: {"fit_n": 3000} for g in exp.config["tickers"]})
        TorchCDF(**params).fit_decision_context(context, context)       # the losses accept the contexts
        assert {t["kind"] for t in params["losses"]} == ({"crps", "tail_crps"} if tag == "L1" else {"crps", "decision_brier"})


# -- protocol revision 3: the contract the documents execute ------------------------------------------------------


def test_the_protocol_is_revision_3_with_a_change_log_that_discloses_the_gate_sequence(proto):
    assert proto["revision"] == 3 and set(proto["change_log"]) >= {"1", "2", "3"}
    log = proto["change_log"]["3"]
    assert len(log) > 400 and "75b1c81b" in log and "committed after" in log and "superseded" in log
    for phrase in ("Stage 0", "evidence tables", "market_calibrated", "SUPERSEDED", "null", "power harness"):
        assert phrase in log, phrase
    assert "run-strike-screen-arms" not in json.dumps(proto)


@pytest.mark.parametrize("name", ["strike-screen-experiment-template.json", "strike-screen-matrix.json"])
def test_the_earlier_design_files_are_marked_superseded_first(name):
    document = _json(name)
    notes = document["notes"] if "notes" in document else document["study"]["notes"]
    assert notes.startswith("SUPERSEDED") and "run-strike-screen-fits" in notes[:400]


def test_the_monitor_band_is_82_by_its_own_derivation(proto):
    band = proto["windows"]["monitor_band"]
    assert band["derivation"] == "40 + ceil(252*60/365)" and 40 + math.ceil(252 * 60 / 365) == band["cal_n"] == 82
    assert proto["fold_tables"]["cal_n"] == 82 and "cal_n 82 in the screen" in proto["training"]["monitor_band_sensitivity"]


def test_sharing_is_what_the_queue_does_and_the_size_tiers_are_measured(proto):
    t = proto["training"]
    assert "one output head per ticker" in t["sharing"] and "118 heads" in t["sharing"] and "promotion-rung axis" in t["sharing"]
    assert "per-ticker heads = " not in t["sharing"] and "no per-ticker heads" not in t["sharing"]
    for total in ("11,118", "12,070", "18,686", "19,638"):
        assert total in json.dumps(t["size_tiers"])
    assert t["head_widths"] == [8]


def test_the_chain_input_is_the_representation_the_panel_emits_and_is_pinned_by_name(docs, proto):
    chain = proto["formulation"]["inputs"]["chain"]
    assert "rn_q_" in chain and "deferred" in chain and "ExactExpiryCDFPanel" in chain
    columns = proto["appendix"]["chain_columns"]
    assert columns == docs["L1"]["study"]["features"][43:161] == docs["L1"]["arms"]["price_plus_chain"]["features"][43:]
    assert len(columns) == len(set(columns)) == 118 and sum(c.startswith("rn_q_") for c in columns) == 19


def test_the_verification_steps_name_their_scripts_and_documents(proto):
    v = proto["verification_before_any_ranking"]
    recover, shift = v["synthetic_recoverability"], v["block_shift_label_control"]
    assert "prepare/synthetic_recoverability.py" in recover and "5%" in recover and "Stage 0" in recover
    assert "D <= 0.02" in recover and "reliability" in recover and "13 grid thresholds" in recover and "information only" in recover
    for pinned in ("learning rate 0.003", "per_row 1000", "head_per_row 1000", "24 synthetic tickers", "seed 2027"):
        assert pinned in recover, pinned
    assert "p above" not in recover and "KS p >" not in recover
    assert "prepare/make_block_shift_panel.py" in shift and CONTROL in shift and "120 sessions" in shift and "no skill" in shift
    assert "epochs 120" in shift and "8 cells" in shift and "0.5%" in shift and "halt before L1" in shift
    assert "1 - mean(model_weighted_crps) / mean(baseline_weighted_crps)" in shift
    for approximation in ("regime persistence", "purge on its own settlement", "uncalibrated"):
        assert approximation in shift, approximation


def test_the_null_and_the_power_harness_are_the_two_executable_checks(proto):
    v = proto["verification_before_any_ranking"]
    assert "no_information_control" not in v and "scale-only" not in json.dumps(v) and "calendar/scale" not in json.dumps(v)
    text = v["null_and_power"]
    assert "block-shift control IS the null" in text and "synthetic recoverability IS the power harness" in text
    assert "external encoder requires a return sequence" in text
    order = v["order"]
    assert "synthetic recoverability + null + power harness" not in order
    assert order.index("synthetic recoverability (Stage 0; the power harness)") < order.index("block-shift label control (the null)") \
        < order.index("per-epoch curves")


def test_the_caps_separate_stores_and_evidence_from_fit_outputs(proto):
    caps = proto["caps"]
    assert caps["new_output_bytes"] == 1_000_000_000 and "excluding" in caps["new_output_scope"] and "evidence tables" in caps["new_output_scope"]
    assert caps["store_ceiling_bytes"] == 8_000_000_000
    assert caps["per_file_write_bytes"] == {"fits": 67_108_864, "data_preparation": 2_147_483_648, "evidence_tables": 2_147_483_648}
    evidence = caps["evidence_tables"]
    assert evidence["files"] == ["rows.parquet", "cells.parquet"] and evidence["ceiling_bytes"] == 4_000_000_000
    assert evidence["excluded_from_new_output_cap"] is True and "957 B/row" in evidence["synthetic_evidence"]
    assert "unmeasured" in evidence["real_size_estimate"] and "first scoring document" in evidence["ceiling_status"]
    assert "min(ceiling, remaining" in caps["wall_rule"]
    assert caps["host_rss_vram_enforcement"] == "observational (logged); wall clock is enforced"


def test_the_promising_cell_rule_is_provisional_until_the_market_stage(proto):
    rule = proto["cells"]["promising_cell_rule"]
    assert set(rule) == {"provisional", "final", "status"}
    assert "market_raw" in rule["provisional"] and "market_calibrated" not in rule["provisional"] and "StrikeRegionScores" in rule["provisional"]
    assert "market_calibrated" in rule["final"] and "market stage" in rule["final"] and "2017-2023" in rule["final"]
    assert "provisional" in rule["status"] and "Holm" in rule["provisional"] + rule["final"]


def test_the_screen_inventory_and_defenses_are_in_the_protocol(proto):
    assert "16 cells" in proto["arms_and_candidates"]["screen_fits"]
    inventory = proto["inventory"]
    assert inventory["synthetic_recoverability_stage0"] == 1 and inventory["timing_neural_cells"] == 4
    assert inventory["control_neural_cells"] == 8 and inventory["screen_neural_cells"] == 16
    assert "after load" in proto["windows"]["holdout"]["read_guard"] and "pushdown" in proto["windows"]["holdout"]["read_guard"]


def test_fit_band_sampling_and_ticker_groups_are_recorded(proto):
    assert proto["scores"]["fit_band"]["sample_rows_per_cell"] == 25000 and "stride" in proto["scores"]["fit_band"]["rule"]
    groups = proto["cells"]["ticker_groups"]["secondary"]
    assert "registration" in groups and "deferred" in groups
    for tag in ("L1", "L3"):
        params = _json(SCORES[tag])["pipeline"]["scores"]["params"]
        assert params["fit_band_sample"] == 25000 and params["ticker_groups"] == {}


def test_the_fold_table_notes_describe_the_queue_not_the_hpo_selection(proto):
    notes = proto["fold_tables"]["notes"]
    assert "CDFExperiment" in notes and "CDFHyperparameterStudy" not in notes and "analysis step" in notes


def test_the_panel_source_says_search_select_evaluate_must_never_run(source):
    notes = source["notes"]
    assert "expected_cells" in notes and "must never be run" in notes
    assert "decision_regions" in source["notes"] and "decision_regions" not in source["data"]


# -- the registration artifacts outside the repo (machine-local; skipped elsewhere) ----------------------------------


def _pilot(*parts):
    path = PILOT.joinpath(*parts)
    if not path.exists():
        pytest.skip("the registration lives under the machine-local pilot root")
    return path


def test_the_inventory_draft_matches_the_documents_and_the_protocol(proto):
    inv = json.loads(_pilot("registration", "inventory-draft.json").read_text())
    assert inv["stage_1_timing"]["neural"] == 4 and inv["stage_1_control"]["neural"] == 8
    assert (inv["stage_1_control"]["epochs"], inv["stage_1_control"]["patience"]) == (120, 10)
    assert inv["stage_2_screen"]["neural"] == 16 and inv["totals"]["neural"] == 28 == 4 + 8 + 16
    ceilings = inv["caps"]["step_ceilings_seconds"]
    assert ceilings["control"] == 2100 and ceilings["sum"] == sum(v for k, v in ceilings.items() if k != "sum" and k != "scoring_each") + 2 * ceilings["scoring_each"]
    assert (ceilings["sum"] > inv["caps"]["wall_seconds_total"]) == ("above" in inv["caps"]["note"] and "approval b" in inv["caps"]["note"].lower())
    assert "(fold, symbol)" in inv["stage_2_screen"]["empirical_unit"]
    assert inv["caps"]["store_ceiling_bytes"] == proto["caps"]["store_ceiling_bytes"]
    assert inv["caps"]["per_file_write_bytes"] == proto["caps"]["per_file_write_bytes"]
    assert inv["caps"]["evidence_tables"] == proto["caps"]["evidence_tables"]["ceiling_bytes"]
    assert inv["approval_points"].keys() == {"A", "B"} and "real-data fits only" in inv["approval_points"]["B"]
    assert inv["stage_0"]["synthetic_recoverability"] == inv["synthetic_recoverability"]["script"] and "Stage 0" in inv["synthetic_recoverability"]["classification"]
    assert "gate change" in inv["synthetic_recoverability"]["sequence_disclosure"] and "superseded" in inv["synthetic_recoverability"]["sequence_disclosure"]
    assert "provisional" in inv["promising_cells"] and "market_calibrated" in inv["promising_cells"]
    assert inv["totals"]["synthetic_recoverability"] == 1 and "excluding" in inv["caps"]["new_output_scope"]
    for name in (CONTROL, FITS["L1"], FITS["L3"], TIMING):
        assert inv["documents"][name]["file_sha256"] == hashlib.sha256((CONFIGS / name).read_bytes()).hexdigest()
    repo_sha = hashlib.sha256((CONFIGS / PROTOCOL).read_bytes()).hexdigest()
    entry = inv["documents"]["strike-screen-protocol.json"]
    assert entry["file_sha256"] == entry["registration_copy_sha256"] == repo_sha and entry["revision"] == 3
    assert entry["registration_copy"].endswith("strike-screen-protocol-rev3.json")


def test_the_registration_copy_of_the_protocol_is_a_byte_copy_of_the_repo_file():
    copy_ = _pilot("registration", "strike-screen-protocol-rev3.json")
    assert copy_.read_bytes() == (CONFIGS / PROTOCOL).read_bytes()
    assert not (PILOT / "registration" / "strike-screen-protocol-rev2.json").exists()


def test_the_study_readme_orders_the_steps_and_applies_the_caps():
    text = _pilot("documents", "study", "README.md").read_text()
    order = ["APPROVAL POINT A", "## 1. Converter", "## 2. Onboard", "## 3. Replace", "## 4. Raw-chain", "## 5. The panel stage",
             "## 6. Registration", "### 6b. Synthetic recoverability", "APPROVAL POINT B", "## 7. Timing", "## 8. Block-shift",
             "## 9. Stop rule", "## 10. L1", "## 11. StrikeRegionScores", "## 12. Report"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    for phrase in ("0.0003", "synthetic scale", "D <= 0.02", "weighted_crps skill", "--fsize=2147483648", "prlimit --fsize=67108864", "evidence tables", "strike-screen-protocol-rev3.json", "convenience", "provisional", "market_calibrated",
                   "documents/superseded", "excluding the stores",
                   "observational", "refuse", "empty source_hashes", "expected_cells", "must never"):
        assert phrase in text, phrase
    assert _pilot("prepare", "synthetic_recoverability.py").exists() and _pilot("prepare", "make_block_shift_panel.py").exists()


def test_the_superseded_recoverability_run_is_kept_and_the_fresh_one_is_recorded():
    old = json.loads(_pilot("stage0", "recoverability-superseded-gate-change.json").read_text())
    assert old["gates"] and old["seed"] == 2027
    for suffix in ("recoverability-superseded-work",):
        assert _pilot("stage0", suffix).is_dir()


def test_a_recorded_recoverability_result_has_pass_or_fail_per_gate():
    result = json.loads(_pilot("stage0", "recoverability.json").read_text())
    gates = result["gates"]
    assert result["protocol_revision"] == 3 and result["tickers"] == 24 and result["seed"] == 2027
    assert set(gates) == {"crps_within_5pct_of_oracle", "pit_ks_statistic_at_most_0.02", "max_reliability_gap_at_most_0.02"}
    assert all(isinstance(v, bool) for v in gates.values()) and result["passed"] == all(gates.values())
    assert {"oracle_pit_ks_statistic", "pit_ks_p", "oracle_pit_ks_p", "max_abs_reliability_gap", "oracle_max_abs_reliability_gap"} <= set(result)
    assert result["learning_rate"] == 0.003 and result["epochs"] == 30 and result["patience"] == 5


# -- the scoring documents ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["L1", "L3"])
def test_each_scoring_document_validates_plans_and_names_its_fit_document(tag, docs, proto):
    from dskit.pipeline.document import load_document
    from dskit.pipeline.planner import plan
    from index_options.strike_region_scores import StrikeRegionScores
    path = CONFIGS / SCORES[tag]
    assert plan(load_document(str(path))) is not None
    raw = json.loads(path.read_text())
    node = raw["pipeline"]["scores"]
    params = node["params"]
    assert node["uses"] == "index_options.strike_region_scores:StrikeRegionScores"
    assert StrikeRegionScores.validate_params(params) == []
    # the resolved copy of the repo document of the same name, beside the other study documents of the pilot root
    assert Path(params["experiment_document"]) == PILOT / "documents" / "study" / FITS[tag]
    assert params["panel"] == docs[tag]["panel"]
    assert params["output"] == str(PILOT / "scores" / f"strike-region-scores-{tag}")
    assert raw["outputs"]["run_root"] == str(PILOT / "scores" / f"run-{tag}")
    assert params["z_grid"] == proto["formulation"]["strike_grid_z"] and params["dte_slices"] == proto["cells"]["dte_slices"]
    assert params["sub_periods"] == proto["windows"]["validation"]["sub_periods"]
    assert params["end_before"] == docs[tag]["date_upper_exclusive"] == HOLDOUT
    assert Path(params["listed_strikes"]["data_root"]) == PILOT / "ob"
    assert FITS[tag] in raw["notes"] and ("L3" if tag == "L3" else "L1") in raw["notes"]


def test_the_two_scoring_documents_differ_only_in_the_loss_they_name():
    l1, l3 = (_json(SCORES[t]) for t in ("L1", "L3"))
    cut = [("name",), ("notes",), ("outputs", "run_root"), ("pipeline", "scores", "params", "experiment_document"),
           ("pipeline", "scores", "params", "output")]
    assert _without(l1, *cut) == _without(l3, *cut)
    assert l1["name"] != l3["name"] and l1["outputs"]["run_root"] != l3["outputs"]["run_root"]


def test_the_scoring_panel_is_the_fit_documents_panel_and_the_cache_frame(docs):
    for tag in ("L1", "L3"):
        params = _json(SCORES[tag])["pipeline"]["scores"]["params"]
        assert params["panel"] == docs[tag]["panel"] == docs["L1"]["panel"]
        assert Path(params["panel"]).name == "frame.parquet" and Path(params["panel"]).parent.name == "panel-cache"


def test_nothing_in_the_scoring_documents_reaches_the_holdout():
    for tag in ("L1", "L3"):
        raw = _json(SCORES[tag])
        dates = [d for s in _strings(raw, skip_notes=True) for d in ISO.findall(s)]    # the notes date the decision, not data
        assert [d for d in dates if d >= HOLDOUT and d != HOLDOUT] == []
