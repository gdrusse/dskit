"""Minute-level features and 15-horizon labels: point-in-time, gap-refusing, family-droppable.

Synthetic bars only. Expected values are restated here in plain Python from the fixture bars
(a deliberate independent restatement: an assertion read from the node asserts nothing). The
leak tests change what the rule must exclude and assert no feature moves; a control changes the
last legal bar and asserts they do, so each test is known to have teeth.
"""

import json
import math
import os
import random

import pytest

from crypto_trading.minute_features import MinuteFeatures

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR = 60_000
#: 2026-01-05 00:00 UTC, a Monday.
START = 1_767_571_200_000
A, B = "AAA", "BBB"
SMALL = {
    "assets": [A, B], "scales": [2, 5, 10], "ewma_half_lives": [3], "ewma_lookback_multiple": 3,
    "range_windows": [4, 8], "flow_window": 6, "taker_windows": [3, 6], "scale_window": 10,
    "shock_k": 2.5, "shock_window": 5, "shock_max_minutes": 8, "history_minutes": 13,
    "max_horizon": 4, "bvol_change_minutes": 5, "bvol_max_age_ms": 120_000,
}
SMALL_NEED = 13  # the widest window of SMALL: shock 8 + 5 (also history_minutes); rms 10, ewma 3 * 3 = 9
GRID = (5, 15, 60, 240, 1440, 10080)  # the plan's scale grid, restated on purpose
FAMILIES = ("ret", "trend", "vol", "range", "shock", "flow", "clock", "holiday", "xasset", "bvol")
SCALE = "meta__scale"  # restated on purpose: the label divisor lives under a reserved non-feature prefix
LABEL_AND_KEYS = {"asset", "t_ms", SCALE}


def bars(asset, n, seed, start=START, drop=()):
    """Build ``n`` 1-minute kline rows of a seeded random walk, skipping the indexes in ``drop``."""
    rng = random.Random(seed)
    price, rows = 100.0, []
    for i in range(n):
        move = math.exp(0.002 * rng.gauss(0, 1))
        open_, price = price, price * move
        volume = 5.0 + 10.0 * rng.random()
        draws = (rng.random(), rng.random(), rng.randrange(50), rng.random())  # drawn even for a dropped bar
        if i in drop:
            continue
        rows.append({
            "asset": asset, "open_time_ms": start + i * BAR, "open": open_,
            "high": max(open_, price) * (1 + 0.001 * draws[0]),
            "low": min(open_, price) * (1 - 0.001 * draws[1]), "close": price, "volume": volume,
            "close_time_ms": start + (i + 1) * BAR - 1, "trades": 50 + draws[2],
            "taker_buy_volume": volume * (0.3 + 0.4 * draws[3]),
        })
    return rows


def bvol(asset, n, seed, start=START, step=1):
    """Build a BVOL reading every ``step`` minutes (index points)."""
    rng = random.Random(seed)
    level, rows = 50.0, []
    for i in range(0, n, step):
        level += rng.gauss(0, 0.2)
        rows.append({"asset": asset, "calc_time_ms": start + i * BAR, "index_value": level})
    return rows


def world(n=60, drop_a=(), drop_b=()):
    return bars(A, n, 1, drop=drop_a) + bars(B, n, 2, drop=drop_b)


def run(klines, bvol_rows=None, **over):
    inputs = {"klines": klines}
    if bvol_rows is not None:
        inputs["bvol"] = bvol_rows
    node = MinuteFeatures("minute", {**SMALL, **over})
    return node.run(None, inputs)


def by_key(out):
    return {(r["asset"], r["t_ms"]): r for r in out["records"]}


def feature_columns(row):
    return [k for k in row if k not in LABEL_AND_KEYS and not k.startswith("y_h") and not k.startswith("meta__")]


def close_at(rows, asset, i):
    return next(r["close"] for r in rows if r["asset"] == asset and r["open_time_ms"] == START + i * BAR)


def closes(rows, asset):
    return [r["close"] for r in sorted(rows, key=lambda r: r["open_time_ms"]) if r["asset"] == asset]


def t_of(i):
    """The information instant of the bar at index ``i``: its close_time_ms."""
    return START + (i + 1) * BAR - 1


# ---------------------------------------------------------------------------
# shape, names, values
# ---------------------------------------------------------------------------


def test_columns_are_family_prefixed_and_labels_are_named():
    rows = run(world(), bvol(A, 60, 3) + bvol(B, 60, 4))["records"]
    assert rows
    for row in rows:
        for column in feature_columns(row):
            assert column.split("__", 1)[0] in FAMILIES, column
        assert [k for k in row if k.startswith("y_h")] == [f"y_h{h}" for h in range(1, 5)]
        assert {"asset", "t_ms", SCALE} <= set(row) and "scale" not in row
    names = set(feature_columns(rows[0]))
    assert {f"ret__r{n}" for n in (2, 5, 10)} <= names
    assert {f"trend__ma_{n}" for n in (2, 5, 10)} <= names
    assert {"vol__rms_5", "vol__rms_10", "vol__ewma_3", "vol__hl_5", "vol__jump_5"} <= names
    assert {f"range__{side}_{w}" for side in ("hi", "lo") for w in (4, 8)} <= names
    assert {"shock__since"} <= names
    assert {"clock__month_end", "clock__us_open", "clock__funding_min"} <= names
    assert {"holiday__is", "holiday__prev", "holiday__next"} <= names
    assert {"flow__log_volume", "flow__log_trades", "flow__taker_3", "flow__taker_6"} <= names
    assert {"clock__tod_sin", "clock__tod_cos", "clock__dow_sin", "clock__dow_cos"} <= names
    assert {"xasset__r2", "xasset__rms_5", "xasset__ewma_3", "xasset__hl_5", "xasset__jump_5"} <= names
    assert {"xasset__pair_log", "xasset__pair_chg_2", "xasset__pair_chg_10"} <= names
    assert {"bvol__level", "bvol__change"} <= names


def test_values_match_plain_python():
    klines = world()
    out = by_key(run(klines, bvol(A, 60, 3) + bvol(B, 60, 4)))
    i = 30
    row = out[(A, t_of(i))]
    c = closes(klines, A)
    rets = [math.log(c[j] / c[j - 1]) for j in range(1, len(c))]  # rets[j - 1] is the return INTO bar j
    window = lambda w: rets[i - w:i]  # noqa: E731  the w returns ending at bar i
    for n in (2, 5, 10):
        assert row[f"ret__r{n}"] == pytest.approx(math.log(c[i] / c[i - n]))
    for w in (5, 10):
        assert row[f"vol__rms_{w}"] == pytest.approx(math.sqrt(sum(r * r for r in window(w)) / w))
    weights = [0.5 ** (age / 3) for age in range(9)]  # age 0 is the newest return
    squared = [r * r for r in reversed(window(9))]
    assert row["vol__ewma_3"] == pytest.approx(math.sqrt(sum(w * s for w, s in zip(weights, squared)) / sum(weights)))
    bar = sorted((r for r in klines if r["asset"] == A), key=lambda r: r["open_time_ms"])
    para = [math.log(b["high"] / b["low"]) ** 2 / (4 * math.log(2)) for b in bar[i - 4:i + 1]]
    assert row["vol__hl_5"] == pytest.approx(math.sqrt(sum(para) / 5))
    vol6 = [b["volume"] for b in bar[i - 5:i + 1]]
    assert row["flow__log_volume"] == pytest.approx(math.log(bar[i]["volume"] / (sum(vol6) / 6)))
    trades6 = [b["trades"] for b in bar[i - 5:i + 1]]
    assert row["flow__log_trades"] == pytest.approx(math.log(bar[i]["trades"] / (sum(trades6) / 6)))
    assert row["flow__taker_3"] == pytest.approx(
        sum(b["taker_buy_volume"] for b in bar[i - 2:i + 1]) / sum(b["volume"] for b in bar[i - 2:i + 1]))
    scale = math.sqrt(sum(r * r for r in window(10)) / 10)
    assert row[SCALE] == pytest.approx(scale) == pytest.approx(row["vol__rms_10"])
    for h in range(1, 5):
        assert row[f"y_h{h}"] == pytest.approx(math.log(c[i + h] / c[i]) / scale)
    other = closes(klines, B)
    assert row["xasset__r2"] == pytest.approx(math.log(other[i] / other[i - 2]))


def test_clock_is_utc_and_taken_at_the_information_instant():
    out = by_key(run(world()))
    # bar 5 closes at 00:06:00 UTC Monday: the information instant is 06 minutes into the day
    row = out[(A, t_of(5 + 30))]  # 00:36 -> the instant is the boundary 00:36:00
    tod = 36 / 1440 * 2 * math.pi
    assert row["clock__tod_sin"] == pytest.approx(math.sin(tod))
    assert row["clock__tod_cos"] == pytest.approx(math.cos(tod))
    assert row["clock__dow_sin"] == pytest.approx(0.0, abs=1e-12)  # Monday = 0
    assert row["clock__dow_cos"] == pytest.approx(1.0)


def test_clock_day_of_week_follows_the_utc_calendar():
    wed = START + 2 * 86_400_000 + 6 * 3_600_000  # Wednesday 06:00 UTC
    klines = bars(A, 60, 1, start=wed) + bars(B, 60, 2, start=wed)
    row = next(r for r in run(klines)["records"] if r["asset"] == A)
    assert row["clock__dow_sin"] == pytest.approx(math.sin(2 * 2 * math.pi / 7))
    # 06:00 plus the 10 warm-up minutes
    assert row["clock__tod_sin"] == pytest.approx(math.sin((6 * 60 + SMALL_NEED + 1) / 1440 * 2 * math.pi))


def _alternating(asset, n, r, start=START):
    """Bars whose close alternates x e^{+r}, x e^{-r}; high/low are the open/close extremes (no wick)."""
    rows, price = [], 100.0
    for i in range(n):
        open_, price = price, price * math.exp(r if i % 2 == 0 else -r)
        rows.append({"asset": asset, "open_time_ms": start + i * BAR, "open": open_, "high": max(open_, price),
                     "low": min(open_, price), "close": price, "volume": 10.0,
                     "close_time_ms": start + (i + 1) * BAR - 1, "trades": 50, "taker_buy_volume": 4.0})
    return rows


def test_vol_columns_take_the_known_per_minute_std_of_the_bars():
    r = 0.01  # every one-minute log return is +-r, so every squared-return mean is exactly r ** 2
    row = by_key(run(_alternating(A, 60, r) + _alternating(B, 60, r)))[(A, t_of(30))]
    for column in ("vol__rms_5", "vol__rms_10", "vol__ewma_3"):
        assert row[column] == pytest.approx(r), column
    # Parkinson: ln(high / low) is r on every bar, so the estimate is sqrt(r ** 2 / (4 ln 2))
    assert row["vol__hl_5"] == pytest.approx(r / math.sqrt(4 * math.log(2)))
    assert row[SCALE] == pytest.approx(r)


def test_importing_the_module_pulls_no_heavy_library():
    import subprocess
    import sys

    code = "import sys, crypto_trading.minute_features; sys.exit(1 if 'numpy' in sys.modules else 0)"
    done = subprocess.run([sys.executable, "-c", code], cwd=CHILD_ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_default_knobs_are_the_plan_table():
    klines = bars("BTC", 10_100, 1) + bars("ETH", 10_100, 2)
    node = MinuteFeatures("m", {"assets": ["BTC", "ETH"]})
    assert node.validate_params(node.params) == []
    assert MinuteFeatures.DEFAULTS["scales"] == GRID  # restated on purpose: the plan's grid
    out = node.run(None, {"klines": klines})
    row = out["records"][0]
    cols = feature_columns(row)
    count = lambda family: [c for c in cols if c.startswith(family + "__")]  # noqa: E731
    assert sorted(count("ret")) == sorted(f"ret__r{n}" for n in GRID)
    assert sorted(count("trend")) == sorted(f"trend__ma_{n}" for n in GRID)
    assert sorted(count("vol")) == sorted(
        [f"vol__{stem}_{w}" for stem in ("rms", "hl", "jump") for w in GRID]
        + [f"vol__ewma_{h}" for h in (15, 60)])
    assert sorted(count("range")) == sorted(f"range__{side}_{w}" for side in ("hi", "lo") for w in (60, 240, 1440))
    assert count("shock") == ["shock__since"]
    assert sorted(count("flow")) == sorted(["flow__log_volume", "flow__log_trades", "flow__taker_15", "flow__taker_60"])
    assert sorted(count("clock")) == sorted(f"clock__{n}" for n in (
        "tod_sin", "tod_cos", "dow_sin", "dow_cos", "month_end", "us_open", "funding_min"))
    assert sorted(count("holiday")) == ["holiday__is", "holiday__next", "holiday__prev"]
    assert len(count("bvol")) == 2
    assert len(count("xasset")) == len(count("ret")) + len(count("vol")) + 1 + len(GRID)
    assert [k for k in row if k.startswith("y_h")] == [f"y_h{h}" for h in range(1, 16)]
    # the widest default window is the 10080-minute scale (and the history floor): 10080 returns
    assert out["provenance"]["need_bars"] == 10_080
    assert row["t_ms"] == t_of(10_080)
    assert row[SCALE] == pytest.approx(row["vol__rms_60"])


# ---------------------------------------------------------------------------
# point in time
# ---------------------------------------------------------------------------


def _shaken(klines, asset, after_open_ms):
    """Copy ``klines`` with every bar of ``asset`` opening at or after ``after_open_ms`` replaced by a spike."""
    out = []
    for r in klines:
        if r["asset"] == asset and r["open_time_ms"] >= after_open_ms:
            r = {**r, "open": r["open"] * 3, "high": r["high"] * 4, "low": r["low"] * 2, "close": r["close"] * 3,
                 "volume": r["volume"] * 9, "trades": r["trades"] * 9, "taker_buy_volume": r["taker_buy_volume"] * 9}
        out.append(r)
    return out


@pytest.mark.parametrize("shaken_asset", [A, B])
def test_changing_a_future_bar_changes_no_feature_at_t(shaken_asset):
    klines, readings = world(), bvol(A, 60, 3) + bvol(B, 60, 4)
    base = by_key(run(klines, readings))
    i = 30
    future = START + (i + 1) * BAR  # the first bar that opens after the bar-i information instant
    moved = by_key(run(_shaken(klines, shaken_asset, future), readings))
    for asset in (A, B):
        for j in range(SMALL_NEED, i + 1):
            before, after = base[(asset, t_of(j))], moved[(asset, t_of(j))]
            assert {k: v for k, v in before.items() if not k.startswith("y_h")} == \
                   {k: v for k, v in after.items() if not k.startswith("y_h")}, (asset, j)
    # the labels of t are what a future bar is FOR
    other = B if shaken_asset == A else A
    assert base[(shaken_asset, t_of(i))]["y_h1"] != moved[(shaken_asset, t_of(i))]["y_h1"]
    assert base[(other, t_of(i))]["y_h1"] == moved[(other, t_of(i))]["y_h1"]  # a label reads only its own asset


def test_control_changing_the_last_legal_bar_moves_the_features():
    klines = world()
    i = 30
    last_legal = START + i * BAR
    moved = by_key(run(_shaken([r for r in klines if r["open_time_ms"] <= last_legal or r["asset"] == B], A, last_legal)))
    base = by_key(run(klines))
    row, other = base[(A, t_of(i))], moved[(A, t_of(i))]
    assert row["ret__r2"] != other["ret__r2"] and row["vol__rms_5"] != other["vol__rms_5"]
    assert base[(B, t_of(i))]["xasset__r2"] != moved[(B, t_of(i))]["xasset__r2"]


def test_truncating_the_future_leaves_every_earlier_feature_unchanged():
    klines = world(80)
    full = by_key(run(klines))
    cut = START + 50 * BAR
    kept = by_key(run([r for r in klines if r["open_time_ms"] < cut]))
    assert kept
    for key, row in kept.items():
        assert {k: v for k, v in row.items() if not k.startswith("y_h")} == \
               {k: v for k, v in full[key].items() if not k.startswith("y_h")}


def test_a_bvol_reading_after_t_is_not_seen_and_one_at_t_is_not_yet_known():
    readings = bvol(A, 60, 3) + bvol(B, 60, 4)
    i = 30
    after = [r for r in readings if r["calc_time_ms"] > START + i * BAR]
    spiked = [({**r, "index_value": 999.0} if r in after else r) for r in readings]
    base, moved = by_key(run(world(), readings)), by_key(run(world(), spiked))
    key = (A, t_of(i))
    assert base[key]["bvol__level"] == moved[key]["bvol__level"]
    # t_of(i) is the close of bar i; the information instant is START + (i + 1) * BAR, so the reading
    # stamped AT START + i*BAR is the newest legal one and the one stamped at START + (i + 1) * BAR is not
    legal = next(r for r in readings if r["asset"] == A and r["calc_time_ms"] == START + i * BAR)
    assert base[key]["bvol__level"] == legal["index_value"]
    at_instant = [({**r, "index_value": 999.0} if r["calc_time_ms"] == START + (i + 1) * BAR else r) for r in readings]
    assert by_key(run(world(), at_instant))[key]["bvol__level"] == legal["index_value"]


# ---------------------------------------------------------------------------
# gaps: refused by name, counted, never filled
# ---------------------------------------------------------------------------


def test_a_gap_inside_a_window_is_refused_by_name_and_counted():
    gap = 25
    out = run(world(drop_a={gap}))
    rows, prov = by_key(out), out["provenance"]["assets"]
    ts = {(a, t) for (a, t) in rows}
    # AAA has no bar `gap`; bar gap+1 has no predecessor, so it and the next 9 hold the gap in a window
    for j in range(gap + 1, gap + 1 + SMALL_NEED):
        assert (A, t_of(j)) not in ts, j
    assert (A, t_of(gap + 1 + SMALL_NEED)) in ts  # the first bar with a clean window
    assert (A, t_of(gap - 1)) in ts
    assert prov[A]["refused"]["gap_in_window"] == SMALL_NEED
    # BBB is whole but cannot be paired with a missing/invalid other side: refused by name, not dropped quietly
    assert (B, t_of(gap)) not in ts and (B, t_of(gap + 3)) not in ts
    assert prov[B]["refused"]["xasset_unavailable"] == SMALL_NEED + 1  # the missing minute itself, then the refused ones
    assert "gap_in_window" not in prov[B]["refused"]


def test_every_bar_is_either_a_row_or_a_named_refusal():
    out = run(world(drop_a={25}, drop_b={40, 41}))
    for asset, stats in out["provenance"]["assets"].items():
        assert stats["rows"] + sum(stats["refused"].values()) == stats["bars"]
        assert stats["rows"] == sum(1 for r in out["records"] if r["asset"] == asset)
        assert set(stats["refused"]) <= {"insufficient_history", "gap_in_window", "invalid_value", "zero_scale",
                                         "xasset_unavailable"}
    assert out["provenance"]["assets"][A]["refused"]["insufficient_history"] == SMALL_NEED


def test_no_value_is_filled_across_a_gap():
    klines = world(drop_a={25})
    base = by_key(run(klines))
    # a window that never touched the gap computes the same as in a world without it
    whole = by_key(run(world()))
    after_clean = 25 + 1 + SMALL_NEED
    assert base[(A, t_of(after_clean))]["vol__rms_10"] == pytest.approx(whole[(A, t_of(after_clean))]["vol__rms_10"])
    assert (A, t_of(after_clean - 1)) not in base  # the window one minute earlier still held the gap


def test_a_missing_label_bar_leaves_none_and_is_counted_but_keeps_the_row():
    i = 30
    out = run(world(drop_a={i + 3}))
    row = by_key(out)[(A, t_of(i))]
    assert row["y_h3"] is None
    assert row["y_h1"] is not None and row["y_h2"] is not None and row["y_h4"] is not None
    assert out["provenance"]["assets"][A]["label_missing"]["3"] >= 1


def test_the_last_rows_have_no_labels_beyond_the_data():
    row = by_key(run(world(40)))[(A, t_of(39))]
    assert [row[f"y_h{h}"] for h in (1, 2, 3, 4)] == [None] * 4
    penult = by_key(run(world(40)))[(A, t_of(37))]
    assert penult["y_h2"] is not None and penult["y_h3"] is None


def test_a_flat_window_has_zero_scale_and_is_refused_by_name():
    klines = world()
    for r in klines:
        if r["asset"] == A and r["open_time_ms"] < START + 20 * BAR:
            r.update(open=100.0, high=100.0, low=100.0, close=100.0)
    out = run(klines)
    assert out["provenance"]["assets"][A]["refused"]["zero_scale"] >= 1
    assert all(r[SCALE] > 0 for r in out["records"])


def test_a_nonpositive_volume_is_refused_by_name_not_logged():
    klines = world()
    next(r for r in klines if r["asset"] == A and r["open_time_ms"] == START + 20 * BAR)["volume"] = 0.0
    out = run(klines)
    assert out["provenance"]["assets"][A]["refused"].get("invalid_value", 0) >= 1
    assert (A, t_of(20)) not in by_key(out)


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dropped", FAMILIES)
def test_dropping_a_family_removes_exactly_its_prefix_and_keeps_the_same_rows(dropped):
    klines, readings = world(), bvol(A, 60, 3) + bvol(B, 60, 4)
    full = by_key(run(klines, readings))
    kept = [f for f in FAMILIES if f != dropped]
    part = by_key(run(klines, readings, families=kept))
    assert set(part) == set(full)  # the same samples in every arm, or the ablation compares different data
    for key, row in part.items():
        assert not any(c.startswith(dropped + "__") for c in row)
        assert {c for c in full[key] if not c.startswith(dropped + "__")} == set(row)
        assert all(row[c] == full[key][c] for c in row)  # labels and the meta scale unchanged by the arm


def test_the_row_set_does_not_depend_on_bvol_or_the_families():
    klines = world(drop_a={25})
    with_bvol = by_key(run(klines, bvol(A, 60, 3) + bvol(B, 60, 4)))
    without = by_key(run(klines))
    assert set(with_bvol) == set(without)
    assert set(by_key(run(klines, families=["clock"]))) == set(without)


def test_bvol_is_none_without_readings_and_when_stale():
    out = run(world())
    assert all(r["bvol__level"] is None and r["bvol__change"] is None for r in out["records"])
    assert out["provenance"]["assets"][A]["bvol_missing"] == out["provenance"]["assets"][A]["rows"]
    sparse = bvol(A, 60, 3, step=30) + bvol(B, 60, 4)  # AAA: readings at minute 0 and 30 only
    rows = by_key(run(world(), sparse, bvol_max_age_ms=2 * BAR))
    assert rows[(A, t_of(15))]["bvol__level"] is None  # the minute-0 reading is 16 minutes old
    assert rows[(B, t_of(15))]["bvol__level"] is not None


def test_bvol_change_is_the_level_now_minus_the_level_before():
    readings = bvol(A, 60, 3) + bvol(B, 60, 4)
    row = by_key(run(world(), readings))[(A, t_of(30))]
    level = {r["calc_time_ms"]: r["index_value"] for r in readings if r["asset"] == A}
    now, before = level[START + 30 * BAR], level[START + 25 * BAR]
    assert row["bvol__level"] == now and row["bvol__change"] == pytest.approx(now - before)


# ---------------------------------------------------------------------------
# default-deny params and inputs
# ---------------------------------------------------------------------------

GOOD_KNOBS = {
    "assets": [A, B], "families": ["ret"], "scales": [2, 5], "ewma_half_lives": [3],
    "ewma_lookback_multiple": 3, "range_windows": [5], "flow_window": 6, "taker_windows": [3],
    "scale_window": 10, "shock_k": 3.0, "shock_window": 5, "shock_max_minutes": 8, "history_minutes": 13,
    "month_end_days": 2, "us_open_tz": "America/New_York", "us_open_start_minute": 570,
    "us_open_minutes": 30, "funding_hours_utc": [0, 12], "holiday_exchange": "XNYS",
    "max_horizon": 4, "bvol_max_age_ms": 1000, "bvol_change_minutes": 5,
}


def test_the_good_knobs_cover_every_param():
    assert set(GOOD_KNOBS) == set(MinuteFeatures._PARAMS)  # a pin that omits a knob claims coverage it lacks
    assert MinuteFeatures.validate_params(GOOD_KNOBS) == []


@pytest.mark.parametrize("name,bad", [
    ("assets", None), ("assets", [A]), ("assets", [A, A]), ("assets", [A, B, "CCC"]), ("assets", [A, ""]),
    ("assets", [A, 3]),
    ("families", []), ("families", ["ret", "nope"]), ("families", ["ret", "ret"]), ("families", "ret"),
    ("scales", []), ("scales", [1]), ("scales", [5, 5]), ("scales", [True]), ("scales", 5),
    ("ewma_half_lives", [-1]), ("ewma_lookback_multiple", 0),
    ("range_windows", [1.5]), ("flow_window", 0), ("taker_windows", []), ("scale_window", 0),
    ("max_horizon", 0), ("bvol_max_age_ms", 0), ("bvol_change_minutes", 0),
    ("shock_k", 0), ("shock_k", -1.0), ("shock_k", float("nan")), ("shock_k", True), ("shock_window", 0),
    ("shock_max_minutes", 0), ("history_minutes", 0), ("month_end_days", 0), ("month_end_days", 32),
    ("us_open_tz", "Nowhere/Land"), ("us_open_tz", ""), ("us_open_start_minute", -1),
    ("us_open_start_minute", 1440), ("us_open_minutes", 0), ("us_open_minutes", 1441),
    ("funding_hours_utc", []), ("funding_hours_utc", [24]), ("funding_hours_utc", [0, 0]),
    ("funding_hours_utc", [-1]), ("holiday_exchange", ""), ("holiday_exchange", 5),
])
def test_bad_knobs_are_refused_by_name(name, bad):
    problems = MinuteFeatures.validate_params({**GOOD_KNOBS, name: bad})
    assert problems and any(name in p for p in problems), problems


def test_an_unknown_knob_and_a_missing_assets_are_refused():
    assert any("typo" in p for p in MinuteFeatures.validate_params({**GOOD_KNOBS, "typo": 1}))
    assert any("assets" in p for p in MinuteFeatures.validate_params({}))


def test_defaults_come_from_one_table():
    # a default has one name: validation approves exactly what run uses
    assert MinuteFeatures.validate_params({"assets": [A, B]}) == []
    assert set(MinuteFeatures.DEFAULTS) == set(MinuteFeatures._PARAMS) - {"assets"}


@pytest.mark.parametrize("klines", [None, "rows", {"AAA": []}])
def test_klines_must_be_a_list(klines):
    node = MinuteFeatures("m", SMALL)
    assert node.validate_inputs({"klines": klines})


def test_bvol_if_wired_must_be_a_list():
    node = MinuteFeatures("m", SMALL)
    assert node.validate_inputs({"klines": [], "bvol": {}})
    assert node.validate_inputs({"klines": []}) == []


def test_bad_rows_raise_with_the_defect_named():
    good = world()
    with pytest.raises(ValueError, match="CCC"):
        run(good + [{**good[0], "asset": "CCC"}])
    with pytest.raises(ValueError, match="duplicate"):
        run(good + [dict(good[0])])
    with pytest.raises(ValueError, match="close_time_ms"):
        run([{**good[0], "close_time_ms": good[0]["close_time_ms"] + 1}] + good[1:])
    with pytest.raises(ValueError, match="volume"):
        run([{**good[0], "volume": "x"}] + good[1:])
    with pytest.raises(ValueError, match="trades"):
        run([{k: v for k, v in good[0].items() if k != "trades"}] + good[1:])
    with pytest.raises(ValueError, match=B):
        run([r for r in good if r["asset"] == A])
    with pytest.raises(ValueError, match="calc_time_ms"):
        run(good, [{"asset": A, "calc_time_ms": "x", "index_value": 50.0}])


def test_unsorted_input_gives_the_same_rows():
    klines = world()
    shuffled = list(klines)
    random.Random(5).shuffle(shuffled)
    assert run(shuffled)["records"] == run(klines)["records"]


# ---------------------------------------------------------------------------
# the family-drop arms config
# ---------------------------------------------------------------------------


def _arms():
    with open(os.path.join(CHILD_ROOT, "configs", "features-arms.json"), encoding="utf-8") as handle:
        return json.load(handle)


def test_arms_are_the_declared_families_minus_one_each():
    config = _arms()
    assert config["notes"] and set(config) == {"notes", "arms", "scale_arms"}
    assert MinuteFeatures.FAMILIES == FAMILIES  # restated here on purpose: a renamed family must be seen
    arms = config["arms"]
    assert set(arms) == {"all"} | {f"minus_{f}" for f in FAMILIES}
    assert arms["all"] == list(FAMILIES)
    for family in FAMILIES:
        assert arms[f"minus_{family}"] == [f for f in FAMILIES if f != family]


@pytest.mark.parametrize("arm", ["all"] + [f"minus_{f}" for f in FAMILIES])
def test_every_arm_is_accepted_by_the_node_and_drops_by_prefix(arm):
    kept = _arms()["arms"][arm]
    assert MinuteFeatures.validate_params({**SMALL, "families": kept}) == []
    row = run(world(), bvol(A, 60, 3) + bvol(B, 60, 4), families=kept)["records"][0]
    present = {c.split("__", 1)[0] for c in feature_columns(row)}
    assert present == set(kept)


# ---------------------------------------------------------------------------
# review fixes: meta scale, taker validation, one source for constants, bad BVOL readings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["all"] + [f"minus_{f}" for f in FAMILIES])
def test_no_arm_carries_a_dropped_family_or_the_label_divisor_as_a_feature(arm):
    kept = _arms()["arms"][arm]
    dropped = set(FAMILIES) - set(kept)
    rows = run(world(), bvol(A, 60, 3) + bvol(B, 60, 4), families=kept)["records"]
    assert rows
    assert "meta" not in FAMILIES and "meta" not in MinuteFeatures.FAMILIES
    for row in rows:
        features = [c for c in row if c.split("__", 1)[0] in FAMILIES]  # what an arm selects
        assert not any(c.split("__", 1)[0] in dropped for c in row), arm
        assert not any(c.startswith("meta__") for c in features)
        assert {c for c in row if c.startswith("meta__")} == {SCALE}
        assert "scale" not in row
        assert set(row) == {"asset", "t_ms", SCALE} | set(features) | {c for c in row if c.startswith("y_h")}


def _with_taker(value, at=20):
    klines = world()
    next(r for r in klines if r["asset"] == A and r["open_time_ms"] == START + at * BAR)["taker_buy_volume"] = value
    return klines


@pytest.mark.parametrize("bad", [-4.0, 1e6])  # negative, and above the bar's volume (5 to 15)
def test_an_impossible_taker_buy_volume_is_refused_by_name(bad):
    out = run(_with_taker(bad))
    assert out["provenance"]["assets"][A]["refused"].get("invalid_value", 0) >= 1
    assert (A, t_of(20)) not in by_key(out)
    for row in out["records"]:  # and the bad bar poisons no later taker share through its window
        assert 0.0 <= row["flow__taker_3"] <= 1.0 and 0.0 <= row["flow__taker_6"] <= 1.0


def test_a_taker_buy_volume_equal_to_the_volume_is_valid():
    klines = world()
    bar = next(r for r in klines if r["asset"] == A and r["open_time_ms"] == START + 20 * BAR)
    bar["taker_buy_volume"] = bar["volume"]
    assert (A, t_of(20)) in by_key(run(klines))


def test_the_minutes_per_day_derives_from_the_bar_length():
    from crypto_trading import minute_features as mf

    assert mf._MINUTES_PER_DAY == 86_400_000 // mf.BAR_MS == 1440  # restated on purpose


def test_families_are_the_registered_family_types_in_order():
    from crypto_trading import minute_features as mf

    assert mf.FAMILIES == tuple(mf.FAMILY_TYPES) == MinuteFeatures.FAMILIES
    assert tuple(MinuteFeatures.DEFAULTS["families"]) == mf.FAMILIES


def test_need_bars_reads_the_estimators_own_lookback(monkeypatch):
    from dskit.pipeline.libs.vol_estimators import Ewma

    resolved = MinuteFeatures.resolve(SMALL)
    assert MinuteFeatures.need_bars(resolved) == SMALL_NEED
    monkeypatch.setattr(Ewma, "lookback_bars", lambda self: 51)  # 50 returns behind each EWMA value
    assert MinuteFeatures.need_bars(resolved) == 50


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "x"])
def test_a_bad_bvol_reading_is_a_missing_reading_and_never_aborts(bad):
    readings = bvol(A, 60, 3) + bvol(B, 60, 4)
    base = by_key(run(world(), readings))
    i = 30
    spoiled = [({**r, "index_value": bad} if r["asset"] == A and r["calc_time_ms"] == START + i * BAR else r)
               for r in readings]
    out = run(world(), spoiled)
    rows = by_key(out)
    assert set(rows) == set(base)  # BVOL never refuses a row
    row = rows[(A, t_of(i))]
    # the newest legal reading is gone: the level is the one before it (within the age cap) or None, never the bad value
    previous = next(r for r in readings if r["asset"] == A and r["calc_time_ms"] == START + (i - 1) * BAR)
    assert row["bvol__level"] in (None, previous["index_value"])
    assert row["bvol__level"] != base[(A, t_of(i))]["bvol__level"]
    assert rows[(B, t_of(i))] == base[(B, t_of(i))]


# ---------------------------------------------------------------------------
# scales: the grid is a param, and its ablation arms cannot drift from it
# ---------------------------------------------------------------------------


def test_scale_arms_are_the_declared_grid_minus_one_each():
    from crypto_trading import minute_features as mf

    assert mf.DEFAULT_SCALES == GRID == MinuteFeatures.DEFAULTS["scales"]
    arms = _arms()["scale_arms"]
    assert set(arms) == {f"minus_scale_{s}" for s in GRID}
    for scale in GRID:
        assert arms[f"minus_scale_{scale}"] == [s for s in GRID if s != scale]
        assert MinuteFeatures.validate_params({"assets": [A, B], "scales": arms[f"minus_scale_{scale}"]}) == []


def test_dropping_a_scale_removes_exactly_its_columns_and_keeps_rows_and_labels():
    klines = world()
    full, part = by_key(run(klines)), by_key(run(klines, scales=[2, 10]))
    assert set(part) == set(full)  # history_minutes holds the warm-up, so no scale arm changes the samples
    for key, row in part.items():
        assert "ret__r5" not in row and "trend__ma_5" not in row and "vol__jump_5" not in row
        assert "vol__rms_5" not in row and "vol__hl_5" not in row
        assert "xasset__pair_chg_5" not in row and "xasset__jump_5" not in row
        assert row["ret__r2"] == full[key]["ret__r2"] and row[SCALE] == full[key][SCALE]
        assert [row[f"y_h{h}"] for h in range(1, 5)] == [full[key][f"y_h{h}"] for h in range(1, 5)]


def test_the_scale_window_is_no_scale_of_the_grid():
    klines = world()
    base = by_key(run(klines))
    other = by_key(run(klines, scales=[2, 5]))  # no 10 in the grid, but scale_window is still 10
    assert all(other[k][SCALE] == base[k][SCALE] for k in base)


# ---------------------------------------------------------------------------
# new families: values restated in plain Python
# ---------------------------------------------------------------------------


def _walk(asset, rets, start=START, wick=1e-4):
    """Bars whose one-minute log returns are ``rets`` (bar 0 opens at 100 and closes at 100 * e^rets[0])."""
    rows, price = [], 100.0
    for i, r in enumerate(rets):
        open_, price = price, price * math.exp(r)
        rows.append({"asset": asset, "open_time_ms": start + i * BAR, "open": open_,
                     "high": max(open_, price) * (1 + wick), "low": min(open_, price) * (1 - wick), "close": price,
                     "volume": 10.0, "close_time_ms": start + (i + 1) * BAR - 1, "trades": 50,
                     "taker_buy_volume": 4.0})
    return rows


def test_trend_is_log_price_against_its_moving_average():
    klines = world()
    row = by_key(run(klines))[(A, t_of(30))]
    c = closes(klines, A)
    for s in (2, 5, 10):
        assert row[f"trend__ma_{s}"] == pytest.approx(math.log(c[30] / (sum(c[31 - s:31]) / s)))


def test_jump_share_follows_bipower_variation():
    klines = world()
    row = by_key(run(klines))[(A, t_of(30))]
    c = closes(klines, A)
    r = [math.log(c[j] / c[j - 1]) for j in range(1, len(c))]  # r[j - 1] is the return into bar j
    for s in (2, 5, 10):
        win = r[30 - s:30]
        rv = sum(x * x for x in win)
        bv = math.pi / 2 * s / (s - 1) * sum(abs(win[k]) * abs(win[k - 1]) for k in range(1, s))
        assert row[f"vol__jump_{s}"] == pytest.approx(max(0.0, 1 - bv / rv))
        assert 0.0 <= row[f"vol__jump_{s}"] <= 1.0


def test_jump_share_is_high_for_one_jump_and_zero_for_steady_returns():
    steady = [0.001 * (-1) ** i for i in range(60)]
    jumpy = list(steady)
    jumpy[40] = 0.05
    calm = by_key(run(_walk(A, steady) + _walk(B, steady)))[(A, t_of(45))]
    jump = by_key(run(_walk(A, jumpy) + _walk(B, steady)))[(A, t_of(45))]
    assert calm["vol__jump_10"] == pytest.approx(0.0, abs=1e-9)
    assert jump["vol__jump_10"] > 0.9  # one big return, its neighbours tiny: nearly all the variance is jump


def test_range_is_the_distance_from_the_trailing_extremes_in_scale_units():
    klines = world()
    row = by_key(run(klines))[(A, t_of(30))]
    bar = sorted((x for x in klines if x["asset"] == A), key=lambda x: x["open_time_ms"])
    for w in (4, 8):
        hi = max(b["high"] for b in bar[31 - w:31])
        lo = min(b["low"] for b in bar[31 - w:31])
        assert row[f"range__hi_{w}"] == pytest.approx(math.log(hi / bar[30]["close"]) / row[SCALE])
        assert row[f"range__lo_{w}"] == pytest.approx(math.log(bar[30]["close"] / lo) / row[SCALE])
        assert row[f"range__hi_{w}"] >= 0.0 and row[f"range__lo_{w}"] >= 0.0


def test_range_window_is_trailing_so_a_spike_just_outside_it_is_not_seen():
    klines = world()
    base = by_key(run(klines))
    spiked = [({**x, "high": x["high"] * 2} if x["asset"] == A and x["open_time_ms"] == START + 22 * BAR else x)
              for x in klines]
    moved = by_key(run(spiked))
    key = (A, t_of(30))  # the 8-minute window covers bars 23..30, so bar 22 is just outside
    assert moved[key]["range__hi_8"] == base[key]["range__hi_8"]
    assert moved[(A, t_of(29))]["range__hi_8"] != base[(A, t_of(29))]["range__hi_8"]  # bar 22 is inside bar 29's


def _shock_expected(rets, k, window, cap, i):
    """Minutes since the last return beyond k times the RMS of the ``window`` returns before it, capped."""
    since = cap
    for j in range(i, max(i - cap, -1), -1):
        prior = rets[j - window:j]
        vol = math.sqrt(sum(x * x for x in prior) / window)
        if abs(rets[j]) > k * vol:
            since = min(i - j, cap)
            break
    return since


def test_shock_counts_minutes_since_the_last_move_beyond_k_times_trailing_vol_and_caps():
    rets = [0.001 * (-1) ** i for i in range(70)]
    rets[30] = 0.01
    klines = _walk(A, rets) + _walk(B, rets)
    out = by_key(run(klines))
    for i in range(SMALL_NEED, 66):
        assert out[(A, t_of(i))]["shock__since"] == _shock_expected(rets, 2.5, 5, 8, i), i
    assert out[(A, t_of(33))]["shock__since"] == 3
    assert out[(A, t_of(29))]["shock__since"] == 8 and out[(A, t_of(45))]["shock__since"] == 8  # the cap
    quiet = by_key(run(klines, shock_k=50.0))
    assert all(r["shock__since"] == 8 for r in quiet.values())


def test_shock_needs_the_cap_plus_the_window_behind_it():
    out = run(world())
    assert out["provenance"]["need_bars"] >= 8 + 5  # a value capped without that history would be a fill
    assert out["provenance"]["assets"][A]["refused"]["insufficient_history"] == out["provenance"]["need_bars"]


def _at(day, hour, minute, month=1, year=2026):
    import datetime

    return int(datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def _clock_rows(start_ms, n=200, **over):
    klines = bars(A, n, 1, start=start_ms) + bars(B, n, 2, start=start_ms)
    return {r["t_ms"] + 1: r for r in run(klines, **over)["records"] if r["asset"] == A}


def test_month_end_flags_the_last_utc_days_of_the_month():
    jan31 = _clock_rows(_at(31, 0, 0))
    assert jan31 and all(r["clock__month_end"] == 1.0 for r in jan31.values())
    feb1 = _clock_rows(_at(1, 0, 0, month=2))
    assert all(r["clock__month_end"] == 0.0 for r in feb1.values())
    assert all(r["clock__month_end"] == 0.0 for r in _clock_rows(_at(30, 0, 0)).values())
    assert all(r["clock__month_end"] == 1.0 for r in _clock_rows(_at(30, 0, 0), month_end_days=2).values())
    feb28 = _clock_rows(_at(28, 0, 0, month=2))  # 2026 is not a leap year
    assert all(r["clock__month_end"] == 1.0 for r in feb28.values())


@pytest.mark.parametrize("start", [
    _at(5, 14, 0),             # Monday in winter: 09:30 New York is 14:30 UTC
    _at(6, 13, 0, month=7),    # Monday in summer: the same wall time is 13:30 UTC
])
def test_us_open_window_follows_new_york_wall_time_through_daylight_saving(start):
    rows = _clock_rows(start, 140)
    opened = start + 30 * BAR  # start is half an hour before the open in both seasons
    for instant, row in rows.items():
        expected = 1.0 if opened <= instant < opened + 60 * BAR else 0.0
        assert row["clock__us_open"] == expected, instant
    assert any(r["clock__us_open"] == 1.0 for r in rows.values())


def test_us_open_is_zero_at_weekends_and_follows_its_params():
    sat = _clock_rows(_at(10, 14, 0), 140)
    assert all(r["clock__us_open"] == 0.0 for r in sat.values())
    narrow = _clock_rows(_at(5, 14, 0), 140, us_open_minutes=10)
    assert sum(r["clock__us_open"] for r in narrow.values()) == 10
    other_zone = _clock_rows(_at(5, 14, 0), 140, us_open_tz="UTC", us_open_start_minute=14 * 60 + 40,
                             us_open_minutes=5)
    assert sum(r["clock__us_open"] for r in other_zone.values()) == 5


def test_minutes_to_the_next_funding_time():
    rows = _clock_rows(_at(5, 7, 30), 90)
    assert rows[_at(5, 7, 59)]["clock__funding_min"] == 1.0
    assert rows[_at(5, 8, 0)]["clock__funding_min"] == 0.0  # a position held at the instant pays
    assert rows[_at(5, 8, 1)]["clock__funding_min"] == 479.0
    late = _clock_rows(_at(5, 23, 0), 90)
    assert late[_at(5, 23, 30)]["clock__funding_min"] == 30.0  # 00:00 UTC the next day
    custom = _clock_rows(_at(5, 7, 30), 90, funding_hours_utc=[12])
    assert custom[_at(5, 7, 59)]["clock__funding_min"] == 241.0


def test_pair_ratio_and_its_change_are_the_same_for_both_assets():
    klines = world()
    out = by_key(run(klines))
    a, b = closes(klines, A), closes(klines, B)
    pair = lambda i: math.log(a[i] / b[i])  # noqa: E731  assets[0] over assets[1]
    for asset in (A, B):
        row = out[(asset, t_of(30))]
        assert row["xasset__pair_log"] == pytest.approx(pair(30))
        for s in (2, 5, 10):
            assert row[f"xasset__pair_chg_{s}"] == pytest.approx(pair(30) - pair(30 - s))
    swapped = by_key(run(klines, assets=[B, A]))
    assert swapped[(A, t_of(30))]["xasset__pair_log"] == pytest.approx(-pair(30))


def _local_day(info_ms):
    return (info_ms - 5 * 3_600_000) // 86_400_000  # New York in January is UTC-5 all month


def _holiday_rows(start_ms, n):
    klines = bars(A, n, 1, start=start_ms) + bars(B, n, 2, start=start_ms)
    return {r["t_ms"] + 1: r for r in run(klines)["records"] if r["asset"] == A}


def test_us_exchange_holiday_and_adjacent_weekday_flags_from_the_dskit_calendar():
    pytest.importorskip("exchange_calendars")
    day = lambda y, m, d: _at(d, 0, 0, month=m, year=y) // 86_400_000  # noqa: E731
    expected = {  # local day -> (is, prev, next): New Year's Day 2026 is a Thursday
        day(2025, 12, 31): (0.0, 0.0, 1.0), day(2026, 1, 1): (1.0, 0.0, 0.0), day(2026, 1, 2): (0.0, 1.0, 0.0)}
    rows = _holiday_rows(_at(31, 12, 0, month=12, year=2025), 3500)
    seen = set()
    for instant, row in rows.items():
        seen.add(_local_day(instant))
        assert (row["holiday__is"], row["holiday__prev"], row["holiday__next"]) == expected[_local_day(instant)]
    assert seen == set(expected)


def test_a_long_weekend_flags_the_friday_the_weekend_and_the_tuesday():
    pytest.importorskip("exchange_calendars")
    day = lambda d: _at(d, 0, 0) // 86_400_000  # noqa: E731
    expected = {  # MLK day 2026-01-19 is a Monday; adjacency is to the neighbouring WEEKDAY
        day(16): (0.0, 0.0, 1.0), day(17): (0.0, 0.0, 1.0), day(18): (0.0, 0.0, 1.0),
        day(19): (1.0, 0.0, 0.0), day(20): (0.0, 1.0, 0.0)}
    rows = _holiday_rows(_at(16, 12, 0), 5000)
    for instant, row in rows.items():
        local = _local_day(instant)
        if local in expected:
            assert (row["holiday__is"], row["holiday__prev"], row["holiday__next"]) == expected[local], local


def test_a_missing_calendar_library_is_refused_by_name_at_plan_and_run(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "exchange_calendars", None)
    assert any("exchange_calendars" in p for p in MinuteFeatures.validate_params({**SMALL, "families": ["holiday"]}))
    assert MinuteFeatures.validate_params({**SMALL, "families": ["ret", "clock"]}) == []
    with pytest.raises(ValueError, match="exchange_calendars"):
        run(world(), families=["ret", "holiday"])
    assert run(world(), families=["ret", "clock"])["records"]  # a document without the family never needs it


def test_an_unknown_exchange_is_refused_by_name():
    pytest.importorskip("exchange_calendars")
    with pytest.raises(ValueError, match="NOPE"):
        run(world(), families=["holiday"], holiday_exchange="NOPE")


# ---------------------------------------------------------------------------
# the mandatory leakage property and the no-fill refusals, at the full 10080-minute scale
# ---------------------------------------------------------------------------

LONG = 10_080 + 420  # long enough for the widest default scale, with rows to test behind it


def _replaced(klines, after_open_ms, seed):
    """Copy ``klines`` with EVERY field of each bar opening after ``after_open_ms`` replaced by other values."""
    fresh = {}
    for asset in (A, B):
        n = sum(1 for r in klines if r["asset"] == asset)
        fresh[asset] = {r["open_time_ms"]: r for r in bars(asset, n, seed)}
    out = []
    for r in klines:
        if r["open_time_ms"] > after_open_ms:
            other = fresh[r["asset"]][r["open_time_ms"]]
            r = {**other, "open": other["open"] * 1.7, "high": other["high"] * 1.7, "low": other["low"] * 1.7,
                 "close": other["close"] * 1.7, "volume": other["volume"] * 5, "trades": other["trades"] + 40,
                 "taker_buy_volume": other["taker_buy_volume"] * 5}
        out.append(r)
    return out


def _past_only(row):
    return {k: v for k, v in row.items() if not k.startswith("y_h")}


@pytest.mark.parametrize("over,n,cuts", [
    (SMALL, 60, (20, 35)),
    ({"assets": [A, B]}, LONG, (10_080 + 5, 10_080 + 150, 10_080 + 300)),
], ids=["small", "default-grid"])
def test_no_feature_of_any_family_moves_when_every_later_bar_changes(over, n, cuts):
    klines = bars(A, n, 1) + bars(B, n, 2)
    readings = bvol(A, n, 3) + bvol(B, n, 4)
    run_ = lambda k, r: MinuteFeatures("m", over).run(None, {"klines": k, "bvol": r})  # noqa: E731
    base = by_key(run_(klines, readings))
    assert {c.split("__", 1)[0] for r in base.values() for c in r if "__" in c and not c.startswith("meta")} \
        == set(FAMILIES)  # every family is in the output being compared
    for cut in cuts:
        later = START + cut * BAR  # bars opening after this, and readings after t, are all replaced
        moved = by_key(run_(_replaced(klines, later, seed=77),
                            [({**r, "index_value": r["index_value"] + 40} if r["calc_time_ms"] > t_of(cut) else r)
                             for r in readings]))
        tested = [key for key in base if key[1] <= t_of(cut)]
        assert tested, cut
        for key in tested:
            assert _past_only(moved[key]) == _past_only(base[key]), (key, cut)  # bit-identical, every column
        after = [key for key in base if key[1] > t_of(cut)]
        assert any(_past_only(moved[key]) != _past_only(base[key]) for key in after)  # the replacement has teeth


@pytest.mark.parametrize("family", [f for f in FAMILIES if f not in ("clock", "holiday")])
def test_control_the_last_legal_bar_does_move_each_data_family(family):
    klines = world()
    base = by_key(run(klines, bvol(A, 60, 3) + bvol(B, 60, 4)))
    i = 30
    changed = [({**r, "close": r["close"] * 1.01, "high": r["high"] * 1.01, "volume": r["volume"] * 3,
                 "trades": r["trades"] + 30} if r["open_time_ms"] == START + i * BAR else r) for r in klines]
    readings = [({**r, "index_value": r["index_value"] + 5} if r["calc_time_ms"] == START + i * BAR else r)
                for r in bvol(A, 60, 3) + bvol(B, 60, 4)]
    moved = by_key(run(changed, readings))
    row, other = base[(A, t_of(i))], moved[(A, t_of(i))]
    prefix = family + "__"
    assert any(row[c] != other[c] for c in row if c.startswith(prefix)), family


def test_a_row_with_less_than_the_widest_window_behind_it_is_refused_by_name_not_filled():
    out = MinuteFeatures("m", {"assets": [A, B]}).run(None, {"klines": bars(A, LONG, 1) + bars(B, LONG, 2)})
    prov = out["provenance"]
    assert prov["need_bars"] == 10_080
    for asset in (A, B):
        assert prov["assets"][asset]["refused"]["insufficient_history"] == 10_080
        assert prov["assets"][asset]["rows"] == LONG - 10_080
    assert min(r["t_ms"] for r in out["records"]) == t_of(10_080)


def test_a_gap_anywhere_in_the_10080_minute_window_is_refused_by_name():
    n = 10_080 + 30
    klines = bars(A, n, 1, drop={10}) + bars(B, n, 2)
    out = MinuteFeatures("m", {"assets": [A, B]}).run(None, {"klines": klines})
    prov = out["provenance"]["assets"][A]
    assert prov["refused"]["gap_in_window"] == 10  # bars past the 10080th, until a window clears the gap
    assert min(r["t_ms"] for r in out["records"] if r["asset"] == A) == t_of(10_091)
    assert all(r["trend__ma_10080"] is not None for r in out["records"])
