"""Pins of the core ``option_exposure`` arithmetic to the child code it duplicates (ADR-0310).

The child's ``WeeklyOptionPortfolioStudy._fit_betas`` and
``CondorQuoteBacktest._benchmark`` are pinned MIO / ADR-0193 code and stay
untouched; these tests require the graduated core arithmetic to give the same
numbers. They live here because a core test must not import a child.
"""

import random
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from dskit.pipeline.option_exposure import StockEquivalent, fit_betas  # noqa: E402

from index_options.ledger_studies import WeeklyOptionPortfolioStudy  # noqa: E402
from index_options.nodes import CondorQuoteBacktest, _EntryFacts  # noqa: E402


def _returns(names, length, seed):
    rng = random.Random(seed)
    return {s: np.array([rng.gauss(0, 0.02) for _ in range(length)]) for s in names}


@pytest.mark.parametrize("count,min_pairs", [(40, 20), (10, 20), (0, 1)])
def test_fit_betas_equals_the_shock_gates_fit(count, min_pairs):
    names = ["AAA", "BBB", "CCC", "DDD"]
    returns = _returns(names, max(count, 1), seed=7)
    study = SimpleNamespace(params={"correlation_min_pairs": min_pairs})
    old = WeeklyOptionPortfolioStudy._fit_betas(study, returns, names, count)
    new = fit_betas({s: list(map(float, r)) for s, r in returns.items()}, names, count, min_pairs)
    assert set(new) == set(old)
    for name in names:
        assert new[name] == pytest.approx(old[name], rel=1e-12, abs=1e-12)


def test_fit_betas_flat_basket_and_no_names_match_the_gate():
    flat = {"A": np.full(5, 0.01), "B": np.full(5, 0.01)}
    study = SimpleNamespace(params={"correlation_min_pairs": 2})
    assert fit_betas({k: list(v) for k, v in flat.items()}, ["A", "B"], 5, 2) == \
        WeeklyOptionPortfolioStudy._fit_betas(study, flat, ["A", "B"], 5)
    assert fit_betas({}, [], 5, 2) == WeeklyOptionPortfolioStudy._fit_betas(study, {}, [], 5)


def test_stock_equivalent_equals_the_condor_delta_benchmark():
    """ADR-0193 fixture: F = entry close, rate 0, equal dividends, same delta."""
    from dskit.pipeline.option_pricing import black76_delta

    legs = [("put", 1), ("put", -1), ("call", -1), ("call", 1)]
    strikes, ivs = [90.0, 95.0, 105.0, 110.0], [0.30, 0.27, 0.24, 0.25]
    entry, settle, multiplier, dte = 100.0, 94.0, 100, 30
    rows = [{"iv": iv} for iv in ivs]
    dividends = [{"date": "2024-03-01", "close": 100.0, "dividend_amount": 0.0},
                 {"date": "2024-03-05", "close": 99.0, "dividend_amount": 0.4},
                 {"date": "2024-03-31", "close": 94.0, "dividend_amount": 0.0}]
    facts = _EntryFacts("XYZ", "2024-03-01", "2024-03-31", dte, entry, settle, {}, None)
    stub = SimpleNamespace(params={"multiplier": multiplier},
                           _charge_window=lambda *args: dividends)
    old_delta, old_equity = CondorQuoteBacktest._benchmark(stub, rows, legs, strikes, facts)

    years = dte / 365
    delta_sh = multiplier * sum(sign * black76_delta(right, entry, k, iv, years)
                                for (right, sign), k, iv in zip(legs, strikes, ivs))
    stock, residual = StockEquivalent().split(12.5, delta_sh, entry, settle,
                                              dividends_per_share=0.4)
    assert delta_sh == pytest.approx(old_delta * multiplier, rel=1e-12)
    assert stock == pytest.approx(old_equity, rel=1e-12)
    assert residual == pytest.approx(12.5 - old_equity, rel=1e-12)


def _node():
    from dskit.pipeline.option_exposure import BookGreeksReport

    class Mem(BookGreeksReport):
        def _load_books(self, raw):
            return raw

        def _load_quotes(self, raw):
            return raw

        def _cell_key(self, book, quotes):
            return {"symbol": book["symbol"]}

    return Mem("pin", {
        "rate": 0.0, "multiplier": 100, "min_years": 0.001, "vol_floor": 0.01, "parity_strikes": 3,
        "min_pairs": 2, "max_pair_spread_frac": 0.5, "cell_by": ["symbol"], "min_total_usd": 1.0,
        "dte_edges": [0, 7], "top_k": 1, "exposure_days": "fill", "beta_lookback": 20,
        "beta_min_pairs": 5, "hedge_fee_per_share": 0.0, "hedge_half_spread_bps": 0.0,
        "hedge_borrow_rate": 0.0})


def test_betas_with_a_late_start_name_equal_the_shock_gates_kelly_estimate():
    """A name without bars across the window is dropped from the basket (beta 1.0), as the gate does."""
    rng = random.Random(11)
    days = [f"2021-01-{d:02d}" for d in range(1, 29)]
    day = "2021-02-01"
    bars = {s: {d: 100.0 * (1 + 0.01 * rng.gauss(0, 1)) for d in days} for s in ("AAA", "BBB", "CCC")}
    bars["LATE"] = {d: 50.0 + i for i, d in enumerate(days[15:])}
    new, _, dropped = _node()._betas(bars, day)

    window = days[-21:]                              # lookback 20: 21 sessions, 20 returns
    symbols = sorted(bars)
    returns = {s: np.diff(np.log(np.array([bars[s].get(d, np.nan) for d in window], dtype=float)))
               for s in symbols}
    stub = SimpleNamespace(params={"symbols": symbols, "correlation_min_pairs": 5},
                           _returns=lambda *args: (returns, 20))
    stub._fit_betas = lambda *args: WeeklyOptionPortfolioStudy._fit_betas(stub, *args)
    old = WeeklyOptionPortfolioStudy._kelly_estimate(stub, day, 20)
    assert old["dropped"] == dropped == ["LATE"]
    for name in old["names"]:
        assert new[name] == pytest.approx(old["betas"][name], rel=1e-12)
    assert new["LATE"] == 1.0
