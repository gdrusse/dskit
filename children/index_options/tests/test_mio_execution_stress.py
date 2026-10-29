"""Focused fixed-selection stress controls."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from dskit.pipeline.libs.option_ledger import FundedOptionAccount
from index_options.ledger_studies import WeeklyOptionPortfolioStudy
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
        "decision_date": "2019-01-02",
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


def test_attempt_evidence_shows_half_depth_refusal_inputs():
    study, book = _case(size=21, quantity=11)
    scenario = {"price_fraction": 0.25, "half_size": True}
    evidence = stress._attempt_evidence(study, book, "2019-01-03", scenario)
    leg = evidence["legs"][0]
    assert (leg["original_side_price"], leg["proposed_fill_price"]) == (1.4, 1.5)
    assert (leg["original_depth"], leg["effective_depth"], leg["quantity"]) == (21, 10, 11)
    assert stress._scenario_fill(study, book, "2019-01-03", {}, scenario)[1] == "insufficient_fill_size"


def test_expiry_shock_changes_realized_cash_for_the_same_filled_call():
    study, book = _case()
    scenario = {"price_fraction": 0.25, "half_size": False}
    filled, reason = stress._scenario_fill(study, book, "2019-01-03", {}, scenario)
    assert reason is None
    study.params = {"bridge": {"solver": {"multiplier": 100}}}
    study._factors["ABC"]["2019-02-01"] = 1.0
    outcomes = []
    for expiry_spot in (110.0, 93.5):
        account = FundedOptionAccount(1000, 0, 14, "2019-01-02")
        account.advance("2019-01-03", {})
        assert account.admit("2019-01-03", [filled],
                             [{"members": ["ABC"], "upper": 1000}])
        study._bars = {"ABC": {"2019-02-01": expiry_spot}}
        settlement = WeeklyOptionPortfolioStudy._settlements(
            study, account, "2019-02-01")
        account.advance("2019-02-01", settlement)
        outcomes.append((account.realized, account.cash, account.closed[0]["pnl"]))
    assert outcomes[0] == pytest.approx((849.35, 1849.35, 849.35))
    assert outcomes[1] == pytest.approx((-150.65, 849.35, -150.65))


def test_quote_index_requires_external_pin(tmp_path):
    path = tmp_path / "changed.sqlite"
    path.write_bytes(b"mutated")
    with pytest.raises(ValueError, match="original quote-index identity drift"):
        stress._index_view(path, "ABC", "0" * 64)


def test_zero_effective_size_refuses_before_invalid_sale_price():
    study, book = _case(sign=-1, bid=0.05, ask=0.5, size=1)
    filled, reason = stress._scenario_fill(
        study, book, "2019-01-03", {},
        {"price_fraction": 0.25, "half_size": True})
    assert filled is None
    assert reason == "insufficient_fill_size"


def test_changed_expiry_profit_changes_later_capacity_with_deposits():
    study, book = _case()
    filled, _ = stress._scenario_fill(
        study, book, "2019-01-03", {},
        {"price_fraction": 0.25, "half_size": False})
    study.params = {"bridge": {"solver": {"multiplier": 100}}}
    study._factors["ABC"]["2019-02-01"] = 1.0
    later = {"id": "later", "symbol": "ABC", "expiry": "2019-02-22",
             "reserve": 700.0, "credit": -700.0}
    eligibility = []
    for expiry_spot in (110.0, 93.5):
        account = FundedOptionAccount(1000, 200, 14, "2019-01-02")
        account.advance("2019-01-03", {})
        assert account.admit("2019-01-03", [filled],
                             [{"members": ["ABC"], "upper": 1000}])
        study._bars = {"ABC": {"2019-02-01": expiry_spot}}
        account.advance("2019-02-01", WeeklyOptionPortfolioStudy._settlements(
            study, account, "2019-02-01"))
        assert account.contributions == 1400
        cash_before = account.cash
        admitted = account.admit(
            "2019-02-01", [later],
            [{"members": ["ABC"], "upper": account.funding * 0.5}])
        eligibility.append(admitted)
        if not admitted:
            assert account.cash == cash_before
            assert not account.open
    assert eligibility == [True, False]


def test_shared_cap_refuses_batch_atomically_and_reserve_breach_is_explicit():
    account = FundedOptionAccount(1000, 0, 14, "2019-01-02")
    account.advance("2019-01-03", {})
    books = [{"id": key, "symbol": "ABC", "expiry": "2019-02-01",
              "reserve": 600.0, "credit": -600.0} for key in ("a", "b")]
    assert not account.admit(
        "2019-01-03", books, [{"members": ["ABC"], "upper": 1000}])
    assert account.cash == 1000
    assert not account.open
    short = {"id": "short", "symbol": "ABC", "expiry": "2019-02-01",
             "reserve": 100.0, "credit": 100.0}
    assert account.admit(
        "2019-01-03", [short], [{"members": ["ABC"], "upper": 1000}])
    with pytest.raises(ValueError, match="within reserved losses"):
        account.advance("2019-02-01", {"short": -150.0})
