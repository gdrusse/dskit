"""Horizon-wise distribution heads, one common scoring grid and two baselines.

Synthetic, CPU, seconds. Every expected number is restated independently: the
Gaussian CRPS by its closed form, the sample CRPS by ``distribution_scores.Crps``,
the tail and pinball scores by ``ChronologicalCDFStudy.scores`` on a numpy
``MixtureCurve``, the knot CDF by ``GridCurve`` and the empirical baseline by
``numpy.quantile`` and ``HorizonEmpiricalCDF``.
"""

import json
import math
from statistics import NormalDist

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from dskit.pipeline.distribution_scores import Crps, PitCalibration, SampleDistribution
from dskit.pipeline.libs import horizon_cdf
from dskit.pipeline.libs.horizon_cdf import (
    NODE_KINDS,
    CommonGrid,
    EmpiricalHorizonFit,
    HorizonDistribution,
    HorizonForecaster,
    MixtureHorizons,
    QuantileHorizons,
    StudentScaleFit,
)
from dskit.pipeline.libs.predictive_cdf import (
    ChronologicalCDFStudy,
    GridCurve,
    HorizonEmpiricalCDF,
    MixtureCurve,
    mixture_family,
)

_N = NormalDist()
GAUSS = {"kind": "gaussian"}
GRID = {"z_min": -10.0, "z_max": 10.0, "levels": [0.1, 0.5, 0.9],
        "tail_intervals": [[-10.0, -2.0], [2.0, 10.0]]}
GRID_BOUNDS = (GRID["z_min"], GRID["z_max"])


def _gauss(mu, sigma, dtype=torch.float64):
    mu = torch.as_tensor(mu, dtype=dtype)
    sigma = torch.as_tensor(sigma, dtype=dtype)
    return MixtureHorizons(torch.zeros_like(mu), mu, sigma, mixture_family(GAUSS))


def _closed_crps(y, mu, s):
    z = (y - mu) / s
    phi = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    return s * (z * (2 * _N.cdf(z) - 1) + 2 * phi - 1 / math.sqrt(math.pi))


class _Stub(HorizonForecaster):
    """Hands back slices of one prebuilt mixture and records the chunk sizes."""

    def __init__(self, logw, mu, sigma, family, **attrs):
        self.parts = (logw, mu, sigma)
        self.family = family
        self.components = mu.shape[-1]
        self.chunks = []
        for name, value in attrs.items():
            setattr(self, name, value)

    def forecast(self, panel, rows, chunk_rows):
        self.chunks.append(chunk_rows)
        rows = torch.as_tensor(rows)
        return MixtureHorizons(*(p[rows] for p in self.parts), self.family)


def _mixture_stub(n=64, h=3, **attrs):
    g = torch.Generator().manual_seed(3)
    mu = torch.randn(n, h, 2, generator=g, dtype=torch.float64) * .3
    sigma = 0.6 + torch.rand(n, h, 2, generator=g, dtype=torch.float64)
    logw = torch.log(torch.tensor([.7, .3], dtype=torch.float64)).expand(n, h, 2).clone()
    y = torch.randn(n, h, generator=g, dtype=torch.float64) * 1.1
    return _Stub(logw, mu, sigma, mixture_family(GAUSS), **attrs), y


# --------------------------------------------------------------------- shape of the module


def test_pack_registers_nothing_and_the_abstract_seams_refuse_construction():
    assert NODE_KINDS == ()
    assert set(horizon_cdf.__all__) >= {
        "CommonGrid", "EmpiricalHorizonFit", "HorizonDistribution", "HorizonForecaster",
        "HorizonScores", "MixtureHorizons", "QuantileHorizons", "StudentScaleFit"}
    for abstract in (HorizonDistribution, HorizonForecaster):
        with pytest.raises(TypeError):
            abstract()

    class NoQuantile(HorizonDistribution):
        def cdf(self, z):
            return z

    with pytest.raises(TypeError):
        NoQuantile()


# --------------------------------------------------------------------- the common grid


def test_gaussian_crps_matches_the_closed_form():
    mu = [[0.0, 0.4], [1.0, -0.5], [-0.3, 0.2]]
    sigma = [[1.0, 0.5], [1.5, 0.8], [0.7, 1.2]]
    y = [[0.3, -0.1], [2.5, 0.0], [-1.0, 1.6]]
    dist = _gauss(torch.tensor(mu)[..., None], torch.tensor(sigma)[..., None])
    out = CommonGrid(GRID).score(dist, torch.tensor(y, dtype=torch.float64))
    for r in range(3):
        for h in range(2):
            assert out["crps"][r, h].item() == pytest.approx(
                _closed_crps(y[r][h], mu[r][h], sigma[r][h]), abs=1e-4)


def test_crps_agrees_with_the_sample_crps_of_midpoint_draws():
    draws = [_N.inv_cdf((k + .5) / 20000) * 1.2 + .3 for k in range(20000)]
    expected = Crps().score(SampleDistribution(draws), .7)
    dist = _gauss([[[.3]]], [[[1.2]]])
    got = CommonGrid(GRID).score(dist, torch.tensor([[.7]], dtype=torch.float64))["crps"]
    assert got.item() == pytest.approx(expected, abs=1e-3)


def test_tail_crps_and_pinball_agree_with_the_study_scores():
    weights = np.tile([.6, .4], (4, 1))
    means = np.array([[-.2, .5], [0., 1.], [.3, -.4], [-.6, .2]])
    scales = np.array([[.8, 1.6], [1., 2.], [.7, 1.3], [1.2, .9]])
    y = np.array([-3.1, .4, 2.6, -.2])
    levels = ((np.arange(20) + .5) / 200).tolist()
    oracle, _ = ChronologicalCDFStudy.scores(
        MixtureCurve(weights, means, scales), y, 2000, GRID["tail_intervals"], 4000)
    grid = CommonGrid({**GRID, "levels": levels, "z_points": 4001})
    dist = MixtureHorizons(torch.log(torch.tensor(weights))[:, None, :],
                           torch.tensor(means)[:, None, :], torch.tensor(scales)[:, None, :],
                           mixture_family(GAUSS))
    out = grid.score(dist, torch.tensor(y)[:, None])
    np.testing.assert_allclose(out["tail_crps"][:, 0].numpy(), oracle["tail_crps"], rtol=2e-3, atol=1e-5)
    np.testing.assert_allclose(out["pinball"][:, 0].numpy(),
                               oracle["lower_tail_quantile_score"] / 2, rtol=2e-3, atol=1e-5)
    np.testing.assert_allclose(out["pit"][:, 0].numpy(), oracle["pit"], atol=1e-6)


def test_calibrated_model_has_uniform_pit_and_an_overdispersed_outcome_is_u_shaped():
    g = torch.Generator().manual_seed(11)
    n = 4000
    mu = torch.randn(n, 1, 1, generator=g, dtype=torch.float64)
    dist = _gauss(mu, torch.ones(n, 1, 1, dtype=torch.float64))
    z = torch.randn(n, 1, generator=g, dtype=torch.float64)
    grid = CommonGrid({**GRID, "pit_bins": 5})
    good = grid.new_scores()
    good.add(dist, mu[:, :, 0] + z)
    bad = grid.new_scores()
    bad.add(dist, mu[:, :, 0] + 2 * z)
    calm, wide = good.summary(), bad.summary()
    assert calm["pit_pvalue"][0] > 0.01
    assert max(calm["histogram"][0]) < 1.2 * n / 5
    hist = wide["histogram"][0]
    assert hist[0] > 1.5 * hist[2] and hist[4] > 1.5 * hist[2]
    assert wide["pit_pvalue"][0] < 1e-6
    pits = [_N.cdf(v) for v in (z[:, 0]).tolist()]
    assert calm["pit_ks"][0] == pytest.approx(PitCalibration(5).evaluate(pits)["statistic"], abs=1e-6)
    assert calm["cdf_basis"] == "parametric"


def test_horizons_never_mix():
    mu = torch.tensor([[[.1], [.5], [-.4]], [[0.], [.2], [.3]]], dtype=torch.float64)
    sigma = torch.tensor([[[1.], [.6], [2.]], [[1.3], [.9], [.5]]], dtype=torch.float64)
    y = torch.tensor([[.2, 1.1, -.5], [-.7, .3, 2.0]], dtype=torch.float64)
    grid = CommonGrid(GRID)
    base = grid.score(_gauss(mu, sigma), y)
    perm = [2, 0, 1]
    moved = grid.score(_gauss(mu[:, perm], sigma[:, perm]), y[:, perm])
    for name in ("crps", "tail_crps", "pinball", "pit"):
        torch.testing.assert_close(moved[name], base[name][:, perm])
    one = grid.score(_gauss(mu[:, 1:2], sigma[:, 1:2]), y[:, 1:2])
    torch.testing.assert_close(one["crps"], base["crps"][:, 1:2])


def test_scores_are_identical_across_chunk_sizes_and_chunk_rows_is_derived():
    forecaster, y = _mixture_stub()
    small = CommonGrid({**GRID, "max_chunk_mb": 0.05})
    large = CommonGrid({**GRID, "max_chunk_mb": 64})
    rows = torch.arange(64)
    a = small.evaluate(forecaster, None, rows, y)
    first_chunks = list(forecaster.chunks)
    b = large.evaluate(forecaster, None, rows, y)
    assert len(first_chunks) > 1 and len(forecaster.chunks) - len(first_chunks) == 1
    for key in ("crps", "tail_crps", "pinball", "pit_ks", "pit_pvalue", "histogram"):
        np.testing.assert_allclose(a[key], b[key], rtol=1e-9, atol=1e-12)
    assert large.chunk_rows(3, 2) > small.chunk_rows(3, 2) >= 1
    assert large.chunk_rows(3, 8) < large.chunk_rows(3, 2)
    assert large.chunk_rows(30, 2) < large.chunk_rows(3, 2)


def test_extreme_outcomes_stay_finite():
    grid = CommonGrid(GRID)
    dist = _gauss([[[0.]], [[0.]]], [[[1.]], [[1.]]])
    y = torch.tensor([[100.], [-100.]], dtype=torch.float64)
    out = grid.score(dist, y)
    assert all(torch.isfinite(v).all() for v in out.values())
    # Outside the grid the outcome adds its distance to the edge, so CRPS grows ~linearly.
    assert out["crps"][0, 0].item() == pytest.approx(100 - 0.5642, abs=0.05)
    knots = QuantileHorizons([.1, .5, .9], torch.tensor([[[-1., 0., 1.]]], dtype=torch.float64), bounds=GRID_BOUNDS)
    out = grid.score(knots, y[:1])
    assert all(torch.isfinite(v).all() for v in out.values())


def test_float32_crps_matches_float64():
    mu = torch.tensor([[[0.], [.4]], [[1.], [-.5]]])
    sigma = torch.tensor([[[1.], [.5]], [[1.5], [.8]]])
    y = torch.tensor([[.3, -.1], [2.5, 0.]])
    grid = CommonGrid(GRID)
    for family in (GAUSS, {"kind": "student", "degrees": 4}):
        fam = mixture_family(family)
        zeros = torch.zeros_like(mu)
        lo = grid.score(MixtureHorizons(zeros, mu, sigma, fam), y)
        hi = grid.score(MixtureHorizons(zeros.double(), mu.double(), sigma.double(), fam), y.double())
        for name in ("crps", "tail_crps", "pinball"):
            np.testing.assert_allclose(lo[name].numpy(), hi[name].numpy(), rtol=2e-4, atol=1e-6)


@pytest.mark.parametrize("bad", [
    {"z_pointz": 5},
    {"z_min": 11.0},
    {"levels": [0.5, 0.2]},
    {"levels": [0.0, 0.5]},
    {"tail_intervals": [[-12.0, -2.0]]},
    {"tail_intervals": [[-2.0, 2.0], [1.0, 3.0]]},
    {"tail_intervals": []},
    {"z_points": 2},
    {"max_chunk_mb": 0},
])
def test_common_grid_is_default_deny_and_refuses_inconsistent_declarations(bad):
    with pytest.raises(ValueError):
        CommonGrid({**GRID, **bad})


def test_common_grid_requires_its_declarations():
    for key in ("z_min", "z_max", "levels", "tail_intervals"):
        with pytest.raises(ValueError, match=key):
            CommonGrid({k: v for k, v in GRID.items() if k != key})


def test_the_training_tail_and_the_grid_tail_are_pinned_to_one_value():
    forecaster, y = _mixture_stub()
    grid = CommonGrid(GRID)
    forecaster.training_tail = [[-10.0, -2.0], [2.0, 10.0]]
    grid.evaluate(forecaster, None, torch.arange(8), y[:8])
    forecaster.training_tail = [[-10.0, -3.0], [3.0, 10.0]]
    with pytest.raises(ValueError, match="tail"):
        grid.evaluate(forecaster, None, torch.arange(8), y[:8])


def test_a_forecaster_lacking_a_grid_level_refuses_so_pinball_is_never_interpolated():
    grid = CommonGrid(GRID)
    forecaster, y = _mixture_stub(levels=(0.1, 0.5))
    with pytest.raises(ValueError, match="level"):
        grid.evaluate(forecaster, None, torch.arange(8), y[:8])
    short = QuantileHorizons([.1, .5], torch.tensor([[[-1., 0.]]], dtype=torch.float64), bounds=GRID_BOUNDS)
    with pytest.raises(ValueError, match="level"):
        grid.score(short, y[:2, :1])
    full = QuantileHorizons([.05, .1, .5, .9], torch.tensor([[[-2., -1., 0., 1.]]], dtype=torch.float64), bounds=GRID_BOUNDS)
    assert torch.isfinite(grid.score(full, y[:2, :1])["pinball"]).all()


def test_quantile_pinball_is_exact_at_the_knots():
    values = torch.tensor([[[-1.3, .1, 1.1]]], dtype=torch.float64)
    dist = QuantileHorizons([.1, .5, .9], values, GRID_BOUNDS)
    y = torch.tensor([[.4]], dtype=torch.float64)
    q = values[0, 0].tolist()
    expected = sum(max(t * (.4 - v), (t - 1) * (.4 - v)) for t, v in zip((.1, .5, .9), q)) / 3
    assert CommonGrid(GRID).score(dist, y)["pinball"].item() == pytest.approx(expected, abs=1e-12)


def test_a_distribution_clipped_to_other_bounds_than_the_grid_refuses():
    dist = QuantileHorizons([.1, .5, .9], torch.tensor([[[-1., 0., 1.]]], dtype=torch.float64),
                            bounds=(-5.0, 5.0))
    with pytest.raises(ValueError, match="bounds"):
        CommonGrid(GRID).score(dist, torch.zeros(1, 1, dtype=torch.float64))


def test_cdf_basis_is_extrapolated_for_knots_and_parametric_for_mixtures():
    grid = CommonGrid(GRID)
    knots = QuantileHorizons([.1, .5, .9], torch.tensor([[[-1., 0., 1.]]], dtype=torch.float64), bounds=GRID_BOUNDS)
    scores = grid.new_scores()
    scores.add(knots, torch.zeros(1, 1, dtype=torch.float64))
    assert scores.summary()["cdf_basis"] == "extrapolated"
    scores.add(_gauss([[[0.]]], [[[1.]]]), torch.zeros(1, 1, dtype=torch.float64))
    with pytest.raises(ValueError, match="basis"):
        scores.summary()


# --------------------------------------------------------------------- the distributions


def test_mixture_quantile_inverts_cdf_for_both_families():
    levels = [.01, .2, .5, .8, .99]
    for family in (GAUSS, {"kind": "student", "degrees": 3}):
        fam = mixture_family(family)
        logw = torch.log(torch.tensor([[[.3, .7]]], dtype=torch.float64))
        dist = MixtureHorizons(logw, torch.tensor([[[-1., 1.5]]], dtype=torch.float64),
                               torch.tensor([[[.6, 1.1]]], dtype=torch.float64), fam)
        q = dist.quantile(levels)
        assert q.shape == (1, 1, 5)
        np.testing.assert_allclose(dist.cdf(q)[0, 0].numpy(), levels, atol=1e-9)


def test_quantile_knots_cdf_equals_gridcurve_on_the_extended_knots():
    levels = [.1, .5, .9]
    values = torch.tensor([[[-1.3, .1, 1.1], [-.4, .0, .9]],
                           [[-2.0, .5, 1.0], [-.8, -.1, .4]]], dtype=torch.float64)
    dist = QuantileHorizons(levels, values)
    zs = np.linspace(-5, 5, 41)
    for r in range(2):
        for h in range(2):
            v = values[r, h].numpy()
            left = v[0] - .1 * (v[1] - v[0]) / (.5 - .1)
            right = v[2] + (1 - .9) * (v[2] - v[1]) / (.9 - .5)
            curve = GridCurve([[left, *v, right]], [0, *levels, 1])
            np.testing.assert_allclose(dist.cdf(torch.tensor(zs))[r, h].numpy(), curve.cdf(zs)[0], atol=1e-12)
            qs = np.array([0, .03, .1, .3, .5, .7, .9, .95, 1.])
            np.testing.assert_allclose(dist.quantile(qs.tolist())[r, h].numpy(), curve.quantile(qs)[0], atol=1e-12)
    inner = [.15, .4, .8]
    q = dist.quantile(inner)
    for j, level in enumerate(inner):
        np.testing.assert_allclose(dist.cdf(q[..., j:j + 1])[..., 0].numpy(), level, atol=1e-12)


def test_quantile_tail_extension_is_clipped_to_the_declared_bounds():
    dist = QuantileHorizons([.1, .5, .9], torch.tensor([[[-1., 0., 1.]]], dtype=torch.float64),
                            bounds=(-1.2, 1.2))
    curve = GridCurve([[-1.2, -1., 0., 1., 1.2]], [0, .1, .5, .9, 1])
    zs = np.linspace(-2, 2, 33)
    np.testing.assert_allclose(dist.cdf(torch.tensor(zs))[0, 0].numpy(), curve.cdf(zs)[0], atol=1e-12)
    assert dist.basis == "extrapolated"


def test_quantile_horizons_refuses_malformed_knots():
    ok = torch.tensor([[[-1., 0., 1.]]], dtype=torch.float64)
    for levels, values in (([.5], ok[..., :1]), ([.1, .5], ok), ([.5, .1, .9], ok),
                           ([.1, .5, .9], ok.flip(-1)), ([0., .5, .9], ok),
                           ([.1, .5, .9], ok.clone().fill_(float("nan")))):
        with pytest.raises(ValueError):
            QuantileHorizons(levels, values)


def test_row_constant_knots_broadcast_over_rows():
    dist = QuantileHorizons([.1, .5, .9], torch.tensor([[[-1., 0., 1.], [-2., 0., 2.]]], dtype=torch.float64), bounds=GRID_BOUNDS)
    y = torch.tensor([[.2, .3], [-.4, 5.], [1., 0.]], dtype=torch.float64)
    out = CommonGrid(GRID).score(dist, y)
    assert out["crps"].shape == (3, 2)
    assert dist.cdf(y.unsqueeze(-1)).shape == (3, 2, 1)


# --------------------------------------------------------------------- the empirical baseline


def _panel(n=6000, h=3, seed=5):
    g = torch.Generator().manual_seed(seed)
    scale = torch.tensor([.8, 1.0, 1.5], dtype=torch.float64)[:h]
    return torch.randn(n, h, generator=g, dtype=torch.float64) * scale + .05


def test_empirical_fit_equals_numpy_quantiles_per_horizon():
    y = _panel()
    levels = [.05, .25, .5, .75, .95]
    dist = EmpiricalHorizonFit(levels).fit(y).predict(bounds=GRID_BOUNDS)
    assert isinstance(dist, QuantileHorizons) and dist.n_rows == 1
    np.testing.assert_allclose(dist.quantile(levels)[0].numpy(),
                               np.quantile(y.numpy(), levels, axis=0).T, atol=1e-12)


def test_empirical_fit_equals_the_horizon_empirical_cdf_on_a_one_horizon_frame():
    y = _panel(h=1)
    levels = np.linspace(0, 1, 401)[1:-1]
    x = np.ones((y.shape[0], 2))
    study = HorizonEmpiricalCDF(0, 1, 401).fit(x, y[:, 0].numpy(), None, None)
    dist = EmpiricalHorizonFit(levels.tolist()).fit(y).predict(bounds=GRID_BOUNDS)
    np.testing.assert_allclose(dist.quantile(levels.tolist())[0, 0].numpy(),
                               study.curve(x[:1]).quantile(levels)[0], atol=1e-12)


def test_empirical_fit_matches_the_location_scale_shape_up_to_sample_resolution():
    from dskit.pipeline.distribution_models import EmpiricalLocationScale
    y = _panel(n=4000, h=1)
    rows = [{"label": v, "scale": 1.0} for v in y[:, 0].tolist()]
    node = EmpiricalLocationScale("m", {"fit_split": "train", "label": "label", "scale_field": "scale",
                                        "n_samples": 4000})
    shape = node.fit(rows, node.params)["shape"]
    levels = [.05, .5, .95]
    got = EmpiricalHorizonFit(levels).fit(y).predict(bounds=GRID_BOUNDS).quantile(levels)[0, 0].tolist()
    for p, v in zip(levels, got):
        assert v == pytest.approx(shape[int(p * 4000)], abs=0.03)


def test_empirical_fit_groups_gather_per_row_and_refuse_unknown_groups():
    y = torch.cat([_panel(n=500, seed=1), _panel(n=500, seed=2) * 3])
    groups = torch.cat([torch.zeros(500), torch.ones(500)]).long()
    fit = EmpiricalHorizonFit([.1, .5, .9]).fit(y, groups)
    dist = fit.predict(torch.tensor([1, 0, 1]), bounds=GRID_BOUNDS)
    assert dist.n_rows == 3
    q = dist.quantile([.1, .9])
    assert (q[0, :, 1] - q[0, :, 0]).gt(q[1, :, 1] - q[1, :, 0]).all()
    with pytest.raises(ValueError, match="group"):
        fit.predict(torch.tensor([2]), bounds=GRID_BOUNDS)
    with pytest.raises(ValueError, match="group"):
        fit.predict(bounds=GRID_BOUNDS)


def test_empirical_fit_state_round_trips_through_json():
    y = _panel(n=300)
    groups = (torch.arange(300) % 2).long()
    fit = EmpiricalHorizonFit([.1, .5, .9]).fit(y, groups)
    again = EmpiricalHorizonFit.from_state(json.loads(json.dumps(fit.state())))
    ids = torch.tensor([0, 1, 1])
    torch.testing.assert_close(again.predict(ids, bounds=GRID_BOUNDS).quantile([.1, .5, .9]),
                               fit.predict(ids, bounds=GRID_BOUNDS).quantile([.1, .5, .9]))


def test_empirical_fit_refuses_unusable_input():
    with pytest.raises(ValueError):
        EmpiricalHorizonFit([.5])
    fit = EmpiricalHorizonFit([.1, .9])
    with pytest.raises(ValueError):
        fit.fit(torch.tensor([[float("nan")]]))
    with pytest.raises(ValueError):
        fit.fit(torch.zeros(0, 2))


def test_the_grid_refuses_knots_whose_tails_were_not_clipped_to_its_range():
    narrow = {"z_min": -1.2, "z_max": 1.2, "levels": [.1, .5, .9],
              "tail_intervals": [[-1.2, -.5], [.5, 1.2]]}
    values = torch.tensor([[[-1., 0., 1.]]], dtype=torch.float64)
    y = torch.tensor([[.3]], dtype=torch.float64)
    grid = CommonGrid(narrow)
    with pytest.raises(ValueError, match="bounds"):
        grid.score(QuantileHorizons([.1, .5, .9], values), y)
    clipped = QuantileHorizons([.1, .5, .9], values, grid.bounds)
    assert clipped.cdf(torch.tensor([-1.2], dtype=torch.float64)).item() == 0.0
    assert torch.isfinite(grid.score(clipped, y)["crps"]).all()
    fit = EmpiricalHorizonFit([.1, .5, .9]).fit(torch.tensor([[-1.], [0.], [1.]], dtype=torch.float64))
    with pytest.raises(TypeError):
        fit.predict()
    with pytest.raises(ValueError, match="bounds"):
        grid.score(fit.predict(bounds=None), y)
    assert torch.isfinite(grid.score(fit.predict(bounds=grid.bounds), y)["crps"]).all()


# --------------------------------------------------------------------- the Student baseline


def _student_panel(n=30000, seed=7, degrees=5):
    g = torch.Generator().manual_seed(seed)
    vol = 0.5 + 1.5 * torch.rand(n, generator=g, dtype=torch.float64)
    multipliers = torch.tensor([.5, 1.0, 2.0], dtype=torch.float64)
    chi = 2 * torch._standard_gamma(torch.full((n, 3), degrees / 2, dtype=torch.float64), generator=g)
    z = torch.randn(n, 3, generator=g, dtype=torch.float64)
    return vol, multipliers, multipliers * (vol / vol.mean())[:, None] * z / torch.sqrt(chi / degrees)


def test_student_fit_recovers_the_planted_degrees_and_horizon_multipliers():
    vol, multipliers, y = _student_panel()
    fit = StudentScaleFit(degrees=(3, 5, 12)).fit(y, vol)
    state = fit.state()
    assert state["degrees"] == 5
    np.testing.assert_allclose(state["multipliers"], multipliers.tolist(), rtol=0.04)
    assert state["reference"] == pytest.approx(vol.mean().item())
    dist = fit.predict(vol[:4])
    assert isinstance(dist, MixtureHorizons) and dist.shape == (4, 3)
    assert dist.basis == "parametric" and dist.mu.shape == (4, 3, 1)
    expected = torch.tensor(state["multipliers"], dtype=torch.float64)[None] * (vol[:4] / state["reference"])[:, None]
    torch.testing.assert_close(dist.sigma[:, :, 0], expected)


def test_student_fit_state_round_trips_through_json_and_refuses_bad_input():
    vol, _, y = _student_panel(n=2000)
    fit = StudentScaleFit(degrees=(4, 6)).fit(y, vol)
    again = StudentScaleFit.from_state(json.loads(json.dumps(fit.state())))
    torch.testing.assert_close(fit.predict(vol[:5]).sigma, again.predict(vol[:5]).sigma)
    for bad in ((2,), (3.5,), (), (True,), (5, 5)):
        with pytest.raises(ValueError):
            StudentScaleFit(degrees=bad)
    with pytest.raises(ValueError):
        fit.fit(y, vol * -1)
    with pytest.raises(ValueError):
        fit.predict(torch.zeros(3, dtype=torch.float64))


def test_student_fit_output_is_scored_by_the_common_grid():
    vol, _, y = _student_panel(n=600)
    fit = StudentScaleFit(degrees=(5,)).fit(y, vol)
    grid = CommonGrid({**GRID, "z_min": -30.0, "z_max": 30.0,
                       "tail_intervals": [[-30.0, -2.0], [2.0, 30.0]]})
    scores = grid.new_scores()
    scores.add(fit.predict(vol), y)
    summary = scores.summary()
    assert len(summary["crps"]) == 3 and all(math.isfinite(v) for v in summary["crps"])
