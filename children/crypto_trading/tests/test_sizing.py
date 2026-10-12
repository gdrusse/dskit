"""The fractional-Kelly sizing backtest: sizes against an independent optimum, money conservation, the venue rows,
no look-ahead, default-deny params, and the pins that tie configs/run-w4-kelly.json to run-w4-blend.json.

Expectations are RESTATED on purpose (the utility being maximised, the joint probabilities, the fee cross-check through
``FeeColumns``): an assertion that read its answer from the code it checks would assert nothing."""

import json
import math
import os
import random

import pytest

pytest.importorskip("pyomo")
pytest.importorskip("highspy")
np = pytest.importorskip("numpy")

from dskit.pipeline.document import load_document
from dskit.pipeline.fee_mechanics import fee_model_from_spec
from dskit.pipeline.node import NodeContext
from dskit.pipeline.planner import plan

import crypto_trading  # noqa: F401  (import = registration)
from crypto_trading import fields as f
from crypto_trading.fees import FeeColumns
from crypto_trading.sizing import (
    DEFAULTS, ExecutableEdge, KalshiBinaryKellyMIO, KalshiKellyBacktest, LabelLedger, LatentCopula, LookAheadError,
    BinaryQuote, SettlementBook, knob, name_of, split_name)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
MIN = 60_000
DAY = 86_400_000
T0 = 1_785_542_400_000 + 20 * DAY          # a UTC midnight inside W4
W4_END_MS = 1_789_430_400_000
SPEC = {"mechanic": "probability_quadratic", "rounding": {"policy": "ceil_to_tick", "tick": 0.01}}
RATE = 0.07


def load(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


def mio_params(gamma=2.0, **over):
    params = {"risk_aversion_gamma": gamma, "n_tangents": 128, "n_scenarios_max": 4, "cvar_alpha": 0.95,
              "cvar_limit": None, "cardinality": None, "min_ticket": 0.0, "fee_model": SPEC,
              "solver": "appsi_highs"}
    params.update(over)
    return params


def backtest_params(gamma=2.0, **over):
    base = {"fair_field": "fair_blend", "starting_bankroll": 1000.0, "lead_min_minutes": 8, "min_edge": 0.02,
            "max_contracts_per_order": 100, "market_cap_frac": 0.1, "copula_rho": 0.8}
    return mio_params(gamma, **{**base, **over})


def quote(ticker="T", bid=0.38, ask=0.40, fair=0.55, close=T0 + 15 * MIN, lead=10, series="S"):
    return BinaryQuote(ticker, series, close, close - lead * MIN, lead, bid, ask, fair, RATE,
                 math.ceil(round(RATE * ask * (1 - ask) * 100, 9)) / 100,
                 math.ceil(round(RATE * (1 - bid) * bid * 100, 9)) / 100)


def size(quotes, cash=1000.0, held=None, candidates=(), **over):
    node = KalshiBinaryKellyMIO("size", mio_params(**over))
    return node.run(None, {"asof_ms": 0, "cash": cash, "held": held or {}, "quotes": {q.ticker: q for q in quotes},
                           "candidates": list(candidates)})


# --- decision rows through the child's own fee node -----------------------------------------------------------------


def priced(raw):
    """Add the fee columns with the shipped FeeColumns node (one-contract fees), as run-w4-blend.json does."""
    schedules = [{f.SERIES: s, f.FEE_TYPE: "quadratic", f.FEE_MULTIPLIER: 1.0, f.RETRIEVED: "2026-08-01T00:00:00Z",
                  f.RETRIEVED_MS: 0} for s in sorted({r[f.SERIES] for r in raw})]
    node = FeeColumns("fees", {"fee_types": {"quadratic": {"base_rate": RATE, "model": SPEC}}, "contracts": 1})
    return node.run(None, {"records": raw, "schedules": schedules})["records"]


def market_rows(ticker, series, close_ms, by_lead, label):
    rows = []
    for lead, (bid, ask, fair) in by_lead.items():
        rows.append({f.TICKER: ticker, f.EVENT: ticker, f.SERIES: series, f.CLOSE_MS: close_ms, f.LABEL: label,
                     f.DECISION_MS: close_ms - lead * MIN, f.LEAD: lead, f.YES_BID: bid, f.YES_ASK: ask,
                     f.TWO_SIDED: True, "fair_blend": fair, "fair_blend_status": "ok"})
    return rows


def random_world(n_markets=6, seed=3, flip=None):
    rng = random.Random(seed)
    raw = []
    for k in range(n_markets):
        close = T0 + (k + 1) * 15 * MIN
        for series in ("KXBTC15M", "KXETH15M"):
            mid = rng.uniform(0.3, 0.7)
            fair = min(max(mid + rng.uniform(-0.1, 0.12), 0.05), 0.95)
            label = int(rng.random() < fair)
            if flip == (k, series):
                label = 1 - label
            by_lead = {lead: (round(mid - 0.01, 2), round(mid + 0.01, 2), min(max(fair + rng.uniform(-0.02, 0.02), 0.02), 0.98))
                       for lead in range(14, 1, -1)}
            raw += market_rows(f"{series}-{k}", series, close, by_lead, label)
    return priced(raw)


def run_backtest(rows, tmp_path, cls=KalshiKellyBacktest, **over):
    node = cls("kelly", backtest_params(**over))
    assert cls.validate_params(node.params) == []
    return node.run(NodeContext(name="kelly", asof="2026-09-15", run_dir=str(tmp_path)), {"records": rows})


# --- the solve against an independent optimum ------------------------------------------------------------------------


def utility(wealth, gamma):
    return np.log(wealth) if gamma == 1.0 else wealth ** (1.0 - gamma) / (1.0 - gamma)


@pytest.mark.parametrize("gamma", [1.0, 2.0, 4.0])
@pytest.mark.parametrize("side,bid,ask,fair", [("yes", 0.38, 0.40, 0.55), ("no", 0.60, 0.62, 0.45)])
def test_one_market_size_matches_the_brute_force_optimum(gamma, side, bid, ask, fair):
    cap_orders = 800
    q = quote(bid=bid, ask=ask, fair=fair)
    out = size([q], candidates=[name_of("T", side)], gamma=gamma, max_contracts_per_order=cap_orders, market_cap_frac=1.0)
    got = out["trades"].get(name_of("T", side), {"buy": 0})["buy"]
    price, win = q.price(side), q.win_prob(side)
    unit = fee_model_from_spec(SPEC).order_fee(cap_orders, price, RATE) / cap_orders
    n = np.arange(0, cap_orders + 1)
    value = win * utility(1000.0 + n * (1 - price - unit), gamma) + (1 - win) * utility(1000.0 - n * (price + unit), gamma)
    best = int(np.argmax(value))
    regret = (value[best] - value[got]) / (value[best] - value[0])
    assert best > 20 and regret < 1e-3, (got, best)
    assert abs(got - best) <= 0.05 * best + 2, "the same size as the independent optimum, to the tangent grid's resolution"


def test_two_correlated_markets_match_the_brute_force_optimum_under_an_independent_copula():
    stats = pytest.importorskip("scipy.stats")
    rho, gamma, room = 0.8, 2.0, 160
    quotes = [quote("A", 0.38, 0.40, 0.55), quote("B", 0.42, 0.44, 0.58)]
    out = size(quotes, candidates=["A|yes", "B|yes"], gamma=gamma, copula_rho=rho, max_contracts_per_order=room,
               market_cap_frac=1.0)
    cut = [stats.norm.ppf(1 - q.fair) for q in quotes]
    both = stats.multivariate_normal.cdf([-cut[0], -cut[1]], mean=[0, 0], cov=[[1, rho], [rho, 1]])
    weights = {(1, 1): both, (1, 0): quotes[0].fair - both, (0, 1): quotes[1].fair - both}
    weights[(0, 0)] = 1 - sum(weights.values())
    unit = [fee_model_from_spec(SPEC).order_fee(room, q.yes_ask, RATE) / room for q in quotes]
    na, nb = np.meshgrid(np.arange(room + 1), np.arange(room + 1), indexing="ij")
    value = 0.0
    for (ya, yb), w in weights.items():
        wealth = 1000.0 - na * (0.40 + unit[0]) - nb * (0.44 + unit[1]) + na * ya + nb * yb
        value = value + w * utility(wealth, gamma)
    ia, ib = np.unravel_index(np.argmax(value), value.shape)
    got = (out["trades"].get("A|yes", {"buy": 0})["buy"], out["trades"].get("B|yes", {"buy": 0})["buy"])
    regret = (value[ia, ib] - value[got]) / (value[ia, ib] - value[0, 0])
    assert regret < 1e-3 and abs(got[0] - ia) <= 0.1 * ia + 3 and abs(got[1] - ib) <= 0.1 * ib + 3, (got, (ia, ib))


@pytest.mark.parametrize("rho", [0.0, 0.5, 0.8, 0.95])
def test_the_copula_keeps_the_marginals_and_the_closed_form_joint(rho):
    copula = LatentCopula(rho)
    w = copula.weights([0.5, 0.5])
    assert w.sum() == pytest.approx(1.0, abs=1e-12)
    assert w[3] == pytest.approx(0.25 + math.asin(rho) / (2 * math.pi), abs=1e-6)       # both YES
    probs = [0.3, 0.7, 0.45]
    w = copula.weights(probs)
    states = copula.outcomes(3)
    for j, p in enumerate(probs):
        assert (w * states[:, j]).sum() == pytest.approx(p, abs=1e-6)


def test_independence_is_exactly_the_product_of_the_marginals():
    w = LatentCopula(0.0).weights([0.3, 0.8])
    assert list(w) == pytest.approx([0.7 * 0.2, 0.3 * 0.2, 0.7 * 0.8, 0.3 * 0.8], abs=1e-15)


def test_more_markets_than_scenarios_refuses_by_name():
    quotes = [quote(f"M{i}") for i in range(3)]
    with pytest.raises(ValueError, match="n_scenarios_max"):
        size(quotes, candidates=[f"M{i}|yes" for i in range(3)])


def test_a_confidence_haircut_never_buys_more():
    plain = size([quote()], candidates=["T|yes"], market_cap_frac=1.0, max_contracts_per_order=800)
    careful = size([quote()], candidates=["T|yes"], market_cap_frac=1.0, max_contracts_per_order=800,
                   uncertainty_frac=0.5, uncertainty_budget=1.0)
    assert 0 < careful["trades"]["T|yes"]["buy"] < plain["trades"]["T|yes"]["buy"]
    assert "robust_protection" in careful["metrics"]


# --- fees ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("contracts", [1, 7, 100])
@pytest.mark.parametrize("bid,ask", [(0.38, 0.40), (0.03, 0.05), (0.60, 0.62)])
def test_fees_are_the_fee_node_s_for_the_same_order_size(contracts, bid, ask):
    raw = market_rows("T", "S", T0 + 15 * MIN, {10: (bid, ask, 0.5)}, 1)
    schedule = [{f.SERIES: "S", f.FEE_TYPE: "quadratic", f.FEE_MULTIPLIER: 1.0, f.RETRIEVED: "x", f.RETRIEVED_MS: 0}]
    node = FeeColumns("fees", {"fee_types": {"quadratic": {"base_rate": RATE, "model": SPEC}}, "contracts": contracts})
    row = node.run(None, {"records": raw, "schedules": schedule})["records"][0]
    book = SettlementBook(1000.0, fee_model_from_spec(SPEC))
    q = quote(bid=bid, ask=ask)
    assert book.order_cost(q, "yes", contracts)[1] == pytest.approx(row[f.FEE_BUY_YES] * contracts, abs=1e-9)
    assert book.order_cost(q, "no", contracts)[1] == pytest.approx(row[f.FEE_BUY_NO] * contracts, abs=1e-9)


def test_the_program_charges_the_per_contract_fee_of_a_full_order_and_the_book_charges_the_exact_one():
    q = quote(bid=0.38, ask=0.40)
    node = KalshiBinaryKellyMIO("size", mio_params(max_contracts_per_order=100))
    rate_row = priced(market_rows("T", "S", q.close_ms, {10: (0.38, 0.40, 0.5)}, 1))[0]
    schedule = [{f.SERIES: "S", f.FEE_TYPE: "quadratic", f.FEE_MULTIPLIER: 1.0, f.RETRIEVED: "x", f.RETRIEVED_MS: 0}]
    hundred = FeeColumns("fees", {"fee_types": {"quadratic": {"base_rate": RATE, "model": SPEC}}, "contracts": 100}) \
        .run(None, {"records": [{**rate_row}], "schedules": schedule})["records"][0]
    assert node.unit_fee(q, "yes") == pytest.approx(hundred[f.FEE_BUY_YES], abs=1e-12)
    book = SettlementBook(1000.0, fee_model_from_spec(SPEC))
    assert book.buy(q, "yes", 3)["fee"] == pytest.approx(0.06)                  # 3 * 0.07 * 0.4 * 0.6 = 0.0504 -> 6 cents


# --- the candidate rule ----------------------------------------------------------------------------------------------


def test_the_gate_takes_the_edge_at_the_touch_over_the_fee_and_the_lead_floor():
    gate = ExecutableEdge(8, 0.02)
    yes = quote(bid=0.38, ask=0.40, fair=0.65)                                  # 0.65 - 0.40 - 0.02 = 0.23
    assert gate.sides(yes) == ["yes"]
    assert gate.sides(quote(bid=0.38, ask=0.40, fair=0.35)) == [], "NO wins 0.65 at 0.62 less a 2-cent fee: 1 cent"
    assert gate.sides(quote(bid=0.38, ask=0.40, fair=0.30)) == ["no"]
    assert gate.sides(quote(fair=0.43)) == [] and gate.sides(quote(fair=0.45)) == ["yes"]   # 1 and 3 cents over the fee vs a 2-cent margin
    assert gate.sides(quote(fair=0.65, lead=7)) == []
    assert gate.sides(quote(fair=0.65, lead=8)) == ["yes"]


def test_a_row_that_is_not_tradeable_gives_no_quote():
    row = priced(market_rows("T", "S", T0 + 15 * MIN, {10: (0.38, 0.40, 0.6)}, 1))[0]
    assert BinaryQuote.from_row(row, "fair_blend") is not None
    for broken in ({f.TWO_SIDED: False}, {"fee_status": "no_schedule"}, {"fair_blend": None}, {"fair_blend": 1.2},
                   {f.FEE_BUY_YES: None}):
        assert BinaryQuote.from_row({**row, **broken}, "fair_blend") is None
    assert not hasattr(BinaryQuote.from_row(row, "fair_blend"), "label")


# --- the venue rows --------------------------------------------------------------------------------------------------


def test_no_position_on_both_sides_of_one_contract():
    out = size([quote(bid=0.50, ask=0.52, fair=0.30)], held={"T|yes": 5}, candidates=["T|no"])
    assert "T|no" not in out["trades"] and "T|no" not in out["target"]
    both = size([quote(bid=0.50, ask=0.52, fair=0.30)], candidates=["T|yes", "T|no"], max_contracts_per_order=800,
                market_cap_frac=1.0)
    assert len([n for n in both["target"] if n.startswith("T|")]) == 1


def test_the_exposure_cap_holds_and_binds():
    q = quote(bid=0.48, ask=0.50, fair=0.90)
    out = size([q], candidates=["T|yes"], market_cap_frac=0.1, max_contracts_per_order=10_000)
    bought = out["trades"]["T|yes"]["buy"]
    assert bought * 0.50 <= 0.1 * 1000.0 + 1e-9 and bought * 0.50 > 0.1 * 1000.0 - 0.50


def test_held_contracts_count_against_the_cap_and_are_not_bought_past_it():
    q = quote(bid=0.48, ask=0.50, fair=0.90)
    partial = size([q], held={"T|yes": 100}, candidates=["T|yes"], market_cap_frac=0.1, max_contracts_per_order=10_000)
    wealth = 1000.0 + 100 * 0.50
    assert (100 + partial["trades"]["T|yes"]["buy"]) * 0.50 <= 0.1 * wealth + 1e-9
    over = size([q], held={"T|yes": 300}, candidates=["T|yes"], market_cap_frac=0.1, max_contracts_per_order=10_000)
    assert over["trades"] == {} and over["target"] == {"T|yes": 300}


def test_the_order_cap_and_the_candidate_list_bind():
    q = quote(bid=0.48, ask=0.50, fair=0.90)
    assert size([q], candidates=["T|yes"], market_cap_frac=1.0, max_contracts_per_order=40)["trades"]["T|yes"]["buy"] == 40
    out = size([q], held={"T|yes": 10}, candidates=[])
    assert out["trades"] == {} and out["target"] == {"T|yes": 10}, "a held position is never sold and never added to off the list"


# --- the book --------------------------------------------------------------------------------------------------------


def test_the_book_conserves_money_through_a_fill_and_a_settlement():
    rows = [{f.TICKER: "T", f.CLOSE_MS: 1000, f.LABEL: 1}]
    book = SettlementBook(100.0, fee_model_from_spec(SPEC))
    q = quote(close=1000)
    fill = book.buy(q, "yes", 10)
    assert book.cash == pytest.approx(100.0 - 10 * 0.40 - fill["fee"]) and book.equity() == pytest.approx(100.0 - fill["fee"])
    assert book.settle(999, LabelLedger(rows)) == [] and book.held() == {"T|yes": 10}
    (settled,) = book.settle(1000, LabelLedger(rows))
    assert book.cash == pytest.approx(100.0 - fill["fee"] - 4.0 + 10.0) and settled["pnl"] == pytest.approx(10 - 4.0 - fill["fee"])
    assert book.positions == {}


def test_the_book_refuses_an_order_the_cash_cannot_pay():
    with pytest.raises(ValueError, match="cash"):
        SettlementBook(1.0, fee_model_from_spec(SPEC)).buy(quote(), "yes", 10)


def test_the_ledger_refuses_a_label_before_the_close():
    ledger = LabelLedger([{f.TICKER: "T", f.CLOSE_MS: 1000, f.LABEL: 0}])
    with pytest.raises(LookAheadError):
        ledger.reveal("T", 999)
    assert ledger.reveal("T", 1000) == 0


def test_names_round_trip():
    assert split_name(name_of("KXBTC15M-26AUG010015-15", "no")) == ("KXBTC15M-26AUG010015-15", "no")
    with pytest.raises(ValueError):
        split_name("T|maybe")


# --- the sequential run ----------------------------------------------------------------------------------------------


def test_settlement_accounting_conserves_money_end_to_end(tmp_path):
    out = run_backtest(random_world(), tmp_path)
    label = {r[f.TICKER]: r[f.LABEL] for r in random_world()}
    for strategy in ("kelly", "flat"):
        fills = [t for t in out["trades"] if t["strategy"] == strategy]
        assert fills, strategy
        paid = sum(t["contracts"] * t["price"] + t["fee"] for t in fills)
        got = sum(t["contracts"] * (label[t["ticker"]] if t["side"] == "yes" else 1 - label[t["ticker"]]) for t in fills)
        final = out["wealth"][-1]
        assert final[f"{strategy}_wealth"] == pytest.approx(1000.0 - paid + got, abs=1e-9)
        assert final[f"{strategy}_open_cost"] == 0.0 and final[f"{strategy}_cash"] == final[f"{strategy}_wealth"]
        assert out["summary"][strategy]["final_wealth"] == pytest.approx(final[f"{strategy}_wealth"])
        assert out["summary"][strategy]["pnl"] == pytest.approx(got - paid, abs=1e-9)
    assert out["summary"]["kelly"]["log_growth"] == pytest.approx(math.log(out["wealth"][-1]["kelly_wealth"] / 1000.0))
    assert os.path.exists(tmp_path / "artifacts" / "kelly" / "kelly_summary.json")


def test_the_flat_rule_is_one_contract_on_every_candidate_with_the_one_contract_fee(tmp_path):
    raw = market_rows("T1", "S", T0 + 15 * MIN, {10: (0.38, 0.40, 0.65), 9: (0.38, 0.40, 0.43)}, 1) \
        + market_rows("T2", "S", T0 + 15 * MIN, {10: (0.38, 0.40, 0.65), 7: (0.38, 0.40, 0.90)}, 0)
    out = run_backtest(priced(raw), tmp_path)
    flat = [t for t in out["trades"] if t["strategy"] == "flat"]
    assert [(t["ticker"], t["lead_minutes"], t["contracts"], t["price"], t["fee"]) for t in flat] == \
        [("T1", 10, 1, 0.40, 0.02), ("T2", 10, 1, 0.40, 0.02)], "lead 9 has no edge and lead 7 is under the floor"
    assert out["summary"]["flat"]["pnl"] == pytest.approx((1 - 0.40 - 0.02) + (0 - 0.40 - 0.02))
    assert out["summary"]["flat"]["pnl_by_lead"] == {"10": {"orders": 2, "contracts": 2, "pnl": pytest.approx(0.16)}}


class SpyMIO(KalshiBinaryKellyMIO):
    calls = []

    def run(self, ctx, inputs):
        out = super().run(ctx, inputs)
        type(self).calls.append((inputs, out))
        return out


class SpyBacktest(KalshiKellyBacktest):
    _SOLVER = SpyMIO


def _walk(value):
    """Yield every key and attribute name inside a nested value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk(item)
    elif hasattr(value, "__dataclass_fields__"):
        yield from value.__dataclass_fields__


def test_the_solver_sees_no_label_no_settled_market_and_never_both_sides_or_past_the_cap(tmp_path):
    SpyMIO.calls = []
    out = run_backtest(random_world(8, seed=11), tmp_path, cls=SpyBacktest)
    assert SpyMIO.calls and out["summary"]["counts"]["solves"] == len(SpyMIO.calls)
    for inputs, result in SpyMIO.calls:
        assert "label" not in set(_walk(inputs)), "a decision never sees an outcome"
        quotes, held = inputs["quotes"], inputs["held"]
        assert all(quotes[split_name(n)[0]].close_ms > inputs["asof_ms"] for n in held), "settled markets are gone"
        assert all(q.decision_ms <= inputs["asof_ms"] for q in quotes.values())
        price = lambda n: quotes[split_name(n)[0]].price(split_name(n)[1])
        wealth = inputs["cash"] + sum(price(n) * c for n, c in held.items())
        by_market = {}
        for name, count in result["target"].items():
            ticker = split_name(name)[0]
            by_market.setdefault(ticker, []).append(name)
            assert count * price(name) <= max(0.1 * wealth, held.get(name, 0) * price(name)) + 1e-9
        assert all(len(names) == 1 for names in by_market.values()), "one side per contract"
        assert all(trade["sell"] == 0 for trade in result["trades"].values())


def test_decisions_before_a_market_closes_do_not_depend_on_its_outcome(tmp_path):
    base = run_backtest(random_world(8, seed=5), tmp_path / "a")
    flipped = run_backtest(random_world(8, seed=5, flip=(3, "KXBTC15M")), tmp_path / "b")
    close = T0 + 4 * 15 * MIN
    key = lambda t: (t["strategy"], t["decision_ms"], t["ticker"], t["side"], t["contracts"], t["price"], t["fee"])
    before = lambda out: [key(t) for t in out["trades"] if t["decision_ms"] < close]
    assert before(base) and before(base) == before(flipped)
    assert base["wealth"][-1]["kelly_wealth"] != flipped["wealth"][-1]["kelly_wealth"], "the outcome does reach the money"


def test_neither_strategy_ever_holds_both_sides_and_the_flat_rule_counts_the_side_it_skips(tmp_path):
    rows = priced(market_rows("T", "S", T0 + 15 * MIN, {14: (0.48, 0.50, 0.75), 13: (0.50, 0.52, 0.25)}, 1))
    out = run_backtest(rows, tmp_path)
    for strategy in ("kelly", "flat"):
        assert {t["side"] for t in out["trades"] if t["strategy"] == strategy} == {"yes"}, strategy
    assert out["summary"]["counts"]["flat_opposite_skipped"] == 1
    world = run_backtest(random_world(10, seed=21), tmp_path / "w")
    sides = {}
    for t in world["trades"]:
        sides.setdefault((t["strategy"], t["ticker"]), set()).add(t["side"])
    assert all(len(v) == 1 for v in sides.values())


def test_the_flat_rule_skips_and_counts_an_order_its_cash_cannot_pay(tmp_path):
    raw = market_rows("T1", "S", T0 + 15 * MIN, {10: (0.38, 0.40, 0.65)}, 1) \
        + market_rows("T2", "S", T0 + 15 * MIN, {10: (0.38, 0.40, 0.65)}, 1)
    out = run_backtest(priced(raw), tmp_path, starting_bankroll=0.45)
    assert [t["ticker"] for t in out["trades"] if t["strategy"] == "flat"] == ["T1"]
    assert out["summary"]["counts"]["flat_cash_skipped"] == 1 and out["summary"]["flat"]["orders"] == 1


def test_three_drawdowns_cost_basis_mark_to_market_and_worst_case_on_a_hand_computed_path(tmp_path):
    # Flat buys YES at 0.50 (lead 14) and at 0.30 (lead 13), a 2-cent fee each; a lead-12 quote of 0.90 / 0.92 is a NO
    # candidate the flat rule skips (it holds YES) and is the newest quote then; YES settles. Equity after each tick, from 1000:
    #   cost basis 999.98, 999.96 | worst case 999.48, 999.16 | mark to the mid 999.97, 999.74 (not the later 0.91).
    raw = market_rows("T", "S", T0 + 15 * MIN,
                      {14: (0.48, 0.50, 0.75), 13: (0.28, 0.30, 0.75), 12: (0.90, 0.92, 0.75)}, 1)
    out = run_backtest(priced(raw), tmp_path)
    flat = out["summary"]["flat"]
    assert flat["max_drawdown_cost_usd"] == pytest.approx(0.04) and flat["max_drawdown_mtm_usd"] == pytest.approx(0.26)
    assert flat["max_drawdown_worst_usd"] == pytest.approx(0.84)
    assert flat["max_drawdown_mtm"] == pytest.approx(0.26 / 1000.0)
    by_time = {w["asof_ms"]: w for w in out["wealth"]}
    after = by_time[T0 + 15 * MIN - 13 * MIN]
    assert (after["flat_wealth"], after["flat_worst"], after["flat_mtm"]) == pytest.approx((999.96, 999.16, 999.74))
    last = out["wealth"][-1]
    assert last["flat_wealth"] == last["flat_worst"] == last["flat_mtm"] == pytest.approx(1001.16)
    for name in ("max_drawdown_cost", "max_drawdown_mtm", "max_drawdown_worst"):
        assert 0.0 <= out["summary"]["kelly"][name] < 1.0


def test_a_mark_never_uses_a_quote_from_after_the_instant(tmp_path):
    base = market_rows("T", "S", T0 + 15 * MIN, {14: (0.48, 0.50, 0.75), 13: (0.28, 0.30, 0.75), 12: (0.90, 0.92, 0.75)}, 1)
    changed = market_rows("T", "S", T0 + 15 * MIN, {14: (0.48, 0.50, 0.75), 13: (0.28, 0.30, 0.75), 12: (0.02, 0.04, 0.75)}, 1)
    early = lambda out: [w for w in out["wealth"] if w["asof_ms"] <= T0 + 15 * MIN - 13 * MIN]
    assert early(run_backtest(priced(base), tmp_path / "a")) == early(run_backtest(priced(changed), tmp_path / "b"))


def test_a_knob_has_one_owner():
    assert not hasattr(KalshiBinaryKellyMIO, "_knob") and not hasattr(KalshiKellyBacktest, "_knob")
    assert knob({}, "min_edge") == DEFAULTS["min_edge"] and knob({"min_edge": 0.5}, "min_edge") == 0.5


def test_a_quote_only_refreshes_to_the_newest_row_of_the_market(tmp_path):
    SpyMIO.calls = []
    rows = priced(market_rows("T", "S", T0 + 15 * MIN, {14: (0.48, 0.50, 0.70), 13: (0.49, 0.51, 0.80)}, 1))
    run_backtest(rows, tmp_path, cls=SpyBacktest)
    assert [inputs["quotes"]["T"].fair for inputs, _ in SpyMIO.calls] == [0.70, 0.80]


def test_a_run_with_no_usable_row_refuses(tmp_path):
    rows = random_world(2)
    for row in rows:
        row["fair_blend_status"] = "warmup"
    with pytest.raises(ValueError, match="no usable row"):
        run_backtest(rows, tmp_path)


def test_a_lower_gamma_risks_more_and_the_stats_are_consistent(tmp_path):
    rows = random_world(10, seed=2)
    half, quarter = (run_backtest(rows, tmp_path / str(g), gamma=g)["summary"] for g in (2.0, 4.0))
    assert half["kelly"]["contracts"] > quarter["kelly"]["contracts"]
    assert half["flat"] == quarter["flat"], "the flat rule does not depend on risk aversion"
    for part in (half["kelly"], half["flat"]):
        assert 0.0 <= part["max_drawdown_cost"] < 1.0 and part["orders"] == sum(c["orders"] for c in part["pnl_by_lead"].values())
        assert part["day_pnl"]["n"] == half["counts"]["days"]


# --- params: default-deny --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cls,make", [(KalshiBinaryKellyMIO, mio_params), (KalshiKellyBacktest, backtest_params)])
def test_the_reference_params_validate_and_an_unknown_one_is_refused(cls, make):
    assert cls.validate_params(make()) == []
    assert any("typo" in p for p in cls.validate_params({**make(), "typo": 1}))


@pytest.mark.parametrize("cls,make", [(KalshiBinaryKellyMIO, mio_params), (KalshiKellyBacktest, backtest_params)])
@pytest.mark.parametrize("bad", [{"copula_rho": 1.0}, {"copula_rho": -0.1}, {"market_cap_frac": 0.0}, {"market_cap_frac": 1.5},
                                 {"max_contracts_per_order": 0}, {"uncertainty_frac": -1}, {"uncertainty_budget": 0},
                                 {"fee_model": {"mechanic": "nope"}}, {"risk_aversion_gamma": 0.5}])
def test_a_bad_knob_is_refused_by_name(cls, make, bad):
    (name,) = bad
    assert any(name in p for p in cls.validate_params({**make(), **bad}))


@pytest.mark.parametrize("missing", ["fee_model", "risk_aversion_gamma", "cvar_alpha", "cvar_limit", "cardinality", "min_ticket"])
def test_the_owner_decisions_and_the_venue_have_no_default(missing):
    params = {k: v for k, v in backtest_params().items() if k != missing}
    assert any(missing in p for p in KalshiKellyBacktest.validate_params(params))


@pytest.mark.parametrize("missing", ["fair_field"])
def test_the_backtest_requires_the_fair_column(missing):
    params = {k: v for k, v in backtest_params().items() if k != missing}
    assert any(missing in p for p in KalshiKellyBacktest.validate_params(params))


@pytest.mark.parametrize("bad", [{"starting_bankroll": 0}, {"lead_min_minutes": -1}, {"min_edge": -0.01}])
def test_a_bad_driver_knob_is_refused_by_name(bad):
    (name,) = bad
    assert any(name in p for p in KalshiKellyBacktest.validate_params({**backtest_params(), **bad}))


def test_every_default_has_exactly_one_home():
    assert set(DEFAULTS) <= set(KalshiKellyBacktest._PARAMS)
    assert KalshiBinaryKellyMIO.validate_params(mio_params()) == [], "every defaulted knob validates when left out"


# --- the shipped document --------------------------------------------------------------------------------------------

BLEND, KELLY = load("run-w4-blend.json"), load("run-w4-kelly.json")
SIZING_NODES = ("kelly_g2", "kelly_g4")


def test_the_kelly_run_is_the_blend_run_up_to_the_blend_and_keeps_the_w5_cut():
    blend, kelly = BLEND["pipeline"], KELLY["pipeline"]
    upto = list(blend)[: list(blend).index("blend_ok") + 1]
    assert list(kelly)[: len(upto)] == upto
    for key in upto:
        assert kelly[key] == blend[key], key
    assert kelly["markets_through"]["params"]["where"][0]["value"] == W4_END_MS
    assert kelly["candles_through"]["params"]["where"][0]["value"] == W4_END_MS


def test_the_sizing_nodes_agree_with_the_blend_the_fees_and_each_other():
    kelly, blend = KELLY["pipeline"], BLEND["pipeline"]
    fees = kelly["fees"]["params"]
    (fee_type,) = fees["fee_types"]
    cutoff = blend["lead_band"]["params"]["cases"][0]["when"][0]
    for key, gamma in zip(SIZING_NODES, (2.0, 4.0)):
        node = kelly[key]
        assert node["uses"] == "crypto_trading.sizing:KalshiKellyBacktest"
        assert node["inputs"] == {"records": "$blend_ok.records"}
        assert KalshiKellyBacktest.validate_params(node["params"]) == []
        params = node["params"]
        assert params["risk_aversion_gamma"] == gamma
        assert params["fair_field"] == kelly["blend"]["params"]["output_field"]
        assert params["fee_model"] == fees["fee_types"][fee_type]["model"], "the venue mechanic is the fee node's"
        assert params["lead_min_minutes"] == cutoff["value"] and cutoff["op"] == ">=", "the pre-registered lead band"
        assert params["starting_bankroll"] == 1000.0 and params["copula_rho"] == 0.8 and params["min_edge"] == 0.02
        assert node["notes"]
    other = [{k: v for k, v in kelly[key]["params"].items() if k != "risk_aversion_gamma"} for key in SIZING_NODES]
    assert other[0] == other[1], "the two variants differ only in risk aversion"


def test_the_ledgers_are_written_under_the_runs_own_data_directory():
    kelly = KELLY["pipeline"]
    writers = {k: v for k, v in kelly.items() if v["uses"].endswith("RecordsWriteRun")}
    assert len(writers) == 4 and not any(k in SIZING_NODES for k in writers)
    wired = {(v["inputs"]["records"], v["params"]["path"].rpartition("/")[0]) for v in writers.values()}
    assert wired == {(f"${key}.{port}", "~/data/crypto_trading/w4-kelly") for key in SIZING_NODES for port in ("trades", "wealth")}
    assert len({v["params"]["path"] for v in writers.values()}) == 4


def test_the_document_plans_in_order_with_the_sizing_after_the_blend_and_the_writers_after_the_sizing():
    resolved = plan(load_document(os.path.join(CONFIGS, "run-w4-kelly.json")))
    order = list(resolved.order)
    assert order.index("blend_ok") < order.index("kelly_g2") < order.index("write_trades_g2")
    assert order.index("blend_ok") < order.index("kelly_g4") < order.index("write_wealth_g4")
    assert resolved.role_of("kelly_g2") == "transform" and resolved.role_of("write_trades_g2") == "report"
