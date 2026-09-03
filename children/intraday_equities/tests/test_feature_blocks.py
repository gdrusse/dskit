"""The three switchable feature blocks: formulas, and no look-ahead.

ADR-0071. Two kinds of test live here and the second matters most.

*Correctness* — every formula is checked against a number worked out by
hand on a tape small enough to read.

*No look-ahead* — for the bar-computable columns the check is prefix
invariance: change the tape only AFTER bar ``t`` and every column at
every bar up to ``t`` must be bit-identical. A column that peeked would
move. For the training-fold-fitted columns the check is fold invariance:
scramble the validation rows and the fitted values must not move at all,
and a fit made on a training fold with one volatility must not inherit
the validation fold's.
"""

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from dskit.pipeline.node import NodeContext

from intraday_equities.features import (
    BLOCKS,
    apply_fold_stats,
    block_causal_names,
    block_columns,
    block_feature_names,
    block_fold_names,
    block_problems,
    fit_fold_stats,
    normalise_blocks,
)
from intraday_equities.nodes import (
    FoldFeatureStats,
    SessionFeatureRows,
    _emit_feature_names,
    _session_feature_arrays,
)

SESSION = {
    "tz": "America/New_York",
    "rth_start_minutes": 9 * 60 + 30,
    "rth_end_minutes": 16 * 60,
}
#: 2026-01-05 is a Monday; 14:30 UTC is 09:30 New York.
_OPEN = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
_DAY_MINUTES = 30
_MAX_GAP_MS = 5 * 60_000


def _ms(day, minute):
    """Epoch ms of ``minute`` minutes past the open on session ``day``."""
    return int(
        (_OPEN + timedelta(days=day, minutes=minute)).timestamp() * 1000
    )


def _tape(prices, days=1, minutes=_DAY_MINUTES):
    """Build one symbol's arrays for ``days`` sessions of ``minutes`` bars.

    ``prices`` is a flat list, session-major. Open, high and low equal
    the close, so every derived column reads one number per bar.
    """
    ms = np.asarray(
        [_ms(d, m) for d in range(days) for m in range(minutes)],
        dtype=np.int64,
    )
    close = np.asarray(prices, dtype=np.float64)
    volume = np.full(close.size, 100.0)
    return ms, close, volume


def _internals(ms, close, volume):
    """Run the real column builder and hand back its tape internals."""
    out = {}
    columns = _session_feature_arrays(
        ms, close, close, close, close, volume, 2, _MAX_GAP_MS, (),
        [{"width": 2, "tag": "2m", "cross_session": False}], SESSION,
        internals=out,
    )
    return columns, out


def _build(blocks, ms, close, volume, keep=None, market=None, sector=None):
    """Build one symbol's block columns on the full tape."""
    columns, inner = _internals(ms, close, volume)
    if keep is None:
        keep = np.ones(ms.size, dtype=bool)
    return block_columns(
        blocks,
        keep=keep,
        logp=inner["logp"],
        ret1=inner["ret1"],
        opn=close,
        sess_start=inner["sess_start"],
        mins=inner["mins"],
        parsed=inner["parsed"],
        overnight=columns["overnight_gap"],
        session=SESSION,
        market_ret=market,
        sector_ret=sector,
        beta_window=5,
    )


def _walk(n, start=100.0, step=0.5):
    """A strictly rising price path; every one-minute return is positive."""
    return [start + step * i for i in range(n)]


# --------------------------------------------------------------------
# names and switches
# --------------------------------------------------------------------


def test_no_block_adds_no_column():
    """The default is off: a document that names none gets what it had."""
    assert block_feature_names(()) == ()
    assert block_feature_names(None) == ()
    assert _build((), *_tape(_walk(_DAY_MINUTES))) == {}


def test_each_block_can_be_selected_alone():
    """Every subset is legal and the order is the module's, not the caller's."""
    assert normalise_blocks(["cross", "tod"]) == ("tod", "cross")
    assert len(block_causal_names(("tod",))) == 29
    assert len(block_causal_names(("bar",))) == 9
    assert len(block_causal_names(("cross",))) == 17
    # tod and bar share the two session-open returns; they emit once.
    assert len(block_causal_names(BLOCKS)) == 29 + 7 + 17
    assert block_fold_names(BLOCKS) == (
        "tod_vol_now", "tod_mean_bucket", "vol_rel_5",
    )


def test_a_block_that_is_not_a_block_is_refused():
    assert block_problems(["tod", "quotes"])
    assert block_problems(["tod", "tod"])
    assert block_problems("tod")
    assert block_problems(None) == []


def test_block_columns_land_after_the_existing_ones():
    """Turning a block on shifts no column that was already there."""
    scales = [{"width": 2, "tag": "2m", "cross_session": False}]
    before = _emit_feature_names(2, scales, ("SPY",), ("tech",))
    after = _emit_feature_names(2, scales, ("SPY",), ("tech",), (), ("tod",))
    assert after[: len(before)] == before
    assert after[len(before):] == block_feature_names(("tod",))


def test_fold_fitted_columns_are_zero_placeholders_here():
    """This node cannot see the split, so it emits no fitted value."""
    built = _build(("tod",), *_tape(_walk(_DAY_MINUTES)))
    for name in block_fold_names(("tod",)):
        assert np.array_equal(built[name], np.zeros(_DAY_MINUTES))


# --------------------------------------------------------------------
# the clock-encoding bug
# --------------------------------------------------------------------


def test_the_open_and_the_close_no_longer_share_a_clock_value():
    """P2's bug: a whole circle over the session wrapped 16:00 onto 09:30."""
    minutes = 391
    ms = np.asarray(
        [
            int((_OPEN + timedelta(minutes=m)).timestamp() * 1000)
            for m in range(minutes)
        ],
        dtype=np.int64,
    )
    close = np.full(minutes, 100.0)
    columns = _session_feature_arrays(
        ms, close, close, close, close, np.full(minutes, 1.0), 2,
        _MAX_GAP_MS, (), [{"width": 2, "tag": "2m", "cross_session": False}],
        SESSION,
    )
    assert columns["tod_sin"][0] == pytest.approx(0.0, abs=1e-12)
    assert columns["tod_cos"][0] == pytest.approx(1.0)
    # 390 minutes past the open is 16:00, half a circle away, not back at
    # the start.
    assert columns["tod_cos"][390] == pytest.approx(-1.0)
    assert columns["tod_cos"][0] != pytest.approx(columns["tod_cos"][390])


# --------------------------------------------------------------------
# block A — time of day, by hand
# --------------------------------------------------------------------


def test_tod_clock_columns_by_hand():
    minutes = 200
    ms, close, volume = _tape(_walk(minutes), minutes=minutes)
    built = _build(("tod",), ms, close, volume)
    span = 390.0
    assert built["tod_frac"][0] == pytest.approx(0.0)
    assert built["tod_frac"][60] == pytest.approx(60.0 / span)
    # 09:30 is bucket 0; 10:00 is bucket 1; 12:45 (195 min) is bucket 6.
    assert built["hh_bucket"][0] == 0.0
    assert built["hh_bucket"][30] == 1.0
    assert built["hh_bucket"][195] == 6.0
    assert built["hh_00"][29] == 1.0 and built["hh_00"][30] == 0.0
    assert built["hh_06"][195] == 1.0
    assert built["is_open30"][29] == 1.0 and built["is_open30"][30] == 0.0
    # Lunch is 12:00-13:00, i.e. 150 to 210 minutes past a 09:30 open.
    assert built["is_lunch"][149] == 0.0
    assert built["is_lunch"][150] == 1.0
    assert built["is_lunch"][199] == 1.0
    # Nothing in a 200-minute session reaches 15:30 or 15:55.
    assert built["is_close30"].max() == 0.0
    assert built["is_close5"].max() == 0.0


def test_tod_calendar_columns_by_hand():
    ms, close, volume = _tape(_walk(_DAY_MINUTES * 2), days=2)
    built = _build(("tod",), ms, close, volume)
    # 2026-01-05 is a Monday, 2026-01-06 a Tuesday.
    assert built["dow_mon"][0] == 1.0 and built["dow_tue"][0] == 0.0
    assert built["dow_tue"][_DAY_MINUTES] == 1.0
    # The 5th and 6th of January are neither the first two nor the last
    # three days of the month.
    assert built["is_month_first2"].max() == 0.0
    assert built["is_month_last3"].max() == 0.0


def test_tod_session_open_returns_by_hand():
    minutes = 40
    prices = _walk(minutes, start=100.0, step=1.0)
    ms, close, volume = _tape(prices, minutes=minutes)
    built = _build(("tod",), ms, close, volume)
    # open = high = low = close, so the session open is 100.0.
    assert built["ret_since_open"][0] == pytest.approx(0.0)
    assert built["ret_since_open"][10] == pytest.approx(math.log(110.0 / 100.0))
    # ret_first30 is zero until 10:00, then the 09:30->10:00 move, held.
    assert built["ret_first30"][29] == pytest.approx(0.0)
    expected = math.log(130.0 / 100.0)
    assert built["ret_first30"][30] == pytest.approx(expected)
    assert built["ret_first30"][39] == pytest.approx(expected)


def test_gap_x_open30_is_the_overnight_move_only_in_the_first_half_hour():
    prices = _walk(_DAY_MINUTES) + _walk(_DAY_MINUTES, start=200.0)
    ms, close, volume = _tape(prices, days=2)
    columns, _ = _internals(ms, close, volume)
    built = _build(("tod",), ms, close, volume)
    overnight = columns["overnight_gap"][_DAY_MINUTES]
    assert overnight == pytest.approx(math.log(200.0 / prices[_DAY_MINUTES - 1]))
    assert built["gap_x_open30"][_DAY_MINUTES] == pytest.approx(overnight)
    # Session two runs 30 bars, so every bar of it is inside the first
    # half hour; the first session has no overnight move at all.
    assert np.isnan(built["gap_x_open30"][0])


# --------------------------------------------------------------------
# block B — bar-derived, by hand
# --------------------------------------------------------------------


def test_sameslot_return_reads_the_previous_session_not_a_bar_offset():
    """Sessions of different length must not slide the slot."""
    prices = _walk(_DAY_MINUTES) + _walk(_DAY_MINUTES, start=300.0, step=2.0)
    ms, close, volume = _tape(prices, days=2)
    built = _build(("bar",), ms, close, volume)
    # A bar 20 minutes into session two reads session one from minute 0
    # (clamped: 20 - 30 is before the open) to minute 20.
    got = built["ret_sameslot_1d"][_DAY_MINUTES + 20]
    assert got == pytest.approx(math.log(prices[20] / prices[0]))
    # Session one has nothing behind it.
    assert np.isnan(built["ret_sameslot_1d"][20])
    # Five sessions are needed for the five-day mean.
    assert np.isnan(built["ret_sameslot_5d"][_DAY_MINUTES + 20])


def test_vol_scaled_past_returns_by_hand():
    """A constant one-minute return makes the scaling arithmetic readable."""
    step = math.log(1.01)
    # The volatility window is 390 minutes, so the tape must be a full
    # session long before the scaling is defined at all.
    minutes = 400
    prices = [100.0 * math.exp(step * i) for i in range(minutes)]
    ms, close, volume = _tape(prices, minutes=minutes)
    built = _build(("bar",), ms, close, volume)
    at = 395
    # Every finite one-minute return equals `step`, so the root mean
    # square over the window is `step` and a k-minute move is k*step.
    assert built["ret_5_z"][at] == pytest.approx(
        5.0 * step / (step * math.sqrt(5.0)), rel=1e-6,
    )
    assert built["ret_15_z"][at] == pytest.approx(
        15.0 * step / (step * math.sqrt(15.0)), rel=1e-6,
    )
    # Long and short windows see the same constant volatility.
    assert built["rv_ratio_30_390"][at] == pytest.approx(1.0, rel=1e-6)


def test_the_long_windows_are_a_one_time_warmup_not_a_daily_hole():
    """A NaN inside every session would cost that fraction of every row."""
    prices = _walk(_DAY_MINUTES * 3)
    ms, close, volume = _tape(prices, days=3)
    built = _build(("bar",), ms, close, volume)
    # ret_since_open and ret_first30 are finite from the first bar of
    # every session, including the sessions after the first.
    for start in (0, _DAY_MINUTES, 2 * _DAY_MINUTES):
        assert np.isfinite(built["ret_since_open"][start])
        assert np.isfinite(built["ret_first30"][start])


# --------------------------------------------------------------------
# block C — cross-stock, by hand
# --------------------------------------------------------------------


def _cross_fixture():
    """One symbol plus a market and a sector series, all 30 bars."""
    prices = [100.0 * math.exp(0.001 * i) for i in range(_DAY_MINUTES)]
    ms, close, volume = _tape(prices)
    market = np.full(_DAY_MINUTES, 0.0004)
    market[0] = np.nan
    sector = np.full(_DAY_MINUTES, 0.0002)
    sector[0] = np.nan
    return ms, close, volume, market, sector


def test_market_and_sector_lags_by_hand():
    ms, close, volume, market, sector = _cross_fixture()
    market = market.copy()
    market[7] = 0.05
    built = _build(
        ("cross",), ms, close, volume, market=market, sector=sector,
    )
    assert built["mkt_lag_1"][8] == pytest.approx(0.05)
    assert built["mkt_lag_2"][9] == pytest.approx(0.05)
    assert built["mkt_lag_5"][12] == pytest.approx(0.05)
    # Lag 0 of the sector fund is the fund's own minute, known at the bar.
    assert built["sec_lag_0"][5] == pytest.approx(0.0002)
    assert built["sec_lag_1"][5] == pytest.approx(0.0002)
    # A lag never reaches back past the first bar of the session.
    assert np.isnan(built["mkt_lag_1"][0])


def test_sector_residual_is_the_plain_difference():
    ms, close, volume, market, sector = _cross_fixture()
    built = _build(
        ("cross",), ms, close, volume, market=market, sector=sector,
    )
    at = 10
    own = 5.0 * 0.001
    assert built["res_sec_cum_5"][at] == pytest.approx(
        own - 5.0 * 0.0002, rel=1e-9,
    )


def test_market_residual_uses_the_trailing_beta():
    """A market that moves in lockstep gives beta one and a flat residual."""
    rng = np.random.default_rng(7)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.001, _DAY_MINUTES)))
    ms, close, volume = _tape(list(prices))
    _, inner = _internals(ms, close, volume)
    market = inner["ret1"].copy()  # the market IS this symbol's own move
    sector = np.zeros(_DAY_MINUTES)
    built = _build(
        ("cross",), ms, close, volume, market=market, sector=sector,
    )
    # Identical series: beta is one and the residual is zero once the
    # beta window has filled.
    assert built["res_mkt_lag_1"][20] == pytest.approx(0.0, abs=1e-9)
    assert built["res_mkt_cum_30"][29] == pytest.approx(0.0, abs=1e-9)


def test_cross_without_its_series_refuses_rather_than_guessing():
    ms, close, volume = _tape(_walk(_DAY_MINUTES))
    with pytest.raises(ValueError, match="cross"):
        _build(("cross",), ms, close, volume)


# --------------------------------------------------------------------
# NO LOOK-AHEAD — the point of the exercise
# --------------------------------------------------------------------


@pytest.mark.parametrize("block", BLOCKS)
def test_no_column_moves_when_only_the_future_changes(block):
    """Prefix invariance: the single test that would catch a leak.

    Two tapes agree up to bar ``cut`` and disagree wildly after it. Every
    block column at every bar up to ``cut`` must be identical. A column
    that read one bar ahead — a centred window, an off-by-one lag, a
    statistic fitted on the whole sample — would differ here.
    """
    days = 6
    n = _DAY_MINUTES * days
    cut = _DAY_MINUTES * 5 + 10
    rng = np.random.default_rng(11)
    base = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.001, n)))
    tampered = base.copy()
    # Not a nudge: a different world after the cut.
    tampered[cut + 1:] = base[cut + 1:] * np.exp(
        np.cumsum(rng.normal(0.05, 0.05, n - cut - 1))
    )
    ms, _, volume = _tape(list(base), days=days)
    market = np.concatenate(([np.nan], rng.normal(0.0, 0.001, n - 1)))
    sector = np.concatenate(([np.nan], rng.normal(0.0, 0.001, n - 1)))
    market_bad = market.copy()
    sector_bad = sector.copy()
    market_bad[cut + 1:] = rng.normal(0.2, 0.2, n - cut - 1)
    sector_bad[cut + 1:] = rng.normal(0.2, 0.2, n - cut - 1)
    honest = _build(
        (block,), ms, base, volume, market=market, sector=sector,
    )
    tampered_out = _build(
        (block,), ms, tampered, volume,
        market=market_bad, sector=sector_bad,
    )
    assert set(honest) == set(tampered_out)
    for name, values in honest.items():
        left = values[: cut + 1]
        right = tampered_out[name][: cut + 1]
        assert np.array_equal(left, right, equal_nan=True), (
            f"{name} moved when only the future changed — it reads ahead"
        )


def test_the_future_change_does_move_later_columns():
    """The leak test would pass on a broken build if nothing ever moved."""
    days = 6
    n = _DAY_MINUTES * days
    cut = _DAY_MINUTES * 5 + 10
    base = np.asarray(_walk(n), dtype=np.float64)
    tampered = base.copy()
    tampered[cut + 1:] += 50.0
    ms, _, volume = _tape(list(base), days=days)
    honest = _build(("bar",), ms, base, volume)
    other = _build(("bar",), ms, tampered, volume)
    assert not np.array_equal(
        honest["ret_since_open"][cut + 1:],
        other["ret_since_open"][cut + 1:],
        equal_nan=True,
    )


# --------------------------------------------------------------------
# NO LOOK-AHEAD — the fold-fitted statistics
# --------------------------------------------------------------------


def _fold_inputs(n=120, seed=3):
    rng = np.random.default_rng(seed)
    minutes = np.asarray([float(i % 30) for i in range(n)])
    bucket = np.floor(minutes / 30.0)
    ret = rng.normal(0.0, 0.001, n)
    volume = rng.uniform(50.0, 150.0, n)
    train = np.zeros(n, dtype=bool)
    train[: n // 2] = True
    return minutes, bucket, ret, volume, train


def test_a_fold_statistic_does_not_move_when_validation_rows_change():
    """Scramble everything the fit must not see; the fit must not move."""
    minutes, bucket, ret, volume, train = _fold_inputs()
    scrambled_ret = ret.copy()
    scrambled_vol = volume.copy()
    rng = np.random.default_rng(99)
    scrambled_ret[~train] = rng.normal(0.0, 5.0, int((~train).sum()))
    scrambled_vol[~train] = rng.uniform(1e6, 2e6, int((~train).sum()))
    first = fit_fold_stats(
        BLOCKS, minutes=minutes, bucket=bucket, ret=ret, volume=volume,
        train=train,
    )
    second = fit_fold_stats(
        BLOCKS, minutes=minutes, bucket=bucket, ret=scrambled_ret,
        volume=scrambled_vol, train=train,
    )
    for key in ("tod_vol", "tod_mean", "vol_slot"):
        assert np.array_equal(first[key], second[key], equal_nan=True), (
            f"{key} moved with the validation rows — it was fitted on them"
        )


def test_a_fold_statistic_is_not_fitted_on_the_whole_sample():
    """A quiet training fold must not inherit a loud validation fold."""
    n = 200
    minutes = np.asarray([float(i % 20) for i in range(n)])
    bucket = np.zeros(n)
    ret = np.where(np.arange(n) < 100, 0.001, 1.0)
    volume = np.where(np.arange(n) < 100, 100.0, 1e6)
    train = np.arange(n) < 100
    fitted = fit_fold_stats(
        BLOCKS, minutes=minutes, bucket=bucket, ret=ret, volume=volume,
        train=train,
    )
    # Training returns are a constant 0.001, so the per-slot spread is
    # zero and nothing about the loud half leaks into the curve.
    assert float(np.nanmax(fitted["tod_vol"])) == pytest.approx(0.0, abs=1e-12)
    assert float(np.nanmax(fitted["vol_slot"])) == pytest.approx(100.0)
    applied = apply_fold_stats(
        fitted, BLOCKS, minutes=minutes, bucket=bucket, volume=volume,
    )
    # The validation half is DIVIDED by the training norm, so it reads
    # far above it rather than being normalised away.
    assert applied["vol_rel_5"][150] == pytest.approx(math.log(1e6 / 100.0))


def test_fold_statistics_by_hand():
    """Two slots, four training rows, numbers small enough to check."""
    minutes = np.asarray([0.0, 1.0, 0.0, 1.0, 0.0, 1.0])
    bucket = np.zeros(6)
    ret = np.asarray([0.02, -0.04, -0.02, 0.04, 9.0, 9.0])
    volume = np.asarray([10.0, 30.0, 30.0, 90.0, 1.0, 1.0])
    train = np.asarray([True, True, True, True, False, False])
    fitted = fit_fold_stats(
        ("tod", "bar"), minutes=minutes, bucket=bucket, ret=ret,
        volume=volume, train=train, smooth=1,
    )
    # Slot 0 holds +0.02 and -0.02: mean 0, root mean square 0.02.
    assert fitted["tod_vol"][0] == pytest.approx(0.02)
    assert fitted["tod_vol"][1] == pytest.approx(0.04)
    # One bucket holds all four training returns; they sum to zero.
    assert fitted["tod_mean"][0] == pytest.approx(0.0)
    # Slot 0 volume mean is 20, slot 1 is 60.
    assert fitted["vol_slot"][0] == pytest.approx(20.0)
    assert fitted["vol_slot"][1] == pytest.approx(60.0)
    applied = apply_fold_stats(
        fitted, ("tod", "bar"), minutes=minutes, bucket=bucket,
        volume=volume,
    )
    assert applied["vol_rel_5"][0] == pytest.approx(math.log(10.0 / 20.0))
    assert applied["vol_rel_5"][3] == pytest.approx(math.log(90.0 / 60.0))
    assert applied["tod_vol_now"][2] == pytest.approx(0.02)


# --------------------------------------------------------------------
# the nodes, wired
# --------------------------------------------------------------------


def _bar_rows(symbol, prices, days=1, minutes=_DAY_MINUTES):
    rows = []
    i = 0
    for day in range(days):
        for minute in range(minutes):
            price = prices[i]
            rows.append({
                "symbol": symbol,
                "asof_ms": _ms(day, minute),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 100.0 + minute,
            })
            i += 1
    return rows


def _blocks_spec():
    return {
        "symbols": ["AAPL", "SPY", "XLK"],
        "tradable": ["AAPL"],
        "reference": ["SPY"],
        "cross": ["XLK"],
        "market": "SPY",
        "sector_etf": {"AAPL": "XLK"},
        "holidays": [],
        "lookback": 2,
        "max_gap_minutes": 5,
        "period_ms": 60_000,
        "offset_ms": 0,
        "price_field": "close",
        "session": dict(SESSION),
        "scales": [
            {"width": 5, "tag": "5m", "cross_session": True},
        ],
        "horizon": {
            "lead_start": 1, "lead_step": 1, "lead_stop": 2,
            "anchors": [2], "top_k": 1, "se_mult": 2.0, "band_leads": 2,
        },
    }


def _feature_frames(tmp_path, blocks):
    SessionFeatureRows._cached_key = None
    spec = _blocks_spec()
    days = 2
    n = _DAY_MINUTES * days
    rng = np.random.default_rng(5)
    records = []
    for symbol, drift in (("AAPL", 0.001), ("SPY", 0.0008), ("XLK", 0.0009)):
        path = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.001, n)))
        records.extend(_bar_rows(symbol, list(path), days=days))
    node = SessionFeatureRows(
        "features", {"layout": "columns", "feature_blocks": list(blocks)},
    )
    ctx = NodeContext(name="t", asof="2026-01-08", run_dir=str(tmp_path))
    assert node.validate_inputs({"records": records, "spec": spec}) == []
    return node.run(ctx, {"records": records, "spec": spec})["records"], spec


def test_the_feature_node_emits_the_block_columns_and_skips_cross_symbols(
    tmp_path,
):
    frames, _ = _feature_frames(tmp_path, BLOCKS)
    symbols = {frame["symbol"] for frame in frames}
    # XLK is a cross-only symbol: read for its returns, never emitted.
    assert symbols == {"AAPL", "SPY"}
    frame = next(f for f in frames if f["symbol"] == "AAPL")
    names = list(frame["names"])
    for name in block_feature_names(BLOCKS):
        assert name in names
    assert frame["X"].shape[1] == len(names)
    assert names[-3:] == list(block_fold_names(BLOCKS))


def test_the_fold_node_fills_the_placeholders_from_training_rows(tmp_path):
    frames, _ = _feature_frames(tmp_path, BLOCKS)
    frame = next(f for f in frames if f["symbol"] == "AAPL")
    fold_start = len(frame["names"]) - 3
    assert not frame["X"][:, fold_start:].any()
    ctx = NodeContext(name="t", asof="2026-01-08", run_dir=str(tmp_path))
    node = FoldFeatureStats("foldstats", {
        "blocks": list(BLOCKS),
        "train_end_ms": _ms(0, _DAY_MINUTES - 1),
        "volume_column": "vol_5m",
    })
    assert node.validate_inputs({"records": frames}) == []
    node.run(ctx, {"records": frames})
    filled = frame["X"][:, fold_start:]
    assert filled.any()
    index = {name: i for i, name in enumerate(frame["names"])}
    # The volume norm inherits the source column's warmup and nothing
    # else: it costs no row that vol_5m does not already cost.
    source = np.isfinite(frame["X"][:, index["vol_5m"]])
    assert np.isfinite(filled[:, :2]).all()
    assert np.array_equal(np.isfinite(filled[:, 2]), source)


def test_the_fold_node_refuses_a_frame_it_was_not_built_for(tmp_path):
    frames, _ = _feature_frames(tmp_path, ("tod",))
    ctx = NodeContext(name="t", asof="2026-01-08", run_dir=str(tmp_path))
    node = FoldFeatureStats("foldstats", {
        "blocks": ["tod", "bar"],
        "train_end_ms": _ms(0, 5),
    })
    with pytest.raises(ValueError, match="feature_blocks"):
        node.run(ctx, {"records": frames})


def test_the_fold_node_reads_only_the_training_window(tmp_path):
    """Same frames, same train window, scrambled validation prices."""
    ctx = NodeContext(name="t", asof="2026-01-08", run_dir=str(tmp_path))
    train_end = _ms(0, _DAY_MINUTES - 1)
    frames, _ = _feature_frames(tmp_path, ("tod",))
    frame = next(f for f in frames if f["symbol"] == "AAPL")
    index = {name: i for i, name in enumerate(frame["names"])}
    stamps = np.asarray(frame["asof_ms"], dtype=np.int64)
    node = FoldFeatureStats(
        "foldstats", {"blocks": ["tod"], "train_end_ms": train_end},
    )
    node.run(ctx, {"records": frames})
    honest = frame["X"][:, -2:].copy()
    # Now wreck every validation row's own return and refit.
    future = stamps > train_end
    frame["X"][future, index["ret_lag_0"]] = 7.0
    frame["X"][:, -2:] = 0.0
    node.run(ctx, {"records": frames})
    assert np.array_equal(frame["X"][:, -2:], honest), (
        "the fitted columns moved with the validation rows"
    )


def test_the_universe_refuses_cross_wiring_it_cannot_honour(tmp_path):
    spec = _blocks_spec()
    del spec["sector_etf"]
    node = SessionFeatureRows(
        "features", {"layout": "columns", "feature_blocks": ["cross"]},
    )
    problems = node.validate_inputs({"records": [], "spec": spec})
    assert any("sector_etf" in problem for problem in problems)
