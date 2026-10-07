"""Parity of the dskit estimators with their interim reference (ADR-0241).

``children/crypto_trading/crypto_trading/vol_estimators.py`` is the behaviour the new pack
graduates. This file loads that module BY FILE PATH (dskit never imports a child, and a test
that imported ``crypto_trading`` would need the child installed) and holds the pack to it on
shared fixtures: the same bars, the same specs, the same numbers, and, through the node, the
same columns once ``column_prefix`` is the reference's ``rv_``.

When the child moves over to dskit and its interim module is removed, this file has nothing
left to compare with and skips itself, saying why; the hand-computed values in
``test_vol_estimators.py`` remain the standing proof.

KNOWN, DELIBERATE DIFFERENCES (neither can be reached by a fixture below):

* a range with an infinite high or low is undefined here, where the reference returns an
  infinite variance; the node never sees one, because the numpy pack lifts a non-finite cell
  as NaN;
* ``contiguous_mask`` of NO bars is empty, where the reference's ``Bars`` of no bars holds
  one entry.
"""

import importlib.util
import pathlib

import pytest

np = pytest.importorskip("numpy")

from dskit.pipeline.libs.vol_estimators import Bars, VolEstimatorFeatures, build_estimators
from dskit.pipeline.node import NodeContext

REFERENCE = (pathlib.Path(__file__).parents[2] / "children" / "crypto_trading" / "crypto_trading"
             / "vol_estimators.py")
BAR = 60_000

SPECS = [
    {"kind": "rms", "window": 1}, {"kind": "rms", "window": 3}, {"kind": "rms", "window": 60},
    {"kind": "ewma", "half_life": 1, "lookback": 3}, {"kind": "ewma", "half_life": 2.5, "lookback": 10},
    {"kind": "ewma", "half_life": 0.3, "lookback": 50}, {"kind": "ewma", "half_life": 30, "lookback": 300},
    {"kind": "high_low", "window": 1}, {"kind": "high_low", "window": 5}, {"kind": "high_low", "window": 60},
]


@pytest.fixture(scope="module")
def reference():
    if not REFERENCE.is_file():
        pytest.skip(f"the interim module {REFERENCE.name} is gone: the child has moved to dskit")
    spec = importlib.util.spec_from_file_location("_reference_vol_estimators", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_bars(seed, n=900):
    """A seeded random walk with missing minutes, bad ranges and a missing close."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.001, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.001, n)))
    steps = np.ones(n, dtype=np.int64)
    steps[[40, 41, 400, 401, 402, 700]] = 2          # missing minutes (including back to back)
    open_time = np.cumsum(steps) * BAR
    low[[10, 200]] = high[[10, 200]] * 1.01          # high below low
    low[300] = 0.0                                   # no positive low
    high[500] = np.nan                               # no high
    close[600] = np.nan                              # no close
    return open_time, high, low, close


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("spec", SPECS, ids=lambda s: "-".join(str(v) for v in s.values()))
def test_every_estimator_matches_the_reference_on_shared_bars(reference, seed, spec):
    open_time, high, low, close = fixture_bars(seed)
    ours = build_estimators([spec])[0]
    theirs = reference.build_estimators([spec])[0]
    got = ours.variance(Bars(open_time, BAR, high=high, low=low, close=close))
    want = theirs.variance(reference.Bars(open_time, high, low, close, BAR))
    assert np.isnan(want).any() and not np.isnan(want).all(), "the fixture exercises both sides"
    np.testing.assert_allclose(got, want, rtol=1e-12, atol=0, equal_nan=True)
    assert ours.lookback_bars() == theirs.lookback_bars()


def test_the_vocabulary_and_the_knobs_are_the_references(reference):
    from dskit.pipeline.libs.vol_estimators import ESTIMATORS

    assert set(ESTIMATORS) == set(reference.ESTIMATORS)
    for kind, cls in ESTIMATORS.items():
        assert cls.keys == reference.ESTIMATORS[kind].keys


def test_the_reference_refuses_what_the_pack_refuses(reference):
    for bad in ({"kind": "rms"}, {"kind": "rms", "window": 0}, {"kind": "rms", "window": 2.5},
                {"kind": "ewma", "half_life": 0, "lookback": 3}, {"kind": "ewma", "half_life": 3},
                {"kind": "high_low", "window": -1}, {"kind": "rms", "window": 2, "surprise": 1}, {"kind": "x"}):
        with pytest.raises(ValueError):
            reference.build_estimators([bad])
        with pytest.raises(ValueError):
            build_estimators([bad])


def test_the_node_writes_the_references_columns_with_the_references_numbers(reference, tmp_path):
    open_time, high, low, close = fixture_bars(7)
    records = [{"instrument": "BTC", "contract": f"c{i}", "asof_ms": int(open_time[i]), "high": float(high[i]),
                "low": float(low[i]), "close": float(close[i])} for i in range(len(close))]
    node = VolEstimatorFeatures("vol", {"bar_ms": BAR, "column_prefix": "rv_", "carry_fields": ["contract"],
                                        "estimators": SPECS})
    rows = node.run(NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)), {"records": records})["rows"]
    bars = reference.Bars(open_time, high, low, close, BAR)
    for built in reference.build_estimators(SPECS):
        want = built.variance(bars)
        got = np.array([np.nan if r[built.column()] is None else r[built.column()] for r in rows])
        np.testing.assert_allclose(got, want, rtol=1e-12, atol=0, equal_nan=True, err_msg=built.column())
    assert {c for c in rows[0] if c != "contract"} == {e.column() for e in reference.build_estimators(SPECS)}


def test_the_reference_helpers_the_pack_restates_agree_with_the_pack(reference):
    from dskit.pipeline.libs.vol_estimators import contiguous_mask

    open_time, *_ = fixture_bars(3)
    np.testing.assert_array_equal(contiguous_mask(open_time, BAR), reference.Bars(open_time, [1.0] * len(open_time),
                                  [1.0] * len(open_time), [1.0] * len(open_time), BAR).contiguous)
