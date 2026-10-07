"""vol_estimators against the behaviour it graduated from, frozen as data (ADR-0241), plus its price-range pins.

The pack graduated from the crypto_trading child's interim module, which an earlier parity test loaded from disk. The child
moved onto the pack and the module was deleted, so that test was removed and nothing skips. ``golden/vol_estimators_reference.json``
is that module's output (taken at the last commit that had it, ``a2749a3``) over one 500-bar fixture with every hard case: missing
minutes (two back to back), a high below its low, a zero low, a missing high, a missing close and a bar priced below one.
The fixture's inputs are frozen with it, since a random generator's stream is not a stable reference. The file IS the
reference now: nothing regenerates it.
"""

import json
import pathlib

import pytest

np = pytest.importorskip("numpy")

from dskit.pipeline.libs.vol_estimators import (  # noqa: E402
    ESTIMATORS,
    Bars,
    VolEstimatorFeatures,
    build_estimators,
    contiguous_mask,
    ewma_variance,
    parkinson_variance,
)
from dskit.pipeline.node import NodeContext  # noqa: E402

GOLDEN = json.loads((pathlib.Path(__file__).parent / "golden" / "vol_estimators_reference.json").read_text(encoding="utf-8"))
BAR = GOLDEN["bar_ms"]
OPEN_TIME = np.array(GOLDEN["open_time"], dtype=np.int64)
HIGH, LOW, CLOSE = (np.array([np.nan if v is None else v for v in GOLDEN[k]]) for k in ("high", "low", "close"))
WANT = {name: np.array([np.nan if v is None else v for v in case["variance"]]) for name, case in GOLDEN["estimators"].items()}
CASES = sorted(GOLDEN["estimators"])


@pytest.mark.parametrize("name", CASES)
def test_every_estimator_reproduces_the_former_childs_variance_bar_for_bar(name):
    case = GOLDEN["estimators"][name]
    built = build_estimators([case["spec"]])[0]
    got = built.variance(Bars(OPEN_TIME, BAR, high=HIGH, low=LOW, close=CLOSE))
    np.testing.assert_allclose(got, WANT[name], rtol=1e-12, atol=0, equal_nan=True)
    assert np.isnan(got).any() and not np.isnan(got).all(), "the fixture exercises both sides of every estimator"
    assert built.lookback_bars() == case["lookback_bars"]
    assert "rv_" + built.label() == case["column"], "the former child's column is the prefix plus the label"


def test_the_node_writes_the_former_childs_columns_with_its_numbers(tmp_path):
    records = [{"instrument": "BTC", "contract": f"c{i}", "asof_ms": int(OPEN_TIME[i]), "high": float(HIGH[i]),
                "low": float(LOW[i]), "close": float(CLOSE[i])} for i in range(len(CLOSE))]
    specs = [GOLDEN["estimators"][name]["spec"] for name in CASES]
    node = VolEstimatorFeatures("vol", {"bar_ms": BAR, "column_prefix": "rv_", "carry_fields": ["contract"], "estimators": specs})
    rows = node.run(NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path)), {"records": records})["rows"]
    assert {c for c in rows[0] if c != "contract"} == {GOLDEN["estimators"][name]["column"] for name in CASES}
    for name in CASES:
        got = np.array([np.nan if r[GOLDEN["estimators"][name]["column"]] is None else r[GOLDEN["estimators"][name]["column"]]
                        for r in rows])
        np.testing.assert_allclose(got, WANT[name], rtol=1e-12, atol=0, equal_nan=True, err_msg=name)


def test_the_gap_rule_is_the_former_childs_a_bar_after_a_missing_minute_has_no_predecessor():
    np.testing.assert_array_equal(contiguous_mask(OPEN_TIME, BAR), np.array(GOLDEN["contiguous"], dtype=bool))
    mask = contiguous_mask(OPEN_TIME, BAR)
    assert not mask[[0, 40, 41, 100, 101, 102, 150]].any(), "the first bar, and each bar that follows a missing minute"
    assert mask[[1, 42, 103, 151]].all(), "the bar after a bar that follows a gap is contiguous again"


def test_the_vocabulary_and_knobs_are_the_former_childs():
    assert {kind: list(cls.keys) for kind, cls in ESTIMATORS.items()} == GOLDEN["vocabulary"]


@pytest.mark.parametrize("bad", GOLDEN["refused_specs"], ids=lambda spec: json.dumps(spec, sort_keys=True))
def test_a_spec_the_former_child_refused_is_refused(bad):
    with pytest.raises(ValueError):
        build_estimators([bad])


# -- the range estimator's validity rule: 0 < low <= high, whatever the price level ------------------------------------


def test_a_bar_priced_below_one_is_a_valid_range():
    """The frozen fixture has one (bar 420: 0.5 to 0.75); the closed form is ``ln(H / L) ** 2 / (4 ln 2)``."""
    want = np.log(0.75 / 0.5) ** 2 / (4.0 * np.log(2.0))
    np.testing.assert_allclose(parkinson_variance([0.75], [0.5]), [want], rtol=1e-12)
    np.testing.assert_allclose(parkinson_variance([1.0], [0.5]), [np.log(2.0) ** 2 / (4.0 * np.log(2.0))], rtol=1e-12)
    assert not np.isnan(WANT["high_low-1"][420]), "the frozen reference priced it too"


@pytest.mark.parametrize("high, low", [(1.0, 0.0), (1.0, -1.0), (0.5, 0.75), (np.nan, 1.0), (1.0, np.nan), (np.inf, 1.0), (1.0, np.inf)])
def test_a_bar_without_a_positive_low_below_its_high_is_undefined(high, low):
    assert np.isnan(parkinson_variance([high], [low])).all()


def test_a_flat_bar_has_zero_range_variance():
    assert list(parkinson_variance([5.0, 0.25], [5.0, 0.25])) == [0.0, 0.0]


# -- the array helpers take a column vector as readily as a flat series -----------------------------------------


def test_the_helpers_flatten_a_column_vector():
    flat = np.array([0, 60_000, 180_000, 240_000])
    np.testing.assert_array_equal(contiguous_mask(flat.reshape(-1, 1), BAR), contiguous_mask(flat, BAR))
    squared = np.array([1e-4, 4e-4, 1e-4, 9e-4])
    np.testing.assert_allclose(ewma_variance(squared.reshape(-1, 1), 1, 2), ewma_variance(squared, 1, 2), equal_nan=True)
    assert ewma_variance(squared.reshape(-1, 1), 1, 2).shape == (4,)


def test_one_bar_has_no_predecessor_and_two_adjacent_bars_are_contiguous():
    assert list(contiguous_mask([5], BAR)) == [False]
    assert list(contiguous_mask([5, 5 + BAR], BAR)) == [False, True]
