"""StrikeRegionScores (ADR-0270 execution path): strike-level assessment of saved CDFExperiment fits.

Synthetic only. The experiment is the REAL ``CDFExperiment`` run on a tiny synthetic panel (CPU, two
epochs); every expected number is recomputed here from the experiment's own stored shards, the panel and
the archive, never read back from the node's output. No market row and no fitted result of a real study
is read.
"""

import copy
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr
from scipy.stats import norm

pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("sklearn")

from dskit.pipeline.document import load_document
from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
from dskit.pipeline.libs.predictive_cdf import DecisionRegionScores, MixtureCurve, TorchCDF
from dskit.pipeline.node import NodeContext
from dskit.pipeline.planner import plan
from dskit.pipeline.stats import cluster_bootstrap_t

from index_options.datafiles import archive_relpath
from index_options.strike_region_scores import (
    NO_LEARNING_BEST_EPOCH, StrikeRegionScores, _CellTable, _ClusteredMean, _Experiment, _LearningCurves)

CTX = NodeContext(name="t", asof="2001-12-31", run_dir="/nonexistent")
CONFIGS = Path(__file__).resolve().parents[1] / "configs"
TICKERS = ("AAA", "BBB")
SPOTS = {"AAA": 100.0, "BBB": 50.0}
HORIZONS = (1, 2, 3)
PROBS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z_GRID = [-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3]
FIT_DATES = [str(d.date()) for d in pd.date_range("2001-01-01", periods=25)]
VAL_DATES = [str(d.date()) for d in pd.date_range("2001-02-01", periods=5)]
LISTED_Z = (-1.6, -0.7, 0.4, 1.3)
SAMPLE = 70
REGIONS = len(Z_GRID) + 1
#: Declared once so the "no listed strikes" row is findable: BBB's first validation origin, one-day expiry.
SPARSE = ("BBB", VAL_DATES[0], 1)
#: Validation rows whose outcome sits exactly on a grid point (and one exactly at zero); the scale is a power of
#: two, so ``terminal_return / reference_scale`` returns the grid point bit for bit.
EDGE = {("AAA", VAL_DATES[1], 1): 0.5, ("BBB", VAL_DATES[1], 1): 0.0}
EDGE_SCALE = 0.0625


def zlabel(z):
    """Restate the column label of a grid point on purpose (a suite does not read it from its subject)."""
    return ("m" if z < 0 else "p") + format(abs(z), "g")


def _quotes(symbol, date, expiry, spot, scale, sparse):
    """Archive rows of one identity: four valid strikes, a one-sided and two dead ones."""
    rows = []
    for z, call, put in ((-2.2, (0., 0.), (0., 0.)), (-1.6, (1., 1.2), (1., 1.2)),
                         (-0.7, (0., 0.), (1., 1.2)), (0.4, (1., 1.2), (1., 1.2)),
                         (1.3, (1., 1.2), (1., 1.2)), (1.9, (1.5, 1.2), (1.5, 1.2))):
        if sparse and z != 0.4:
            call = put = (0., 0.)
        strike = spot * math.exp(z * scale)
        for kind, (bid, ask) in (("call", call), ("put", put)):
            rows.append({"symbol": symbol, "date": date, "expiration": expiry, "strike": strike,
                         "type": kind, "bid": bid, "ask": ask})
    return rows


class World:
    """Everything the tests share: the experiment document, its output, the panel and the archive."""


CONTEXT_Z = [-1.6, -0.7, 0.4, 1.3]


def _context(row):
    """The JSON-text decision context a panel_context source reads: four strikes, two wings."""
    return json.dumps({"identity": [row.symbol, row.quote_date, row.expiry], "thresholds": CONTEXT_Z,
                       "weights": [.25] * 4, "intervals": [[-1.6, -0.7], [0.4, 1.3]], "status": "eligible"})


def _build_world(tmp, with_context=False, training=None):
    cfg = json.loads((CONFIGS / "advanced-cdf-zoo.json").read_text())
    cfg["source_hashes"], cfg["dependencies"] = {}, {}
    cfg["tickers"] = list(TICKERS)
    cfg["arms"] = {"base": cfg["arms"]["base"]}
    cfg["arms"]["base"].update(min_fit=2, min_cal=1)
    cfg["arms"]["starved"] = {**copy.deepcopy(cfg["arms"]["base"]), "min_fit": 10 ** 6}
    live = cfg["candidates"][0]
    live["adapters"]["pooled"] = live["adapters"]["unpooled"][-1:]
    cfg["candidates"] = [live, {**copy.deepcopy(live), "name": "Skippy",
                                "skip_reason": "synthetic library incompatibility"}]
    cfg["training"].update(epochs=2, patience=None)
    cfg["training"].update(training or {})
    cfg["capacity"]["pooled"].update(device="cpu", per_row=1000, head_per_row=1000)
    cfg["date_upper_exclusive"] = cfg["end_upper_exclusive"] = "2002-01-01"
    folds = tmp / "folds.jsonl"
    folds.write_text(json.dumps({"fold": 1, "role": "scored", "train_start": "2001-01-01",
                                 "train_end": "2001-01-25", "val_start": "2001-02-01",
                                 "val_end": "2001-02-05"}) + "\n")
    cfg["study"]["fold_table"].update(path=str(folds), sha256=AtomicFitStore.file_hash(folds),
                                      holdout_start="2002-01-01", cal_n=5)
    cfg["output"], cfg["panel"] = str(tmp / "out"), str(tmp / "panel.parquet")
    rng = np.random.default_rng(100)
    rows, quotes = [], []
    for ticker in TICKERS:
        for date in FIT_DATES + VAL_DATES:
            for dte in HORIZONS:
                row = {name: float(rng.normal()) for name in cfg["arms"]["base"]["features"]}
                expiry = str(dt.date.fromisoformat(date) + dt.timedelta(days=dte))
                edge = EDGE.get((ticker, date, dte))
                scale = EDGE_SCALE if edge is not None else float(0.02 + 0.02 * rng.random())
                spot = SPOTS[ticker] * (1 + 0.01 * float(rng.normal()))
                eligible = bool(rng.random() > 0.25)
                q = np.sort(norm.ppf(PROBS) + 0.1 * rng.normal(size=len(PROBS)))
                row.update(symbol=ticker, quote_date=date, expiry=expiry, settlement_date=expiry,
                           actual_calendar_dte=float(dte), reference_scale=scale,
                           terminal_return=float(rng.normal() * scale) if edge is None else edge * scale,
                           spot=spot,
                           rn_proxy_eligible=int(eligible))
                for p, value in zip(PROBS, q):
                    row[f"rn_q_{round(p * 10000):04d}"] = float(value) if eligible else np.nan
                rows.append(row)
                quotes += _quotes(ticker, date, expiry, spot, scale, (ticker, date, dte) == SPARSE)
    panel = pd.DataFrame(rows)
    if with_context:
        panel["decision_context"] = [_context(r) for r in panel.itertuples()]
    panel.to_parquet(cfg["panel"])
    cfg["panel_sha256"] = AtomicFitStore.file_hash(cfg["panel"])
    archive = tmp / "archive"
    for ticker, part in pd.DataFrame(quotes).groupby("symbol"):
        path = archive / archive_relpath(ticker, 2001)
        path.parent.mkdir(parents=True)
        part.to_parquet(path, index=False)
    document = tmp / "experiment.json"
    document.write_text(json.dumps(cfg))
    CDFExperiment(cfg).run("pooled")
    w = World()
    w.tmp, w.cfg, w.document, w.panel_path, w.archive = tmp, cfg, document, Path(cfg["panel"]), archive
    w.panel = panel
    w.root = Path(cfg["output"]) / "pooled" / "base" / "PatchTST" / "1" / "pooled"
    w.counter = 0
    return w


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return _build_world(tmp_path_factory.mktemp("strike-region-scores"))


@pytest.fixture(scope="module")
def context_world(tmp_path_factory):
    return _build_world(tmp_path_factory.mktemp("strike-region-context"), with_context=True)


def make_params(world, **over):
    world.counter += 1
    params = {
        "experiment_document": str(world.document), "panel": str(world.panel_path),
        "output": str(world.tmp / f"scores-{world.counter}"), "z_grid": Z_GRID,
        "dte_slices": [[1, 1], [2, 3]],
        "sub_periods": {"early": ["2001-01-01", "2001-01-14"], "late": ["2001-01-15", "2001-02-05"]},
        "ticker_groups": {"AAA": "liquid", "BBB": "thin"},
        "market": {"probabilities": PROBS, "prefix": "rn_q_", "eligible_field": "rn_proxy_eligible",
                   "units": "z", "tail_width": 4.0},
        "listed_strikes": {"source": "archive", "archive_root": str(world.archive),
                           "data_root": None, "min_valid_strikes": 2},
        "fit_band_sample": SAMPLE, "end_before": "2002-01-01", "chunk_rows": 16,
        "stored_tolerance": 1e-4}
    params.update(over)
    return params


def run_node(world, **over):
    params = make_params(world, **over)
    return StrikeRegionScores("scores", params).run(CTX, {}), params


class Result:
    """The default run's outputs, read once."""


@pytest.fixture(scope="module")
def result(world):
    out, params = run_node(world)
    root = Path(params["output"])
    r = Result()
    r.out, r.params, r.root = out, params, root
    r.rows = pd.read_parquet(root / "rows.parquet")
    r.cells = pd.read_parquet(root / "cells.parquet")
    r.curves = json.loads((root / "curves.json").read_text())
    r.summary = json.loads((root / "summary.json").read_text())
    return r


@pytest.fixture(scope="module")
def stored(world):
    frames = [pd.read_parquet(world.root / "scores" / t / "scores.parquet") for t in TICKERS]
    return pd.concat(frames, ignore_index=True)


def val_rows(result):
    return result.rows[result.rows.band == "val"].reset_index(drop=True)


KEY = ["symbol", "quote_date", "expiry"]


# -- rows: bands, Brier, masses, market, baseline, decision ---------------------------------------------

def test_rows_cover_the_fit_sample_and_every_validation_row(result, stored):
    rows = result.rows
    assert set(rows.band) == {"fit", "val"}
    val = rows[rows.band == "val"]
    assert len(val) == len(stored) and set(map(tuple, val[KEY].values)) == set(map(tuple, stored[KEY].values))
    fit = rows[rows.band == "fit"]
    assert 0 < len(fit) <= SAMPLE
    assert set(fit.candidate) == {"PatchTST"} and set(fit.arm) == {"base"} and set(fit.fold) == {1}
    assert (fit.quote_date <= "2001-01-25").all() and (val.quote_date >= "2001-02-01").all()


def test_fit_sample_is_deterministic_and_unsampled_when_unbounded(world, result):
    again, params = run_node(world)
    assert pd.read_parquet(Path(params["output"]) / "rows.parquet")[KEY + ["band"]].equals(
        result.rows[KEY + ["band"]])
    full, params = run_node(world, fit_band_sample=None)
    fit_all = pd.read_parquet(Path(params["output"]) / "rows.parquet").query("band == 'fit'")
    assert len(fit_all) == result.summary["scored_cells"][0]["fit_rows_total"] > len(result.rows.query("band == 'fit'"))


def test_threshold_brier_at_every_grid_point_matches_a_hand_computation(world, result, stored):
    val = val_rows(result).merge(stored, on=KEY, suffixes=("", "_stored")).merge(
        world.panel[KEY + ["terminal_return"]], on=KEY)
    row = val.iloc[5]
    n = sum(1 for c in val.columns if c.startswith("weights") and c[7:].isdigit())
    w, m, s = (np.array([row[f"{name}{i}"] for i in range(n)]) for name in ("weights", "means", "scales"))
    y = row.terminal_return / row.reference_scale
    for z in Z_GRID:
        cdf = float((w * ndtr((z - m) / s)).sum())
        assert row[f"model_brier_z{zlabel(z)}"] == pytest.approx((cdf - float(y <= z)) ** 2, abs=1e-9)
    assert row.y == pytest.approx(y)


def test_region_masses_sum_to_one_and_the_realized_indicator_is_one_hot(result):
    rows = result.rows
    assert len(Z_GRID) + 1 == REGIONS == 14
    one_hot = rows[[f"realized_r{r:02d}" for r in range(REGIONS)]].to_numpy()
    assert (one_hot.sum(1) == 1).all() and set(np.unique(one_hot)) <= {0, 1}
    assert (one_hot.argmax(1) == np.searchsorted(Z_GRID, rows.y.to_numpy(), side="left")).all()
    assert (rows.realized_region.to_numpy() == one_hot.argmax(1)).all()
    for who in ("model", "baseline"):
        mass = rows[[f"{who}_mass_r{r:02d}" for r in range(REGIONS)]].to_numpy()
        assert np.allclose(mass.sum(1), 1) and (mass >= -1e-12).all()
    market = rows[rows.market_eligible][[f"market_mass_r{r:02d}" for r in range(REGIONS)]].to_numpy()
    assert len(market) and np.allclose(market.sum(1), 1)


def test_market_curve_is_the_monotone_piecewise_linear_quantile_curve(world, result):
    rows = result.rows.merge(world.panel[KEY + ["rn_proxy_eligible"] + [f"rn_q_{round(p * 1e4):04d}" for p in PROBS]],
                             on=KEY)
    assert (rows.market_eligible == (rows.rn_proxy_eligible == 1)).all() and (~rows.market_eligible).any()
    dead = rows[~rows.market_eligible]
    assert dead[[c for c in rows if c.startswith("market_") and c != "market_eligible"]].isna().all().all()
    live = rows[rows.market_eligible].iloc[:12]
    for _, row in live.iterrows():
        q = np.array([row[f"rn_q_{round(p * 1e4):04d}"] for p in PROBS])
        xs, ps = np.r_[q[0] - 4.0, q, q[-1] + 4.0], np.r_[0.0, PROBS, 1.0]
        cdf = np.interp(Z_GRID, xs, ps, left=0, right=1)
        expected = np.diff(np.r_[0.0, cdf, 1.0])
        got = np.array([row[f"market_mass_r{r:02d}"] for r in range(REGIONS)])
        assert np.allclose(got, expected, atol=1e-9) and (np.diff(np.cumsum(got)) >= -1e-12).all()
        assert row.market_pit == pytest.approx(float(np.interp(row.y, xs, ps, left=0, right=1)))
        assert row.market_sd_z > 0 and row.market_q95_q05 > 0


def test_log_return_units_divide_by_each_rows_own_reference_scale(world, result):
    out, params = run_node(world, market={**make_params(world)["market"], "units": "log_return"})
    rows = pd.read_parquet(Path(params["output"]) / "rows.parquet")
    names = [f"rn_q_{round(p * 1e4):04d}" for p in PROBS]
    both = rows[rows.market_eligible].merge(world.panel[KEY + names], on=KEY)
    assert len(both) and both.reference_scale.nunique() > 20
    spread = (both[names[-1]] - both[names[0]]).to_numpy()
    assert np.allclose(both.market_q95_q05, spread / both.reference_scale.to_numpy())
    plain = result.rows[result.rows.market_eligible].merge(world.panel[KEY + names], on=KEY)
    assert np.allclose(plain.market_q95_q05, (plain[names[-1]] - plain[names[0]]).to_numpy())


def test_baseline_and_model_crps_equal_the_experiments_own_validation_scores(result, stored):
    val = val_rows(result).merge(stored, on=KEY, suffixes=("", "_stored"))
    for who in ("model", "baseline"):
        for metric in ("crps", "tail_crps", "weighted_crps"):
            assert np.allclose(val[f"{who}_{metric}"], val[f"{who}_{metric}_stored"], rtol=1e-5, atol=1e-7)


def test_decision_strike_brier_equals_decision_region_scores_on_the_listed_strikes(world, result, stored):
    val = val_rows(result).merge(stored, on=KEY, suffixes=("", "_stored"))
    quotes = pd.concat([pd.read_parquet(world.archive / archive_relpath(t, 2001)) for t in TICKERS])
    checked = 0
    for _, row in val.iterrows():
        mine = quotes[(quotes.symbol == row.symbol) & (quotes.date == row.quote_date)
                      & (quotes.expiration == row.expiry)]
        good = mine[(mine.bid > 0) & (mine.ask >= mine.bid)]
        z = np.sort(np.unique(np.log(good.strike.to_numpy() / row.spot) / row.reference_scale))
        n = sum(1 for c in val.columns if c.startswith("weights") and c[7:].isdigit())
        curve = MixtureCurve(*[[[row[f"{name}{i}"] for i in range(n)]] for name in ("weights", "means", "scales")])
        if (row.symbol, row.quote_date, int(row.actual_calendar_dte)) == SPARSE:
            assert row.listed_strikes_n == 0 and math.isnan(row.model_decision_strike_brier)
            assert math.isnan(row.baseline_decision_wing_twcrps)
            continue
        assert len(z) >= 2 and row.listed_strikes_n == len(z)
        context = [{"identity": ("a", "b", "c"), "thresholds": z.tolist(), "weights": [1 / len(z)] * len(z),
                    "intervals": []}]
        expected = DecisionRegionScores(context).score(curve, np.array([row.y]))["decision_strike_brier"][0]
        assert row.model_decision_strike_brier == pytest.approx(expected, abs=1e-9)
        checked += 1
    assert checked >= 20


def test_decision_wing_twcrps_uses_the_adjacent_pairs_that_do_not_straddle_zero(world, result, stored):
    val = val_rows(result).merge(stored, on=KEY, suffixes=("", "_stored"))
    row = val[(val.listed_strikes_n >= 2)].iloc[0]
    z = np.array(LISTED_Z)
    spot_z = z.copy()          # built as spot * exp(z * the row's own scale)
    pairs = [[float(a), float(b)] for a, b in zip(spot_z[:-1], spot_z[1:]) if not (a < 0 < b)]
    n = sum(1 for c in val.columns if c.startswith("weights") and c[7:].isdigit())
    curve = MixtureCurve(*[[[row[f"{name}{i}"] for i in range(n)]] for name in ("weights", "means", "scales")])
    context = [{"identity": ("a", "b", "c"), "thresholds": spot_z.tolist(),
                "weights": [1 / len(z)] * len(z), "intervals": pairs}]
    expected = DecisionRegionScores(context).wing_score(curve, np.array([row.y]))["decision_wing_twcrps"][0]
    assert row.model_decision_wing_twcrps == pytest.approx(expected, rel=1e-6)


def test_sharpness_columns_are_the_curve_moments_and_quantile_spread(result, stored):
    val = val_rows(result).merge(stored, on=KEY, suffixes=("", "_stored"))
    row = val.iloc[2]
    n = sum(1 for c in val.columns if c.startswith("weights") and c[7:].isdigit())
    curve = MixtureCurve(*[[[row[f"{name}{i}"] for i in range(n)]] for name in ("weights", "means", "scales")])
    assert row.model_sd_z == pytest.approx(curve.moments()[1][0])
    q = curve.quantile([.05, .95])[0]
    assert row.model_q95_q05 == pytest.approx(q[1] - q[0], rel=1e-6)
    assert row.model_pit == pytest.approx(float(curve.cdf([row.y])[0, 0]), abs=1e-9)


# -- cells ------------------------------------------------------------------------------------------------

def _cell(cells, **where):
    mask = np.ones(len(cells), dtype=bool)
    for key, value in where.items():
        mask &= (cells[key] == value).to_numpy()
    return cells[mask]


def test_cell_support_counts_match_the_rows(result):
    rows, cells = result.rows, result.cells
    for band in ("fit", "val"):
        sub = rows[rows.band == band]
        for label, lo, hi in (("1-1", 1, 1), ("2-3", 2, 3)):
            part = sub[(sub.actual_calendar_dte >= lo) & (sub.actual_calendar_dte <= hi)]
            region = _cell(cells, band=band, window="full", dte_slice=label, ticker_group="all",
                           metric="region_brier")
            assert len(region) == REGIONS
            assert (region.rows == len(part)).all()
            assert (region.dates == part.quote_date.nunique()).all()
            assert (region.expiries == part.expiry.nunique()).all()
            assert (region.symbols == part.symbol.nunique()).all()
            assert region.events.sum() == len(part)
            for _, cell in region.iterrows():
                r = int(cell.region)
                assert cell.realized_freq_model == pytest.approx(part[f"realized_r{r:02d}"].mean())
                assert cell.mass_model == pytest.approx(part[f"model_mass_r{r:02d}"].mean())
                brier = ((part[f"model_mass_r{r:02d}"] - part[f"realized_r{r:02d}"]) ** 2).mean()
                assert cell.score_model == pytest.approx(brier)


def test_market_pairs_use_only_market_eligible_rows(result):
    rows = result.rows
    sub = rows[(rows.band == "val") & rows.market_eligible]
    cell = _cell(result.cells, band="val", window="full", dte_slice="2-3", ticker_group="all",
                 metric="region_brier", region=6).iloc[0]
    part = sub[(sub.actual_calendar_dte >= 2) & (sub.actual_calendar_dte <= 3)]
    assert cell.score_market_n == len(part) == cell.diff_model_market_n
    diff = ((part.model_mass_r06 - part.realized_r06) ** 2 - (part.market_mass_r06 - part.realized_r06) ** 2)
    assert cell.diff_model_market == pytest.approx(diff.mean())
    assert cell.diff_model_baseline_n >= cell.diff_model_market_n


def test_windows_groups_and_global_metrics_are_present(result):
    cells = result.cells
    assert {"full", "early", "late", "y2001"} <= set(cells.window)
    assert set(cells.window_kind) == {"full", "sub_period", "year"}
    assert set(cells.ticker_group) == {"all", "liquid", "thin"}
    assert {"region_brier", "crps", "tail_crps", "weighted_crps", "decision_strike_brier",
            "decision_wing_twcrps"} <= set(cells.metric)
    early = _cell(cells, band="val", window="early")           # no validation origin that early: empty cells
    assert len(early) and (early.rows == 0).all() and early.score_model.isna().all()
    liquid = _cell(cells, band="val", window="full", ticker_group="liquid", metric="crps", dte_slice="1-1")
    assert (liquid.symbols == 1).all() and liquid.score_model_se_symbol.isna().all()
    assert liquid.score_model_se_quote_date.notna().all() and liquid.score_model_se.isna().all()   # never a fallback


def test_cell_errors_report_the_wider_of_the_two_clusterings(result):
    cells = result.cells
    live = cells[(cells.band == "val") & (cells.window == "full") & (cells.ticker_group == "all")
                 & (cells.metric == "crps")].dropna(subset=["score_model_se"])
    assert len(live) and (live.score_model_se > 0).all()
    wider = np.fmax(live.score_model_se_symbol, live.score_model_se_quote_date)
    assert np.allclose(live.score_model_se, wider)


# -- clustered standard errors ---------------------------------------------------------------------------

def test_clustered_se_is_larger_than_iid_for_correlated_errors_and_matches_the_toolkit():
    rng = np.random.default_rng(3)
    clusters = np.repeat(np.arange(12), 25)
    values = rng.normal(size=12)[clusters] * 2 + rng.normal(size=300) * .3
    mean, n, se = _ClusteredMean.estimate(values[:, None], clusters)
    iid = values.std(ddof=1) / math.sqrt(len(values))
    assert se[0] > 3 * iid > 0 and n[0] == 300 and mean[0] == pytest.approx(values.mean())
    ref = cluster_bootstrap_t({str(c): values[clusters == c].tolist() for c in range(12)}, 1, 0)
    assert se[0] == pytest.approx(ref["se"], rel=1e-9)


def test_clustered_se_skips_missing_values_and_needs_two_clusters():
    codes = np.array([0, 0, 1, 1, 2])
    values = np.array([[1.0, np.nan], [3.0, np.nan], [2.0, 5.0], [6.0, np.nan], [4.0, np.nan]])
    mean, n, se = _ClusteredMean.estimate(values, codes)
    assert n.tolist() == [5, 1] and mean[0] == pytest.approx(3.2) and math.isnan(se[1])
    assert math.isnan(_ClusteredMean.estimate(values[:2, :1], np.array([0, 0]))[2][0])


# -- curves -----------------------------------------------------------------------------------------------

def test_curves_carry_per_epoch_losses_and_unevaluable_signatures_without_a_monitor(world, result):
    assert [c["cell"] for c in result.curves["cells"]] == ["base/PatchTST/1"]
    cell = result.curves["cells"][0]
    metadata = json.loads((world.root / "fit" / "metadata.json").read_text())
    summary = json.loads((world.root / "completed" / "summary.json").read_text())
    assert cell["parameters"] == summary["parameters"] and cell["elapsed_seconds"] == metadata["elapsed_seconds"]
    assert cell["total_epochs"] == summary["total_epochs"] == [2]
    seed = cell["seeds"][0]
    assert seed["seed"] == 11 and len(seed["train_loss"]) == 2 and seed["monitor_loss"] == []
    assert seed["best_epoch"] is None and seed["epoch_zero_monitor_loss"] is not None
    assert cell["signatures"] == {"no_learning": None, "overfit": None}
    assert "best_epoch <= 1" in result.curves["rules"]["no_learning"]
    assert NO_LEARNING_BEST_EPOCH == 1


def _state(train, monitor, best, zero, seeds=(7,)):
    return {"settings": {"seeds": list(seeds)}, "losses": [train], "monitor_losses": [monitor],
            "best_epochs": [best], "diagnostics": [{"epoch_zero_monitor_loss": zero,
                                                    "parameter_delta_l2": 1.0}]}


def test_no_learning_fires_when_the_best_epoch_is_the_first():
    s = _LearningCurves(_state([3, 2.9, 2.8], [1.0, 1.1, 1.2], 1, 1.5)).signatures()
    assert s["no_learning"] is True and s["overfit"] is True
    assert _LearningCurves(_state([3, 2, 1], [1.0, .9, .8], 3, 1.5)).signatures() == {
        "no_learning": False, "overfit": False}


def test_no_learning_also_fires_when_the_best_monitor_is_not_below_epoch_zero():
    s = _LearningCurves(_state([3, 2, 2.1], [1.6, 1.55, 1.7], 2, 1.5)).signatures()
    assert s["no_learning"] is True and s["overfit"] is False


def test_overfit_needs_train_improving_while_the_monitor_worsens_after_the_best():
    worse = _LearningCurves(_state([3, 2, 1.5, 1.0], [2.0, 1.0, 1.2, 1.4], 2, 3.0)).signatures()
    flat_train = _LearningCurves(_state([3, 2, 2.5, 2.6], [2.0, 1.0, 1.2, 1.4], 2, 3.0)).signatures()
    assert worse["overfit"] is True and flat_train["overfit"] is False


def test_signatures_on_a_real_checkpoint_state(tmp_path):
    rng = np.random.default_rng(5)
    x, y = rng.normal(size=(60, 3)), rng.normal(size=60) * .5
    model = TorchCDF(encoder={"kind": "mlp"}, losses=[{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 1.0}], hidden=[4],
                     epochs=40, patience=2, batch_size=30, seeds=[3, 4], learning_rate=.05, device="cpu",
                     training_telemetry=True)
    def context(n, prefix):
        return [{"identity": [prefix + str(i)], "thresholds": [-2.5, -.5, .5, 2.5], "weights": [.25] * 4,
                 "intervals": [[-2.5, -.5], [.5, 2.5]]} for i in range(n)]
    model.fit_decision_context(context(40, "f"), context(20, "c"))
    model.fit(x[:40], y[:40], x[40:], y[40:])
    model.save_checkpoint(tmp_path / "model")
    state = json.loads((tmp_path / "model" / "state.json").read_text())
    record = _LearningCurves(state).record()
    assert [s["best_epoch"] for s in record["seeds"]] == state["best_epochs"]
    assert all(len(s["monitor_loss"]) == len(s["train_loss"]) for s in record["seeds"])
    expected = any(b <= 1 for b in state["best_epochs"])
    expected_zero = any(m[b - 1] >= d["epoch_zero_monitor_loss"] for m, b, d in
                        zip(state["monitor_losses"], state["best_epochs"], state["diagnostics"]))
    assert record["signatures"]["no_learning"] is (expected or expected_zero)


# -- summary, publication and refusals --------------------------------------------------------------------

def test_summary_names_scored_skipped_and_hashes(world, result):
    summary = result.summary
    assert [c["cell"] for c in summary["scored_cells"]] == ["base/PatchTST/1"]
    skipped = summary["skipped_cells"]
    assert [c["cell"] for c in skipped] == ["base/Skippy/1"]
    assert "synthetic library incompatibility" in skipped[0]["reason"]["reason"]
    hashes = summary["hashes"]
    assert [c["cell"] for c in summary["not_eligible_cells"]] == ["starved/PatchTST/1", "starved/Skippy/1"]
    assert summary["not_eligible_cells"][0]["admission"]["AAA"]["eligible"] is False
    assert summary["forecasters"] == ["model", "baseline", "pooled", "market"]
    assert hashes["experiment_document_sha256"] == AtomicFitStore.file_hash(world.document)
    assert hashes["panel_sha256"] == AtomicFitStore.file_hash(world.panel_path) == world.cfg["panel_sha256"]
    state = json.loads((world.root / "fit" / "model" / "state.json").read_text())
    assert hashes["checkpoints"]["base/PatchTST/1"]["weights_sha256"] == state["weights_sha256"]
    for name in ("rows.parquet", "cells.parquet", "curves.json"):
        assert hashes["outputs"][name] == AtomicFitStore.file_hash(result.root / name)
    assert summary["refusals"] == [] and summary["scored_cells"][0]["rows_without_listed_strikes"]["val"] == 1
    assert result.out["summary"]["output"] == str(result.root)
    assert sorted(p.name for p in result.root.iterdir()) == [
        "cells.parquet", "curves.json", "rows.parquet", "summary.json"]


def test_an_unfinished_cell_is_a_named_refusal_not_a_score(world):
    done = world.root / "completed"
    hidden = world.root / "completed.hidden"
    done.rename(hidden)
    try:
        out, params = run_node(world)
    finally:
        hidden.rename(done)
    summary = json.loads((Path(params["output"]) / "summary.json").read_text())
    assert out["summary"]["refusals"] == 1 and summary["scored_cells"] == []
    assert summary["refusals"][0]["cell"] == "base/PatchTST/1" and "incomplete" in summary["refusals"][0]["reason"]
    assert pd.read_parquet(Path(params["output"]) / "rows.parquet").empty


def test_existing_output_is_refused(world):
    params = make_params(world)
    Path(params["output"]).mkdir()
    with pytest.raises(ValueError, match="exists"):
        StrikeRegionScores("scores", params).run(CTX, {})


def test_a_panel_without_spot_is_refused_before_any_cell_is_read(world):
    panel = world.panel.drop(columns=["spot"])
    path = world.tmp / "panel-nospot.parquet"
    panel.to_parquet(path)
    cfg = {**world.cfg, "panel": str(path), "panel_sha256": AtomicFitStore.file_hash(path)}
    document = world.tmp / "experiment-nospot.json"
    document.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="spot"):
        run_node(world, experiment_document=str(document), panel=str(path))


def test_a_row_at_or_after_end_before_is_refused(world):
    with pytest.raises(ValueError, match="end_before"):
        run_node(world, end_before="2001-02-04")


def test_an_end_before_earlier_than_the_experiments_bounds_refuses_before_the_panel_is_read(world, monkeypatch):
    def forbidden(self):
        raise AssertionError("the panel was read")
    monkeypatch.setattr(CDFExperiment, "load_panel", forbidden)
    with pytest.raises(ValueError, match="end_before"):
        run_node(world, end_before="2001-03-01")


def test_nothing_in_the_panel_read_reaches_end_before(world, result):
    assert result.rows.quote_date.max() < result.params["end_before"]
    assert result.rows.settlement_date.max() < result.params["end_before"]


def test_end_before_past_the_documents_holdout_is_refused(world):
    with pytest.raises(ValueError, match="holdout"):
        run_node(world, end_before="2002-01-02")


def test_a_mismatched_panel_hash_is_an_integrity_error(world):
    cfg = {**world.cfg, "panel_sha256": "0" * 64}
    document = world.tmp / "experiment-badsha.json"
    document.write_text(json.dumps(cfg))
    with pytest.raises(IntegrityError, match="panel hash"):
        run_node(world, experiment_document=str(document))


def test_a_panel_path_other_than_the_documents_is_refused(world):
    other = world.tmp / "elsewhere.parquet"
    other.write_bytes(world.panel_path.read_bytes())
    with pytest.raises(ValueError, match="panel"):
        run_node(world, panel=str(other))


def test_a_corrupted_checkpoint_is_an_integrity_error(world):
    weights = world.root / "fit" / "model" / "weights.pt"
    original = weights.read_bytes()
    try:
        weights.write_bytes(b"corruption")
        with pytest.raises(IntegrityError):
            run_node(world)
    finally:
        weights.write_bytes(original)


def test_a_rescore_that_disagrees_with_the_stored_shards_is_an_integrity_error(world, monkeypatch):
    import index_options.strike_region_scores as module
    from dskit.pipeline.libs.predictive_cdf import GridCurve, HorizonEmpiricalCDF

    class Drifted(HorizonEmpiricalCDF):
        def curve(self, x):
            curve = super().curve(x)
            return GridCurve(curve.values * 1.5, curve.probabilities)

    monkeypatch.setattr(module, "HorizonEmpiricalCDF", Drifted)
    with pytest.raises(IntegrityError, match="stored shards"):
        run_node(world)
    assert not list(world.tmp.glob(".scores-*.partial"))


@pytest.mark.parametrize("over, text", [
    ({"unknown": 1}, "unknown"),
    ({"z_grid": [0, 0]}, "z_grid"),
    ({"dte_slices": [[2, 1]]}, "dte_slices"),
    ({"dte_slices": [[1, 5], [4, 9]]}, "overlap"),
    ({"sub_periods": {"a": ["2001-02-01", "2001-01-01"]}}, "sub_periods"),
    ({"ticker_groups": {"AAA": "all"}}, "ticker_groups"),
    ({"fit_band_sample": 0}, "fit_band_sample"),
    ({"end_before": "March"}, "end_before"),
    ({"chunk_rows": 0}, "chunk_rows"),
    ({"stored_tolerance": 0}, "stored_tolerance"),
    ({"market": {"probabilities": [0.5], "prefix": "rn_q_", "eligible_field": "e", "units": "z",
                 "tail_width": 4.0}}, "market"),
    ({"market": {"probabilities": PROBS, "prefix": "rn_q_", "eligible_field": "e", "units": "bps",
                 "tail_width": 4.0}}, "units"),
    ({"listed_strikes": {"source": "magic"}}, "listed_strikes"),
    ({"listed_strikes": {"source": "archive", "archive_root": "x", "data_root": None}}, "min_valid_strikes"),
    ({"listed_strikes": {"source": "panel_context"}}, "context_field"),
])
def test_validate_params_refuses_each_bad_knob(world, over, text):
    problems = StrikeRegionScores.validate_params({**make_params(world), **over})
    assert any(text in p for p in problems), problems


def test_validate_params_accepts_the_default_world_and_requires_every_knob(world):
    assert StrikeRegionScores.validate_params(make_params(world)) == []
    for key in make_params(world):
        if key == "ticker_groups":
            continue
        params = make_params(world)
        del params[key]
        assert StrikeRegionScores.validate_params(params), key


def test_the_market_column_prefix_agrees_with_the_chain_builder():
    from index_options.cdf_study import RawChainFeatureBuilder
    assert all(f"rn_q_{round(p * 10000):04d}" == RawChainFeatureBuilder._qname(p) for p in PROBS + [.025, .975])


# -- panel-context listed strikes -------------------------------------------------------------------------

def test_panel_context_strikes_are_read_from_a_json_text_column(context_world):
    w = context_world
    out, params = run_node(w, listed_strikes={"source": "panel_context", "context_field": "decision_context"})
    rows = pd.read_parquet(Path(params["output"]) / "rows.parquet")
    assert (rows.listed_strikes_n == 4).all() and (rows.listed_wing_intervals_n == 2).all()
    assert rows.model_decision_strike_brier.notna().all() and rows.baseline_decision_wing_twcrps.notna().all()
    stored = pd.concat([pd.read_parquet(w.root / "scores" / t / "scores.parquet") for t in TICKERS])
    val = rows[rows.band == "val"].merge(stored, on=KEY, suffixes=("", "_stored"))
    row = val.iloc[7]
    n = sum(1 for c in val.columns if c.startswith("weights") and c[7:].isdigit())
    curve = MixtureCurve(*[[[row[f"{name}{i}"] for i in range(n)]] for name in ("weights", "means", "scales")])
    context = [{"identity": ("a", "b", "c"), "thresholds": CONTEXT_Z, "weights": [.25] * 4,
                "intervals": [[-1.6, -0.7], [0.4, 1.3]]}]
    scorer = DecisionRegionScores(context)
    y = np.array([row.y])
    assert row.model_decision_strike_brier == pytest.approx(scorer.score(curve, y)["decision_strike_brier"][0], abs=1e-9)
    assert row.model_decision_wing_twcrps == pytest.approx(
        scorer.wing_score(curve, y)["decision_wing_twcrps"][0], rel=1e-6)


def test_a_context_naming_another_row_is_an_integrity_error(context_world):
    from index_options.strike_region_scores import LISTED_SOURCES
    source = LISTED_SOURCES["panel_context"]({"source": "panel_context", "context_field": "decision_context"})
    frame = context_world.panel.head(2).copy()
    frame["decision_context"] = frame["decision_context"].iloc[::-1].tolist()
    with pytest.raises(IntegrityError, match="identity"):
        source.contexts(frame)


# -- the shipped document ---------------------------------------------------------------------------------

def test_the_shipped_document_validates_and_plans():
    path = CONFIGS / "run-strike-region-scores.json"
    document = load_document(str(path))
    resolved = plan(document)
    assert resolved is not None
    raw = json.loads(path.read_text())
    assert "ADR-0270" in raw["notes"] and "strike-screen-protocol" in raw["notes"]
    node = next(iter(raw["pipeline"].values()))
    assert node["uses"] == "index_options.strike_region_scores:StrikeRegionScores"
    assert StrikeRegionScores.validate_params(node["params"]) == []
    protocol = json.loads((CONFIGS / "strike-screen-protocol.json").read_text())
    assert node["params"]["z_grid"] == protocol["formulation"]["strike_grid_z"]
    assert node["params"]["dte_slices"] == protocol["cells"]["dte_slices"]
    assert node["params"]["sub_periods"] == protocol["windows"]["validation"]["sub_periods"]
    assert node["params"]["end_before"] == protocol["fold_tables"]["holdout_start"]["screen"]


# -- correction round: reliability, the pooled forecaster, population-consistent frequencies -------------

def test_rows_carry_the_cdf_at_every_grid_point_for_every_forecaster(result):
    rows = result.rows
    for who in ("model", "baseline", "pooled", "market"):
        for z in Z_GRID:
            cdf = rows[f"{who}_cdf_z{zlabel(z)}"]
            brier = rows[f"{who}_brier_z{zlabel(z)}"]
            ok = cdf.notna()
            assert np.allclose(brier[ok], (cdf[ok] - (rows.y[ok] <= z)) ** 2)
        assert np.allclose(rows[f"{who}_mass_r00"].dropna(), rows[f"{who}_cdf_zm3"].dropna())


def test_reliability_cells_give_mean_cdf_and_realized_frequency_per_threshold(result):
    rows = result.rows
    part = rows[(rows.band == "val") & (rows.actual_calendar_dte >= 2) & (rows.actual_calendar_dte <= 3)]
    cells = _cell(result.cells, band="val", window="full", dte_slice="2-3", ticker_group="all", metric="reliability")
    assert sorted(cells.region) == list(range(len(Z_GRID)))
    for _, cell in cells.iterrows():
        z = Z_GRID[int(cell.region)]
        assert cell.threshold == z
        for who in ("model", "baseline", "pooled"):
            assert cell[f"score_{who}"] == pytest.approx(part[f"{who}_cdf_z{zlabel(z)}"].mean())
            assert cell[f"realized_freq_{who}"] == pytest.approx((part.y <= z).mean())
        live = part[part.market_eligible]
        assert cell.score_market == pytest.approx(live[f"market_cdf_z{zlabel(z)}"].mean())
        assert cell.realized_freq_market == pytest.approx((live.y <= z).mean())
        assert cell.realized_freq_market_n == len(live)
    assert (cells.score_model_se > 0).all()
    assert (cells.realized_freq_model_se >= 0).all() and (cells.realized_freq_model_se > 0).any()


def test_the_pooled_forecaster_is_one_horizon_empirical_curve_per_fold_on_the_pooled_fit_band(world, result):
    exp = CDFExperiment(world.cfg)
    frame = exp.load_panel()
    bands, groups, _ = exp._bands(frame, exp.study.plan.calendar(frame), 1, world.cfg["arms"]["base"])
    fit = bands[0]
    knots = world.cfg["study"]["models"]["horizon_empirical"]["params"]["knots"]
    p = np.linspace(0, 1, knots)
    val = val_rows(result).merge(world.panel[KEY], on=KEY)
    checked = 0
    for dte in HORIZONS:
        pool = (fit[fit.actual_calendar_dte == dte].terminal_return / fit[fit.actual_calendar_dte == dte].reference_scale)
        q = np.quantile(pool.to_numpy(), p)
        rows = val[val.actual_calendar_dte == dte]
        expected = np.interp(rows.y, q, p, left=0, right=1)
        assert np.allclose(rows.pooled_pit, expected, atol=1e-9)
        checked += len(rows)
    assert checked == len(val)
    assert result.rows.pooled_crps.notna().all() and (result.rows.pooled_crps != result.rows.baseline_crps).any()


def test_pairs_include_the_pooled_forecaster(result):
    cell = _cell(result.cells, band="val", window="full", dte_slice="2-3", ticker_group="all",
                 metric="region_brier", region=6).iloc[0]
    assert cell.diff_model_pooled_n == cell.rows and cell.diff_market_pooled_n == cell.score_market_n
    rows = result.rows
    part = rows[(rows.band == "val") & (rows.actual_calendar_dte >= 2)]
    diff = ((part.model_mass_r06 - part.realized_r06) ** 2 - (part.pooled_mass_r06 - part.realized_r06) ** 2)
    assert cell.diff_model_pooled == pytest.approx(diff.mean())


def test_realized_frequency_is_taken_on_each_forecasters_own_rows(result):
    rows = result.rows
    part = rows[(rows.band == "val") & (rows.actual_calendar_dte >= 2)]
    cell = _cell(result.cells, band="val", window="full", dte_slice="2-3", ticker_group="all",
                 metric="region_brier", region=6).iloc[0]
    live = part[part.market_eligible]
    assert cell.realized_freq_market == pytest.approx(live.realized_r06.mean()) != part.realized_r06.mean()
    assert cell.realized_freq_model == pytest.approx(part.realized_r06.mean())
    assert cell.mass_market == pytest.approx(live.market_mass_r06.mean())
    assert cell.realized_freq_pooled == pytest.approx(part.realized_r06.mean())


def test_population_columns_name_sampled_fit_bands(world, result):
    fit = result.rows[result.rows.band == "fit"]
    val = result.rows[result.rows.band == "val"]
    total = result.summary["scored_cells"][0]["fit_rows_total"]
    assert (fit.band_sampled == True).all() and (fit.band_rows_population == total).all()   # noqa: E712
    assert (val.band_sampled == False).all() and (val.band_rows_population == len(val)).all()   # noqa: E712
    cells = result.cells
    assert (cells[cells.band == "fit"].band_sampled).all() and not cells[cells.band == "val"].band_sampled.any()
    assert set(cells[cells.band == "fit"].band_rows_population) == {total}


# -- correction round: integrity and eligibility --------------------------------------------------------

def test_a_re_scored_mixture_that_differs_from_the_stored_parameters_is_refused(world, monkeypatch):
    import index_options.strike_region_scores as module
    original = TorchCDF.curve

    def shifted(self, x):
        curve = original(self, x)
        return MixtureCurve(curve.weights, curve.means + 1e-3, curve.scales)

    monkeypatch.setattr(module.TorchCDF, "curve", shifted)
    with pytest.raises(IntegrityError, match="mixture"):
        run_node(world, stored_tolerance=1.0)


def test_an_unexpected_score_shard_is_refused_like_the_queue_does(world):
    extra = world.root / "scores" / "ZZZ"
    extra.mkdir()
    try:
        with pytest.raises(IntegrityError, match="unexpected score shard"):
            run_node(world)
    finally:
        extra.rmdir()


def _market(**over):
    from index_options.strike_region_scores import _MarketCurves
    params = {"probabilities": [0.1, 0.5, 0.9], "prefix": "q_", "eligible_field": "ok", "units": "z",
              "tail_width": 4.0, **over}
    return _MarketCurves(params)


def _frame(flag, quantiles):
    return pd.DataFrame({"ok": flag, "q_1000": [quantiles[0]], "q_5000": [quantiles[1]], "q_9000": [quantiles[2]]})


def test_unordered_market_quantiles_and_nonbinary_flags_are_refused_not_repaired():
    market = _market()
    eligible, curve = market.curve(_frame([1], [-1.0, 0.0, 1.0]), np.array([1.0]))
    assert eligible.tolist() == [True] and curve.cdf([0.0])[0, 0] == pytest.approx(0.5)
    with pytest.raises(ValueError, match="ordered"):
        market.curve(_frame([1], [0.5, 0.0, 1.0]), np.array([1.0]))
    with pytest.raises(ValueError, match="binary"):
        market.curve(_frame([2], [-1.0, 0.0, 1.0]), np.array([1.0]))
    with pytest.raises(ValueError, match="binary"):
        market.curve(_frame([np.nan], [-1.0, 0.0, 1.0]), np.array([1.0]))
    with pytest.raises(ValueError, match="finite"):
        market.curve(_frame([1], [-1.0, np.nan, 1.0]), np.array([1.0]))
    eligible, curve = market.curve(_frame([0], [np.nan] * 3), np.array([1.0]))
    assert eligible.tolist() == [False] and curve is None


# -- correction round 2: clustering date, bounds, archive, groups, checkpoint pin -------------------------

def test_the_reported_error_clusters_on_the_quote_date_not_the_settlement_date():
    rng = np.random.default_rng(11)
    origins, symbols = 240, 40
    origin = np.repeat(np.arange(origins), symbols)
    symbol = np.tile(np.arange(symbols), origins)
    settle = origin + rng.integers(1, 61, size=origin.size)
    values = (rng.normal(size=origins)[origin] + .3 * rng.normal(size=origin.size))[:, None]
    codes = {"symbol": symbol, "quote_date": origin, "settlement": settle}
    out = _CellTable.errors(values, codes)
    assert out["se_quote_date"][0] > 2 * out["se_settlement"][0] > 0      # the shared shock is a quote-date one
    assert out["se"][0] == out["se_quote_date"][0] == max(out["se_symbol"][0], out["se_quote_date"][0])
    one = _CellTable.errors(values, {**codes, "symbol": np.zeros(origin.size, dtype=int)})
    assert np.isnan(one["se_symbol"][0]) and np.isnan(one["se"][0]) and one["se_quote_date"][0] > 0


def test_cells_carry_the_three_clusterings(result):
    cells = result.cells
    live = cells[(cells.band == "val") & (cells.window == "full") & (cells.metric == "crps")
                 & (cells.ticker_group == "all")]
    for name in ("se_symbol", "se_quote_date", "se_settlement", "se"):
        assert f"score_model_{name}" in cells and live[f"score_model_{name}"].notna().all()
    assert not any(c.endswith("_se_date") for c in cells)


def test_one_field_for_date_and_end_takes_the_smaller_bound():
    fields = {"date": "d", "end": "d", "identity": ["s", "d", "e"]}
    config = {"date_upper_exclusive": "2020-02-01", "end_upper_exclusive": "2020-01-15"}
    assert _Experiment.read_bounds(fields, config, "2020-03-01") == {"d": {"end_before": "2020-01-15"}}
    config = {"date_upper_exclusive": "2020-01-15", "end_upper_exclusive": "2020-02-01"}
    assert _Experiment.read_bounds(fields, config, "2020-03-01") == {"d": {"end_before": "2020-01-15"}}
    split = {**fields, "end": "z"}
    assert _Experiment.read_bounds(split, config, "2020-01-20") == {
        "d": {"end_before": "2020-01-15"}, "z": {"end_before": "2020-01-20"}}


def test_archive_rows_outside_the_wanted_identities_are_discarded(world, tmp_path):
    from index_options.strike_region_scores import _ArchiveStrikes
    quotes = pd.read_parquet(world.archive / archive_relpath("AAA", 2001))
    late = quotes.head(2).copy()
    late["date"], late["expiration"] = "2002-03-01", "2002-03-05"
    path = tmp_path / archive_relpath("AAA", 2001)
    path.parent.mkdir(parents=True)
    pd.concat([quotes, late]).to_parquet(path, index=False)
    source = _ArchiveStrikes({"source": "archive", "archive_root": str(tmp_path), "data_root": None,
                              "min_valid_strikes": 2})
    frame = world.panel[world.panel.symbol == "AAA"].head(3).rename(columns={})
    source.contexts(frame)
    assert set(source.strikes) == set(zip(frame.symbol, frame.quote_date, frame.expiry))


def test_an_admitted_symbol_missing_from_a_nonempty_group_mapping_is_refused(world):
    with pytest.raises(ValueError, match="BBB"):
        run_node(world, ticker_groups={"AAA": "liquid"})
    out, params = run_node(world, ticker_groups={})
    cells = pd.read_parquet(Path(params["output"]) / "cells.parquet")
    assert set(cells.ticker_group) == {"all"}


def test_a_completed_summary_naming_another_checkpoint_is_refused(world, monkeypatch):
    original = AtomicFitStore.verify

    def verify(self):
        if self.path.name == "fit":
            return {**original(self), "files": {}}
        if self.path.parent.name == "scores":
            return {}
        return original(self)

    monkeypatch.setattr(AtomicFitStore, "verify", verify)
    with pytest.raises(IntegrityError, match="summary checkpoint"):
        run_node(world)


def test_stored_tail_and_weighted_crps_are_compared_too(world, monkeypatch):
    import index_options.strike_region_scores as module
    original = module.ChronologicalCDFStudy.scores

    def drifted(curve, y, samples, intervals, points, tail_probabilities=None):
        values, q = original(curve, y, samples, intervals, points, tail_probabilities)
        if type(curve).__name__ == "MixtureCurve":
            values = {**values, "tail_crps": values["tail_crps"] * 1.5}
        return values, q

    monkeypatch.setattr(module.ChronologicalCDFStudy, "scores", staticmethod(drifted))
    with pytest.raises(IntegrityError, match="tail_crps"):
        run_node(world)


# -- correction round 3: batching, scales, grid-point outcomes, bands, store form, learning curves -------

DECISIONS = [f"{who}_{m}" for who in ("model", "baseline", "pooled", "market")
             for m in ("decision_strike_brier", "decision_wing_twcrps")] + ["listed_strikes_n",
                                                                         "listed_wing_intervals_n"]


def test_a_multi_batch_archive_scores_exactly_as_one_batch(world, result, monkeypatch):
    import pyarrow.parquet as pq
    import index_options.strike_region_scores as module
    calls = []
    original = pq.ParquetFile.iter_batches

    def spy(self, *args, **kwargs):
        for batch in original(self, *args, **kwargs):
            calls.append(batch.num_rows)
            yield batch

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", spy)
    monkeypatch.setattr(module, "ARCHIVE_BATCH_ROWS", 7)
    out, params = run_node(world)
    assert len(calls) > 100 and max(calls) <= 7        # every identity's quotes span several batches
    rows = pd.read_parquet(Path(params["output"]) / "rows.parquet")
    cells = pd.read_parquet(Path(params["output"]) / "cells.parquet")
    pd.testing.assert_frame_equal(rows[KEY + ["band"] + DECISIONS], result.rows[KEY + ["band"] + DECISIONS],
                                  check_exact=True)
    pick = lambda c: c[c.metric.isin(["decision_strike_brier", "decision_wing_twcrps"])].reset_index(drop=True)  # noqa: E731
    pd.testing.assert_frame_equal(pick(cells), pick(result.cells), check_exact=True)


def test_outcomes_on_a_grid_point_follow_the_documented_conventions(result):
    val = val_rows(result)
    for (symbol, date, dte), z in EDGE.items():
        row = val[(val.symbol == symbol) & (val.quote_date == date) & (val.actual_calendar_dte == dte)].iloc[0]
        index = Z_GRID.index(z)
        assert row.y == z and row.realized_region == index      # (z[r-1], z[r]]: a point on an edge is the bin below it
        one_hot = [row[f"realized_r{r:02d}"] for r in range(REGIONS)]
        assert sum(one_hot) == 1 and one_hot[index] == 1
        for who in ("model", "baseline", "pooled"):
            for step, hit in ((-1, 0.0), (0, 1.0), (1, 1.0)):       # 1{y <= z}: equality counts as a hit
                label = zlabel(Z_GRID[index + step])
                cdf = row[f"{who}_cdf_z{label}"]
                assert row[f"{who}_brier_z{label}"] == pytest.approx((cdf - hit) ** 2)
    part = val[val.actual_calendar_dte == 1]
    cells = _cell(result.cells, band="val", window="full", dte_slice="1-1", ticker_group="all",
                  metric="reliability")
    for z in (0, 0.5):
        cell = cells[cells.threshold == z].iloc[0]
        assert cell.realized_freq_model == pytest.approx((part.y <= z).mean())
        assert cell.realized_freq_model > (part.y < z).mean()


def test_a_validation_band_larger_than_the_fit_sample_is_scored_completely(world, stored):
    out, params = run_node(world, fit_band_sample=5)
    rows = pd.read_parquet(Path(params["output"]) / "rows.parquet")
    assert (rows.band == "val").sum() == len(stored) > 5 and (rows.band == "fit").sum() == 5
    summary = json.loads((Path(params["output"]) / "summary.json").read_text())
    assert summary["scored_cells"][0]["rows"] == {"fit": 5, "val": len(stored)}


def test_sub_periods_include_both_of_their_end_dates(world, result):
    out, params = run_node(world, sub_periods={"head": ["2001-02-01", "2001-02-03"],
                                               "tail": ["2001-02-04", "2001-02-05"]})
    cells = pd.read_parquet(Path(params["output"]) / "cells.parquet")
    val = result.rows[result.rows.band == "val"]
    for name, last in (("head", "2001-02-03"), ("tail", "2001-02-05")):
        window = cells[(cells.band == "val") & (cells.window == name) & (cells.metric == "crps")
                       & (cells.ticker_group == "all")]
        inside = val[(val.quote_date >= {"head": "2001-02-01", "tail": "2001-02-04"}[name]) & (val.quote_date <= last)]
        assert len(inside) == {"head": 18, "tail": 12}[name] and window.rows.sum() == len(inside)
        assert window.set_index("dte_slice").dates["2-3"] == (3 if name == "head" else 2)


def test_store_form_archive_reads_like_a_directory_and_records_manifest_digests(tmp_path, blob_store, world, result):
    blob_store.add("archive", world.archive)
    reference = {"source": "archive", "stream": "files"}
    out, params = run_node(world, listed_strikes={"source": "archive", "archive_root": reference,
                                                  "data_root": str(blob_store.path), "min_valid_strikes": 2})
    rows = pd.read_parquet(Path(params["output"]) / "rows.parquet")
    pd.testing.assert_frame_equal(rows[KEY + ["band"] + DECISIONS], result.rows[KEY + ["band"] + DECISIONS],
                                  check_exact=True)
    sources = json.loads((Path(params["output"]) / "summary.json").read_text())["hashes"]["listed_strike_sources"]
    assert sorted(sources) == [f"store:archive/files/{t.lower()}/options_2001.parquet" for t in TICKERS]
    for label, digest in sources.items():
        assert digest == AtomicFitStore.file_hash(world.archive / label.split("files/")[1])


def test_curves_equal_the_checkpoint_state_exactly(world, result):
    state = json.loads((world.root / "fit" / "model" / "state.json").read_text())
    seed = result.curves["cells"][0]["seeds"][0]
    assert seed["train_loss"] == state["losses"][0] and seed["monitor_loss"] == []
    assert seed["epoch_zero_monitor_loss"] == state["diagnostics"][0]["epoch_zero_monitor_loss"]
    assert seed["parameter_delta_l2"] == state["diagnostics"][0]["parameter_delta_l2"]


@pytest.fixture(scope="module")
def patience_world(tmp_path_factory):
    return _build_world(tmp_path_factory.mktemp("strike-region-patience"),
                        training={"epochs": 40, "patience": 2, "seeds": [11, 29], "learning_rate": 0.05})


def test_a_patience_fit_with_two_seeds_gives_monitor_curves_signatures_and_a_checked_mixture(patience_world):
    w = patience_world
    state = json.loads((w.root / "fit" / "model" / "state.json").read_text())
    assert len(state["losses"]) == len(state["monitor_losses"]) == len(state["best_epochs"]) == 2
    out, params = run_node(w)
    root = Path(params["output"])
    cell = json.loads((root / "curves.json").read_text())["cells"][0]
    assert [s["seed"] for s in cell["seeds"]] == [11, 29] and cell["best_epochs"] == state["best_epochs"]
    for i, seed in enumerate(cell["seeds"]):
        assert seed["train_loss"] == state["losses"][i] and seed["monitor_loss"] == state["monitor_losses"][i]
        assert seed["best_epoch"] == state["best_epochs"][i]
        assert len(seed["monitor_loss"]) == len(seed["train_loss"]) == cell["total_epochs"][i]
    assert cell["signatures"] == _LearningCurves(state).signatures()
    assert all(isinstance(v, bool) for v in cell["signatures"].values())
    rows = pd.read_parquet(root / "rows.parquet")
    assert (rows.band == "val").sum() == 30 and rows.model_crps.notna().all()


def test_the_scorer_conditions_an_unseen_horizon_on_the_nearest_fitted_one_like_the_queue():
    from dskit.pipeline.libs.predictive_cdf import HorizonEmpiricalCDF
    from index_options.strike_region_scores import StrikeRegionScores
    rng = np.random.default_rng(5)
    fit_x = np.column_stack([np.repeat([1.0, 2.0, 5.0], 40), np.full(120, 0.03)])
    baseline = HorizonEmpiricalCDF(0, 1, 21).fit(fit_x, rng.normal(size=120), None, None)
    query = np.array([[3.0, 0.03], [5.0, 0.03], [1.0, 0.03], [9.0, 0.03]])
    moved = StrikeRegionScores._conditioned(baseline, query)
    assert moved[:, 0].tolist() == [2.0, 5.0, 1.0, 5.0] and moved[:, 1].tolist() == query[:, 1].tolist()
    assert query[0, 0] == 3.0                                           # the caller's rows are untouched
    assert baseline.curve(moved).cdf([0.0]).shape == (4, 1)             # the raw query would raise KeyError
    with pytest.raises(KeyError):
        baseline.curve(query)


def test_a_validation_sample_is_a_deterministic_stride_whose_rows_equal_the_full_runs(world, result, stored):
    n = len(stored)
    limit = max(2, n // 3)
    sampled, params = run_node(world, validation_band_sample=limit)
    rows = pd.read_parquet(Path(params["output"]) / "rows.parquet")
    val, full = rows[rows.band == "val"], val_rows(result)
    stride = -(-n // limit)
    assert 0 < len(val) < len(full) and len(val) == len(range(0, n, stride)) * len(set(full.candidate))
    joined = val.merge(full, on=KEY + ["arm", "candidate", "fold", "band"], suffixes=("", "_full"), validate="one_to_one")
    assert len(joined) == len(val)
    for name in ("model_crps", "baseline_crps", "model_weighted_crps"):
        np.testing.assert_allclose(joined[name].to_numpy(), joined[name + "_full"].to_numpy(), rtol=1e-12, atol=0)
    again, params2 = run_node(world, validation_band_sample=limit)
    assert pd.read_parquet(Path(params2["output"]) / "rows.parquet")[KEY + ["band"]].equals(rows[KEY + ["band"]])


def test_validation_band_sample_is_optional_and_must_be_a_positive_integer_or_none(world):
    assert StrikeRegionScores.validate_params(make_params(world, validation_band_sample=None)) == []
    assert StrikeRegionScores.validate_params(make_params(world, validation_band_sample=5)) == []
    assert StrikeRegionScores.validate_params(make_params(world, validation_band_sample=0))
