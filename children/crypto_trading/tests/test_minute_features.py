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
    "assets": [A, B], "ret_minutes": [1, 2, 5], "rms_windows": [5, 10], "ewma_half_lives": [3],
    "ewma_lookback_multiple": 3, "range_windows": [5], "flow_window": 6, "taker_windows": [3, 6],
    "scale_window": 10, "max_horizon": 4, "bvol_change_minutes": 5, "bvol_max_age_ms": 120_000,
}
SMALL_NEED = 10  # the widest window of SMALL: rms 10, scale 10, ewma 3 * 3 = 9
FAMILIES = ("ret", "vol", "flow", "clock", "xasset", "bvol")
LABEL_AND_KEYS = {"asset", "t_ms", "scale"}


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
    return [k for k in row if k not in LABEL_AND_KEYS and not k.startswith("y_h")]


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
        assert {"asset", "t_ms", "scale"} <= set(row)
    names = set(feature_columns(rows[0]))
    assert {f"ret__r{n}" for n in (1, 2, 5)} <= names
    assert {"vol__rms_5", "vol__rms_10", "vol__ewma_3", "vol__hl_5"} <= names
    assert {"flow__log_volume", "flow__log_trades", "flow__taker_3", "flow__taker_6"} <= names
    assert {"clock__tod_sin", "clock__tod_cos", "clock__dow_sin", "clock__dow_cos"} <= names
    assert {"xasset__r1", "xasset__rms_5", "xasset__ewma_3", "xasset__hl_5"} <= names
    assert {"bvol__level", "bvol__change"} <= names


def test_values_match_plain_python():
    klines = world()
    out = by_key(run(klines, bvol(A, 60, 3) + bvol(B, 60, 4)))
    i = 30
    row = out[(A, t_of(i))]
    c = closes(klines, A)
    rets = [math.log(c[j] / c[j - 1]) for j in range(1, len(c))]  # rets[j - 1] is the return INTO bar j
    window = lambda w: rets[i - w:i]  # noqa: E731  the w returns ending at bar i
    for n in (1, 2, 5):
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
    assert row["scale"] == pytest.approx(scale) == pytest.approx(row["vol__rms_10"])
    for h in range(1, 5):
        assert row[f"y_h{h}"] == pytest.approx(math.log(c[i + h] / c[i]) / scale)
    other = closes(klines, B)
    assert row["xasset__r1"] == pytest.approx(math.log(other[i] / other[i - 1]))


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


def test_vol_columns_use_dskits_estimators():
    from dskit.pipeline.libs import vol_estimators

    source = open(os.path.join(CHILD_ROOT, "crypto_trading", "minute_features.py"), encoding="utf-8").read()
    assert "dskit.pipeline.libs.vol_estimators" in source
    assert "def ewma_variance" not in source and "def parkinson_variance" not in source
    assert vol_estimators.build_estimators  # the import the module relies on exists


def test_importing_the_module_pulls_no_heavy_library():
    import subprocess
    import sys

    code = "import sys, crypto_trading.minute_features; sys.exit(1 if 'numpy' in sys.modules else 0)"
    done = subprocess.run([sys.executable, "-c", code], cwd=CHILD_ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_default_knobs_are_the_plan_table():
    klines = bars("BTC", 420, 1) + bars("ETH", 420, 2)
    node = MinuteFeatures("m", {"assets": ["BTC", "ETH"]})
    assert node.validate_params(node.params) == []
    out = node.run(None, {"klines": klines})
    row = out["records"][0]
    cols = feature_columns(row)
    count = lambda family: [c for c in cols if c.startswith(family + "__")]  # noqa: E731
    assert sorted(count("ret")) == sorted(f"ret__r{n}" for n in (1, 2, 3, 5, 10, 15, 30, 60))
    assert sorted(count("vol")) == sorted(
        [f"vol__rms_{w}" for w in (15, 30, 60, 240)] + [f"vol__ewma_{h}" for h in (15, 60)]
        + [f"vol__hl_{w}" for w in (15, 60)])
    assert sorted(count("flow")) == sorted(["flow__log_volume", "flow__log_trades", "flow__taker_15", "flow__taker_60"])
    assert len(count("clock")) == 4 and len(count("bvol")) == 2
    assert len(count("xasset")) == len(count("ret")) + len(count("vol"))
    assert [k for k in row if k.startswith("y_h")] == [f"y_h{h}" for h in range(1, 16)]
    # the widest default window is the 60-minute EWMA over 5 half-lives = 300 returns
    assert out["provenance"]["need_bars"] == 300
    assert row["t_ms"] == t_of(300)
    assert row["scale"] == pytest.approx(row["vol__rms_60"])


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
    assert base[(A, t_of(i))]["y_h1"] != moved[(A, t_of(i))]["y_h1"] or shaken_asset == B
    assert base[(shaken_asset, t_of(i))]["y_h1"] != moved[(shaken_asset, t_of(i))]["y_h1"]


def test_control_changing_the_last_legal_bar_moves_the_features():
    klines = world()
    i = 30
    last_legal = START + i * BAR
    moved = by_key(run(_shaken([r for r in klines if r["open_time_ms"] <= last_legal or r["asset"] == B], A, last_legal)))
    base = by_key(run(klines))
    row, other = base[(A, t_of(i))], moved[(A, t_of(i))]
    assert row["ret__r1"] != other["ret__r1"] and row["vol__rms_5"] != other["vol__rms_5"]
    assert base[(B, t_of(i))]["xasset__r1"] != moved[(B, t_of(i))]["xasset__r1"]


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
    assert all(r["scale"] > 0 for r in out["records"])


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
        assert all(row[c] == full[key][c] for c in row)  # labels and scale unchanged by the arm


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
    "assets": [A, B], "families": ["ret"], "ret_minutes": [1, 2], "rms_windows": [5], "ewma_half_lives": [3],
    "ewma_lookback_multiple": 3, "range_windows": [5], "flow_window": 6, "taker_windows": [3],
    "scale_window": 10, "max_horizon": 4, "bvol_max_age_ms": 1000, "bvol_change_minutes": 5,
}


def test_the_good_knobs_cover_every_param():
    assert set(GOOD_KNOBS) == set(MinuteFeatures._PARAMS)  # a pin that omits a knob claims coverage it lacks
    assert MinuteFeatures.validate_params(GOOD_KNOBS) == []


@pytest.mark.parametrize("name,bad", [
    ("assets", None), ("assets", [A]), ("assets", [A, A]), ("assets", [A, B, "CCC"]), ("assets", [A, ""]),
    ("assets", [A, 3]),
    ("families", []), ("families", ["ret", "nope"]), ("families", ["ret", "ret"]), ("families", "ret"),
    ("ret_minutes", []), ("ret_minutes", [0]), ("ret_minutes", [1, 1]), ("ret_minutes", [True]),
    ("ret_minutes", 5),
    ("rms_windows", [0]), ("ewma_half_lives", [-1]), ("ewma_lookback_multiple", 0),
    ("range_windows", [1.5]), ("flow_window", 0), ("taker_windows", []), ("scale_window", 0),
    ("max_horizon", 0), ("bvol_max_age_ms", 0), ("bvol_change_minutes", 0),
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
    with pytest.raises(ValueError, match="index_value"):
        run(good, [{"asset": A, "calc_time_ms": START, "index_value": None}])


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
    assert config["notes"] and set(config) == {"notes", "arms"}
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
