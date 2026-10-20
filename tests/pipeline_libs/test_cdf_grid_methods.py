"""GridCDFMethod (ADR-0270): forecast methods on a CDF grid over a scored-rows table.

Every fixture is synthetic with a known answer: the truth is a normal or a known mixture, the forecasters are
normals with chosen location and scale, so a recalibration, a pool weight or a cell choice has a value the
test can state in advance. Expected numbers are recomputed here with plain numpy, never read back from the
node under test.
"""

import hashlib
import itertools
import json
import types

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
ndtr = pytest.importorskip("scipy.special").ndtr
ndtri = pytest.importorskip("scipy.special").ndtri

from dskit.pipeline.base import ConfigError, value_hash
from dskit.pipeline.libs import cdf_grid_methods as cgm
from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod
from dskit.pipeline.node import NodeContext

Z = [-3 + 0.5 * i for i in range(13)]
LABELS = [("zm" if z < 0 else "zp") + format(abs(z), "g") for z in Z]
COLUMNS = {"identity": ["row_id", "symbol", "quote_date"], "date": "quote_date", "settlement": "settlement_date",
           "fold": "fold", "band": "band", "outcome": "y", "dte": "actual_calendar_dte", "group": "symbol",
           "eligible": "market_eligible"}
BASE_DAY = np.datetime64("2020-01-01")
CTX = NodeContext(name="g", asof="2026-01-01", run_dir="/nonexistent")


def make_frame(rng, folds=(1, 2, 3), n_fit=300, n_val=300, symbols=("A", "B"), dte_hi=30, fold_days=60, spread=10):
    """One row per forecast: a fit block and a later val block for every fold."""
    parts = []
    for f in folds:
        for band, n, offset in (("fit", n_fit, 0), ("val", n_val, 20)):
            days = fold_days * f + offset + rng.integers(0, spread, n)
            quote = (BASE_DAY + days.astype("timedelta64[D]")).astype("datetime64[ns]")
            dte = rng.integers(1, dte_hi + 1, n)
            parts.append(pd.DataFrame({
                "symbol": rng.choice(list(symbols), n), "quote_date": quote, "actual_calendar_dte": dte,
                "settlement_date": quote + dte.astype("timedelta64[D]"), "fold": f, "band": band,
                "market_eligible": True}))
    df = pd.concat(parts, ignore_index=True)
    df.insert(0, "row_id", np.arange(len(df)))
    return df


def add_cdf(df, name, mu, sd):
    """Add the forecaster ``name``: a normal CDF with per-row or scalar location and scale."""
    mu, sd = np.broadcast_to(mu, len(df)), np.broadcast_to(sd, len(df))
    for z, label in zip(Z, LABELS):
        df[f"{name}_cdf_{label}"] = ndtr((z - mu) / sd)


def tw(F, y, w=None):
    """The analysis convention at spacing 0.5: 0.5 * (sum f - 0.5 * (f0 + f_last))."""
    w = np.ones(13) if w is None else np.asarray(w, dtype=float)
    ind = (np.asarray(y)[:, None] <= np.array(Z)[None, :]).astype(float)
    f = w * (np.asarray(F) - ind) ** 2
    return 0.5 * (f.sum(1) - 0.5 * (f[:, 0] + f[:, -1]))


def cdfs(df, name):
    return df[[f"{name}_cdf_{label}" for label in LABELS]].to_numpy()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def write_source(tmp_path, df, name="src.parquet"):
    path = tmp_path / name
    df.to_parquet(path, index=False)
    return path


def params_for(tmp_path, df, method, components=None, fallback="model", **over):
    path = write_source(tmp_path, df)
    base = {
        "source": str(path), "source_sha256": sha(path), "output": str(tmp_path / "out"),
        "end_before": "2030-01-01", "columns": json.loads(json.dumps(COLUMNS)), "z_grid": list(Z),
        "grid_labels": list(LABELS), "score_weights": [1.0] * 13, "pit_tail": 4.0,
        "components": components or [{"name": "market", "source": "mkt"}, {"name": "model", "source": "mdl"}],
        "fallback": fallback, "method": method, "tables": [], "comparators": ["market"],
        "ranked_folds": [1, 2, 3]}
    base.update(over)
    return base


def run(params):
    return GridCDFMethod("g", params).run(CTX, {})


def read_out(params):
    out = params["output"]
    forecasts = pd.read_parquet(f"{out}/forecasts.parquet")
    fit = json.load(open(f"{out}/fit.json"))
    summary = json.load(open(f"{out}/summary.json"))
    return forecasts, fit, summary


def joined(forecasts, df):
    """The forecasts with the source's outcome and the other columns, keyed by row_id."""
    extra = df.drop(columns=[c for c in df.columns if c in forecasts.columns and c != "row_id"])
    return forecasts.merge(extra, on="row_id", how="left")


def pool_frame(rng, n_fit=1500, n_val=400, weight=0.7, **kw):
    """Truth is the mixture ``weight`` * N(-1, .7) + (1 - weight) * N(1.5, .8); model and market are its parts."""
    df = make_frame(rng, n_fit=n_fit, n_val=n_val, **kw)
    n = len(df)
    first = rng.random(n) < (weight if np.isscalar(weight) else weight(df))
    df["y"] = np.where(first, rng.normal(-1, 0.7, n), rng.normal(1.5, 0.8, n))
    add_cdf(df, "mdl", -1.0, 0.7)
    add_cdf(df, "mkt", 1.5, 0.8)
    return df


# --------------------------------------------------------------------------------------------- the grid score


def test_grid_score_matches_the_analysis_formula_at_half_spacing():
    rng = np.random.default_rng(1)
    F = np.sort(rng.random((50, 13)), axis=1)
    y = rng.normal(size=50)
    w = rng.random(13)
    grid = cgm._Grid(Z, LABELS, w.tolist(), 4.0)
    assert np.allclose(grid.score(F, y), tw(F, y, w), atol=1e-14)
    assert np.allclose(grid.score(F, y, weights=np.ones(13)), tw(F, y), atol=1e-14)


def test_grid_score_is_a_trapezoid_with_the_actual_spacing():
    rng = np.random.default_rng(2)
    z = np.linspace(-3, 3, 25)  # spacing 0.25
    grid = cgm._Grid(z.tolist(), [f"p{i}" for i in range(25)], [1.0] * 25, 4.0)
    F = np.sort(rng.random((10, 25)), axis=1)
    y = rng.normal(size=10)
    f = (F - (y[:, None] <= z[None, :])) ** 2
    assert np.allclose(grid.score(F, y), 0.25 * (f.sum(1) - 0.5 * (f[:, 0] + f[:, -1])), atol=1e-14)


def test_grid_score_matches_a_fine_integral_of_a_step_weight():
    z = np.linspace(-3, 3, 241)
    step = (z > 0).astype(float)
    grid = cgm._Grid(z.tolist(), [f"p{i}" for i in range(241)], step.tolist(), 4.0)
    F = ((z + 3) / 6)[None, :]  # uniform on [-3, 3]; y at the left edge, so the indicator is one everywhere
    got = grid.score(F, np.array([-3.0]))[0]
    fine = np.linspace(0, 3, 300001)
    integrand = ((3 - fine) / 6) ** 2
    exact = float(np.sum((integrand[1:] + integrand[:-1]) / 2 * np.diff(fine)))
    assert exact == pytest.approx(0.25, abs=1e-6)
    assert got == pytest.approx(exact, abs=0.01)


def test_region_masses_partition_the_line():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    F = np.sort(np.random.default_rng(3).random((20, 13)), axis=1)
    masses = grid.region_masses(F)
    assert masses.shape == (20, 14)
    assert np.allclose(masses.sum(1), 1.0, atol=1e-14)
    assert np.allclose(masses[:, 0], F[:, 0]) and np.allclose(masses[:, -1], 1 - F[:, -1])
    assert list(grid.region_index(np.array([-9.0, -3.0, -2.9, 0.0, 3.0, 3.1]))) == [0, 0, 1, 6, 12, 13]


def test_pit_of_a_grid_forecast_interpolates_and_extrapolates_to_the_tails():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    F = np.tile(np.linspace(0.1, 0.9, 13), (4, 1))
    pit = grid.pit(F, np.array([-2.75, -7.0, 9.0, -5.0]))
    assert pit[0] == pytest.approx(0.1 + 0.5 * 0.8 / 12)  # halfway between two grid points
    assert pit[1] == pytest.approx(0.0)  # at z0 - tail
    assert pit[2] == pytest.approx(1.0)  # beyond z_last + tail
    assert pit[3] == pytest.approx(0.05)  # halfway along the linear tail


# ---------------------------------------------------------------------------------------------- PIT map


def test_pit_map_recalibrates_a_too_narrow_forecaster(tmp_path):
    rng = np.random.default_rng(10)
    df = make_frame(rng, n_fit=1500, n_val=600)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mkt", 0.0, 0.6)
    add_cdf(df, "mdl", 0.0, 1.0)
    params = params_for(tmp_path, df,
                        {"kind": "pit_map", "learn": "fit_band", "component": "market", "knots": 41,
                         "min_rows": 50})
    run(params)
    forecasts, fit, _ = read_out(params)
    full = joined(forecasts, df)
    val = full[full["band"] == "val"]
    method = val[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    raw = cdfs(val, "mkt")
    best = tw(cdfs(val, "mdl"), val["y"]).mean()  # the true forecaster
    assert tw(method, val["y"]).mean() < tw(raw, val["y"]).mean()
    assert tw(method, val["y"]).mean() < 1.02 * best
    freq = (val["y"].to_numpy()[:, None] <= np.array(Z)[None, :]).mean(0)
    assert np.abs(method.mean(0) - freq).max() < 0.045
    assert np.abs(raw.mean(0) - freq).max() > 0.09
    assert full["fitted"].all()
    assert fit["folds"]["2"]["learning_rows"] == 1500


# ----------------------------------------------------------------------------------------------- fixtures


@pytest.fixture
def pooled(tmp_path):
    rng = np.random.default_rng(20)
    return tmp_path, pool_frame(rng)


def pool_method(**over):
    method = {"kind": "linear_pool", "learn": "fit_band", "components": ["model", "market"], "min_rows": 20}
    method.update(over)
    return method


# ------------------------------------------------------------------------------------------- linear pool


def test_linear_pool_recovers_the_true_mixture_weight(pooled):
    tmp_path, df = pooled
    params = params_for(tmp_path, df, pool_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    weights = fit["folds"]["2"]["state"]["global"]["weights"]
    assert weights[0] == pytest.approx(0.7, abs=0.07) and sum(weights) == pytest.approx(1.0)
    full = joined(forecasts, df)
    got = full[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    w = np.array(fit["folds"]["2"]["state"]["global"]["weights"])
    fold2 = full["fold"] == 2
    expect = w[0] * cdfs(full, "mdl") + w[1] * cdfs(full, "mkt")
    assert np.allclose(got[fold2], expect[fold2], atol=1e-12)
    assert full["fitted"].all()


def brute_simplex(Q, step=0.01):
    """The minimizer of w'Qw over the simplex by exhaustive search on a regular lattice."""
    k = len(Q)
    best, arg = np.inf, None
    ticks = int(round(1 / step))
    for head in itertools.product(range(ticks + 1), repeat=k - 1):
        if sum(head) > ticks:
            continue
        w = np.array([*head, ticks - sum(head)]) / ticks
        value = w @ Q @ w
        if value < best:
            best, arg = value, w
    return best, arg


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("k", [2, 3, 4])
def test_simplex_solver_matches_brute_force(seed, k):
    rng = np.random.default_rng(100 * k + seed)
    A = rng.normal(size=(k + 2, k))
    Q = A.T @ A / (k + 2)
    w = cgm._SimplexQuadratic.solve(Q)
    assert w.min() >= -1e-12 and w.sum() == pytest.approx(1.0, abs=1e-12)
    best, _ = brute_simplex(Q, step=0.05 if k == 4 else 0.01)
    assert w @ Q @ w <= best + 1e-9


def test_simplex_solver_handles_identical_components():
    Q = np.ones((3, 3)) * 0.4 + np.diag([0.0, 0.0, 0.2])
    w = cgm._SimplexQuadratic.solve(Q)
    assert w.sum() == pytest.approx(1.0) and w[2] < 1e-9


def test_three_way_pool_reaches_the_brute_force_optimum_on_the_learning_rows(tmp_path):
    rng = np.random.default_rng(21)
    df = make_frame(rng, folds=(1, 2), n_fit=800, n_val=100)
    df["y"] = rng.normal(0.3, 1.1, len(df))
    add_cdf(df, "mdl", -0.5, 1.0)
    add_cdf(df, "mkt", 0.8, 0.9)
    add_cdf(df, "third", 0.0, 1.6)
    components = [{"name": "model", "source": "mdl"}, {"name": "market", "source": "mkt"},
                  {"name": "third", "source": "third"}]
    params = params_for(tmp_path, df, pool_method(components=["model", "market", "third"]),
                        components=components)
    run(params)
    _, fit, _ = read_out(params)
    got = np.array(fit["folds"]["2"]["state"]["global"]["weights"])
    rows = df[(df["fold"] == 2) & (df["band"] == "fit")]
    parts = [cdfs(rows, n) for n in ("mdl", "mkt", "third")]

    def objective(w):
        return tw(sum(wi * p for wi, p in zip(w, parts)), rows["y"].to_numpy()).mean()

    best = min(objective(np.array([a, b, 100 - a - b]) / 100)
               for a in range(0, 101, 2) for b in range(0, 101 - a, 2))
    assert objective(got) <= best + 1e-9
    assert got.sum() == pytest.approx(1.0)


def test_per_threshold_pool_gives_monotone_cdfs_inside_the_unit_interval(pooled):
    tmp_path, df = pooled
    params = params_for(tmp_path, df, pool_method(per_threshold=True))
    run(params)
    forecasts, fit, _ = read_out(params)
    cdf = forecasts[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    assert (np.diff(cdf, axis=1) >= -1e-12).all() and cdf.min() >= 0 and cdf.max() <= 1
    weights = np.array(fit["folds"]["2"]["state"]["global"]["weights"])
    assert weights.shape == (13, 2) and np.allclose(weights.sum(1), 1.0)
    assert weights.std(0).max() > 0  # the weights differ by threshold


def test_per_threshold_rearranges_a_crossing_pool_into_a_monotone_cdf():
    raw = np.array([[0.1, 0.5, 0.4, 0.9, 1.2, -0.1, 0.8, 0.9, 0.95, 0.99, 1.0, 1.0, 1.0]])
    fixed = cgm._rearranged(raw)
    assert (np.diff(fixed, axis=1) >= 0).all() and fixed.min() >= 0 and fixed.max() <= 1
    assert sorted(np.clip(raw[0], 0, 1)) == list(fixed[0])


def test_shrinkage_moves_bucket_weights_toward_the_global_ones(tmp_path):
    rng = np.random.default_rng(22)
    weight = lambda d: np.where(d["symbol"] == "A", 0.85, 0.35)  # noqa: E731
    df = pool_frame(rng, weight=weight, n_fit=1500)
    out = {}
    for tag, over in (("none", {}), ("some", {"shrink_rows": 500}), ("huge", {"shrink_rows": 1e9})):
        params = params_for(tmp_path, df, pool_method(by="symbol", **over), output=str(tmp_path / tag))
        run(params)
        out[tag] = read_out(params)[1]["folds"]["2"]["state"]
    glob = np.array(out["none"]["global"]["weights"])
    for symbol in ("A", "B"):
        hat = np.array(out["none"]["buckets"][symbol]["weights"])
        n = out["none"]["buckets"][symbol]["rows"]
        some = np.array(out["some"]["buckets"][symbol]["weights"])
        assert some == pytest.approx((n * hat + 500 * glob) / (n + 500), abs=1e-12)
        assert np.allclose(out["huge"]["buckets"][symbol]["weights"], glob, atol=1e-6)
    hat_a = out["none"]["buckets"]["A"]["weights"][0]
    hat_b = out["none"]["buckets"]["B"]["weights"][0]
    assert hat_a == pytest.approx(0.85, abs=0.08) and hat_b == pytest.approx(0.35, abs=0.08)


def test_buckets_with_too_few_learning_rows_use_the_global_weights(tmp_path):
    rng = np.random.default_rng(23)
    df = pool_frame(rng, symbols=("A", "B"), n_fit=900)
    rare = df.index[(df["fold"] == 2) & (df["band"] == "fit")][:5]
    df.loc[rare, "symbol"] = "C"
    params = params_for(tmp_path, df, pool_method(by="symbol", min_rows=50))
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]
    assert "C" not in state["buckets"] and {"A", "B"} <= set(state["buckets"])
    w = np.array(state["global"]["weights"])
    full = joined(forecasts, df)
    rows = (full["fold"] == 2) & (full["symbol"] == "C")
    expect = w[0] * cdfs(full, "mdl") + w[1] * cdfs(full, "mkt")
    got = full[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    assert rows.sum() == 5 and np.allclose(got[rows], expect[rows], atol=1e-12)


def test_quantile_buckets_learn_their_edges_on_the_learning_rows(tmp_path):
    rng = np.random.default_rng(24)
    df = pool_frame(rng, n_fit=900)
    df["vol"] = rng.random(len(df))
    params = params_for(tmp_path, df, pool_method(quantile_by={"column": "vol", "quantiles": [0.333, 0.667]}))
    run(params)
    _, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]["vol"].to_numpy()
    assert state["bucketing"]["edges"] == pytest.approx(np.quantile(learn, [0.333, 0.667]).tolist())
    assert len(state["buckets"]) == 3


# ------------------------------------------------------------------------- learning rows and the fallback


def purge_frame(rng):
    df = pool_frame(rng, n_fit=200, n_val=200, dte_hi=90)
    df.loc[rng.random(len(df)) < 0.05, "market_eligible"] = False
    return df


def expected_learning(df, fold, learn, used):
    finite = np.ones(len(df), bool)
    for name in used:
        finite &= np.isfinite(cdfs(df, name)).all(1)
    ok = finite & df["market_eligible"].to_numpy()
    if learn == "fit_band":
        return int((ok & (df["band"] == "fit") & (df["fold"] == fold)).sum())
    first = df.loc[(df["fold"] == fold) & (df["band"] == "val"), "quote_date"].min()
    return int((ok & (df["band"] == "val") & (df["fold"] < fold) & (df["settlement_date"] < first)).sum())


def test_prior_validation_excludes_rows_that_settle_inside_the_target_fold(tmp_path):
    df = purge_frame(np.random.default_rng(30))
    params = params_for(tmp_path, df, pool_method(learn="prior_validation"))
    run(params)
    _, fit, _ = read_out(params)
    for fold in ("2", "3"):
        want = expected_learning(df, int(fold), "prior_validation", ("mdl", "mkt"))
        assert fit["folds"][fold]["learning_rows"] == want > 0
    unpurged = int(((df["band"] == "val") & (df["fold"] < 3) & df["market_eligible"]).sum())
    assert fit["folds"]["3"]["learning_rows"] < unpurged  # the purge removed settled-late rows


def test_the_first_fold_has_no_prior_evidence_so_it_keeps_the_fallback(tmp_path):
    df = purge_frame(np.random.default_rng(31))
    params = params_for(tmp_path, df, pool_method(learn="prior_validation"), fallback="model")
    run(params)
    forecasts, fit, summary = read_out(params)
    full = joined(forecasts, df)
    first = full["fold"] == 1
    assert fit["folds"]["1"]["learning_rows"] == 0 and fit["folds"]["1"]["fitted"] is False
    assert not full.loc[first, "fitted"].any() and full.loc[~first, "fitted"].all()
    got = full[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    assert np.allclose(got[first], cdfs(full, "mdl")[first])
    assert summary["rows_not_fitted"] == int(first.sum())


def test_a_row_missing_a_component_falls_back_and_is_not_fitted(tmp_path):
    rng = np.random.default_rng(32)
    df = pool_frame(rng, n_fit=300, n_val=300)
    gap = df.index[(df["fold"] == 2) & (df["band"] == "val")][:10]
    df.loc[gap, [f"mdl_cdf_{label}" for label in LABELS]] = np.nan
    params = params_for(tmp_path, df, pool_method(), fallback="market")
    run(params)
    forecasts, _, summary = read_out(params)
    full = joined(forecasts, df)
    got = full[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    rows = full["row_id"].isin(df.loc[gap, "row_id"])
    assert rows.sum() == 10 and not full.loc[rows, "fitted"].any() and full.loc[~rows, "fitted"].all()
    assert np.allclose(got[rows], cdfs(full, "mkt")[rows])
    assert summary["rows_not_fitted"] == 10


def test_a_component_pit_map_is_learned_per_fold_on_that_folds_fit_band(tmp_path):
    rng = np.random.default_rng(33)
    df = make_frame(rng, n_fit=1200, n_val=300)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mkt", 0.0, 0.6)
    add_cdf(df, "mdl", 0.0, 1.0)
    components = [{"name": "market", "source": "mkt"}, {"name": "model", "source": "mdl"},
                  {"name": "market_cal", "source": "mkt", "pit_map": {"knots": 41, "learn": "fit_band"}}]
    params = params_for(tmp_path, df, pool_method(components=["model", "market_cal"]), components=components,
                        comparators=["market", "market_cal"])
    run(params)
    forecasts, fit, _ = read_out(params)
    cal = fit["components"]["market_cal"]
    assert sorted(cal) == ["1", "2", "3"] and cal["2"]["learning_rows"] == 1200
    assert len(cal["2"]["g"]) == 41 and cal["2"]["g"][-1] == pytest.approx(1.0)
    full = joined(forecasts, df)
    val = full[(full["band"] == "val") & (full["fold"] == 2)]
    method = val[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    assert tw(method, val["y"]).mean() < tw(cdfs(val, "mkt"), val["y"]).mean()


# -------------------------------------------------------------------------------------- best component


def cell_frame(rng):
    df = make_frame(rng, n_fit=500, n_val=200, symbols=("A", "B"))
    df["y"] = rng.normal(size=len(df))
    good_a = (df["symbol"] == "A").to_numpy()
    add_cdf(df, "mdl", 0.0, np.where(good_a, 1.0, 0.5))
    add_cdf(df, "mkt", 0.0, np.where(good_a, 0.5, 1.0))
    rare = df.index[(df["fold"] == 2) & (df["band"] == "fit")][:3]
    df.loc[rare, "symbol"] = "C"
    return df


def best_method(**over):
    method = {"kind": "best_component", "learn": "fit_band", "components": ["model", "market"],
              "by": ["symbol"], "default": "market", "min_rows": 20, "margin": 0.0}
    method.update(over)
    return method


def test_best_component_picks_the_better_forecaster_per_cell(tmp_path):
    df = cell_frame(np.random.default_rng(40))
    params = params_for(tmp_path, df, best_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    cells = {c["cell"]["symbol"]: c for c in fit["folds"]["2"]["state"]["cells"]}
    assert cells["A"]["chosen"] == "model" and cells["B"]["chosen"] == "market" and "C" not in cells
    full = joined(forecasts, df)
    got = full[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    fold2 = full["fold"] == 2
    a = fold2 & (full["symbol"] == "A")
    other = fold2 & (full["symbol"] != "A")
    assert np.allclose(got[a], cdfs(full, "mdl")[a]) and np.allclose(got[other], cdfs(full, "mkt")[other])


def test_best_component_respects_min_rows_and_the_margin(tmp_path):
    df = cell_frame(np.random.default_rng(41))
    for tag, over in (("rows", {"min_rows": 10 ** 6}), ("margin", {"margin": 0.99})):
        params = params_for(tmp_path, df, best_method(**over), output=str(tmp_path / tag))
        run(params)
        forecasts, fit, _ = read_out(params)
        full = joined(forecasts, df)
        got = full[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
        assert np.allclose(got, cdfs(full, "mkt"))  # the default everywhere
        assert all(c["chosen"] == "market" for c in fit["folds"]["2"]["state"]["cells"])


# ------------------------------------------------------------------------------------------ binary GBM


def gbm_frame(rng):
    df = make_frame(rng, folds=(1, 2), n_fit=800, n_val=400)
    x = rng.normal(size=len(df))
    df["y"] = 0.8 * x + 0.6 * rng.normal(size=len(df))
    df["x"] = x
    add_cdf(df, "mkt", 0.6, 1.0)  # a deliberately biased base
    add_cdf(df, "mdl", 0.0, 1.0)
    return df


def gbm_method(feature_path, feature_sha, **over):
    method = {"kind": "binary_gbm", "learn": "fit_band", "base": "market", "features": ["x"],
              "feature_source": {"path": str(feature_path), "sha256": feature_sha, "keys": ["row_id", "quote_date"]},
              "lgbm_params": {"n_estimators": 20, "num_leaves": 7, "min_child_samples": 20, "learning_rate": 0.3}, "seed": 3,
              "max_learning_rows": 800}
    method.update(over)
    return method


def gbm_params(tmp_path, df, output="out", **over):
    feats = tmp_path / "features.parquet"
    if not feats.exists():
        df[["row_id", "quote_date", "x"]].to_parquet(feats, index=False)
    params = params_for(tmp_path, df, gbm_method(feats, sha(feats), **over), output=str(tmp_path / output),
                        ranked_folds=[1, 2])
    return params


def test_binary_gbm_is_monotone_beats_a_biased_base_and_keeps_its_booster(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(50))
    params = gbm_params(tmp_path, df)
    run(params)
    forecasts, fit, _ = read_out(params)
    cdf = forecasts[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    assert np.isfinite(cdf).all() and cdf.min() >= 0 and cdf.max() <= 1
    assert (np.diff(cdf, axis=1) >= -1e-12).all()
    full = joined(forecasts, df)
    val = full[(full["band"] == "val") & (full["fold"] == 2)]
    got = val[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    assert tw(got, val["y"]).mean() < 0.7 * tw(cdfs(val, "mkt"), val["y"]).mean()
    state = fit["folds"]["2"]["state"]
    text = (tmp_path / "out" / state["booster_file"]).read_text()
    assert state["booster_file"] == "fold_2_booster.txt"
    assert hashlib.sha256(text.encode()).hexdigest() == state["booster_sha256"]
    assert state["learning_rows_used"] == 800 and state["examples"] == 800 * 13


def test_binary_gbm_is_deterministic(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(51))
    first, second = gbm_params(tmp_path, df, "a"), gbm_params(tmp_path, df, "b")
    run(first)
    run(second)
    a, fit_a, _ = read_out(first)
    b, fit_b, _ = read_out(second)
    pd.testing.assert_frame_equal(a, b)
    assert fit_a["folds"]["2"]["state"]["booster_sha256"] == fit_b["folds"]["2"]["state"]["booster_sha256"]


def test_binary_gbm_subsamples_learning_rows_by_a_deterministic_stride(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(52))
    params = gbm_params(tmp_path, df, max_learning_rows=300)
    run(params)
    state = read_out(params)[1]["folds"]["2"]["state"]
    assert state["learning_rows_used"] <= 300 and state["learning_rows_total"] == 800


@pytest.mark.parametrize("bad", [{"n_jobs": 4}, {"num_threads": 2}, {"random_state": 1}, {"seed": 1},
                                 {"deterministic": False}, {"verbosity": 2}, {"unknown_key": 1},
                                 {"class_weight": "balanced"}, {"n_estimators": "many"},
                                 {"objective": "regression"}])
def test_binary_gbm_refuses_forbidden_or_unknown_lgbm_keys(tmp_path, bad):
    df = gbm_frame(np.random.default_rng(53))
    params = gbm_params(tmp_path, df)
    params["method"]["lgbm_params"] = bad
    assert any("lgbm_params" in p for p in GridCDFMethod.validate_params(params))
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


def test_every_allowed_lgbm_key_is_a_classifier_keyword():
    lightgbm = pytest.importorskip("lightgbm")
    from dskit.pipeline.libs.lightgbm import ALLOWED_LGBM_KEYS
    assert set(ALLOWED_LGBM_KEYS) <= set(lightgbm.LGBMClassifier().get_params())


def test_binary_gbm_refuses_duplicate_feature_keys_and_a_feature_hash_mismatch(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(54))
    params = gbm_params(tmp_path, df)
    pd.concat([df[["row_id", "quote_date", "x"]]] * 2).to_parquet(tmp_path / "features.parquet", index=False)
    params["method"]["feature_source"]["sha256"] = sha(tmp_path / "features.parquet")
    with pytest.raises(ValueError, match="duplicate"):
        run(params)
    params["method"]["feature_source"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        run(params)
    assert not (tmp_path / "out").exists()


def test_binary_gbm_rows_without_features_still_get_a_forecast(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(55))
    params = gbm_params(tmp_path, df)
    feats = df[["row_id", "quote_date", "x"]].iloc[::2]
    feats.to_parquet(tmp_path / "features.parquet", index=False)
    params["method"]["feature_source"]["sha256"] = sha(tmp_path / "features.parquet")
    run(params)
    forecasts, _, _ = read_out(params)
    assert np.isfinite(forecasts[[f"method_cdf_{label}" for label in LABELS]].to_numpy()).all()


# ---------------------------------------------------------------------------------------- validation


def valid_params(tmp_path):
    df = pool_frame(np.random.default_rng(60), n_fit=50, n_val=50)
    return df, params_for(tmp_path, df, pool_method())


def test_valid_params_pass_and_construct(tmp_path):
    _, params = valid_params(tmp_path)
    assert GridCDFMethod.validate_params(params) == []
    assert GridCDFMethod("g", params).role == "transform"


REFUSALS = {
    "unknown key": (lambda p: p.update(surprise=1), "surprise"),
    "unknown top-level method key": (lambda p: p["method"].update(surprise=1), "surprise"),
    "unknown kind": (lambda p: p["method"].update(kind="astrology"), "kind"),
    "unknown learn": (lambda p: p["method"].update(learn="always"), "learn"),
    "unknown pool component": (lambda p: p["method"].update(components=["model", "ghost"]), "ghost"),
    "unknown fallback": (lambda p: p.update(fallback="ghost"), "ghost"),
    "unknown comparator": (lambda p: p.update(comparators=["ghost"]), "ghost"),
    "descending grid": (lambda p: p.update(z_grid=list(reversed(Z))), "ascending"),
    "short labels": (lambda p: p.update(grid_labels=LABELS[:-1]), "grid_labels"),
    "duplicate labels": (lambda p: p.update(grid_labels=["a"] * 13), "grid_labels"),
    "short weights": (lambda p: p.update(score_weights=[1.0] * 12), "score_weights"),
    "negative weight": (lambda p: p.update(score_weights=[-1.0] + [1.0] * 12), "score_weights"),
    "bad end_before": (lambda p: p.update(end_before="2030-13-01"), "end_before"),
    "bad sha": (lambda p: p.update(source_sha256="abc"), "source_sha256"),
    "missing column role": (lambda p: p["columns"].pop("eligible"), "columns"),
    "duplicate component": (lambda p: p["components"].append({"name": "model", "source": "mdl"}), "duplicate"),
    "one-component pool": (lambda p: p["method"].update(components=["model"]), "components"),
    "by with quantile_by": (lambda p: p["method"].update(by="symbol", quantile_by={"column": "v", "quantiles": [.5]}),
                            "quantile_by"),
    "shrink without by": (lambda p: p["method"].update(shrink_rows=10), "shrink_rows"),
    "bad knots": (lambda p: p["components"][0].update(pit_map={"knots": 1, "learn": "fit_band"}), "knots"),
    "bad ranked folds": (lambda p: p.update(ranked_folds=[]), "ranked_folds"),
    "bad table": (lambda p: p.update(tables=[{"name": "windows", "by": [], "regions": False}]), "windows"),
    "bad periods": (lambda p: p.update(periods={"P": ["2020-02-01", "2020-01-01"]}), "periods"),
    "bad buckets": (lambda p: p.update(buckets={"b": {"column": "actual_calendar_dte", "edges": [[5, 1]]}}),
                    "buckets"),
    "table by period without periods": (
        lambda p: p.update(tables=[{"name": "t", "by": ["period"], "regions": False}]), "period"),
    "table by symbol and period without periods": (
        lambda p: p.update(tables=[{"name": "t", "by": ["symbol", "period"], "regions": False}]), "period"),
    "component named method": (lambda p: p["components"].append({"name": "method", "source": "mdl"}), "reserved"),
}


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_validate_params_refuses(tmp_path, name):
    _, params = valid_params(tmp_path)
    change, needle = REFUSALS[name]
    change(params)
    problems = GridCDFMethod.validate_params(params)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


def test_validate_params_refuses_an_existing_output_directory(tmp_path):
    _, params = valid_params(tmp_path)
    (tmp_path / "out").mkdir()
    assert any("exists" in p for p in GridCDFMethod.validate_params(params))


def test_a_source_hash_mismatch_is_refused_before_anything_is_read_or_written(tmp_path):
    _, params = valid_params(tmp_path)
    params["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        run(params)
    assert not (tmp_path / "out").exists() and not list(tmp_path.glob(".out*"))


def test_a_missing_source_column_is_refused_and_leaves_no_partial_output(tmp_path):
    _, params = valid_params(tmp_path)
    params["components"][0]["source"] = "nonexistent"
    with pytest.raises(ValueError, match="nonexistent_cdf_zm3"):
        run(params)
    assert not (tmp_path / "out").exists() and not list(tmp_path.glob(".out*"))


@pytest.mark.parametrize("kind", ["timestamp", "date32", "string"])
def test_rows_at_or_after_end_before_are_never_read_or_written(tmp_path, kind):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    df = pool_frame(np.random.default_rng(61), n_fit=100, n_val=100)
    cut = pd.Timestamp("2020-04-01")
    params = params_for(tmp_path, df, pool_method(), end_before="2020-04-01")
    table = pa.Table.from_pandas(df, preserve_index=False)
    for name in ("quote_date", "settlement_date"):
        col = table[name]
        col = (col.cast(pa.date32()) if kind == "date32" else
               pa.array([t.strftime("%Y-%m-%d") for t in df[name]]) if kind == "string" else col)
        table = table.set_column(table.schema.get_field_index(name), name, col)
    pq.write_table(table, params["source"])
    params["source_sha256"] = sha(params["source"])
    run(params)
    forecasts, _, summary = read_out(params)
    keep = (df["quote_date"] < cut) & (df["settlement_date"] < cut)
    assert 0 < keep.sum() < len(df)
    assert sorted(forecasts["row_id"]) == sorted(df.loc[keep, "row_id"])
    assert summary["rows"] == int(keep.sum())


def test_the_output_is_published_by_rename_and_carries_the_documented_files(tmp_path):
    df, params = valid_params(tmp_path)
    returned = run(params)
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == ["fit.json", "forecasts.parquet", "summary.json", "tables"]
    assert not list(tmp_path.glob(".out*"))
    assert returned["summary"]["output"] == str(out) and returned["summary"]["rows"] == len(df)
    forecasts, _, summary = read_out(params)
    assert list(forecasts.columns) == ["row_id", "symbol", "quote_date", "fold", "band",
                                       *[f"method_cdf_{label}" for label in LABELS], "fitted"]
    assert all(forecasts[f"method_cdf_{label}"].dtype == np.float64 for label in LABELS)
    assert summary["source_sha256"] == params["source_sha256"]
    assert summary["params_identity"] == value_hash(params)
    assert summary["rows"] == len(df) and summary["wall_seconds"] >= 0
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)  # the output now exists


# --------------------------------------------------------------------------------------------- tables


def table_setup(tmp_path):
    rng = np.random.default_rng(70)
    df = make_frame(rng, n_fit=800, n_val=500, symbols=("A", "B", "C"))
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mkt", 0.0, 0.6)
    add_cdf(df, "mdl", 0.0, 1.0)
    params = params_for(
        tmp_path, df, {"kind": "pit_map", "learn": "fit_band", "component": "market", "knots": 41, "min_rows": 50},
        comparators=["market", "model"], ranked_folds=[2, 3],
        buckets={"dte_bucket": {"column": "actual_calendar_dte", "edges": [[1, 7], [8, 14], [15, 21]]}},
        periods={"P1": ["2020-05-15", "2020-06-15"], "P2": ["2020-07-01", "2020-07-31"]},
        tables=[{"name": "by_symbol", "by": ["symbol"], "regions": True},
                {"name": "by_dte", "by": ["dte_bucket"], "regions": False},
                {"name": "by_symbol_dte", "by": ["symbol", "dte_bucket"], "regions": False}])
    return df, params


def cluster_se(d, date, group, by_group=True):
    """The ratio-of-totals linearization, the larger of the date and group clusters."""
    ses = []
    for clusters in ([date, group] if by_group else [date]):
        frame = pd.DataFrame({"d": d, "c": clusters})
        g = frame.groupby("c")["d"].agg(["sum", "count"])
        n = g["count"].sum()
        theta = g["sum"].sum() / n
        if len(g) < 2:
            return np.nan
        ses.append(np.sqrt(len(g) / (len(g) - 1) * ((g["sum"] - theta * g["count"]) ** 2).sum()) / n)
    return max(ses)


def test_tables_edge_sign_values_and_clusters_match_an_independent_computation(tmp_path):
    df, params = table_setup(tmp_path)
    run(params)
    forecasts, _, _ = read_out(params)
    full = joined(forecasts, df)
    val = full[(full["band"] == "val") & full["fold"].isin([2, 3])]
    method = tw(val[[f"method_cdf_{label}" for label in LABELS]].to_numpy(), val["y"])
    for comparator, source in (("market", "mkt"), ("model", "mdl")):
        comp = tw(cdfs(val, source), val["y"])
        windows = pd.read_csv(tmp_path / "out" / "tables" / "windows.csv")
        row = windows[(windows["window_kind"] == "pooled") & (windows["comparator"] == comparator)
                      & (windows["metric"] == "twcrps")].iloc[0]
        assert row["rows"] == len(val) and row["dates"] == val["quote_date"].nunique()
        assert row["method"] == pytest.approx(method.mean()) and row["comparator_mean"] == pytest.approx(comp.mean())
        assert row["edge_pct"] == pytest.approx(100 * (comp.mean() - method.mean()) / comp.mean())
        se = cluster_se(comp - method, val["quote_date"].to_numpy(), val["symbol"].to_numpy())
        assert row["se_pct"] == pytest.approx(100 * se / comp.mean())
        assert row["t"] == pytest.approx(row["edge_pct"] / row["se_pct"])
    market = pd.read_csv(tmp_path / "out" / "tables" / "windows.csv")
    pooled = market[(market["window_kind"] == "pooled") & (market["comparator"] == "market")
                    & (market["metric"] == "twcrps")].iloc[0]
    assert pooled["edge_pct"] > 0  # the recalibrated market beats the too-narrow raw market: positive = better


def test_windows_cover_fold_year_period_pooled_and_in_sample(tmp_path):
    df, params = table_setup(tmp_path)
    run(params)
    windows = pd.read_csv(tmp_path / "out" / "tables" / "windows.csv")
    kinds = set(windows["window_kind"])
    assert kinds == {"fold", "year", "period", "pooled", "in_sample"}
    assert set(windows.loc[windows["window_kind"] == "fold", "window"].astype(str)) == {"2", "3"}
    assert set(windows.loc[windows["window_kind"] == "in_sample", "metric"]) == {"twcrps", "crps13"}
    assert set(windows["metric"]) == {"twcrps", "crps13"}


def test_a_grouped_table_has_rows_dates_and_region_briers(tmp_path):
    df, params = table_setup(tmp_path)
    run(params)
    forecasts, _, _ = read_out(params)
    full = joined(forecasts, df)
    table = pd.read_csv(tmp_path / "out" / "tables" / "by_symbol.csv")
    assert {"symbol", "comparator", "metric", "region", "rows", "dates", "method", "comparator_mean",
            "edge_pct", "se_pct", "t"} <= set(table.columns)
    assert set(table["metric"]) == {"twcrps", "crps13", "region_brier"}
    assert sorted(table.loc[table["metric"] == "region_brier", "region"].dropna().unique()) == list(range(14))
    val = full[(full["band"] == "val") & full["fold"].isin([2, 3]) & (full["symbol"] == "B")]
    F = val[[f"method_cdf_{label}" for label in LABELS]].to_numpy()
    mass = np.concatenate([F[:, :1], np.diff(F, axis=1), 1 - F[:, -1:]], axis=1)
    inside = np.zeros_like(mass)
    inside[np.arange(len(val)), np.searchsorted(Z, val["y"].to_numpy(), side="left")] = 1.0
    brier = ((mass - inside) ** 2).mean(0)
    cell = table[(table["symbol"] == "B") & (table["comparator"] == "market") & (table["metric"] == "region_brier")]
    assert cell.sort_values("region")["method"].to_numpy() == pytest.approx(brier)
    rows = table[(table["symbol"] == "B") & (table["comparator"] == "market") & (table["metric"] == "twcrps")]
    assert rows["rows"].iloc[0] == len(val)
    # a table grouped by the group column clusters by date alone, so a symbol cell still has a standard error
    assert np.isfinite(rows["se_pct"].iloc[0])


def test_a_table_with_no_grouping_column_is_one_pooled_cell(tmp_path):
    df, params = table_setup(tmp_path)
    params["tables"] = [{"name": "pooled", "by": [], "regions": False}]
    run(params)
    pooled = pd.read_csv(tmp_path / "out" / "tables" / "pooled.csv")
    windows = pd.read_csv(tmp_path / "out" / "tables" / "windows.csv")
    one = windows[(windows["window_kind"] == "pooled")].drop(columns=["window_kind", "window"])
    assert len(pooled) == len(one) == 4 and pooled.drop(columns="region", errors="ignore").equals(one.reset_index(drop=True))


def test_bucket_tables_leave_out_rows_outside_every_bucket(tmp_path):
    df, params = table_setup(tmp_path)
    run(params)
    forecasts, _, _ = read_out(params)
    full = joined(forecasts, df)
    val = full[(full["band"] == "val") & full["fold"].isin([2, 3])]
    table = pd.read_csv(tmp_path / "out" / "tables" / "by_dte.csv", dtype={"dte_bucket": str})
    got = table[(table["comparator"] == "market") & (table["metric"] == "twcrps")].set_index("dte_bucket")["rows"]
    inside = val[val["actual_calendar_dte"] <= 21]
    assert got.to_dict() == {"1-7": int((inside["actual_calendar_dte"] <= 7).sum()),
                             "8-14": int(inside["actual_calendar_dte"].between(8, 14).sum()),
                             "15-21": int(inside["actual_calendar_dte"].between(15, 21).sum())}


def test_the_standard_error_is_nan_with_a_single_cluster(tmp_path):
    df, params = table_setup(tmp_path)
    params["periods"] = {"ONE": ["2020-05-25", "2020-05-25"]}
    run(params)
    windows = pd.read_csv(tmp_path / "out" / "tables" / "windows.csv")
    one = windows[(windows["window_kind"] == "period") & (windows["metric"] == "twcrps")]
    assert len(one) == 2 and (one["dates"] == 1).all()
    assert one["se_pct"].isna().all() and one["t"].isna().all() and one["edge_pct"].notna().all()


def test_rows_with_a_nonfinite_comparator_are_left_out_of_the_comparison(tmp_path):
    df, params = table_setup(tmp_path)
    hole = df.index[(df["fold"] == 2) & (df["band"] == "val")][:25]
    df.loc[hole, [f"mdl_cdf_{label}" for label in LABELS]] = np.nan
    path = write_source(tmp_path, df, "holey.parquet")
    params.update(source=str(path), source_sha256=sha(path))
    run(params)
    windows = pd.read_csv(tmp_path / "out" / "tables" / "windows.csv")
    pooled = windows[(windows["window_kind"] == "pooled") & (windows["metric"] == "twcrps")]
    val = df[(df["band"] == "val") & df["fold"].isin([2, 3])]
    assert (pooled["rows"] == len(val) - 25).all()


def test_a_declared_pit_column_is_read_from_the_source(tmp_path):
    """Regression: a component's pit_column must reach the panel (it was read, then dropped by the column select)."""
    rng = np.random.default_rng(41)
    df = make_frame(rng)
    df["y"] = rng.normal(0, 1.6, len(df))
    add_cdf(df, "mkt", 0.0, 1.0)
    add_cdf(df, "mdl", 0.0, 1.6)
    df["mkt_pit"] = ndtr(df["y"].to_numpy())
    components = [{"name": "market", "source": "mkt", "pit_column": "mkt_pit"}, {"name": "model", "source": "mdl"},
                  {"name": "market_cal", "source": "mkt", "pit_column": "mkt_pit", "pit_map": {"knots": 21, "learn": "fit_band"}}]
    params = params_for(tmp_path, df, {"kind": "pit_map", "learn": "fit_band", "component": "market", "knots": 21, "min_rows": 50},
                        components=components)
    run(params)
    forecasts, fit, _ = read_out(params)
    assert forecasts["fitted"].all()
    assert fit["components"]["market_cal"]["1"]["learning_rows"] == 300


def test_region_groups_sum_the_region_briers_of_each_named_group_with_their_own_errors(tmp_path):
    df, params = table_setup(tmp_path)
    groups = {"left": [0, 1, 2, 3], "body": [6, 7], "right": [10, 11, 12, 13]}
    params["tables"] = [{"name": "by_symbol", "by": ["symbol"], "regions": True, "region_groups": groups}]
    run(params)
    forecasts, _, _ = read_out(params)
    full = joined(forecasts, df)
    table = pd.read_csv(tmp_path / "out" / "tables" / "by_symbol.csv")
    zone = table[table["metric"] == "region_group_brier"]
    assert set(zone["region_group"]) == set(groups)
    val = full[(full["band"] == "val") & full["fold"].isin([2, 3]) & (full["symbol"] == "B")]

    def brier(F):
        mass = np.concatenate([F[:, :1], np.diff(F, axis=1), 1 - F[:, -1:]], axis=1)
        inside = np.zeros_like(mass)
        inside[np.arange(len(val)), np.searchsorted(Z, val["y"].to_numpy(), side="left")] = 1.0
        return (mass - inside) ** 2

    method = brier(val[[f"method_cdf_{label}" for label in LABELS]].to_numpy())
    market = brier(cdfs(val, "mkt"))
    for name, regions in groups.items():
        m, c = method[:, regions].sum(1), market[:, regions].sum(1)
        row = zone[(zone["symbol"] == "B") & (zone["comparator"] == "market") & (zone["region_group"] == name)].iloc[0]
        assert row["method"] == pytest.approx(m.mean()) and row["comparator_mean"] == pytest.approx(c.mean())
        se = cluster_se(c - m, val["quote_date"].to_numpy(), None, by_group=False)
        assert row["se_pct"] == pytest.approx(100 * se / c.mean())
    assert table.loc[table["metric"] != "region_group_brier", "region_group"].isna().all()


@pytest.mark.parametrize("groups", [{"x": []}, {"x": [14]}, {"x": [1, 1]}, {"": [1]}, [[1]], {"x": [1.0]}])
def test_bad_region_groups_are_refused(tmp_path, groups):
    df, params = table_setup(tmp_path)
    params["tables"] = [{"name": "t", "by": ["symbol"], "regions": True, "region_groups": groups}]
    assert any("region_groups" in p for p in GridCDFMethod.validate_params(params))


# ------------------------------------------------------------------- pins: bucketed and per-threshold pools


def method_cdfs(frame):
    """The method's CDF columns of a forecasts-like frame as an array."""
    return frame[[f"method_cdf_{label}" for label in LABELS]].to_numpy()


def regional_frame(rng, n_fit=1500, n_val=300):
    """Truth N(0, 1); the model is sharp and offset, the market wide: their best mix differs by region of z."""
    df = make_frame(rng, n_fit=n_fit, n_val=n_val)
    df["y"] = rng.normal(0, 1, len(df))
    add_cdf(df, "mdl", 0.3, 0.7)
    add_cdf(df, "mkt", 0.0, 2.0)
    return df


DTE_BUCKETS = {"dte_bucket": {"column": "actual_calendar_dte", "edges": [[1, 15], [16, 30]]}}


def test_bucketed_pool_learns_and_applies_each_buckets_own_weights(tmp_path):
    rng = np.random.default_rng(80)
    truth = {"1-15": 0.8, "16-30": 0.2}
    df = pool_frame(rng, n_fit=2000, weight=lambda d: np.where(d["actual_calendar_dte"] <= 15, 0.8, 0.2))
    params = params_for(tmp_path, df, pool_method(by="dte_bucket"), buckets=DTE_BUCKETS)
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]
    assert sorted(state["buckets"]) == sorted(truth)
    w = {b: np.array(state["buckets"][b]["weights"]) for b in truth}
    for bucket, expected in truth.items():
        assert w[bucket][0] == pytest.approx(expected, abs=0.07)
    assert abs(w["1-15"][0] - w["16-30"][0]) > 0.4
    full = joined(forecasts, df)
    got = method_cdfs(full)
    dte = full["actual_calendar_dte"].to_numpy()
    in_fold = (full["fold"] == 2).to_numpy()
    for bucket, inside in (("1-15", dte <= 15), ("16-30", dte >= 16)):
        rows = in_fold & inside
        expect = w[bucket][0] * cdfs(full, "mdl") + w[bucket][1] * cdfs(full, "mkt")
        assert rows.sum() > 100 and np.allclose(got[rows], expect[rows], atol=1e-12)


def test_per_threshold_weights_are_each_thresholds_own_brier_minimizer(tmp_path):
    df = regional_frame(np.random.default_rng(81))
    run(params := params_for(tmp_path, df, pool_method(per_threshold=True)))
    _, fit, _ = read_out(params)
    weights = np.array(fit["folds"]["2"]["state"]["global"]["weights"])
    rows = df[(df["fold"] == 2) & (df["band"] == "fit")]
    first, second = cdfs(rows, "mdl"), cdfs(rows, "mkt")
    hit = (rows["y"].to_numpy()[:, None] <= np.array(Z)[None, :]).astype(float)
    ticks = np.linspace(0, 1, 2001)
    best = np.empty(13)
    for k in range(13):
        pooled = ticks[:, None] * first[:, k] + (1 - ticks[:, None]) * second[:, k]
        best[k] = ticks[np.argmin(((pooled - hit[:, k]) ** 2).mean(1))]
    assert best.max() - best.min() > 0.3  # the minimizers differ by threshold, so a scrambled layout shows
    assert weights.shape == (13, 2) and np.allclose(weights.sum(1), 1.0)
    assert np.allclose(weights[:, 0], best, atol=1e-3)


def test_per_threshold_predict_returns_the_sorted_clipped_pooled_values():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    spec = {"kind": "linear_pool", "learn": "fit_band", "components": ["a", "b"], "min_rows": 1,
            "per_threshold": True}
    method = cgm._LinearPoolMethod(spec, {}, grid)
    z = np.array(Z)
    low, high = ndtr(z - 2.0), ndtr(z + 0.5)
    panel = types.SimpleNamespace(cdf={"a": np.tile(low, (3, 1)), "b": np.tile(high, (3, 1))})
    first = np.array([1.0, 0.0, 1.0, 0.3, 0.0, 1.0, 0.0, 0.8, 1.0, 0.0, 0.6, 0.0, 0.0])  # weight of "a": a zigzag
    weights = np.stack([first, 1 - first], axis=1)
    pooled = first * low + (1 - first) * high
    assert (np.diff(pooled) < -0.05).any()  # the premise: the pooled values cross, so a rearrangement is needed
    fitted = cgm._Fitted({}, {"global": weights, "buckets": {}, "states": []})
    got = method.predict(panel, np.arange(3), fitted)
    assert got.shape == (3, 13)
    assert np.allclose(got, np.sort(np.clip(pooled, 0, 1))[None, :], atol=1e-15)
    assert (np.diff(got, axis=1) >= 0).all()


# ------------------------------------------------------------------------------ pins: the weighted objective


def tw_minimizer(rows, weights, trapezoid=True):
    """The weight on the model minimizing the weighted grid twCRPS of ``w * F_model + (1 - w) * F_market``."""
    first, second, y = cdfs(rows, "mdl"), cdfs(rows, "mkt"), rows["y"].to_numpy()
    ticks = np.linspace(0, 1, 2001)
    if trapezoid:
        loss = [tw(w * first + (1 - w) * second, y, weights).mean() for w in ticks]
    else:
        hit = (y[:, None] <= np.array(Z)[None, :]).astype(float)
        loss = [(((w * first + (1 - w) * second - hit) ** 2) @ np.asarray(weights, dtype=float)).mean()
                for w in ticks]
    return ticks[int(np.argmin(loss))]


@pytest.mark.parametrize("case", ["taper", "two_points"])
def test_pool_weight_minimizes_the_score_weighted_twcrps(tmp_path, case):
    weights = {"taper": [0.25] * 4 + [1.0] * 5 + [0.25] * 4,
               "two_points": [1.0] + [0.0] * 5 + [1.0] + [0.0] * 6}[case]
    df = regional_frame(np.random.default_rng(82))
    run(params := params_for(tmp_path, df, pool_method(), score_weights=weights))
    _, fit, _ = read_out(params)
    got = fit["folds"]["2"]["state"]["global"]["weights"][0]
    rows = df[(df["fold"] == 2) & (df["band"] == "fit")]
    expected = tw_minimizer(rows, weights)
    assert abs(expected - tw_minimizer(rows, [1.0] * 13)) > 0.05  # the weights matter on this data
    if case == "two_points":
        assert abs(expected - tw_minimizer(rows, weights, trapezoid=False)) > 0.05  # and so does the spacing
    assert got == pytest.approx(expected, abs=1.5e-3)


# ---------------------------------------------------------------------------------- pins: the pit_map paths


def pit_frame(rng, symbols=("A", "B"), folds=(1, 2)):
    """Truth N(0, 1) for symbol A and N(0, 2) for the others; the market is N(0, 1), its PIT is a column."""
    df = make_frame(rng, folds=folds, n_fit=900, n_val=300, symbols=symbols)
    spread = np.where(df["symbol"] == "A", 1.0, 2.0)
    df["y"] = rng.normal(0, 1, len(df)) * spread
    add_cdf(df, "mkt", 0.0, 1.0)
    add_cdf(df, "mdl", 0.0, 1.0)
    df["mkt_pit"] = ndtr(df["y"].to_numpy())
    return df


PIT_COMPONENTS = [{"name": "market", "source": "mkt", "pit_column": "mkt_pit"}, {"name": "model", "source": "mdl"}]


def empirical_map(pits, knots):
    """G(p) = the fraction of ``pits`` at or below p, on ``knots`` evenly spaced points of [0, 1]."""
    p = np.linspace(0, 1, knots)
    return p, np.array([(pits <= point).mean() for point in p])


def test_pit_map_buckets_apply_their_own_map_and_small_buckets_use_the_global_one(tmp_path):
    rng = np.random.default_rng(83)
    df = pit_frame(rng)
    rare = df.index[(df["fold"] == 2) & (df["band"] == "fit")][:5]
    df.loc[rare, "symbol"] = "C"
    method = {"kind": "pit_map", "learn": "fit_band", "component": "market", "knots": 41, "min_rows": 50,
              "by": "symbol"}
    params = params_for(tmp_path, df, method, components=PIT_COMPONENTS, fallback="market")
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]
    assert sorted(state["buckets"]) == ["A", "B"]
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]
    full = joined(forecasts, df)
    fold2 = full[full["fold"] == 2]
    raw = cdfs(fold2, "mkt")
    got = method_cdfs(fold2)
    maps = {}
    for symbol in ("A", "B"):
        p, maps[symbol] = empirical_map(learn.loc[learn["symbol"] == symbol, "mkt_pit"].to_numpy(), 41)
    _, maps["global"] = empirical_map(learn["mkt_pit"].to_numpy(), 41)
    assert np.abs(maps["A"] - maps["B"]).max() > 0.1  # the buckets really are miscalibrated differently
    assert np.abs(maps["global"] - maps["A"]).max() > 0.05
    for symbol, key in (("A", "A"), ("B", "B"), ("C", "global")):
        rows = (fold2["symbol"] == symbol).to_numpy()
        assert rows.sum() >= 5
        assert np.allclose(got[rows], np.interp(raw[rows], p, maps[key][None, :][0]), atol=1e-12)
    assert state["global"]["rows"] == len(learn) and state["buckets"]["A"]["g"] == pytest.approx(maps["A"].tolist())


def test_pit_map_learns_from_eligible_rows_only(tmp_path):
    rng = np.random.default_rng(84)
    df = pit_frame(rng, symbols=("A",))
    bad = ((df["fold"] == 2) & (df["band"] == "fit") & (rng.random(len(df)) < 0.3)).to_numpy()
    df.loc[bad, "market_eligible"] = False
    df.loc[bad, "y"] = rng.normal(0, 4, int(bad.sum()))
    df["mkt_pit"] = ndtr(df["y"].to_numpy())
    components = [*PIT_COMPONENTS, {"name": "market_cal", "source": "mkt", "pit_column": "mkt_pit",
                                    "pit_map": {"knots": 41, "learn": "fit_band"}}]
    method = {"kind": "pit_map", "learn": "fit_band", "component": "market", "knots": 41, "min_rows": 50}
    params = params_for(tmp_path, df, method, components=components, fallback="market")
    run(params)
    forecasts, fit, _ = read_out(params)
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]
    eligible = learn[learn["market_eligible"]]
    p, expected = empirical_map(eligible["mkt_pit"].to_numpy(), 41)
    _, everything = empirical_map(learn["mkt_pit"].to_numpy(), 41)
    assert 0 < len(eligible) < len(learn) and np.abs(expected - everything).max() > 0.03
    state = fit["folds"]["2"]["state"]
    assert state["global"]["rows"] == len(eligible)
    assert state["global"]["g"] == pytest.approx(expected.tolist())
    component = fit["components"]["market_cal"]["2"]
    assert component["learning_rows"] == len(eligible) and component["g"] == pytest.approx(expected.tolist())
    full = joined(forecasts, df)
    fold2 = full[full["fold"] == 2]
    assert np.allclose(method_cdfs(fold2), np.interp(cdfs(fold2, "mkt"), p, expected), atol=1e-12)


# ------------------------------------------------------------------------------ pins: best_component cells


def test_best_component_applies_a_nondefault_choice_and_the_default_to_an_unlearned_cell(tmp_path):
    rng = np.random.default_rng(85)
    df = make_frame(rng, folds=(1, 2), n_fit=600, n_val=300, symbols=("A", "C"))
    df["y"] = rng.normal(size=len(df))
    unseen = df.index[(df["fold"] == 2) & (df["band"] == "val")][:40]
    df.loc[unseen, "symbol"] = "B"  # a cell with no learning rows, sorting between the two learned ones
    model_wins = (df["symbol"] == "C").to_numpy()
    add_cdf(df, "mdl", 0.0, np.where(model_wins, 1.0, 0.5))
    add_cdf(df, "mkt", 0.0, np.where(model_wins, 0.5, 1.0))
    params = params_for(tmp_path, df, best_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]
    for symbol, better in (("A", "mkt"), ("C", "mdl")):
        rows = learn[learn["symbol"] == symbol]
        scores = {n: tw(cdfs(rows, n), rows["y"].to_numpy()).mean() for n in ("mdl", "mkt")}
        assert min(scores, key=scores.get) == better
    cells = {c["cell"]["symbol"]: c["chosen"] for c in fit["folds"]["2"]["state"]["cells"]}
    assert cells == {"A": "market", "C": "model"}
    full = joined(forecasts, df)
    fold2 = full[full["fold"] == 2]
    got = method_cdfs(fold2)
    for symbol, source in (("A", "mkt"), ("C", "mdl"), ("B", "mkt")):
        rows = (fold2["symbol"] == symbol).to_numpy()
        assert rows.sum() > 0 and np.allclose(got[rows], cdfs(fold2, source)[rows])
    assert (fold2["symbol"] == "B").sum() == 40


# ------------------------------------------------------------------------------ pins: the table's errors


def test_a_table_not_grouped_by_the_group_column_takes_the_larger_of_the_date_and_group_errors(tmp_path):
    rng = np.random.default_rng(86)
    df = make_frame(rng, n_fit=800, n_val=500, symbols=("A", "B"))
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mkt", 0.0, np.where(df["symbol"] == "A", 0.5, 1.0))  # the market is wrong for symbol A only
    add_cdf(df, "mdl", 0.0, 1.0)
    method = {"kind": "pit_map", "learn": "fit_band", "component": "market", "knots": 41, "min_rows": 50}
    params = params_for(tmp_path, df, method, comparators=["market"], ranked_folds=[2, 3], buckets=DTE_BUCKETS,
                        tables=[{"name": "by_dte", "by": ["dte_bucket"], "regions": False}])
    run(params)
    forecasts, _, _ = read_out(params)
    full = joined(forecasts, df)
    val = full[(full["band"] == "val") & full["fold"].isin([2, 3])]
    table = pd.read_csv(tmp_path / "out" / "tables" / "by_dte.csv", dtype={"dte_bucket": str})
    table = table[(table["comparator"] == "market") & (table["metric"] == "twcrps")].set_index("dte_bucket")
    dte = val["actual_calendar_dte"].to_numpy()
    for bucket, inside in (("1-15", dte <= 15), ("16-30", dte >= 16)):
        cell = val[inside]
        d = tw(cdfs(cell, "mkt"), cell["y"].to_numpy()) - tw(method_cdfs(cell), cell["y"].to_numpy())
        dates, groups = cell["quote_date"].to_numpy(), cell["symbol"].to_numpy()
        by_date = cluster_se(d, dates, groups, by_group=False)
        both = cluster_se(d, dates, groups)
        assert both > 1.5 * by_date  # the group clusters carry the larger error here
        other = tw(cdfs(cell, "mkt"), cell["y"].to_numpy()).mean()
        assert table.loc[bucket, "se_pct"] == pytest.approx(100 * both / other)


# ---------------------------------------------------------------- pins: y on a grid point counts as y <= z


def test_the_score_counts_an_outcome_on_a_grid_point_as_at_or_below_it():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    F = np.sort(np.random.default_rng(87).random((13, 13)), axis=1)
    y = np.array(Z)
    assert np.allclose(grid.score(F, y), tw(F, y), atol=1e-14)
    assert (grid.indicator(y) == (y[:, None] <= np.array(Z)[None, :])).all() and grid.indicator(y)[0, 0] == 1.0


def test_a_pool_on_outcomes_that_sit_on_grid_points_is_scored_with_at_or_below(tmp_path):
    rng = np.random.default_rng(88)
    df = make_frame(rng, folds=(1, 2), n_fit=1200, n_val=100)
    df["y"] = np.clip(np.round(rng.normal(0.4, 1.0, len(df)) * 2) / 2, -3, 3)  # every outcome is a grid value
    add_cdf(df, "mdl", 0.3, 0.7)
    add_cdf(df, "mkt", 0.0, 2.0)
    run(params := params_for(tmp_path, df, pool_method()))
    _, fit, _ = read_out(params)
    rows = df[(df["fold"] == 2) & (df["band"] == "fit")]
    assert np.isin(rows["y"], Z).all()
    expected = tw_minimizer(rows, [1.0] * 13)
    strict = rows.assign(y=rows["y"] + 1e-9)  # the same outcomes just above their grid points: not y <= z there
    assert abs(tw_minimizer(strict, [1.0] * 13) - expected) > 0.01  # the tie rule is visible in the optimum
    assert fit["folds"]["2"]["state"]["global"]["weights"][0] == pytest.approx(expected, abs=1.5e-3)


def test_binary_gbm_labels_an_outcome_on_a_grid_point_as_at_or_below_it(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(89))
    df["y"] = 0.0  # exactly the grid point z = 0 for every row
    params = gbm_params(tmp_path, df)
    run(params)
    forecasts, _, _ = read_out(params)
    at_zero = forecasts[f"method_cdf_{LABELS[Z.index(0.0)]}"].to_numpy()
    just_below = forecasts[f"method_cdf_{LABELS[Z.index(-0.5)]}"].to_numpy()
    assert at_zero.mean() > 0.9 and just_below.mean() < 0.1


# ----------------------------------------------------------------------------------- pins: validation


def test_a_table_may_group_by_year_and_by_a_declared_period(tmp_path):
    _, params = valid_params(tmp_path)
    params["tables"] = [{"name": "y", "by": ["year"], "regions": False}]
    assert GridCDFMethod.validate_params(params) == []
    params["periods"] = {"P1": ["2020-05-15", "2020-06-15"]}
    params["tables"] = [{"name": "p", "by": ["period"], "regions": False}]
    assert GridCDFMethod.validate_params(params) == []


def test_a_period_table_without_declared_periods_is_refused_not_a_crash_after_the_compute(tmp_path):
    df, params = valid_params(tmp_path)
    params["tables"] = [{"name": "p", "by": ["period"], "regions": False}]
    assert any("period" in p for p in GridCDFMethod.validate_params(params))
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params).run(CTX, {})


# ===================================================================================================
# Round 2 (ADR-0270): rolling refits, recency weights, beta pool, quantile average, external components
# ===================================================================================================

special = pytest.importorskip("scipy.special")
TRAPEZOID = np.array([0.25] + [0.5] * 11 + [0.25])  # the grid trapezoid at spacing 0.5, all weights one


def drift_weight(df):
    """The truth's weight on the model component, falling from 0.9 (fold 1) to about 0.1 (fold 3)."""
    day = (df["quote_date"] - pd.Timestamp(BASE_DAY)).dt.days.to_numpy()
    return np.clip(0.9 - 0.8 * (day - 250) / 400.0, 0.05, 0.95)


def roll_frame(rng, drift=False, n_fit=400, n_val=1200):
    """Three folds 200 days apart, each with a 70-day validation span, so a 14-day refit has five blocks."""
    return pool_frame(rng, n_fit=n_fit, n_val=n_val, weight=drift_weight if drift else 0.7, folds=(1, 2, 3),
                      fold_days=200, spread=70, dte_hi=20)


def rolling_method(**over):
    method = {"kind": "linear_pool", "learn": "rolling", "refit_days": 14, "components": ["model", "market"],
              "min_rows": 20}
    method.update(over)
    return method


def block_starts(fit, fold):
    return [pd.Timestamp(b["start"]) for b in fit["folds"][str(fold)]["blocks"]]


def cdf_columns(frame, name="method"):
    return frame[[f"{name}_cdf_{label}" for label in LABELS]].to_numpy()


def first_val(df, fold):
    return df.loc[(df["fold"] == fold) & (df["band"] == "val"), "quote_date"].min()


def days(series):
    return (series - pd.Timestamp(BASE_DAY)).dt.days.to_numpy()


def recency(rows, reference, half_life):
    """The documented weight 0.5 ** ((reference day - settlement day) / half-life), recomputed here."""
    return 0.5 ** ((days(pd.Series([reference]))[0] - days(rows["settlement_date"])) / half_life)


def pool_weight_closed_form(rows, omega):
    """The model weight minimizing the omega-weighted grid twCRPS of w * F_model + (1 - w) * F_market."""
    first, second, y = cdfs(rows, "mdl"), cdfs(rows, "mkt"), rows["y"].to_numpy()
    hit = (y[:, None] <= np.array(Z)[None, :]).astype(float)
    d, r0 = first - second, second - hit
    num = (omega[:, None] * TRAPEZOID * r0 * d).sum()
    den = (omega[:, None] * TRAPEZOID * d * d).sum()
    return float(np.clip(-num / den, 0.0, 1.0))


def pit_of(F, y):
    x = np.r_[Z[0] - 4.0, Z, Z[-1] + 4.0]
    return np.array([np.interp(yy, x, np.r_[0.0, f, 1.0]) for f, yy in zip(F, y)])


# --------------------------------------------------------------------------------------- rolling refits


def test_rolling_blocks_start_at_the_first_validation_quote_and_learn_from_settled_rows(tmp_path):
    df = roll_frame(np.random.default_rng(300))
    params = params_for(tmp_path, df, rolling_method())
    run(params)
    _, fit, _ = read_out(params)
    for fold in (1, 2, 3):
        val = df[(df["fold"] == fold) & (df["band"] == "val")]
        first, last = val["quote_date"].min(), val["quote_date"].max()
        starts = block_starts(fit, fold)
        count = (last - first).days // 14 + 1
        assert count >= 4
        assert starts == [first + pd.Timedelta(14 * i, "D") for i in range(count)]
        record = fit["folds"][str(fold)]
        for block in record["blocks"]:
            settled = (df["band"] == "val") & (df["fold"] <= fold) & (df["settlement_date"] < pd.Timestamp(block["start"]))
            assert block["learning_rows"] == int(settled.sum())
        assert sum(b["rows"] for b in record["blocks"]) == record["rows"] == int((df["fold"] == fold).sum())
    for fold in ("2", "3"):
        counts = [b["learning_rows"] for b in fit["folds"][fold]["blocks"]]
        assert all(a < b for a, b in zip(counts, counts[1:])), counts  # settled rows accumulate block by block


def test_the_first_rolling_block_is_the_prior_validation_fit_and_one_huge_block_is_prior_validation(tmp_path):
    df = roll_frame(np.random.default_rng(301))
    rolled = params_for(tmp_path, df, rolling_method(refit_days=100_000), output=str(tmp_path / "rolled"))
    prior = params_for(tmp_path, df, pool_method(learn="prior_validation"), output=str(tmp_path / "prior"))
    run(rolled)
    run(prior)
    a, fit_a, _ = read_out(rolled)
    b, fit_b, _ = read_out(prior)
    pd.testing.assert_frame_equal(a, b)  # bit-identical forecasts and fitted flags
    for fold in ("1", "2", "3"):
        assert len(fit_a["folds"][fold]["blocks"]) == 1
        assert fit_a["folds"][fold]["state"] == fit_b["folds"][fold]["state"]
        assert fit_a["folds"][fold]["learning_rows"] == fit_b["folds"][fold]["learning_rows"]
    many = params_for(tmp_path, df, rolling_method(), output=str(tmp_path / "many"))
    run(many)
    _, fit_m, _ = read_out(many)
    for fold in ("2", "3"):
        assert fit_m["folds"][fold]["blocks"][0]["state"] == fit_b["folds"][fold]["state"]


def test_each_block_forecasts_its_own_rows_and_the_fit_band_uses_the_first_blocks_state(tmp_path):
    df = roll_frame(np.random.default_rng(302), drift=True)
    params = params_for(tmp_path, df, rolling_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    full = joined(forecasts, df)
    got = cdf_columns(full)
    blocks = fit["folds"]["2"]["blocks"]
    starts = np.array(block_starts(fit, 2), dtype="datetime64[ns]")
    weights = np.array([b["state"]["global"]["weights"] for b in blocks])
    assert np.ptp(weights[:, 0]) > 0.03  # the blocks really differ
    is_fold = (full["fold"] == 2).to_numpy()
    where = np.searchsorted(starts, full["quote_date"].to_numpy(dtype="datetime64[ns]"), side="right") - 1
    where = np.where((full["band"] == "val").to_numpy(), np.maximum(where, 0), 0)  # fit-band rows: block 0
    w = weights[where]
    expect = w[:, :1] * cdfs(full, "mdl") + w[:, 1:] * cdfs(full, "mkt")
    assert np.allclose(got[is_fold], expect[is_fold], atol=1e-12)
    assert (where[is_fold & (full["band"] == "fit").to_numpy()] == 0).all()
    assert (np.unique(where[is_fold & (full["band"] == "val").to_numpy()]) == np.arange(len(blocks))).all()


def test_poisoning_outcomes_that_settle_on_or_after_a_block_start_leaves_that_block_bit_identical(tmp_path):
    df = roll_frame(np.random.default_rng(303), drift=True)
    clean = params_for(tmp_path, df, rolling_method(), output=str(tmp_path / "clean"))
    run(clean)
    forecasts, fit, _ = read_out(clean)
    target = 2
    start, after = block_starts(fit, 2)[target], block_starts(fit, 2)[target + 1]
    bad = df.copy()
    bad.loc[bad["settlement_date"] >= start, "y"] = 40.0
    poisoned = params_for(tmp_path, bad, rolling_method(), output=str(tmp_path / "poisoned"))
    run(poisoned)
    other, _, _ = read_out(poisoned)
    a, b = joined(forecasts, df), joined(other, bad)
    same = ((a["fold"] == 1) | ((a["fold"] == 2) & ((a["band"] != "val") | (a["quote_date"] < after)))).to_numpy()
    assert same.sum() > 1000
    assert np.array_equal(cdf_columns(a)[same], cdf_columns(b)[same])  # bit-identical
    assert (a["fitted"].to_numpy()[same] == b["fitted"].to_numpy()[same]).all()
    later = ~same
    assert not np.allclose(cdf_columns(a)[later], cdf_columns(b)[later], atol=1e-6)  # the poison does bite later


def test_a_rolling_block_with_no_learning_rows_falls_back_unfitted(tmp_path):
    df = roll_frame(np.random.default_rng(304))
    params = params_for(tmp_path, df, rolling_method(), fallback="model")
    run(params)
    forecasts, fit, summary = read_out(params)
    blocks = fit["folds"]["1"]["blocks"]
    assert blocks[0]["learning_rows"] == 0 and blocks[0]["fitted"] is False and blocks[0]["state"] is None
    assert all(b["fitted"] for b in blocks[1:])  # fold 1's own early rows settle and teach the later blocks
    full = joined(forecasts, df)
    first = (full["fold"] == 1).to_numpy() & ((full["band"] != "val") | (full["quote_date"] < block_starts(fit, 1)[1])).to_numpy()
    assert not full["fitted"].to_numpy()[first].any()
    assert np.allclose(cdf_columns(full)[first], cdfs(full, "mdl")[first])
    assert summary["rows_not_fitted"] == int(first.sum())
    assert fit["folds"]["1"]["fitted"] is False and fit["folds"]["2"]["fitted"] is True  # the fold reads its first block


ROLLING_KINDS = {
    "pit_map": {"kind": "pit_map", "learn": "rolling", "refit_days": 14, "component": "market", "knots": 11,
                "min_rows": 50},
    "best_component": best_method(learn="rolling", refit_days=14),
}


@pytest.mark.parametrize("kind", sorted(ROLLING_KINDS))
def test_rolling_works_for_the_other_kinds(tmp_path, kind):
    df = roll_frame(np.random.default_rng(305))
    df["symbol"] = np.where(np.arange(len(df)) % 2 == 0, "A", "B")
    params = params_for(tmp_path, df, ROLLING_KINDS[kind])
    run(params)
    forecasts, fit, _ = read_out(params)
    blocks = fit["folds"]["3"]["blocks"]
    assert len(blocks) >= 4 and all(b["fitted"] for b in blocks)
    assert blocks[0]["learning_rows"] < blocks[-1]["learning_rows"]
    cdf = cdf_columns(forecasts)
    assert np.isfinite(cdf).all() and cdf.min() >= 0 and cdf.max() <= 1


def test_rolling_binary_gbm_keeps_one_booster_per_block(tmp_path):
    pytest.importorskip("lightgbm")
    df = gbm_frame(np.random.default_rng(306))
    params = gbm_params(tmp_path, df, learn="rolling", refit_days=3)
    run(params)
    _, fit, _ = read_out(params)
    blocks = fit["folds"]["2"]["blocks"]
    assert len(blocks) >= 3 and all(b["fitted"] for b in blocks)
    names = [b["state"]["booster_file"] for b in blocks]
    assert names == [f"fold_2_block_{i}_booster.txt" for i in range(len(blocks))]
    assert all((tmp_path / "out" / n).is_file() for n in names)
    assert fit["folds"]["1"]["blocks"][0]["fitted"] is False


# ----------------------------------------------------------------------------- recency weights (half life)


def test_half_life_weights_the_pool_by_the_settlement_age_at_the_target_folds_first_validation_quote(tmp_path):
    df = roll_frame(np.random.default_rng(310), drift=True)
    plain = params_for(tmp_path, df, pool_method(learn="prior_validation"), output=str(tmp_path / "plain"))
    aged = params_for(tmp_path, df, pool_method(learn="prior_validation", half_life_days=60),
                      output=str(tmp_path / "aged"))
    run(plain)
    run(aged)
    _, fit_plain, _ = read_out(plain)
    _, fit_aged, _ = read_out(aged)
    rows = df[(df["band"] == "val") & (df["fold"] < 3) & (df["settlement_date"] < first_val(df, 3))]
    omega = recency(rows, first_val(df, 3), 60.0)
    assert omega.min() < 0.1 * omega.max()
    expected = pool_weight_closed_form(rows, omega)
    w_plain = fit_plain["folds"]["3"]["state"]["global"]["weights"][0]
    w_aged = fit_aged["folds"]["3"]["state"]["global"]["weights"][0]
    assert w_aged == pytest.approx(expected, abs=1e-9)
    assert w_plain == pytest.approx(pool_weight_closed_form(rows, np.ones(len(rows))), abs=1e-9)
    assert w_aged < w_plain - 0.1  # the truth's weight is falling, recency follows it


def test_fit_band_half_life_references_the_folds_first_validation_quote(tmp_path):
    df = roll_frame(np.random.default_rng(311), drift=True)
    params = params_for(tmp_path, df, pool_method(half_life_days=10))
    run(params)
    _, fit, _ = read_out(params)
    rows = df[(df["band"] == "fit") & (df["fold"] == 2)]
    omega = recency(rows, first_val(df, 2), 10.0)
    assert (omega > 1).any() and (omega < 1).any()  # this frame has fit rows on both sides of the reference
    assert fit["folds"]["2"]["state"]["global"]["weights"][0] == pytest.approx(pool_weight_closed_form(rows, omega), abs=1e-9)
    assert fit["folds"]["2"]["learning_rows"] == len(rows)  # counts rows, not weight


def test_rolling_half_life_follows_a_drifting_truth_better_than_equal_weights(tmp_path):
    df = roll_frame(np.random.default_rng(312), drift=True)
    plain = params_for(tmp_path, df, rolling_method(), output=str(tmp_path / "plain"))
    aged = params_for(tmp_path, df, rolling_method(half_life_days=45), output=str(tmp_path / "aged"))
    run(plain)
    run(aged)
    out_plain, fit_plain, _ = read_out(plain)
    out_aged, fit_aged, _ = read_out(aged)
    scores = {}
    for label, out in (("plain", out_plain), ("aged", out_aged)):
        full = joined(out, df)
        val = full[(full["band"] == "val") & (full["fold"] == 3) & (full["quote_date"] >= block_starts(fit_plain, 3)[2])]
        scores[label] = tw(cdf_columns(val), val["y"]).mean()
    assert scores["aged"] < 0.99 * scores["plain"], scores
    truth_end = float(drift_weight(df.assign(quote_date=pd.Timestamp("2021-09-01"))).mean())  # about 0.1
    last = lambda fit: fit["folds"]["3"]["blocks"][-1]["state"]["global"]["weights"][0]
    assert abs(last(fit_aged) - truth_end) < abs(last(fit_plain) - truth_end)


def test_half_life_weights_the_pit_map_empirical_cdf(tmp_path):
    df = roll_frame(np.random.default_rng(313), drift=True)
    method = {"kind": "pit_map", "learn": "prior_validation", "component": "market", "knots": 11, "min_rows": 50,
              "half_life_days": 40}
    params = params_for(tmp_path, df, method)
    run(params)
    _, fit, _ = read_out(params)
    rows = df[(df["band"] == "val") & (df["fold"] < 3) & (df["settlement_date"] < first_val(df, 3))]
    omega = recency(rows, first_val(df, 3), 40.0)
    pit = pit_of(cdfs(rows, "mkt"), rows["y"].to_numpy())
    knots = np.linspace(0, 1, 11)
    expected = np.array([omega[pit <= p].sum() for p in knots]) / omega.sum()
    got = np.array(fit["folds"]["3"]["state"]["global"]["g"])
    assert np.allclose(got, expected, atol=1e-12) and got[-1] == 1.0
    plain = np.array([(pit <= p).mean() for p in knots])
    assert np.abs(plain - expected).max() > 0.02  # the weights matter on this data


def test_half_life_weights_each_pit_map_bucket_by_its_own_rows(tmp_path):
    df = roll_frame(np.random.default_rng(320), drift=True)
    df["symbol"] = np.where(np.arange(len(df)) % 3 == 0, "A", "B")
    method = {"kind": "pit_map", "learn": "prior_validation", "component": "market", "knots": 11, "min_rows": 50,
              "by": "symbol", "half_life_days": 40}
    params = params_for(tmp_path, df, method)
    run(params)
    _, fit, _ = read_out(params)
    rows = df[(df["band"] == "val") & (df["fold"] < 3) & (df["settlement_date"] < first_val(df, 3))]
    omega = recency(rows, first_val(df, 3), 40.0)
    pit = pit_of(cdfs(rows, "mkt"), rows["y"].to_numpy())
    knots = np.linspace(0, 1, 11)
    for symbol in ("A", "B"):
        mine = (rows["symbol"] == symbol).to_numpy()
        expected = np.array([omega[mine][pit[mine] <= p].sum() for p in knots]) / omega[mine].sum()
        assert np.allclose(fit["folds"]["3"]["state"]["buckets"][symbol]["g"], expected, atol=1e-12)


def test_half_life_weights_the_best_component_cell_means(tmp_path):
    df = cell_frame(np.random.default_rng(314))
    params = params_for(tmp_path, df, best_method(half_life_days=10))
    run(params)
    _, fit, _ = read_out(params)
    rows = df[(df["band"] == "fit") & (df["fold"] == 2)]
    omega = recency(rows, first_val(df, 2), 10.0)
    cells = {c["cell"]["symbol"]: c for c in fit["folds"]["2"]["state"]["cells"]}
    for symbol, cell in cells.items():
        mine = (rows["symbol"] == symbol).to_numpy()
        for name, source in (("model", "mdl"), ("market", "mkt")):
            score = tw(cdfs(rows, source)[mine], rows["y"].to_numpy()[mine])
            expect = (omega[mine] * score).sum() / omega[mine].sum()
            assert cell["scores"][name] == pytest.approx(expect, rel=1e-12)
            assert cell["rows"] == int(mine.sum())
        plain = tw(cdfs(rows, "mkt")[mine], rows["y"].to_numpy()[mine]).mean()
        assert abs(plain - cell["scores"]["market"]) > 1e-4  # weighted differs from the plain mean


def test_binary_gbm_passes_the_recency_weights_as_sample_weights(tmp_path, monkeypatch):
    lightgbm = pytest.importorskip("lightgbm")
    seen = []
    original = lightgbm.LGBMClassifier.fit

    def spy(self, x, y, *args, **kwargs):
        seen.append(kwargs.get("sample_weight"))
        return original(self, x, y, *args, **kwargs)

    monkeypatch.setattr(lightgbm.LGBMClassifier, "fit", spy)
    df = gbm_frame(np.random.default_rng(315))
    params = gbm_params(tmp_path, df, half_life_days=12)
    run(params)
    fold2 = seen[-1]
    rows = df[(df["band"] == "fit") & (df["fold"] == 2)]
    omega = recency(rows, first_val(df, 2), 12.0)
    assert fold2 is not None and np.allclose(fold2, np.repeat(omega, 13), rtol=1e-12)
    seen.clear()
    run(gbm_params(tmp_path, df, "plain"))
    assert seen[-1] is None  # no half life: the unweighted fit call is unchanged


def test_half_life_leaves_the_quantile_bucket_edges_unweighted_and_min_rows_counts_rows(tmp_path):
    df = roll_frame(np.random.default_rng(316))
    method = pool_method(half_life_days=0.5, quantile_by={"column": "actual_calendar_dte", "quantiles": [0.5]},
                         min_rows=20)
    params = params_for(tmp_path, df, method)
    run(params)
    _, fit, _ = read_out(params)
    rows = df[(df["band"] == "fit") & (df["fold"] == 2)]
    state = fit["folds"]["2"]["state"]
    assert state["bucketing"]["edges"] == [float(np.quantile(rows["actual_calendar_dte"], 0.5))]
    edge = state["bucketing"]["edges"][0]
    dte = rows["actual_calendar_dte"].to_numpy()
    assert {k: b["rows"] for k, b in state["buckets"].items()} == {"q0": int((dte < edge).sum()), "q1": int((dte >= edge).sum())}
    assert all(b["rows"] >= 20 for b in state["buckets"].values())  # row counts, never weight mass


def test_half_life_shrinkage_uses_the_weight_mass_of_each_bucket(tmp_path):
    df = roll_frame(np.random.default_rng(319), drift=True)
    df["symbol"] = np.where(np.arange(len(df)) % 3 == 0, "A", "B")
    params = params_for(tmp_path, df, pool_method(learn="prior_validation", by="symbol", shrink_rows=50,
                                                 half_life_days=60))
    run(params)
    _, fit, _ = read_out(params)
    rows = df[(df["band"] == "val") & (df["fold"] < 3) & (df["settlement_date"] < first_val(df, 3))]
    omega = recency(rows, first_val(df, 3), 60.0)
    glob = pool_weight_closed_form(rows, omega)
    state = fit["folds"]["3"]["state"]
    assert state["global"]["weights"][0] == pytest.approx(glob, abs=1e-9)
    for symbol in ("A", "B"):
        mine = (rows["symbol"] == symbol).to_numpy()
        mass = omega[mine].sum()
        own = pool_weight_closed_form(rows[mine], omega[mine])
        expect = (mass * own + 50 * glob) / (mass + 50)
        assert mass < 0.5 * mine.sum()  # the mass, not the row count, is what shrinks
        assert state["buckets"][symbol]["weights"][0] == pytest.approx(expect, abs=1e-9)
        assert state["buckets"][symbol]["rows"] == int(mine.sum())


def test_rows_whose_weight_underflows_to_zero_are_dropped_and_an_empty_set_is_not_fitted(tmp_path):
    df = roll_frame(np.random.default_rng(317))
    params = params_for(tmp_path, df, pool_method(learn="prior_validation", half_life_days=1e-4))
    run(params)
    forecasts, fit, summary = read_out(params)
    for fold in ("2", "3"):
        assert fit["folds"][fold]["learning_rows"] == 0 and fit["folds"][fold]["fitted"] is False
        assert fit["folds"][fold]["reason"] == "no learning rows"
    assert np.isfinite(cdf_columns(forecasts)).all()
    assert summary["rows_not_fitted"] == len(df)


@pytest.mark.parametrize("kind", ["pit_map", "linear_pool", "best_component"])
def test_an_enormous_half_life_matches_the_unweighted_fit(tmp_path, kind):
    df = roll_frame(np.random.default_rng(318))
    df["symbol"] = np.where(np.arange(len(df)) % 2 == 0, "A", "B")
    base = {"pit_map": {"kind": "pit_map", "learn": "prior_validation", "component": "market", "knots": 11,
                        "min_rows": 50},
            "linear_pool": pool_method(learn="prior_validation"),
            "best_component": best_method(learn="prior_validation")}[kind]
    plain = params_for(tmp_path, df, base, output=str(tmp_path / "plain"))
    aged = params_for(tmp_path, df, {**base, "half_life_days": 1e12}, output=str(tmp_path / "aged"))
    run(plain)
    run(aged)
    a, _, _ = read_out(plain)
    b, _, _ = read_out(aged)
    assert np.allclose(cdf_columns(a), cdf_columns(b), atol=1e-9, equal_nan=True)


# ------------------------------------------------------------------------------ the new keys are validated

ROUND_TWO_REFUSALS = {
    "rolling without refit_days": (lambda p: p["method"].update(learn="rolling"), "refit_days"),
    "refit_days on another rule": (lambda p: p["method"].update(refit_days=7), "refit_days"),
    "zero refit_days": (lambda p: p["method"].update(learn="rolling", refit_days=0), "refit_days"),
    "float refit_days": (lambda p: p["method"].update(learn="rolling", refit_days=7.5), "refit_days"),
    "bool refit_days": (lambda p: p["method"].update(learn="rolling", refit_days=True), "refit_days"),
    "text refit_days": (lambda p: p["method"].update(learn="rolling", refit_days="7"), "refit_days"),
    "zero half life": (lambda p: p["method"].update(half_life_days=0), "half_life_days"),
    "negative half life": (lambda p: p["method"].update(half_life_days=-3), "half_life_days"),
    "text half life": (lambda p: p["method"].update(half_life_days="30"), "half_life_days"),
    "bool half life": (lambda p: p["method"].update(half_life_days=True), "half_life_days"),
    "nan half life": (lambda p: p["method"].update(half_life_days=float("nan")), "half_life_days"),
    "unhashable learn": (lambda p: p["method"].update(learn=["rolling"]), "learn"),
}


@pytest.mark.parametrize("name", sorted(ROUND_TWO_REFUSALS))
def test_round_two_keys_are_refused_when_malformed(tmp_path, name):
    _, params = valid_params(tmp_path)
    change, needle = ROUND_TWO_REFUSALS[name]
    change(params)
    problems = GridCDFMethod.validate_params(params)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


def test_well_formed_round_two_keys_pass(tmp_path):
    _, params = valid_params(tmp_path)
    params["method"].update(learn="rolling", refit_days=21, half_life_days=365.5)
    assert GridCDFMethod.validate_params(params) == []
    params["method"].update(learn="prior_validation")
    params["method"].pop("refit_days")
    assert GridCDFMethod.validate_params(params) == []


def test_the_edge_method_documents_of_the_child_still_validate():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "children" / "index_options" / "configs"
    documents = sorted(root.glob("run-edge-method-*.json"))
    if not documents:
        pytest.skip("no edge-method documents in this checkout")
    for path in documents:
        params = json.loads(path.read_text())["pipeline"]["method"]["params"]
        problems = [p for p in GridCDFMethod.validate_params(params) if "exists; choose a new directory" not in p]
        assert problems == [], (path.name, problems)


# ---------------------------------------------------------------------------------------------- beta pool


def beta_truth_frame(rng, a0=1.8, b0=2.6, w0=0.65, n_fit=2500, n_val=400, folds=(1, 2), **kw):
    """The truth CDF of every row is ``BetaCDF_{a0,b0}(w0 * F_model + (1 - w0) * F_market)``, sampled exactly.

    Model and market are unit-variance normals on either side of a row-specific centre, so their linear pool is
    over-dispersed relative to the truth; the exact correction is the beta transform with known shapes.
    """
    df = make_frame(rng, folds=folds, n_fit=n_fit, n_val=n_val, **kw)
    n = len(df)
    centre = rng.normal(0.0, 0.5, n)
    low, high = centre - 0.7, centre + 0.7
    target = special.betaincinv(a0, b0, rng.random(n))
    left, right = np.full(n, -15.0), np.full(n, 15.0)
    for _ in range(60):
        mid = (left + right) / 2
        above = w0 * ndtr(mid - low) + (1 - w0) * ndtr(mid - high) > target
        right, left = np.where(above, mid, right), np.where(above, left, mid)
    df["y"] = (left + right) / 2
    add_cdf(df, "mdl", low, 1.0)
    add_cdf(df, "mkt", high, 1.0)
    return df


def beta_method(**over):
    method = {"kind": "beta_pool", "learn": "fit_band", "components": ["model", "market"], "min_rows": 50}
    method.update(over)
    return method


def test_beta_pool_recovers_a_known_dispersion_correction(tmp_path):
    df = beta_truth_frame(np.random.default_rng(400))
    params = params_for(tmp_path, df, beta_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]["global"]
    assert sum(state["weights"]) == pytest.approx(1.0) and state["a"] > 1.4 and state["b"] > 1.3  # sharpened
    assert state["a"] == pytest.approx(1.8, abs=0.35)
    full = joined(forecasts, df)
    val = full[(full["band"] == "val") & (full["fold"] == 2)]
    linear = 0.65 * cdfs(val, "mdl") + 0.35 * cdfs(val, "mkt")  # the true-weight pool: over-dispersed
    truth = special.betainc(1.8, 2.6, linear)
    got = cdf_columns(val)
    # (weights, a, b) trade off along a ridge, so what is recovered is the corrected CDF itself
    assert np.abs(got - truth).mean() < 0.01 and np.abs(got - truth).mean() < 0.2 * np.abs(linear - truth).mean()
    assert tw(got, val["y"]).mean() < 0.95 * tw(linear, val["y"]).mean()
    # the output is the stated transform of the stated pool, recomputed with scipy
    w = np.array(state["weights"])
    expect = special.betainc(state["a"], state["b"], np.clip(w[0] * cdfs(val, "mdl") + w[1] * cdfs(val, "mkt"), 0, 1))
    assert np.allclose(got, expect, atol=1e-12)


@pytest.mark.parametrize("frame", ["dispersed", "mixture"])
def test_beta_pool_is_never_worse_than_the_linear_pool_on_its_learning_rows(tmp_path, frame):
    rng = np.random.default_rng(401)
    df = beta_truth_frame(rng, n_fit=800, n_val=100, folds=(1, 2, 3)) if frame == "dispersed" else pool_frame(rng)
    beta = params_for(tmp_path, df, beta_method(), output=str(tmp_path / "beta"))
    linear = params_for(tmp_path, df, pool_method(), output=str(tmp_path / "linear"))
    run(beta)
    run(linear)
    b, fit_b, _ = read_out(beta)
    a, _, _ = read_out(linear)
    full_b, full_a = joined(b, df), joined(a, df)
    for fold in (1, 2, 3):
        rows = ((full_b["fold"] == fold) & (full_b["band"] == "fit")).to_numpy()
        score_b = tw(cdf_columns(full_b)[rows], full_b["y"][rows]).mean()
        score_a = tw(cdf_columns(full_a)[rows], full_a["y"][rows]).mean()
        assert score_b <= score_a + 1e-12, (fold, score_b, score_a)
        state = fit_b["folds"][str(fold)]["state"]["global"]
        assert state["objective"] <= state["linear_pool_objective"] + 1e-15
        assert state["linear_pool_objective"] == pytest.approx(score_a, rel=1e-9)  # the exact linear-pool score


def test_beta_pool_output_is_a_monotone_cdf_in_the_unit_interval_and_deterministic(tmp_path):
    df = beta_truth_frame(np.random.default_rng(402), n_fit=600, n_val=100)
    first, second = (params_for(tmp_path, df, beta_method(), output=str(tmp_path / n)) for n in ("a", "b"))
    run(first)
    run(second)
    a, _, _ = read_out(first)
    b, _, _ = read_out(second)
    pd.testing.assert_frame_equal(a, b)
    cdf = cdf_columns(a)
    assert np.isfinite(cdf).all() and cdf.min() >= 0 and cdf.max() <= 1
    assert (np.diff(cdf, axis=1) >= -1e-12).all()


def test_beta_pool_state_objective_is_the_weighted_score_on_the_strided_learning_rows(tmp_path):
    df = beta_truth_frame(np.random.default_rng(403), n_fit=1500, n_val=100)
    method = beta_method(max_learning_rows=500, half_life_days=30)
    params = params_for(tmp_path, df, method)
    run(params)
    _, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]["global"]
    rows = df[(df["band"] == "fit") & (df["fold"] == 2)]
    assert state["rows"] == len(rows) and state["rows_used"] == len(range(0, len(rows), 3))
    used = rows.iloc[::3]
    omega = recency(used, first_val(df, 2), 30.0)
    w = np.array(state["weights"])
    pooled = np.clip(w[0] * cdfs(used, "mdl") + w[1] * cdfs(used, "mkt"), 0, 1)
    score = tw(special.betainc(state["a"], state["b"], pooled), used["y"].to_numpy())
    assert state["objective"] == pytest.approx((omega * score).sum() / omega.sum(), rel=1e-9)
    lp = np.array(state["linear_pool_weights"])
    score = tw(lp[0] * cdfs(used, "mdl") + lp[1] * cdfs(used, "mkt"), used["y"].to_numpy())
    assert state["linear_pool_objective"] == pytest.approx((omega * score).sum() / omega.sum(), rel=1e-9)


def test_beta_pool_buckets_use_their_own_parameters_and_small_ones_the_global(tmp_path):
    df = beta_truth_frame(np.random.default_rng(404), n_fit=900, n_val=100)
    df["symbol"] = np.where(np.arange(len(df)) % 2 == 0, "A", "B")
    rare = df.index[(df["fold"] == 2) & (df["band"] == "fit")][:5]
    df.loc[rare, "symbol"] = "C"
    params = params_for(tmp_path, df, beta_method(by="symbol"))
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]
    assert sorted(state["buckets"]) == ["A", "B"]
    assert state["buckets"]["A"]["a"] != state["buckets"]["B"]["a"]
    full = joined(forecasts, df)
    for symbol, got in (("A", state["buckets"]["A"]), ("B", state["buckets"]["B"]), ("C", state["global"])):
        rows = full[(full["fold"] == 2) & (full["symbol"] == symbol)]
        w = np.array(got["weights"])
        expect = special.betainc(got["a"], got["b"], np.clip(w[0] * cdfs(rows, "mdl") + w[1] * cdfs(rows, "mkt"), 0, 1))
        assert np.allclose(cdf_columns(rows), expect, atol=1e-12), symbol


def test_beta_pool_with_too_few_learning_rows_falls_back_unfitted(tmp_path):
    df = beta_truth_frame(np.random.default_rng(405), n_fit=60, n_val=60)
    params = params_for(tmp_path, df, beta_method(min_rows=100), fallback="model")
    run(params)
    forecasts, fit, summary = read_out(params)
    assert all(not r["fitted"] and r["reason"] == "the method declined to fit" for r in fit["folds"].values())
    assert summary["rows_not_fitted"] == len(df)
    assert np.allclose(cdf_columns(joined(forecasts, df)), cdfs(joined(forecasts, df), "mdl"))


def test_beta_pool_gradient_matches_finite_differences():
    rng = np.random.default_rng(406)
    grid = cgm._Grid(Z, LABELS, [0.25] * 4 + [1.0] * 5 + [0.25] * 4, 4.0)
    centre = rng.normal(size=(40, 1))
    stack = np.stack([ndtr(np.array(Z)[None, :] - centre - s) for s in (-0.5, 0.2, 0.9)], axis=1)
    y = rng.normal(size=40)
    objective = cgm._BetaPoolObjective(grid, stack, y, rng.random(40) + 0.1)
    theta = np.array([0.3, -0.2, 0.1, 0.4, -0.3])
    value, grad = objective.value_and_grad(theta)
    for i in range(len(theta)):
        step = np.zeros_like(theta)
        step[i] = 1e-6
        numeric = (objective.value_and_grad(theta + step)[0] - objective.value_and_grad(theta - step)[0]) / 2e-6
        assert grad[i] == pytest.approx(numeric, rel=2e-4, abs=1e-8), i


# --------------------------------------------------------------------------------------- quantile average


def quantile_method(**over):
    method = {"kind": "quantile_average", "learn": "fit_band", "components": ["model", "market"], "min_rows": 50,
              "levels": 199}
    method.update(over)
    return method


def test_the_quantile_function_of_a_grid_cdf_follows_the_pit_tail_convention():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    inverse = cgm._QuantileGrid(grid, 9)
    levels = np.arange(1, 10) / 10
    assert np.allclose(inverse.p, np.r_[0.0, levels, 1.0])
    normal = ndtr(np.array(Z))[None, :]
    q = inverse.invert(normal)
    assert q.shape == (1, 11) and q[0, 0] == Z[0] - 4.0 and q[0, -1] == Z[-1] + 4.0
    assert np.allclose(q[0, 3:8], special.ndtri(levels[2:7]), atol=0.02)  # the interior tracks the normal
    # a CDF that starts at 0.1: the left tail is linear from (z0 - tail, 0) to (z0, 0.1); level 0.05 is its middle
    cdf = np.array([[0.1] + [0.1 + 0.8 * (i + 1) / 12 for i in range(12)]])
    q = cgm._QuantileGrid(grid, 19).invert(cdf)
    assert q[0, 1] == pytest.approx(Z[0] - 4.0 + 0.5 * 4.0)  # level 0.05
    assert q[0, 19] == pytest.approx(Z[-1] + 4.0 * (0.95 - 0.9) / (1.0 - 0.9))  # level 0.95 in the right tail


def test_a_flat_part_of_the_cdf_inverts_to_the_left_end_of_the_plateau_deterministically():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    cdf = np.array([[0.0, 0.0, 0.3, 0.3, 0.3, 0.3, 0.3, 0.6, 0.6, 0.9, 1.0, 1.0, 1.0]])
    inverse = cgm._QuantileGrid(grid, 9)  # levels 0.1 .. 0.9
    q = inverse.invert(cdf)[0]
    # F is 0.3 on [z_2, z_6] = [-2, 0]: level 0.3 sits at the plateau's left end, 0.4 is past its right end
    assert q[3] == pytest.approx(-2.0)
    assert q[4] == pytest.approx(0.0 + 0.5 * (0.4 - 0.3) / (0.6 - 0.3))
    assert np.array_equal(q, inverse.invert(cdf)[0]) and (np.diff(q) >= 0).all()


def test_the_pooled_cdf_is_the_inverse_of_the_averaged_quantile_function():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    inverse = cgm._QuantileGrid(grid, 199)
    z = np.array(Z)
    shifted = [ndtr(z[None, :] - mu) for mu in (-1.0, 1.0)]
    pooled = 0.5 * inverse.invert(shifted[0]) + 0.5 * inverse.invert(shifted[1])
    _, cdf = inverse.locate(pooled)
    assert np.allclose(cdf[0], ndtr(z), atol=0.03)  # the location-averaged normal ...
    mixture = 0.5 * shifted[0][0] + 0.5 * shifted[1][0]
    assert np.abs(cdf[0] - mixture).max() > 0.08  # ... not the mixture
    assert (np.diff(cdf, axis=1) >= 0).all() and cdf.min() >= 0 and cdf.max() <= 1


def test_the_pooled_cdf_jumps_to_the_upper_level_where_the_pooled_quantile_function_is_flat():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    inverse = cgm._QuantileGrid(grid, 9)  # levels 0.1 .. 0.9
    pooled = np.array([[-7.0, -3.5, -3.2, -2.5, -1.0, -1.0, -0.5, 0.5, 1.5, 2.5, 7.0]])
    _, cdf = inverse.locate(pooled)
    assert cdf[0, 4] == pytest.approx(0.5)  # z = -1.0 sits on the flat part Q(0.4) = Q(0.5): F takes the larger level
    assert cdf[0, 5] == pytest.approx(0.6)  # z = -0.5 is Q(0.6) exactly
    assert (np.diff(cdf, axis=1) >= 0).all()


def test_beta_pool_keeps_the_linear_pool_when_the_optimizer_does_not_beat_it(monkeypatch):
    rng = np.random.default_rng(407)
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    centre = rng.normal(size=(60, 1))
    stack = np.stack([ndtr(np.array(Z)[None, :] - centre - s) for s in (-0.5, 0.6)], axis=1)
    objective = cgm._BetaPoolObjective(grid, stack, rng.normal(size=60), None)
    lp = np.array([0.0, 1.0])  # a boundary solution: its logits are lifted off zero, so the start is not exact

    def worse(fun, x0, **kwargs):
        return types.SimpleNamespace(x=np.asarray(x0) + 3.0, fun=1e9, nit=0, success=False)

    monkeypatch.setattr("scipy.optimize.minimize", worse)
    solved = objective.solve(lp)
    assert np.array_equal(solved["weights"], lp) and solved["a"] == 1.0 and solved["b"] == 1.0
    assert solved["objective"] == solved["linear_pool_objective"] == objective.loss(lp, 1.0, 1.0)
    assert solved["converged"] is False


def test_quantile_average_of_two_location_shifted_normals_is_the_location_averaged_normal(tmp_path):
    rng = np.random.default_rng(410)
    df = make_frame(rng, folds=(1, 2), n_fit=1500, n_val=100)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mdl", -1.0, 1.0)
    add_cdf(df, "mkt", 1.0, 1.0)
    params = params_for(tmp_path, df, quantile_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]["global"]
    assert state["weights"] == pytest.approx([0.5, 0.5], abs=0.08)
    full = joined(forecasts, df)
    got = cdf_columns(full[full["fold"] == 2])
    w = np.array(state["weights"])
    centre = w[0] * -1.0 + w[1] * 1.0
    assert np.allclose(got, ndtr(np.array(Z) - centre)[None, :], atol=0.035)
    mixture = 0.5 * ndtr(np.array(Z) + 1.0) + 0.5 * ndtr(np.array(Z) - 1.0)
    assert np.abs(got - mixture[None, :]).max() > 0.06
    assert (np.diff(got, axis=1) >= -1e-12).all() and got.min() >= 0 and got.max() <= 1
    assert state["rows"] == 1500 and state["levels"] == 199


def test_quantile_average_weights_favour_the_better_component_and_use_the_stride(tmp_path):
    rng = np.random.default_rng(411)
    df = make_frame(rng, folds=(1, 2), n_fit=1200, n_val=100)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mdl", 0.0, 1.0)
    add_cdf(df, "mkt", 0.8, 1.6)
    params = params_for(tmp_path, df, quantile_method(max_learning_rows=400, levels=99))
    run(params)
    _, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]["global"]
    assert state["weights"][0] > 0.8 and sum(state["weights"]) == pytest.approx(1.0)
    assert state["rows"] == 1200 and state["rows_used"] == len(range(0, 1200, 3)) and state["levels"] == 99


def test_quantile_average_with_too_few_rows_and_buckets(tmp_path):
    rng = np.random.default_rng(412)
    df = make_frame(rng, folds=(1, 2), n_fit=700, n_val=60)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mdl", 0.0, 1.0)
    add_cdf(df, "mkt", 0.4, 1.4)
    params = params_for(tmp_path, df, quantile_method(by="symbol", levels=49))
    run(params)
    forecasts, fit, _ = read_out(params)
    assert sorted(fit["folds"]["2"]["state"]["buckets"]) == ["A", "B"]
    assert np.isfinite(cdf_columns(forecasts)).all()
    declined = params_for(tmp_path, df, quantile_method(min_rows=10_000), output=str(tmp_path / "declined"))
    run(declined)
    _, fit_d, _ = read_out(declined)
    assert all(not r["fitted"] for r in fit_d["folds"].values())


def test_quantile_average_gradient_matches_finite_differences():
    rng = np.random.default_rng(413)
    grid = cgm._Grid(Z, LABELS, [0.25] * 4 + [1.0] * 5 + [0.25] * 4, 4.0)
    centre = rng.normal(size=(40, 1))
    stack = np.stack([ndtr((np.array(Z)[None, :] - centre - s) / sd) for s, sd in ((-0.5, 1.0), (0.2, 1.3), (0.9, 0.8))], axis=1)
    objective = cgm._QuantileAverageObjective(grid, stack, rng.normal(size=40), rng.random(40) + 0.1, levels=99)
    theta = np.array([0.3, -0.2, 0.1])
    value, grad = objective.value_and_grad(theta)
    for i in range(3):
        step = np.zeros(3)
        step[i] = 1e-6
        numeric = (objective.value_and_grad(theta + step)[0] - objective.value_and_grad(theta - step)[0]) / 2e-6
        assert grad[i] == pytest.approx(numeric, rel=1e-3, abs=1e-8), i


def test_quantile_average_rolls_and_weights_by_recency(tmp_path):
    df = roll_frame(np.random.default_rng(414), drift=True, n_val=500)
    params = params_for(tmp_path, df, quantile_method(learn="rolling", refit_days=28, half_life_days=60, levels=49,
                                                      max_learning_rows=600))
    run(params)
    forecasts, fit, _ = read_out(params)
    assert all(b["fitted"] for b in fit["folds"]["3"]["blocks"])
    cdf = cdf_columns(forecasts)
    assert np.isfinite(cdf).all() and (np.diff(cdf, axis=1) >= -1e-12).all()


NEW_KIND_REFUSALS = {
    "beta one component": (lambda m: m.update(kind="beta_pool", components=["model"]), "components"),
    "beta nine components": (lambda m: m.update(kind="beta_pool", components=[f"c{i}" for i in range(9)]), "components"),
    "beta unknown component": (lambda m: m.update(kind="beta_pool", components=["model", "ghost"]), "ghost"),
    "beta duplicate component": (lambda m: m.update(kind="beta_pool", components=["model", "model"]), "components"),
    "beta no min_rows": (lambda m: (m.update(kind="beta_pool"), m.pop("min_rows")), "min_rows"),
    "beta zero min_rows": (lambda m: m.update(kind="beta_pool", min_rows=0), "min_rows"),
    "beta list by": (lambda m: m.update(kind="beta_pool", by=["symbol"]), "by"),
    "beta zero cap": (lambda m: m.update(kind="beta_pool", max_learning_rows=0), "max_learning_rows"),
    "beta float cap": (lambda m: m.update(kind="beta_pool", max_learning_rows=1.5), "max_learning_rows"),
    "beta levels": (lambda m: m.update(kind="beta_pool", levels=99), "levels"),
    "beta quantile_by": (lambda m: m.update(kind="beta_pool", quantile_by={"column": "v", "quantiles": [0.5]}), "quantile_by"),
    "average levels too few": (lambda m: m.update(kind="quantile_average", levels=2), "levels"),
    "average float levels": (lambda m: m.update(kind="quantile_average", levels=99.5), "levels"),
    "average bool levels": (lambda m: m.update(kind="quantile_average", levels=True), "levels"),
    "average per_threshold": (lambda m: m.update(kind="quantile_average", per_threshold=True), "per_threshold"),
    "average one component": (lambda m: m.update(kind="quantile_average", components=["market"]), "components"),
}


@pytest.mark.parametrize("name", sorted(NEW_KIND_REFUSALS))
def test_new_kinds_refuse_malformed_keys(tmp_path, name):
    _, params = valid_params(tmp_path)
    change, needle = NEW_KIND_REFUSALS[name]
    change(params["method"])
    problems = GridCDFMethod.validate_params(params)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


@pytest.mark.parametrize("kind", ["beta_pool", "quantile_average"])
def test_new_kinds_with_every_key_pass(tmp_path, kind):
    _, params = valid_params(tmp_path)
    params["method"].update(kind=kind, by="symbol", max_learning_rows=1000, half_life_days=30, learn="rolling",
                            refit_days=21)
    if kind == "quantile_average":
        params["method"]["levels"] = 99
    assert GridCDFMethod.validate_params(params) == []
    assert set(cgm.METHOD_KINDS) >= {"beta_pool", "quantile_average"}


# --------------------------------------------------------------------------------- external components


def external_setup(tmp_path, seed=420, keys=("row_id", "quote_date")):
    """A main source whose model is too wide, plus another rows file whose same-prefix forecaster is right."""
    rng = np.random.default_rng(seed)
    df = make_frame(rng, folds=(1, 2), n_fit=600, n_val=200)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mdl", 0.0, 3.0)  # the internal "mdl" forecaster: far too wide
    add_cdf(df, "mkt", 0.3, 1.2)
    ext = df[list(dict.fromkeys([*keys, "fold", "band"]))].copy()
    add_cdf(ext, "mdl", 0.0, 1.0)  # the SAME prefix "mdl", in the other file: right
    return df, ext


def external_component(path, keys=("row_id", "quote_date"), **over):
    spec = {"name": "model_heads", "source": "mdl", "path": str(path), "sha256": sha(path), "keys": list(keys)}
    spec.update(over)
    return spec


def external_params(tmp_path, df, ext, keys=("row_id", "quote_date"), method=None, name="other.parquet", **over):
    path = write_source(tmp_path, ext, name)
    components = [{"name": "market", "source": "mkt"}, {"name": "model", "source": "mdl"},
                  external_component(path, keys)]
    method = method or pool_method(components=["model", "model_heads"])
    return params_for(tmp_path, df, method, components=components, **over)


def test_two_components_with_one_source_prefix_stay_distinct_one_internal_one_external(tmp_path):
    df, ext = external_setup(tmp_path)
    params = external_params(tmp_path, df, ext)
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]["global"]
    assert state["weights"][1] > 0.9  # the external, correctly-wide forecaster wins
    full = joined(forecasts, df)
    w = np.array(state["weights"])
    merged = full[["row_id"]].merge(ext, on="row_id", how="left")
    expect = w[0] * cdfs(full, "mdl") + w[1] * cdfs(merged, "mdl")  # internal columns from main, external from the file
    fold2 = (full["fold"] == 2).to_numpy()
    assert np.allclose(cdf_columns(full)[fold2], expect[fold2], atol=1e-12)
    assert np.abs(cdfs(full, "mdl") - cdfs(merged, "mdl")).max() > 0.1  # the two really differ


def test_external_keys_may_include_fold_and_band(tmp_path):
    keys = ("row_id", "quote_date", "fold", "band")
    df, ext = external_setup(tmp_path, keys=keys)
    params = external_params(tmp_path, df, ext, keys=keys)
    run(params)
    _, fit, _ = read_out(params)
    assert fit["external"]["model_heads"]["keys"] == list(keys)
    assert fit["external"]["model_heads"]["coverage"] == {"1": 1.0, "2": 1.0}


def test_external_rows_without_a_match_are_nan_unusable_for_learning_and_fall_back(tmp_path):
    df, ext = external_setup(tmp_path)
    gone = df["row_id"] % 5 == 0
    ext = ext[~ext["row_id"].isin(df.loc[gone, "row_id"])]
    params = external_params(tmp_path, df, ext, fallback="model")
    run(params)
    forecasts, fit, summary = read_out(params)
    full = joined(forecasts, df)
    missing = (full["row_id"] % 5 == 0).to_numpy()
    assert not full["fitted"].to_numpy()[missing].any() and full["fitted"].to_numpy()[~missing].all()
    assert np.allclose(cdf_columns(full)[missing], cdfs(full, "mdl")[missing])  # the fallback, the main model
    assert summary["rows_not_fitted"] == int(missing.sum())
    rows = df[(df["band"] == "fit") & (df["fold"] == 2) & (df["row_id"] % 5 != 0)]
    assert fit["folds"]["2"]["learning_rows"] == len(rows)
    for fold in (1, 2):
        share = float((df.loc[df["fold"] == fold, "row_id"] % 5 != 0).mean())
        assert fit["external"]["model_heads"]["coverage"][str(fold)] == pytest.approx(share, abs=1e-12)


def test_an_external_file_with_the_wrong_sha_is_refused_before_anything_is_written(tmp_path):
    df, ext = external_setup(tmp_path)
    params = external_params(tmp_path, df, ext)
    params["components"][2]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        run(params)
    assert not (tmp_path / "out").exists() and not (tmp_path / ".out.partial").exists()


def test_duplicate_external_keys_are_refused_after_the_end_before_filter(tmp_path):
    df, ext = external_setup(tmp_path)
    late = ext.iloc[:2].copy()
    late["quote_date"] = pd.Timestamp("2031-01-01").as_unit("ns")  # at or after end_before: never read, so duplicates there are fine
    late["row_id"] = 10 ** 9
    ok = external_params(tmp_path, df, pd.concat([ext, late, late], ignore_index=True))
    run(ok)
    assert (tmp_path / "out").is_dir()
    duplicated = pd.concat([ext, ext.iloc[:3]], ignore_index=True)
    bad = external_params(tmp_path, df, duplicated, output=str(tmp_path / "bad"), name="dup.parquet")
    with pytest.raises(ValueError, match="duplicate"):
        run(bad)
    assert not (tmp_path / "bad").exists() and not (tmp_path / ".bad.partial").exists()


def test_an_external_pit_column_comes_from_the_external_file(tmp_path):
    df, ext = external_setup(tmp_path)
    ext["pit"] = np.linspace(0.0, 1.0, len(ext)) ** 2  # deliberately not the grid PIT of the outcome
    path = write_source(tmp_path, ext, "pit.parquet")
    components = [{"name": "market", "source": "mkt"}, {"name": "model", "source": "mdl"},
                  external_component(path, pit_column="pit")]
    method = {"kind": "pit_map", "learn": "fit_band", "component": "model_heads", "knots": 11, "min_rows": 50}
    params = params_for(tmp_path, df, method, components=components)
    run(params)
    _, fit, _ = read_out(params)
    rows = (df["band"] == "fit") & (df["fold"] == 2)
    pit = ext.loc[rows.to_numpy(), "pit"].to_numpy()
    expect = np.array([(pit <= p).mean() for p in np.linspace(0, 1, 11)])
    assert np.allclose(fit["folds"]["2"]["state"]["global"]["g"], expect, atol=1e-12)


def test_an_external_component_is_absent_from_fit_json_when_none_is_declared(tmp_path):
    df = pool_frame(np.random.default_rng(421), n_fit=100, n_val=50)
    params = params_for(tmp_path, df, pool_method())
    run(params)
    _, fit, _ = read_out(params)
    assert "external" not in fit


EXTERNAL_REFUSALS = {
    "path without sha and keys": (lambda c: (c.pop("sha256"), c.pop("keys")), "path"),
    "sha without path": (lambda c: c.pop("path"), "path"),
    "keys without path": (lambda c: (c.pop("path"), c.pop("sha256")), "keys"),
    "bad sha": (lambda c: c.update(sha256="abc"), "sha256"),
    "empty path": (lambda c: c.update(path=""), "path"),
    "keys without the date column": (lambda c: c.update(keys=["row_id", "symbol"]), "date"),
    "empty keys": (lambda c: c.update(keys=[]), "keys"),
    "duplicate keys": (lambda c: c.update(keys=["quote_date", "quote_date"]), "keys"),
    "non-list keys": (lambda c: c.update(keys="quote_date"), "keys"),
    "unknown component key": (lambda c: c.update(surprise=1), "surprise"),
}


@pytest.mark.parametrize("name", sorted(EXTERNAL_REFUSALS))
def test_external_component_keys_are_validated(tmp_path, name):
    df, ext = external_setup(tmp_path)
    params = external_params(tmp_path, df, ext)
    assert GridCDFMethod.validate_params(params) == []
    change, needle = EXTERNAL_REFUSALS[name]
    change(params["components"][2])
    problems = GridCDFMethod.validate_params(params)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


# ------------------------------------------------------------------ round-two pins (skeptic review of ADR-0270)


def interp_quantiles(cdf, levels, tail=4.0):
    """The quantile function of one strictly increasing grid CDF at ``l / (levels + 1)``, closed by the PIT tails.

    Recomputed with ``np.interp`` through the knots ``(z_first - tail, 0), (z_k, F_k), (z_last + tail, 1)``;
    the two end levels 0 and 1 are the tail ends. No running maximum: the inputs of its callers are monotone.
    """
    x = np.r_[Z[0] - tail, Z, Z[-1] + tail]
    p = np.r_[0.0, np.arange(1, levels + 1) / (levels + 1), 1.0]
    return np.interp(p, np.r_[0.0, cdf, 1.0], x)


def averaged_quantile_cdf(components, weights, levels, tail=4.0):
    """The CDF on the grid of ``sum_i w_i Q_i`` per row, where ``components`` are ``(rows, 13)`` CDF arrays.

    Component ``i`` takes ``weights[i]`` (the order the document lists them in); the averaged quantile
    function is read back at the grid points by linear interpolation of the level against the quantile.
    """
    p = np.r_[0.0, np.arange(1, levels + 1) / (levels + 1), 1.0]
    out = []
    for row in zip(*components):
        pooled = sum(w * interp_quantiles(cdf, levels, tail) for w, cdf in zip(weights, row))
        out.append(np.interp(Z, pooled, p))
    return np.array(out)


def asymmetric_average_frame(rng, n_fit=700, n_val=200, folds=(1, 2)):
    """Truth N(0.85, 1.4), close to the second of N(0, 1) and N(1, 1.5): unequal weights (about 0.2 / 0.8) are optimal."""
    df = make_frame(rng, folds=folds, n_fit=n_fit, n_val=n_val)
    df["y"] = rng.normal(0.85, 1.4, len(df))
    add_cdf(df, "mdl", 0.0, 1.0)
    add_cdf(df, "mkt", 1.0, 1.5)
    return df


def mean_score(cdf, rows, omega=None):
    """The omega-weighted mean grid twCRPS of ``cdf`` against the outcomes of ``rows`` (equal weights if None)."""
    score = tw(cdf, rows["y"].to_numpy())
    return float(score.mean() if omega is None else (omega * score).sum() / omega.sum())


def test_quantile_average_applies_the_fitted_weights_to_the_components_in_document_order(tmp_path):
    df = asymmetric_average_frame(np.random.default_rng(430))
    params = params_for(tmp_path, df, quantile_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    full = joined(forecasts, df)
    for fold in (1, 2):
        state = fit["folds"][str(fold)]["state"]["global"]
        w = state["weights"]
        assert w[0] < 0.3 < 0.7 < w[1]  # unequal, so the order and the values matter
        rows = full[full["fold"] == fold]  # the fit band and the validation band alike
        expect = averaged_quantile_cdf([cdfs(rows, "mdl"), cdfs(rows, "mkt")], w, 199)
        assert np.allclose(cdf_columns(rows), expect, atol=1e-9)
        # the neighbouring mistakes move the forecast far more than the tolerance
        assert np.abs(averaged_quantile_cdf([cdfs(rows, "mdl"), cdfs(rows, "mkt")], [0.5, 0.5], 199)
                      - expect).max() > 0.02
        assert np.abs(averaged_quantile_cdf([cdfs(rows, "mkt"), cdfs(rows, "mdl")], w, 199) - expect).max() > 0.02


def test_a_nondefault_levels_drives_both_the_quantile_average_objective_and_its_applied_forecast(tmp_path):
    df = asymmetric_average_frame(np.random.default_rng(431), n_fit=500)
    params = params_for(tmp_path, df, quantile_method(levels=49))
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]["global"]
    assert state["levels"] == 49
    rows = df[(df["band"] == "fit") & (df["fold"] == 2)]
    comps = [cdfs(rows, "mdl"), cdfs(rows, "mkt")]
    for key, weights in (("objective", state["weights"]), ("start_objective", state["start_weights"])):
        at_49 = mean_score(averaged_quantile_cdf(comps, weights, 49), rows)
        at_199 = mean_score(averaged_quantile_cdf(comps, weights, 199), rows)
        assert state[key] == pytest.approx(at_49, rel=1e-9), key
        assert abs(at_49 - at_199) > 1e-6, key  # the default would have given another number
    full = joined(forecasts, df)
    every = full[full["fold"] == 2]
    both = [cdfs(every, "mdl"), cdfs(every, "mkt")]
    assert np.allclose(cdf_columns(every), averaged_quantile_cdf(both, state["weights"], 49), atol=1e-9)
    assert np.abs(averaged_quantile_cdf(both, state["weights"], 199) - cdf_columns(every)).max() > 1e-4


@pytest.mark.parametrize("kind", ["beta_pool", "quantile_average"])
def test_bucket_cells_of_a_parametric_pool_are_fitted_with_the_recency_weights(tmp_path, kind):
    df = roll_frame(np.random.default_rng(440), drift=True, n_val=500)
    extra = {"levels": 49} if kind == "quantile_average" else {}
    base = {"kind": kind, "learn": "prior_validation", "components": ["model", "market"], "min_rows": 50,
            "by": "symbol", **extra}
    aged = params_for(tmp_path, df, {**base, "half_life_days": 60}, output=str(tmp_path / "aged"))
    plain = params_for(tmp_path, df, base, output=str(tmp_path / "plain"))
    run(aged)
    run(plain)
    _, fit_aged, _ = read_out(aged)
    _, fit_plain, _ = read_out(plain)
    reference = first_val(df, 3)
    state, state_plain = fit_aged["folds"]["3"]["state"], fit_plain["folds"]["3"]["state"]
    assert sorted(state["buckets"]) == ["A", "B"]
    start = "linear_pool" if kind == "beta_pool" else "start"
    for symbol in ("A", "B"):
        rows = df[(df["band"] == "val") & (df["fold"] < 3) & (df["settlement_date"] < reference)
                  & (df["symbol"] == symbol)]
        omega = recency(rows, reference, 60.0)
        assert omega.min() < 0.1 * omega.max()
        cell, cell_plain = state["buckets"][symbol], state_plain["buckets"][symbol]
        assert cell["rows"] == len(rows)  # the count is of rows, whatever their weight
        # the starting point is the exact weighted linear pool of this bucket's rows ...
        assert cell[f"{start}_weights"][0] == pytest.approx(pool_weight_closed_form(rows, omega), abs=1e-9)
        # ... and the fitted objective is the weighted mean score of the cell's own parameters
        w = np.array(cell["weights"])
        comps = [cdfs(rows, "mdl"), cdfs(rows, "mkt")]
        if kind == "beta_pool":
            fitted = special.betainc(cell["a"], cell["b"], np.clip(w[0] * comps[0] + w[1] * comps[1], 0, 1))
        else:
            fitted = averaged_quantile_cdf(comps, w, 49)
        assert cell["objective"] == pytest.approx(mean_score(fitted, rows, omega), rel=1e-8)
        assert cell["objective"] != pytest.approx(mean_score(fitted, rows), rel=1e-4)  # equal weights would differ
        assert abs(cell[f"{start}_weights"][0] - cell_plain[f"{start}_weights"][0]) > 0.05  # recency moved it
        assert abs(cell["objective"] - cell_plain["objective"]) > 1e-4


@pytest.mark.parametrize("kind", ["beta_pool", "quantile_average"])
def test_parametric_pools_fit_exactly_at_min_rows_and_decline_one_row_below(tmp_path, kind):
    n, small = 120, 40
    rng = np.random.default_rng(450)
    df = make_frame(rng, folds=(1,), n_fit=n, n_val=20)
    df["y"] = rng.normal(0.3, 1.2, len(df))
    add_cdf(df, "mdl", 0.0, 1.0)
    add_cdf(df, "mkt", 0.5, 1.5)
    df["symbol"] = "B"
    df.loc[df.index[df["band"] == "fit"][:small], "symbol"] = "A"  # 40 rows in A, 80 in B, 120 in all
    base = {"kind": kind, "learn": "fit_band", "components": ["model", "market"]}

    def fitted(min_rows, tag, **over):
        params = params_for(tmp_path, df, {**base, "min_rows": min_rows, **over}, output=str(tmp_path / tag))
        run(params)
        return read_out(params)[1]["folds"]["1"]

    at, below = fitted(n, "global_at"), fitted(n + 1, "global_below")
    assert at["fitted"] is True and at["state"]["global"]["rows"] == n
    assert below["fitted"] is False and below["state"] is None and below["reason"] == "the method declined to fit"
    assert sorted(fitted(small, "bucket_at", by="symbol")["state"]["buckets"]) == ["A", "B"]
    assert sorted(fitted(small + 1, "bucket_below", by="symbol")["state"]["buckets"]) == ["B"]


@pytest.mark.parametrize("kind", ["beta_pool", "quantile_average"])
def test_max_learning_rows_takes_the_stride_rounded_up(tmp_path, kind):
    rng = np.random.default_rng(451)
    df = make_frame(rng, folds=(1,), n_fit=1000, n_val=20)
    df["y"] = rng.normal(0.7, 1.35, len(df))
    add_cdf(df, "mdl", 0.0, 1.0)
    add_cdf(df, "mkt", 1.0, 1.5)
    method = {"kind": kind, "learn": "fit_band", "components": ["model", "market"], "min_rows": 50,
              "max_learning_rows": 300, **({"levels": 49} if kind == "quantile_average" else {})}
    params = params_for(tmp_path, df, method)
    run(params)
    state = read_out(params)[1]["folds"]["1"]["state"]["global"]
    # 1000 / 300 = 3.33: the stride is 4 (not 3), so 250 rows are used and they are every fourth one
    assert state["rows"] == 1000 and state["rows_used"] == len(range(0, 1000, 4)) == 250
    used = df[(df["band"] == "fit") & (df["fold"] == 1)].iloc[::4]
    comps, w = [cdfs(used, "mdl"), cdfs(used, "mkt")], np.array(state["weights"])
    if kind == "beta_pool":
        fitted = special.betainc(state["a"], state["b"], np.clip(w[0] * comps[0] + w[1] * comps[1], 0, 1))
    else:
        fitted = averaged_quantile_cdf(comps, w, 49)
    assert state["objective"] == pytest.approx(mean_score(fitted, used), rel=1e-9)


def test_the_fold_record_counts_the_rows_fitted_over_all_its_rolling_blocks(tmp_path):
    df = roll_frame(np.random.default_rng(452), n_fit=100, n_val=400)
    params = params_for(tmp_path, df, rolling_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    for fold in (1, 2, 3):
        record = fit["folds"][str(fold)]
        blocks = record["blocks"]
        flagged = int(forecasts.loc[forecasts["fold"] == fold, "fitted"].sum())
        assert record["rows_fitted"] == sum(b["rows_fitted"] for b in blocks) == flagged
        assert max(b["rows_fitted"] for b in blocks) < record["rows_fitted"]  # more than any one block alone
    first = fit["folds"]["1"]
    assert first["blocks"][0]["fitted"] is False and first["rows_fitted"] < first["rows"]  # nothing settled yet


def test_a_fold_record_names_its_weight_reference_only_for_a_single_block_rule_with_a_half_life(tmp_path):
    df = roll_frame(np.random.default_rng(453), n_fit=80, n_val=150)
    records = {}
    for tag, method in (("fit_band", pool_method(half_life_days=30)),
                        ("prior", pool_method(learn="prior_validation", half_life_days=30)),
                        ("plain", pool_method()),
                        ("rolling", rolling_method(half_life_days=30))):
        params = params_for(tmp_path, df, method, output=str(tmp_path / tag))
        run(params)
        records[tag] = read_out(params)[1]["folds"]
    for fold in (1, 2, 3):
        day = first_val(df, fold).date().isoformat()
        assert records["fit_band"][str(fold)]["reference"] == day
        assert records["prior"][str(fold)]["reference"] == day
        assert "reference" not in records["plain"][str(fold)]
        assert "reference" not in records["rolling"][str(fold)] and "blocks" not in records["plain"][str(fold)]
        assert records["rolling"][str(fold)]["blocks"][0]["start"] == day  # a block's start is its reference


def left_inverse(raw, level, tail=4.0):
    """``inf {z : F(z) >= level}`` of the piecewise-linear CDF through ``raw`` (the running maximum of it).

    The first knot whose raw value reaches the level is found by a plain scan, and the segment before it
    runs from the highest value seen so far (below the level) at the previous knot.
    """
    x = np.r_[Z[0] - tail, Z, Z[-1] + tail]
    f = np.r_[0.0, raw, 1.0]
    k = next(i for i, value in enumerate(f) if value >= level)
    low = max(f[:k])
    return x[k - 1] + (level - low) / (f[k] - low) * (x[k] - x[k - 1])


def test_the_quantile_function_reads_a_nonmonotone_cdf_as_its_running_maximum():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    inverse = cgm._QuantileGrid(grid, 99)
    base = np.linspace(0.05, 0.95, 13)
    dipped = []
    for at in (3, 6, 9):  # a dip of 0.02 below the previous knot, in three places
        row = base.copy()
        row[at] = row[at - 1] - 0.02
        dipped.append(row)
    got = inverse.invert(np.array(dipped))
    levels = np.arange(1, 100) / 100
    for row, quantiles in zip(dipped, got):
        expect = [left_inverse(row, p) for p in levels]
        assert np.allclose(quantiles[1:-1], expect, atol=1e-12)
        assert (np.diff(quantiles) >= 0).all()
        assert (np.abs(quantiles[1:-1] - [left_inverse(base, p) for p in levels]) > 1e-3).any()  # the dip matters


def test_an_external_join_on_several_keys_outside_the_identity_roles_matches_each_row(tmp_path):
    rng = np.random.default_rng(454)
    df = make_frame(rng, folds=(1, 2), n_fit=200, n_val=80)
    df["y"] = rng.normal(size=len(df))
    add_cdf(df, "mdl", 0.0, 1.0)
    add_cdf(df, "mkt", 0.3, 1.2)
    seen = df.groupby("quote_date").cumcount().to_numpy()
    df["leg"], df["slot"] = seen % 3, seen // 3  # neither is an identity column; only the triple is unique
    assert not df.duplicated(subset=["quote_date", "leg", "slot"]).any() and df["leg"].nunique() == 3
    centre = lambda frame: 0.4 * frame["leg"].to_numpy() - 0.15 * frame["slot"].to_numpy()
    ext = df[["quote_date", "leg", "slot"]].sample(frac=1.0, random_state=3).reset_index(drop=True)
    add_cdf(ext, "mdl", centre(ext), 1.0)  # a CDF that depends on the key triple, in another row order
    keys = ("leg", "slot", "quote_date")
    path = write_source(tmp_path, ext, "legs.parquet")
    components = [{"name": "market", "source": "mkt"}, {"name": "model", "source": "mdl"},
                  external_component(path, keys, name="heads")]
    declined = beta_method(components=["model", "heads"], min_rows=10 ** 6)  # declines, so the fallback is output
    params = params_for(tmp_path, df, declined, components=components, fallback="heads")
    run(params)
    forecasts, fit, _ = read_out(params)
    full = joined(forecasts, df)
    expect = ndtr((np.array(Z)[None, :] - centre(full)[:, None]) / 1.0)
    assert np.allclose(cdf_columns(full), expect, atol=1e-12)  # every row took its own triple's CDF
    record = fit["external"]["heads"]
    assert record["keys"] == list(keys) and record["rows_read"] == len(df)
    assert record["coverage"] == {"1": 1.0, "2": 1.0}


def test_the_beta_pool_shapes_stop_at_the_log_bound(tmp_path):
    bound = float(np.exp(3.0))  # BETA_LOG_BOUND = 3: a and b never exceed e ** 3
    df = beta_truth_frame(np.random.default_rng(455), a0=80.0, b0=80.0, w0=0.5, n_fit=1500, n_val=50, folds=(1,))
    params = params_for(tmp_path, df, beta_method())
    run(params)
    state = read_out(params)[1]["folds"]["1"]["state"]["global"]
    assert cgm.BETA_LOG_BOUND == 3.0
    assert state["a"] == pytest.approx(bound, rel=1e-9) and state["b"] <= bound * (1 + 1e-9)
    # the truth is the beta transform with shapes 80 of the equal-weight pool: far beyond the bound, and better
    rows = df[(df["band"] == "fit") & (df["fold"] == 1)]
    equal = np.clip(0.5 * cdfs(rows, "mdl") + 0.5 * cdfs(rows, "mkt"), 0, 1)
    assert mean_score(special.betainc(80.0, 80.0, equal), rows) < state["objective"] - 0.005


# ============================================================================== round three (ADR-0270)
# quantile level bands, feature-conditioned weights, the location-scale adjustment


def assert_cdf(cdf):
    """Every row finite, in [0, 1] and non-decreasing."""
    assert np.isfinite(cdf).all() and cdf.min() >= 0 and cdf.max() <= 1
    assert (np.diff(cdf, axis=1) >= -1e-12).all()


def check_gradient(objective, theta, rel=2e-3, abs_tol=1e-8):
    """Compare the analytic gradient with central differences at ``theta``."""
    value, grad = objective.value_and_grad(theta)
    for i in range(len(theta)):
        step = np.zeros_like(theta)
        step[i] = 1e-6
        numeric = (objective.value_and_grad(theta + step)[0] - objective.value_and_grad(theta - step)[0]) / 2e-6
        assert grad[i] == pytest.approx(numeric, rel=rel, abs=abs_tol), (i, grad[i], numeric)
    return value


def three_point_grid():
    return cgm._Grid(Z, LABELS, [0.25] * 4 + [1.0] * 5 + [0.25] * 4, 4.0)


# ------------------------------------------------------------------------------- the component cap


def test_the_simplex_solver_handles_eight_components_exactly():
    q = np.diag([0.5, 1.0, 2.0, 0.25, 4.0, 1.5, 3.0, 0.8])
    w = cgm._SimplexQuadratic.solve(q)
    expect = (1 / np.diag(q)) / (1 / np.diag(q)).sum()
    assert cgm.MAX_POOL_COMPONENTS == 8
    assert np.allclose(w, expect, atol=1e-12) and w.sum() == pytest.approx(1.0)


@pytest.mark.parametrize("kind", ["linear_pool", "beta_pool", "quantile_average"])
def test_seven_and_eight_components_are_allowed_and_nine_refused(tmp_path, kind):
    names = [f"c{i}" for i in range(9)]
    comps = [{"name": n, "source": "mdl"} for n in names]
    df = pool_frame(np.random.default_rng(600), n_fit=30, n_val=30)
    for count, ok in ((7, True), (8, True), (9, False)):
        params = params_for(tmp_path, df, pool_method(kind=kind, components=names[:count]), components=comps,
                            fallback="c0", comparators=["c0"])
        problems = GridCDFMethod.validate_params(params)
        assert (problems == []) is ok, (count, problems)


# ------------------------------------------------------------------------------------- level bands


def band_frame(rng, n_fit=3000, n_val=300, folds=(1, 2)):
    """Truth: the narrow N(0, 0.8) in its lower half and the wide N(0, 2) in its upper half (the quantile function)."""
    df = make_frame(rng, folds=folds, n_fit=n_fit, n_val=n_val)
    u = rng.random(len(df))
    df["y"] = np.where(u < 0.5, 0.8 * ndtri(u), 2.0 * ndtri(u))
    add_cdf(df, "mdl", 0.0, 0.8)
    add_cdf(df, "mkt", 0.0, 2.0)
    return df


def banded_quantile_cdf(components, band_weights, cuts, levels, tail=4.0):
    """The grid CDF of the band-weighted quantile function per row, recomputed with plain numpy.

    Level ``p`` takes the weights of band ``searchsorted(cuts, p, 'right')``; the pooled quantile function is
    read as its running maximum, then inverted at the grid points.
    """
    p = np.r_[0.0, np.arange(1, levels + 1) / (levels + 1), 1.0]
    band = np.searchsorted(cuts, p, side="right")
    w = np.asarray(band_weights)[band].T
    out = []
    for row in zip(*components):
        q = np.array([interp_quantiles(cdf, levels, tail) for cdf in row])
        out.append(np.interp(Z, np.maximum.accumulate((w * q).sum(0)), p))
    return np.array(out)


def test_level_bands_follow_the_component_that_matches_each_part_of_the_distribution(tmp_path):
    df = band_frame(np.random.default_rng(601))
    banded = params_for(tmp_path, df, quantile_method(levels=99, level_bands=[0.3, 0.7]), output=str(tmp_path / "b"))
    single = params_for(tmp_path, df, quantile_method(levels=99), output=str(tmp_path / "s"))
    run(banded)
    run(single)
    forecasts, fit, _ = read_out(banded)
    state = fit["folds"]["2"]["state"]["global"]
    low, body, high = state["band_weights"]  # weights on (model = narrow, market = wide) per band
    assert state["level_bands"] == [0.3, 0.7]
    assert low[0] > 0.8 and high[1] > 0.8 and low[0] - body[0] > 0.5 and low[0] - high[0] > 0.8  # model low, market high
    assert all(sum(w) == pytest.approx(1.0) and min(w) >= 0 for w in state["band_weights"])
    # the joint fit beats the single weight vector on the very rows both learned from
    plain = read_out(single)[1]["folds"]["2"]["state"]["global"]
    assert state["rows_used"] == plain["rows_used"]
    assert state["objective"] < 0.97 * plain["objective"]
    assert state["objective"] <= state["start_objective"]
    assert_cdf(cdf_columns(forecasts))


def test_banded_forecast_applies_each_bands_weights_at_its_levels(tmp_path):
    df = band_frame(np.random.default_rng(602), n_fit=1200, n_val=100)
    params = params_for(tmp_path, df, quantile_method(levels=99, level_bands=[0.15, 0.85]))
    run(params)
    forecasts, fit, _ = read_out(params)
    full = joined(forecasts, df)
    state = fit["folds"]["2"]["state"]["global"]
    rows = full[full["fold"] == 2]
    expect = banded_quantile_cdf([cdfs(rows, "mdl"), cdfs(rows, "mkt")], state["band_weights"], [0.15, 0.85], 99)
    assert np.allclose(cdf_columns(rows), expect, atol=1e-9)
    # a different set of band weights would move the forecast well beyond the tolerance
    other = banded_quantile_cdf([cdfs(rows, "mdl"), cdfs(rows, "mkt")], state["band_weights"][::-1], [0.15, 0.85], 99)
    assert np.abs(other - expect).max() > 0.02


def test_banded_objective_with_equal_band_weights_is_the_single_band_objective():
    rng = np.random.default_rng(603)
    grid = three_point_grid()
    centre = rng.normal(size=(60, 1))
    stack = np.stack([ndtr((np.array(Z)[None, :] - centre - s) / sd) for s, sd in ((-0.5, 1.0), (0.2, 1.3), (0.9, 0.8))], axis=1)
    y, omega = rng.normal(size=60), rng.random(60) + 0.1
    plain = cgm._QuantileAverageObjective(grid, stack, y, omega, levels=49)
    banded = cgm._BandedQuantileAverageObjective(grid, stack, y, omega, 49, [0.2, 0.5, 0.9])
    w = np.array([0.5, 0.3, 0.2])
    assert banded.loss(np.tile(w, (4, 1))) == pytest.approx(plain.loss(w), rel=1e-12)


@pytest.mark.parametrize("theta", [[0.3, -0.2, 0.1, 0.5, -0.4, 0.2, -0.3, 0.1, 0.0],
                                   [3.0, -3.0, 0.0, -3.0, 3.0, 0.0, 2.0, 1.0, -2.0]])
def test_banded_quantile_average_gradient_matches_finite_differences(theta):
    # the second start puts opposite components in neighbouring bands, so the pooled quantile function dips at
    # the band edge and is read as its running maximum
    rng = np.random.default_rng(604)
    grid = three_point_grid()
    centre = rng.normal(size=(50, 1))
    stack = np.stack([ndtr((np.array(Z)[None, :] - centre - s) / sd) for s, sd in ((-1.0, 0.4), (1.0, 0.4), (0.0, 1.2))], axis=1)
    objective = cgm._BandedQuantileAverageObjective(grid, stack, rng.normal(size=50), rng.random(50) + 0.1, 49, [0.3, 0.7])
    check_gradient(objective, np.array(theta))


def test_banded_pooled_cdf_is_monotone_even_when_neighbouring_bands_pick_opposite_components():
    # the lower band takes the component centred at +1.2, the upper bands the one at -1.2, so the pooled quantile
    # function falls at the band edge; it is read as its running maximum and the CDF jumps there
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    rng = np.random.default_rng(605)
    high = ndtr((np.array(Z)[None, :] - 1.2) / 0.4) * np.ones((20, 1))
    low = ndtr((np.array(Z)[None, :] + 1.2) / 0.4) * np.ones((20, 1))
    stack = np.stack([low, high], axis=1)
    spec = {"kind": "quantile_average", "learn": "fit_band", "components": ["model", "market"], "min_rows": 10,
            "levels": 49, "level_bands": [0.3, 0.7]}
    method = cgm.METHOD_KINDS["quantile_average"](spec, {}, grid)
    panel = types.SimpleNamespace(cdf={"model": low, "market": high})
    weights = [[0.0, 1.0], [1.0, 0.0], [1.0, 0.0]]  # (band, component): market below p = 0.3, model above
    got = method._apply(panel, np.arange(20), {"band_weights": weights, "level_bands": [0.3, 0.7]})
    assert_cdf(got)
    objective = cgm._BandedQuantileAverageObjective(grid, stack, rng.normal(size=20), None, 49, [0.3, 0.7])
    raw = objective._combine(np.array(weights))
    assert (np.diff(raw, axis=1) < -0.5).any()  # the unrepaired pooled quantile function does fall
    running, _, cdf = objective._evaluate(np.array(weights))
    assert (np.diff(running, axis=1) >= 0).all() and np.allclose(cdf, got, atol=1e-12)
    # the dip is a flat piece of the quantile function, which the CDF reads as a jump
    assert np.abs(np.diff(got, axis=1)).max() > 0.2


def test_banded_buckets_recency_and_rolling_each_carry_their_band_weights(tmp_path):
    df = roll_frame(np.random.default_rng(606), drift=True, n_val=500)
    params = params_for(tmp_path, df, quantile_method(learn="rolling", refit_days=28, half_life_days=60, levels=49,
                                                      level_bands=[0.5], by="symbol", max_learning_rows=600))
    run(params)
    forecasts, fit, _ = read_out(params)
    blocks = fit["folds"]["3"]["blocks"]
    assert all(b["fitted"] for b in blocks)
    cell = blocks[-1]["state"]["buckets"]["A"]
    assert len(cell["band_weights"]) == 2 and cell["level_bands"] == [0.5]
    assert_cdf(cdf_columns(forecasts))


@pytest.mark.parametrize("cuts,needle", [
    (0.5, "level_bands"), ([], "level_bands"), ([0.0, 0.5], "level_bands"), ([0.5, 1.0], "level_bands"),
    ([-0.1], "level_bands"), ([0.7, 0.3], "level_bands"), ([0.5, 0.5], "level_bands"), ([True], "level_bands"),
    (["0.5"], "level_bands"), ([float("nan")], "level_bands"), ([0.001], "band")])
def test_malformed_level_bands_are_refused(tmp_path, cuts, needle):
    _, params = valid_params(tmp_path)
    params["method"].update(kind="quantile_average", levels=99, level_bands=cuts)
    problems = GridCDFMethod.validate_params(params)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


@pytest.mark.parametrize("kind", ["linear_pool", "beta_pool"])
def test_only_the_quantile_average_takes_level_bands(tmp_path, kind):
    _, params = valid_params(tmp_path)
    params["method"].update(kind=kind, level_bands=[0.5])
    assert any("level_bands" in p for p in GridCDFMethod.validate_params(params))


# ------------------------------------------------------------------------------------- weight features


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def feature_frame(rng, mode, n_fit=3000, n_val=1500, slope=2.0, folds=(1, 2), nan_share=0.0):
    """Truth mixes N(-1, .7) and N(1.5, .8) with the weight ``sigmoid(slope * feat)`` on the first.

    ``mode`` ``linear`` draws each outcome from one part (a linear pool is exact); ``quantile`` takes the weighted
    average of the two quantile functions at one probability (a quantile average is exact).
    """
    df = make_frame(rng, folds=folds, n_fit=n_fit, n_val=n_val)
    n = len(df)
    df["feat"] = rng.normal(size=n)
    df["other"] = rng.normal(size=n)
    w = sigmoid(slope * df["feat"].to_numpy())
    if mode == "linear":
        df["y"] = np.where(rng.random(n) < w, rng.normal(-1, 0.7, n), rng.normal(1.5, 0.8, n))
    else:
        u = ndtri(rng.random(n))
        df["y"] = w * (-1 + 0.7 * u) + (1 - w) * (1.5 + 0.8 * u)
    add_cdf(df, "mdl", -1.0, 0.7)
    add_cdf(df, "mkt", 1.5, 0.8)
    if nan_share:
        df.loc[rng.random(n) < nan_share, "feat"] = np.nan
    return df, w


def featured_method(kind, **over):
    base = pool_method(kind=kind) if kind == "linear_pool" else quantile_method(levels=99)
    return {**base, "weight_features": ["feat"], **over}


def softmax_rows(logits):
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def featured_weights(cell, columns):
    """The row weights softmax(B x + c) recomputed from a fit.json cell and the raw feature columns."""
    x = (np.column_stack(columns) - np.array(cell["feature_mean"])) / np.array(cell["feature_sd"])
    return softmax_rows(x @ np.array(cell["coefficients"]).T + np.array(cell["bias"]))


@pytest.mark.parametrize("kind", ["linear_pool", "quantile_average"])
def test_feature_weights_track_a_truth_whose_mixture_weight_depends_on_the_feature(tmp_path, kind):
    df, w = feature_frame(np.random.default_rng(610), "linear" if kind == "linear_pool" else "quantile")
    params = params_for(tmp_path, df, featured_method(kind))
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    val = df[(df["fold"] == 2) & (df["band"] == "val")]
    fitted = featured_weights(cell, [val["feat"].to_numpy()])[:, 0]
    assert np.corrcoef(fitted, w[val["row_id"].to_numpy()])[0, 1] > 0.9
    assert fitted.max() - fitted.min() > 0.5
    assert cell["objective"] < cell["start_objective"]
    assert_cdf(cdf_columns(forecasts))


@pytest.mark.parametrize("kind", ["linear_pool", "quantile_average"])
def test_feature_weights_beat_the_constant_weight_pool_out_of_sample(tmp_path, kind):
    df, _ = feature_frame(np.random.default_rng(611), "linear" if kind == "linear_pool" else "quantile")
    constant = {k: v for k, v in featured_method(kind).items() if k != "weight_features"}
    featured = params_for(tmp_path, df, featured_method(kind), output=str(tmp_path / "f"))
    plain = params_for(tmp_path, df, constant, output=str(tmp_path / "c"))
    run(featured)
    run(plain)
    score = {}
    for name, params in (("f", featured), ("c", plain)):
        full = joined(read_out(params)[0], df)
        val = full[(full["fold"] == 2) & (full["band"] == "val")]
        score[name] = tw(cdf_columns(val), val["y"]).mean()
    assert score["f"] < 0.97 * score["c"], score


@pytest.mark.parametrize("kind", ["linear_pool", "quantile_average"])
def test_feature_standardization_is_stored_and_nan_rows_are_dropped_from_learning(tmp_path, kind):
    df, _ = feature_frame(np.random.default_rng(612), "linear" if kind == "linear_pool" else "quantile",
                          n_fit=1500, n_val=300, nan_share=0.1)
    params = params_for(tmp_path, df, featured_method(kind))
    run(params)
    _, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]
    finite = learn["feat"].dropna().to_numpy()
    assert cell["features"] == ["feat"]
    assert cell["feature_mean"] == pytest.approx([finite.mean()], rel=1e-12)
    assert cell["feature_sd"] == pytest.approx([finite.std()], rel=1e-12)
    assert cell["rows"] == len(finite) and cell["rows_missing_features"] == len(learn) - len(finite)
    assert np.array(cell["coefficients"]).shape == (2, 1) and len(cell["bias"]) == 2


@pytest.mark.parametrize("kind", ["linear_pool", "quantile_average"])
def test_a_row_with_a_missing_feature_gets_the_intercept_only_weights(tmp_path, kind):
    df, _ = feature_frame(np.random.default_rng(613), "linear" if kind == "linear_pool" else "quantile",
                          n_fit=1500, n_val=300, nan_share=0.15)
    params = params_for(tmp_path, df, featured_method(kind))
    run(params)
    forecasts, fit, summary = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    full = joined(forecasts, df)
    rows = full[(full["fold"] == 2) & full["feat"].isna()]
    assert len(rows) > 20 and rows["fitted"].all()
    w = softmax_rows(np.array(cell["bias"])[None, :])[0]
    comps = [cdfs(rows, "mdl"), cdfs(rows, "mkt")]
    expect = w[0] * comps[0] + w[1] * comps[1] if kind == "linear_pool" else averaged_quantile_cdf(comps, w, 99)
    assert np.allclose(cdf_columns(rows), expect, atol=1e-9)
    assert cell["weights"] == pytest.approx(list(w), abs=1e-12)  # the state names the intercept-only weights


def test_feature_weights_apply_the_row_weights_of_each_row(tmp_path):
    df, _ = feature_frame(np.random.default_rng(614), "linear", n_fit=1500, n_val=300)
    params = params_for(tmp_path, df, featured_method("linear_pool"))
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    full = joined(forecasts, df)
    rows = full[full["fold"] == 2]
    w = featured_weights(cell, [rows["feat"].to_numpy()])
    expect = w[:, :1] * cdfs(rows, "mdl") + w[:, 1:] * cdfs(rows, "mkt")
    assert np.allclose(cdf_columns(rows), expect, atol=1e-12)


def test_quantile_feature_weights_apply_per_row_and_a_log_feature_transform_is_read(tmp_path):
    df, _ = feature_frame(np.random.default_rng(615), "quantile", n_fit=1200, n_val=200)
    dte = df["actual_calendar_dte"].to_numpy().astype(float)
    w_true = sigmoid(2.0 * (np.log(dte) - 2.0))
    u = ndtri(np.random.default_rng(616).random(len(df)))
    df["y"] = w_true * (-1 + 0.7 * u) + (1 - w_true) * (1.5 + 0.8 * u)
    feature = {"column": "actual_calendar_dte", "transform": "log"}
    params = params_for(tmp_path, df, featured_method("quantile_average", weight_features=[feature]))
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    assert cell["features"] == ["log(actual_calendar_dte)"]
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]
    assert cell["feature_mean"] == pytest.approx([np.log(learn["actual_calendar_dte"].to_numpy()).mean()], rel=1e-12)
    full = joined(forecasts, df)
    rows = full[full["fold"] == 2]
    w = featured_weights(cell, [np.log(rows["actual_calendar_dte"].to_numpy().astype(float))])
    assert np.corrcoef(w[:, 0], w_true[rows["row_id"].to_numpy()])[0, 1] > 0.9
    expect = np.array([averaged_quantile_cdf([cdfs(rows.iloc[[i]], "mdl"), cdfs(rows.iloc[[i]], "mkt")], w[i], 99)[0]
                       for i in range(0, len(rows), 40)])
    assert np.allclose(cdf_columns(rows)[::40], expect, atol=1e-9)


def test_a_feature_with_no_signal_leaves_the_weights_close_to_constant(tmp_path):
    df, _ = feature_frame(np.random.default_rng(617), "linear", slope=0.0)
    params = params_for(tmp_path, df, featured_method("linear_pool", weight_features=["other"]))
    run(params)
    cell = read_out(params)[1]["folds"]["2"]["state"]["global"]
    assert np.abs(np.array(cell["coefficients"])).max() < 0.25
    assert cell["weights"][0] == pytest.approx(0.5, abs=0.1)


def test_feature_transform_and_missing_values_unit(tmp_path):
    data = {"a": np.array([1.0, 2.0, 0.0, np.nan, 4.0, -1.0]), "b": np.array([1.0, 3.0, 5.0, 7.0, 9.0, np.inf])}
    panel = types.SimpleNamespace(numeric=lambda column: data[column])
    features = cgm._FeatureSet([{"column": "a", "transform": "log"}, "b"])
    rows = np.arange(6)
    assert features.names() == ["log(a)", "b"] and features.columns() == ["a", "b"]
    raw = features.raw(panel, rows)
    assert np.isfinite(raw).all(axis=1).tolist() == [True, True, False, False, True, False]  # log 0, nan, log -1, inf
    state = features.standardizer(panel, rows)
    kept = raw[[0, 1, 4]]
    assert state["feature_mean"] == pytest.approx(kept.mean(axis=0).tolist())
    assert state["feature_sd"] == pytest.approx(kept.std(axis=0).tolist())
    design = features.design(panel, rows, state)
    assert (design[[2, 3, 5]] == 0).all()  # a missing value takes the standardized mean
    assert design[[0, 1, 4]] == pytest.approx((kept - kept.mean(axis=0)) / kept.std(axis=0))


def test_a_constant_feature_has_unit_scale():
    panel = types.SimpleNamespace(numeric=lambda column: np.full(5, 3.0))
    state = cgm._FeatureSet(["c"]).standardizer(panel, np.arange(5))
    assert state == {"features": ["c"], "feature_mean": [3.0], "feature_sd": [1.0]}


@pytest.mark.parametrize("theta_scale", [0.0, 0.3])
def test_feature_linear_objective_gradient_matches_finite_differences(theta_scale):
    rng = np.random.default_rng(618)
    grid = three_point_grid()
    stack = np.stack([ndtr(np.array(Z)[None, :] - rng.normal(size=(40, 1)) - s) for s in (-0.5, 0.2, 0.9)], axis=1)
    objective = cgm._FeatureLinearObjective(grid, stack, rng.normal(size=40), rng.random(40) + 0.1,
                                            rng.normal(size=(40, 2)))
    theta = np.r_[[0.3, -0.2, 0.1], theta_scale * rng.normal(size=6)]
    check_gradient(objective, theta)


@pytest.mark.parametrize("theta_scale", [0.0, 0.3])
def test_feature_quantile_objective_gradient_matches_finite_differences(theta_scale):
    rng = np.random.default_rng(619)
    grid = three_point_grid()
    centre = rng.normal(size=(40, 1))
    stack = np.stack([ndtr((np.array(Z)[None, :] - centre - s) / sd) for s, sd in ((-0.5, 1.0), (0.2, 1.3), (0.9, 0.8))], axis=1)
    objective = cgm._FeatureQuantileObjective(grid, stack, rng.normal(size=40), rng.random(40) + 0.1, 49,
                                              rng.normal(size=(40, 2)))
    theta = np.r_[[0.3, -0.2, 0.1], theta_scale * rng.normal(size=6)]
    check_gradient(objective, theta)


def test_feature_objectives_start_at_the_constant_weight_pool():
    rng = np.random.default_rng(620)
    grid = three_point_grid()
    stack = np.stack([ndtr(np.array(Z)[None, :] - rng.normal(size=(30, 1)) - s) for s in (-0.5, 0.9)], axis=1)
    y, omega, design = rng.normal(size=30), rng.random(30) + 0.1, rng.normal(size=(30, 3))
    w = np.array([0.35, 0.65])
    linear = cgm._FeatureLinearObjective(grid, stack, y, omega, design)
    constant = (omega * tw(w[0] * stack[:, 0] + w[1] * stack[:, 1], y, grid.weights)).sum() / omega.sum()
    value, _ = linear.value_and_grad(linear.start(w))
    assert value == pytest.approx(constant, rel=1e-3)  # the start logits lift only a zero weight, so this is the pool
    quantile = cgm._FeatureQuantileObjective(grid, stack, y, omega, 49, design)
    plain = cgm._QuantileAverageObjective(grid, stack, y, omega, 49)
    assert quantile.value_and_grad(quantile.start(w))[0] == pytest.approx(plain.loss(w), rel=1e-9)


FEATURE_REFUSALS = {
    "features not a list": (lambda m: m.update(weight_features="feat"), "weight_features"),
    "features empty": (lambda m: m.update(weight_features=[]), "weight_features"),
    "features duplicate": (lambda m: m.update(weight_features=["a", "a"]), "weight_features"),
    "features number": (lambda m: m.update(weight_features=[5]), "weight_features"),
    "features dict no column": (lambda m: m.update(weight_features=[{"transform": "log"}]), "weight_features"),
    "features unknown transform": (lambda m: m.update(weight_features=[{"column": "a", "transform": "sqrt"}]),
                                   "weight_features"),
    "features dict extra key": (lambda m: m.update(weight_features=[{"column": "a", "scale": 2}]), "weight_features"),
    "features log and plain duplicate label": (
        lambda m: m.update(weight_features=["a", {"column": "a", "transform": "log"}, {"column": "a", "transform": "log"}]),
        "weight_features"),
    "features with by": (lambda m: m.update(weight_features=["a"], by="symbol"), "weight_features"),
    "features with quantile_by": (lambda m: m.update(weight_features=["a"], quantile_by={"column": "v", "quantiles": [0.5]}),
                                  "weight_features"),
    "features with per_threshold": (lambda m: m.update(weight_features=["a"], per_threshold=True), "weight_features"),
    "features with shrink": (lambda m: m.update(weight_features=["a"], by="symbol", shrink_rows=5), "weight_features"),
    "features zero cap": (lambda m: m.update(weight_features=["a"], max_learning_rows=0), "max_learning_rows"),
    "cap without features": (lambda m: m.update(max_learning_rows=100), "max_learning_rows"),
    "beta features": (lambda m: m.update(kind="beta_pool", weight_features=["a"]), "weight_features"),
    "average features with by": (lambda m: m.update(kind="quantile_average", weight_features=["a"], by="symbol"),
                                 "weight_features"),
    "average features with bands": (lambda m: m.update(kind="quantile_average", weight_features=["a"], level_bands=[0.5]),
                                    "level_bands"),
}


@pytest.mark.parametrize("name", sorted(FEATURE_REFUSALS))
def test_malformed_weight_features_are_refused(tmp_path, name):
    _, params = valid_params(tmp_path)
    change, needle = FEATURE_REFUSALS[name]
    change(params["method"])
    problems = GridCDFMethod.validate_params(params)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


@pytest.mark.parametrize("kind", ["linear_pool", "quantile_average"])
def test_weight_features_with_every_compatible_key_pass(tmp_path, kind):
    _, params = valid_params(tmp_path)
    params["method"].update(kind=kind, weight_features=["a", {"column": "dte", "transform": "log1p"}, "b"],
                            max_learning_rows=1000, half_life_days=30, learn="rolling", refit_days=21)
    if kind == "quantile_average":
        params["method"]["levels"] = 99
    assert GridCDFMethod.validate_params(params) == []


def test_feature_pool_with_half_life_and_rolling_runs_and_declines_below_min_rows(tmp_path):
    df = roll_frame(np.random.default_rng(621), drift=True, n_val=500)
    df["feat"] = np.random.default_rng(622).normal(size=len(df))
    params = params_for(tmp_path, df, rolling_method(weight_features=["feat"], half_life_days=60,
                                                      max_learning_rows=400))
    run(params)
    forecasts, fit, _ = read_out(params)
    assert all(b["fitted"] for b in fit["folds"]["3"]["blocks"])
    assert_cdf(cdf_columns(forecasts))
    declined = params_for(tmp_path, df, rolling_method(weight_features=["feat"], min_rows=10_000),
                          output=str(tmp_path / "declined"))
    run(declined)
    assert all(not r["fitted"] for r in read_out(declined)[1]["folds"].values())


@pytest.mark.parametrize("kind", ["linear_pool", "quantile_average"])
def test_feature_pool_fits_at_min_rows_counting_only_rows_with_features(tmp_path, kind):
    df, _ = feature_frame(np.random.default_rng(623), "linear", n_fit=200, n_val=30, folds=(1,))
    fit_rows = df.index[df["band"] == "fit"]
    df.loc[fit_rows[:30], "feat"] = np.nan  # 170 rows with a feature
    base = featured_method(kind)

    def fitted(min_rows, tag):
        params = params_for(tmp_path, df, {**base, "min_rows": min_rows}, output=str(tmp_path / tag))
        run(params)
        return read_out(params)[1]["folds"]["1"]

    assert fitted(170, "at")["fitted"] is True and fitted(170, "at2")["state"]["global"]["rows"] == 170
    assert fitted(171, "below")["fitted"] is False


# ------------------------------------------------------------------------------------ location-scale


def ls_method(**over):
    method = {"kind": "location_scale", "learn": "fit_band", "base": "market", "features": ["feat"], "min_rows": 50}
    method.update(over)
    return method


def ls_frame(rng, mean, sd, base_sd=1.0, n_fit=4000, n_val=1500, folds=(1, 2)):
    """The base market is N(0, base_sd); the truth is N(mean(feat), sd(feat)) with the feature N(0, 1)."""
    df = make_frame(rng, folds=folds, n_fit=n_fit, n_val=n_val)
    df["feat"] = rng.normal(size=len(df))
    df["other"] = rng.normal(size=len(df))
    df["y"] = rng.normal(np.broadcast_to(mean(df), len(df)), np.broadcast_to(sd(df), len(df)))
    add_cdf(df, "mkt", 0.0, base_sd)
    add_cdf(df, "mdl", 0.0, 1.0)
    return df


def raw_coefficients(cell, name):
    """Intercept and slopes in raw feature units of ``location`` or ``log_scale``."""
    mean, sd = np.array(cell["feature_mean"]), np.array(cell["feature_sd"])
    slope = np.array(cell[f"{name}_coefficients"]) / sd
    return cell[f"{name}_intercept"] - float(slope @ mean), slope


def val_rows(forecasts, df, fold=2):
    full = joined(forecasts, df)
    return full[(full["fold"] == fold) & (full["band"] == "val")]


def test_the_extended_line_cdf_follows_the_pit_tail_convention():
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    F = np.tile(np.linspace(0.1, 0.9, 13), (3, 1))
    u = np.array([[Z[0] - 2.0, Z[0] - 9.0, Z[3], Z[12] + 2.0, Z[12] + 9.0, -2.75],
                  [Z[5]] * 6, [Z[0] - 4.0, Z[12] + 4.0, 0.0, 0.1, -0.1, 1.2]])
    got = grid.extended(F, u)
    x = np.r_[Z[0] - 4.0, Z, Z[-1] + 4.0]
    assert np.allclose(got, [np.interp(row, x, np.r_[0.0, f, 1.0]) for row, f in zip(u, F)], atol=1e-15)
    assert got[0, 0] == pytest.approx(0.05) and got[0, 1] == 0.0 and got[0, 4] == 1.0
    y = np.array([-2.75, -7.0, 9.0])
    assert np.array_equal(grid.extended(F, y[:, None])[:, 0], grid.pit(F, y))


def ls_spec_method(**over):
    grid = cgm._Grid(Z, LABELS, [1.0] * 13, 4.0)
    return cgm.METHOD_KINDS["location_scale"]({"kind": "location_scale", "learn": "fit_band", "base": "market",
                                               "features": [], "min_rows": 10, **over}, {}, grid)


def zero_cell(features=(), **over):
    names = [f if isinstance(f, str) else "x" for f in features]
    cell = {"features": names, "feature_mean": [0.0] * len(names), "feature_sd": [1.0] * len(names),
            "location_intercept": 0.0, "location_coefficients": [0.0] * len(names),
            "log_scale_intercept": 0.0, "log_scale_coefficients": [0.0] * len(names)}
    cell.update(over)
    return cell


def stub_panel(base, **columns):
    return types.SimpleNamespace(cdf={"market": base}, numeric=lambda c: columns[c])


def test_zero_location_and_log_scale_reproduce_the_base_exactly():
    rng = np.random.default_rng(630)
    base = ndtr((np.array(Z)[None, :] - rng.normal(size=(25, 1))) / rng.uniform(0.5, 2, size=(25, 1)))
    method = ls_spec_method(features=["x"])
    panel = stub_panel(base, x=rng.normal(size=25))
    assert np.array_equal(method._apply(panel, np.arange(25), zero_cell(["x"])), base)


def test_location_and_scale_transform_the_base_cdf_on_the_extended_line():
    rng = np.random.default_rng(631)
    base = ndtr((np.array(Z)[None, :] - rng.normal(size=(10, 1))) / 1.3)
    panel = stub_panel(base)
    x = np.r_[Z[0] - 4.0, Z, Z[-1] + 4.0]
    for mu, log_s in ((0.5, 0.0), (-0.7, 0.4), (0.2, -0.6)):
        got = ls_spec_method()._apply(panel, np.arange(10), zero_cell(location_intercept=mu, log_scale_intercept=log_s))
        expect = np.array([np.interp((np.array(Z) - mu) / np.exp(log_s), x, np.r_[0.0, f, 1.0]) for f in base])
        assert np.allclose(got, expect, atol=1e-14), (mu, log_s)
        assert_cdf(got)


def test_a_missing_feature_takes_the_intercepts_alone():
    rng = np.random.default_rng(632)
    base = ndtr((np.array(Z)[None, :] - rng.normal(size=(6, 1))))
    feat = np.array([1.0, np.nan, -1.0, 0.5, np.nan, 2.0])
    cell = zero_cell(["x"], location_intercept=0.3, location_coefficients=[0.4], log_scale_intercept=0.2,
                     log_scale_coefficients=[0.1])
    got = ls_spec_method(features=["x"])._apply(stub_panel(base, x=feat), np.arange(6), cell)
    x = np.r_[Z[0] - 4.0, Z, Z[-1] + 4.0]
    mu = np.where(np.isnan(feat), 0.3, 0.3 + 0.4 * np.nan_to_num(feat))
    s = np.exp(np.where(np.isnan(feat), 0.2, 0.2 + 0.1 * np.nan_to_num(feat)))
    expect = np.array([np.interp((np.array(Z) - m) / sc, x, np.r_[0.0, f, 1.0]) for f, m, sc in zip(base, mu, s)])
    assert np.allclose(got, expect, atol=1e-14)


def test_location_scale_recovers_a_feature_dependent_shift(tmp_path):
    df = ls_frame(np.random.default_rng(633), lambda d: 0.6 * d["feat"], lambda d: 1.0)
    params = params_for(tmp_path, df, ls_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    intercept, slope = raw_coefficients(cell, "location")
    assert slope[0] == pytest.approx(0.6, abs=0.05) and intercept == pytest.approx(0.0, abs=0.05)
    _, log_slope = raw_coefficients(cell, "log_scale")
    assert abs(log_slope[0]) < 0.07
    val = val_rows(forecasts, df)
    assert tw(cdf_columns(val), val["y"]).mean() < 0.9 * tw(cdfs(val, "mkt"), val["y"]).mean()
    assert cell["objective"] < cell["start_objective"] and cell["features"] == ["feat"]
    assert_cdf(cdf_columns(forecasts))


def test_location_scale_widens_a_base_too_narrow_by_a_feature_dependent_factor(tmp_path):
    df = ls_frame(np.random.default_rng(634), lambda d: 0.0, lambda d: np.exp(0.3 * d["feat"]), base_sd=0.6)
    params = params_for(tmp_path, df, ls_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    intercept, slope = raw_coefficients(cell, "log_scale")
    assert intercept == pytest.approx(np.log(1 / 0.6), abs=0.1) and slope[0] == pytest.approx(0.3, abs=0.07)
    assert abs(raw_coefficients(cell, "location")[1][0]) < 0.05
    val = val_rows(forecasts, df)
    truth = ndtr(np.array(Z)[None, :] / np.exp(0.3 * val["feat"].to_numpy())[:, None])  # the data-generating CDF
    got = tw(cdf_columns(val), val["y"]).mean()
    assert got < 0.95 * tw(cdfs(val, "mkt"), val["y"]).mean() and got < 1.02 * tw(truth, val["y"]).mean()
    assert_cdf(cdf_columns(forecasts))


def test_location_scale_without_features_is_a_global_shift_and_scale(tmp_path):
    df = ls_frame(np.random.default_rng(635), lambda d: 0.5, lambda d: 1.4)
    params = params_for(tmp_path, df, ls_method(features=[]))
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    assert cell["features"] == [] and cell["location_coefficients"] == [] and cell["log_scale_coefficients"] == []
    assert cell["location_intercept"] == pytest.approx(0.5, abs=0.07)
    assert np.exp(cell["log_scale_intercept"]) == pytest.approx(1.4, abs=0.1)
    assert_cdf(cdf_columns(forecasts))


def test_location_scale_keeps_the_base_when_it_is_already_right(tmp_path):
    df = ls_frame(np.random.default_rng(636), lambda d: 0.0, lambda d: 1.0)
    params = params_for(tmp_path, df, ls_method(features=["other"]))
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    assert cell["objective"] <= cell["start_objective"]
    assert abs(cell["location_intercept"]) < 0.08 and abs(cell["log_scale_intercept"]) < 0.08
    val = val_rows(forecasts, df)
    assert tw(cdf_columns(val), val["y"]).mean() <= 1.01 * tw(cdfs(val, "mkt"), val["y"]).mean()


def test_location_scale_objective_at_zero_is_the_weighted_score_of_the_base_and_its_gradient_is_exact():
    rng = np.random.default_rng(637)
    grid = three_point_grid()
    base = ndtr((np.array(Z)[None, :] - rng.normal(size=(50, 1))) / 1.2)
    y, omega = rng.normal(size=50), rng.random(50) + 0.1
    design = np.column_stack([rng.normal(size=(50, 2)), np.ones(50)])
    objective = cgm._LocationScaleObjective(grid, base[:, None, :], y, omega, design)
    zero = np.zeros(6)
    assert objective.value_and_grad(zero)[0] == pytest.approx((omega * grid.score(base, y)).sum() / omega.sum(), rel=1e-12)
    check_gradient(objective, np.array([0.1, -0.2, 0.05, 0.15, 0.1, -0.1]))


def test_location_scale_buckets_fit_their_own_parameters_and_small_ones_use_the_global(tmp_path):
    df = ls_frame(np.random.default_rng(638), lambda d: 0.0, lambda d: 1.0, n_fit=2000, n_val=100)
    shift = np.where(df["symbol"] == "A", 0.6, -0.6)
    df["y"] = np.random.default_rng(639).normal(shift, 1.0)
    rare = df.index[(df["fold"] == 2) & (df["band"] == "fit")][:5]
    df.loc[rare, "symbol"] = "C"
    params = params_for(tmp_path, df, ls_method(features=[], by="symbol"))
    run(params)
    forecasts, fit, _ = read_out(params)
    state = fit["folds"]["2"]["state"]
    assert sorted(state["buckets"]) == ["A", "B"]
    assert state["buckets"]["A"]["location_intercept"] == pytest.approx(0.6, abs=0.1)
    assert state["buckets"]["B"]["location_intercept"] == pytest.approx(-0.6, abs=0.1)
    full = joined(forecasts, df)
    x = np.r_[Z[0] - 4.0, Z, Z[-1] + 4.0]
    for symbol, cell in (("A", state["buckets"]["A"]), ("C", state["global"])):
        rows = full[(full["fold"] == 2) & (full["symbol"] == symbol)]
        expect = np.array([np.interp((np.array(Z) - cell["location_intercept"]) / np.exp(cell["log_scale_intercept"]),
                                     x, np.r_[0.0, f, 1.0]) for f in cdfs(rows, "mkt")])
        assert np.allclose(cdf_columns(rows), expect, atol=1e-12), symbol


def test_location_scale_uses_recency_weights_the_stride_and_min_rows(tmp_path):
    df = roll_frame(np.random.default_rng(640), drift=True, n_val=500)
    df["feat"] = np.random.default_rng(641).normal(size=len(df))
    params = params_for(tmp_path, df, ls_method(learn="rolling", refit_days=28, half_life_days=60,
                                                max_learning_rows=500, min_rows=30))
    run(params)
    forecasts, fit, _ = read_out(params)
    blocks = fit["folds"]["3"]["blocks"]
    assert all(b["fitted"] for b in blocks)
    cell = blocks[-1]["state"]["global"]
    stride = -(-cell["rows"] // 500)
    assert cell["rows_used"] == len(range(0, cell["rows"], stride))
    assert_cdf(cdf_columns(forecasts))
    declined = params_for(tmp_path, df, ls_method(min_rows=100_000), output=str(tmp_path / "declined"))
    run(declined)
    assert all(not r["fitted"] for r in read_out(declined)[1]["folds"].values())


def test_location_scale_drops_rows_missing_a_feature_from_learning_and_counts_them(tmp_path):
    df = ls_frame(np.random.default_rng(642), lambda d: 0.6 * d["feat"], lambda d: 1.0, n_fit=1500, n_val=200)
    df.loc[np.random.default_rng(643).random(len(df)) < 0.2, "feat"] = np.nan
    params = params_for(tmp_path, df, ls_method())
    run(params)
    forecasts, fit, _ = read_out(params)
    cell = fit["folds"]["2"]["state"]["global"]
    learn = df[(df["fold"] == 2) & (df["band"] == "fit")]
    assert cell["rows"] == learn["feat"].notna().sum()
    assert cell["rows_missing_features"] == learn["feat"].isna().sum()
    full = joined(forecasts, df)
    nan_rows = full[(full["fold"] == 2) & full["feat"].isna()]
    assert len(nan_rows) > 20 and nan_rows["fitted"].all()
    x = np.r_[Z[0] - 4.0, Z, Z[-1] + 4.0]
    expect = np.array([np.interp((np.array(Z) - cell["location_intercept"]) / np.exp(cell["log_scale_intercept"]),
                                 x, np.r_[0.0, f, 1.0]) for f in cdfs(nan_rows, "mkt")])
    assert np.allclose(cdf_columns(nan_rows), expect, atol=1e-12)


LS_REFUSALS = {
    "unknown base": (lambda m: m.update(base="ghost"), "ghost"),
    "no base": (lambda m: m.pop("base"), "base"),
    "no features": (lambda m: m.pop("features"), "features"),
    "features not a list": (lambda m: m.update(features="feat"), "features"),
    "features duplicate": (lambda m: m.update(features=["a", "a"]), "features"),
    "features number": (lambda m: m.update(features=[3]), "features"),
    "features bad transform": (lambda m: m.update(features=[{"column": "a", "transform": "exp"}]), "features"),
    "no min_rows": (lambda m: m.pop("min_rows"), "min_rows"),
    "zero min_rows": (lambda m: m.update(min_rows=0), "min_rows"),
    "zero cap": (lambda m: m.update(max_learning_rows=0), "max_learning_rows"),
    "float cap": (lambda m: m.update(max_learning_rows=2.5), "max_learning_rows"),
    "list by": (lambda m: m.update(by=["symbol"]), "by"),
    "components": (lambda m: m.update(components=["model", "market"]), "components"),
    "per_threshold": (lambda m: m.update(per_threshold=True), "per_threshold"),
    "levels": (lambda m: m.update(levels=99), "levels"),
    "quantile_by": (lambda m: m.update(quantile_by={"column": "v", "quantiles": [0.5]}), "quantile_by"),
    "weight_features": (lambda m: m.update(weight_features=["a"]), "weight_features"),
    "level_bands": (lambda m: m.update(level_bands=[0.5]), "level_bands"),
}


@pytest.mark.parametrize("name", sorted(LS_REFUSALS))
def test_malformed_location_scale_is_refused(tmp_path, name):
    _, params = valid_params(tmp_path)
    params["method"] = ls_method()
    LS_REFUSALS[name][0](params["method"])
    problems = GridCDFMethod.validate_params(params)
    assert any(LS_REFUSALS[name][1] in p for p in problems), problems
    with pytest.raises(ConfigError):
        GridCDFMethod("g", params)


def test_location_scale_with_every_key_passes_and_no_features_is_allowed(tmp_path):
    _, params = valid_params(tmp_path)
    params["method"] = ls_method(by="symbol", max_learning_rows=1000, half_life_days=30, learn="rolling",
                                 refit_days=21, features=["a", {"column": "dte", "transform": "log"}])
    assert GridCDFMethod.validate_params(params) == []
    params["method"]["features"] = []
    assert GridCDFMethod.validate_params(params) == []
    assert "location_scale" in cgm.METHOD_KINDS


def test_a_location_scale_fit_leaves_a_monotone_cdf_even_for_a_noisy_base(tmp_path):
    rng = np.random.default_rng(644)
    df = ls_frame(rng, lambda d: 0.4 * d["feat"], lambda d: 1.3, n_fit=800, n_val=100)
    noise = rng.normal(0, 0.02, (len(df), 13))
    for i, label in enumerate(LABELS):
        df["mkt_cdf_" + label] = np.clip(df["mkt_cdf_" + label] + noise[:, i], 0, 1)
    params = params_for(tmp_path, df, ls_method())
    run(params)
    assert_cdf(cdf_columns(read_out(params)[0]))


def test_the_edge_method_documents_still_validate_with_the_round_three_kinds_present():
    test_the_edge_method_documents_of_the_child_still_validate()
