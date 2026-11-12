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
    study = WeeklyOptionPortfolioStudy.__new__(WeeklyOptionPortfolioStudy)
    study.params = {}
    study._nodes = {"ABC": node}
    study._factors = {"ABC": {"2019-01-03": 1.0}}
    study._quotes = {"ABC": {("2019-01-03", "2019-02-01", "call", 100.0): row}}
    study._charge = lambda method: {}
    study._floor = lambda reserve, charge, legs, mult: reserve
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
    assert stress._approved_scenarios('mio-execution-stress/v5') == v1  # ADR-0280 amendment
    with pytest.raises(ValueError):
        stress._approved_scenarios('mio-execution-stress/v6')


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
def _weekdays(n=252, start='2019-01-02'):
    from datetime import date, timedelta
    day, days = date.fromisoformat(start), []
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


def _quote_rows(calendar=None, expiry=None):
    calendar, expiry = calendar or CALENDAR, expiry or EXPIRY
    rows = _Quotes()
    for day in calendar[1:21]:
        for strike, bid, ask, bid_size, ask_size in ((100.0, 4.0, 4.2, 10, 3),
                                                     (105.0, 2.0, 2.1, 10, 10)):
            rows[day, expiry, 'call', strike] = {
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


def _draft_packets(domain, calendar=None):
    calendar = calendar or CALENDAR
    expiry = calendar[20]
    legs = [{'right': 'call', 'strike': 100.0, 'sign': 1, 'quantity': 4, 'price': 4.2},
            {'right': 'call', 'strike': 105.0, 'sign': -1, 'quantity': 4, 'price': 2.0}]
    book = {'id': calendar[0] + ':ABC:' + expiry, 'symbol': 'ABC', 'expiry': expiry,
            'decision_date': calendar[0], 'factor': 1.0, 'contracts': 8, 'legs': legs,
            'credit': -885.2, 'reserve': 885.2, 'max_loss': 885.2}
    decision = {'date': calendar[0], 'status': 'selected', 'candidate_books': 1,
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


# ADR-0280 amendment (ADR-0285): schema v5 replays any pinned range. The
# fixture mirrors the Q1 2021 layout: one cohort, three methods, 61 sessions,
# a run document in a configs subdirectory and the mio/exports result folder.
V5 = 'mio-execution-stress/v5'
Q1 = _weekdays(61, '2021-01-04')
Q1_EXPIRY = Q1[20]
Q1_COHORT = 'q1_top3'
Q1_CONFIG = 'mio-q1/run.json'
Q1_BOUNDS = {'methods': 3, 'sessions': 61, 'end_before': '2021-04-01',
             'latest_expiry': '2021-03-26'}


def _commit(repo):
    import subprocess
    if not (repo / '.git').exists():
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                    '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                    'commit', '-q', '--allow-empty', '-m', 'fixture'], check=True)


def _q1_domain():
    domain = _domain('available_size')
    domain['params']['methods'] = domain['params']['methods'][:3]
    domain['params']['bridge'] = {  # The pinned Q1 run document's window fields.
        **domain['params']['bridge'], 'start': '2021-01-04', 'end_before': '2021-04-01',
        'protected_end_before': '2024-01-01', 'fold': 3}
    return domain


def _v5_tree(tmp_path, *, results=None, overrides=None, config=Q1_CONFIG):
    """A v5 repository root, the exports folder, results pins and document."""
    sha = stress._sha
    repo, evidence = tmp_path / 'repo', tmp_path / 'evidence'
    domain = _q1_domain()
    config_path = repo / 'children/index_options/configs' / config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(
        {'pipeline': {'plan': {'params': {'study': {'domain': domain}}}}}))
    _commit(repo)
    results = results if results is not None else _draft_packets(domain, Q1)
    exports = evidence / 'mio/exports'
    exports.mkdir(parents=True, exist_ok=True)
    (exports / 'result.json').write_text(json.dumps(
        {'complete': True, 'results': results, 'audit': {'calendar': Q1}}))
    csv_sha = {}
    for csv_name, body in _csvs(results).items():
        (exports / csv_name).write_text(body)
        csv_sha[csv_name] = sha(exports / csv_name)
    (exports / 'artifact-index.json').write_text(json.dumps(
        {'files': {name: {'sha256': value} for name, value in csv_sha.items()}}))
    pins = {'evidence': {'artifact-index.json': {
                'path': str(exports / 'artifact-index.json'),
                'sha256': sha(exports / 'artifact-index.json')}},
            'original_configs': {config: {'sha256': sha(config_path)}},
            'runs': {Q1_COHORT: {'run_dir': str(evidence), 'run_hash': 'r', 'sha256': 's',
                                 'document_hash': 'd'}},
            'sources': {}}
    (evidence / 'pins.json').write_text(json.dumps(pins))
    entry = {'config': config, 'result_dir': str(exports),
             'result_sha256': sha(exports / 'result.json'), 'csv_sha256': csv_sha,
             'quote_index_sha256': {'ABC': 'q'}, **Q1_BOUNDS, **(overrides or {})}
    document = {
        'schema': V5, 'repository_root': str(repo), 'period_label': '2021 Q1 validation',
        'results_pins': str(evidence / 'pins.json'),
        'results_pins_sha256': sha(evidence / 'pins.json'),
        'cohorts': {Q1_COHORT: entry},
        'scenarios': stress._approved_scenarios(V5),
        'output_root': str(tmp_path / 'out')}
    (evidence / 'document.json').write_text(json.dumps(document))
    return document, pins


@pytest.fixture
def q1_loaders(loaders, monkeypatch):
    """The in-memory readers, with quotes on the Q1 calendar."""
    monkeypatch.setattr(stress, '_index_view', lambda path, symbol, expected: (
        _Quotes(_quote_rows(Q1, Q1_EXPIRY)), {'sha256': expected, 'rows': 40}))


def test_v5_reuses_exactly_the_seven_v4_scenarios():
    assert stress._approved_scenarios(V5) == stress._approved_scenarios(V4)
    assert [s['id'] for s in stress._approved_scenarios(V5)] == [
        'control', 'adverse_price', 'half_size', 'expiry_down_15', 'expiry_up_15',
        'joint_down_15', 'joint_up_15']


def test_v5_and_available_size_require_each_other():
    stress.FrozenMIOReplay._sizing_schema(AVAILABLE, V5)
    for legacy in ({}, {'fill_sizing': 'all_or_none'}):
        with pytest.raises(ValueError, match='v5'):
            stress.FrozenMIOReplay._sizing_schema(legacy, V5)


def test_protected_window_is_the_path_a0003_holdout_start():
    import csv
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs/decisioning/path.csv'
    with path.open(newline='') as stream:
        row = next(r for r in csv.DictReader(stream) if r['id'] == 'A0003')
    assert 'holdout ' + stress._PROTECTED_FROM + '..' in ' '.join(row.values())


def _q1_original_results(tmp_path):
    """Recompute the Q1-shaped control once; it becomes the pinned original."""
    document, pins = _v5_tree(tmp_path / 'draft')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(stress.FrozenMIOReplay, '_verify_control_csv_rows', lambda self, r: None)
        replay = stress.FrozenMIOReplay(Q1_COHORT, document, pins)
        results = []
        for packet in replay.original['results']:
            result, _daily = replay.run_method(packet, stress._approved_scenarios(V5)[0])
            results.append({**packet, 'result': json.loads(json.dumps(result))})
    return results


def test_v5_execute_replays_a_pinned_q1_range_controls_first(tmp_path, q1_loaders, capsys):
    results = _q1_original_results(tmp_path)
    assert [p['id'] for p in results] == ['m0', 'm1', 'm2']
    for packet in results:  # Same hand-computed 3x3 fill as the 2019 known answer.
        trade, = packet['result']['trades']
        assert trade['fill_date'] == Q1[1] and trade['close_date'] == Q1_EXPIRY
        assert trade['pnl'] == pytest.approx(-663.9, abs=1e-9)
        assert len(packet['result']['curve']) == 61
    _v5_tree(tmp_path / 'good', results=results)
    capsys.readouterr()
    output = stress.execute(tmp_path / 'good/evidence/document.json')
    cases = [line.split()[:3] for line in capsys.readouterr().out.splitlines()
             if line.startswith(Q1_COHORT + ' ')]
    assert len(cases) == 21
    assert [c[1] for c in cases[:3]] == ['control'] * 3
    assert all(c[1] != 'control' for c in cases[3:])
    summary = json.loads((output / 'summary.json').read_text())
    assert len(summary) == 21 and {r['status'] for r in summary} == {'complete'}
    assert {(r['scenario'], r['method']) for r in summary} == {
        (s['id'], m) for s in stress._approved_scenarios(V5) for m in ('m0', 'm1', 'm2')}
    control = [r for r in summary if r['scenario'] == 'control']
    assert [r['final_profit_usd'] for r in control] == [
        p['result']['pnl'] for p in results]
    report = (output / 'report.md').read_text()
    assert report.startswith(
        '# 2021 Q1 validation MIO fixed-selection execution and expiry stress\n')
    assert '2019' not in report and '252' not in report
    daybook = (output / Q1_COHORT / 'control/m0.md').read_text()
    assert '2021 Q1 validation diagnostic.' in daybook and '2019' not in daybook
    case = json.loads((output / Q1_COHORT / 'half_size/m1.json').read_text())
    assert case['decision_reference']['path'] == str(
        tmp_path / 'good/evidence/mio/exports/result.json')
    assert len(case['daily']) == 61
    manifest = json.loads((output / 'pins.json').read_text())
    assert list(manifest['original_runs']) == [Q1_COHORT]
    config = tmp_path / 'good/repo/children/index_options/configs' / Q1_CONFIG
    assert manifest['original_run_documents'] == {
        Q1_COHORT: {'name': Q1_CONFIG, 'sha256': stress._sha(config)}}
    assert manifest['cohort_bounds'] == {Q1_COHORT: Q1_BOUNDS}
    assert manifest['period_label'] == '2021 Q1 validation'
    assert manifest['evidence_kind'] == stress._PROXY_NOTE


@pytest.mark.parametrize('overrides, match', [
    ({'sessions': 60}, 'protected account calendar'),
    ({'sessions': 62}, 'protected account calendar'),
    ({'end_before': Q1[-1]}, 'protected account calendar'),
    ({'latest_expiry': Q1[19]}, 'selected expiry outside the document bounds'),
    ({'methods': 4}, 'method'),
    ({'methods': 2}, 'method'),
    ({'end_before': '2024-01-02'}, 'protected window'),
    ({'end_before': '2026-01-01', 'latest_expiry': '2024-01-02'}, 'protected window'),
    ({'end_before': '2021-04-02'}, 'end_before differs from the run document'),
    ({'latest_expiry': '2021-04-01'}, 'latest_expiry'),
    ({'end_before': '2021-4-01'}, 'end_before'),
    ({'end_before': 20210401}, 'end_before'),
    ({'sessions': True}, 'sessions'),
    ({'methods': 0}, 'methods'),
])
def test_v5_refuses_results_outside_the_document_bounds(tmp_path, q1_loaders, overrides, match):
    document, pins = _v5_tree(tmp_path, overrides=overrides)
    with pytest.raises(ValueError, match=match):
        stress.FrozenMIOReplay(Q1_COHORT, document, pins)


@pytest.mark.parametrize('name', ['result.json', 'trades.csv', 'daily-account.csv'])
def test_v5_refuses_result_or_csv_drift(tmp_path, q1_loaders, name):
    document, pins = _v5_tree(tmp_path)
    path = tmp_path / 'evidence/mio/exports' / name
    path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError, match='identity drift'):
        stress.FrozenMIOReplay(Q1_COHORT, document, pins)


def test_v5_run_document_may_sit_in_a_configs_subdirectory(tmp_path):
    configs = tmp_path / 'children/index_options/configs'
    for name in ('run.json', 'mio-q1/run.json'):
        document = _cohort_document(tmp_path, V5, {'config': name})
        assert stress.FrozenMIOReplay._run_document(document, 'limited36') == configs / name
    (tmp_path / 'outside.json').write_text('{}')
    (configs / 'link.json').symlink_to(tmp_path / 'outside.json')
    for entry in ({}, {'config': '../x.json'}, {'config': 'a/../../x.json'},
                  {'config': str(configs / 'run.json')}, {'config': ''}, {'config': '.'},
                  {'config': 'a/..'}, {'config': 7}, {'config': 'link.json'}):
        with pytest.raises(ValueError, match='config'):
            stress.FrozenMIOReplay._run_document(
                _cohort_document(tmp_path, V5, entry), 'limited36')


def _rewrite(document, path):
    path.write_text(json.dumps(document))
    return path


@pytest.mark.parametrize('change, match', [
    (lambda d: d.update(original_root='/x'), 'invalid execution-stress document'),
    (lambda d: d.pop('period_label'), 'invalid execution-stress document'),
    (lambda d: d.update(period_label=''), 'period_label'),
    (lambda d: d.update(period_label=7), 'period_label'),
    (lambda d: d.update(cohorts={}), 'cohort'),
    (lambda d: d.update(cohorts={'../evil': d['cohorts'][Q1_COHORT]}), 'cohort'),
    (lambda d: d['cohorts'][Q1_COHORT].update(extra=1), 'cohort'),
    (lambda d: d['cohorts'][Q1_COHORT].pop('csv_sha256'), 'cohort'),
    (lambda d: d['scenarios'].pop(), 'scenario values differ'),
    (lambda d: d['cohorts'][Q1_COHORT].update(latest_expiry='2024-06-03',
                                              end_before='2025-01-01'), 'protected window'),
])
def test_v5_execute_refuses_invalid_documents_before_any_output(
        tmp_path, q1_loaders, change, match):
    document, _pins = _v5_tree(tmp_path)
    change(document)
    path = _rewrite(document, tmp_path / 'evidence/document.json')
    with pytest.raises(ValueError, match=match):
        stress.execute(path)
    assert not (tmp_path / 'out').exists()


def test_v4_document_presented_as_v5_refuses_and_v4_refuses_v5_fields(tmp_path, loaders):
    document, _pins = _tree(tmp_path, schema=V4)
    path = tmp_path / 'evidence/document.json'
    with pytest.raises(ValueError, match='invalid execution-stress document'):
        stress.execute(_rewrite({**document, 'schema': V5}, path))
    with pytest.raises(ValueError, match='invalid execution-stress document'):
        stress.execute(_rewrite({**document, 'period_label': '2019'}, path))
    assert not (tmp_path / 'out').exists()


# --- ADR-0312 Amendment 1 S3.3: the no-solve static_forward_hedge scenarios over a saved selection ---------------------

def _hedge_replay(spot_at_expiry=92., legs=(("put", 1, 100., 3),), extra_books=()):
    """A FrozenMIOReplay (built without its pinned files) over the hedge world and one saved selection."""
    import test_ledger_studies as tls

    study, _, spots = tls._hedge_study(spot_at_expiry=spot_at_expiry)
    book = tls._filled(study, [(r, s, k, q) for r, s, k, q in legs])
    saved = {k: v for k, v in book.items() if k not in ("fill_date", "decision_credit", "premium", "fees", "stress_extra")}
    saved.update(status="selected", credit=0., reserve=0., max_loss=0.)
    replay = stress.FrozenMIOReplay.__new__(stress.FrozenMIOReplay)
    replay.params, replay.calendar, replay.bars = study.params, study.calendar, study._bars
    replay.quotes, replay.factors, replay.symbols = study._quotes, study._factors, ["X"]
    replay.original = {"results": []}
    replay.hedge_spec = {"block": {k: v for k, v in tls._REPLAY_BLOCK.items() if k != "ratio"},
                         "parity": dict(tls._PARITY), "ratios": list(stress.HEDGE_RATIOS)}
    # the saved selection is decided on the day before the fill, with the fill-day reprice the study makes
    saved = tls._saved_book(study, saved)
    decision = {"date": "2019-01-15", "books": [saved], "components": {"groups": []}, "candidate_books": 1,
                "candidate_contracts": 3, "status": "selected", "refusals": []}
    packet = {"id": "m", "result": {"method": study.params["methods"][0], "decisions": [decision]}}
    return replay, packet, spots


def _scenario(ratio, mark="parity"):
    return {"id": stress.hedge_scenario_id(ratio, mark), "price_fraction": 0, "half_size": False,
            "expiry_multiplier": 1, "hedge_ratio": ratio, "hedge_mark": mark}


CONTROL = {"id": "static_control", "price_fraction": 0, "half_size": False, "expiry_multiplier": 1}


def test_the_hedged_scenario_settles_the_forward_leg_and_the_control_has_no_hedge_field():
    import json as j

    replay, packet, spots = _hedge_replay()
    plain, _ = replay.run_method(packet, CONTROL)
    assert "hedge" not in j.dumps(plain), "no hedge field in the unhedged twin"
    hedged, daily = replay.run_method(packet, _scenario(1.0))
    book, base = hedged["trades"][0], plain["trades"][0]
    record = book["hedge"]
    assert record["status"] == "ok" and record["units"] > 0
    from index_options.ledger_studies import ReplayHedge

    assert book["pnl"] == pytest.approx(base["pnl"] + ReplayHedge.net_settle(record, 92.))
    assert hedged["pnl"] == pytest.approx(book["pnl"]) and hedged["hedge"]["mark_rule"] == "parity"
    assert hedged["hedge"]["ratio"] == 1.0 and len(hedged["curve"]) == len(plain["curve"])


def test_the_hedged_curve_marks_the_leg_at_the_days_forward_and_contributions_are_untouched():
    replay, packet, spots = _hedge_replay()
    plain, _ = replay.run_method(packet, CONTROL)
    hedged, _ = replay.run_method(packet, _scenario(1.0))
    record = hedged["trades"][0]["hedge"]
    day = "2019-01-23"
    p, h = next(c for c in plain["curve"] if c["date"] == day), next(c for c in hedged["curve"] if c["date"] == day)
    assert h["contributions"] == p["contributions"]
    forward = spots[day] * 1.001
    entry = (record["units"] * 0 + 0)  # noqa: F841
    from index_options.ledger_studies import ReplayHedge

    assert h["equity"] - p["equity"] == pytest.approx(ReplayHedge.mark(record, forward))


def test_the_carry_ratio_twin_marks_every_day_on_the_carry_ratio_and_ratio_zero_changes_nothing():
    replay, packet, spots = _hedge_replay()
    plain, _ = replay.run_method(packet, CONTROL)
    zero, _ = replay.run_method(packet, _scenario(0.0))
    assert zero["pnl"] == pytest.approx(plain["pnl"]) and [c["equity"] for c in zero["curve"]] == \
        pytest.approx([c["equity"] for c in plain["curve"]])
    carry, _ = replay.run_method(packet, _scenario(1.0, "carry_ratio"))
    primary, _ = replay.run_method(packet, _scenario(1.0))
    assert carry["pnl"] == pytest.approx(primary["pnl"]), "settlement does not depend on the mark rule"
    assert carry["hedge"]["mark_rule"] == "carry_ratio"


def test_v6_scenarios_are_the_v5_seven_plus_the_registered_sweep_with_a_carry_twin():
    import test_ledger_studies as tls

    hedge = {"block": {k: v for k, v in tls._REPLAY_BLOCK.items() if k != "ratio"}, "parity": dict(tls._PARITY),
             "ratios": list(stress.HEDGE_RATIOS)}
    v5 = stress._approved_scenarios("mio-execution-stress/v5")
    v6 = stress._approved_scenarios("mio-execution-stress/v6", hedge)
    assert v6[:len(v5)] == v5 and len(v6) == len(v5) + 8
    assert [s["id"] for s in v6[len(v5):]][:2] == ["static_forward_hedge_r025", "static_forward_hedge_r025_carry"]
    for bad in ({**hedge, "ratios": [0.5]}, {**hedge, "block": {**hedge["block"], "mode": "program"}},
                {**hedge, "block": {**hedge["block"], "ratio": 1.0}}, {**hedge, "extra": 1}):
        with pytest.raises(ValueError):
            stress._approved_scenarios("mio-execution-stress/v6", bad)
    assert stress._approved_scenarios("mio-execution-stress/v5") == v5, "earlier schemas do not move"


def test_the_stress_route_refuses_a_hedge_past_the_seal_a_sizer_and_a_split_with_the_replay_codes():
    check = stress.FrozenMIOReplay.check_hedge_seal
    check(None, {"end_before": "2022-01-01", "latest_expiry": None}, "c")           # no hedge: no seal rule here
    check({"block": {}}, {"end_before": "2021-07-01", "latest_expiry": "2021-07-01"}, "c")
    with pytest.raises(ValueError, match="hedge_seal"):
        check({"block": {}}, {"end_before": "2021-07-01", "latest_expiry": "2021-07-02"}, "c")
    with pytest.raises(ValueError, match="hedge_seal"):
        check({"block": {}}, {"end_before": "2021-07-02", "latest_expiry": None}, "c")
    replay, packet, _ = _hedge_replay()
    sized = {**packet["result"]["method"], "sizer": {"beta_lookback": 5}}
    with pytest.raises(ValueError, match="hedge_with_sizer"):
        replay.run_method({**packet, "result": {**packet["result"], "method": sized}}, _scenario(1.0))
    replay.params = {**replay.params}
    original = stress.FrozenMIOReplay.study

    def with_split(self, scenario):
        study = original(self, scenario)
        study.adjustments = [{"symbol": "X"}]
        return study

    stress.FrozenMIOReplay.study = with_split
    try:
        with pytest.raises(ValueError, match="hedge_split_unsupported"):
            replay.run_method(packet, _scenario(1.0))
        replay.run_method(packet, CONTROL)                                          # the control twin is untouched
    finally:
        stress.FrozenMIOReplay.study = original

def test_a_missing_later_day_forward_carries_the_last_level_flagged_and_counted_on_the_stress_route_too():
    replay, packet, spots = _hedge_replay()
    for day in ("2019-01-22", "2019-01-23"):
        replay.bars["X"].pop(day, None)
    result, _ = replay.run_method(packet, _scenario(1.0))
    counts = result["hedge"]["counts"]
    assert counts["hedge_forward_missing"] >= 2 and counts["mark_forward_carried"] >= 2 and counts["hedge_marks"] > 0
    assert result["curve"]

def test_v6_packet_replays_a_post_entry_settlement_with_pinned_history(tmp_path, q1_loaders, monkeypatch):
    """Gate 0 reproduces the original account across its independently bounded settlement tail."""
    from datetime import date, timedelta
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    end = Q1[10]
    tail = (date.fromisoformat(end) + timedelta(days=60)).isoformat()
    calendar = [d for d in Q1 if d < tail]
    document, pins = _v5_tree(tmp_path, overrides={'end_before': end, 'sessions': len(calendar),
                                                    'latest_expiry': Q1_EXPIRY})
    cohort = document['cohorts'][Q1_COHORT]
    config_path = stress.FrozenMIOReplay._run_document(document, Q1_COHORT)
    config = json.loads(config_path.read_text())
    config['name'] = 'frozen-packet'
    config['pipeline']['plan']['uses'] = 'dskit.pipeline.study_protocol:StudyProtocolPlan'
    config['pipeline']['close'] = {'uses': 'dskit.pipeline.study_protocol:StudyPacketClose'}
    study = config['pipeline']['plan']['params']['study']
    study.update(contract='index_options.mio_contract:MIOStudyContract', selected_packet='dev',
                 packets=[{'id': 'dev', 'start': Q1[0], 'end_before': end}])
    study['domain'].update(history_start='2020-12-31', tail_padding_days=0)
    study['domain']['params']['bridge']['end_before'] = end
    study['domain']['sources']['sessions'] = {'pin': {'id': 'sessions'}, 'files': {'shared': 'days.parquet'}}

    def pin_config():
        config_path.write_text(json.dumps(config))
        pins['original_configs'][cohort['config']]['sha256'] = stress._sha(config_path)
        from dskit.pipeline.document import PipelineDocument
        pins['runs'][Q1_COHORT]['document_hash'] = PipelineDocument.from_obj(config).hash

    pin_config()
    result_dir = Path(cohort['result_dir'])
    result_path = result_dir / 'result.json'
    original = json.loads(result_path.read_text())
    original['audit']['calendar'] = calendar

    def pin_result():
        result_path.write_text(json.dumps(original))
        cohort['result_sha256'] = stress._sha(result_path)
        pins['runs'][Q1_COHORT]['sha256'] = cohort['result_sha256']

    pin_result()
    document.update(schema='mio-execution-stress/v6', hedge={'block': {}})
    history = ['2020-12-31', *calendar]
    session_file = tmp_path / 'days.parquet'
    pq.write_table(pa.table({'date': [*history, '2021-07-01', '2021-07-02']}), session_file)
    verified = []

    class PinnedSessions:
        def __init__(self, pin):
            assert pin == {'id': 'sessions'}

        def file(self, relative, *, verify):
            assert relative == session_file.name and verify
            verified.append(relative)
            return session_file

    from dskit.pipeline.driver import RunAttestation

    def attested_output(self, node, port, document_hash):
        assert (node, port, document_hash) == ('close', 'receipt', pins['runs'][Q1_COHORT]['document_hash'])
        return {'sha256': pins['runs'][Q1_COHORT]['sha256']}

    monkeypatch.setattr(RunAttestation, 'attested_output', attested_output)
    monkeypatch.setattr(stress, 'PinnedPayloadSource', PinnedSessions)
    replay = stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    assert replay.calendar == calendar and max(replay.calendar) > end
    assert replay.study(CONTROL).adjustments == {}
    assert set(replay.bars['ABC']) == set(history)
    assert verified == ['days.parquet', 'days.parquet']

    with monkeypatch.context() as patch:
        patch.setattr(stress.FrozenMIOReplay, '_verify_control_csv_rows', lambda self, result: None)
        for packet in original['results']:
            result, _ = replay.run_method(packet, CONTROL)
            packet['result'] = json.loads(json.dumps(result))
    pin_result()
    for name, body in _csvs(original['results']).items():
        (result_dir / name).write_text(body)
        cohort['csv_sha256'][name] = stress._sha(result_dir / name)
    replay = stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    result, _ = replay.run_method(replay.original['results'][0], CONTROL)
    assert result == original['results'][0]['result']
    assert result['trades'][0]['close_date'] == Q1_EXPIRY > end
    assert all(row['date'] < end for row in result['curve'] if row['deposit'])

    original['audit']['calendar'] = calendar[:-1]
    pin_result()
    with pytest.raises(ValueError, match='calendar differs from the pinned packet sessions'):
        stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    original['audit']['calendar'] = calendar
    pin_result()
    verified.clear()
    sha, load = stress._sha, stress._load_json

    def metadata_only(read):
        def wrapped(path):
            assert not Path(path).is_relative_to(result_dir), "result or CSV was accessed before seal refusal"
            return read(path)
        return wrapped

    monkeypatch.setattr(stress, '_sha', metadata_only(sha))
    monkeypatch.setattr(stress, '_load_json', metadata_only(load))
    monkeypatch.setattr(stress.FrozenMIOReplay, '_verify_original_csvs',
                        lambda *args: pytest.fail("CSV reads before seal refusal"))
    good_result = cohort['result_sha256']
    cohort['result_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='close receipt'):
        stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    cohort['result_sha256'] = good_result

    study['domain']['tail_padding_days'] = 400
    pin_config()
    with pytest.raises(ValueError, match='hedge_seal'):
        stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    assert verified == []


# =============================================================================================================
# ADR-0316 Decision A (Amendments 1 and 3): schema v7, the severity scenarios, three per-case statistics, the
# reverse stress and the pre-registered verdict. Synthetic only; every expected number is worked by hand.
# =============================================================================================================
import math  # noqa: E402

V7 = 'mio-execution-stress/v7'
NEW_EIGHT = ['adverse_price_100', 'quarter_size', 'expiry_down_3s', 'expiry_up_3s',
             'market_down_30', 'market_up_20', 'joint_down_severe', 'joint_up_severe']
FLAT, VOL, MKT = 'flat', 'volatility', 'market'


def _shock(kind, value):
    return {'kind': kind, {FLAT: 'multiplier', VOL: 'sigmas', MKT: 'move'}[kind]: value}


def _v7(price=0, divisor=1, shock=None, name='case'):
    return {'id': name, 'price_fraction': price, 'size_divisor': divisor,
            'expiry_shock': shock or _shock(FLAT, 1)}


def test_v7_scenarios_are_the_fifteen_approved_cases_in_order():
    cases = stress._approved_scenarios(V7)
    assert [c['id'] for c in cases] == [
        'control', 'adverse_price', 'half_size', 'expiry_down_15', 'expiry_up_15',
        'joint_down_15', 'joint_up_15', *NEW_EIGHT]
    assert {tuple(sorted(c)) for c in cases} == {
        ('expiry_shock', 'id', 'price_fraction', 'size_divisor')}
    # (price fraction, size divisor, expiry shock); the joint severe cases keep FULL depth (Amendment 3).
    expected = {
        'control': (0, 1, _shock(FLAT, 1)), 'adverse_price': (0.25, 1, _shock(FLAT, 1)),
        'half_size': (0, 2, _shock(FLAT, 1)), 'expiry_down_15': (0, 1, _shock(FLAT, 0.85)),
        'expiry_up_15': (0, 1, _shock(FLAT, 1.15)), 'joint_down_15': (0.25, 2, _shock(FLAT, 0.85)),
        'joint_up_15': (0.25, 2, _shock(FLAT, 1.15)),
        'adverse_price_100': (1.0, 1, _shock(FLAT, 1)), 'quarter_size': (0, 4, _shock(FLAT, 1)),
        'expiry_down_3s': (0, 1, _shock(VOL, -3)), 'expiry_up_3s': (0, 1, _shock(VOL, 3)),
        'market_down_30': (0, 1, _shock(MKT, -0.30)), 'market_up_20': (0, 1, _shock(MKT, 0.20)),
        'joint_down_severe': (1.0, 1, _shock(MKT, -0.30)), 'joint_up_severe': (1.0, 1, _shock(MKT, 0.20))}
    for case in cases:
        assert (case['price_fraction'], case['size_divisor'], case['expiry_shock']) == expected[case['id']]


def test_the_v7_legacy_seven_carry_the_v5_values_and_every_earlier_list_is_unchanged():
    v5 = {c['id']: c for c in stress._approved_scenarios(V5)}
    for case in stress._approved_scenarios(V7)[:7]:
        old = v5[case['id']]
        assert case['price_fraction'] == old['price_fraction']
        assert case['size_divisor'] == (2 if old['half_size'] else 1)
        assert case['expiry_shock'] == _shock(FLAT, old['expiry_multiplier'])
    for schema in ('mio-execution-stress/v1', V4, V5):
        assert all(set(c) >= {'id', 'price_fraction', 'half_size', 'expiry_multiplier'}
                   and 'size_divisor' not in c and 'expiry_shock' not in c
                   for c in stress._approved_scenarios(schema))
    assert stress._SCHEMA_VERSIONS[-1] == V7 and len(stress._approved_scenarios(V7)) == 15


def test_scaled_size_floors_the_displayed_depth_and_keeps_the_half_rule():
    assert [stress.scaled_size(11, d) for d in (1, 2, 4)] == [11, 5, 2]
    assert stress.scaled_size(3, 4) == 0 and stress.scaled_size(7, 1) == 7
    assert stress.effective_size(5, True) == stress.scaled_size(5, 2)
    assert stress._size_divisor({'half_size': True, 'price_fraction': 0}) == 2
    assert stress._size_divisor({'half_size': False, 'price_fraction': 0}) == 1
    assert stress._size_divisor(_v7(divisor=4)) == 4


def test_adverse_price_100_pays_a_full_spread_on_a_buy_and_gives_up_a_full_spread_on_a_sale():
    scenario = _v7(price=1.0)
    study, book = _case()                              # bid 1.0 ask 1.4: buy at 1.4 + 0.4
    filled, reason = stress._scenario_fill(study, book, '2019-01-03', {}, scenario)
    assert reason is None and filled['legs'][0]['fill_price'] == pytest.approx(1.8)
    assert filled['credit'] == pytest.approx(-180.65)  # -1.8 x 100 less the 0.65 fee
    study, book = _case(sign=-1)                       # sell at 1.0 - 0.4
    filled, reason = stress._scenario_fill(study, book, '2019-01-03', {}, scenario)
    assert reason is None and filled['legs'][0]['fill_price'] == pytest.approx(0.6)
    assert filled['credit'] == pytest.approx(59.35)
    study, book = _case(sign=-1, bid=0.3, ask=0.7)     # 0.3 - 0.4 < 0: refused, not clipped
    assert stress._scenario_fill(study, book, '2019-01-03', {}, scenario) == (
        None, 'invalid_stressed_fill_price')


def test_quarter_size_floor_divides_displayed_depth_by_four_in_every_reader():
    scenario = _v7(divisor=4)
    study, book = _case(size=11, quantity=3)           # 11 // 4 = 2 < 3
    assert stress._scenario_fill(study, book, '2019-01-03', {}, scenario) == (
        None, 'insufficient_fill_size')
    evidence = stress._attempt_evidence(study, book, '2019-01-03', scenario)['legs'][0]
    assert (evidence['original_depth'], evidence['effective_depth']) == (11, 2)
    study, book = _case(size=11, quantity=2)
    filled, reason = stress._scenario_fill(study, book, '2019-01-03', {}, scenario)
    assert reason is None and filled['legs'][0]['quantity'] == 2
    study, book = _case(size=3, quantity=1)            # 3 // 4 = 0
    assert stress._scenario_fill(study, book, '2019-01-03', {}, scenario)[1] == 'insufficient_fill_size'
    assert stress._fill_shocked(scenario) and not stress._fill_shocked(_v7())
    assert stress._fill_shocked(_v7(price=1.0))
    study, book = _sized((6, 4), (16, 12))             # available size: 16//4 = 4, 12//4 = 3 -> 3 and 2
    filled, reason = stress._scenario_fill(study, book, '2019-01-03', {}, scenario)
    assert reason is None and [leg['quantity'] for leg in filled['legs']] == [3, 2]


def test_a_v7_formatted_legacy_case_fills_exactly_like_its_v5_twin():
    v5 = {c['id']: c for c in stress._approved_scenarios(V5)}
    for case in stress._approved_scenarios(V7)[:7]:
        old = v5[case['id']]
        depths = (8, 6) if old['half_size'] else (4, 3)
        assert (stress._scenario_fill(*_sized((6, 4), depths), '2019-01-03', {}, case)
                == stress._scenario_fill(*_sized((6, 4), depths), '2019-01-03', {}, old))
        study, book = _sized((6, 4), depths)
        assert (stress._attempt_evidence(study, book, '2019-01-03', case)
                == stress._attempt_evidence(study, book, '2019-01-03', old))


# --- the expiry-shock hook hierarchy ---------------------------------------------------------------------------
@pytest.mark.parametrize('spec, cls', [(_shock(FLAT, 0.85), 'FlatShock'), (_shock(VOL, -3), 'VolatilityShock'),
                                       (_shock(MKT, 0.2), 'MarketShock')])
def test_expiry_shock_builds_the_subclass_its_kind_names(spec, cls):
    shock = stress.ExpiryShock.of(spec)
    assert type(shock) is getattr(stress, cls) and shock.spec() == spec


@pytest.mark.parametrize('spec', [
    {'kind': 'nope', 'multiplier': 1}, {'kind': FLAT}, {'kind': FLAT, 'multiplier': 1, 'extra': 1},
    _shock(FLAT, 0), _shock(FLAT, -1), _shock(FLAT, float('nan')), _shock(FLAT, True), _shock(FLAT, '1'),
    _shock(VOL, True), _shock(VOL, float('inf')), _shock(VOL, 'x'), _shock(MKT, None), _shock(MKT, float('nan')),
    {'multiplier': 1}, [], None])
def test_expiry_shock_is_default_deny(spec):
    with pytest.raises(ValueError):
        stress.ExpiryShock.of(spec)


def test_the_expiry_shock_hook_is_abstract():
    with pytest.raises(TypeError):
        stress.ExpiryShock()
    with pytest.raises(TypeError):
        stress.EstimatedShock(1)
    assert stress.expiry_shock({'expiry_multiplier': 0.85, 'half_size': False,
                                'price_fraction': 0}).spec() == _shock(FLAT, 0.85)


# --- the estimator: a wave world with hand-worked scale and beta ------------------------------------------------
W = _weekdays(10, '2021-01-04')                        # d0 .. d9


def _wave(amplitude, days=W):
    """Bars whose log returns alternate -a, +a, -a ... from d1 (odd index up)."""
    return {day: 100.0 * math.exp(amplitude * (i % 2)) for i, day in enumerate(days)}


def _estimation_params(symbols, min_pairs=3):
    return {'symbols': list(symbols), 'correlation_min_pairs': min_pairs,
            'bridge': {'solver': {'multiplier': 100}}}


def _estimator(bars=None, lookback=4, symbols=('A', 'B'), min_pairs=3):
    bars = bars if bars is not None else {'A': _wave(0.02), 'B': _wave(0.04)}
    factors = {s: {d: 1.0 for d in W} for s in symbols}
    return stress.ShockEstimator(_estimation_params(symbols, min_pairs), W, bars, factors, lookback)


def test_scale_is_the_sample_deviation_of_the_window_before_the_day_and_counts_sessions_to_expiry():
    # Fill d6: the window is the four returns d1->d5, (-a, +a, -a, +a); ddof 1 gives a*sqrt(4/3).
    # Sessions after d6 up to d9 are d7, d8, d9 = 3, so the horizon scale is a*sqrt(4/3)*sqrt(3) = 2a.
    est = _estimator()
    std, sessions, window_end = est.scale('A', W[6], W[9])
    assert (sessions, window_end) == (3, W[5]) and std == pytest.approx(0.02 * math.sqrt(4 / 3))
    assert est.scale('B', W[6], W[9])[0] == pytest.approx(0.04 * math.sqrt(4 / 3))
    assert stress.VolatilityShock(-3).estimate(est, 'A', W[6], W[9])[0] == pytest.approx(0.8869204367171575)
    assert stress.VolatilityShock(3).estimate(est, 'A', W[6], W[9])[0] == pytest.approx(1.1274968515793757)
    assert stress.VolatilityShock(-3).estimate(est, 'B', W[6], W[9])[0] == pytest.approx(0.7866278610665535)
    assert stress.VolatilityShock(3).estimate(est, 'B', W[6], W[9])[0] == pytest.approx(1.2712491503214047)
    multiplier, record = stress.VolatilityShock(-3).estimate(est, 'A', W[6], W[9])
    assert record == {'symbol': 'A', 'expiry': W[9], 'window_end': W[5], 'sessions_to_expiry': 3,
                      'std': pytest.approx(0.02309401076758503), 'beta': None,
                      'multiplier': multiplier}
    # One session to expiry from d8: window d3->d7 is again (-,+,-,+); horizon 1.
    assert stress.VolatilityShock(-3).estimate(est, 'A', W[8], W[9])[0] == pytest.approx(0.9330634889213453)


def test_beta_is_the_equal_weight_basket_loading_and_the_market_multiplier_floors_at_zero():
    # A = a*p, B = 2a*p, basket = 1.5a*p: beta_A = 2/3, beta_B = 4/3.
    est = _estimator()
    assert est.beta('A', W[6]) == pytest.approx(2 / 3) and est.beta('B', W[6]) == pytest.approx(4 / 3)
    for move, a, b in ((-0.30, 0.8, 0.6), (0.20, 1 + 0.2 * 2 / 3, 1 + 0.2 * 4 / 3)):
        shock = stress.MarketShock(move)
        assert shock.estimate(est, 'A', W[6], W[9])[0] == pytest.approx(a)
        assert shock.estimate(est, 'B', W[6], W[9])[0] == pytest.approx(b)
    record = stress.MarketShock(-0.30).estimate(est, 'A', W[6], W[9])[1]
    assert record['beta'] == pytest.approx(2 / 3) and record['window_end'] == W[5] and record['std'] is None
    # Four names, only A moving: beta_A = 4, beta_B = 0; 1 + 4 x -0.30 < 0 floors at zero.
    flat = {s: {d: 100.0 for d in W} for s in 'BCD'}
    est4 = _estimator({'A': _wave(0.1), **flat}, symbols=('A', 'B', 'C', 'D'))
    assert est4.beta('A', W[6]) == pytest.approx(4.0)
    assert stress.MarketShock(-0.30).estimate(est4, 'A', W[6], W[9])[0] == 0.0
    assert stress.MarketShock(-0.30).estimate(est4, 'B', W[6], W[9])[0] == 1.0


def test_an_estimate_never_reads_the_fill_session_or_later_but_does_read_the_session_before():
    base = _estimator()
    moved = {'A': _wave(0.02), 'B': _wave(0.04)}
    for day in W[6:]:
        moved['A'][day] *= 3.0
        moved['B'][day] *= 0.2
    later = _estimator(moved)
    assert later.scale('A', W[6], W[9])[0] == base.scale('A', W[6], W[9])[0]
    assert later.beta('A', W[6]) == base.beta('A', W[6])
    moved['A'][W[5]] *= 1.5
    assert _estimator(moved).scale('A', W[6], W[9])[0] != base.scale('A', W[6], W[9])[0]


@pytest.mark.parametrize('bad, call, match', [
    ('short', lambda e: e.scale('A', W[3], W[9]), 'window'),
    ('short', lambda e: e.beta('A', W[3]), 'window'),
    ('zero', lambda e: e.scale('A', W[6], W[9]), 'bar'),
    ('negative', lambda e: e.scale('A', W[6], W[9]), 'bar'),
    ('nan', lambda e: e.scale('A', W[6], W[9]), 'bar'),
    ('other_name', lambda e: e.beta('A', W[6]), 'bar'),
    ('flat_name', lambda e: e.scale('A', W[6], W[9]), 'standard deviation'),
    ('flat_all', lambda e: e.beta('A', W[6]), 'basket variance'),
    ('plain', lambda e: e.scale('A', W[9], W[9]), 'sessions')])
def test_a_bad_window_is_unsupported_and_nothing_is_substituted(bad, call, match):
    bars = {'A': _wave(0.02), 'B': _wave(0.04)}
    if bad == 'zero':
        bars['A'][W[4]] = 0.0
    elif bad == 'negative':
        bars['A'][W[4]] = -5.0
    elif bad == 'nan':
        bars['A'][W[4]] = float('nan')
    elif bad == 'other_name':
        bars['B'][W[4]] = 0.0
    elif bad == 'flat_name':
        bars['A'] = {d: 100.0 for d in W}
    elif bad == 'flat_all':
        bars = {'A': {d: 100.0 for d in W}, 'B': {d: 100.0 for d in W}}
    with pytest.raises(stress.UnsupportedStress, match=match):
        call(_estimator(bars))
    assert issubclass(stress.UnsupportedStress, ValueError)


def test_the_name_scale_ignores_another_names_bad_bar_but_the_beta_needs_every_symbol():
    bars = {'A': _wave(0.02), 'B': _wave(0.04)}
    bars['B'][W[4]] = float('nan')
    est = _estimator(bars)
    assert est.scale('A', W[6], W[9])[0] == pytest.approx(0.02309401076758503)
    with pytest.raises(stress.UnsupportedStress):
        est.beta('A', W[6])


def test_a_lookback_below_the_beta_owners_minimum_pairs_is_refused_at_construction():
    with pytest.raises(ValueError, match='correlation_min_pairs'):
        _estimator(lookback=2, min_pairs=3)
    _estimator(lookback=3, min_pairs=3)


# --- fill points and per-method multipliers --------------------------------------------------------------------
def _book(symbol, expiry, decision):
    return {'id': decision + ':' + symbol + ':' + expiry, 'symbol': symbol, 'expiry': expiry,
            'decision_date': decision}


def _packet(method, *books):
    decisions = [{'date': d, 'books': [b for b in books if b['decision_date'] == d]}
                 for d in sorted({b['decision_date'] for b in books})]
    return {'id': method, 'result': {'method': {'id': method}, 'decisions': decisions}}


def _bare_replay(packets, lookback=4):
    replay = stress.FrozenMIOReplay.__new__(stress.FrozenMIOReplay)
    replay.params = _estimation_params(['A', 'B'])
    replay.history, replay.calendar, replay.symbols = W, W[3:], ['A', 'B']
    replay.bars = {'A': _wave(0.02), 'B': _wave(0.04)}
    replay.factors = {s: {d: 1.0 for d in W} for s in 'AB'}
    replay.original = {'results': packets}
    replay.cohort = 'c'
    replay.per_method = True
    replay.stress = SimpleNamespace(lookback=lookback)
    return replay


def test_the_estimate_day_is_the_earliest_fill_session_of_the_methods_books_sharing_symbol_and_expiry():
    early, late = _book('A', W[9], W[5]), _book('A', W[9], W[7])
    other = _book('B', W[9], W[7])
    packet = _packet('m0', late, early, other)
    replay = _bare_replay([packet])
    assert replay.fill_points(packet) == {('A', W[9]): W[6], ('B', W[9]): W[8]}
    multipliers, records = stress.VolatilityShock(-3).apply(replay, packet)
    assert multipliers[('A', W[9])] == pytest.approx(0.8869204367171575)      # window to d5, 3 sessions
    assert [r['window_end'] for r in records] == [W[5], W[7]] and records[0]['sessions_to_expiry'] == 3


def test_each_methods_multipliers_depend_only_on_its_own_books_and_no_estimate_is_shared():
    p1 = _packet('m0', _book('A', W[9], W[5]), _book('B', W[9], W[5]))
    p2 = _packet('m1', _book('A', W[9], W[7]))
    replay = _bare_replay([p1, p2])
    alone = _bare_replay([p1])
    shock = stress.VolatilityShock(-3)
    assert shock.apply(replay, p1)[0] == shock.apply(alone, p1)[0]
    assert shock.apply(replay, p2)[0][('A', W[9])] == pytest.approx(0.9330634889213453)   # d7 window, 1 session
    assert ('B', W[9]) not in shock.apply(replay, p2)[0]
    first, second = replay.new_estimator(), replay.new_estimator()
    assert first is not second and first.study._beta_cache is not second.study._beta_cache
    assert first.study._bars is replay.bars          # the unshocked pinned bars, never a case's copy


def test_a_flat_shock_moves_the_packets_own_expiries_and_one_moves_nothing():
    p1 = _packet('m0', _book('A', W[9], W[5]))
    p2 = _packet('m1', _book('B', W[8], W[5]))
    replay = _bare_replay([p1, p2])
    assert stress.FlatShock(0.85).apply(replay, p1) == ({('A', W[9]): 0.85}, [])
    assert stress.FlatShock(1).apply(replay, p1) == ({}, [])
    assert replay.expiry_points(None) == {('A', W[9]), ('B', W[8])}      # the v1-v6 route shocks every method's expiry


@pytest.mark.parametrize('decision', [W[2], W[9]])
def test_a_book_with_no_usable_window_or_no_fill_session_makes_the_case_unsupported(decision):
    packet = _packet('m0', _book('A', W[9], decision))
    with pytest.raises(stress.UnsupportedStress):
        stress.VolatilityShock(-3).apply(_bare_replay([packet]), packet)


# --- per-case statistics ---------------------------------------------------------------------------------------
def _row(open_ids, equity, breach=False, cash=False, funding=False):
    return {'open_ids': open_ids, 'equity': equity, 'reserve_breach': breach,
            'negative_cash': cash, 'negative_funding': funding}


def test_case_statistics_are_hand_worked():
    trades = [{'id': 'b1', 'symbol': 'A', 'reserve': 300.0}, {'id': 'b2', 'symbol': 'B', 'reserve': 100.0},
              {'id': 'b3', 'symbol': 'A', 'reserve': 200.0}]
    result = {'pnl': -663.9, 'contributions': 10000.0, 'trades': trades}
    daily = [_row([], 9000.0), _row(['b1', 'b2', 'b3'], 2000.0, breach=True),   # A 500 / 2000 = 0.25
             _row(['b2'], 500.0, cash=True), _row(['b1'], 4000.0, funding=True), _row([], 9000.0, breach=True)]
    stats = stress.CaseStatistics.of(result, daily)
    assert stats == {'loss_fraction': pytest.approx(0.06639), 'breach_days': 4,
                     'worst_name_reserve_share': pytest.approx(0.25)}
    assert stress.CaseStatistics.of({**result, 'pnl': 50.0}, [_row([], 9000.0)]) == {
        'loss_fraction': pytest.approx(-0.005), 'breach_days': 0, 'worst_name_reserve_share': None}
    zero = stress.CaseStatistics.of({**result, 'pnl': 0.0}, [_row([], 9000.0)])
    assert zero['loss_fraction'] == 0.0 and str(zero['loss_fraction']) == '0.0'


# --- the verdict -----------------------------------------------------------------------------------------------
def _case_tuple(scenario, status='complete', selected=5, filled=5, loss=0.02, drawdown=-0.10, breach=0,
                share=0.10, reverse=None, cohort='c', method='m'):
    if status != 'complete':
        metrics = {'status': status, 'selected': selected, 'filled': None, 'max_drawdown': None,
                   'loss_fraction': None, 'breach_days': None, 'worst_name_reserve_share': None}
    else:
        metrics = {'status': status, 'selected': selected, 'filled': filled, 'max_drawdown': drawdown,
                   'loss_fraction': loss, 'breach_days': breach, 'worst_name_reserve_share': share}
    result = {'reverse_stress': reverse} if reverse is not None else None
    return (cohort, scenario, method, result, [], metrics, [])


REVERSE = {'down_move': 0.1, 'down_reached': True, 'up_move': 0.3, 'up_reached': False,
           'sessions': 7, 'unavailable': None}
CRITERIA = [
    {'name': 'loss', 'stat': 'worst_loss_fraction', 'op': '<=', 'value': 0.25, 'min_n': 1},
    {'name': 'drawdown', 'stat': 'worst_drawdown', 'op': '<=', 'value': 0.35, 'min_n': 1},
    {'name': 'breach', 'stat': 'worst_breach_days', 'op': '<=', 'value': 0, 'min_n': 1},
    {'name': 'name', 'stat': 'worst_name_reserve_share', 'op': '<', 'value': 0.15, 'min_n': 1},
    {'name': 'down', 'stat': 'reverse_down_move', 'op': '>=', 'value': 0.1, 'min_n': 1},
    {'name': 'up', 'stat': 'reverse_up_move', 'op': '>', 'value': 0.2, 'min_n': 1}]


def _cases(**extra):
    return [_case_tuple('control', reverse=REVERSE, **extra),
            _case_tuple('adverse_price', filled=4, loss=0.05, drawdown=-0.20, share=0.12),
            _case_tuple('half_size', filled=3, loss=0.03, drawdown=-0.30, share=None)]


def test_the_statistic_table_takes_worst_values_over_complete_cases_with_their_n():
    row, = stress.StressVerdict([]).rows(_cases())
    stats = row['statistics']
    assert {k: v['value'] for k, v in stats.items()} == {
        'worst_loss_fraction': 0.05, 'worst_drawdown': 0.30, 'worst_breach_days': 0,
        'worst_name_reserve_share': 0.12, 'reverse_down_move': 0.1, 'reverse_up_move': 0.3}
    # worst_* n is the smallest filled count over the complete cases (3); the reverse n is the control's (5).
    assert {k: v['n'] for k, v in stats.items()} == {
        'worst_loss_fraction': 3, 'worst_drawdown': 3, 'worst_breach_days': 3,
        'worst_name_reserve_share': 3, 'reverse_down_move': 5, 'reverse_up_move': 5}
    assert row['status'] == 'UNJUDGED' and row['unsupported'] == []
    assert [(c['scenario'], c['selected'], c['filled']) for c in row['cases']] == [
        ('control', 5, 5), ('adverse_price', 5, 4), ('half_size', 5, 3)]
    assert row['reverse'] == {'down_reached': True, 'up_reached': False, 'sessions': 7, 'unavailable': None}


def test_pass_fail_and_inconclusive_are_each_reachable_and_pass_needs_every_statistic_covered():
    assert stress.StressVerdict(CRITERIA).rows(_cases())[0]['status'] == 'PASS'
    failing = [{**CRITERIA[0], 'value': 0.04}, *CRITERIA[1:]]
    assert stress.StressVerdict(failing).rows(_cases())[0]['status'] == 'FAIL'
    thin = [{**CRITERIA[0], 'min_n': 4}, *CRITERIA[1:]]                 # n = 3 < 4
    assert stress.StressVerdict(thin).rows(_cases())[0]['status'] == 'INCONCLUSIVE'
    partial = stress.StressVerdict(CRITERIA[:5]).rows(_cases())[0]       # no criterion on reverse_up_move
    assert partial['status'] == 'INCONCLUSIVE' and partial['uncovered'] == ['reverse_up_move']
    assert stress.StressVerdict(CRITERIA).rows(_cases())[0]['uncovered'] == []
    verdicts = stress.StressVerdict(CRITERIA).rows(_cases())[0]['criteria']
    assert [v['status'] for v in verdicts] == ['PASS'] * 6 and verdicts[0]['observed'] == 0.05


def test_an_unsupported_case_forces_fail_and_is_skipped_by_the_worst_value():
    cases = [*_cases(), _case_tuple('expiry_down_3s', status='unsupported')]
    row, = stress.StressVerdict(CRITERIA).rows(cases)
    assert row['status'] == 'FAIL' and row['unsupported'] == ['expiry_down_3s']
    assert row['statistics']['worst_loss_fraction'] == {'value': 0.05, 'n': 3}
    assert stress.StressVerdict([]).rows(cases)[0]['status'] == 'FAIL'     # even with no criteria


def test_a_method_with_no_control_fill_has_every_statistic_null():
    cases = [_case_tuple('control', filled=0, loss=0.0, drawdown=0.0, share=None, reverse=REVERSE),
             _case_tuple('half_size', filled=0, loss=0.0, drawdown=0.0, share=None)]
    row, = stress.StressVerdict(CRITERIA).rows(cases)
    assert all(s['value'] is None for s in row['statistics'].values())
    assert row['status'] == 'INCONCLUSIVE'
    assert stress.StressVerdict([]).rows(cases)[0]['status'] == 'UNJUDGED'


def test_a_null_reverse_value_is_inconclusive_and_each_method_and_cohort_gets_its_own_row():
    unavailable = {'down_move': None, 'down_reached': None, 'up_move': None, 'up_reached': None,
                   'sessions': 0, 'unavailable': 'window'}
    cases = [_case_tuple('control', reverse=unavailable), _case_tuple('control', reverse=REVERSE, method='n'),
             _case_tuple('control', reverse=REVERSE, cohort='d')]
    rows = stress.StressVerdict(CRITERIA).rows(cases)
    assert [(r['cohort'], r['method']) for r in rows] == [('c', 'm'), ('c', 'n'), ('d', 'm')]
    assert rows[0]['status'] == 'INCONCLUSIVE' and rows[0]['statistics']['reverse_down_move']['value'] is None
    assert rows[1]['status'] == 'PASS'


@pytest.mark.parametrize('criterion, problem', [
    ({'name': 'x', 'stat': 'sharpe', 'op': '<=', 'value': 1, 'min_n': 1}, 'unknown statistic'),
    ({'name': 'x', 'stat': 'worst_drawdown', 'op': '>=', 'value': 0.1, 'min_n': 1}, 'direction'),
    ({'name': 'x', 'stat': 'worst_drawdown', 'op': '>', 'value': 0.1, 'min_n': 1}, 'direction'),
    ({'name': 'x', 'stat': 'reverse_up_move', 'op': '<', 'value': 0.1, 'min_n': 1}, 'direction'),
    ({'name': 'x', 'stat': 'reverse_up_move', 'op': '<=', 'value': 0.1, 'min_n': 1}, 'direction'),
    ({'name': 'x', 'stat': 'worst_drawdown', 'op': '<=', 'value': 0.1}, 'missing'),
    ({'name': 'x', 'stat': 'worst_drawdown', 'op': '<=', 'value': 0.1, 'min_n': 1, 'extra': 1}, 'unknown'),
    ('not an object', 'object')])
def test_a_criterion_outside_the_closed_set_or_direction_is_refused(criterion, problem):
    with pytest.raises(ValueError, match=problem):
        stress.StressVerdict.check([criterion])
    stress.StressVerdict.check([])
    stress.StressVerdict.check(CRITERIA)
    with pytest.raises(ValueError, match='list'):
        stress.StressVerdict.check({})


def test_the_closed_statistic_set_is_the_six_the_contract_names():
    assert stress.STRESS_STATISTICS == (
        'worst_loss_fraction', 'worst_drawdown', 'worst_breach_days', 'worst_name_reserve_share',
        'reverse_down_move', 'reverse_up_move')


# --- the document ----------------------------------------------------------------------------------------------
STRESS = {'lookback': 20, 'reverse': {'moves': [-0.2, -0.05, 0.05, 0.2], 'loss_fraction': 0.05}}


@pytest.mark.parametrize('stress_spec', [
    {'reverse': STRESS['reverse']}, {**STRESS, 'extra': 1}, {**STRESS, 'lookback': 0},
    {**STRESS, 'lookback': True}, {**STRESS, 'lookback': 2.5}, {**STRESS, 'lookback': '20'},
    {**STRESS, 'reverse': {'moves': [-0.2, 0.2]}}, {**STRESS, 'reverse': {**STRESS['reverse'], 'loss_fraction': 0}},
    {**STRESS, 'reverse': {**STRESS['reverse'], 'moves': [0.05, 0.2]}},
    {**STRESS, 'reverse': {**STRESS['reverse'], 'moves': [0.2, -0.2]}},
    {**STRESS, 'reverse': {**STRESS['reverse'], 'moves': [-1.0, 0.2]}},
    {**STRESS, 'reverse': {**STRESS['reverse'], 'typo': 1}}, [], None])
def test_the_stress_entry_is_default_deny(stress_spec):
    with pytest.raises(ValueError):
        stress.StressSpec.from_document({'stress': stress_spec, 'criteria': []})
    spec = stress.StressSpec.from_document({'stress': STRESS, 'criteria': CRITERIA})
    assert spec.lookback == 20 and spec.reverse.params['loss_fraction'] == 0.05


def test_the_v7_deployment_template_pins_the_approved_scenarios_and_carries_only_placeholders():
    from pathlib import Path
    template = json.loads((Path(__file__).resolve().parents[1]
                           / 'configs/mio-deployment-stress-template.json').read_text())
    assert template['schema'] == V7
    assert template['scenarios'] == stress._approved_scenarios(V7)
    assert set(template) == stress._STRESS_DOCUMENT
    assert template['criteria'] == []
    stress.StressSpec.from_document(template)
    entry, = template['cohorts'].values()
    assert set(entry) == stress._RANGE_ENTRY
    pins = [template['results_pins_sha256'], entry['result_sha256'], *entry['csv_sha256'].values(),
            *entry['quote_index_sha256'].values()]
    assert all(str(pin).startswith('REPLACE') for pin in pins)
    assert set(entry['csv_sha256']) == set(stress._CONTROL_CSVS)


# --- a v7 document over a Q1-shaped packet, on disk with every pin the replay checks ----------------------------
def _before(day, count):
    """Return the ``count`` weekdays that precede ``day``, ascending."""
    from datetime import date, timedelta
    found, cursor = [], date.fromisoformat(day)
    while len(found) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            found.append(cursor.isoformat())
    return found[::-1]


def _v7_world(tmp_path, monkeypatch, *, amplitude=0.02, lookback=20, criteria=(), reverse=None, pre=25):
    """Build the packet layout of the v6 test, regenerate the pinned control, and return a v7 document.

    The bars alternate ``100`` and ``100 e^amplitude`` along the pinned session spine, so every window of an even
    number of returns has mean zero and the expiry bar (spine index ``pre + 20``, odd) sits at ``100 e^amplitude``.
    """
    from datetime import date, timedelta
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.driver import RunAttestation

    end = Q1[10]
    tail = (date.fromisoformat(end) + timedelta(days=60)).isoformat()
    calendar = [d for d in Q1 if d < tail]
    spine = [*_before(Q1[0], pre), *calendar]
    document, pins = _v5_tree(tmp_path, overrides={'end_before': end, 'sessions': len(calendar),
                                                    'latest_expiry': Q1_EXPIRY})
    cohort = document['cohorts'][Q1_COHORT]
    config_path = stress.FrozenMIOReplay._run_document(document, Q1_COHORT)
    config = json.loads(config_path.read_text())
    config['name'] = 'frozen-packet'
    config['pipeline']['plan']['uses'] = 'dskit.pipeline.study_protocol:StudyProtocolPlan'
    config['pipeline']['close'] = {'uses': 'dskit.pipeline.study_protocol:StudyPacketClose'}
    study = config['pipeline']['plan']['params']['study']
    study.update(contract='index_options.mio_contract:MIOStudyContract', selected_packet='dev',
                 packets=[{'id': 'dev', 'start': Q1[0], 'end_before': end}])
    study['domain'].update(history_start=spine[0], tail_padding_days=0)
    study['domain']['params']['bridge']['end_before'] = end
    study['domain']['sources']['sessions'] = {'pin': {'id': 'sessions'}, 'files': {'shared': 'days.parquet'}}
    session_file = tmp_path / 'days.parquet'
    pq.write_table(pa.table({'date': spine}), session_file)

    class PinnedSessions:
        def __init__(self, pin):
            assert pin == {'id': 'sessions'}

        def file(self, relative, *, verify):
            return session_file

    def attested_output(self, node, port, document_hash):
        return {'sha256': pins['runs'][Q1_COHORT]['sha256']}

    monkeypatch.setattr(RunAttestation, 'attested_output', attested_output)
    monkeypatch.setattr(stress, 'PinnedPayloadSource', PinnedSessions)
    monkeypatch.setattr(stress, '_bar_rows', lambda binding, symbols, days: {
        s: {d: 100.0 * math.exp(amplitude * (i % 2)) for i, d in enumerate(days)} for s in symbols})

    result_dir = Path(cohort['result_dir'])
    result_path = result_dir / 'result.json'
    original = json.loads(result_path.read_text())
    original['audit']['calendar'] = calendar
    world = SimpleNamespace(document=document, pins=pins, original=original, repo=Path(document['repository_root']),
                            path=Path(pins['evidence']['artifact-index.json']['path']).parents[2] / 'document.json',
                            output=Path(document['output_root']), cohort=cohort)

    def pin_config():
        config_path.write_text(json.dumps(config))
        pins['original_configs'][cohort['config']]['sha256'] = stress._sha(config_path)
        pins['runs'][Q1_COHORT]['document_hash'] = PipelineDocument.from_obj(config).hash

    def pin_result():
        result_path.write_text(json.dumps(original))
        cohort['result_sha256'] = stress._sha(result_path)
        pins['runs'][Q1_COHORT]['sha256'] = cohort['result_sha256']

    def save():
        pins_path = Path(document['results_pins'])
        pins_path.write_text(json.dumps(pins))
        document['results_pins_sha256'] = stress._sha(pins_path)
        world.path.write_text(json.dumps(document))

    world.pin_result, world.save = pin_result, save
    pin_config()
    pin_result()
    # Regenerate the pinned control with the v6 route (no reverse record), then write its extracts.
    document.update(schema='mio-execution-stress/v6', hedge={'block': {}})
    replay = stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    with monkeypatch.context() as patch:
        patch.setattr(stress.FrozenMIOReplay, '_verify_control_csv_rows', lambda self, result: None)
        for packet in original['results']:
            result, _ = replay.run_method(packet, stress._approved_scenarios(V5)[0])
            packet['result'] = json.loads(json.dumps(result))
    pin_result()
    for name, body in _csvs(original['results']).items():
        (result_dir / name).write_text(body)
        cohort['csv_sha256'][name] = stress._sha(result_dir / name)
    del document['hedge']
    document.update(schema=V7, scenarios=stress._approved_scenarios(V7),
                    stress={'lookback': lookback, 'reverse': reverse or STRESS['reverse']},
                    criteria=list(criteria))
    save()
    _commit(world.repo)
    world.replay = lambda: stress.FrozenMIOReplay(Q1_COHORT, document, pins)
    return world


def _fingerprint(result):
    return (result['pnl'], [(t['id'], t['credit'], [leg['quantity'] for leg in t['legs']])
                            for t in result['trades']])


def test_the_control_world_matches_the_hand_worked_known_answer(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    replay = world.replay()
    assert replay.per_method and replay.history[0] < replay.calendar[0] and replay.calendar == Q1[:len(replay.calendar)]
    result, _ = replay.run_method(replay.original['results'][0], stress._approved_scenarios(V7)[0])
    # 3x3 call spread: debit 663.90 (known answer); the expiry bar is 100 e^0.02 so the spread pays 3 x (S - 100) x 100.
    assert result['pnl'] == pytest.approx(-663.9 + 300 * (100 * math.exp(0.02) - 100))


def test_each_new_case_differs_from_the_control_on_a_fixture_built_to_be_sensitive_to_it(tmp_path, q1_loaders,
                                                                                         monkeypatch):
    """Expiry sits inside the 100/105 spread and the call leg shows only 3 of 4 contracts, so every field matters."""
    world = _v7_world(tmp_path, monkeypatch)
    replay = world.replay()
    packet = replay.original['results'][0]
    cases = {c['id']: c for c in stress._approved_scenarios(V7)}
    control, _ = replay.run_method(packet, cases['control'])
    assert len(control['trades']) == 1
    seen = {}
    for name in NEW_EIGHT:
        result, _ = replay.run_method(packet, cases[name])
        seen[name] = _fingerprint(result)
        assert seen[name] != _fingerprint(control), name
    assert seen['quarter_size'][1] == []                                  # 3 // 4 = 0 displayed: not filled
    assert seen['adverse_price_100'][1][0][2] == [3, 3]                   # full depth, worse price only
    assert [seen[n][1][0][2] for n in ('joint_down_severe', 'joint_up_severe')] == [[3, 3], [3, 3]]


def test_the_seven_legacy_cases_run_identically_in_the_v7_and_the_v5_shape(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    replay = world.replay()
    packet = replay.original['results'][1]
    new = stress._approved_scenarios(V7)[:7]
    for old, case in zip(stress._approved_scenarios(V5), new):
        if case['id'] == 'control':
            continue
        assert replay.run_method(packet, case) == replay.run_method(packet, old), case['id']


def test_no_case_shocks_the_pinned_bars_and_the_estimate_reads_them_unshocked(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    replay = world.replay()
    packet = replay.original['results'][0]
    before = deepcopy(replay.bars)
    for case in stress._approved_scenarios(V7):
        replay.run_method(packet, case)
    assert replay.bars == before
    study = replay.study(next(c for c in stress._approved_scenarios(V7) if c['id'] == 'expiry_down_3s'), packet)
    # std of 20 alternating +-0.02 returns (ddof 1) times sqrt(19 sessions to expiry) = 0.02 sqrt(20).
    multiplier = math.exp(-3 * 0.02 * math.sqrt(20))
    assert multiplier == pytest.approx(0.7646568122115767)
    assert study._bars['ABC'][Q1_EXPIRY] == pytest.approx(before['ABC'][Q1_EXPIRY] * multiplier)
    assert study._bars['ABC'][Q1[5]] == before['ABC'][Q1[5]] and study._bars is not replay.bars
    assert replay.new_estimator().scale('ABC', Q1[1], Q1_EXPIRY)[0] == pytest.approx(0.02 * math.sqrt(20 / 19))


@pytest.mark.parametrize('fraction, expected', [
    # Down moves leave the spread worthless: a 663.90 loss on a funding of 10,000 or 11,000. 5% of funding is at most
    # 550, so the smallest declared move already breaches (0.0); 12% is at least 1,200, so none does. Up moves pay
    # the spread in full (a gain), so they never breach.
    (0.05, {'down_move': 0.0, 'down_reached': True, 'up_move': 0.2, 'up_reached': False}),
    (0.12, {'down_move': 0.2, 'down_reached': False, 'up_move': 0.2, 'up_reached': False})])
def test_reverse_stress_on_the_control_is_the_funding_basis_lower_bound(tmp_path, q1_loaders, monkeypatch,
                                                                         fraction, expected):
    reverse = {'moves': [-0.2, -0.05, 0.05, 0.2], 'loss_fraction': fraction}
    world = _v7_world(tmp_path, monkeypatch, reverse=reverse)
    replay = world.replay()
    control = stress._approved_scenarios(V7)[0]
    for packet in replay.original['results']:
        result, _ = replay.run_method(packet, control)
        assert result['reverse_stress'] == {**expected, 'sessions': 19, 'unavailable': None}
    other, _ = replay.run_method(replay.original['results'][0], stress._approved_scenarios(V7)[1])
    assert 'reverse_stress' not in other                                    # the control account only


def test_reverse_stress_input_errors_give_nulls_never_an_exception(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch, amplitude=0.0)              # flat bars: zero basket variance
    replay = world.replay()
    result, _ = replay.run_method(replay.original['results'][0], stress._approved_scenarios(V7)[0])
    assert result['reverse_stress']['down_move'] is None and result['reverse_stress']['up_reached'] is None
    assert 'basket variance' in result['reverse_stress']['unavailable']
    assert result['reverse_stress']['sessions'] == 0


def test_an_unsupported_estimate_is_a_recorded_case_not_a_stopped_run(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch, amplitude=0.0, criteria=CRITERIA)
    output = stress.execute(world.path)
    summary = json.loads((output / 'summary.json').read_text())
    unsupported = ['expiry_down_3s', 'expiry_up_3s', 'market_down_30', 'market_up_20',
                   'joint_down_severe', 'joint_up_severe']
    assert {(r['scenario'], r['status']) for r in summary if r['status'] != 'complete'} == {
        (name, 'unsupported') for name in unsupported}
    row = next(r for r in summary if r['scenario'] == 'market_down_30')
    assert [row[k] for k in ('loss_fraction', 'breach_days', 'worst_name_reserve_share')] == [None] * 3
    verdict = json.loads((output / 'verdict.json').read_text())
    assert [(r['method'], r['status'], r['unsupported']) for r in verdict['rows']] == [
        (m, 'FAIL', unsupported) for m in ('m0', 'm1', 'm2')]
    assert json.loads((output / 'shocks.json').read_text()) == []         # nothing was estimated


def test_v7_execute_publishes_fifteen_cases_with_statistics_shocks_and_a_hashed_verdict(
        tmp_path, q1_loaders, monkeypatch, capsys):
    world = _v7_world(tmp_path, monkeypatch)
    capsys.readouterr()
    output = stress.execute(world.path)
    lines = [line.split()[:3] for line in capsys.readouterr().out.splitlines() if line.startswith(Q1_COHORT + ' ')]
    assert len(lines) == 45 and [c[1] for c in lines[:3]] == ['control'] * 3     # Gate 0 first
    assert all(c[1] != 'control' for c in lines[3:])
    summary = json.loads((output / 'summary.json').read_text())
    assert len(summary) == 45 and {r['status'] for r in summary} == {'complete'}
    assert all({'loss_fraction', 'breach_days', 'worst_name_reserve_share'} <= set(r) for r in summary)
    control = next(r for r in summary if r['scenario'] == 'control' and r['method'] == 'm0')
    assert control['loss_fraction'] == pytest.approx(-control['final_profit_usd'] / control['contributions_usd'])
    assert control['breach_days'] == 0 and 0 < control['worst_name_reserve_share'] < 0.2
    # The expiry bar of every shocked case, against the same case's unshocked bar.
    ratios = {(r['scenario']): r['stressed_expiry_underlying'] / r['original_expiry_underlying']
              for r in json.loads((output / 'dispositions.json').read_text())
              if r['method'] == 'm0' and r['stressed_expiry_underlying'] is not None}
    expected = {'control': 1, 'adverse_price': 1, 'expiry_down_15': 0.85, 'expiry_up_15': 1.15,
                'expiry_down_3s': 0.7646568122115767, 'expiry_up_3s': 1.307776225922519,
                'market_down_30': 0.7, 'market_up_20': 1.2, 'joint_down_severe': 0.7, 'joint_up_severe': 1.2,
                'adverse_price_100': 1}
    assert {k: v for k, v in ratios.items() if k in expected} == pytest.approx(expected)
    shocks = json.loads((output / 'shocks.json').read_text())
    assert len(shocks) == 18 and {r['case'] for r in shocks} == {
        'expiry_down_3s', 'expiry_up_3s', 'market_down_30', 'market_up_20', 'joint_down_severe', 'joint_up_severe'}
    vol = next(r for r in shocks if r['case'] == 'expiry_down_3s' and r['method'] == 'm1')
    assert vol == {'cohort': Q1_COHORT, 'method': 'm1', 'case': 'expiry_down_3s', 'symbol': 'ABC',
                   'expiry': Q1_EXPIRY, 'window_end': Q1[0], 'sessions_to_expiry': 19,
                   'std': pytest.approx(0.02 * math.sqrt(20 / 19)), 'beta': None,
                   'multiplier': pytest.approx(0.7646568122115767)}
    market = next(r for r in shocks if r['case'] == 'market_up_20')
    assert market['beta'] == pytest.approx(1.0) and market['std'] is None and market['multiplier'] == pytest.approx(1.2)
    verdict = json.loads((output / 'verdict.json').read_text())
    assert [(r['cohort'], r['method'], r['status']) for r in verdict['rows']] == [
        (Q1_COHORT, m, 'UNJUDGED') for m in ('m0', 'm1', 'm2')]
    assert all(len(r['cases']) == 15 and r['unsupported'] == [] for r in verdict['rows'])
    assert json.loads((output / Q1_COHORT / 'control/m0.json').read_text())['result']['reverse_stress']['sessions'] == 19
    assert '## Verdict' in (output / 'report.md').read_text()
    manifest = json.loads((output / 'pins.json').read_text())
    for name in ('verdict.json', 'shocks.json'):
        assert manifest['files'][name]['sha256'] == stress._sha(output / name)
    assert manifest['evidence_kind'] == stress._PROXY_NOTE


def test_v7_verdict_statuses_follow_the_pre_registered_criteria(tmp_path, q1_loaders, monkeypatch):
    loose = [{'name': 'loss', 'stat': 'worst_loss_fraction', 'op': '<=', 'value': 0.9, 'min_n': 0},
             {'name': 'drawdown', 'stat': 'worst_drawdown', 'op': '<=', 'value': 0.9, 'min_n': 0},
             {'name': 'breach', 'stat': 'worst_breach_days', 'op': '<=', 'value': 0, 'min_n': 0},
             {'name': 'name', 'stat': 'worst_name_reserve_share', 'op': '<=', 'value': 0.9, 'min_n': 0},
             {'name': 'down', 'stat': 'reverse_down_move', 'op': '>=', 'value': 0, 'min_n': 1},
             {'name': 'up', 'stat': 'reverse_up_move', 'op': '>=', 'value': 0, 'min_n': 1}]
    outcomes = {}
    for label, criteria in (('pass', loose), ('fail', [{**loose[2], 'value': -1}, *loose[:2], *loose[3:]]),
                            ('thin', [{**loose[0], 'min_n': 1000}, *loose[1:]]), ('partial', loose[:5])):
        world = _v7_world(tmp_path / label, monkeypatch, criteria=criteria)
        output = stress.execute(world.path)
        outcomes[label] = {r['status'] for r in json.loads((output / 'verdict.json').read_text())['rows']}
    assert outcomes == {'pass': {'PASS'}, 'fail': {'FAIL'}, 'thin': {'INCONCLUSIVE'}, 'partial': {'INCONCLUSIVE'}}


def test_a_criterion_naming_an_unknown_statistic_refuses_before_any_replay(tmp_path, q1_loaders, monkeypatch):
    bad = [{'name': 'x', 'stat': 'sharpe', 'op': '<=', 'value': 1, 'min_n': 1}]
    world = _v7_world(tmp_path, monkeypatch)
    world.document['criteria'] = bad
    world.save()
    monkeypatch.setattr(stress, 'FrozenMIOReplay', lambda *a: pytest.fail('a replay was opened'))
    with pytest.raises(ValueError, match='unknown statistic'):
        stress.execute(world.path)
    assert not world.output.exists()


def test_gate_0_mismatch_stops_the_run_and_publishes_nothing(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    world.original['results'][0]['result']['pnl'] += 1.0
    world.pin_result()
    world.save()
    with pytest.raises(ValueError, match='zero-shock control mismatch'):
        stress.execute(world.path)
    assert not world.output.exists()


def test_v7_checks_the_worktree_is_clean_before_any_replay(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    (world.repo / 'stray.txt').write_text('uncommitted')
    monkeypatch.setattr(stress, 'FrozenMIOReplay', lambda *a: pytest.fail('a replay was opened'))
    with pytest.raises(ValueError, match='worktree must be clean'):
        stress.execute(world.path)
    assert not world.output.exists()


def test_v7_keeps_the_v5_refusal_of_the_protected_window(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    world.cohort.update(end_before='2024-01-02', latest_expiry='2024-01-02')
    world.save()
    monkeypatch.setattr(stress, 'FrozenMIOReplay', lambda *a: pytest.fail('a replay was opened'))
    with pytest.raises(ValueError, match='protected window'):
        stress.execute(world.path)
    assert not world.output.exists()


def test_v7_refuses_a_run_document_without_the_packet_layout_by_name(tmp_path, q1_loaders):
    document, pins = _v5_tree(tmp_path)
    document.update(schema=V7, scenarios=stress._approved_scenarios(V7), stress=STRESS, criteria=[])
    with pytest.raises(ValueError, match='MIOStudyContract'):
        stress.FrozenMIOReplay(Q1_COHORT, document, pins)


def test_v7_refuses_a_lookback_below_the_beta_owners_minimum_pairs(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch, lookback=19)               # the run's correlation_min_pairs is 20
    with pytest.raises(ValueError, match='correlation_min_pairs'):
        world.replay()


@pytest.mark.parametrize('key, value', [('hedge', {'block': {}}), ('stress', None), ('criteria', 'x')])
def test_v7_document_keys_are_closed(tmp_path, q1_loaders, monkeypatch, key, value):
    world = _v7_world(tmp_path, monkeypatch)
    if key == 'hedge':
        world.document['hedge'] = value
    else:
        world.document[key] = value
    world.save()
    with pytest.raises(ValueError):
        stress.execute(world.path)
    assert not world.output.exists()


def test_a_v7_scenario_list_that_differs_from_the_approved_set_refuses(tmp_path, q1_loaders, monkeypatch):
    world = _v7_world(tmp_path, monkeypatch)
    for mutate in (lambda c: c.reverse(), lambda c: c.pop(),
                   lambda c: c[8].update(size_divisor=2),
                   lambda c: c[13]['expiry_shock'].update(move=-0.31)):
        cases = stress._approved_scenarios(V7)
        mutate(cases)
        world.document['scenarios'] = cases
        world.save()
        with pytest.raises(ValueError, match='scenario values differ'):
            stress.execute(world.path)
    assert not world.output.exists()


def test_v5_outputs_carry_no_v7_file_and_no_v7_column(tmp_path, q1_loaders):
    results = _q1_original_results(tmp_path)
    _v5_tree(tmp_path / 'good', results=results)
    output = stress.execute(tmp_path / 'good/evidence/document.json')
    assert not (output / 'verdict.json').exists() and not (output / 'shocks.json').exists()
    summary = json.loads((output / 'summary.json').read_text())
    assert not {'loss_fraction', 'breach_days', 'worst_name_reserve_share'} & set(summary[0])
    assert '## Verdict' not in (output / 'report.md').read_text()
    assert 'reverse_stress' not in json.loads((output / Q1_COHORT / 'control/m0.json').read_text())['result']
