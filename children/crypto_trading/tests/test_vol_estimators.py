"""Volatility estimators on 1-minute bars: rolling RMS, EWMA, high-low.

Each expected number is computed by hand in plain Python from the same toy closes, so
the numpy path is checked against an independent restatement. The estimators return a
PER-BAR variance; the spot node converts units.
"""

import math

import numpy as np
import pytest

from crypto_trading.vol_estimators import ESTIMATORS, Bars, build_estimators

BAR = 60_000
RETURNS = [None, 0.01, -0.01, 0.02, 0.0]


def closes_from(returns, first=100.0):
    out, last = [], first
    for r in returns:
        last = last * math.exp(r) if r is not None else last
        out.append(last)
    return out


def bars(returns=RETURNS, gaps=(), highs=None, lows=None):
    """Toy bars; ``gaps`` are bar indexes whose open time skips one minute before them."""
    closes = closes_from(returns)
    opens, skipped = [], 0
    for i in range(len(closes)):
        skipped += 1 if i in gaps else 0
        opens.append((i + skipped) * BAR)
    return Bars(np.array(opens), np.array(highs or closes, float), np.array(lows or closes, float),
                np.array(closes), BAR)


def est(**spec):
    return build_estimators([spec])[0]


def test_rolling_rms_is_the_root_mean_square_of_the_last_window_log_returns():
    variance = est(kind="rms", window=2).variance(bars())
    assert np.isnan(variance[:2]).all(), "needs a full window of valid returns"
    assert variance[2] == pytest.approx((0.01**2 + 0.01**2) / 2)
    assert variance[3] == pytest.approx((0.01**2 + 0.02**2) / 2)
    assert variance[4] == pytest.approx((0.02**2 + 0.0) / 2)


def test_a_missing_minute_is_never_bridged_by_a_return():
    gapped = bars(gaps={3})  # a bar vanished between index 2 and 3
    variance = est(kind="rms", window=2).variance(gapped)
    assert variance[2] == pytest.approx(1e-4)
    assert np.isnan(variance[3]) and np.isnan(variance[4]), "the windows holding the gap are undefined"


def test_ewma_weights_halve_every_half_life_and_the_window_is_truncated():
    variance = est(kind="ewma", half_life=1, lookback=3).variance(bars())
    w = np.array([1.0, 0.5, 0.25])
    assert np.isnan(variance[:3]).all()
    assert variance[3] == pytest.approx((0.02**2 * 1 + 0.01**2 * 0.5 + 0.01**2 * 0.25) / w.sum())
    assert variance[4] == pytest.approx((0.0 + 0.02**2 * 0.5 + 0.01**2 * 0.25) / w.sum())


def test_ewma_with_a_fractional_half_life():
    half_life = 2.5
    variance = est(kind="ewma", half_life=half_life, lookback=3).variance(bars())
    lam = 0.5 ** (1.0 / half_life)
    expected = (0.02**2 + 0.01**2 * lam + 0.01**2 * lam**2) / (1 + lam + lam**2)
    assert variance[3] == pytest.approx(expected)


def test_high_low_is_the_parkinson_range_variance_averaged_over_the_window():
    highs = [101.0, 102.0, 103.0, 105.0, 104.0]
    lows = [100.0, 100.0, 101.0, 102.0, 102.0]
    variance = est(kind="high_low", window=2).variance(bars(highs=highs, lows=lows))

    def bar_var(high, low):
        return math.log(high / low) ** 2 / (4.0 * math.log(2.0))

    assert np.isnan(variance[:2]).all()
    assert variance[2] == pytest.approx((bar_var(102, 100) + bar_var(103, 101)) / 2)
    assert variance[4] == pytest.approx((bar_var(105, 102) + bar_var(104, 102)) / 2)


def test_a_bar_whose_high_is_below_its_low_is_undefined_not_a_range():
    toy = bars(highs=[1.0, 1.0, 1.0, 1.0, 1.0], lows=[2.0, 2.0, 2.0, 2.0, 2.0])  # high < low
    assert np.isnan(est(kind="high_low", window=2).variance(toy)).all()


def test_the_column_names_and_lookbacks_are_each_estimator_s_own():
    rms, ewma, hl = (est(kind="rms", window=60), est(kind="ewma", half_life=30, lookback=300),
                     est(kind="high_low", window=15))
    assert (rms.column(), ewma.column(), hl.column()) == ("rv_rms_60", "rv_ewma_30", "rv_hl_15")
    assert est(kind="ewma", half_life=2.5, lookback=10).column() == "rv_ewma_2.5"
    assert (rms.lookback_bars(), ewma.lookback_bars(), hl.lookback_bars()) == (61, 301, 16)


@pytest.mark.parametrize("kind, extra", [("rms", {"window": 7}), ("ewma", {"half_life": 3, "lookback": 9}),
                                         ("high_low", {"window": 7})])
def test_no_estimator_reads_a_bar_after_the_one_it_reports(kind, extra):
    # Causality by construction: the value at bar i is unchanged when every later bar is cut away
    # or replaced with garbage.
    rng = np.random.default_rng(3)
    returns = [None] + list(rng.normal(0, 0.002, 59))
    full = bars(returns)
    estimator = est(kind=kind, **extra)
    whole = estimator.variance(full)
    for cut in (20, 33, 59):
        head = Bars(full.open_time[: cut + 1], full.high[: cut + 1], full.low[: cut + 1],
                    full.close[: cut + 1], BAR)
        prefix = estimator.variance(head)
        assert np.array_equal(prefix, whole[: cut + 1], equal_nan=True)
    spiked = Bars(full.open_time, full.high.copy(), full.low.copy(), full.close.copy(), BAR)
    spiked.close[40:] *= 10.0
    spiked.high[40:] *= 10.0
    assert np.array_equal(estimator.variance(spiked)[:40], whole[:40], equal_nan=True)


def test_specs_are_validated_and_default_deny():
    for bad in ({"kind": "mystery"}, {"kind": "rms"}, {"kind": "rms", "window": 0},
                {"kind": "rms", "window": 2.5}, {"kind": "rms", "window": True},
                {"kind": "rms", "window": 2, "surprise": 1}, {"kind": "ewma", "half_life": 0, "lookback": 3},
                {"kind": "ewma", "half_life": 3}, {"kind": "ewma", "half_life": 3, "lookback": 0},
                {"kind": "high_low", "window": -1}, "rms", {}):
        with pytest.raises(ValueError):
            build_estimators([bad])
    with pytest.raises(ValueError, match="repeat"):
        build_estimators([{"kind": "rms", "window": 5}, {"kind": "rms", "window": 5}])


def test_the_registry_is_the_whole_vocabulary():
    assert set(ESTIMATORS) == {"rms", "ewma", "high_low"}
