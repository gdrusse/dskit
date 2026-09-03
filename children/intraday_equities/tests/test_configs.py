"""Every shipped config validates, and the action documents stay twins."""

import copy
import json
import os

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.pipeline.document import load_document

from intraday_equities.connectors import AlpacaBars, SchwabBars
from intraday_equities.nodes import _emit_feature_names, session_feature_names

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

ACTION_DOCS = (
    "run-action-01m.json",
    "run-action-05m.json",
    "run-action-15m.json",
    "run-action-30m.json",
    "run-action-60m.json",
)
HORIZONS = {
    "run-action-01m.json": (60_000, 1),
    "run-action-05m.json": (300_000, 5),
    "run-action-15m.json": (900_000, 15),
    "run-action-30m.json": (1_800_000, 30),
    "run-action-60m.json": (3_600_000, 60),
}
ACTION_NODES = {
    "universe", "alpaca", "session", "window", "grid", "tradable",
    "train_rows", "val_rows", "qhat", "select",
}


def _path(name):
    return os.path.join(CONFIGS, name)


def _raw(name):
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


UNIVERSE = _raw("universe.json")


def test_every_run_document_loads():
    for name in sorted(os.listdir(CONFIGS)):
        if name.startswith("run-") and name.endswith(".json"):
            document = load_document(_path(name))
            assert document.hash, name


def test_action_documents_differ_only_in_cadence():
    canonical = None
    for name in ACTION_DOCS:
        raw = _raw(name)
        assert set(raw["pipeline"]) == ACTION_NODES, name
        period, lead = HORIZONS[name]
        assert raw["pipeline"]["grid"]["params"]["period_ms"] == period
        assert raw["pipeline"]["window"]["params"]["label_lead"] == lead
        clone = copy.deepcopy(raw)
        clone["name"] = "pinned"
        clone["notes"] = "pinned"
        clone["pipeline"]["window"]["params"]["label_lead"] = 0
        clone["pipeline"]["grid"]["params"]["period_ms"] = 0
        clone["pipeline"]["window"]["notes"] = "pinned"
        clone["pipeline"]["grid"]["notes"] = "pinned"
        if canonical is None:
            canonical = clone
        else:
            assert clone == canonical, name


def test_action_documents_share_cuts_and_ridge():
    first = _raw(ACTION_DOCS[0])
    for name in ACTION_DOCS[1:]:
        other = _raw(name)
        assert other["splits"] == first["splits"]
        assert other["pipeline"]["qhat"]["params"] == first["pipeline"]["qhat"]["params"]
        assert other["pipeline"]["select"]["params"] == first["pipeline"]["select"]["params"]
        assert other["pipeline"]["select"]["inputs"]["tradable"] == "$universe.tradable"


def test_horizon_scan_never_reads_the_lockbox():
    scan = _raw("run-horizon-scan.json")["pipeline"]["scan"]["params"]
    assert set(scan) == {"split", "train_end_ms", "val_start_ms", "val_end_ms"}
    assert scan["split"] == "val"
    assert "test_end_ms" not in json.dumps(scan)


def test_hl_scan_stops_before_august():
    raw = _raw("run-hl-scan.json")
    assert raw["splits"]["test_end_ms"] == UNIVERSE["holdouts"]["test_a_end_ms"]
    assert raw["splits"]["test_end_ms"] < UNIVERSE["holdouts"]["test_b_start_ms"]
    lscan = raw["pipeline"]["lscan"]["params"]
    assert lscan["lead"] == "$scan.metrics.farthest_confident_lead"
    assert lscan["split"] == "val"
    assert "test_end_ms" not in json.dumps(lscan)
    assert raw["pipeline"]["features"]["params"]["lookback"] == (
        "$universe.spec.scan.lookback_stop"
    )
    assert raw["pipeline"]["features"]["params"]["layout"] == "columns"
    assert raw["pipeline"]["scan"]["inputs"]["bars"] == "$features.tape"
    assert raw["pipeline"]["lscan"]["inputs"]["bars"] == "$features.tape"


def test_hstar_cv_clock_mean_doc_is_superseded():
    raw = _raw("run-hstar-cv.json")
    assert raw["name"] == "intraday-equities-hstar-cv"
    assert "SUPERSEDED" in raw["notes"]
    assert raw["walkforward"]["objective"] == "$scan.metrics.go"


def test_hstar_cv_series_walkforward_pins():
    raw = _raw("run-hstar-cv-series.json")
    wf = raw["walkforward"]
    assert wf["first"] == "2019-01-07"
    assert wf["step_days"] == 63
    assert wf["count"] == 40
    assert wf["val_days"] == 63
    assert wf["embargo_days"] == 5
    assert wf["train_days"] == 730
    assert wf["objective"] == "$scan.metrics.go_frac"
    assert wf["select"] == "max"
    trees = raw["pipeline"]["scan"]["params"]["estimator_params"]
    assert trees["max_depth"] == 4
    assert trees["num_leaves"] == 15
    assert trees["min_child_samples"] == 400
    assert trees["reg_lambda"] == 5.0
    scan_params = raw["pipeline"]["scan"]["params"]
    assert scan_params["hpo_trials"] == 8
    assert scan_params["hpo_val_days"] == 63
    assert set(scan_params["hpo_space"]) == {
        "num_leaves", "max_depth", "min_child_samples",
        "learning_rate", "reg_lambda",
    }
    feat = raw["pipeline"]["features"]["params"]
    assert feat["lookback"] == 0
    extra = feat["momentum_horizons"]
    assert [row["tag"] for row in extra] == ["3m", "2h", "3h", "2s", "1w"]
    industry = tuple(sorted(set((UNIVERSE.get("industry") or {}).values())))
    base = session_feature_names(
        0, UNIVERSE["scales"], UNIVERSE["reference"], industry,
    )
    assert len(base) == 46
    assert all(not name.startswith("ret_lag_") for name in base)
    names = _emit_feature_names(
        0, UNIVERSE["scales"], UNIVERSE["reference"], industry, extra,
    )
    assert len(names) == 66
    assert raw["pipeline"]["scan"]["uses"] == (
        "intraday_equities-no-information-scan"
    )
    assert "test_end_ms" not in json.dumps(raw["pipeline"]["scan"])
    document = load_document(_path("run-hstar-cv-series.json"))
    assert document.walkforward.fold_cutoffs()[-1] == "2025-09-29"
    assert document.walkforward.fold_cutoffs()[0] == "2019-01-07"
    assert len(document.walkforward.fold_cutoffs()) == 40
    assert document.name == "intraday-equities-hstar-cv-series"


#: 1165 RTH minutes is the scan's farthest confident lead.
_HORIZON_LEAD = 1165


def test_horizon_models_labels_stop_at_the_cuts():
    raw = _raw("run-horizon-models.json")
    assert raw["pipeline"]["label_train"]["params"]["lead"] == _HORIZON_LEAD
    assert raw["pipeline"]["label_val"]["params"]["lead"] == _HORIZON_LEAD
    for key in ("label_train", "label_val"):
        params = raw["pipeline"][key]["params"]
        assert params["train_end_ms"] == "$splits.train_end_ms"
        assert params["val_end_ms"] == "$splits.val_end_ms"
        assert "test_end_ms" not in params
    session_cols = list(session_feature_names(
        UNIVERSE["lookback"], UNIVERSE["scales"], UNIVERSE["reference"],
        tuple(sorted(set((UNIVERSE.get("industry") or {}).values()))),
    ))
    for key in ("ridge", "tree"):
        assert raw["pipeline"][key]["params"]["features"] == session_cols
        assert raw["pipeline"][key]["params"]["label"] == "y_next"
    tags = [scale["tag"] for scale in UNIVERSE["scales"]]
    har = []
    for prefix in ("ret", "rv", "range", "vol"):
        har.extend(f"{prefix}_{tag}" for tag in tags)
    har.extend(["overnight_gap"] * len(tags))
    har.extend(["residual_SPY"] * len(tags))
    expect = len(tags) * 6
    for key in ("dlinear", "mlp", "patchtst", "transformer"):
        feats = raw["pipeline"][key]["params"]["features"]
        assert feats == har
        assert len(feats) == expect
        assert raw["pipeline"][key]["params"]["seq_len"] == len(tags)
        assert raw["pipeline"][key]["params"]["channels"] == 6
    assert raw["pipeline"]["dlinear"]["params"]["loss"] == (
        "torch.nn.functional:smooth_l1_loss"
    )
    assert raw["pipeline"]["mlp"]["params"]["loss"] == (
        "torch.nn.functional:smooth_l1_loss"
    )
    assert raw["pipeline"]["patchtst"]["params"]["head"] == "binary"
    assert raw["pipeline"]["transformer"]["params"]["head"] == "binary"
    assert raw["pipeline"]["patchtst"]["params"]["label"] == "y_up"
    assert raw["pipeline"]["transformer"]["params"]["label"] == "y_up"


def test_train_has_no_search_node():
    raw = _raw("run-train.json")
    assert "search" not in raw["pipeline"]
    assert raw["pipeline"]["select"]["uses"] == "intraday_equities-portfolio"


def test_hpo_documents_declare_their_trial_counts():
    counts = {
        "run-hpo-linear.json": 32,
        "run-hpo-tree.json": 40,
        "run-hpo-tcn.json": 24,
    }
    for name, n_trials in counts.items():
        search = _raw(name)["pipeline"]["search"]
        assert search["params"]["n_trials"] == n_trials
        assert search["params"]["objective"] == "$select.metrics.rank_ic"


def test_framework_pins_hl_keep_and_holdouts():
    raw = _raw("run-framework.json")
    keep = UNIVERSE["keep_features"]
    derived = set(session_feature_names(
        UNIVERSE["lookback"], UNIVERSE["scales"], UNIVERSE["reference"],
        tuple(sorted(set((UNIVERSE.get("industry") or {}).values()))),
    ))
    assert keep
    assert all(name in derived for name in keep)
    assert all(not name.startswith("ret_lag_") for name in keep)
    assert UNIVERSE["horizon"]["label_lead"] == 470
    assert UNIVERSE["scan"]["picked_lookback"] == 120
    assert UNIVERSE["lookback"] == 30
    pipe = raw["pipeline"]
    assert pipe["label_train"]["params"]["lead"] == (
        "$universe.spec.horizon.label_lead"
    )
    assert pipe["label_val"]["params"]["lead"] == (
        "$universe.spec.horizon.label_lead"
    )
    assert pipe["qhat"]["params"]["features"] == keep
    assert pipe["search"]["params"]["n_trials"] == 50
    assert pipe["search"]["params"]["objective"] == "$select.metrics.rank_ic"
    assert pipe["ensemble"]["uses"] == "top-trials"
    assert pipe["ensemble"]["params"]["frac"] == 0.1
    assert pipe["ensemble"]["params"]["size"] == 5
    assert pipe["ensemble"]["params"]["select"] == "max"
    assert raw["splits"]["test_end_ms"] == UNIVERSE["holdouts"]["test_a_end_ms"]
    assert pipe["features"]["params"]["layout"] == "columns"
    assert pipe["label_train"]["inputs"]["bars"] == "$features.tape"
    assert pipe["label_val"]["inputs"]["bars"] == "$features.tape"


def test_lookback_agrees_with_ridge_features():
    lookback = UNIVERSE["lookback"]
    features = _raw("run-train.json")["pipeline"]["qhat"]["params"]["features"]
    assert _raw("run-train.json")["pipeline"]["window"]["params"]["lookback"] == (
        "$universe.lookback"
    )
    assert features == [f"ret_lag_{i}" for i in range(lookback)]


def test_sources_and_suites_follow_the_universe():
    symbols = UNIVERSE["symbols"]
    assert set(UNIVERSE["tradable"]) | set(UNIVERSE["reference"]) == set(symbols)
    assert not set(UNIVERSE["tradable"]) & set(UNIVERSE["reference"])
    alpaca = _raw("source-alpaca-backfill.json")
    schwab = _raw("source-schwab-live.json")
    assert alpaca["symbols"] == schwab["symbols"] == symbols
    session = UNIVERSE["session"]
    width = session["rth_end_minutes"] - session["rth_start_minutes"]
    horizon = UNIVERSE["horizon"]
    assert horizon["lead_stop"] == 3 * width
    assert horizon["anchors"] == [width, 2 * width, 3 * width]
    assert any(
        scale["width"] == horizon["lead_stop"] and scale["cross_session"]
        for scale in UNIVERSE["scales"]
    )
    assert "2022-06-20" in UNIVERSE["holidays"]
    assert "2021-06-18" not in UNIVERSE["holidays"]


# The cohort, the calendar and the grid geometry. A universe variant may
# move the horizon, the estimator or a scale's cross_session flag; it may
# NOT move any of these, because two copies of a cohort that can drift are
# the defect this file exists to prevent.
_COHORT_KEYS = (
    "symbols", "tradable", "reference", "industry", "holidays",
    "session", "period_ms", "offset_ms", "price_field", "max_gap_minutes",
)


def _universe_path(raw, name):
    """The universe file a run doc pins, asserted consistent across nodes."""
    path = raw["pipeline"]["universe"]["params"]["path"]
    for node in raw["pipeline"].values():
        if node.get("uses") == "intraday_equities-bars":
            assert node["params"]["universe"] == path, name
    return path


def test_run_docs_do_not_restate_the_cohort():
    for name in _run_docs():
        raw = _raw(name)
        path = _universe_path(raw, name)
        if path != "configs/universe.json":
            # A variant is allowed, but its cohort must be the SAME cohort.
            variant = json.loads(
                open(os.path.join(CONFIGS, os.path.basename(path))).read()
            )
            for key in _COHORT_KEYS:
                assert variant.get(key) == UNIVERSE.get(key), (
                    f"{name}: {path} diverges from universe.json at {key!r}"
                )
        for node in raw["pipeline"].values():
            if node.get("uses") == "intraday_equities-keep-symbols":
                assert node["inputs"]["symbols"] == "$universe.tradable", name
            if node.get("uses") == "intraday_equities-portfolio":
                assert node["inputs"]["tradable"] == "$universe.tradable", name
                assert "tradable" not in node["params"], name


def test_sources_pin_the_same_one_minute_cohort():
    alpaca = _raw("source-alpaca-backfill.json")
    schwab = _raw("source-schwab-live.json")
    assert alpaca["symbols"] == schwab["symbols"] == UNIVERSE["symbols"]
    assert alpaca["timeframe"] == schwab["timeframe"] == [1, "Minute"]
    assert alpaca["start"] == "2016-01-01"
    assert alpaca["feed"] == "sip"
    assert alpaca["adjustment"] == "raw"
    assert alpaca["storage"] == schwab["storage"] == {
        "payload_codec": "gzip",
        "observations_codec": "gzip",
    }
    check_config(AlpacaBars(), alpaca)
    check_config(SchwabBars(), schwab)


def test_split_source_declares_its_scale_and_the_data_cut():
    """The store a model is fit on says its price scale out loud (ADR-0063).

    The raw source keeps the as-traded scale for the Schwab overlap; the
    split source is the one documents read, and its ``end`` is the study's
    hard cut expressed where the fetch happens rather than trimmed after.
    """
    split = _raw("source-alpaca-split-backfill.json")
    raw = _raw("source-alpaca-backfill.json")
    assert split["adjustment"] == "split"
    assert raw["adjustment"] == "raw"
    assert split["end"] == "2026-02-28T23:59:59+00:00"
    assert split["start"] == raw["start"] == "2016-01-01"
    assert split["feed"] == raw["feed"] == "sip"
    assert split["timeframe"] == raw["timeframe"] == [1, "Minute"]
    assert split["storage"] == raw["storage"]
    assert len(set(split["symbols"])) == len(split["symbols"])
    # The cohort, plus the market and sector funds the cross-stock work
    # needs. XLE and XLK split in December 2025, inside this window.
    assert set(UNIVERSE["symbols"]) < set(split["symbols"])
    assert {"QQQ", "XLF", "XLV", "XLE", "XLK", "XLP"} <= set(split["symbols"])
    check_config(AlpacaBars(), split)


def test_suites_and_asset_model_validate():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}
    for name in ("suite-alpaca-bars.json", "suite-schwab-bars.json"):
        suite = load_suite(_path(name))
        assert [rule.id for rule in suite.rules] == [
            "bars-arrived",
            "close-present",
            "close-positive",
            "ohlc-present",
            "symbol-vocabulary",
            "dates-parse-bitemporally",
        ]
        vocab = next(rule for rule in suite.rules if rule.id == "symbol-vocabulary")
        assert vocab.kwargs["values"] == UNIVERSE["symbols"]


def _run_docs():
    return sorted(
        name for name in os.listdir(CONFIGS)
        if name.startswith("run-") and name.endswith(".json")
    )


def test_every_run_uses_one_local_mlflow_experiment():
    """Cadence and HPO compare in one local store, not per-run dirs."""
    for name in _run_docs():
        sinks = _raw(name)["tracking"]["sinks"]
        assert len(sinks) == 1, name
        sink = sinks[0]
        assert sink["kind"] == "dskit.pipeline.libs.mlflow:MlflowTracker", name
        assert sink["params"]["tracking_uri"] == "sqlite:///mlruns.db", name
        assert sink["params"]["experiment"] == "intraday_equities", name


def test_the_child_installs_what_its_tracking_sinks_need():
    import tomllib

    with open(os.path.join(CHILD_ROOT, "pyproject.toml"), "rb") as fh:
        declared = tomllib.load(fh)["project"]["dependencies"]
    for name in _run_docs():
        for sink in _raw(name)["tracking"]["sinks"]:
            module = sink["kind"].split(":")[0]
            pack = module.rsplit(".", 1)[1]
            assert any(pack in req for req in declared), (name, pack, declared)


def test_tracking_is_not_identity(tmp_path):
    name = _run_docs()[0]
    with_track = load_document(_path(name))
    raw = _raw(name)
    raw.pop("tracking")
    bare = tmp_path / name
    bare.write_text(json.dumps(raw), encoding="utf-8")
    assert with_track.hash == load_document(str(bare)).hash


# The store every model is fit on, and the window it may read (ADR-0063,
# ADR-0066). run-feed-parity is the one exception and says why.
SPLIT_SOURCE = "alpaca-sip-split"
RAW_ONLY = {"run-feed-parity.json"}
STUDY_START_MS = 1514764800000  # 2018-01-01T00:00:00Z
# The quoted family (P4 arm C): the only runs whose price may be the
# NBBO midpoint rather than the last trade. Minute quotes exist for
# 2024-11-01 onward and for LLY and XOM alone, so these runs start where
# the quotes start and score those two names. Both departures are bought
# by the same fact and are checked here rather than waived: a run in this
# family MUST declare a quote source, MUST start on the quote start, and
# MUST score exactly the quoted pair.
QUOTE_SOURCE = "alpaca-sip-quotes"
QUOTE_START_MS = 1730419200000  # 2024-11-01T00:00:00Z
QUOTED_NAMES = ["LLY", "XOM"]


def _quoted_family():
    """Run docs that read the minute-quote tree, by file name."""
    names = set()
    for name in _run_docs():
        for node in _raw(name)["pipeline"].values():
            if (
                isinstance(node, dict)
                and node.get("uses") == "intraday_equities-bars"
                and node.get("params", {}).get("quote_source") is not None
            ):
                names.add(name)
    return names


def test_every_run_reads_the_split_adjusted_store_from_the_study_start():
    """No run may read a tape with a change of unit in it (ADR-0063).

    ``run-feed-parity`` compares the Schwab live prints against the
    vendor's as-traded ones, which only works on the RAW scale — that is
    the whole reason ``alpaca-sip`` still exists — so it is the single
    documented exception.
    """
    seen = 0
    for name in _run_docs():
        for node in _raw(name)["pipeline"].values():
            if not isinstance(node, dict):
                continue
            if node.get("uses") != "intraday_equities-bars":
                continue
            seen += 1
            params = node["params"]
            if name in RAW_ONLY:
                # Two tapes, both as-traded: the Alpaca history and the
                # Schwab live prints it is compared against.
                assert params["source"] in ("alpaca-sip", "schwab"), name
                assert "start_ms" not in params, name
                continue
            assert params["source"] == SPLIT_SOURCE, name
            if params.get("quote_source") is not None:
                # The quoted family still reads the split-adjusted tape;
                # it just starts later, because a minute with no quote
                # cannot be scored on the midpoint. Later than the study
                # start is safe by construction: ADR-0066's job is to put
                # the XLF spin-off out of reach, and moving the start
                # FORWARD cannot undo that.
                assert params["quote_source"] == QUOTE_SOURCE, name
                assert params["start_ms"] == QUOTE_START_MS, name
                assert params["start_ms"] > STUDY_START_MS, name
                continue
            assert params["start_ms"] == STUDY_START_MS, name
    assert seen >= len(_run_docs())


def test_the_study_start_puts_the_xlf_spin_off_out_of_reach():
    """2018-01-01 is after XLF's 2016-09-19 XLRE spin-off (ADR-0066).

    That spin-off was carried out partly as a 1231-for-1000 share split
    and the vendor adjusts neither scale for it, so it is the one change
    of unit the split-adjusted store still contains. The study start is
    what keeps it out of every feature input.
    """
    from datetime import datetime, timezone

    start = datetime.fromtimestamp(STUDY_START_MS / 1000, tz=timezone.utc)
    assert start == datetime(2018, 1, 1, tzinfo=timezone.utc)
    spin_off = datetime(2016, 9, 19, tzinfo=timezone.utc)
    assert spin_off < start
    # And it must not eat into any fold: the earliest bar the walk reads
    # is its first validation date less the training window.
    from datetime import timedelta

    doc = _raw("run-multi3-h01-ridge.json")["walkforward"]
    first_val = datetime.fromisoformat(doc["first"]).replace(tzinfo=timezone.utc)
    earliest_train = first_val - timedelta(days=doc["train_days"])
    assert start < earliest_train


def test_the_five_tradables_are_scored_again():
    """The split fix removed the reason AAPL and WMT were excluded."""
    tradable = sorted(UNIVERSE["tradable"])
    quoted = _quoted_family()
    checked = 0
    for name in _run_docs():
        node = _raw(name)["pipeline"].get("names")
        if not isinstance(node, dict) or node.get("uses") != "filter":
            continue
        for clause in node["params"]["where"]:
            if clause["field"] == "symbol" and clause["op"] == "in":
                if name in quoted:
                    # Not an exclusion on price-history grounds, which is
                    # what this test exists to forbid: the other three
                    # names have no minute quotes at all, so scoring them
                    # here would put a midpoint against a trade price.
                    assert sorted(clause["value"]) == QUOTED_NAMES, name
                    continue
                assert sorted(clause["value"]) == tradable, name
                checked += 1
    assert checked >= 30


def test_a_run_may_move_spacing_and_price_without_a_second_universe():
    """ADR-0065: the three run knobs validate, and nothing else does."""
    from intraday_equities.nodes import (
        UNIVERSE_OVERRIDE_KEYS,
        NoInformationScan,
        Universe,
    )

    assert set(UNIVERSE_OVERRIDE_KEYS) == {
        "period_ms", "offset_ms", "price_field",
    }
    path = _path("universe.json")
    assert Universe.validate_params({
        "path": path,
        "overrides": {"period_ms": 60_000, "price_field": "vwap"},
    }) == []
    # The cohort keys stay in the cohort file.
    problems = Universe.validate_params({
        "path": path, "overrides": {"symbols": ["AAPL"]},
    })
    assert problems and "cohort" in problems[0]
    # A price field the store cannot carry is a typo, not a knob.
    assert Universe.validate_params({
        "path": path, "overrides": {"price_field": "closing"},
    })
    # P1's grid: rows every minute, every cell judged every 30.
    assert NoInformationScan.validate_params({
        "split": "val",
        "train_end_ms": 1, "val_start_ms": 2, "val_end_ms": 3,
        "score_period_ms": 1_800_000,
    }) == []


# The scan knobs a P7 cell is ALLOWED to move. Everything else in the
# document, in every node, must equal the P1 s05 cell at the same
# look-ahead — that is what makes the shortlist a model comparison
# rather than a second grid (ADR-0071).
_MODEL_BLOCK = {
    "estimator", "estimator_params", "hpo_trials", "hpo_seed",
    "hpo_val_days", "hpo_embargo_days", "hpo_space", "hpo_objective",
}


def _p7_cells():
    return tuple(sorted(
        name for name in os.listdir(CONFIGS)
        if name.startswith("run-p7-") and name.endswith(".json")
    ))


def test_the_p7_shortlist_moves_the_model_and_nothing_else():
    """Each P7 cell is its P1 s05 twin with a different estimator."""
    cells = _p7_cells()
    assert len(cells) == 19, cells
    for name in cells:
        lead = name.rsplit("-h", 1)[1][:2]
        base = _raw(f"run-p1-s05-h{lead}-ridge.json")
        cell = _raw(name)
        assert cell["walkforward"] == base["walkforward"], name
        assert cell["outputs"] == base["outputs"], name
        assert cell["tracking"] == base["tracking"], name
        assert set(cell["pipeline"]) == set(base["pipeline"]), name
        for node, spec in base["pipeline"].items():
            if node == "scan":
                continue
            assert cell["pipeline"][node] == spec, (name, node)
        want = base["pipeline"]["scan"]["params"]
        got = cell["pipeline"]["scan"]["params"]
        assert set(want) - _MODEL_BLOCK == set(got) - _MODEL_BLOCK, name
        for knob in set(want) - _MODEL_BLOCK:
            assert got[knob] == want[knob], (name, knob)
        # The label and the lattice are the load-bearing ones; say so.
        assert got["score_period_ms"] == 1_800_000, name
        assert got["lead_start"] == got["lead_stop"] == int(lead), name


def test_every_p7_cell_gets_the_same_purged_search():
    """ADR-0071: tuning effort is declared and equal across the family."""
    for name in _p7_cells():
        params = _raw(name)["pipeline"]["scan"]["params"]
        assert params["hpo_trials"] >= 4, name
        assert params["hpo_seed"] == 0, name
        assert params["hpo_val_days"] == 63, name
        assert params["hpo_embargo_days"] == 5, name
        assert params["hpo_objective"] == "ic", name
        space = params["hpo_space"]
        assert isinstance(space, dict) and space, name
        for values in space.values():
            assert isinstance(values, list) and values, name


def test_the_p7_nets_are_searched_where_the_old_nets_were_not():
    """The unfairness the P7 research doc found, fixed in config.

    The multi3 nets ran no search, no weight decay and a fixed epoch
    count. Putting ``epochs`` in the purged grid IS a stopping rule
    chosen on data the fold's validation never reads.
    """
    old = _raw("run-multi3-h01-gru.json")["pipeline"]["scan"]["params"]
    assert "hpo_space" not in old and "hpo_trials" not in old
    assert old["estimator_params"]["weight_decay"] == 0.0
    assert old["estimator_params"]["epochs"] == 30

    nets = [
        name for name in _p7_cells()
        if "gru4" in name or "nlinear" in name
    ]
    assert len(nets) == 7, nets
    for name in nets:
        params = _raw(name)["pipeline"]["scan"]["params"]
        assert params["estimator"].endswith("ZooEstimator"), name
        assert params["estimator_params"]["weight_decay"] > 0.0, name
        assert "epochs" in params["hpo_space"], name


def test_the_seed_averaged_cell_is_its_single_seed_twin():
    """ADR-0071: five seeds averaged, the same net otherwise."""
    single = _raw("run-p7-gru4-h01.json")["pipeline"]["scan"]["params"]
    averaged = _raw("run-p7-gru4-seedavg-h01.json")["pipeline"]["scan"]["params"]
    assert averaged["estimator"] == single["estimator"]
    one = dict(single["estimator_params"])
    many = dict(averaged["estimator_params"])
    assert one.pop("seed") == 0
    assert many.pop("seeds") == [0, 1, 2, 3, 4]
    assert one == many
    # A seed set costs five fits a draw, so it buys fewer draws.
    assert averaged["hpo_trials"] < single["hpo_trials"]
    assert averaged["hpo_space"] == single["hpo_space"]


def test_the_p1_cell_differs_from_its_baseline_in_two_knobs_only():
    """Rows every minute, judged every thirty; everything else held.

    P1's grid is only meaningful if a cell moves the row spacing and
    nothing else. This is the worked example and the template for the
    other twenty-three cells (ADR-0065).
    """
    base = _raw("run-multi3-h01-ridge.json")
    cell = _raw("run-p1-s01-h01-ridge.json")
    assert cell["pipeline"]["universe"]["params"]["overrides"] == {
        "period_ms": 60000,
    }
    assert cell["pipeline"]["scan"]["params"]["score_period_ms"] == 1800000
    # The lattice is a whole multiple of the spacing, or no row lands.
    assert 1800000 % 60000 == 0
    # Same cohort file, same folds, same label, same estimator.
    assert (
        cell["pipeline"]["universe"]["params"]["path"]
        == base["pipeline"]["universe"]["params"]["path"]
    )
    assert cell["walkforward"] == base["walkforward"]
    for knob in (
        "estimator", "estimator_params", "label_scale", "label_residual",
        "lead_start", "lead_step", "lead_stop",
    ):
        assert (
            cell["pipeline"]["scan"]["params"][knob]
            == base["pipeline"]["scan"]["params"][knob]
        ), knob
