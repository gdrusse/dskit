"""The one book stack: ladders, the mirror rule, fills, records — golden
cases from the parent's oracle suite, restated independently."""

import json
import math

import pytest

from pmquant.books import (
    BookSnapshot,
    ContractInputs,
    CrossedBookError,
    DecisionEpochRecord,
    IncompleteBookError,
    Order,
    asks_from_bids,
    book_quality_ok,
    contract_inputs_from_book,
    entry_gate,
    ladders_from_book_json,
    market_record_from_epoch,
    mid_from_ladders,
    net_edge,
    parse_ladder,
    records_from_pit_rows,
    walk_book,
)
from pmquant.fees import FeeRateUnresolved

# --- ladders --------------------------------------------------------------


def test_parse_ladder_normalizes_to_resting_bid_order():
    levels = parse_ladder([[0.40, 30.9], [0.55, 20], [1.0, 7], [0.0, 5], [0.3, 0.4]])
    assert levels == ((0.55, 20), (0.40, 30))
    assert parse_ladder(json.dumps([[0.2, 1]])) == ((0.2, 1),)
    assert parse_ladder(None) == ()


def test_parse_ladder_refuses_malformed_levels():
    with pytest.raises(ValueError, match="pair"):
        parse_ladder([[0.5]])
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        parse_ladder([[1.5, 3]])
    with pytest.raises(ValueError, match="size"):
        parse_ladder([[0.5, -3]])
    with pytest.raises(ValueError, match="JSON"):
        parse_ladder("{not json")


def test_asks_from_bids_is_the_oracle_transform():
    # Oracle 1: yes_book / no_book -> executable asks, sub-lot and boundary drops.
    yes_book = [[0.55, 20.9], [0.40, 30], [1.00, 7]]
    no_book = [[0.30, 12.7], [0.42, 40], [0.00, 9]]
    yes_asks = asks_from_bids(no_book)
    no_asks = asks_from_bids(yes_book)
    assert [round(p, 6) for p, _ in yes_asks] == [0.58, 0.70]
    assert [d for _, d in yes_asks] == [40, 12]
    assert [round(p, 6) for p, _ in no_asks] == [0.45, 0.60]
    assert [d for _, d in no_asks] == [20, 30]
    assert yes_asks[0][0] + no_asks[0][0] >= 1.0  # uncrossed
    assert asks_from_bids(json.dumps(no_book)) == yes_asks


def test_mid_is_none_when_one_sided():
    assert mid_from_ladders(((0.4, 1),), ((0.55, 1),)) == pytest.approx(0.425)
    assert mid_from_ladders((), ((0.55, 1),)) is None


def test_ladders_from_book_json_reads_both_encodings():
    kalshi = json.dumps({"orderbook_fp": {"yes_dollars": [[0.4, 10]], "no_dollars": [[0.55, 12]]}})
    poly = json.dumps({"orderbook_fp": {"yes_dollars": [[0.4, 10]], "yes_asks": [[0.45, 12]]}})
    yes_k, no_k = ladders_from_book_json(kalshi)
    yes_p, no_p = ladders_from_book_json(poly)
    assert yes_k == yes_p == ((0.4, 10),)
    assert no_k == ((0.55, 12),)
    assert no_p == ((0.55, 12),)  # 1 - 0.45: the poly ask IS a NO bid
    assert ladders_from_book_json(None) == ((), ())
    with pytest.raises(ValueError, match="orderbook_fp"):
        ladders_from_book_json(json.dumps({"yes": []}))


def test_book_quality_gate():
    assert book_quality_ok(((0.40, 1),), ((0.55, 1),), 0.10)  # ask 0.45, spread 0.05
    assert not book_quality_ok((), ((0.55, 1),), 0.10)  # one-sided
    assert not book_quality_ok(((0.40, 1),), ((0.65, 1),), 0.10)  # ask 0.35 <= bid: crossed
    assert not book_quality_ok(((0.40, 1),), ((0.40, 1),), 0.10)  # ask 0.60 - 0.40 = 0.20 wide
    assert not book_quality_ok(((0.40, 1),), ((0.0001, 1),), 0.10)  # converged at 1


# --- records --------------------------------------------------------------


def _rec(**over):
    base = dict(
        series="KXA", event_ticker="KXA-1", contract_ticker="KXA-1-T50", epoch_kind="lead",
        lead_frac=0.5, epoch_ts_ms=1_000, source="pit", yes_levels=((0.40, 10),),
        no_levels=((0.55, 12),), p_mid=0.425, staleness_ms=0, admissible=True,
        quality_ok=True, usable=True, reason="ok",
    )
    base.update(over)
    return DecisionEpochRecord(**base)


def test_record_invariants():
    _rec()
    with pytest.raises(ValueError, match="usable IS"):
        _rec(usable=False)
    with pytest.raises(ValueError, match="lead_frac"):
        _rec(lead_frac=None)
    with pytest.raises(ValueError, match="carries no lead_frac"):
        _rec(epoch_kind="settle", lead_frac=0.5)
    with pytest.raises(ValueError, match="epoch_kind"):
        _rec(epoch_kind="tick")
    with pytest.raises(ValueError, match="reason"):
        _rec(reason="")


def test_records_from_pit_rows_recomputes_usable_and_survives_a_bad_book():
    rows = [
        {"event_ticker": "KXA-1", "contract_ticker": "KXA-1-T50", "kind": "lead", "lead_frac": 0.5,
         "epoch_ts_ms": 1000, "staleness_ms": 7, "admissible": True, "quality_ok": True,
         "reason": "ok", "usable": False,  # the stored flag is IGNORED
         "book_json": json.dumps({"orderbook_fp": {"yes_dollars": [[0.4, 10]], "no_dollars": [[0.55, 12]]}})},
        {"event_ticker": "KXA-1", "contract_ticker": "KXA-1-T50", "kind": "lead", "lead_frac": 0.02,
         "epoch_ts_ms": 2000, "staleness_ms": None, "admissible": True, "quality_ok": True,
         "reason": "ok", "book_json": "{broken"},
        {"event_ticker": "KXA-1", "contract_ticker": "KXA-1-T50", "kind": "settle", "lead_frac": None,
         "epoch_ts_ms": 3000, "staleness_ms": None, "admissible": False, "quality_ok": False,
         "reason": "settle", "book_json": None},
    ]
    recs = records_from_pit_rows(rows, "kalshi", "pit")
    assert [r.usable for r in recs] == [True, False, False]
    assert recs[0].series == "KXA" and recs[0].p_mid == pytest.approx(0.425) and recs[0].staleness_ms == 7
    assert recs[1].reason == "bad_book" and recs[1].yes_levels == ()
    assert recs[2].epoch_kind == "settle" and recs[2].lead_frac is None
    with pytest.raises(ValueError, match="required field"):
        records_from_pit_rows([{"kind": "lead"}], "kalshi", "pit")


def test_market_record_from_epoch_mirrors_the_ask():
    env = market_record_from_epoch("kalshi", _rec())
    assert (env.instrument, env.contract, env.group) == ("KXA", "KXA-1-T50", "KXA-1")
    assert env.bid == 0.40 and env.ask == pytest.approx(0.45) and env.mid == pytest.approx(0.425)
    assert env.native is not None and env.lead_frac == 0.5 and env.asof_ms == 1000
    one_sided = market_record_from_epoch("kalshi", _rec(no_levels=(), p_mid=None,
                                                       quality_ok=False, usable=False))
    assert one_sided.ask is None and one_sided.mid is None


# --- contract inputs + the gate ------------------------------------------


def test_contract_inputs_from_book_oracles():
    yes_book = [[0.55, 20.9], [0.40, 30], [1.00, 7]]
    no_book = [[0.30, 12.7], [0.42, 40], [0.00, 9]]
    ci = contract_inputs_from_book("X", 0.5, yes_bids=yes_book, no_bids=no_book, fee_rate=0.07)
    assert [round(p, 6) for p, _ in ci.yes_levels] == [0.58, 0.70]
    assert ci.q_hat == 0.5 and ci.fee_rate == 0.07 and ci.cell_lo_c is None
    # 1b: JSON string == list
    ci2 = contract_inputs_from_book("X", 0.5, yes_bids=json.dumps(yes_book),
                                    no_bids=json.dumps(no_book), fee_rate=0.07)
    assert ci2.yes_levels == ci.yes_levels and ci2.no_levels == ci.no_levels
    # 1c: every level floors to zero -> incomplete
    with pytest.raises(IncompleteBookError):
        contract_inputs_from_book("X", 0.5, yes_bids=[[0.40, 0.9]], no_bids=[[0.30, 25]], fee_rate=0.07)
    # 4a: crossed -> arb
    with pytest.raises(CrossedBookError):
        contract_inputs_from_book("X", 0.5, yes_bids=[[0.85, 50]], no_bids=[[0.90, 50]], fee_rate=0.07)
    assert issubclass(CrossedBookError, IncompleteBookError)
    # a touching book (asks sum to exactly 1) is legal
    contract_inputs_from_book("X", 0.5, yes_bids=[[0.60, 5]], no_bids=[[0.40, 5]], fee_rate=0.07)
    with pytest.raises(ValueError, match="missing"):
        contract_inputs_from_book("X", 0.5, yes_bids=yes_book, no_bids=no_book, fee_rate=None)
    with pytest.raises(ValueError, match="q_hat"):
        ContractInputs("X", 1.5, ((0.3, 1),), ((0.7, 1),), 0.07)
    with pytest.raises(ValueError, match="depths"):
        ContractInputs("X", 0.5, ((0.3, 1.5),), ((0.7, 1),), 0.07)


def test_net_edge_and_entry_gate():
    # q 0.40 vs ask 0.30 / bid 0.28: YES side, gross 0.10 minus the 1-lot fee
    info = net_edge(0.40, 0.28, 0.30, 0.07, "KXA")
    assert info["side"] == "yes" and info["gross_edge"] == pytest.approx(0.10)
    assert info["fee"] == pytest.approx(0.02)  # ceil(0.07*0.3*0.7*100)=2 cents
    assert info["net_edge"] == pytest.approx(0.08)
    # q 0.10: NO side, gross 0.18
    assert net_edge(0.10, 0.28, 0.30, 0.07, "KXA")["side"] == "no"
    with pytest.raises(ValueError, match="crossed"):
        net_edge(0.5, 0.6, 0.5, 0.07, "KXA")
    ci = contract_inputs_from_book("KXA-1", 0.40, yes_bids=[[0.28, 2000]], no_bids=[[0.70, 2000]],
                                   fee_rate=0.07)
    side, info = entry_gate(ci, "KXA")
    assert side == "yes" and info["net_edge"] > 0
    assert entry_gate(ci, "KXA", tau=0.5)[0] is None
    # CI-robust: no bound fails closed; a bound above the floor passes
    assert entry_gate(ci, "KXA", cell_lo_c_floor=0.0)[0] is None
    ci_lo = contract_inputs_from_book("KXA-1", 0.40, yes_bids=[[0.28, 2000]], no_bids=[[0.70, 2000]],
                                      fee_rate=0.07, cell_lo_c=0.01)
    assert entry_gate(ci_lo, "KXA", cell_lo_c_floor=0.0)[0] == "yes"
    # no edge: q 0.29 between bid 0.28 and ask 0.30
    ci_flat = contract_inputs_from_book("KXA-1", 0.29, yes_bids=[[0.28, 10]], no_bids=[[0.70, 10]],
                                        fee_rate=0.07)
    assert entry_gate(ci_flat, "KXA")[0] is None


# --- the fill walk --------------------------------------------------------


def test_walk_book_bills_the_venue_rounding_on_the_total_at_vwap():
    fill = walk_book(BookSnapshot("KXCOPPERD-X", "ask", ((0.25, 100),)), Order(100, 1.0, 0.07))
    assert fill.filled == 100 and not fill.is_partial and fill.vwap == 0.25
    assert fill.fee == pytest.approx(1.32) and fill.net_cost == pytest.approx(25.0 + 1.32)
    poly = walk_book(BookSnapshot("POLYBTCUPDOWN15M-X", "ask", ((0.25, 100),)), Order(100, 1.0, 0.07))
    assert poly.fee == pytest.approx(1.3125) and poly.net_cost == pytest.approx(25.0 + 1.3125)
    zero = walk_book(BookSnapshot("POLYWXHINYC-X", "ask", ((0.25, 100),)), Order(100, 1.0, 0.0))
    assert zero.fee == 0.0 and zero.net_cost == 25.0
    with pytest.raises(ValueError, match="fee_rate is required"):
        Order(100, 1.0, None)


def test_walk_book_is_best_first_partial_and_limit_bound():
    book = BookSnapshot("KXA-1", "ask", ((0.25, 40), (0.30, 50), (0.40, 100)), mid=0.24)
    fill = walk_book(book, Order(70, 0.35, 0.07))
    assert [(h.price, h.contracts) for h in fill.levels] == [(0.25, 40), (0.30, 30)]
    assert fill.filled == 70 and not fill.is_partial
    assert fill.vwap == pytest.approx((0.25 * 40 + 0.30 * 30) / 70)
    assert fill.slippage_vs_mid == pytest.approx(fill.vwap - 0.24)
    partial = walk_book(book, Order(500, 0.30, 0.07))
    assert partial.filled == 90 and partial.is_partial
    empty = walk_book(BookSnapshot("KXA-1", "ask", ()), Order(10, 1.0, 0.07))
    assert empty.filled == 0 and empty.is_partial and math.isnan(empty.vwap) and empty.fee == 0.0
    bids = walk_book(BookSnapshot("KXA-1", "bid", ((0.60, 10), (0.55, 10))), Order(15, 0.0, 0.07))
    assert bids.filled == 15 and bids.net_cost == pytest.approx(15 * (1.0 - bids.vwap) + bids.fee)
    with pytest.raises(ValueError, match="executable order"):
        BookSnapshot("KXA-1", "ask", ((0.30, 1), (0.25, 1)))


def test_fee_rate_none_on_the_series_path_refuses():
    with pytest.raises(FeeRateUnresolved):
        net_edge(0.4, 0.28, 0.30, None, "KXA")
