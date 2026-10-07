"""Volatility estimators (ADR-0241): EWMA, high-low and rolling mean-square, with a gap rule.

Every expected number is computed by hand in plain Python from the same toy closes, so the
numpy path is checked against an independent restatement. The estimators return a PER-BAR
VARIANCE; the node writes one column per estimator through the numpy pack's ``ArrayFeatures``
seam, so its framing, causality guard and serving call are the pack's own.

Fixture/in-memory data only: no file is read.
"""

import ast
import math
import pathlib

import pytest

np = pytest.importorskip("numpy")

from dskit.pipeline.base import ConfigError, OutputsConfig
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import NodeSpec, PipelineDocument
from dskit.pipeline.driver import run_document
from dskit.pipeline.libs import vol_estimators as ve
from dskit.pipeline.libs.numpy import ArrayFeatures, accessor_narrowing_problems
from dskit.pipeline.libs.vol_estimators import (
    DEFAULT_CLOSE_FIELD,
    DEFAULT_COLUMN_PREFIX,
    DEFAULT_HIGH_FIELD,
    DEFAULT_LOW_FIELD,
    ESTIMATORS,
    NODE_KINDS,
    Bars,
    VolEstimator,
    VolEstimatorFeatures,
    build_estimators,
    contiguous_mask,
    ewma_variance,
    parkinson_variance,
)
from dskit.pipeline.node import NodeContext
from dskit.pipeline.planner import plan

BAR = 60_000
RETURNS = [None, 0.01, -0.01, 0.02, 0.0]
LN2_X4 = 4.0 * math.log(2.0)

#: One valid spec per kind, and the column label each is expected to take.
SPEC_BY_KIND = {
    "rms": {"kind": "rms", "window": 3},
    "ewma": {"kind": "ewma", "half_life": 2, "lookback": 3},
    "high_low": {"kind": "high_low", "window": 3},
}
SPECS = list(SPEC_BY_KIND.values())


def closes_from(returns, first=100.0):
    """Closes whose successive log returns are ``returns`` (None keeps the price)."""
    out, last = [], first
    for r in returns:
        last = last * math.exp(r) if r is not None else last
        out.append(last)
    return out


def opens_for(n, gaps=()):
    """Bar open times; each index in ``gaps`` skips one more minute before its bar."""
    opens, skipped = [], 0
    for i in range(n):
        skipped += 1 if i in gaps else 0
        opens.append((i + skipped) * BAR)
    return opens


def toy(returns=RETURNS, gaps=(), highs=None, lows=None, spread=0.01):
    """Toy 1-minute bars as parallel lists: ``(opens, highs, lows, closes)``."""
    closes = closes_from(returns)
    highs = highs if highs is not None else [c * (1 + spread) for c in closes]
    lows = lows if lows is not None else [c * (1 - spread) for c in closes]
    return opens_for(len(closes), gaps), list(highs), list(lows), closes


def make_bars(*args, **kwargs):
    opens, highs, lows, closes = toy(*args, **kwargs)
    return Bars(opens, BAR, high=highs, low=lows, close=closes)


def sliced(bars, sl):
    return Bars(bars.open_time[sl], BAR, high=bars.high[sl], low=bars.low[sl], close=bars.close[sl])


def est(**spec):
    return build_estimators([spec])[0]


def squares(returns):
    return [None if r is None else r * r for r in returns]


# ---------------------------------------------------------------------------
# contiguous_mask: the gap rule's one home
# ---------------------------------------------------------------------------


def test_contiguous_mask_marks_a_bar_that_opens_exactly_one_bar_after_the_last():
    mask = contiguous_mask([0, BAR, 3 * BAR, 4 * BAR], BAR)
    assert mask.dtype == bool
    assert mask.tolist() == [False, True, False, True]


def test_contiguous_mask_does_not_bridge_repeats_or_reversals():
    assert contiguous_mask([0, 0, BAR, BAR - 1], BAR).tolist() == [False, False, True, False]
    assert contiguous_mask([5 * BAR, 4 * BAR], BAR).tolist() == [False, False]


def test_contiguous_mask_of_nothing_and_of_one_bar():
    assert contiguous_mask([], BAR).tolist() == []
    assert contiguous_mask([BAR], BAR).tolist() == [False]


@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf"), "60000", None])
def test_contiguous_mask_refuses_a_bar_length_that_is_not_a_positive_number(bad):
    with pytest.raises(ValueError, match="bar_ms"):
        contiguous_mask([0, BAR], bad)


# ---------------------------------------------------------------------------
# ewma_variance: truncated window, weights 0.5 ** (age / half_life)
# ---------------------------------------------------------------------------


def test_ewma_weights_halve_every_half_life_and_the_window_is_truncated():
    sq = [float("nan")] + [1e-4, 1e-4, 4e-4, 0.0]
    out = ewma_variance(sq, 1, 3)
    w = [1.0, 0.5, 0.25]  # newest first
    assert np.isnan(out[:3]).all(), "no full window of valid squares yet"
    assert out[3] == pytest.approx((4e-4 * w[0] + 1e-4 * w[1] + 1e-4 * w[2]) / sum(w), rel=1e-12)
    assert out[4] == pytest.approx((0.0 * w[0] + 4e-4 * w[1] + 1e-4 * w[2]) / sum(w), rel=1e-12)


def test_ewma_with_a_fractional_half_life():
    sq = [3e-4, 1e-4, 2e-4, 5e-4]
    out = ewma_variance(sq, 2.5, 3)
    lam = 0.5 ** (1.0 / 2.5)
    assert out[3] == pytest.approx((5e-4 + 2e-4 * lam + 1e-4 * lam**2) / (1 + lam + lam**2), rel=1e-12)
    assert out[2] == pytest.approx((2e-4 + 1e-4 * lam + 3e-4 * lam**2) / (1 + lam + lam**2), rel=1e-12)


def test_ewma_matches_a_plain_loop_on_random_squares():
    rng = np.random.default_rng(11)
    sq = rng.normal(0, 0.002, 300) ** 2
    half_life, lookback = 7.3, 25
    out = ewma_variance(sq, half_life, lookback)
    weights = [0.5 ** (age / half_life) for age in range(lookback)]
    for i in range(lookback - 1, len(sq)):
        want = sum(w * sq[i - age] for age, w in enumerate(weights)) / sum(weights)
        assert out[i] == pytest.approx(want, rel=1e-12)
    assert np.isnan(out[: lookback - 1]).all()


@pytest.mark.parametrize("start", [0, 41, 150])
def test_ewma_does_not_depend_on_where_the_loaded_history_starts(start):
    rng = np.random.default_rng(12)
    sq = rng.normal(0, 0.002, 400) ** 2
    full = ewma_variance(sq, 9.0, 30)
    cut = ewma_variance(sq[start:], 9.0, 30)
    assert np.isnan(cut[:29]).all()
    np.testing.assert_allclose(cut[29:], full[start + 29:], rtol=1e-12, atol=0)


def test_ewma_blanks_exactly_the_windows_that_hold_a_missing_square():
    sq = np.full(12, 1e-4)
    sq[5] = np.nan
    out = ewma_variance(sq, 2.0, 3)
    assert np.flatnonzero(np.isnan(out)).tolist() == [0, 1, 5, 6, 7]


def test_ewma_of_an_input_shorter_than_the_window_is_all_undefined():
    assert np.isnan(ewma_variance([1e-4, 2e-4], 2.0, 5)).all()
    assert ewma_variance([], 2.0, 5).tolist() == []


def test_ewma_of_a_window_of_one_is_the_square_itself():
    assert ewma_variance([1e-4, 2e-4, 3e-4], 4.0, 1).tolist() == [1e-4, 2e-4, 3e-4]


@pytest.mark.parametrize("half_life, lookback", [
    (0, 3), (-1, 3), (True, 3), (float("nan"), 3), (float("inf"), 3), ("2", 3), (None, 3), (10**400, 3),
    (2, 0), (2, -1), (2, 2.5), (2, True), (2, "3"), (2, None)])
def test_ewma_refuses_a_half_life_or_lookback_it_cannot_use(half_life, lookback):
    with pytest.raises(ValueError):
        ewma_variance([1e-4, 1e-4, 1e-4], half_life, lookback)


# ---------------------------------------------------------------------------
# parkinson_variance: ln(H/L)**2 / (4 ln 2), undefined unless 0 < L <= H
# ---------------------------------------------------------------------------


def test_parkinson_is_the_hand_computed_range_variance():
    out = parkinson_variance([102.0, 105.0, 50.0], [100.0, 102.0, 50.0])
    assert out[0] == pytest.approx(math.log(102 / 100) ** 2 / LN2_X4, rel=1e-12)
    assert out[1] == pytest.approx(math.log(105 / 102) ** 2 / LN2_X4, rel=1e-12)
    assert out[2] == 0.0, "a bar whose high equals its low has a zero range, which is defined"


@pytest.mark.parametrize("high, low", [
    (1.0, 2.0),                   # high below low
    (1.0, 0.0), (1.0, -1.0),      # no positive low
    (float("nan"), 1.0), (1.0, float("nan")),
    (float("inf"), 1.0), (1.0, float("-inf")), (float("inf"), float("inf"))])
def test_parkinson_is_undefined_unless_low_is_positive_and_not_above_high(high, low):
    assert np.isnan(parkinson_variance([high], [low])).all()


def test_parkinson_refuses_arrays_of_different_shapes():
    with pytest.raises(ValueError, match="shape"):
        parkinson_variance([2.0, 2.0], [1.0])


def test_the_docstring_examples_state_what_the_functions_return():
    assert contiguous_mask([0, 60000, 180000], 60000).tolist() == [False, True, False]
    out = ewma_variance([1e-4, 1e-4, 4e-4], 1, 3)
    assert np.isnan(out[:2]).all() and out[2] == pytest.approx(2.7143e-4, rel=1e-4)
    assert parkinson_variance([102.0], [100.0])[0] == pytest.approx(1.4144e-4, rel=1e-4)


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------


def test_bars_carry_only_the_roles_they_are_given_and_say_where_a_bar_is_contiguous():
    bars = Bars([0, BAR, 3 * BAR], BAR, close=[1.0, 1.1, 1.2])
    assert bars.contiguous.tolist() == [False, True, False]
    assert bars.high is None and bars.low is None
    assert bars.close.dtype == np.float64 and bars.open_time.dtype.kind in "iu"


def test_bars_squared_returns_are_undefined_across_a_gap_and_at_the_first_bar():
    bars = make_bars(RETURNS, gaps={3})
    sq = bars.squared_returns()
    assert np.isnan(sq[[0, 3]]).all() and not np.isnan(sq[[1, 2, 4]]).any()
    assert sq[1] == pytest.approx(0.01**2, rel=1e-9) and sq[4] == pytest.approx(0.0, abs=1e-12)


def test_bars_that_lack_a_close_cannot_give_squared_returns():
    with pytest.raises(ValueError, match="close"):
        Bars([0, BAR], BAR, high=[1.0, 1.0], low=[1.0, 1.0]).squared_returns()


def test_bars_refuse_ragged_roles():
    with pytest.raises(ValueError, match="length"):
        Bars([0, BAR], BAR, close=[1.0, 1.1, 1.2])


# ---------------------------------------------------------------------------
# The estimators: hand values, gaps, causality, history, specs
# ---------------------------------------------------------------------------


def test_rolling_rms_is_the_mean_of_the_last_window_squared_log_returns():
    variance = est(kind="rms", window=2).variance(make_bars())
    assert np.isnan(variance[:2]).all(), "a full window of valid returns first"
    assert variance[2] == pytest.approx((0.01**2 + 0.01**2) / 2)
    assert variance[3] == pytest.approx((0.01**2 + 0.02**2) / 2)
    assert variance[4] == pytest.approx((0.02**2 + 0.0) / 2, abs=1e-12)


def test_ewma_estimator_weights_the_squared_returns():
    variance = est(kind="ewma", half_life=1, lookback=3).variance(make_bars())
    w = [1.0, 0.5, 0.25]
    assert np.isnan(variance[:3]).all()
    assert variance[3] == pytest.approx((0.02**2 * w[0] + 0.01**2 * w[1] + 0.01**2 * w[2]) / sum(w))
    assert variance[4] == pytest.approx((0.0 + 0.02**2 * w[1] + 0.01**2 * w[2]) / sum(w), abs=1e-12)


def test_ewma_estimator_with_a_fractional_half_life():
    variance = est(kind="ewma", half_life=2.5, lookback=3).variance(make_bars())
    lam = 0.5 ** (1.0 / 2.5)
    assert variance[3] == pytest.approx((0.02**2 + 0.01**2 * lam + 0.01**2 * lam**2) / (1 + lam + lam**2))


def test_high_low_is_the_parkinson_variance_averaged_over_the_window():
    highs = [101.0, 102.0, 103.0, 105.0, 104.0]
    lows = [100.0, 100.0, 101.0, 102.0, 102.0]
    variance = est(kind="high_low", window=2).variance(make_bars(highs=highs, lows=lows))

    def bar_var(high, low):
        return math.log(high / low) ** 2 / LN2_X4

    assert np.isnan(variance[:2]).all()
    assert variance[2] == pytest.approx((bar_var(102, 100) + bar_var(103, 101)) / 2)
    assert variance[4] == pytest.approx((bar_var(105, 102) + bar_var(104, 102)) / 2)


def test_a_bar_whose_high_is_below_its_low_is_undefined_not_a_range():
    bars = make_bars(highs=[1.0] * 5, lows=[2.0] * 5)
    assert np.isnan(est(kind="high_low", window=2).variance(bars)).all()


LONG_RETURNS = [None] + [0.01 * (1 + i % 3) * (-1) ** i for i in range(1, 14)]


@pytest.mark.parametrize("spec", SPECS, ids=list(SPEC_BY_KIND))
def test_a_missing_minute_blanks_exactly_the_windows_that_hold_it(spec):
    # Bar 6 opens two minutes after bar 5: its return and its range are undefined, so the
    # three-bar windows ending at bars 6, 7 and 8 hold it. Bars 0-2 lack a full window.
    variance = est(**spec).variance(make_bars(LONG_RETURNS, gaps={6}))
    assert np.flatnonzero(np.isnan(variance)).tolist() == [0, 1, 2, 6, 7, 8]


def test_a_value_after_a_gap_uses_only_the_bars_after_it():
    variance = est(kind="rms", window=3).variance(make_bars(LONG_RETURNS, gaps={6}))
    want = sum(r * r for r in LONG_RETURNS[7:10]) / 3
    assert variance[9] == pytest.approx(want)
    assert variance[5] == pytest.approx(sum(r * r for r in LONG_RETURNS[3:6]) / 3)


@pytest.mark.parametrize("spec", SPECS, ids=list(SPEC_BY_KIND))
def test_the_first_bar_has_no_predecessor_so_no_value(spec):
    assert np.isnan(est(**spec).variance(make_bars(LONG_RETURNS))[0])


@pytest.mark.parametrize("spec", [
    {"kind": "rms", "window": 7}, {"kind": "ewma", "half_life": 3, "lookback": 9},
    {"kind": "high_low", "window": 7}], ids=list(SPEC_BY_KIND))
def test_no_estimator_reads_a_bar_after_the_one_it_reports(spec):
    # Causality by prefix: the value at bar i is unchanged when every later bar is cut away
    # or replaced with garbage.
    rng = np.random.default_rng(3)
    full = make_bars([None] + list(rng.normal(0, 0.002, 59)), gaps={30})
    estimator = build_estimators([spec])[0]
    whole = estimator.variance(full)
    for cut in (20, 33, 59):
        np.testing.assert_array_equal(estimator.variance(sliced(full, slice(0, cut + 1))), whole[: cut + 1])
    spiked = Bars(full.open_time, BAR, high=full.high * 1.0, low=full.low * 1.0, close=full.close * 1.0)
    spiked.close[40:] *= 10.0
    spiked.high[40:] *= 10.0
    np.testing.assert_array_equal(estimator.variance(spiked)[:40], whole[:40])


SPECS_FOR_HISTORY = [
    {"kind": "rms", "window": 7}, {"kind": "ewma", "half_life": 3.5, "lookback": 9},
    {"kind": "high_low", "window": 7}, {"kind": "rms", "window": 1}, {"kind": "ewma", "half_life": 1, "lookback": 1}]


@pytest.mark.parametrize("spec", SPECS_FOR_HISTORY)
def test_lookback_bars_is_exactly_the_history_a_value_needs(spec):
    rng = np.random.default_rng(5)
    full = make_bars([None] + list(rng.normal(0, 0.002, 199)))
    estimator = build_estimators([spec])[0]
    whole = estimator.variance(full)
    need = estimator.lookback_bars()
    enough = estimator.variance(sliced(full, slice(len(whole) - need, None)))
    assert enough[-1] == pytest.approx(whole[-1], rel=1e-9), "that many bars are enough"
    short = estimator.variance(sliced(full, slice(len(whole) - need + 1, None)))
    assert np.isnan(short[-1]), "one bar fewer is not"


@pytest.mark.parametrize("cls", list(ESTIMATORS.values()), ids=list(ESTIMATORS))
def test_an_estimator_reads_only_the_roles_it_declares(cls):
    full = make_bars(LONG_RETURNS)
    only = Bars(full.open_time, BAR, **{role: getattr(full, role) for role in cls.needs})
    spec = SPEC_BY_KIND[cls.kind]
    np.testing.assert_array_equal(cls(spec).variance(only), cls(spec).variance(full))
    assert set(cls.needs) <= {"close", "high", "low"}


def test_labels_and_lookbacks_are_each_estimators_own():
    rms, ewma, hl = (est(kind="rms", window=60), est(kind="ewma", half_life=30, lookback=300),
                     est(kind="high_low", window=15))
    assert (rms.label(), ewma.label(), hl.label()) == ("rms_60", "ewma_30", "hl_15")
    assert est(kind="ewma", half_life=2.5, lookback=10).label() == "ewma_2.5"
    assert est(kind="ewma", half_life=30.0, lookback=10).label() == "ewma_30"
    assert est(kind="rms", window=60.0).label() == "rms_60"
    assert (rms.lookback_bars(), ewma.lookback_bars(), hl.lookback_bars()) == (61, 301, 16)


BAD_SPECS = [
    {"kind": "mystery"}, {"kind": "rms"}, {"kind": "rms", "window": 0}, {"kind": "rms", "window": 2.5},
    {"kind": "rms", "window": True}, {"kind": "rms", "window": "2"}, {"kind": "rms", "window": 2, "surprise": 1},
    {"kind": "ewma", "half_life": 0, "lookback": 3}, {"kind": "ewma", "half_life": 3},
    {"kind": "ewma", "half_life": 3, "lookback": 0}, {"kind": "ewma", "half_life": 10**400, "lookback": 3},
    {"kind": "high_low", "window": -1}, {"kind": "high_low", "window": 2, "half_life": 3},
    {"kind": None}, {"kind": ["rms"]}, {"kind": {"rms": 1}}, {"window": 3}, "rms", {}, None, 3, ["rms"]]


@pytest.mark.parametrize("bad", BAD_SPECS, ids=repr)
def test_a_bad_spec_is_refused_not_defaulted(bad):
    with pytest.raises(ValueError):
        build_estimators([bad])


@pytest.mark.parametrize("not_a_list", [None, 5, "rms", {"kind": "rms", "window": 2}])
def test_the_specs_must_be_a_list(not_a_list):
    with pytest.raises(ValueError, match="list"):
        build_estimators(not_a_list)


@pytest.mark.parametrize("cls", list(ESTIMATORS.values()), ids=list(ESTIMATORS))
def test_every_knob_of_every_kind_is_required_and_every_other_is_refused(cls):
    good = SPEC_BY_KIND[cls.kind]
    assert set(good) == {"kind", *cls.keys}, "the pinned spec states every knob the kind declares"
    for key in cls.keys:
        with pytest.raises(ValueError, match=key):
            build_estimators([{k: v for k, v in good.items() if k != key}])
    with pytest.raises(ValueError, match="surprise"):
        build_estimators([{**good, "surprise": 1}])


def test_two_specs_that_would_write_the_same_label_are_refused():
    for specs in (
        [{"kind": "rms", "window": 5}, {"kind": "rms", "window": 5}],
        [{"kind": "rms", "window": 5}, {"kind": "rms", "window": 5.0}],
        [{"kind": "ewma", "half_life": 30, "lookback": 300}, {"kind": "ewma", "half_life": 30, "lookback": 600}],
        [{"kind": "ewma", "half_life": 1, "lookback": 5}, {"kind": "ewma", "half_life": 1.0000001, "lookback": 5}],
    ):
        with pytest.raises(ValueError, match="repeat"):
            build_estimators(specs)
    assert len(build_estimators([{"kind": "rms", "window": 5}, {"kind": "high_low", "window": 5}])) == 2


def test_the_registry_is_the_whole_vocabulary():
    assert set(ESTIMATORS) == {"rms", "ewma", "high_low"}
    assert all(cls.kind == name for name, cls in ESTIMATORS.items())
    assert all(issubclass(cls, VolEstimator) for cls in ESTIMATORS.values())


def test_a_kind_added_to_the_registry_is_built_without_touching_the_builder(monkeypatch):
    class Flat(VolEstimator):
        kind, keys, needs = "flat", (), ("close",)

        @classmethod
        def spec_problems(cls, spec):
            return []

        def label(self):
            return "flat"

        def lookback_bars(self):
            return 1

        def variance(self, bars):
            return np.zeros(bars.close.shape)

    monkeypatch.setitem(ESTIMATORS, "flat", Flat)
    assert build_estimators([{"kind": "flat"}])[0].label() == "flat"


def test_the_estimator_base_is_abstract_all_the_way_down():
    with pytest.raises(TypeError):
        VolEstimator({"kind": "x"})

    class Incomplete(VolEstimator):
        kind, keys, needs = "incomplete", (), ("close",)

        @classmethod
        def spec_problems(cls, spec):
            return []

        def label(self):
            return "incomplete"

        def lookback_bars(self):
            return 1

    with pytest.raises(TypeError):  # no variance(): refused at construction, not at first use
        Incomplete({"kind": "incomplete"})


# ---------------------------------------------------------------------------
# Module hygiene: the public surface and the tier rules this module owes
# ---------------------------------------------------------------------------

MODULE_PATH = pathlib.Path(ve.__file__)


def test_the_public_surface_is_declared_and_complete():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    public = {name for name in defined if not name.startswith("_") and name != "__all__"}
    assert set(ve.__all__) == public
    assert all(hasattr(ve, name) for name in ve.__all__)
    assert not [name for name in ve.__all__ if name.startswith("_")]


def test_no_private_name_of_another_module_is_imported():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names]
    assert not [name for name in imported if name.startswith("_")]


def test_the_pack_registers_nothing_and_is_wired_by_import_path():
    assert NODE_KINDS == ()


# ---------------------------------------------------------------------------
# VolEstimatorFeatures: the node over the numpy pack's ArrayFeatures seam
# ---------------------------------------------------------------------------


def records(returns=RETURNS, gaps=(), name="SYN", highs=None, lows=None, spread=0.01, **names):
    """Bar records: instrument, contract, asof_ms (the bar's open) and OHLC fields."""
    opens, highs, lows, closes = toy(returns, gaps, highs, lows, spread)
    order = names.get("order", "asof_ms")
    return [{"instrument": name, "contract": f"{name}-{i}", order: opens[i], "high": highs[i], "low": lows[i],
             "close": closes[i]} for i in range(len(closes))]


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))


def params(*estimators, **extra):
    base = {"bar_ms": BAR, "carry_fields": ["contract"],
            "estimators": list(estimators) or [{"kind": "rms", "window": 2}]}
    base.update(extra)
    return base


def run_rows(tmp_path, node_params, recs):
    return VolEstimatorFeatures("vol", node_params).run(ctx(tmp_path), {"records": recs})["rows"]


def column(rows, name):
    return np.array([np.nan if r[name] is None else r[name] for r in rows], dtype=float)


def test_rows_carry_the_hand_computed_variance_of_every_estimator(tmp_path):
    highs = [101.0, 102.0, 103.0, 105.0, 104.0]
    lows = [100.0, 100.0, 101.0, 102.0, 102.0]
    rows = run_rows(tmp_path, params({"kind": "rms", "window": 2}, {"kind": "ewma", "half_life": 1, "lookback": 3},
                                     {"kind": "high_low", "window": 2}), records(highs=highs, lows=lows))
    assert [r["contract"] for r in rows] == [f"SYN-{i}" for i in range(5)]
    assert set(rows[0]) == {"contract", "var_rms_2", "var_ewma_1", "var_hl_2"}
    assert rows[0]["var_rms_2"] is None and rows[1]["var_rms_2"] is None, "warm-up is an absent belief"
    assert rows[2]["var_rms_2"] == pytest.approx(1e-4)
    assert rows[3]["var_rms_2"] == pytest.approx((0.01**2 + 0.02**2) / 2)
    w = [1.0, 0.5, 0.25]
    assert rows[3]["var_ewma_1"] == pytest.approx((0.02**2 + 0.01**2 * 0.5 + 0.01**2 * 0.25) / sum(w))
    assert rows[2]["var_ewma_1"] is None

    def bar_var(h, lo):
        return math.log(h / lo) ** 2 / LN2_X4

    assert rows[2]["var_hl_2"] == pytest.approx((bar_var(102, 100) + bar_var(103, 101)) / 2)
    assert rows[1]["var_hl_2"] is None


def test_a_missing_minute_makes_every_window_that_holds_it_absent(tmp_path):
    rows = run_rows(tmp_path, params({"kind": "rms", "window": 2}, {"kind": "high_low", "window": 2}),
                    records(gaps={3}))
    assert rows[2]["var_rms_2"] == pytest.approx(1e-4) and rows[2]["var_hl_2"] is not None
    for i in (3, 4):
        assert rows[i]["var_rms_2"] is None and rows[i]["var_hl_2"] is None


def test_a_gap_bound_in_the_stream_framing_changes_nothing_the_gap_rule_did_not(tmp_path):
    # max_gap splits the stream into segments before any arithmetic; the gap rule already blanks
    # every window that holds a gap, so the two routes must agree everywhere.
    spec = params({"kind": "rms", "window": 2}, {"kind": "ewma", "half_life": 2, "lookback": 3},
                  {"kind": "high_low", "window": 2})
    recs = records(LONG_RETURNS, gaps={6, 10})
    plain = run_rows(tmp_path, spec, recs)
    framed = run_rows(tmp_path, {**spec, "max_gap": BAR}, recs)
    for name in ("var_rms_2", "var_ewma_2", "var_hl_2"):
        np.testing.assert_allclose(column(framed, name), column(plain, name), rtol=1e-9, equal_nan=True)


def test_each_group_is_framed_on_its_own_and_gaps_do_not_cross_groups(tmp_path):
    spec = params({"kind": "rms", "window": 2}, {"kind": "high_low", "window": 2})
    a, b = records(LONG_RETURNS, gaps={6}, name="AAA"), records(LONG_RETURNS[::-1], name="BBB")
    both = run_rows(tmp_path, spec, a + b)
    assert len(both) == len(a) + len(b)
    for name, own in (("AAA", a), ("BBB", b)):
        mine = [r for r, rec in zip(both, a + b) if rec["instrument"] == name]
        alone = run_rows(tmp_path, spec, own)
        for col in ("var_rms_2", "var_hl_2"):
            np.testing.assert_allclose(column(mine, col), column(alone, col), rtol=1e-9, equal_nan=True)


def test_a_projects_own_field_names_are_spelled_in_the_document_not_the_code(tmp_path):
    recs = [{"sym": "X", "t": r["asof_ms"], "px": r["close"], "hi": r["high"], "lo": r["low"], "id": r["contract"]}
            for r in records(LONG_RETURNS)]
    spec = params({"kind": "rms", "window": 3}, {"kind": "high_low", "window": 3},
                  group_field="sym", order_field="t", close_field="px", high_field="hi", low_field="lo",
                  carry_fields=["id"], require_fields=["id"], column_prefix="rv_")
    renamed = run_rows(tmp_path, spec, recs)
    default = run_rows(tmp_path, params({"kind": "rms", "window": 3}, {"kind": "high_low", "window": 3}),
                       records(LONG_RETURNS))
    assert set(renamed[0]) == {"id", "rv_rms_3", "rv_hl_3"}
    np.testing.assert_allclose(column(renamed, "rv_rms_3"), column(default, "var_rms_3"), equal_nan=True)
    np.testing.assert_allclose(column(renamed, "rv_hl_3"), column(default, "var_hl_3"), equal_nan=True)


def test_only_the_roles_the_estimators_need_are_lifted(tmp_path):
    def fields(*estimators, **extra):
        return VolEstimatorFeatures("v", params(*estimators, **extra)).fields()

    rms, hl = {"kind": "rms", "window": 2}, {"kind": "high_low", "window": 2}
    assert fields(rms) == (DEFAULT_CLOSE_FIELD,)
    assert fields(hl) == (DEFAULT_HIGH_FIELD, DEFAULT_LOW_FIELD)
    assert fields(hl, rms, close_field="c", high_field="h", low_field="l") == ("c", "h", "l")
    bare = [{k: v for k, v in r.items() if k not in ("high", "low")} for r in records()]
    rows = run_rows(tmp_path, params(rms), bare)
    assert rows[2]["var_rms_2"] == pytest.approx(1e-4)


def test_the_defaults_are_named_once():
    assert (DEFAULT_CLOSE_FIELD, DEFAULT_HIGH_FIELD, DEFAULT_LOW_FIELD) == ("close", "high", "low")
    node = VolEstimatorFeatures("v", params())
    assert (node.close_field(), node.high_field(), node.low_field()) == (
        DEFAULT_CLOSE_FIELD, DEFAULT_HIGH_FIELD, DEFAULT_LOW_FIELD)
    assert node.column_prefix() == DEFAULT_COLUMN_PREFIX
    explicit = VolEstimatorFeatures("v", params(close_field="c", high_field="h", low_field="l", column_prefix="rv_"))
    assert (explicit.close_field(), explicit.high_field(), explicit.low_field(), explicit.column_prefix()) == (
        "c", "h", "l", "rv_")


def test_the_node_is_an_array_features_that_narrows_fields_away():
    assert issubclass(VolEstimatorFeatures, ArrayFeatures)
    assert accessor_narrowing_problems(VolEstimatorFeatures) == []
    assert "fields" not in VolEstimatorFeatures._PARAMS
    assert VolEstimatorFeatures.role == "tensor"
    assert all(callable(getattr(VolEstimatorFeatures, knob)) for knob in VolEstimatorFeatures._PARAMS)


@pytest.mark.parametrize("bad, knob", [
    ({"estimators": [{"kind": "rms", "window": 2}]}, "bar_ms"),
    (params(bar_ms=0), "bar_ms"), (params(bar_ms=True), "bar_ms"), (params(bar_ms=-5), "bar_ms"),
    (params(bar_ms=float("inf")), "bar_ms"), (params(bar_ms=float("nan")), "bar_ms"),
    (params(bar_ms="60000"), "bar_ms"), (params(bar_ms=None), "bar_ms"),
    ({"bar_ms": BAR}, "estimators"),
    (params(estimators=[]), "estimators"), (params(estimators="rms"), "estimators"),
    (params(estimators=[{"kind": "mystery"}]), "estimators"),
    (params(estimators=[{"kind": "rms", "window": 2}, {"kind": "rms", "window": 2}]), "estimators"),
    (params(typo=1), "typo"), (params(fields=["close"]), "fields"),
    (params(column_prefix=""), "column_prefix"), (params(column_prefix=3), "column_prefix"),
    (params(close_field=""), "close_field"), (params(high_field=3), "high_field"),
    (params(low_field=None), "low_field"),
    (params(close_field="asof_ms"), "close_field"), (params(order_field="t", low_field="t"), "low_field"),
    (params(causality_check="yes"), "causality_check"), (params(max_gap=0), "max_gap"),
])
def test_invalid_params_are_refused_by_name(bad, knob):
    with pytest.raises(ConfigError, match=knob):
        VolEstimatorFeatures("vol", bad)


def test_a_wire_in_place_of_a_literal_passes_plan_time_validation():
    wired = {"bar_ms": "$cfg.bar_ms", "estimators": "$cfg.estimators", "close_field": "$cfg.close",
             "high_field": "$cfg.high", "low_field": "$cfg.low", "column_prefix": "$cfg.prefix"}
    assert VolEstimatorFeatures.validate_params(wired) == []


def test_the_estimator_vocabulary_the_node_accepts_is_the_registry(monkeypatch):
    for kind in ESTIMATORS:
        assert VolEstimatorFeatures.validate_params(params(SPEC_BY_KIND[kind])) == []
    assert VolEstimatorFeatures.validate_params(params({"kind": "garch"}))


def test_a_feature_column_may_not_take_a_carried_field_name(tmp_path):
    carrying = [dict(r, **{"var_rms_2": 1.0}) for r in records()]
    with pytest.raises(ValueError, match="collide"):
        run_rows(tmp_path, params({"kind": "rms", "window": 2}, carry_fields=["var_rms_2"]), carrying)
    rows = run_rows(tmp_path, params({"kind": "rms", "window": 2}, column_prefix="v_", carry_fields=["var_rms_2"]),
                    carrying)
    assert set(rows[0]) == {"var_rms_2", "v_rms_2"}


def test_the_numpy_packs_causality_guard_is_what_screens_the_estimators(tmp_path, monkeypatch):
    class Leaky(VolEstimator):
        kind, keys, needs = "leaky", (), ("close",)

        @classmethod
        def spec_problems(cls, spec):
            return []

        def label(self):
            return "leaky"

        def lookback_bars(self):
            return 1

        def variance(self, bars):
            out = bars.close.copy()
            out[:-1] = bars.close[1:]  # the NEXT bar's close
            return out

    recs = records(LONG_RETURNS)
    honest = params({"kind": "rms", "window": 2})
    assert run_rows(tmp_path, honest, recs)
    monkeypatch.setitem(ESTIMATORS, "leaky", Leaky)
    with pytest.raises(ValueError, match="not causal"):
        run_rows(tmp_path, params({"kind": "leaky"}), recs)
    assert run_rows(tmp_path, params({"kind": "leaky"}, causality_check=False), recs), "the document owns turning it off"


def test_a_long_stream_with_gaps_passes_the_guard_with_the_shipped_estimators(tmp_path):
    rng = np.random.default_rng(21)
    recs = records([None] + list(rng.normal(0, 0.002, 599)), gaps={150, 151, 400}, name="LONG")
    spec = params({"kind": "rms", "window": 20}, {"kind": "ewma", "half_life": 7.5, "lookback": 60},
                  {"kind": "high_low", "window": 20})
    rows = run_rows(tmp_path, spec, recs)
    assert len(rows) == 600
    # Returns are undefined at bar 0 and at the three bars after a missing minute (150, 151, 400); a
    # window of w bars ending at e holds returns e-w+1..e.
    for name, width in (("var_rms_20", 20), ("var_hl_20", 20), ("var_ewma_7.5", 60)):
        want = sorted({e for bad in (0, 150, 151, 400) for e in range(bad, bad + width)})
        assert np.flatnonzero(np.isnan(column(rows, name))).tolist() == [e for e in want if e < 600], name


def test_a_serving_row_needs_exactly_lookback_bars_and_equals_the_training_row(tmp_path):
    rng = np.random.default_rng(8)
    recs = records([None] + list(rng.normal(0, 0.002, 99)))
    node = VolEstimatorFeatures("vol", params({"kind": "rms", "window": 5}, {"kind": "ewma", "half_life": 3,
                                                                           "lookback": 10},
                                              {"kind": "high_low", "window": 5}))
    need = node.lookback_bars()
    assert need == 11
    training = node.run(ctx(tmp_path), {"records": recs})["rows"][-1]
    served = node.latest_rows(recs[-need:])["SYN"]
    assert set(served) == set(training)
    for col in training:
        if col != "contract":
            assert served[col] == pytest.approx(training[col], rel=1e-9)
    assert node.latest_rows(recs[-(need - 1):]) == {}, "one bar fewer leaves the ewma undefined, so no row"


def test_an_input_that_is_not_a_list_is_refused_by_name():
    problems = VolEstimatorFeatures("vol", params()).validate_inputs({"records": iter([])})
    assert problems and "list" in problems[0]


# ---------------------------------------------------------------------------
# Conformance and import-path wiring
# ---------------------------------------------------------------------------


def _probes(tmp_path):
    return {
        "vol-estimator-features": NodeProbe(
            params=params({"kind": "rms", "window": 2}, {"kind": "ewma", "half_life": 1, "lookback": 2},
                          {"kind": "high_low", "window": 2}),
            required=("bar_ms", "estimators"),
            inputs={"records": records()}, stream_ports=("records",), runnable=True,
        ),
    }


TestVolEstimatorsConformance = conformance_suite(
    registry=(("vol-estimator-features", VolEstimatorFeatures),),
    module="dskit.pipeline.libs.vol_estimators",
    probes=_probes,
    expected_roles={"vol-estimator-features": "tensor"},
    name="TestVolEstimatorsConformance",
)


def _document(tmp_path, estimators):
    return PipelineDocument(
        name="vol-int",
        pipeline={
            "bars": NodeSpec(
                uses="dskit.pipeline.synthetic_nodes:SynthEvents",
                params={"n_events": 30, "n_instruments": 2, "seed": 4, "spacing_ms": BAR},
            ),
            "vol": NodeSpec(
                uses="dskit.pipeline.libs.vol_estimators:VolEstimatorFeatures",
                inputs={"records": "$bars.events"},
                params={"bar_ms": BAR, "close_field": "mid", "carry_fields": ["contract"], "estimators": estimators},
            ),
        },
        outputs=OutputsConfig(run_root=str(tmp_path / "runs")),
    )


def test_import_path_wiring_plans_and_runs_through_the_real_driver(tmp_path):
    doc = _document(tmp_path, [{"kind": "rms", "window": 3}, {"kind": "ewma", "half_life": 4, "lookback": 5}])
    assert plan(doc).role_of("vol") == "tensor"
    result = run_document(doc, asof="2026-01-01")
    assert result.state == "ran" and result.exit_code == 0
    rows = result.outputs["vol"]["rows"]
    assert len(rows) == 60
    assert sum(r["var_rms_3"] is None for r in rows) == 2 * 3
    assert sum(r["var_ewma_4"] is None for r in rows) == 2 * 5
    assert result.outputs["vol"]["metrics"]["n_columns"] == 2


def test_the_estimator_specs_are_part_of_the_run_identity(tmp_path):
    one = _document(tmp_path, [{"kind": "ewma", "half_life": 4, "lookback": 5}])
    other = _document(tmp_path, [{"kind": "ewma", "half_life": 5, "lookback": 5}])
    assert one.hash != other.hash
