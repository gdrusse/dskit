"""Focused fixed-selection stress controls."""

from copy import deepcopy
import json
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
        "credit": -100.0, "reserve": 100.0,
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


def test_nonfinite_proposed_price_is_a_pinned_invalid_refusal():
    study, book = _case(bid=1.1e308, ask=1.7e308)
    scenario = {"price_fraction": 0.25, "half_size": False}
    evidence = stress._attempt_evidence(study, book, "2019-01-03", scenario)
    assert evidence["legs"][0]["proposed_fill_price"] is None
    assert evidence["legs"][0]["proposed_price_valid"] is False
    assert stress._scenario_fill(study, book, "2019-01-03", {}, scenario)[1] == "invalid_stressed_fill_price"
    json.dumps(evidence, allow_nan=False)


def test_failed_session_preserves_prior_daily_account_and_failure_state():
    replay = stress.FrozenMIOReplay.__new__(stress.FrozenMIOReplay)
    replay.calendar = ["2019-01-02", "2019-01-03"]
    replay.params = {
        "initial": 1000, "deposit": 0, "deposit_every_days": 14,
        "bridge": {"end_before": "2020-01-01"}}
    def settlements(account, day):
        if day == "2019-01-03":
            raise ValueError("synthetic reserve breach")
        return {}
    replay.study = lambda scenario: SimpleNamespace(_settlements=settlements)
    packet = {"result": {"method": {"id": "synthetic"}, "decisions": []}}
    with pytest.raises(ValueError, match="synthetic reserve breach"):
        replay.run_method(packet, {"id": "expiry_down_15"})
    progress = replay.last_progress
    assert progress["date"] == "2019-01-03"
    assert len(progress["daily"]) == 1
    assert progress["daily"][0]["cash"] == 1000
    assert progress["account"].cash == 1000


def test_partial_disposition_keeps_closed_trade_and_cap_refusal_details():
    study, book = _case()
    filled, _ = stress._scenario_fill(
        study, book, "2019-01-03", {},
        {"price_fraction": 0.25, "half_size": False})
    decision = {"date": "2019-01-02", "books": [book],
                "candidate_books": 3, "candidate_contracts": 10}
    attempt = stress._attempt_evidence(
        study, book, "2019-01-03",
        {"price_fraction": 0.25, "half_size": False})
    daily = [{"date": "2019-01-03", "cash": 849.35,
              "attempts": [attempt], "expiries": []}]
    closed = {**filled, "pnl": 849.35, "close_date": "2019-02-01"}
    incomplete = {
        "failure": {"date": "2019-02-01", "cash": 1849.35,
                    "attempts": [], "reason": "later invariant"},
        "closed_trades": [closed], "open_books": [], "rejected": []}
    rows = stress._dispositions(
        "limited36", "expiry_up_15", "synthetic", None, daily,
        decisions=[decision], incomplete=incomplete)
    assert len(rows) == 1
    assert rows[0]["status"] == "filled"
    assert rows[0]["realized_pnl"] == 849.35
    assert rows[0]["cash_after_expiry_day"] == 1849.35

    refusal = {**filled, "reason": "repriced_shared_cap", "status": "unfilled"}
    complete = {"decisions": [decision], "trades": [], "rejected": [refusal]}
    rows = stress._dispositions(
        "limited36", "adverse_price", "synthetic", complete, daily)
    assert rows[0]["status"] == "refused"
    assert rows[0]["preflight_fees"] == 0.65
    assert rows[0]["preflight_reserve"] == filled["reserve"]
    assert rows[0]["fill_fees"] is None


def test_failed_settlement_reports_book_level_reserve_breach():
    account = SimpleNamespace(
        open={"short": {"reserve": 100.0}}, cash=1100.0,
        funding=1000.0, contributions=1000.0, realized=0.0)
    progress = {"account": account, "date": "2019-02-01",
                "attempted_ids": [], "attempts": [],
                "settlements": {"short": -150.0}}
    failure = stress._failure_state(progress, "settlements exceed reserved losses")
    assert failure["reserve_breach"] is True
    assert failure["aggregate_reserve_breach"] is False
    assert failure["settlement_reserve_breaches"] == [{
        "id": "short", "attempted_pnl": -150.0,
        "reserved_loss": 100.0, "excess_loss": 50.0}]


@pytest.mark.parametrize('sign', [1, -1])
def test_single_leg_available_size_recomputes_executed_contracts(sign):
    study, book = _case(sign=sign, size=2, quantity=3)
    selected = deepcopy(book)
    scenario = {'price_fraction': 0, 'half_size': False,
                'single_leg_fill': True}
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {}, scenario)
    assert reason is None
    assert book == selected
    assert filled['legs'][0]['quantity'] == 2
    assert filled['contracts'] == 2
    assert filled['fees'] == 1.30
    assert filled['credit'] == pytest.approx(
        -281.30 if sign == 1 else 198.70)
    evidence = stress._attempt_evidence(
        study, book, '2019-01-03', scenario)
    assert evidence['legs'][0]['quantity'] == 3
    assert evidence['legs'][0]['effective_depth'] == 2


def test_single_leg_zero_size_refuses_and_multi_leg_stays_atomic():
    study, book = _case(size=0, quantity=3)
    scenario = {'price_fraction': 0, 'half_size': False,
                'single_leg_fill': True}
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 scenario)[1] == 'insufficient_fill_size'
    study, book = _case(size=2, quantity=3)
    book['legs'].append({**book['legs'][0], 'strike': 101.0})
    study._fill = lambda book, day, charge: WeeklyOptionPortfolioStudy._fill(
        study, book, day, charge)
    study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 101.0)] = (
        study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 100.0)])
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 scenario)[1] == 'insufficient_fill_size'


def test_single_leg_full_depth_matches_legacy_fill():
    study, book = _case(size=4, quantity=3)
    study._fill = lambda book, day, charge: WeeklyOptionPortfolioStudy._fill(
        study, book, day, charge)
    legacy = {'price_fraction': 0, 'half_size': False}
    partial = {**legacy, 'single_leg_fill': True}
    assert stress._scenario_fill(study, book, '2019-01-03', {}, partial) == (
        stress._scenario_fill(study, book, '2019-01-03', {}, legacy))


def test_one_nonzero_leg_ignores_zero_quantity_placeholder():
    study, book = _case(size=2, quantity=3)
    book['legs'].append({**book['legs'][0], 'strike': 101.0,
                         'quantity': 0})
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {},
        {'price_fraction': 0, 'half_size': False,
         'single_leg_fill': True})
    assert reason is None
    assert filled['contracts'] == 2
    assert len(filled['legs']) == 1


def test_single_leg_half_size_zero_refuses_and_partial_reserve_changes_cap():
    study, book = _case(size=1, quantity=3)
    scenario = {'price_fraction': 0, 'half_size': True,
                'single_leg_fill': True}
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 scenario)[1] == 'insufficient_fill_size'

    study, book = _case(size=2, quantity=3)
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {},
        {'price_fraction': 0, 'half_size': False,
         'single_leg_fill': True})
    assert reason is None
    account = FundedOptionAccount(300, 0, 14, '2019-01-02')
    account.advance('2019-01-03', {})
    assert account.admit('2019-01-03', [filled],
                         [{'members': ['ABC'], 'upper': 300}])
    assert account.cash == pytest.approx(18.7)
    assert account.open[book['id']]['reserve'] == pytest.approx(281.3)
    study.params = {'bridge': {'solver': {'multiplier': 100}}}
    study._factors['ABC']['2019-02-01'] = 1.0
    study._bars = {'ABC': {'2019-02-01': 110.0}}
    account.advance('2019-02-01', WeeklyOptionPortfolioStudy._settlements(
        study, account, '2019-02-01'))
    assert account.closed[0]['pnl'] == pytest.approx(1718.7)
    assert account.cash == pytest.approx(2018.7)

    full_study, full_book = _case(size=4, quantity=3)
    full_study._fill = lambda book, day, charge: WeeklyOptionPortfolioStudy._fill(
        full_study, book, day, charge)
    full, _ = stress._scenario_fill(
        full_study, full_book, '2019-01-03', {},
        {'price_fraction': 0, 'half_size': False})
    limited = FundedOptionAccount(300, 0, 14, '2019-01-02')
    limited.advance('2019-01-03', {})
    assert not limited.admit('2019-01-03', [full],
                             [{'members': ['ABC'], 'upper': 300}])


PROPORTIONAL = {'price_fraction': 0, 'half_size': False,
                'proportional_fill': True}


def _two_leg(quantities, depths, *, legacy=True):
    """A buy/sell call pair with the given quantities and side depths."""
    study, book = _case(quantity=quantities[0], size=depths[0])
    book['legs'].append({**book['legs'][0], 'strike': 105.0, 'sign': -1,
                         'quantity': quantities[1]})
    book['contracts'] = sum(quantities)
    study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 105.0)] = {
        'bid': 0.5, 'ask': 0.7, 'bid_size': depths[1], 'ask_size': 0,
        'basis_factor': 1.0}
    if legacy:
        study._fill = lambda book, day, charge: WeeklyOptionPortfolioStudy._fill(
            study, book, day, charge)
    return study, book


def test_proportional_fill_keeps_ratio_at_largest_depth_multiple():
    study, book = _two_leg((6, 4), (4, 3))
    selected = deepcopy(book)
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {}, PROPORTIONAL)
    assert reason is None
    assert book == selected
    assert [leg['quantity'] for leg in filled['legs']] == [3, 2]
    assert [leg['selected_quantity'] for leg in filled['legs']] == [6, 4]
    assert filled['contracts'] == 5
    assert filled['fees'] == pytest.approx(3.25)
    assert filled['credit'] == pytest.approx(-3 * 140 + 2 * 50 - 3.25)
    assert filled['reserve'] == pytest.approx(max(0.0, filled['max_loss'],
                                                  -filled['credit']))
    legacy, _ = stress._scenario_fill(
        *_two_leg((6, 4), (6, 4)), '2019-01-03', {}, PROPORTIONAL)
    for key in ('premium', 'fees', 'credit', 'max_loss', 'reserve'):
        assert filled[key] * 2 == pytest.approx(legacy[key])


def test_proportional_primitive_ratio_short_of_one_unit_refuses_whole():
    study, book = _two_leg((3, 2), (2, 2))
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 PROPORTIONAL) == (None, 'insufficient_fill_size')


def test_proportional_never_exceeds_selection_and_full_depth_is_legacy():
    study, book = _two_leg((2, 2), (100, 100))
    legacy = {'price_fraction': 0, 'half_size': False}
    assert stress._scenario_fill(study, book, '2019-01-03', {}, PROPORTIONAL) == (
        stress._scenario_fill(study, book, '2019-01-03', {}, legacy))
    study, book = _two_leg((6, 4), (4, 3))
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 legacy) == (None, 'insufficient_fill_size')


def test_proportional_single_leg_is_available_size_and_zero_refuses():
    study, book = _two_leg((11, 0), (7, 0))
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {}, PROPORTIONAL)
    assert reason is None
    assert [leg['quantity'] for leg in filled['legs']] == [7, 0]
    assert filled['contracts'] == 7
    study, book = _two_leg((11, 0), (0, 0))
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 PROPORTIONAL)[1] == 'insufficient_fill_size'


def test_proportional_placeholder_leg_keeps_quote_rule():
    study, book = _two_leg((6, 0), (4, 0))
    del study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 105.0)]
    study._nodes['ABC']._usable_quote = (
        lambda row, factor: None if row is None else (row['bid'], row['ask']))
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 PROPORTIONAL)[1] == 'missing_fill_quote'


def test_proportional_downsized_batch_still_faces_atomic_cap():
    study, book = _two_leg((6, 4), (4, 3))
    other = deepcopy(book)
    other['id'] = 'other'
    first, _ = stress._scenario_fill(study, book, '2019-01-03', {}, PROPORTIONAL)
    second, _ = stress._scenario_fill(study, other, '2019-01-03', {}, PROPORTIONAL)
    account = FundedOptionAccount(1000, 0, 14, '2019-01-02')
    account.advance('2019-01-03', {})
    upper = first['reserve'] * 1.5
    assert not account.admit('2019-01-03', [first, second],
                             [{'members': ['ABC'], 'upper': upper}])
    assert account.open == {}


def test_approved_scenarios_extend_without_changing_earlier_schemas():
    v1 = stress._approved_scenarios('mio-execution-stress/v1')
    v2 = stress._approved_scenarios('mio-execution-stress/v2')
    v3 = stress._approved_scenarios('mio-execution-stress/v3')
    assert len(v1) == 7 and v2[:7] == v1 and v3[:9] == v2
    assert v3[9:] == [{'id': 'proportional_available_size',
                       'price_fraction': 0, 'half_size': False,
                       'expiry_multiplier': 1, 'proportional_fill': True}]
    with pytest.raises(ValueError):
        stress._approved_scenarios('mio-execution-stress/v4')


def test_v3_document_is_the_approved_contract_with_a_distinct_root():
    from pathlib import Path
    configs = Path(__file__).resolve().parents[1] / 'configs'
    docs = {name: json.loads((configs / name).read_text()) for name in (
        'mio-2019-execution-stress.json',
        'mio-2019-execution-stress-single-leg.json',
        'mio-2019-execution-stress-multileg.json')}
    v3 = docs['mio-2019-execution-stress-multileg.json']
    assert v3['scenarios'] == stress._approved_scenarios(v3['schema'])
    assert v3['schema'] == 'mio-execution-stress/v3'
    assert len({doc['output_root'] for doc in docs.values()}) == 3
    v2 = docs['mio-2019-execution-stress-single-leg.json']
    assert {k: v for k, v in v3.items() if k not in ('schema', 'scenarios', 'output_root')} == {
        k: v for k, v in v2.items() if k not in ('schema', 'scenarios', 'output_root')}
