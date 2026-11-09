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
