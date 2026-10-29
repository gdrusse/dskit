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
        params={}, _nodes={"ABC": node}, _factors={"ABC": {"2019-01-03": 1.0}},
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
    assert stress._approved_scenarios('mio-execution-stress/v4') == v1
    with pytest.raises(ValueError):
        stress._approved_scenarios('mio-execution-stress/v5')


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


@pytest.mark.parametrize('depths, executed', [
    ((9, 2), [3, 2]), ((5, 4), [3, 2]), ((6, 2), [3, 2]), ((9, 4), [6, 4])])
def test_proportional_multiple_is_bound_by_the_shallowest_leg(depths, executed):
    study, book = _two_leg((6, 4), depths)
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {}, PROPORTIONAL)
    assert reason is None
    assert [leg['quantity'] for leg in filled['legs']] == executed


@pytest.mark.parametrize('depths, reason', [
    ((2, 2), 'insufficient_fill_size'), ((3, 1), 'insufficient_fill_size')])
def test_proportional_any_leg_short_of_one_unit_refuses(depths, reason):
    study, book = _two_leg((3, 2), depths)
    assert stress._scenario_fill(study, book, '2019-01-03', {},
                                 PROPORTIONAL) == (None, reason)


def test_proportional_three_legs_with_common_divisor():
    study, book = _two_leg((9, 6), (7, 6))
    book['legs'].append({**book['legs'][0], 'strike': 110.0, 'quantity': 3})
    book['contracts'] = 18
    study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 110.0)] = {
        'bid': 0.1, 'ask': 0.2, 'bid_size': 0, 'ask_size': 4,
        'basis_factor': 1.0}
    filled, reason = stress._scenario_fill(
        study, book, '2019-01-03', {}, PROPORTIONAL)
    assert reason is None
    assert [leg['quantity'] for leg in filled['legs']] == [6, 4, 2]
    assert filled['contracts'] == 12


def test_proportional_downsized_reserve_admits_where_full_size_breaches():
    study, book = _two_leg((6, 4), (4, 3))
    downsized, _ = stress._scenario_fill(study, book, '2019-01-03', {}, PROPORTIONAL)
    full, _ = stress._scenario_fill(
        *_two_leg((6, 4), (6, 4)), '2019-01-03', {}, PROPORTIONAL)
    upper = downsized['reserve'] * 1.5
    assert full['reserve'] > upper
    for books, admitted in (([full], False), ([downsized], True)):
        account = FundedOptionAccount(1000, 0, 14, '2019-01-02')
        account.advance('2019-01-03', {})
        assert account.admit('2019-01-03', books,
                             [{'members': ['ABC'], 'upper': upper}]) is admitted


def test_proportional_refuses_combination_with_other_shocks():
    study, book = _two_leg((6, 4), (4, 3))
    with pytest.raises(ValueError, match='zero-shock'):
        stress._scenario_fill(study, book, '2019-01-03', {},
                              {**PROPORTIONAL, 'half_size': True})


def _daybook_case():
    """Two filled books, one downsized, and complete metrics."""
    def leg(quantity, selected):
        return {'side': 'buy', 'right': 'call', 'strike': 100.0, 'price': 1.0,
                'quantity': quantity, 'selected_quantity': selected}
    fills = [{'id': 'a', 'legs': [leg(3, 6), leg(2, 4)], 'fees': 1.0, 'credit': -1.0},
             {'id': 'b', 'legs': [leg(5, 5)], 'fees': 1.0, 'credit': -1.0}]
    daily = [{'date': '2019-01-03', 'candidate_books': 3, 'possible_option_contracts': 9,
              'selected_ids': ['a', 'b', 'c'], 'fills': fills, 'expiries': [],
              'refusals': [{'id': 'c', 'reason': 'insufficient_fill_size'}],
              'expiry_losses': 0.0, 'cash': 10.0, 'candidate_refusals': []}]
    metrics = {'status': 'complete', 'selected': 3, 'filled': 2, 'refused': 1,
               'final_profit_usd': 1.0, 'max_drawdown': 0.0,
               'refusal_reasons': {'insufficient_fill_size': 1}}
    return metrics, daily


def test_legacy_daybook_keeps_v1_v2_wording():
    metrics, daily = _daybook_case()
    lines = stress._daybook('c', 's', 'm', metrics, daily).split('\n')
    assert lines[2:8] == [
        '2019 development diagnostic. Chosen orders are decided after the named',
        'close; fills appear on the next session. Expiry P&L is realized;',
        'marked daily change is interim equity change after deposits.',
        'Executed-leg quantities are shown as filled/selected.', '',
        'Selected 3; filled 2; refused 1; final profit $1.00; max drawdown 0.0%.']
    assert 'depth' not in '\n'.join(lines[:10])


def test_v3_daybook_headline_counts_books_contracts_and_downsized():
    metrics, daily = _daybook_case()
    lines = stress._daybook('c', 's', 'm', metrics, daily, None, 19).split('\n')
    assert lines[6:11] == [
        stress._PROXY_NOTE, '',
        'Books: selected 3; filled 2 (1 downsized); refused 1. '
        'Contracts: selected 19; filled 10.', '',
        'Selected 3; filled 2; refused 1; final profit $1.00; max drawdown 0.0%.']


def test_depth_proxy_is_v3_only_and_sums_selected_contracts():
    decisions = [{'books': [{'contracts': 10}, {'contracts': 4}]}, {'date': 'x'}]
    for schema in ('mio-execution-stress/v1', 'mio-execution-stress/v2'):
        assert stress._depth_proxy(schema, decisions) is None
    assert stress._depth_proxy('mio-execution-stress/v3', decisions) == 14


# ADR-0282 Amendment 1: the opt-in domain knob in the ordinary fill owner.
AVAILABLE = {'fill_sizing': 'available_size'}


def _sized(quantities, depths, params=AVAILABLE):
    study, book = _two_leg(quantities, depths)
    study.params = dict(params)
    return study, book


def _ordinary(study, book):
    return WeeklyOptionPortfolioStudy._fill(study, book, '2019-01-03', {})


def test_ordinary_fill_available_size_keeps_gcd_ratio():
    study, book = _sized((6, 4), (4, 3))
    selected = deepcopy(book)
    filled, reason = _ordinary(study, book)
    assert reason is None and book == selected
    assert [leg['quantity'] for leg in filled['legs']] == [3, 2]
    assert [leg['selected_quantity'] for leg in filled['legs']] == [6, 4]
    assert filled['contracts'] == 5
    assert filled['fees'] == pytest.approx(3.25)
    assert filled['credit'] == pytest.approx(-3 * 140 + 2 * 50 - 3.25)
    assert filled['reserve'] == pytest.approx(max(0.0, filled['max_loss'], -filled['credit']))
    frozen, _ = stress._scenario_fill(*_two_leg((6, 4), (4, 3)), '2019-01-03', {}, PROPORTIONAL)
    assert filled == frozen


@pytest.mark.parametrize('params', [{}, {'fill_sizing': 'all_or_none'}])
def test_ordinary_fill_default_stays_all_or_none(params):
    assert _ordinary(*_sized((6, 4), (4, 3), params)) == (None, 'insufficient_fill_size')
    full = _ordinary(*_sized((6, 4), (6, 4), params))
    assert full == _ordinary(*_sized((6, 4), (6, 4)))
    assert 'selected_quantity' not in full[0]['legs'][0]


def test_ordinary_fill_single_leg_clip_and_primitive_refusal():
    filled, reason = _ordinary(*_sized((11, 0), (7, 0)))
    assert reason is None and [leg['quantity'] for leg in filled['legs']] == [7, 0]
    assert filled['contracts'] == 7 and filled['fees'] == pytest.approx(7 * 0.65)
    assert _ordinary(*_sized((11, 0), (0, 0))) == (None, 'insufficient_fill_size')
    assert _ordinary(*_sized((3, 2), (2, 2))) == (None, 'insufficient_fill_size')


@pytest.mark.parametrize('case', ['half_size', 'adverse_price', 'joint_down_15'])
def test_available_size_domain_sizes_every_shocked_case(case):
    scenario = next(s for s in stress._approved_scenarios('mio-execution-stress/v1')
                    if s['id'] == case)
    depths = (8, 6) if scenario['half_size'] else (4, 3)
    study, book = _sized((6, 4), depths)
    selected = deepcopy(book)
    filled, reason = stress._scenario_fill(study, book, '2019-01-03', {}, scenario)
    assert reason is None and book == selected
    assert [leg['quantity'] for leg in filled['legs']] == [3, 2]
    assert [leg['selected_quantity'] for leg in filled['legs']] == [6, 4]
    assert filled['contracts'] == 5
    expected = [stress.adverse_side(1.0, 1.4, 1, scenario['price_fraction']),
                stress.adverse_side(0.5, 0.7, -1, scenario['price_fraction'])]
    assert [leg['fill_price'] for leg in filled['legs']] == expected
    assert stress._scenario_fill(*_two_leg((6, 4), depths), '2019-01-03', {},
                                 scenario) == (None, 'insufficient_fill_size')


@pytest.mark.parametrize('case', ['control', 'expiry_down_15', 'expiry_up_15'])
def test_available_size_unshocked_fill_is_the_ordinary_owner(case):
    scenario = next(s for s in stress._approved_scenarios('mio-execution-stress/v1')
                    if s['id'] == case)
    study, book = _sized((6, 4), (4, 3))
    filled = stress._scenario_fill(study, book, '2019-01-03', {}, scenario)
    assert filled[1] is None and filled[0]['contracts'] == 5
    assert filled == _ordinary(*_sized((6, 4), (4, 3)))


def test_available_size_shocked_refusal_precedence_per_leg():
    joint = {'price_fraction': 0.25, 'half_size': True, 'expiry_multiplier': 1}
    study, book = _sized((6, 4), (1, 6))
    assert stress._scenario_fill(study, book, '2019-01-03', {}, joint)[1] == (
        'insufficient_fill_size')
    study, book = _sized((6, 4), (8, 6))
    study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 105.0)]['bid'] = 0.04
    study._quotes['ABC'][('2019-01-03', '2019-02-01', 'call', 105.0)]['ask'] = 0.24
    assert stress._scenario_fill(study, book, '2019-01-03', {}, joint)[1] == (
        'invalid_stressed_fill_price')


V4 = 'mio-execution-stress/v4'


def test_available_size_requires_v4_and_v4_requires_available_size():
    for schema in ('mio-execution-stress/v1', 'mio-execution-stress/v2',
                   'mio-execution-stress/v3'):
        with pytest.raises(ValueError, match='v4'):
            stress.FrozenMIOReplay._sizing_schema(AVAILABLE, schema)
        stress.FrozenMIOReplay._sizing_schema({}, schema)
        stress.FrozenMIOReplay._sizing_schema({'fill_sizing': 'all_or_none'}, schema)
    stress.FrozenMIOReplay._sizing_schema(AVAILABLE, V4)
    for legacy in ({}, {'fill_sizing': 'all_or_none'}):
        with pytest.raises(ValueError, match='v4'):
            stress.FrozenMIOReplay._sizing_schema(legacy, V4)


def _cohort_document(tmp_path, schema, entry):
    (tmp_path / 'children/index_options/configs').mkdir(parents=True, exist_ok=True)
    return {'schema': schema, 'repository_root': str(tmp_path),
            'cohorts': {'limited36': entry}}


def test_v4_cohort_run_document_is_explicit_and_inside_configs(tmp_path):
    configs = tmp_path / 'children/index_options/configs'
    document = _cohort_document(tmp_path, V4, {'config': 'fresh-limited36.json'})
    assert stress.FrozenMIOReplay._run_document(document, 'limited36') == (
        configs / 'fresh-limited36.json')
    for entry in ({}, {'config': '../x.json'}, {'config': 'a/b.json'}, {'config': ''},
                  {'config': '..'}, {'config': 7}):
        with pytest.raises(ValueError, match='config'):
            stress.FrozenMIOReplay._run_document(
                _cohort_document(tmp_path, V4, entry), 'limited36')
    (tmp_path / 'outside.json').write_text('{}')
    (configs / 'link.json').symlink_to(tmp_path / 'outside.json')
    with pytest.raises(ValueError, match='config'):
        stress.FrozenMIOReplay._run_document(
            _cohort_document(tmp_path, V4, {'config': 'link.json'}), 'limited36')


@pytest.mark.parametrize('schema', ['mio-execution-stress/v1', 'mio-execution-stress/v2',
                                    'mio-execution-stress/v3'])
def test_legacy_cohort_run_document_keeps_its_default(tmp_path, schema):
    document = _cohort_document(tmp_path, schema, {})
    assert stress.FrozenMIOReplay._run_document(document, 'limited36') == (
        tmp_path / 'children/index_options/configs/mio-2019-limited36.json')
    with pytest.raises(ValueError, match='v4'):
        stress.FrozenMIOReplay._run_document(
            _cohort_document(tmp_path, schema, {'config': 'x.json'}), 'limited36')


def test_v4_keeps_v3_proxy_wording_and_contract_headline():
    decisions = [{'books': [{'contracts': 10}, {'contracts': 4}]}]
    assert stress._depth_proxy(V4, decisions) == 14
    metrics, daily = _daybook_case()
    selected = stress._depth_proxy(V4, [{'books': [{'contracts': 19}]}])
    lines = stress._daybook('c', 's', 'm', metrics, daily, None, selected).split('\n')
    assert lines[6:9] == [
        stress._PROXY_NOTE, '',
        'Books: selected 3; filled 2 (1 downsized); refused 1. '
        'Contracts: selected 19; filled 10.']


def test_one_sizing_owner_serves_ordinary_and_stressed_fills(monkeypatch):
    W = WeeklyOptionPortfolioStudy
    assert W._units([{'quantity': 6}, {'quantity': 4}]) == 2
    assert W._units([{'quantity': 11}, {'quantity': 0}]) == 11
    assert W._depth_multiple(4, 2, 7) == 3 and W._depth_multiple(4, 2, 1) is None
    assert W._depth_multiple(0, 2, 0) == 2
    assert W._depth_multiple(4, 0, 4) == 0 and W._depth_multiple(4, 0, 3) is None
    book = {'contracts': 10, 'legs': [{'quantity': 6}, {'quantity': 4}]}
    W._resize(book, 2, 1)
    assert book == {'contracts': 5, 'legs': [{'quantity': 3, 'selected_quantity': 6},
                                             {'quantity': 2, 'selected_quantity': 4}]}
    calls = []
    owner = W._depth_multiple
    monkeypatch.setattr(W, '_depth_multiple', staticmethod(
        lambda *args: calls.append(args) or owner(*args)))
    joint = {'price_fraction': 0.25, 'half_size': True, 'expiry_multiplier': 1}
    stress._scenario_fill(*_sized((6, 4), (8, 6)), '2019-01-03', {}, joint)
    assert calls[:2] == [(6, 2, 4), (4, 2, 3)]
    calls.clear()
    _ordinary(*_sized((6, 4), (4, 3)))
    assert calls == [(6, 2, 4), (4, 2, 3)]


# Review Major (test quality): construct FrozenMIOReplay and execute on a tmp tree.
def _weekdays(n=252):
    from datetime import date, timedelta
    day, days = date(2019, 1, 2), []
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


CALENDAR = _weekdays()
EXPIRY = CALENDAR[20]


class _Quotes(dict):
    """An in-memory stand-in for the pinned SQLite quote view."""

    def close(self, verify=False):
        pass


def _quote_rows():
    rows = _Quotes()
    for day in CALENDAR[1:21]:
        for strike, bid, ask, bid_size, ask_size in ((100.0, 4.0, 4.2, 10, 3),
                                                     (105.0, 2.0, 2.1, 10, 10)):
            rows[day, EXPIRY, 'call', strike] = {
                'bid': bid, 'ask': ask, 'bid_size': bid_size,
                'ask_size': ask_size, 'basis_factor': 1.0}
    return rows


def _domain(fill_sizing):
    from pathlib import Path
    real = json.loads((Path(__file__).resolve().parents[1] / 'configs/mio-2019-limited36.json')
                      .read_text())['pipeline']['plan']['params']['study']['domain']
    params = {**real['params'], 'symbols': ['ABC'], 'methods': [
        {'id': 'm' + str(i), 'rho': 0, 'portfolio_fraction': 1, 'name_fraction': 1,
         'component_fraction': None, **({'reserve_per_contract': 200.0} if i == 1 else {})}
        for i in range(4)]}
    if fill_sizing is not None:
        params['fill_sizing'] = fill_sizing
    return {'params': params, 'pairs': [{'id': 'p', 'symbol': 'ABC', 'dte_min': 1, 'dte_max': 60}],
            'sources': {'bars': {}, 'chain': {}}}


def _draft_packets(domain):
    legs = [{'right': 'call', 'strike': 100.0, 'sign': 1, 'quantity': 4, 'price': 4.2},
            {'right': 'call', 'strike': 105.0, 'sign': -1, 'quantity': 4, 'price': 2.0}]
    book = {'id': CALENDAR[0] + ':ABC:' + EXPIRY, 'symbol': 'ABC', 'expiry': EXPIRY,
            'decision_date': CALENDAR[0], 'factor': 1.0, 'contracts': 8, 'legs': legs,
            'credit': -885.2, 'reserve': 885.2, 'max_loss': 885.2}
    decision = {'date': CALENDAR[0], 'status': 'selected', 'candidate_books': 1,
                'candidate_contracts': 2, 'refusals': [], 'components': {'groups': []},
                'books': [book]}
    return [{'id': m['id'], 'status': 'completed',
             'result': {'method': m, 'decisions': [decision]}}
            for m in domain['params']['methods']]


def _csvs(results, partial_csv_quantity=None):
    """Write the four control extracts the way the original packet does."""
    def text(header, rows):
        import io
        stream = io.StringIO()
        writer = __import__('csv').DictWriter(stream, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: '' if v is None else v for k, v in row.items()})
        return stream.getvalue()

    trades, rejected, daily, orders = [], [], [], []
    for packet in results:
        result, method = packet['result'], packet['id']
        for t in result.get('trades', []):
            trades.append({'method': method, 'id': t['id'], 'contracts': t['contracts'],
                           'pnl': repr(t['pnl'])})
        for r in result.get('rejected', []):
            rejected.append({'method': method, 'id': r['id'], 'reason': r['reason']})
        for p in result.get('curve', []):
            daily.append({'method': method, 'date': p['date'], 'equity': repr(p['equity'])})
        filled = {t['id']: t for t in result.get('trades', [])}
        for decision in result['decisions']:
            for book in decision['books']:
                trade = filled.get(book['id'])
                for leg, actual in zip(book['legs'], trade['legs'] if trade else book['legs']):
                    orders.append({
                        'method': method, 'date': decision['date'], 'group_id': book['id'],
                        'right': leg['right'], 'strike': leg['strike'],
                        'side': 'buy' if leg['sign'] > 0 else 'sell',
                        'selected_by_optimizer': 1, 'selected_quantity': leg['quantity'],
                        'filled_quantity': (partial_csv_quantity or actual['quantity'])
                        if trade else 0,
                        'fill_price': repr(actual['fill_price']) if trade else None,
                        'fill_date': trade['fill_date'] if trade else None,
                        'rejection_reason': None})
    return {'trades.csv': text(['method', 'id', 'contracts', 'pnl'], trades),
            'rejected-orders.csv': text(['method', 'id', 'reason'], rejected),
            'daily-account.csv': text(['method', 'date', 'equity'], daily),
            'orders-and-fills.csv': text(
                ['method', 'date', 'group_id', 'right', 'strike', 'side',
                 'selected_by_optimizer', 'selected_quantity', 'filled_quantity',
                 'fill_price', 'fill_date', 'rejection_reason'], orders)}


def _tree(tmp_path, *, schema, fill_sizing='available_size', config='fresh-run.json',
          pinned=True, wrong_sha=False, results=None, partial_csv_quantity=None):
    """A repository root, original packets, pins and a stress document."""
    import subprocess
    sha = stress._sha
    repo, evidence = tmp_path / 'repo', tmp_path / 'evidence'
    configs = repo / 'children/index_options/configs'
    configs.mkdir(parents=True, exist_ok=True)
    domain = _domain(fill_sizing)
    name = config if schema == V4 else None
    config_path = configs / (name or 'mio-2019-limited36.json')
    config_path.write_text(json.dumps(
        {'pipeline': {'plan': {'params': {'study': {'domain': domain}}}}}))
    if not (repo / '.git').exists():
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                    '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                    'commit', '-q', '--allow-empty', '-m', 'fixture'], check=True)
    results = results if results is not None else _draft_packets(domain)
    index = {}
    for cohort in ('limited36', 'broad212'):
        folder = evidence / cohort
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'result.json').write_text(json.dumps(
            {'complete': True, 'results': results, 'audit': {'calendar': CALENDAR}}))
        for csv_name, body in _csvs(results, partial_csv_quantity).items():
            (folder / csv_name).write_text(body)
            index[cohort + '/' + csv_name] = {'sha256': sha(folder / csv_name)}
    (evidence / 'artifact-index.json').write_text(json.dumps({'csv_files': index}))
    pins = {'evidence': {'artifact-index.json': {
                'path': str(evidence / 'artifact-index.json'),
                'sha256': sha(evidence / 'artifact-index.json')}},
            'original_configs': {} if not pinned else {config_path.name: {
                'sha256': '0' * 64 if wrong_sha else sha(config_path)}},
            'runs': {c: {'run_dir': str(evidence), 'run_hash': 'r', 'sha256': 's',
                         'document_hash': 'd'} for c in ('limited36', 'broad212')},
            'sources': {}}
    (evidence / 'pins.json').write_text(json.dumps(pins))
    cohort_entry = {'quote_index_sha256': {'ABC': 'q'}}
    if name:
        cohort_entry['config'] = name
    document = {
        'schema': schema, 'repository_root': str(repo), 'original_root': str(evidence),
        'results_pins': str(evidence / 'pins.json'),
        'results_pins_sha256': sha(evidence / 'pins.json'),
        'cohorts': {c: {**cohort_entry, 'result_sha256': sha(evidence / c / 'result.json')}
                    for c in ('limited36', 'broad212')},
        'scenarios': stress._approved_scenarios(schema),
        'output_root': str(tmp_path / 'out')}
    (evidence / 'document.json').write_text(json.dumps(document))
    return document, pins


@pytest.fixture
def loaders(monkeypatch):
    """Replace only the pinned parquet/SQLite readers with in-memory data."""
    monkeypatch.setattr(stress, '_bar_rows', lambda binding, symbols, calendar: {
        s: {d: 100.0 for d in calendar} for s in symbols})
    monkeypatch.setattr(stress, '_index_view', lambda path, symbol, expected: (
        _Quotes(_quote_rows()), {'sha256': expected, 'rows': 40}))
    monkeypatch.setattr(stress, '_index_factors', lambda view, calendar: {
        d: 1.0 for d in calendar})
    monkeypatch.setattr(stress, '_missing_expiry_factors', lambda *args: None)


@pytest.mark.parametrize('schema, fill_sizing', [
    ('mio-execution-stress/v3', 'available_size'), (V4, None), (V4, 'all_or_none')])
def test_replay_refuses_mismatched_sizing_and_schema(tmp_path, loaders, schema, fill_sizing):
    document, pins = _tree(tmp_path, schema=schema, fill_sizing=fill_sizing)
    with pytest.raises(ValueError, match='schema v4 require each other'):
        stress.FrozenMIOReplay('limited36', document, pins)


@pytest.mark.parametrize('pinned, wrong_sha', [(False, False), (True, True)])
def test_replay_refuses_unpinned_or_drifted_v4_run_document(tmp_path, loaders, pinned, wrong_sha):
    document, pins = _tree(tmp_path, schema=V4, pinned=pinned, wrong_sha=wrong_sha)
    with pytest.raises(ValueError, match='run document drift'):
        stress.FrozenMIOReplay('limited36', document, pins)


def _original_results(tmp_path):
    """Recompute the control once from draft packets: it becomes the pinned original."""
    document, pins = _tree(tmp_path / 'draft', schema=V4)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(stress.FrozenMIOReplay, '_verify_control_csv_rows', lambda self, r: None)
        replay = stress.FrozenMIOReplay('limited36', document, pins)
        results = []
        for packet in replay.original['results']:
            result, _daily = replay.run_method(packet, stress._approved_scenarios(V4)[0])
            results.append({**packet, 'result': json.loads(json.dumps(result))})
    return results


def _known_answer(results):
    """Hand-computed 3x3 fill of the 4x4 call spread; never regenerated by the replay.

    Buy 100C at ask 4.20 and sell 105C at bid 2.00, three each: premium
    -1260 + 600 = -660; fees 6 x 0.65 = 3.90; credit -663.90 (selected
    4x4 credit -885.20). The debit is the maximum loss and m0's reserve;
    m1's 200/contract floor gives 1200. Stress extra 0.25 x (0.20 + 0.10)
    x 100 x 3 = 22.50. Cash on the fill session is 10,000 - 663.90; at
    the 100 expiry both calls pay nothing, so P&L is the credit.
    """
    from datetime import date
    deposits = (date.fromisoformat(CALENDAR[-1]) - date.fromisoformat(CALENDAR[0])).days // 14
    for packet, reserve in zip(results, (663.9, 1200.0, 663.9, 663.9)):
        result = packet['result']
        trade, = result['trades']
        assert [(leg['quantity'], leg['selected_quantity'], leg['fill_price'])
                for leg in trade['legs']] == [(3, 4, 4.2), (3, 4, 2.0)]
        assert trade['contracts'] == 6 and trade['decision_credit'] == pytest.approx(-885.2)
        for key, value in (('premium', -660.0), ('fees', 3.9), ('credit', -663.9),
                           ('max_loss', 663.9), ('reserve', reserve),
                           ('stress_extra', 22.5), ('pnl', -663.9)):
            assert trade[key] == pytest.approx(value, abs=1e-9), key
        by_day = {point['date']: point for point in result['curve']}
        assert by_day[CALENDAR[1]]['cash'] == pytest.approx(10000.0 - 663.9, abs=1e-9)
        assert by_day[CALENDAR[1]]['reserve'] == pytest.approx(reserve, abs=1e-9)
        assert by_day[EXPIRY]['open_ids'] == [] and by_day[EXPIRY]['reserve'] == 0
        assert result['contributions'] == 10000.0 + 1000.0 * deposits
        assert result['pnl'] == pytest.approx(-663.9, abs=1e-9)
        assert result['final_equity'] == pytest.approx(
            result['contributions'] - 663.9, abs=1e-9)
        assert result['curve'][-1]['cash'] == pytest.approx(result['final_equity'], abs=1e-9)


def test_v4_execute_pins_run_document_and_compares_partial_fill_rows(tmp_path, loaders):
    results = _original_results(tmp_path)
    _known_answer(results)
    trade = results[0]['result']['trades'][0]
    assert [(leg['quantity'], leg['selected_quantity']) for leg in trade['legs']] == [(3, 4), (3, 4)]
    document, _pins = _tree(tmp_path / 'bad', schema=V4, results=results, partial_csv_quantity=4)
    with pytest.raises(ValueError, match='control order/fill CSV mismatch'):
        stress.execute(tmp_path / 'bad/evidence/document.json')
    document, _pins = _tree(tmp_path / 'good', schema=V4, results=results)
    output = stress.execute(tmp_path / 'good/evidence/document.json')
    manifest = json.loads((output / 'pins.json').read_text())
    config = tmp_path / 'good/repo/children/index_options/configs/fresh-run.json'
    assert manifest['original_run_documents'] == {
        c: {'name': 'fresh-run.json', 'sha256': stress._sha(config)}
        for c in ('limited36', 'broad212')}
    assert manifest['evidence_kind'] == stress._PROXY_NOTE
    summary = (output / 'report.md').read_text()
    assert '| limited36 | control | m0 | complete | 1 | 1 | 0 |' in summary
