"""GridCDFMethod (ADR-0270): forecast methods on a CDF grid over a scored-rows table.

Every fixture is synthetic with a known answer: the truth is a normal or a known mixture, the forecasters are
normals with chosen location and scale, so a recalibration, a pool weight or a cell choice has a value the
test can state in advance. Expected numbers are recomputed here with plain numpy, never read back from the
node under test.
"""

import hashlib
import itertools
import json

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
ndtr = pytest.importorskip("scipy.special").ndtr

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


def make_frame(rng, folds=(1, 2, 3), n_fit=300, n_val=300, symbols=("A", "B"), dte_hi=30, fold_days=60):
    """One row per forecast: a fit block and a later val block for every fold."""
    parts = []
    for f in folds:
        for band, n, offset in (("fit", n_fit, 0), ("val", n_val, 20)):
            days = fold_days * f + offset + rng.integers(0, 10, n)
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
