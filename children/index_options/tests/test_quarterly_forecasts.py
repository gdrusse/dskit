"""The quarterly forecast pipeline (ADR-0298, proposed) on a tiny synthetic panel: no market data, no real fit."""

import copy
import gc
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("torch")
pytest.importorskip("sklearn")

from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.libs.cdf_research import ResidualCDFStudy
from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy, MixtureCurve, TorchCDF

from index_options import panel_extension as pe
from index_options import quarterly_forecasts as qf
from index_options.strike_region_scores import StrikeRegionScores

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
TICKERS = ["AAA", "BBB"]
PROBS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z_GRID = [-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3]
RN = [f"rn_q_{round(p * 10000):04d}" for p in PROBS]
FEATURES = RN + ["log_sessions_to_expiry"]
QUARTERS = ["2023Q4", "2024Q1"]
SCORE_WEIGHTS = [.25, 1, 1, 1, 1, 1, .25, 1, 1, 1, 1, 1, .25]
RECIPE = {"kind": "fixed_loc", "learning_rate": 3e-4, "dropout": .3, "weight_decay": .3, "hidden": 8,
          "width": 4, "epochs": 1, "seeds": [11, 29], "average": "linear_pool"}


def _panel_rows(seed=7):
    rng = np.random.default_rng(seed)
    rows = []
    for ticker in TICKERS:
        for day in pd.date_range("2023-01-02", "2024-03-25", freq="W-MON"):
            for dte in (7, 14):
                expiry = (day + pd.Timedelta(days=dte)).date().isoformat()
                row = {"symbol": ticker, "quote_date": day.date().isoformat(), "expiry": expiry,
                       "settlement_date": expiry, "actual_calendar_dte": float(dte),
                       "reference_scale": float(.02 + .02 * rng.random()), "spot": float(100 + rng.normal()),
                       "terminal_return": float(rng.normal() * .03), "rn_proxy_eligible": 1,
                       "log_sessions_to_expiry": float(np.log(dte))}
                row.update({c: float(v) for c, v in zip(RN, np.sort(rng.normal(size=5)))})
                row["decision_context"] = json.dumps({
                    "identity": [ticker, row["quote_date"], expiry], "thresholds": [-1.6, -.7, .4, 1.3],
                    "weights": [.25] * 4, "intervals": [[-1.6, -.7], [.4, 1.3]], "status": "eligible"})
                rows.append(row)
    return pd.DataFrame(rows)


def _templates(tmp):
    base = json.loads((CONFIGS / "advanced-cdf-zoo.json").read_text())
    adapter = {"class": "dskit.pipeline.libs.torch_ts:ArchSequenceEncoder",
               "params": {"arch": "mlp", "arch_params": {"hidden_size": 64}, "width": 16}}
    study = base["study"]
    study["features"] = list(FEATURES)
    study["models"]["horizon_empirical"]["params"].update(horizon_index=0, reference_index=1)
    study["fold_table"] = {"path": "unused", "sha256": "0" * 64, "holdout_start": "2024-01-01", "cal_n": 5,
                           "roles": ["warmup", "scored"]}
    doc = {"arms": {"context_chain": {"observed_columns": [], "min_fit": 2, "min_cal": 1, "min_disjoint": 0,
                                      "features": list(FEATURES), "sequence_indices": [[i] for i in range(5)],
                                      "context_indices": [5]}},
           "candidates": [{"name": "fixed_loc_market_only_mlp64", "adapters": {"pooled": [adapter],
                                                                               "unpooled": [adapter]},
                           "skip_reason": None}],
           "capacity": {"pooled": {"absolute": 100000, "per_row": 1000, "head_per_row": 1000, "head_widths": [2],
                                   "batch_size": 16, "device": "cuda", "ticker_heads": False},
                        "unpooled": base["capacity"]["unpooled"]},
           "date_upper_exclusive": "2024-01-01", "end_upper_exclusive": "2024-01-01", "dependencies": {},
           "output": "unused", "panel": "unused", "panel_sha256": "0" * 64, "source_hashes": {}, "study": study,
           "tickers": TICKERS, "training": {"components": 3, "deterministic": True, "dropout": .3, "epochs": 1,
                                            "learning_rate": 3e-4, "losses": [{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 1.0}],
                                            "patience": None, "seeds": [11], "weight_decay": .3,
                                            "fixed_location": True},
           "tickers_sha256": hashlib.sha256(json.dumps(sorted(TICKERS), separators=(",", ":")).encode()).hexdigest(),
           "refit_full_train": True}
    score = {"name": "s", "outputs": {"run_root": "unused"}, "pipeline": {"scores": {
        "uses": "index_options.strike_region_scores:StrikeRegionScores",
        "params": {"experiment_document": "unused", "panel": "unused", "output": "unused", "z_grid": Z_GRID,
                   "dte_slices": [[1, 7], [8, 14]], "ticker_groups": {},
                   "sub_periods": {"S3_rate_shock": ["2023-01-01", "2023-06-30"], "S4_2023": ["2023-07-01", "2023-12-31"]},
                   "market": {"probabilities": PROBS, "prefix": "rn_q_", "eligible_field": "rn_proxy_eligible",
                              "units": "z", "tail_width": 4.0},
                   "listed_strikes": {"source": "panel_context", "context_field": "decision_context"},
                   "fit_band_sample": 50, "end_before": "2024-01-01", "chunk_rows": 32, "stored_tolerance": 1e-4}}}}
    (tmp / "fit-template.json").write_text(json.dumps(doc))
    (tmp / "score-template.json").write_text(json.dumps(score))


def _lock(tmp):
    wire = json.dumps(TICKERS, separators=(",", ":")).encode()
    path = tmp / "lock.json"
    path.write_text(json.dumps({"symbols": TICKERS, "count": 2, "sha256": hashlib.sha256(wire).hexdigest()}))
    return path


def _write_verify(tmp, passed, **extra):
    (tmp / "verify.json").write_text(json.dumps({
        "passed": passed, "panel_sha256": AtomicFitStore.file_hash(tmp / "frame-2025.parquet"),
        "old_frame_sha256": AtomicFitStore.file_hash(tmp / "frame-2023.parquet"), **extra}))


def _world(tmp, recipe=None, panels=None, score_weights=SCORE_WEIGHTS):
    tmp.mkdir(parents=True, exist_ok=True)
    _templates(tmp)
    frame = _panel_rows() if panels is None else panels
    frozen = frame[frame.settlement_date < "2024-01-01"]
    frozen.to_parquet(tmp / "frame-2023.parquet", index=False)
    frame.to_parquet(tmp / "frame-2025.parquet", index=False)
    _write_verify(tmp, passed=True)
    (tmp / "recipe.json").write_text(json.dumps(recipe or RECIPE))
    run = {"recipe": str(tmp / "recipe.json"), "fit_template": str(tmp / "fit-template.json"),
           "score_template": str(tmp / "score-template.json"),
           "panel_open": str(tmp / "frame-2023.parquet"), "panel_sealed": str(tmp / "frame-2025.parquet"),
           "panel_verify": str(tmp / "verify.json"),
           "open_root": str(tmp / "open"), "sealed_root": str(tmp / "sealed"), "scores_root": str(tmp / "scores"),
           "plan_root": str(tmp / "plan"), "ticker_lock": str(_lock(tmp)), "synthetic": True,
           "device": "cpu", "train_start": "2023-01-02", "grid": {
               "z_grid": Z_GRID, "score_weights": score_weights, "tail": 4.0, "levels": 39, "batch_rows": 32},
           "proxy": {"columns": RN, "probabilities": PROBS, "eligible": "rn_proxy_eligible", "units": "z",
                     "tail_width": 4.0},
           "calibration": {"max_rows": 1000, "knots": 5},
           "ram_model": {"base_gb": 2.7, "gb_per_million_rows": 2.4, "basis": "synthetic"},
           "score_ram_gb": 1.0, "python": "python", "repo": str(Path(qf.__file__).resolve().parents[3]),
           "quarters": QUARTERS}
    path = tmp / "run.json"
    path.write_text(json.dumps(run))
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One plan, both quarters run once on CPU."""
    tmp = tmp_path_factory.mktemp("quarterly")
    path = _world(tmp)
    qf.main(["plan", str(path)])
    for quarter in QUARTERS:
        qf.main(["quarter", str(path), quarter])
    return tmp, path, qf.Pipeline.load(path)


def _forecast(built, quarter):
    tmp, _, pipe = built
    return pipe.forecast_store(qf.Quarter.parse(quarter)).path


# --- the quarter table and its two seals ---------------------------------------------------------------

def test_quarters_are_2019q4_then_2020q1_through_the_holdout_and_end_at_the_seal():
    all_ = qf.Quarter.all()
    assert all_[0].name == "2019Q4" and all_[0].start == "2019-10-01" and all_[0].train_end == "2019-09-30"
    assert all_[1].name == "2020Q1" and not any(q.name in ("2019Q1", "2019Q2", "2019Q3") for q in all_)
    assert all_[-1].name == "2025Q4" and all_[-1].end_before == "2025-11-29" and len(all_) == 25
    assert [q.name for q in all_ if not q.sealed][-1] == "2023Q4" and all_[17].sealed and sum(not q.sealed for q in all_) == 17
    assert sum(q.sealed for q in all_) == 8
    assert all(a.end_before == b.start for a, b in zip(all_, all_[1:]))


def test_2019q4_keeps_fold_29_and_every_existing_fold_stays():
    folds = {q.name: q.fold for q in qf.Quarter.all()}
    assert folds["2019Q4"] == 29
    assert folds["2020Q1"] == 2 and folds["2023Q4"] == 17 and folds["2024Q1"] == 18 and folds["2025Q4"] == 25
    assert sorted(folds.values()) == [*range(2, 26), 29]    # whole numbers from 1, as MIO's bridge requires
    assert [q.name for q in qf.Quarter.all() if q.tuned] == ["2019Q4"]


@pytest.mark.parametrize("name", ["2026Q1", "2018Q4", "2025Q5", "2024", "2026q1", "2019Q1", "2019Q2", "2019Q3"])
def test_a_quarter_outside_the_table_refuses(name):
    with pytest.raises(ValueError):
        qf.Quarter.parse(name)


@pytest.mark.parametrize("day", ["2025-11-29", "2025-12-31", "2026-01-02"])
def test_any_date_at_or_past_the_forever_seal_refuses(day):
    with pytest.raises(IntegrityError):
        qf.ScoreGate.refuse_past_forever(pd.Series(["2025-11-28", day]))


def test_a_panel_with_a_row_past_the_forever_seal_refuses_the_plan(tmp_path):
    frame = _panel_rows()
    frame.loc[0, "settlement_date"] = "2026-01-02"
    path = _world(tmp_path, panels=frame)
    with pytest.raises(IntegrityError):
        qf.main(["plan", str(path)])


# --- the structural refusal on metrics ---------------------------------------------------------------

@pytest.mark.parametrize("day", ["2024-01-01", "2024-01-02", "2025-11-28"])
def test_metrics_structurally_refuse_any_date_from_2024(day):
    with pytest.raises(IntegrityError):
        qf.ScoreGate.refuse_sealed(pd.Series(["2023-06-01", day]))


def test_the_metric_entry_points_refuse_a_sealed_quarter(built):
    _, path, pipe = built
    sealed = qf.Quarter.parse("2024Q1")
    with pytest.raises(IntegrityError):
        pipe.method_scores(sealed)
    with pytest.raises(IntegrityError):
        pipe.full_scorer_document(sealed)
    with pytest.raises(IntegrityError):
        qf.main(["score", str(path), "2024Q1"])
    assert not list(Path(built[0] / "scores").glob("2024Q1*"))


def test_the_scoring_core_refuses_a_row_from_2024_and_a_sealed_path(built):
    tmp, _, pipe = built
    frame = pd.DataFrame({"quote_date": ["2023-10-02", "2024-01-02"], "settlement_date": ["2023-10-09", "2024-01-09"]})
    with pytest.raises(IntegrityError):
        pipe.score_rows(frame, np.zeros((2, 13)), None, np.zeros(2))
    with pytest.raises(IntegrityError):
        pipe.refuse_sealed_path(_forecast(built, "2024Q1") / "forecasts.parquet")
    # A row quoted in 2023 whose label reaches 2024 is on the later side (A0003).
    frame = pd.DataFrame({"quote_date": ["2023-12-28"], "settlement_date": ["2024-01-05"]})
    with pytest.raises(IntegrityError):
        pipe.score_rows(frame, np.zeros((1, 13)), None, np.zeros(1))


def test_the_full_scorer_itself_refuses_an_end_past_the_metric_seal(built, tmp_path):
    _, _, pipe = built
    params = json.loads(json.dumps(pipe.full_scorer_document(qf.Quarter.parse("2023Q4"))["pipeline"]["scores"]["params"]))
    params.update(end_before="2024-01-02", output=str(tmp_path / "x"))
    node = StrikeRegionScores("scores", params)
    with pytest.raises(ValueError, match="2024-01-01"):
        node.run(None, {})
    assert not (tmp_path / "x").exists()


def test_no_score_table_or_metric_file_exists_for_a_sealed_quarter(built):
    tmp, _, pipe = built
    sealed = Path(pipe.config["sealed_root"])
    names = [p.name for p in sealed.rglob("*") if p.is_file()]
    assert not [n for n in names if re.search("score|crps|metric|table|summary$", n) and n != "summary.json"]
    published = pipe.forecast_store(qf.Quarter.parse("2024Q1")).path
    assert sorted(p.name for p in published.iterdir()) == ["complete.json", "contexts.parquet", "forecasts.parquet",
                                                           "receipt.json"]
    columns = pq.read_schema(published / "forecasts.parquet").names + pq.read_schema(published / "contexts.parquet").names
    assert not [c for c in columns if re.search("^y$|crps|pit|realized|terminal|score", c)]
    fit = pipe.fit_root(qf.Quarter.parse("2024Q1"))
    assert not (fit / "scores").exists()
    summary = json.loads((fit / "completed" / "summary.json").read_text())
    assert summary["diagnostics"] == []            # no monitor loss on rows from 2024


def test_sealed_output_is_a_separate_private_folder_with_a_hash_manifest(built):
    tmp, path, pipe = built
    qf.main(["check", str(path)])
    manifest = json.loads((Path(pipe.config["sealed_root"]) / "MANIFEST.json").read_text())
    assert set(manifest["quarters"]) == {"2024Q1"}
    entry = manifest["quarters"]["2024Q1"]
    published = pipe.forecast_store(qf.Quarter.parse("2024Q1")).path
    assert entry["files"]["forecasts.parquet"] == AtomicFitStore.file_hash(published / "forecasts.parquet")
    assert Path(pipe.config["sealed_root"]).stat().st_mode & 0o077 == 0
    assert not set(Path(pipe.config["open_root"]).resolve().parents) & {Path(pipe.config["sealed_root"]).resolve()}
    opened = json.loads((Path(pipe.config["open_root"]) / "MANIFEST.json").read_text())
    assert set(opened["quarters"]) == {"2023Q4"}


# --- the reports print structure only ----------------------------------------------------------------

def test_check_prints_no_value(built, capsys):
    _, path, _ = built
    qf.main(["check", str(path)])
    out = capsys.readouterr().out
    assert '"finite": true' in out and "2024Q1" in out
    assert not re.search(r"\d\.\d", out)            # counts, dates and hashes only


def test_check_finds_a_damaged_file_without_showing_values(built, tmp_path, capsys):
    tmp, path, pipe = built
    quarter = qf.Quarter.parse("2024Q1")
    frame = pd.read_parquet(_forecast(built, "2024Q1") / "forecasts.parquet")
    report = pipe.structure(quarter, frame.iloc[:-1])
    assert report["rows_match_panel"] is False
    frame.loc[0, "method_cdf_zp0"] = float("nan")
    assert pipe.structure(quarter, frame)["finite"] is False


# --- windows and maturity (A0003, A0004, A0008) -------------------------------------------------------

@pytest.mark.parametrize("quarter", QUARTERS)
def test_each_quarter_trains_only_on_labels_settled_before_it_starts(built, quarter):
    _, _, pipe = built
    q = qf.Quarter.parse(quarter)
    metadata = json.loads((pipe.fit_root(q) / "fit" / "metadata.json").read_text())
    fit_dates, cal_dates = metadata["band_dates"][:2]
    assert fit_dates["settlement_date"][1] < q.start and cal_dates["settlement_date"][1] < q.start
    assert metadata["band_counts"][0] > 0


def _rerun_with(tmp, frame):
    path = _world(tmp, panels=frame)
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    pipe.run_quarter(qf.Quarter.parse("2024Q1"))
    published = pipe.forecast_store(qf.Quarter.parse("2024Q1")).path
    receipt = json.loads((published / "receipt.json").read_text())
    assert receipt["forecasts_sha256"] == AtomicFitStore.file_hash(published / "forecasts.parquet")
    return pd.read_parquet(published / "forecasts.parquet")


def test_a_label_not_yet_settled_at_the_quarter_start_cannot_move_the_forecast(built, tmp_path):
    frame = _panel_rows()
    late = frame.settlement_date >= "2024-01-01"
    frame.loc[late, "terminal_return"] = 9.0
    moved = _rerun_with(tmp_path, frame)
    pd.testing.assert_frame_equal(moved, pd.read_parquet(_forecast(built, "2024Q1") / "forecasts.parquet"))
    published = qf.Pipeline.load(tmp_path / "run.json").forecast_store(qf.Quarter.parse("2024Q1")).path
    assert (json.loads((published / "receipt.json").read_text())["forecasts_sha256"]
            == AtomicFitStore.file_hash(_forecast(built, "2024Q1") / "forecasts.parquet"))     # the runbook's rerun check


def test_a_settled_label_does_move_the_forecast(built, tmp_path):
    frame = _panel_rows()
    frame.loc[frame.settlement_date < "2023-12-31", "terminal_return"] *= 3.0
    moved = _rerun_with(tmp_path, frame)
    columns = [c for c in moved if c.startswith("method_cdf_")]
    assert not np.allclose(moved[columns], pd.read_parquet(_forecast(built, "2024Q1") / "forecasts.parquet")[columns])


def test_open_and_sealed_training_give_the_same_weights(tmp_path):
    """The owner's "just as we do on the other quarters": scoring and telemetry never touch the weights."""
    path = _world(tmp_path)
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    opened = pd.read_parquet(pipe.forecast_store(q).path / "forecasts.parquet")
    other = copy.deepcopy(pipe)
    document = other.fit_document(q)
    document.update(date_upper_exclusive=q.start, end_upper_exclusive=q.start, training_telemetry=False,
                    output=str(tmp_path / "mode-a"))
    CDFExperiment(document).run("pooled")
    root = tmp_path / "mode-a" / "pooled" / "context_chain" / "fixed_loc_market_only_mlp64" / str(q.fold) / "pooled"
    a = (pipe.fit_root(q) / "fit" / "model").iterdir()
    assert {p.name for p in a} == {p.name for p in (root / "fit" / "model").iterdir()}
    state_a = json.loads((pipe.fit_root(q) / "fit" / "model" / "state.json").read_text())
    state_b = json.loads((root / "fit" / "model" / "state.json").read_text())
    assert state_a["weights_sha256"] == state_b["weights_sha256"]
    assert not opened.empty


# --- the average method is a parameter ----------------------------------------------------------------

def test_recipe_refuses_a_missing_or_unknown_field():
    for bad in ({k: v for k, v in RECIPE.items() if k != "average"}, {**RECIPE, "average": "best"},
                {**RECIPE, "kind": "mlp"}, {**RECIPE, "extra": 1}, {**RECIPE, "seeds": []}, {**RECIPE, "hidden": 8.5}):
        with pytest.raises(ValueError):
            qf.Recipe(bad)


def test_seeds_and_width_of_the_recipe_reach_the_fit_document(built):
    _, _, pipe = built
    document = pipe.fit_document(qf.Quarter.parse("2023Q4"))
    assert document["training"]["seeds"] == [11, 29] and document["training"]["epochs"] == 1
    adapter = document["candidates"][0]["adapters"]["pooled"][0]["params"]
    assert adapter["width"] == 4 and adapter["arch_params"]["hidden_size"] == 8
    assert document["training"]["fixed_location"] is True and document["capacity"]["pooled"]["device"] == "cpu"
    assert document["study"]["fold_table"]["holdout_start"] == "2024-01-01"
    sealed = pipe.fit_document(qf.Quarter.parse("2024Q1"))
    assert sealed["study"]["fold_table"]["holdout_start"] == "2025-11-29" and sealed["panel"].endswith("frame-2025.parquet")
    assert sealed["date_upper_exclusive"] == sealed["end_upper_exclusive"] == "2024-01-01"


def _mixture(path):
    frame = pd.read_parquet(path)
    n = sum(c.startswith("weights") for c in frame)
    return frame, [frame[[f"{name}{i}" for i in range(n)]].to_numpy(float) for name in ("weights", "means", "scales")]


def test_linear_pool_is_the_mixture_and_quantile_average_differs_only_by_the_method(built, tmp_path):
    _, _, pipe = built
    q = qf.Quarter.parse("2023Q4")
    frame, (w, m, s) = pipe.mixture_rows(q)
    linear = qf.SeedAverage("linear_pool", 2, pipe.grid)
    np.testing.assert_allclose(linear.cdf(w, m, s), MixtureCurve(w, m, s).cdf(Z_GRID))
    published = pd.read_parquet(pipe.forecast_store(q).path / "forecasts.parquet")
    columns = [f"method_cdf_{label}" for label in pipe.grid.labels]
    ordered = frame[qf.KEYS].merge(published, on=qf.KEYS, validate="one_to_one")
    np.testing.assert_allclose(ordered[columns].to_numpy(float), linear.cdf(w, m, s))
    average = qf.SeedAverage("quantile_average", 2, pipe.grid)
    cdf = average.cdf(w, m, s)
    assert np.isfinite(cdf).all() and (np.diff(cdf, axis=1) >= -1e-12).all() and not np.allclose(cdf, linear.cdf(w, m, s))
    # The middle of a two-seed quantile average lies between the seeds' own CDFs.
    one = MixtureCurve(w[:, :3] * 2, m[:, :3], s[:, :3]).cdf(Z_GRID)
    two = MixtureCurve(w[:, 3:] * 2, m[:, 3:], s[:, 3:]).cdf(Z_GRID)
    assert (cdf[:, 6] >= np.minimum(one, two)[:, 6] - .05).all() and (cdf[:, 6] <= np.maximum(one, two)[:, 6] + .05).all()


def test_the_seed_components_are_the_independent_single_seed_fits(built, tmp_path):
    """A joint fit is its seeds' fits, so the average can change without any new training."""
    _, _, pipe = built
    q = qf.Quarter.parse("2023Q4")
    document = pipe.fit_document(q)
    document["training"]["seeds"] = [29]
    document["output"] = str(tmp_path / "single")
    CDFExperiment(document).run("pooled")
    root = Path(document["output"]) / "pooled" / "context_chain" / "fixed_loc_market_only_mlp64" / str(q.fold) / "pooled"
    single = pd.concat(pd.read_parquet(p) for p in root.glob("scores/*/scores.parquet")).sort_values(qf.KEYS)
    joint, (w, m, s) = pipe.mixture_rows(q)
    assert joint[qf.KEYS].reset_index(drop=True).equals(single[qf.KEYS].reset_index(drop=True))
    np.testing.assert_allclose(w[:, 3:] * 2, single[[f"weights{i}" for i in range(3)]].to_numpy(float), atol=1e-6)
    np.testing.assert_allclose(s[:, 3:], single[[f"scales{i}" for i in range(3)]].to_numpy(float), atol=1e-6)


def test_changing_the_average_reuses_the_fits(tmp_path):
    path = _world(tmp_path)
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    before = (pipe.fit_root(q) / "fit" / "complete.json").read_bytes()
    (tmp_path / "recipe.json").write_text(json.dumps({**RECIPE, "average": "quantile_average"}))
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    assert (pipe.fit_root(q) / "fit" / "complete.json").read_bytes() == before
    pipe.run_quarter(q)
    assert "quantile_average" in str(json.loads((pipe.forecast_store(q).path / "receipt.json").read_text())["recipe"])
    assert pipe.forecast_store(q).path.exists()


# --- resume and atomic publish ------------------------------------------------------------------------

def test_a_finished_quarter_is_verified_not_repeated_and_damage_refuses(tmp_path):
    path = _world(tmp_path)
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    target = pipe.forecast_store(q).path / "forecasts.parquet"
    stamp = target.stat().st_mtime_ns
    pipe.run_quarter(q)
    assert target.stat().st_mtime_ns == stamp
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(IntegrityError):
        pipe.run_quarter(q)


def test_an_interrupted_quarter_leaves_nothing_published_and_resumes(tmp_path, monkeypatch):
    path = _world(tmp_path)
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    q = qf.Quarter.parse("2023Q4")
    original = pipe._forecast_files
    monkeypatch.setattr(pipe, "_forecast_files", lambda *a: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        pipe.run_quarter(q)
    assert not pipe.forecast_store(q).path.exists() and (pipe.fit_root(q) / "fit" / "complete.json").exists()
    monkeypatch.setattr(pipe, "_forecast_files", original)
    stamp = (pipe.fit_root(q) / "fit" / "complete.json").stat().st_mtime_ns
    pipe.run_quarter(q)
    assert pipe.forecast_store(q).path.exists()
    assert (pipe.fit_root(q) / "fit" / "complete.json").stat().st_mtime_ns == stamp


def test_a_changed_document_after_the_plan_refuses(tmp_path):
    path = _world(tmp_path)
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    q = qf.Quarter.parse("2023Q4")
    document = pipe.plan_root / "docs" / f"{q.name}-fit.json"
    document.write_text(document.read_text().replace('"epochs": 1', '"epochs": 2'))
    with pytest.raises(IntegrityError):
        pipe.run_quarter(q)


def test_the_ticker_lock_guards_every_document(tmp_path):
    path = _world(tmp_path)
    config = json.loads(path.read_text())
    Path(config["ticker_lock"]).write_text(json.dumps({"symbols": ["AAA"], "count": 1, "sha256": hashlib.sha256(
        json.dumps(["AAA"], separators=(",", ":")).encode()).hexdigest()}))
    with pytest.raises((ValueError, IntegrityError)):
        qf.main(["plan", str(path)])


def test_a_real_run_config_refuses_cpu_and_a_private_lock(tmp_path):
    path = _world(tmp_path)
    config = json.loads(path.read_text())
    config["synthetic"] = False
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="cuda"):
        qf.Pipeline.load(path)
    config["device"] = "cuda"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="lock"):
        qf.Pipeline.load(path)


def test_the_halt_file_stops_a_quarter_before_any_work(tmp_path):
    path = _world(tmp_path)
    config = json.loads(path.read_text())
    config["halt_file"] = str(tmp_path / "HALT")
    path.write_text(json.dumps(config))
    qf.main(["plan", str(path)])
    (tmp_path / "HALT").write_text("stop")
    pipe = qf.Pipeline.load(path)
    with pytest.raises(RuntimeError, match="HALT"):
        pipe.run_quarter(qf.Quarter.parse("2023Q4"))
    assert not pipe.fit_root(qf.Quarter.parse("2023Q4")).exists()


# --- compute gate -------------------------------------------------------------------------------------

def test_the_job_plan_is_what_the_gate_needs_and_grows_with_the_quarter(built):
    tmp, _, pipe = built
    jobs = json.loads((pipe.plan_root / "jobs-open.json").read_text())
    assert jobs["limits"]["gpu_slots"] == 3
    job = jobs["jobs"][0]
    assert job["id"] == "forecast-2023Q4" and job["gpu"] == 1 and job["ram_gb"] >= 2.7 and job["ram_basis"]
    assert "quarter" in job["command"] and "2023Q4" in job["command"]
    assert job["env"]["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"
    sealed = json.loads((pipe.plan_root / "jobs-sealed.json").read_text())["jobs"][0]
    assert sealed["id"] == "forecast-2024Q1" and sealed["ram_gb"] >= job["ram_gb"]
    score = {j["id"]: j for j in jobs["jobs"]}["score-2023Q4"]
    assert score["depends_on"] == ["forecast-2023Q4"] and score["serial_group"] == "scores" and score["serial_reason"]
    assert "dskit.pipeline" not in score["command"] and not (pipe.plan_root / "jobs-scores.json").exists()


# --- the MIO contract ---------------------------------------------------------------------------------

def test_files_and_columns_are_the_mio_forecast_input(built):
    _, _, pipe = built
    q = qf.Quarter.parse("2023Q4")
    published = pipe.forecast_store(q).path
    forecasts = pq.read_table(published / "forecasts.parquet")
    labels = ["m3", "m2.5", "m2", "m1.5", "m1", "m0.5", "p0", "p0.5", "p1", "p1.5", "p2", "p2.5", "p3"]
    cdf = [f"method_cdf_z{label}" for label in labels]
    identity = ["symbol", "quote_date", "expiry", "fold", "band"]
    assert set(forecasts.schema.names) == set(identity + cdf + ["fitted"])
    contexts = pq.read_table(published / "contexts.parquet")
    assert set(contexts.schema.names) == set(identity + ["spot", "reference_scale", "settlement_date"])
    assert forecasts.schema.field("fitted").type == pa.bool_()
    frame, other = forecasts.to_pandas(), contexts.to_pandas()
    CDFExperiment.check_pairing(frame, other, identity)
    assert frame["fold"].eq(q.fold).all() and frame["band"].eq("val").all()
    assert frame["quote_date"].between(q.start, q.end_before, inclusive="left").all()
    # The very filter observations.MIOPacketInputs.read_bounded applies.
    delta = (ds.field("expiry").cast(pa.date32()).cast(pa.int32())
             - ds.field("quote_date").cast(pa.date32()).cast(pa.int32()))
    allowed = (ds.field("symbol") == "AAA") & (delta >= 1) & (delta <= 60) & (ds.field("fold") == q.fold) & (ds.field("band") == "val")
    rows = ds.dataset(published / "forecasts.parquet").to_table(filter=allowed)
    assert 0 < rows.num_rows < len(frame)
    receipt = json.loads((published / "receipt.json").read_text())
    assert receipt["schema"] == "quarterly-forecast/v1" and receipt["evaluation_computed"] is False
    assert receipt["forecast_window"] == {"start": q.start, "end_before": q.end_before}
    assert receipt["label_cutoff"] == q.start and receipt["bridge_fold"] == q.fold
    assert receipt["expected_names"] + receipt["missing_names"] == TICKERS


def test_open_forecasts_never_reach_2024_and_pins_are_mio_readable(built, tmp_path):
    tmp, path, pipe = built
    root = tmp_path / "ob"
    qf.main(["pin", str(path), "--onboarding-root", str(root), "--source", "synthetic-quarterly-forecasts"])
    binding = json.loads((Path(pipe.config["open_root"]) / f"mio-bindings-{pipe.recipe.identity[:12]}" / "2023Q4.json").read_text())
    assert set(binding) == {"forecasts", "contexts", "receipt"}
    pin = binding["forecasts"]["pin"]
    source = PinnedPayloadSource(pin)
    assert binding["forecasts"]["files"]["shared"] == "2023Q4/forecasts.parquet"
    assert source.pin == binding["contexts"]["pin"]
    forecast = pd.read_parquet(source.file("2023Q4/forecasts.parquet", verify=True))
    context = pd.read_parquet(source.file("2023Q4/contexts.parquet", verify=True))
    assert (forecast.quote_date < "2024-01-01").all() and (context.settlement_date < "2024-01-01").all()
    assert not [name for name in pin["files"] if name.startswith("2024")]
    assert (root / "raw").exists()
    # A sealed quarter has no binding, and the pin path refuses the sealed folder.
    with pytest.raises(IntegrityError):
        pipe.refuse_sealed_path(Path(pipe.config["sealed_root"]) / "anything")


# --- through-2023 scores, with CRPS beside twCRPS -------------------------------------------------------

def test_fast_scores_are_exact_and_carry_crps_beside_twcrps(built):
    tmp, path, pipe = built
    q = qf.Quarter.parse("2023Q4")
    qf.main(["score", str(path), q.name])
    rows = pd.read_parquet(pipe.method_score_path(q))
    assert {"twcrps", "crps"} <= set(rows.columns) and rows.settlement_date.max() < "2024-01-01"
    frame, (w, m, s) = pipe.mixture_rows(q)
    joined = frame[qf.KEYS].merge(rows, on=qf.KEYS, validate="one_to_one")
    panel = pd.read_parquet(pipe.config["panel_open"])
    y = joined.merge(panel, on=qf.KEYS).eval("terminal_return / reference_scale").to_numpy()
    curve = MixtureCurve(w, m, s)
    expected = ChronologicalCDFStudy.scores(curve, y, 201, [[-2.5, -.5], [.5, 2.5]], 101)[0]["crps"]
    np.testing.assert_allclose(joined["crps"], expected)
    coefficient = np.array([.0625, .5, .5, .5, .5, .5, .125, .5, .5, .5, .5, .5, .0625])
    grid = curve.cdf(Z_GRID)
    np.testing.assert_allclose(joined["twcrps"], np.square(grid - (y[:, None] <= np.array(Z_GRID))) @ coefficient)


def test_fast_scores_equal_the_full_scorers_rows(built):
    tmp, path, pipe = built
    q = qf.Quarter.parse("2023Q4")
    qf.main(["score", str(path), q.name])
    full = pd.read_parquet(pipe.full_scorer_path(q) / "rows.parquet")
    full = full[full.band == "val"]
    assert {"model_crps", "market_crps", "baseline_crps", "pooled_crps"} <= set(full.columns)
    rows = pd.read_parquet(pipe.method_score_path(q))
    joined = rows.merge(full, on=qf.KEYS, validate="one_to_one")
    assert len(joined) == len(full) > 0
    np.testing.assert_allclose(joined["crps"], joined["model_crps"], rtol=1e-9, atol=1e-9)
    columns = [f"model_cdf_{label}" for label in pipe.grid.labels]
    grid = pd.read_parquet(pipe.forecast_store(q).path / "forecasts.parquet").merge(full[qf.KEYS + columns], on=qf.KEYS)
    np.testing.assert_allclose(grid[[f"method_cdf_{label}" for label in pipe.grid.labels]], grid[columns], atol=1e-12)


def test_the_score_table_has_crps_beside_twcrps_and_refuses_a_sealed_row(built):
    tmp, path, pipe = built
    qf.main(["score", str(path), "2023Q4"])
    qf.main(["table", str(path)])
    table = pd.read_csv(pipe.score_table_path())
    assert {"twcrps", "crps", "twcrps_gain_pct", "crps_gain_pct", "twcrps_ci_low", "crps_ci_low"} <= set(table.columns)
    for level in ("all", "year", "regime", "dte", "cell"):
        assert level in set(table["level"])
    assert {"market", "baseline"} <= set(table["versus"])
    assert (table[table.level == "all"]["rows"] > 0).all()
    assert table["period"].astype(str).str.contains("2024").sum() == 0
    gate = json.loads(pipe.score_gate_path().read_text())
    assert set(gate) == {"rows", "tuned_rows_excluded", "quarters", "a0005"} and gate["tuned_rows_excluded"] == 0
    whole = table[(table.level == "all") & (table.versus == "market")].iloc[0]
    rows = pipe._score_frame([qf.Quarter.parse("2023Q4")])
    owner = ResidualCDFStudy.paired((rows["market_twcrps"] - rows["twcrps"]).to_numpy(float), rows["expiry"], rows["symbol"])
    np.testing.assert_allclose(whole["twcrps_ci_low"], 100 * owner["ci95"][0] / whole["versus_twcrps"])
    assert "luck_baseline" not in gate and "cells_passing" not in gate
    with pytest.raises(IntegrityError):
        pipe.score_table(quarters=[qf.Quarter.parse("2024Q1")])


# --- a fit failure is retried, never a terminal state (review, 2026-11-05) -----------------------------

def _planned(tmp, **changes):
    path = _world(tmp)
    if changes:
        path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))
    qf.main(["plan", str(path)])
    return path, qf.Pipeline.load(path)


@pytest.mark.parametrize("step", ["_fit", "_score"])
def test_a_transient_fit_error_publishes_nothing_is_recorded_and_the_retry_succeeds(tmp_path, monkeypatch, step):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    with monkeypatch.context() as patch:
        def oom(*args):
            raise RuntimeError("CUDA out of memory")
        patch.setattr(CDFExperiment, step, oom)
        with pytest.raises(RuntimeError, match="out of memory"):
            pipe.run_quarter(q)
    gc.collect()                # the failed run's queue lock closes with its traceback, as a new process would
    root = pipe.fit_root(q)
    assert not (root / "skipped").exists() and not (root / "completed").exists()
    record = json.loads((pipe.plan_root / "fit-failures" / "2023Q4.jsonl").read_text().splitlines()[0])
    assert record["error"] == "RuntimeError" and "out of memory" in record["reason"]
    pipe.run_quarter(q)
    assert pipe.forecast_store(q).path.exists() and (root / "completed").exists()


def test_a_sealed_fit_failure_keeps_the_type_and_words_and_no_number(tmp_path, monkeypatch):
    _, pipe = _planned(tmp_path)

    def fail(*args):
        raise ValueError("CUDA out of memory: loss -0.1234e-2 on 2024-02-05 at z=1.5")
    monkeypatch.setattr(CDFExperiment, "_fit", fail)
    with pytest.raises(RuntimeError) as caught:
        pipe.run_quarter(qf.Quarter.parse("2024Q1"))
    gc.collect()
    record = json.loads((pipe.plan_root / "fit-failures" / "2024Q1.jsonl").read_text().splitlines()[0])
    assert record["error"] == "ValueError" and "CUDA out of memory" in record["reason"]
    assert not re.search(r"\d", record["reason"]) and not re.search(r"1234|02-05", str(caught.value))
    assert caught.value.__suppress_context__


def test_a_sealed_fit_that_never_moved_its_weights_publishes_nothing(tmp_path, monkeypatch):
    """Telemetry is off for sealed fits, so the guard must not depend on it."""
    import torch

    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2024Q1")
    assert pipe.fit_document(q)["training_telemetry"] is False
    monkeypatch.setattr(torch.optim.AdamW, "step", lambda self, closure=None: None)
    with pytest.raises(RuntimeError, match="no learned parameter movement"):
        pipe.run_quarter(q)
    gc.collect()
    assert not pipe.forecast_store(q).path.exists() and not (pipe.fit_root(q) / "completed").exists()


def test_a_document_without_the_switch_keeps_the_skip_behaviour(tmp_path, monkeypatch):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    document = pipe.fit_document(q)
    assert document["raise_fit_errors"] is True
    del document["raise_fit_errors"]
    document["output"] = str(tmp_path / "other")

    def oom(*args):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(CDFExperiment, "_fit", oom)
    CDFExperiment(document).run("pooled")
    root = tmp_path / "other" / "pooled" / "context_chain" / "fixed_loc_market_only_mlp64" / str(q.fold) / "pooled"
    assert (root / "skipped" / "reason.json").exists()


def test_an_old_skipped_store_is_kept_and_refused_not_deleted(tmp_path):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    (pipe.fit_root(q) / "skipped").mkdir(parents=True)
    with pytest.raises(IntegrityError, match="skipped"):
        pipe._prove_fit(q)
    assert (pipe.fit_root(q) / "skipped").exists()


def test_a_gpu_run_refuses_without_the_deterministic_workspace(tmp_path, monkeypatch):
    _, pipe = _planned(tmp_path)
    pipe.config["device"] = "cuda"
    monkeypatch.delenv("CUBLAS_WORKSPACE_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="CUBLAS_WORKSPACE_CONFIG"):
        pipe.run_quarter(qf.Quarter.parse("2023Q4"))
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    pipe._gates()


# --- the extension's proof is bound into the plan ------------------------------------------------------

def test_a_sealed_plan_needs_a_passed_verify_of_these_exact_panels(tmp_path):
    path = _world(tmp_path)
    (tmp_path / "verify.json").unlink()
    with pytest.raises(IntegrityError, match="verify"):
        qf.main(["plan", str(path)])
    _write_verify(tmp_path, passed=False)
    with pytest.raises(IntegrityError, match="verify"):
        qf.main(["plan", str(path)])
    _write_verify(tmp_path, passed=True, panel_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="verify"):
        qf.main(["plan", str(path)])
    _write_verify(tmp_path, passed=True, old_frame_sha256="0" * 64)
    with pytest.raises(IntegrityError, match="verify"):
        qf.main(["plan", str(path)])
    _write_verify(tmp_path, passed=True, synthetic=True)
    config = json.loads(path.read_text())
    config.update(synthetic=False, device="cuda", ticker_lock=str(qf.DEFAULT_LOCK), c_floor_gb=0, c_vhdx=str(tmp_path / "no"))
    real = qf.Pipeline(config)
    with pytest.raises(IntegrityError, match="synthetic"):
        real._refuse_unverified_extension({False: AtomicFitStore.file_hash(tmp_path / "frame-2023.parquet"),
                                           True: AtomicFitStore.file_hash(tmp_path / "frame-2025.parquet")})
    _write_verify(tmp_path, passed=True)
    qf.main(["plan", str(path)])
    assert (tmp_path / "plan" / "jobs-sealed.json").exists()


def test_a_changed_through_2023_row_fails_verify_and_so_refuses_the_plan(tmp_path):
    path = _world(tmp_path)
    frame = pd.read_parquet(tmp_path / "frame-2025.parquet")
    frame.loc[frame.quote_date == "2023-03-06", "terminal_return"] += 1e-9
    frame.to_parquet(tmp_path / "frame-2025.parquet", index=False)
    with pytest.raises(IntegrityError, match="verify"):          # the old proof is stale
        qf.main(["plan", str(path)])
    report = pe.verify_overlap(tmp_path / "frame-2023.parquet", tmp_path / "frame-2025.parquet", TICKERS)
    assert not report["passed"] and report["differing_columns"] == ["terminal_return"]
    _write_verify(tmp_path, passed=report["passed"])
    with pytest.raises(IntegrityError, match="verify"):
        qf.main(["plan", str(path)])


# --- no locked name vanishes silently (owner: one locked list, no silent cuts) -------------------------

def _late_name_world(tmp, exceptions=None):
    """BBB has quarter rows but too little history: the fit does not admit it."""
    frame = _panel_rows()
    frame = frame[(frame.symbol == "AAA") | (frame.quote_date >= "2023-12-25")]
    path = _world(tmp, panels=frame)
    template = json.loads((tmp / "fit-template.json").read_text())
    template["arms"]["context_chain"]["min_fit"] = 30
    (tmp / "fit-template.json").write_text(json.dumps(template))
    if exceptions:
        path.write_text(json.dumps({**json.loads(path.read_text()), "owner_name_exceptions": exceptions}))
    qf.main(["plan", str(path)])
    return qf.Pipeline.load(path)


def test_a_locked_name_with_panel_rows_but_no_forecast_refuses(tmp_path):
    pipe = _late_name_world(tmp_path)
    q = qf.Quarter.parse("2024Q1")
    with pytest.raises(IntegrityError, match="rows_match_panel|names_covered"):
        pipe.run_quarter(q)
    assert not pipe.forecast_store(q).path.exists()


def test_a_written_owner_exception_admits_the_gap_and_the_receipt_records_it(tmp_path):
    pipe = _late_name_world(tmp_path, {"BBB": "owner ruling, test only"})
    q = qf.Quarter.parse("2024Q1")
    pipe.run_quarter(q)
    receipt = json.loads((pipe.forecast_store(q).path / "receipt.json").read_text())
    assert receipt["owner_name_exceptions"] == {"BBB": "owner ruling, test only"} and receipt["missing_names"] == ["BBB"]
    assert receipt["expected_names"] == ["AAA"]


def test_structure_reports_the_missing_name_and_only_its_name(built):
    _, _, pipe = built
    q = qf.Quarter.parse("2024Q1")
    frame = pd.read_parquet(_forecast(built, "2024Q1") / "forecasts.parquet")
    report = pipe.structure(q, frame[frame.symbol == "AAA"].reset_index(drop=True))
    assert report["names_missing"] == ["BBB"] and report["names_covered"] is False and report["rows_match_panel"] is False


@pytest.mark.parametrize("bad", [{"ZZZ": "ruling"}, {"AAA": ""}])
def test_an_exception_must_name_a_locked_name_and_a_ruling(tmp_path, bad):
    path = _world(tmp_path)
    path.write_text(json.dumps({**json.loads(path.read_text()), "owner_name_exceptions": bad}))
    with pytest.raises(ValueError, match="exception"):
        qf.Pipeline.load(path).plan()


# --- disk and privacy at plan time ---------------------------------------------------------------------

class _Usage:
    free = 5 * 1024**3


def _quarters_with_floor(tmp, monkeypatch, vhdx):
    monkeypatch.setattr(qf.shutil, "disk_usage", lambda _: _Usage)
    path = _world(tmp)
    path.write_text(json.dumps({**json.loads(path.read_text()), "c_floor_gb": 4.9, "c_vhdx": str(vhdx)}))
    return path


def test_plan_refuses_when_the_projected_writes_pass_the_c_floor(tmp_path, monkeypatch):
    vhdx = tmp_path / "ext4.vhdx"
    vhdx.write_text("x")
    path = _quarters_with_floor(tmp_path, monkeypatch, vhdx)
    with pytest.raises(IntegrityError, match="C:"):
        qf.main(["plan", str(path)])
    assert not (tmp_path / "plan" / "plan.json").exists()


def test_plan_passes_once_the_wsl_disk_left_c(tmp_path, monkeypatch):
    path = _quarters_with_floor(tmp_path, monkeypatch, tmp_path / "moved-away.vhdx")
    qf.main(["plan", str(path)])
    assert (tmp_path / "plan" / "plan.json").exists()


def test_a_drive_root_costs_c_only_if_it_is_c():
    floor = qf.CFloor({"c_floor_gb": 3, "c_vhdx": None})
    assert floor.on_c("/mnt/c/x") and not floor.on_c("/mnt/d/x") and not floor.on_c("/home/x")
    assert qf.CFloor({"c_floor_gb": 3, "c_vhdx": __file__}).on_c("/home/x")


def test_plan_refuses_a_sealed_root_where_chmod_did_not_take(tmp_path, monkeypatch):
    path = _world(tmp_path)
    (tmp_path / "sealed").mkdir(mode=0o755)
    monkeypatch.setattr(qf.os, "chmod", lambda *a: None)
    with pytest.raises(IntegrityError, match="private"):
        qf.main(["plan", str(path)])


# --- stale pending folders, resume, pins ---------------------------------------------------------------

def test_a_stale_pending_shard_is_not_read_as_scores(tmp_path):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    before, _ = pipe.mixture_rows(q)
    pending = pipe.fit_root(q) / "scores" / "AAA.pending-x"
    pending.mkdir()
    (pending / "scores.parquet").write_bytes(b"not a parquet")
    after, _ = pipe.mixture_rows(q)
    pd.testing.assert_frame_equal(before, after)


def test_pin_resumes_and_skips_a_stale_pending_folder(tmp_path):
    path, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    pending = pipe.forecast_dir(q).with_name(q.name + ".pending-x")
    pending.mkdir()
    (pending / "forecasts.parquet").write_bytes(b"junk")
    root = tmp_path / "ob"
    arguments = ["pin", str(path), "--onboarding-root", str(root), "--source", "synthetic-quarterly-forecasts"]
    qf.main(arguments)
    qf.main(arguments)
    binding = json.loads((Path(pipe.config["open_root"]) / f"mio-bindings-{pipe.recipe.identity[:12]}" / "2023Q4.json").read_text())
    assert sorted(binding["forecasts"]["pin"]["files"]) == ["2023Q4/contexts.parquet", "2023Q4/forecasts.parquet",
                                                            "2023Q4/receipt.json"]


# --- the plan is bound at run time, and the receipt carries the owner's disclosures --------------------

def test_a_changed_recipe_after_the_plan_refuses_at_run_time(tmp_path):
    path, pipe = _planned(tmp_path)
    (tmp_path / "recipe.json").write_text(json.dumps({**RECIPE, "learning_rate": 1e-3}))
    with pytest.raises(IntegrityError, match="recipe"):
        qf.main(["quarter", str(path), "2023Q4"])


def test_score_caches_change_with_the_fit_document(tmp_path):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    method, full = pipe.method_score_path(q), pipe.full_scorer_path(q)
    document = pipe._doc_path(q, "fit")
    assert AtomicFitStore.file_hash(document)[:12] in str(method) and AtomicFitStore.file_hash(document)[:12] in str(full)
    document.write_text(document.read_text().replace('"epochs": 1', '"epochs": 2'))
    assert pipe.method_score_path(q) != method and pipe.full_scorer_path(q) != full


def test_the_receipt_carries_hashes_hardware_and_the_open_questions(built):
    _, _, pipe = built
    for name, expiry in (("2023Q4", "2024-01-01"), ("2024Q1", "2025-11-29")):
        published = pipe.forecast_store(qf.Quarter.parse(name)).path
        receipt = json.loads((published / "receipt.json").read_text())
        assert receipt["forecasts_sha256"] == AtomicFitStore.file_hash(published / "forecasts.parquet")
        assert receipt["contexts_sha256"] == AtomicFitStore.file_hash(published / "contexts.parquet")
        assert receipt["hardware"] == {"device": "cpu"} and receipt["synthetic"] is True
        assert len(receipt["ticker_lock_sha256"]) == 64
        assert expiry in receipt["coverage_note"] and "A0008" in receipt["seed_average_status"]


def test_the_gpu_receipt_names_model_driver_and_cuda(built, monkeypatch):
    import torch

    _, _, pipe = built
    monkeypatch.setitem(pipe.config, "device", "cuda")
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda _: "synthetic gpu")
    monkeypatch.setattr(torch.version, "cuda", "12.1")
    monkeypatch.setattr(qf.subprocess, "run", lambda *a, **k: type("Done", (), {"stdout": "999.9\n"}))
    assert pipe._hardware() == {"device": "cuda", "gpu_model": "synthetic gpu", "driver": "999.9", "cuda_version": "12.1"}


def test_a_real_run_must_name_the_c_floor(tmp_path):
    path = _world(tmp_path)
    config = {**json.loads(path.read_text()), "synthetic": False, "device": "cuda", "ticker_lock": str(qf.DEFAULT_LOCK)}
    with pytest.raises(ValueError, match="c_floor_gb"):
        qf.Pipeline(config)


# --- surviving mutations (review, 2026-11-05) ----------------------------------------------------------

@pytest.fixture()
def fitted(tmp_path):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    return pipe, q, pipe.fit_root(q) / "completed" / "summary.json"


def _tamper(path, change):
    summary = json.loads(path.read_text())
    change(summary)
    path.write_text(json.dumps(summary))


def test_the_fit_proof_refuses_short_training(fitted):
    pipe, q, summary = fitted
    pipe._prove_fit(q)
    _tamper(summary, lambda s: s.update(total_epochs=[0, 1]))
    with pytest.raises(IntegrityError, match="settings"):
        pipe._prove_fit(q)


@pytest.mark.parametrize("field, value", [("hidden_size", 80), ("width", 40)])
def test_the_fit_proof_compares_parsed_values_not_text(fitted, field, value):
    pipe, q, summary = fitted

    def change(saved):
        params = saved["settings"]["encoder"]["adapter"]["params"]
        (params["arch_params"] if field == "hidden_size" else params)[field] = value
    _tamper(summary, change)
    with pytest.raises(IntegrityError, match="settings"):
        pipe._prove_fit(q)


def test_a_sealed_fit_holding_scores_refuses(built, tmp_path):
    _, _, pipe = built
    q = qf.Quarter.parse("2024Q1")
    pipe._prove_fit(q)
    scores = pipe.fit_root(q) / "scores"
    scores.mkdir()
    try:
        with pytest.raises(IntegrityError, match="scores"):
            pipe._prove_fit(q)
    finally:
        scores.rmdir()


def test_the_scorer_refuses_a_sealed_style_document(built, tmp_path):
    _, _, pipe = built
    sealed = pipe._doc_path(qf.Quarter.parse("2024Q1"), "fit")
    params = copy.deepcopy(pipe.full_scorer_document(qf.Quarter.parse("2023Q4"))["pipeline"]["scores"]["params"])
    params.update(experiment_document=str(sealed), panel=pipe.config["panel_sealed"], end_before=qf.FOREVER_SEAL,
                  output=str(tmp_path / "sealed-scores"))
    with pytest.raises(ValueError, match="2024-01-01"):
        StrikeRegionScores("scores", params).run(None, {})
    assert not (tmp_path / "sealed-scores").exists()


def test_fit_stage_forecasts_equal_the_forecast_method_on_the_same_rows(built, tmp_path):
    _, _, pipe = built
    q = qf.Quarter.parse("2023Q4")
    published = pd.read_parquet(pipe.forecast_store(q).path / "forecasts.parquet")
    document = pipe.fit_document(q)
    document.update(date_upper_exclusive=q.start, end_upper_exclusive=q.start, training_telemetry=False,
                    output=str(tmp_path / "fit-only"))
    experiment = CDFExperiment(document)
    experiment.run("pooled")
    spec = {"fold": q.fold, "arm": "context_chain", "candidate": "fixed_loc_market_only_mlp64", "start": q.start,
            "end_before": q.end_before, "z_grid": pipe.grid.z, "grid_labels": pipe.grid.labels,
            "output": str(tmp_path / "components"), "batch_rows": 32, "proxy": pipe.config["proxy"],
            "calibration": pipe.config["calibration"]}
    experiment.forecast(spec)
    other = pd.read_parquet(tmp_path / "components" / "forecasts.parquet")
    joined = published.merge(other, on=qf.KEYS, validate="one_to_one", suffixes=("", "_other"))
    assert len(joined) == len(published) > 0
    labels = pipe.grid.labels
    np.testing.assert_allclose(joined[[f"method_cdf_{x}" for x in labels]].to_numpy(float),
                               joined[[f"model_cdf_{x}" for x in labels]].to_numpy(float), atol=1e-6)


def test_training_telemetry_defaults_on_and_absence_leaves_the_identity_alone(built):
    _, _, pipe = built
    document = pipe.fit_document(qf.Quarter.parse("2024Q1"))
    assert document["training_telemetry"] is False
    arm = document["arms"]["context_chain"]
    adapter = document["candidates"][0]["adapters"]["pooled"][0]
    off = CDFExperiment(document)
    assert off._settings(arm, adapter, "pooled", TICKERS, 100, 2)["training_telemetry"] is False
    without = {k: v for k, v in document.items() if k != "training_telemetry"}
    on = CDFExperiment(without)
    assert on._settings(arm, adapter, "pooled", TICKERS, 100, 2)["training_telemetry"] is True
    assert on.identity == value_hash(without) and on.identity != off.identity
    assert pipe.fit_document(qf.Quarter.parse("2023Q4")).get("training_telemetry", True) is True


def test_the_largest_tuned_recipe_fits_the_real_template_budget():
    """HPO may pick hidden 128 and width 32; five seeds must still pass the fit's parameter budget."""
    template = json.loads((CONFIGS / "quarterly-forecasts-run.json").read_text())["fit_template"]
    if not Path(template).exists():
        pytest.skip("the zoo's fit document is not on this machine")
    document = json.loads(Path(template).read_text())
    qf.Recipe({**RECIPE, "hidden": 128, "width": 32, "seeds": [11, 29, 47, 53, 59]}).apply(document)
    arm = next(iter(document["arms"].values()))
    adapter = document["candidates"][0]["adapters"]["pooled"][0]
    training = dict(document["training"])
    model = TorchCDF(encoder={"kind": "external", "adapter": adapter, "sequence_indices": arm["sequence_indices"],
                              "context_indices": arm["context_indices"]},
                     hidden=[document["capacity"]["pooled"]["head_widths"][0]], mask_constant_features=True,
                     device="cpu", feature_indices=list(range(len(arm["features"]))), **training)
    assert model.parameter_count(len(arm["features"])) <= document["capacity"]["pooled"]["absolute"]


def test_real_configs_parse_with_the_new_keys():
    run = json.loads((CONFIGS / "quarterly-forecasts-run.json").read_text())
    assert set(run) <= qf.Pipeline.KEYS | qf.Pipeline.OPTIONAL and qf.Pipeline.KEYS <= set(run)
    assert run["panel_verify"] is None and run["panel_sealed"] is None and run["owner_name_exceptions"] == {}
    extension = json.loads((CONFIGS / "quarterly-panel-extension.json").read_text())
    pe.PanelExtension(extension)
    assert "final_d" not in extension and not extension["work_root"].startswith("/mnt/")


# --- the owner's finer second grid for MIO, through 2023 only -----------------------------------------------

FINE = [z / 4 for z in range(-12, 13)]


def _fine_world(tmp, average="linear_pool"):
    path = _world(tmp, recipe={**RECIPE, "average": average})
    path.write_text(json.dumps({**json.loads(path.read_text()), "fine_z_grid": FINE}))
    qf.main(["plan", str(path)])
    pipe = qf.Pipeline.load(path)
    for quarter in QUARTERS:
        pipe.run_quarter(qf.Quarter.parse(quarter))
    return path, pipe, qf.Quarter.parse("2023Q4")


def test_the_fine_grid_is_the_mixture_at_every_quarter_z_and_leaves_the_13_points_alone(tmp_path, built):
    path, pipe, q = _fine_world(tmp_path)
    published = pipe.forecast_store(q).path
    coarse = pd.read_parquet(published / "forecasts.parquet")
    pd.testing.assert_frame_equal(coarse, pd.read_parquet(_forecast(built, "2023Q4") / "forecasts.parquet"))
    fine = pd.read_parquet(published / pipe.FINE_FILE)
    assert len(pipe.fine.labels) == 25 and {"method_cdf_zm2.75", "method_cdf_zp0.25"} <= set(fine)
    assert set(fine) == {*qf.IDENTITY, "fitted", *pipe.fine.columns}
    _, (w, m, s) = pipe.mixture_rows(q)
    np.testing.assert_allclose(fine[pipe.fine.columns].to_numpy(float), MixtureCurve(w, m, s).cdf(FINE), atol=1e-12)
    np.testing.assert_array_equal(fine[pipe.grid.columns].to_numpy(float), coarse[pipe.grid.columns].to_numpy(float))
    assert (np.diff(fine[pipe.fine.columns].to_numpy(float), axis=1) >= -qf.MONOTONE_TOLERANCE).all()
    receipt = json.loads((published / "receipt.json").read_text())
    assert receipt["fine_grid"]["sha256"] == AtomicFitStore.file_hash(published / pipe.FINE_FILE)
    assert receipt["fine_grid"] == {"file": "forecasts-fine.parquet", "sha256": AtomicFitStore.file_hash(published / pipe.FINE_FILE),
                                    "z_grid": FINE, "grid_labels": ["z" + qf.zlabel(z) for z in FINE],
                                    "seed_average": "linear_pool", "scored": False}
    assert set(pipe.fine.columns) == {"method_cdf_" + label for label in receipt["fine_grid"]["grid_labels"]}
    assert pd.read_parquet(published / receipt["fine_grid"]["file"]).shape[0] == receipt["rows"]
    assert receipt["z_grid"] == Z_GRID and receipt["forecasts_sha256"] == AtomicFitStore.file_hash(published / "forecasts.parquet")
    report = pipe.check()
    assert report["open"]["2023Q4"]["failed"] == [] and report["open"]["2023Q4"]["fine_monotone"] is True


def test_a_quantile_average_fine_grid_is_its_own_average_close_at_shared_points(tmp_path):
    _, pipe, q = _fine_world(tmp_path, "quantile_average")
    published = pipe.forecast_store(q).path
    coarse, fine = pd.read_parquet(published / "forecasts.parquet"), pd.read_parquet(published / pipe.FINE_FILE)
    a, b = coarse[pipe.grid.columns].to_numpy(float), fine[pipe.grid.columns].to_numpy(float)
    assert np.abs(a - b).max() < .02 and not np.array_equal(a, b)      # the rule in SeedAverage's docstring
    assert (np.diff(fine[pipe.fine.columns].to_numpy(float), axis=1) >= -qf.MONOTONE_TOLERANCE).all()


def test_no_sealed_quarter_gets_a_fine_file(tmp_path):
    _, pipe, _ = _fine_world(tmp_path)
    published = pipe.forecast_store(qf.Quarter.parse("2024Q1")).path
    assert not (published / pipe.FINE_FILE).exists()
    assert "fine_grid" not in json.loads((published / "receipt.json").read_text())


def test_the_fine_binding_carries_its_file_labels_and_hash_and_the_13_point_binding_stays(tmp_path):
    path, pipe, q = _fine_world(tmp_path)
    qf.main(["pin", str(path), "--onboarding-root", str(tmp_path / "ob"), "--source", "synthetic-quarterly-forecasts"])
    root = Path(pipe.config["open_root"])
    coarse = json.loads((root / f"mio-bindings-{pipe.recipe.identity[:12]}" / "2023Q4.json").read_text())
    fine = json.loads((root / f"mio-bindings-{pipe.recipe.identity[:12]}-fine" / "2023Q4.json").read_text())
    assert set(coarse) == {"forecasts", "contexts", "receipt"} and coarse["forecasts"]["files"]["shared"] == "2023Q4/forecasts.parquet"
    assert fine["forecasts"]["files"]["shared"] == "2023Q4/forecasts-fine.parquet" and fine["contexts"] == coarse["contexts"]
    assert fine["grid"]["grid_labels"] == pipe.fine.labels
    assert fine["grid"]["forecasts_sha256"] == AtomicFitStore.file_hash(pipe.forecast_store(q).path / pipe.FINE_FILE)
    frame = pd.read_parquet(PinnedPayloadSource(fine["forecasts"]["pin"]).file(fine["forecasts"]["files"]["shared"], verify=True))
    assert len(frame) > 0


def test_mio_bridge_accepts_the_25_labels_and_a_grid_missing_a_scored_point_refuses(tmp_path):
    from index_options.nodes import CDFQuoteCondorSelect

    config = json.loads((CONFIGS / "mio-2019-broad212.json").read_text())
    bridge = json.dumps(config)
    found = re.search(r'"bridge": ', bridge)
    decoded, _ = json.JSONDecoder().raw_decode(bridge[found.end():])
    labels = ["z" + label for label in map(qf.zlabel, FINE)]
    params = {**decoded, "z_grid": FINE, "grid_labels": labels, "symbol": "AAA"}
    assert [p for p in CDFQuoteCondorSelect.validate_params(params) if "grid" in p] == []
    assert not [p for p in CDFQuoteCondorSelect.validate_params({**decoded, "symbol": "AAA"}) if "grid" in p]
    with pytest.raises(ValueError, match="fine_z_grid"):
        qf.ForecastGrid({"z_grid": Z_GRID, "score_weights": SCORE_WEIGHTS, "tail": 4., "levels": 39, "batch_rows": 32}).finer(
            [-3, -1, 1, 3])
    run = json.loads((CONFIGS / "quarterly-forecasts-run.json").read_text())
    assert run["fine_z_grid"] == FINE and run["grid"]["z_grid"] == Z_GRID


def test_the_real_templates_become_documents_the_owners_accept(tmp_path):
    """Only documents and pins are read here: no panel, no fit."""
    run = json.loads((CONFIGS / "quarterly-forecasts-run.json").read_text())
    if not Path(run["fit_template"]).exists():
        pytest.skip("the zoo's documents are not on this machine")
    (tmp_path / "recipe.json").write_text(json.dumps({**RECIPE, "seeds": [11, 29, 47, 53, 59]}))
    repo = str(Path(qf.__file__).resolve().parents[3])
    (tmp_path / "panel").write_text("x")
    config = {**run, "recipe": str(tmp_path / "recipe.json"), "plan_root": str(tmp_path / "plan"), "repo": repo,
              "open_root": str(tmp_path / "open"), "sealed_root": str(tmp_path / "sealed"), "scores_root": str(tmp_path / "scores")}
    pipe = qf.Pipeline(config)
    (tmp_path / "plan" / "docs").mkdir(parents=True)
    folds = tmp_path / "folds.jsonl"
    template = json.loads(Path(run["fit_template"]).read_text())
    for name in ("2020Q1", "2025Q4"):
        q = qf.Quarter.parse(name)
        folds.write_text(json.dumps({"fold": q.fold, "role": "scored", "train_start": run["train_start"], "train_end": q.train_end,
                                     "val_start": q.start, "val_end": q.val_end}) + "\n")
        document = pipe._make_fit(q, template, str(tmp_path / "panel"), AtomicFitStore.file_hash(tmp_path / "panel"), folds)
        assert pipe._lock().check(document) >= 1
        assert CDFExperiment(document).identity and document["raise_fit_errors"] is True
        if q.sealed:
            assert document["training_telemetry"] is False and set(pipe._make_forecast(q, document)) >= {"proxy", "calibration"}
        else:
            path = pipe._doc_path(q, "fit")
            path.write_text(json.dumps(document))
            params = pipe._make_score(q, json.loads(Path(run["score_template"]).read_text()), path, "x")["pipeline"]["scores"]["params"]
            assert params["end_before"] == qf.METRIC_SEAL and params["z_grid"] == run["grid"]["z_grid"]


# --- review, 2026-11-05: the 2019 quarters, the rerun check, cache keys and completeness ----------------------

def _world_2019(tmp, **changes):
    """Rows in 2019 and in 2023, so the tuned quarter and one untuned quarter run the same open path."""
    early = _panel_rows()
    for column in ("quote_date", "expiry", "settlement_date"):
        early[column] = (pd.to_datetime(early[column]) - pd.Timedelta(days=364 * 4)).dt.strftime("%Y-%m-%d")
    early = early[early.quote_date < "2020-01-01"].copy()
    early["decision_context"] = [json.dumps({**json.loads(text), "identity": [a, b, c]}) for text, a, b, c in
                                 zip(early["decision_context"], early["symbol"], early["quote_date"], early["expiry"])]
    path = _world(tmp, panels=pd.concat([early, _panel_rows()], ignore_index=True))
    config = {**json.loads(path.read_text()), "train_start": "2019-01-01", "quarters": ["2019Q4", "2023Q4"],
              "fine_z_grid": FINE, **changes}
    path.write_text(json.dumps(config))
    qf.main(["plan", str(path)])
    return path, qf.Pipeline.load(path)


def test_2019_runs_the_open_path_with_the_fine_file_its_disclosure_and_scores_outside_the_gate(tmp_path):
    path, pipe = _world_2019(tmp_path)
    tuned, plain = qf.Quarter.parse("2019Q4"), qf.Quarter.parse("2023Q4")
    jobs = json.loads((pipe.plan_root / "jobs-open.json").read_text())["jobs"]
    assert [j["id"] for j in jobs] == ["forecast-2019Q4", "forecast-2023Q4", "score-2019Q4", "score-2023Q4"]
    fold = json.loads((pipe.plan_root / "folds" / "2019Q4.jsonl").read_text())
    assert fold == {"fold": 29, "role": "scored", "train_start": "2019-01-01", "train_end": "2019-09-30",
                    "val_start": "2019-10-01", "val_end": "2019-12-31"}
    for quarter in (tuned, plain):
        pipe.run_quarter(quarter)
        qf.main(["score", str(path), quarter.name])
    published = pipe.forecast_store(tuned).path
    receipt = json.loads((published / "receipt.json").read_text())
    assert receipt["bridge_fold"] == 29 and receipt["fine_grid"]["sha256"] == AtomicFitStore.file_hash(published / pipe.FINE_FILE)
    assert "tuned" in receipt["tuning_disclosure"] and "not independent evidence" in receipt["tuning_disclosure"]
    assert "tuning_disclosure" not in json.loads((pipe.forecast_store(plain).path / "receipt.json").read_text())
    assert pd.read_parquet(published / "forecasts.parquet")["fold"].eq(29).all()
    metadata = json.loads((pipe.fit_root(tuned) / "fit" / "metadata.json").read_text())
    assert metadata["band_dates"][0]["settlement_date"][1] < "2019-10-01"
    qf.main(["table", str(path)])
    table = pd.read_csv(pipe.score_table_path())
    assert set(table[table.level == "tuned_year"]["period"].astype(str)) == {"2019"}
    assert set(table[table.level == "year"]["period"].astype(str)) == {"2023"}      # the gate's years
    gate = json.loads(pipe.score_gate_path().read_text())
    rows = pipe._score_frame([tuned, plain])
    assert gate["tuned_rows_excluded"] == int(rows.quote_date.str.startswith("2019").sum()) > 0
    assert gate["rows"] == int(rows.quote_date.str.startswith("2023").sum()) == table[table.level == "all"]["rows"].iloc[0]


def test_the_rerun_check_has_its_own_job_id_roots_and_no_score_job(tmp_path):
    path, pipe = _planned(tmp_path)
    main_jobs = json.loads((pipe.plan_root / "jobs-open.json").read_text())
    jobs_path = qf.plan_rerun_check(path, "2023Q4")
    jobs = json.loads(jobs_path.read_text())
    assert jobs_path.parent == tmp_path / "rerun-check" / "plan"
    assert jobs["run_id"] != main_jobs["run_id"] and jobs["run_id"].startswith(main_jobs["run_id"] + "-rerun-")
    assert [j["id"] for j in jobs["jobs"]] == ["forecast-2023Q4"] and "rerun-check/plan/run.json" in jobs["jobs"][0]["command"]
    assert not (jobs_path.parent / "jobs-sealed.json").exists()
    rerun = qf.Pipeline.load(jobs_path.parent / "run.json")
    q = qf.Quarter.parse("2023Q4")
    for key in ("open_root", "sealed_root", "scores_root", "plan_root"):
        assert rerun.config[key].startswith(str(tmp_path / "rerun-check")) and rerun.config[key] != pipe.config[key]
    with pytest.raises(IntegrityError, match="rerun"):
        rerun.score_quarter(q)
    with pytest.raises(IntegrityError, match="not in the plan"):
        rerun.full_scorer_document(q)
    with pytest.raises(IntegrityError, match="MIO"):
        rerun.pin(tmp_path / "ob", "x")
    with pytest.raises(IntegrityError, match="exists"):
        qf.plan_rerun_check(path, "2023Q4")
    with pytest.raises(ValueError, match="one open quarter"):
        qf.Pipeline({**rerun.config, "quarters": ["2024Q1"]})
    # The runbook's comparison, on a synthetic fit.
    pipe.run_quarter(q)
    rerun.run_quarter(q)
    assert (AtomicFitStore.file_hash(rerun.forecast_store(q).path / "forecasts.parquet")
            == AtomicFitStore.file_hash(pipe.forecast_store(q).path / "forecasts.parquet"))


@pytest.mark.parametrize("change", [
    {"score_weights": [.5] + SCORE_WEIGHTS[1:]}, {"levels": 19}, {"tail": 5.0}])
def test_score_files_are_keyed_by_the_grid_settings(tmp_path, change):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    before = (pipe.method_score_path(q), pipe.full_scorer_path(q), pipe.score_table_path(), pipe.score_gate_path())
    other = qf.Pipeline({**pipe.config, "grid": {**pipe.config["grid"], **change}})
    after = (other.method_score_path(q), other.full_scorer_path(q), other.score_table_path(), other.score_gate_path())
    assert all(a != b for a, b in zip(before, after))
    with pytest.raises(IntegrityError, match="plan again"):
        other.full_scorer_document(q)


def test_score_files_are_keyed_by_the_score_template(tmp_path):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    before = pipe.method_score_path(q), pipe.full_scorer_path(q)
    template = Path(pipe.config["score_template"])
    template.write_text(template.read_text().replace('"chunk_rows": 32', '"chunk_rows": 16'))
    assert pipe.method_score_path(q) != before[0] and pipe.full_scorer_path(q) != before[1]
    with pytest.raises(IntegrityError, match="plan again"):
        pipe.full_scorer_document(q)


COARSE_PLUS = [-3, -2.5, -2, -1.5, -1, -.5, 0, .5, .75, 1, 1.5, 2, 2.5, 3]


@pytest.mark.parametrize("change", [
    {"fine_z_grid": [-3, -2.5, -2, -1.5, -1, -.5, 0, .25, .5, 1, 1.5, 2, 2.5, 3]},
    {"levels": 19}, {"tail": 5.0}])
def test_the_forecast_store_refuses_a_changed_grid(tmp_path, change):
    _, pipe = _planned(tmp_path, fine_z_grid=COARSE_PLUS)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    pipe.forecast_store(q).verify()
    changed = change if "fine_z_grid" in change else {"grid": {**pipe.config["grid"], **change}}
    with pytest.raises(IntegrityError):
        qf.Pipeline({**pipe.config, **changed}).forecast_store(q).verify()


def test_check_exits_nonzero_with_a_missing_quarter_and_zero_once_all_are_there(tmp_path, capsys):
    path, pipe = _planned(tmp_path)
    pipe.run_quarter(qf.Quarter.parse("2023Q4"))
    assert qf.main(["check", str(path)]) == 3
    assert '"2024Q1"' in capsys.readouterr().out
    pipe.run_quarter(qf.Quarter.parse("2024Q1"))
    assert qf.main(["check", str(path)]) == 0


def test_a_real_run_requires_all_25_quarters_and_pin_all_17_open_ones(tmp_path):
    path, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    pipe.config["synthetic"] = False
    assert len(pipe.required_quarters()) == 25
    with pytest.raises(IntegrityError, match="17 planned open quarters") as caught:
        pipe.pin(tmp_path / "ob", "x")
    assert "2019Q4" in str(caught.value) and "2023Q3" in str(caught.value) and "2024Q1" not in str(caught.value)
    assert "2019Q1" not in str(caught.value)
    assert not (tmp_path / "ob").exists()
    assert "2019Q4" in pipe.check()["missing"] and "2019Q1" not in pipe.check()["missing"]
    pipe.config["panel_sealed"] = None
    assert [q.name for q in pipe.required_quarters() if q.sealed] == []
    assert len(pipe.required_quarters()) == 17
    assert "2024Q1" not in pipe.check()["missing"]


def test_pin_refuses_a_missing_or_an_unplanned_quarter(tmp_path):
    path, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    with pytest.raises(IntegrityError, match="missing"):
        pipe.pin(tmp_path / "ob", "x")
    pipe.run_quarter(q)
    pipe.forecast_dir(q).with_name("2023Q3").mkdir()
    with pytest.raises(IntegrityError, match="unplanned"):
        pipe.pin(tmp_path / "ob", "x")


@pytest.mark.parametrize("gap, passes", [(5e-10, True), (2e-9, False)])
def test_both_forecast_files_share_one_stated_tolerance(built, gap, passes):
    _, _, pipe = built
    q = qf.Quarter.parse("2023Q4")
    assert qf.MONOTONE_TOLERANCE == 1e-9
    frame = pd.read_parquet(pipe.forecast_store(q).path / "forecasts.parquet")
    frame[pipe.grid.columns] = np.tile(np.linspace(.1, .9, 13), (len(frame), 1))
    frame["method_cdf_zp0.5"] = frame["method_cdf_zp0"] - gap
    assert pipe.structure(q, frame)["monotone"] is passes


# --- final fix pass, 2026-11-05: interrupted scorer, gate boolean, verified forecast, code commit ------------

def _scorer_world(tmp):
    path, pipe = _planned(tmp)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    return pipe, q, Path(pipe.config["scores_root"]) / "runs" / "2023Q4"


def test_a_scorer_retry_after_an_interruption_clears_only_that_quarters_unfinished_run(tmp_path):
    pipe, q, runs = _scorer_world(tmp_path)
    pipe.run_full_scorer(q)
    (run,) = list(runs.iterdir())
    assert json.loads((run / "result.json").read_text())["state"] == "ran"
    # A kill leaves the run dir without its result and the published output unwritten.
    shutil.rmtree(pipe.full_scorer_path(q))
    (run / "result.json").unlink()
    other = runs / "another-document-2023-12-31-abcd1234"
    other.mkdir()
    (other / "kept").write_text("x")
    pipe.run_full_scorer(q)             # fails with "run dir ... already exists" without the clearing
    assert (pipe.full_scorer_path(q) / "rows.parquet").exists() and (other / "kept").exists()
    (own,) = [d for d in runs.iterdir() if d.name.startswith("quarterly-scores-2023q4-")]
    assert json.loads((own / "result.json").read_text())["state"] == "ran"


def test_a_scorer_run_that_finished_is_never_removed_even_without_its_output(tmp_path):
    pipe, q, runs = _scorer_world(tmp_path)
    pipe.run_full_scorer(q)
    (run,) = list(runs.iterdir())
    shutil.rmtree(pipe.full_scorer_path(q))
    with pytest.raises(IntegrityError, match="finished scorer run"):
        pipe.run_full_scorer(q)
    assert (run / "result.json").exists()


def test_a_finished_run_of_an_older_plan_neither_blocks_nor_is_removed(tmp_path):
    pipe, q, runs = _scorer_world(tmp_path)
    pipe.run_full_scorer(q)
    (current,) = list(runs.iterdir())
    # Re-planned: the old run has another hash in its name and another output in its record, as a changed recipe gives.
    run = runs / (current.name[:-8] + "01d91a40")
    current.rename(run)
    record = json.loads((run / "config.json").read_text())
    record["pipeline"]["scores"]["params"]["output"] = str(tmp_path / "an-older-plan" / "2023Q4")
    (run / "config.json").write_text(json.dumps(record))
    unfinished = runs / (current.name[:-8] + "deadbeef")
    shutil.copytree(run, unfinished)
    (unfinished / "result.json").unlink()
    shutil.rmtree(pipe.full_scorer_path(q))
    pipe.run_full_scorer(q)             # a finished run of this plan would refuse; these are another plan's
    assert (pipe.full_scorer_path(q) / "rows.parquet").exists()
    assert (run / "result.json").exists() and unfinished.exists() and current.exists()


def test_a_scorer_retry_refuses_while_a_run_of_that_document_is_alive(tmp_path):
    pipe, q, runs = _scorer_world(tmp_path)
    pipe.run_full_scorer(q)
    (run,) = list(runs.iterdir())
    shutil.rmtree(pipe.full_scorer_path(q))
    (run / "result.json").unlink()
    partial = pipe.full_scorer_path(q).parent / f".{q.name}.partial"
    partial.mkdir(parents=True)
    document = pipe._doc_path(q, "score")
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "dskit.pipeline", "run", str(document)])
    try:
        with pytest.raises(IntegrityError, match="still alive"):
            pipe.run_full_scorer(q)
        assert run.exists() and partial.exists()         # nothing was cleared
    finally:
        live.kill()
        live.wait()
    pipe.run_full_scorer(q)             # the process is gone: the retry clears and runs
    assert (pipe.full_scorer_path(q) / "rows.parquet").exists()


def test_a_published_scorer_output_is_returned_and_its_run_left_alone(tmp_path):
    pipe, q, runs = _scorer_world(tmp_path)
    pipe.run_full_scorer(q)
    (run,) = list(runs.iterdir())
    before = sorted(p.name for p in run.iterdir())
    assert pipe.run_full_scorer(q) == pipe.full_scorer_path(q)
    assert sorted(p.name for p in run.iterdir()) == before


def _gate(tmp, monkeypatch, twcrps, crps, baseline_twcrps=None, baseline_crps=None):
    """Return the A0005 booleans for per-year gains over each reference.

    Each year of 2020-2023 has four rows whose model loss is the reference loss (1) minus the year's gain. Gains are
    dyadic, so every mean and percentage is exact. The baseline's gains default to the market's.
    """
    _, pipe = _planned(tmp)
    baseline_twcrps = twcrps if baseline_twcrps is None else baseline_twcrps
    baseline_crps = crps if baseline_crps is None else baseline_crps
    rows = []
    for i, year in enumerate(range(2020, 2024)):
        for symbol in ("AAA", "BBB"):
            for expiry in ("06-15", "06-22"):
                rows.append({"symbol": symbol, "quote_date": f"{year}-06-01", "expiry": f"{year}-{expiry}",
                             "actual_calendar_dte": 7.0, "twcrps": 1. - twcrps[i], "crps": 1. - crps[i],
                             "market_twcrps": 1., "market_crps": 1.,
                             "baseline_twcrps": 1. - twcrps[i] + baseline_twcrps[i],
                             "baseline_crps": 1. - crps[i] + baseline_crps[i]})
    frame = pd.DataFrame(rows)
    monkeypatch.setattr(pipe, "_score_frame", lambda quarters: frame)
    pipe.score_table(quarters=[qf.Quarter.parse("2023Q4")])
    return json.loads(pipe.score_gate_path().read_text())["a0005"]


@pytest.mark.parametrize("gains, passes", [
    ([.25, .25, .25, -.25], True),          # three of four years and a positive whole: the gate
    ([.25, .25, .25, .25], True),
    ([.5, .5, -.25, -.25], False),          # two of four years: too few (kills >= 3 turned into >= 2)
    ([.25, .25, .25, -.75], False),         # three years, whole exactly zero (kills whole >= 0)
    ([.25, .25, .25, -1.], False),          # three years, whole negative (kills dropping the whole test)
    ([.25, .25, 0., -.125], False),         # a year at exactly zero is not a gain (kills years >= 0)
    ([.25, .25, .25, 0.], True),            # three positive years, a zero year, a positive whole
])
def test_the_a0005_gate_needs_a_positive_whole_and_three_positive_years(tmp_path, monkeypatch, gains, passes):
    gate = _gate(tmp_path, monkeypatch, gains, gains)
    assert gate == {key: passes for key in ("market_twcrps", "market_crps", "baseline_twcrps", "baseline_crps")}


def test_the_a0005_gate_judges_each_metric_and_each_reference_on_its_own(tmp_path, monkeypatch):
    good, bad = [.25] * 4, [.5, .5, -.25, -.25]
    gate = _gate(tmp_path, monkeypatch, good, bad, baseline_twcrps=bad, baseline_crps=good)
    assert gate == {"market_twcrps": True, "market_crps": False, "baseline_twcrps": False, "baseline_crps": True}


def test_method_scores_verify_the_published_forecast_before_scoring_it(tmp_path):
    _, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    published = pipe.forecast_store(q).path / "forecasts.parquet"
    original = published.read_bytes()
    published.write_bytes(original + b"x")
    with pytest.raises(IntegrityError):
        pipe.method_scores(q)
    assert not pipe.method_score_path(q).exists()
    published.write_bytes(original)
    assert pipe.method_scores(q).exists()


def _git_repo(tmp):
    repo = tmp / "repo"
    (repo / "children/index_options/docs/decisioning").mkdir(parents=True)
    (repo / "code.py").write_text("x = 1\n")
    (repo / "children/index_options/docs/decisioning/actions.csv").write_text("id\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                                 "commit", "-qm", "c"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE, text=True)
    return repo, head.stdout.strip()


def test_a_real_run_needs_a_clean_tree_and_the_journal_files_do_not_count(tmp_path):
    repo, head = _git_repo(tmp_path)
    tree, real = qf.CodeTree(repo), {"synthetic": False}
    assert tree.require_clean(real) == head
    journal = repo / "children/index_options/docs/decisioning/actions.csv"
    journal.write_text("id\nA1\n")                 # a real run appends here by design
    assert tree.require_clean(real) == head
    (repo / "code.py").write_text("x = 2\n")
    with pytest.raises(IntegrityError, match="committed tree"):
        tree.require_clean(real)
    assert tree.require_clean({"synthetic": True}) is None and tree.commit() is None     # exempt, no commit named
    (repo / "code.py").write_text("x = 1\n")
    (repo / "new.py").write_text("y = 1\n")        # untracked code counts
    with pytest.raises(IntegrityError, match="committed tree"):
        tree.require_clean(real)


def test_a_synthetic_run_outside_git_is_exempt_and_a_real_one_refuses(tmp_path):
    assert qf.CodeTree(tmp_path).require_clean({"synthetic": True}) is None
    with pytest.raises(IntegrityError, match="git work tree"):
        qf.CodeTree(tmp_path).require_clean({})


def test_the_receipt_names_the_commit_of_a_clean_tree_and_no_commit_of_a_dirty_one(tmp_path):
    repo, head = _git_repo(tmp_path)
    _, pipe = _planned(tmp_path / "w")
    pipe.config["repo"] = str(repo)
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    assert json.loads((pipe.forecast_store(q).path / "receipt.json").read_text())["code_commit"] == head
    (repo / "code.py").write_text("x = 2\n")
    q2 = qf.Quarter.parse("2024Q1")
    pipe.run_quarter(q2)
    assert json.loads((pipe.forecast_store(q2).path / "receipt.json").read_text())["code_commit"] is None


def test_real_gates_and_pin_refuse_a_dirty_tree_before_any_work(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    _, pipe = _planned(tmp_path / "w")
    q = qf.Quarter.parse("2023Q4")
    pipe.run_quarter(q)
    pipe.config.update(synthetic=False, repo=str(repo))
    pipe._gates()                                     # clean: passes
    monkeypatch.setattr(pipe, "required_quarters", lambda: [q])
    (repo / "code.py").write_text("x = 2\n")
    with pytest.raises(IntegrityError, match="committed tree"):
        pipe._gates()
    with pytest.raises(IntegrityError, match="committed tree"):
        pipe.run_quarter(q)
    with pytest.raises(IntegrityError, match="committed tree"):
        pipe.pin(tmp_path / "ob", "x")
    assert not (tmp_path / "ob").exists()


def test_the_parity_plan_takes_another_recipe_into_its_own_folder_and_matches_a_direct_fit(tmp_path, monkeypatch):
    path, pipe = _planned(tmp_path)
    q = qf.Quarter.parse("2023Q4")
    other = tmp_path / "parity-recipe.json"
    other.write_text(json.dumps({**RECIPE, "seeds": [11], "average": "linear_pool"}))
    jobs_path = qf.plan_rerun_check(path, "2023Q4", other)
    assert jobs_path.parent == tmp_path / "parity-check" / "plan" and not (tmp_path / "rerun-check").exists()
    with pytest.raises(IntegrityError, match="exists"):
        qf.plan_rerun_check(path, "2023Q4", other)
    parity = qf.Pipeline.load(jobs_path.parent / "run.json")
    assert parity.config["recipe"] == str(other) and parity.recipe.seeds == [11]
    # A relative recipe path is stored absolute: the plan must not depend on where the verb was run.
    (tmp_path / "parity-check").rename(tmp_path / "gone")
    monkeypatch.chdir(tmp_path)
    again = qf.plan_rerun_check(path, "2023Q4", "parity-recipe.json")
    assert json.loads((again.parent / "run.json").read_text())["recipe"] == str(other.resolve())
    shutil.rmtree(tmp_path / "parity-check")
    (tmp_path / "gone").rename(tmp_path / "parity-check")
    # The same settings fit by the main run's own pipeline give the same weights file.
    direct = qf.Pipeline({**pipe.config, "recipe": str(other), "plan_root": str(tmp_path / "direct-plan"),
                          "open_root": str(tmp_path / "direct-open"), "sealed_root": str(tmp_path / "direct-sealed")})
    direct.plan_root.mkdir()
    (direct.plan_root / "run.json").write_text(json.dumps(direct.config))
    direct.plan()
    parity.run_quarter(q)
    direct.run_quarter(q)
    weights = lambda p: AtomicFitStore.file_hash(p.fit_root(q) / "fit" / "model" / "weights.pt")
    assert weights(parity) == weights(direct)
