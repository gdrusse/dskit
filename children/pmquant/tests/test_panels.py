"""Panels + the frozen token features: independent oracles, not re-derivations.

Every expected number here is computed by hand (or by a one-line
restatement) from the fixture books, never by calling the module twice.
The 41 column names are spelled out literally — a test that imported the
list from the module would assert nothing.
"""

import math

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from pmquant.books import DecisionEpochRecord, market_record_from_epoch
from pmquant.ladder.panels import (
    DEFAULT_K_LVL,
    ITEM_KEYS,
    PANEL_KEYS,
    TAIL_GROUPS,
    EventPanel,
    MarketVocab,
    TokenFeaturizer,
    build_panel_items,
    build_panels,
    collate_items,
)
from pmquant.ladder.protocols import LadderType, LeadGrid

FRACS = (0.9, 0.5, 0.1)
GRID = LeadGrid(FRACS)
HOUR_MS = 3_600_000

#: The frozen 41-column order at k_lvl 5 — RESTATED, deliberately.
EXPECTED_NAMES = (
    "yes_present", "yes_touch",
    "yes_off1", "yes_off2", "yes_off3", "yes_off4", "yes_off5",
    "yes_logdep1", "yes_logdep2", "yes_logdep3", "yes_logdep4", "yes_logdep5",
    "yes_n_levels", "yes_log_depth",
    "no_present", "no_touch",
    "no_off1", "no_off2", "no_off3", "no_off4", "no_off5",
    "no_logdep1", "no_logdep2", "no_logdep3", "no_logdep4", "no_logdep5",
    "no_n_levels", "no_log_depth",
    "staleness",
    "strike_z", "gap_z", "rung_pos", "log_n_contracts", "lead_frac", "dur_h",
    "ladder_mass", "ladder_center", "ladder_entropy", "source_f", "hour_sin", "hour_cos",
)

YES_BIDS = ((0.40, 10), (0.35, 5))
NO_BIDS = ((0.55, 12),)


def lead_record(series, event, contract, frac, close_ms, *, yes=YES_BIDS, no=NO_BIDS,
                staleness_ms=3000, venue="kalshi"):
    """One usable lead epoch at ``close - frac * 1h`` with the given resting bids."""
    ts = int(close_ms - frac * HOUR_MS)
    mid = 0.5 * (yes[0][0] + (1.0 - no[0][0])) if yes and no else None
    rec = DecisionEpochRecord(
        series=series, event_ticker=event, contract_ticker=contract, epoch_kind="lead",
        lead_frac=frac, epoch_ts_ms=ts, source="pit", yes_levels=tuple(yes),
        no_levels=tuple(no), p_mid=mid, staleness_ms=staleness_ms, admissible=True,
        quality_ok=bool(yes and no), usable=bool(yes and no),
        reason="ok" if (yes and no) else "no_book",
    )
    return market_record_from_epoch(venue, rec)


def market_row(ticker, event, series, strike_type, floor, cap, close_ms):
    return {
        "ticker": ticker, "event_ticker": event, "series_ticker": series,
        "strike_type": strike_type, "floor_strike": floor, "cap_strike": cap,
        "close_ms": close_ms, "open_ms": close_ms - 24 * HOUR_MS,
    }


def threshold_event(series="KXA", event="KXA-1", close_ms=100 * HOUR_MS, fracs=FRACS):
    """Two greater-rungs (an upper threshold), fully observed on the grid."""
    contracts = [f"{event}-T50", f"{event}-T60"]
    records = [
        lead_record(series, event, c, f, close_ms) for c in contracts for f in fracs
    ]
    markets = [
        market_row(contracts[0], event, series, "greater", 50.0, None, close_ms),
        market_row(contracts[1], event, series, "greater", 60.0, None, close_ms),
    ]
    outcomes = {contracts[0]: True, contracts[1]: False}
    return records, markets, outcomes, contracts


def build(records, markets, outcomes, **kw):
    return build_panels(records, outcomes, markets, GRID, **kw)


# --- the panel ----------------------------------------------------------------


def test_build_panels_orders_rungs_canonically_and_labels_them():
    close = 100 * HOUR_MS
    event, series = "KXA-1", "KXA"
    # shuffled: greater, less, between, between — canonical order is
    # less (cap asc), between (floor asc), greater (floor asc)
    rows = [
        market_row("G70", event, series, "greater", 70.0, None, close),
        market_row("L40", event, series, "less", None, 40.0, close),
        market_row("B60", event, series, "between", 60.0, 70.0, close),
        market_row("B50", event, series, "between", 50.0, 60.0, close),
    ]
    records = [lead_record(series, event, r["ticker"], f, close) for r in rows for f in FRACS]
    outcomes = {"G70": False, "L40": False, "B60": True, "B50": False}
    built = build(records, rows, outcomes)
    assert len(built.panels) == 1
    panel = built.panels[0]
    assert isinstance(panel, EventPanel)
    assert panel.contracts == ["L40", "B50", "B60", "G70"]
    assert panel.y.tolist() == [0.0, 0.0, 1.0, 0.0]
    assert panel.st_code.tolist() == [0, 1, 1, 2]
    assert panel.ladder_type is LadderType.PARTITION
    assert panel.close_ts_ms == close
    assert panel.source_f == 0.0
    # dur_h: the earliest observed epoch is close - 0.9h
    assert panel.dur_h == pytest.approx(0.9)
    assert built.vocab.series == ("KXA",)
    assert panel.market_id == 0


def test_threshold_classification_and_strike_geometry():
    records, markets, outcomes, contracts = threshold_event()
    built = build(records, markets, outcomes)
    panel = built.panels[0]
    assert panel.ladder_type is LadderType.UPPER_THRESHOLD
    assert panel.contracts == contracts
    # strikes 50, 60: z = (sv - mean)/std with population std 5 -> [-1, 1];
    # gaps [10], gmed 10 -> gap_z = [10/10 - 1, 0] = [0, 0]
    assert panel.strike_z.tolist() == pytest.approx([-1.0, 1.0])
    assert panel.gap_z.tolist() == pytest.approx([0.0, 0.0])
    assert panel.strike_z.dtype == np.float32


def test_strike_geometry_is_zero_without_two_finite_strikes():
    close = 100 * HOUR_MS
    rows = [
        market_row("A", "E", "KXA", "greater", 50.0, None, close),
        market_row("B", "E", "KXA", "greater", None, None, close),
    ]
    records = [lead_record("KXA", "E", r["ticker"], f, close) for r in rows for f in FRACS]
    built = build(records, rows, {"A": True, "B": False})
    assert built.panels[0].strike_z.tolist() == [0.0, 0.0]
    assert built.panels[0].gap_z.tolist() == [0.0, 0.0]


def test_unsettled_events_are_skipped_and_counted():
    records, markets, outcomes, contracts = threshold_event()
    del outcomes[contracts[1]]
    built = build(records, markets, outcomes)
    assert built.panels == []
    assert built.counts["n_skipped_unsettled"] == 1


def test_off_grid_rows_are_skipped_and_counted():
    records, markets, outcomes, contracts = threshold_event()
    records.append(lead_record("KXA", "KXA-1", contracts[0], 0.77, 100 * HOUR_MS))
    built = build(records, markets, outcomes)
    assert built.counts["n_off_grid_rows"] == 1
    assert len(built.panels[0].cells) == 6  # 2 rungs x 3 grid leads


def test_min_contracts_skips_thin_events():
    close = 100 * HOUR_MS
    rows = [market_row("ONLY", "E", "KXA", "greater", 50.0, None, close)]
    records = [lead_record("KXA", "E", "ONLY", f, close) for f in FRACS]
    built = build(records, rows, {"ONLY": True})
    assert built.panels == [] and built.counts["n_skipped_min_contracts"] == 1
    kept = build(records, rows, {"ONLY": True}, min_contracts=1)
    assert len(kept.panels) == 1


def test_a_contract_without_a_markets_row_is_a_defect():
    records, markets, outcomes, _ = threshold_event()
    with pytest.raises(ValueError, match="markets row"):
        build(records, markets[:1], outcomes)


def test_non_lead_records_are_ignored_and_counted():
    records, markets, outcomes, contracts = threshold_event()
    settle = DecisionEpochRecord(
        series="KXA", event_ticker="KXA-1", contract_ticker=contracts[0],
        epoch_kind="settle", lead_frac=None, epoch_ts_ms=100 * HOUR_MS, source="pit",
        yes_levels=(), no_levels=(), p_mid=None, staleness_ms=None, admissible=False,
        quality_ok=False, usable=False, reason="settle",
    )
    records.append(market_record_from_epoch("kalshi", settle))
    built = build(records, markets, outcomes)
    assert built.counts["n_skipped_non_lead"] == 1
    assert len(built.panels) == 1


def test_vocab_is_sorted_and_adding_a_series_shifts_indices():
    vocab = MarketVocab.from_series(["KXB", "KXA", "KXB"])
    assert vocab.series == ("KXA", "KXB")
    assert vocab.index("KXB") == 1 and len(vocab) == 2
    assert vocab.to_dict() == {"KXA": 0, "KXB": 1}
    grown = MarketVocab.from_series(["KXB", "KXA", "KXAA"])
    assert grown.index("KXB") == 2  # the insertion shifted it
    with pytest.raises(ValueError, match="vocab"):
        vocab.index("POLYX")


def test_source_flag_reads_the_declared_venue_table():
    close = 100 * HOUR_MS
    rows = [
        market_row("POLYX-1-A", "POLYX-1", "POLYX", "greater", 1.0, None, close),
        market_row("POLYX-1-B", "POLYX-1", "POLYX", "greater", 2.0, None, close),
    ]
    records = [
        lead_record("POLYX", "POLYX-1", r["ticker"], f, close, venue="polymarket")
        for r in rows for f in FRACS
    ]
    built = build(records, rows, {"POLYX-1-A": True, "POLYX-1-B": False})
    assert built.panels[0].source_f == 1.0


# --- the featurizer -----------------------------------------------------------


def test_the_frozen_41_names_and_the_tail_groups():
    fz = TokenFeaturizer()
    assert fz.k_lvl == DEFAULT_K_LVL == 5
    assert fz.feature_names() == EXPECTED_NAMES
    assert fz.n_features == 41
    assert TAIL_GROUPS == {"strike": (0, 1, 2, 3), "ladder": (6, 7, 8), "context": (4, 5, 9, 10, 11)}
    assert TokenFeaturizer(k_lvl=2).n_features == 2 * (2 * 2 + 4) + 1 + 12
    with pytest.raises(ValueError, match="k_lvl"):
        TokenFeaturizer(k_lvl=0)
    with pytest.raises(ValueError, match="strike"):
        TokenFeaturizer(drop=("nope",))


def test_side_features_are_the_executable_asks_of_the_opposite_bids():
    records, markets, outcomes, contracts = threshold_event()
    panel = build(records, markets, outcomes).panels[0]
    feats, seen = TokenFeaturizer().encode(panel, GRID)
    assert feats.shape == (3, 2, 41) and feats.dtype == np.float32
    assert seen.shape == (3, 2) and seen.all()
    cell = feats[1, 0]  # step 1 (lead 0.5), rung 0
    # YES asks = mirror of NO bids ((0.55, 12),) -> ((0.45, 12),)
    yes = cell[0:14]
    assert yes[0] == 1.0 and yes[1] == pytest.approx(0.45)
    assert yes[2:7].tolist() == [0.0] * 5
    assert yes[7] == pytest.approx(math.log1p(12)) and yes[8:12].tolist() == [0.0] * 4
    assert yes[12] == 1.0 and yes[13] == pytest.approx(math.log1p(12))
    # NO asks = mirror of YES bids ((0.40,10),(0.35,5)) -> ((0.60,10),(0.65,5))
    no = cell[14:28]
    assert no[0] == 1.0 and no[1] == pytest.approx(0.60)
    assert no[2] == pytest.approx(0.0) and no[3] == pytest.approx(0.05)
    assert no[4:7].tolist() == [0.0] * 3
    assert no[7] == pytest.approx(math.log1p(10)) and no[8] == pytest.approx(math.log1p(5))
    assert no[9:12].tolist() == [0.0] * 3
    assert no[12] == 2.0 and no[13] == pytest.approx(math.log1p(15))
    assert cell[28] == pytest.approx(math.log1p(3.0))  # staleness 3000 ms


def test_the_tail_features_restated():
    records, markets, outcomes, contracts = threshold_event()
    panel = build(records, markets, outcomes).panels[0]
    feats, _ = TokenFeaturizer().encode(panel, GRID)
    names = list(EXPECTED_NAMES)
    tail = {n: feats[1, :, names.index(n)] for n in names[29:]}
    assert tail["strike_z"].tolist() == pytest.approx([-1.0, 1.0])
    assert tail["gap_z"].tolist() == pytest.approx([0.0, 0.0])
    assert tail["rung_pos"].tolist() == pytest.approx([0.0, 1.0])
    assert tail["log_n_contracts"].tolist() == pytest.approx([math.log1p(2)] * 2)
    assert tail["lead_frac"].tolist() == pytest.approx([0.5, 0.5])
    assert tail["dur_h"].tolist() == pytest.approx([math.log1p(0.9)] * 2)
    # both rungs quote a YES ask of 0.45: mass 0.9, pn = [.5,.5],
    # center = (0*.5 + 1*.5)/1 = .5, entropy = -2*.5*ln(.5)/ln(2) = 1
    assert tail["ladder_mass"].tolist() == pytest.approx([0.9, 0.9])
    assert tail["ladder_center"].tolist() == pytest.approx([0.5, 0.5])
    assert tail["ladder_entropy"].tolist() == pytest.approx([1.0, 1.0])
    assert tail["source_f"].tolist() == [0.0, 0.0]
    ts = 100 * HOUR_MS - 0.5 * HOUR_MS
    hfrac = ((ts / 3.6e6) % 24.0) / 24.0
    assert tail["hour_sin"].tolist() == pytest.approx([math.sin(2 * math.pi * hfrac)] * 2)
    assert tail["hour_cos"].tolist() == pytest.approx([math.cos(2 * math.pi * hfrac)] * 2)


def test_an_absent_side_is_all_zeros_and_seen_needs_one_side():
    close = 100 * HOUR_MS
    records, markets, outcomes, contracts = threshold_event()
    # rung 1 at step 0: one-sided (no NO bids) -> YES-ask block zero, still seen
    records = [
        r for r in records if not (r.contract == contracts[1] and r.lead_frac == 0.9)
    ]
    records.append(lead_record("KXA", "KXA-1", contracts[1], 0.9, close, no=()))
    # rung 0 at step 0: no book at all -> unseen
    records = [
        r for r in records if not (r.contract == contracts[0] and r.lead_frac == 0.9)
    ]
    records.append(lead_record("KXA", "KXA-1", contracts[0], 0.9, close, yes=(), no=()))
    panel = build(records, markets, outcomes).panels[0]
    feats, seen = TokenFeaturizer().encode(panel, GRID)
    assert not seen[0, 0] and seen[0, 1]
    assert feats[0, 1, 0:14].tolist() == [0.0] * 14  # no YES asks without NO bids
    assert feats[0, 1, 14] == 1.0  # the NO-ask side is there
    assert feats[0, 0, 0:28].tolist() == [0.0] * 28  # both side blocks empty
    assert feats[0, 0, 28] == pytest.approx(math.log1p(3.0))  # staleness is the record's


def test_ablation_zeroes_the_named_group_last():
    records, markets, outcomes, _ = threshold_event()
    panel = build(records, markets, outcomes).panels[0]
    full, _ = TokenFeaturizer().encode(panel, GRID)
    dropped, _ = TokenFeaturizer(drop=("context",)).encode(panel, GRID)
    base = 29
    for off in TAIL_GROUPS["context"]:
        assert (dropped[:, :, base + off] == 0.0).all()
    for off in TAIL_GROUPS["strike"] + TAIL_GROUPS["ladder"]:
        assert (dropped[:, :, base + off] == full[:, :, base + off]).all()
    assert (dropped[:, :, :base] == full[:, :, :base]).all()
    # a string names one group, exactly like a one-element tuple
    single, _ = TokenFeaturizer(drop="context").encode(panel, GRID)
    assert (single == dropped).all()


def test_encode_is_deterministic():
    records, markets, outcomes, _ = threshold_event()
    a = build(records, markets, outcomes).panels[0]
    b = build(list(reversed(records)), markets, outcomes).panels[0]
    fa, sa = TokenFeaturizer().encode(a, GRID)
    fb, sb = TokenFeaturizer().encode(b, GRID)
    assert (fa == fb).all() and (sa == sb).all()


# --- items + collate ----------------------------------------------------------


def test_items_carry_every_key_and_visible_is_the_running_or():
    close = 100 * HOUR_MS
    records, markets, outcomes, contracts = threshold_event()
    # rung 1 unseen at step 0 and step 2, seen at step 1 -> visible F,T,T
    records = [r for r in records if r.contract != contracts[1]]
    records += [
        lead_record("KXA", "KXA-1", contracts[1], 0.9, close, yes=(), no=()),
        lead_record("KXA", "KXA-1", contracts[1], 0.5, close),
        lead_record("KXA", "KXA-1", contracts[1], 0.1, close, yes=(), no=()),
    ]
    built = build(records, markets, outcomes)
    items = build_panel_items(built.panels, TokenFeaturizer(), GRID, eligible={"KXA"})
    assert len(items) == 1
    item = items[0]
    assert set(item) == set(ITEM_KEYS) and set(PANEL_KEYS) <= set(item)
    assert item["seen"][:, 1].tolist() == [False, True, False]
    assert item["visible"][:, 1].tolist() == [False, True, True]
    assert item["visible"][:, 0].tolist() == [True, True, True]
    assert item["y"].tolist() == [1.0, 0.0] and item["y"].dtype == np.float32
    assert item["st_code"].tolist() == [2, 2] and item["st_code"].dtype == np.int64
    assert item["is_partition"] is False and item["eligible"] is True
    assert item["market_id"] == 0 and item["contracts"] == contracts
    assert item["series"] == "KXA" and item["event"] == "KXA-1"
    assert item["close_ts_ms"] == close and item["lead_fracs"] == FRACS
    assert item["featurizer"] == (5, ()) and item["vocab"] == {"KXA": 0}
    assert item["asks"][1].tolist() == pytest.approx([0.45, 0.45])
    assert math.isnan(item["asks"][0, 1]) and item["ask_sz"][0, 1] == 0.0
    assert item["ask_sz"][1].tolist() == pytest.approx([12.0, 12.0])
    assert item["bid_sz"][1].tolist() == pytest.approx([10.0, 10.0])
    assert item["asks_no"][1].tolist() == pytest.approx([0.60, 0.60])
    other = build_panel_items(built.panels, TokenFeaturizer(), GRID, eligible=())
    assert other[0]["eligible"] is False


def test_collate_pads_the_contract_axis():
    close = 100 * HOUR_MS
    r2, m2, o2, _ = threshold_event(event="KXA-1", close_ms=close)
    rows3 = [
        market_row(f"KXA-2-T{s}", "KXA-2", "KXA", "greater", float(s), None, close)
        for s in (50, 60, 70)
    ]
    r3 = [lead_record("KXA", "KXA-2", r["ticker"], f, close) for r in rows3 for f in FRACS]
    o3 = {"KXA-2-T50": True, "KXA-2-T60": True, "KXA-2-T70": False}
    built = build(r2 + r3, m2 + rows3, {**o2, **o3})
    items = build_panel_items(built.panels, TokenFeaturizer(), GRID, eligible={"KXA"})
    batch = collate_items(items)
    assert batch["feats"].shape == (2, 3, 3, 41) and batch["feats"].dtype == torch.float32
    assert batch["seen"].shape == (2, 3, 3) and batch["seen"].dtype == torch.bool
    assert batch["visible"].shape == (2, 3, 3)
    assert batch["y"].tolist() == [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]
    assert batch["contract_mask"].tolist() == [[True, True, False], [True, True, True]]
    assert batch["st_code"].tolist() == [[2, 2, 1], [2, 2, 2]]  # padded with between
    assert batch["st_code"].dtype == torch.long and batch["market_id"].dtype == torch.long
    assert batch["market_id"].tolist() == [0, 0]
    assert batch["is_partition"].tolist() == [False, False]
    assert batch["eligible"].tolist() == [True, True]
    assert set(batch) == {
        "feats", "seen", "visible", "y", "market_id", "is_partition", "st_code",
        "contract_mask", "eligible", "featurizer",
    }
    assert batch["featurizer"] == (5, ())
    # the padded rung is unseen and never visible
    assert not batch["seen"][0, :, 2].any() and not batch["visible"][0, :, 2].any()
    with pytest.raises(ValueError, match="empty"):
        collate_items([])
    # one layout per batch: the ablated item is refused beside the plain one
    ablated = build_panel_items(built.panels, TokenFeaturizer(drop="context"), GRID, eligible=())
    assert ablated[0]["featurizer"] == (5, ("context",))
    with pytest.raises(ValueError, match="layout"):
        collate_items(items + ablated[:1])
