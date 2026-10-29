"""Focused fixed-selection stress controls."""

from copy import deepcopy
from types import SimpleNamespace

from index_options import mio_execution_stress as stress


def test_adverse_side_prices_and_half_capacity():
    """A buy worsens above ask, a sale below bid, and odd depth floors."""
    assert stress.adverse_side(1.0, 1.4, 1) == 1.5
    assert stress.adverse_side(1.0, 1.4, -1) == 0.9
    assert stress.effective_size(5, True) == 2
    assert stress.effective_size(5, False) == 5


def _case(sign=1, bid=1.0, ask=1.4, size=5, quantity=1):
    book = {
        "id": "2019-01-02:ABC:2019-02-01", "symbol": "ABC",
        "expiry": "2019-02-01", "factor": 1.0, "contracts": quantity,
        "credit": -100.0,
        "legs": [{"right": "call", "strike": 100.0, "sign": sign,
                  "quantity": quantity, "price": 1.2, "capacity": size}],
    }
    row = {"bid": bid, "ask": ask, "bid_size": size,
           "ask_size": size, "basis_factor": 1.0}
    node = SimpleNamespace(
        params={"solver": {"multiplier": 100, "fee_per_contract_usd": 0.65},
                "stress_spread_fraction": 0.25},
        _usable_quote=lambda row, factor: (row["bid"], row["ask"]))
    study = SimpleNamespace(
        _nodes={"ABC": node}, _factors={"ABC": {"2019-01-03": 1.0}},
        _quotes={"ABC": {("2019-01-03", "2019-02-01", "call", 100.0): row}},
        _charge=lambda method: {}, _floor=lambda reserve, charge, legs, mult: reserve)
    return study, book


def test_changed_fill_price_recomputes_credit_without_mutating_order():
    study, book = _case()
    original = deepcopy(book)
    scenario = {"price_fraction": 0.25, "half_size": False}
    filled, reason = stress._scenario_fill(study, book, "2019-01-03", {}, scenario)
    assert reason is None
    assert filled["legs"][0]["fill_price"] == 1.5
    assert filled["fees"] == 0.65
    assert filled["credit"] == -150.65
    assert book == original


def test_half_size_refuses_whole_order():
    study, book = _case(size=5, quantity=3)
    filled, reason = stress._scenario_fill(
        study, book, "2019-01-03", {},
        {"price_fraction": 0, "half_size": True})
    assert filled is None
    assert reason == "insufficient_fill_size"


def test_nonpositive_stressed_sale_refuses_whole_order():
    study, book = _case(sign=-1, bid=0.05, ask=0.5)
    filled, reason = stress._scenario_fill(
        study, book, "2019-01-03", {},
        {"price_fraction": 0.25, "half_size": False})
    assert filled is None
    assert reason == "invalid_stressed_fill_price"
