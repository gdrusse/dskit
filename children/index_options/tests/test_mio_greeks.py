"""MIOGreeksDiagnostics: Greeks, stock-equivalent split and exposure over a saved closed MIO run.

Everything runs on a synthetic closed-run directory. ``PinnedPayloadSource`` is replaced by a
fake over a temp dir (as in ``test_mio_book_outcomes``); the rules under test are the child's own:
the data window, the refusals, the read-only discipline, the A0006 rank and the files written.
"""

import ast
import copy
import hashlib
import json
import math
import os
import shutil
import stat
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.option_pricing import black76, black76_delta
from index_options import mio_greeks, mio_run_events
from index_options.mio_greeks import CellRank, GreeksRefusal, MIOGreeksDiagnostics

CHILD = Path(__file__).resolve().parents[1]
MULT, FEE = 100, 0.65
METHOD = "nominal_lp"
DECISION, FILL, EXPIRY = "2021-04-05", "2021-04-06", "2021-04-16"
LATER_DECISION, LATER_FILL = "2021-04-12", "2021-04-13"
MONTHLY_EXPIRY = "2021-05-21"
DAYS = (DECISION, FILL, LATER_DECISION, LATER_FILL)
BLK_FORWARD, BLK_SPOT, BLK_VOL = 453.0, 452.0, 0.18
MMM_FORWARD, MMM_SPOT, MMM_VOL = 100.5, 100.0, 0.25
LISTED = {"BLK": ["2021-04-09", "2021-04-16", "2021-04-23", "2021-04-30"],
          "MMM": [EXPIRY, MONTHLY_EXPIRY, "2021-06-18"]}


class _FakePinned:
    base = None

    def __init__(self, pin):
        self.pin = pin

    def file(self, relative, *, verify=False):
        if relative not in self.pin["files"]:
            raise ValueError("undeclared source file")
        path = self.base / relative
        if verify and hashlib.sha256(path.read_bytes()).hexdigest() != self.pin["files"][relative]:
            raise ValueError("source payload drift")
        return str(path)


def _sessions():
    day, out = date(2021, 3, 22), []
    while day <= date(2021, 5, 28):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _closes(start, step, overrides):
    out = {}
    for i, day in enumerate(_sessions()):
        out[day] = round(start * math.exp(step * ((i % 5) - 2) * 0.01 + 0.0005 * i), 4)
    out.update(overrides)
    return out


def _chain_rows(symbol, day, expiry, forward, vol, strikes, with_iv=True, rights=("call", "put")):
    years = (date.fromisoformat(expiry) - date.fromisoformat(day)).days / 365
    rows = []
    for k in strikes:
        for right in rights:
            mid = black76(right, forward, k, vol, years)
            rows.append({"symbol": symbol, "date": day, "expiration": expiry, "type": right,
                         "strike_raw": float(k), "bid": round(mid - 0.05, 4),
                         "ask": round(mid + 0.05, 4), "iv": vol if with_iv else None,
                         "underlying_price": forward, "basis_factor": 1.0})
    return rows


def _leg(right, sign, qty, strike, price, fill_price):
    return {"right": right, "sign": sign, "quantity": qty, "strike": strike, "price": price,
            "fill_price": fill_price}


def _credit(legs):
    return (math.fsum(-leg["sign"] * leg["fill_price"] * leg["quantity"] * MULT for leg in legs)
            - sum(leg["quantity"] for leg in legs) * FEE)


def _trade(tid, symbol, decision, fill, expiry, legs, settle, factor=1.0):
    legs = copy.deepcopy(legs)              # a test may edit its own copy, never the shared one
    payoff = 0.0
    for leg in legs:
        intrinsic = max(settle - leg["strike"], 0.0) if leg["right"] == "call" else max(
            leg["strike"] - settle, 0.0)
        payoff += leg["sign"] * leg["quantity"] * intrinsic
    credit = _credit(legs)
    return {"id": tid, "symbol": symbol, "decision_date": decision, "fill_date": fill,
            "close_date": expiry, "expiry": expiry, "dte": 10, "contracts": sum(
                leg["quantity"] for leg in legs), "premium": 0.0, "fees": 0.0, "credit": credit,
            "pnl": credit + MULT * payoff, "factor": factor, "legs": legs}


# docs/explanations/mio-glossary.md worked example: both calls BOUGHT at the ask, expired worthless.
BLK_LEGS = [_leg("call", 1, 5, 450.0, 1.60, 1.65), _leg("call", 1, 1, 455.0, 1.15, 1.10)]
BLK_SETTLE = 448.0
MMM_LEGS = [_leg("put", -1, 2, 100.0, 2.1, 2.0)]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _block(base, folder, frames):
    (base / folder).mkdir(exist_ok=True)
    files, digests = {}, {}
    for symbol, frame in frames.items():
        rel = f"{folder}/{symbol}.parquet"
        frame.to_parquet(base / rel, index=False)
        files[symbol] = rel
        digests[rel] = _sha(base / rel)
    return {"files": files, "pin": {"root": "/r", "source": "s", "snapshot": folder,
                                    "stream": "files", "manifest_sha256": "0" * 64,
                                    "files": digests}}


def build_run(root, *, mutate=None, drop_chain=(), calls_only=(), no_iv=(), extra_methods=None,
              dividends=None):
    """Write a closed run, bars and chains under ``root``; return ``(run_dir, base)``."""
    base = Path(root) / "source"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(root) / "run"
    run_dir.mkdir()
    bars = {"BLK": _closes(450.0, 1.0, {FILL: BLK_SPOT, EXPIRY: BLK_SETTLE}),
            "MMM": _closes(100.0, 1.0, {FILL: MMM_SPOT, MONTHLY_EXPIRY: 105.0})}
    bar_frames = {s: pd.DataFrame({"symbol": s, "date": list(v), "close": list(v.values())})
                  for s, v in bars.items()}
    for symbol, by_day in (dividends or {}).items():     # a dividend_amount column, 0.0 by default
        bar_frames[symbol]["dividend_amount"] = [by_day.get(d, 0.0) for d in bars[symbol]]
    chains = {}
    for symbol, forward, vol, strikes in (("BLK", BLK_FORWARD, BLK_VOL, range(440, 470, 5)),
                                          ("MMM", MMM_FORWARD, MMM_VOL, range(90, 112, 2))):
        rows = []
        for day in DAYS:
            for expiry in LISTED[symbol]:
                if (symbol, day) in drop_chain or expiry <= day:
                    continue
                rows += _chain_rows(symbol, day, expiry, forward, vol, strikes,
                                    with_iv=symbol not in no_iv,
                                    rights=("call",) if symbol in calls_only else ("call", "put"))
        chains[symbol] = pd.DataFrame(rows)
    blk = _trade("2021-04-05:BLK:2021-04-16", "BLK", DECISION, FILL, EXPIRY, BLK_LEGS, BLK_SETTLE)
    mmm = _trade("2021-04-05:MMM:2021-05-21", "MMM", DECISION, FILL, MONTHLY_EXPIRY, MMM_LEGS, 105.0)
    later = _trade("2021-04-12:MMM:2021-05-21", "MMM", LATER_DECISION, LATER_FILL, MONTHLY_EXPIRY,
                   MMM_LEGS, 105.0)
    methods = [{"id": METHOD}, *(extra_methods or [])]
    table = {
        "complete": True,
        "plan": {"source_pins": {"bars": _block(base, "bars", bar_frames),
                                 "chain": _block(base, "chain", chains)},
                 "study": {"domain": {"params": {
                     "symbols": ["BLK", "MMM"], "methods": methods, "correlation_min_pairs": 2,
                     "bridge": {"solver": {"multiplier": MULT, "fee_per_contract_usd": FEE}}}}}},
        "results": [{"id": m["id"], "result": {"trades": [blk, mmm, later], "rejected": [],
                                                "curve": []}} for m in methods],
    }
    if mutate:
        mutate(table)
    (run_dir / "result.json").write_text(json.dumps(table, sort_keys=True))
    rows = [(r["id"], t) for r in table["results"] for t in r["result"]["trades"]]
    (run_dir / "trades.csv").write_text("method,id,contracts,pnl\n" + "".join(
        f"{m},{t['id']},{t['contracts']},{t['pnl']!r}\n" for m, t in rows))
    (run_dir / "daily-account.csv").write_text(
        "method,date,equity\n" + "".join(f"{r['id']},{FILL},10000.0\n" for r in table["results"]))
    (run_dir / "run.log").write_text("weekly_decision SECRET\n")
    return run_dir, base


PARAMS = {
    "methods": [METHOD], "window": {"start": "2021-04-01", "end": "2021-06-30"},
    "sealed_from": "2021-07-01", "rate": 0.0, "iv_source": "vendor", "multiplier": MULT,
    "min_years": 0.001, "vol_floor": 0.01, "parity_strikes": 3, "min_pairs": 2,
    "max_pair_spread_frac": 0.5, "cell_by": ["symbol", "cadence", "rank"], "min_total_usd": 1.0,
    "dte_edges": [0, 7, 14, 28, 60], "top_k": 1, "exposure_days": "both",
    "beta_lookback": 3, "beta_min_pairs": 2, "chain_cache_days": 8,
    "hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.005,
    "hedge_cost_source": "placeholder", "min_books": 5, "htb_lookback": 20,
    "htb_vol_threshold": 2.5, "htb_jump_threshold": 0.75,
    "quarter": "2021Q2", "dividends": "optional", "seal_crossing": "refuse",
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    count = []

    def make(**kwargs):
        root = tmp_path / f"world{len(count)}"
        count.append(root)
        root.mkdir()
        run_dir, base = build_run(root, **kwargs)
        _FakePinned.base = base
        monkeypatch.setattr(mio_greeks, "PinnedPayloadSource", _FakePinned)
        monkeypatch.setattr(mio_run_events, "PinnedPayloadSource", _FakePinned)
        return run_dir, root / "out"
    return make


def run_node(run_dir, out_dir, **over):
    params = {**PARAMS, "run_dir": str(run_dir), "out_dir": str(out_dir), **over}
    return MIOGreeksDiagnostics("greeks", params).run(None, {})


def code_of(excinfo):
    return excinfo.value.code


def rows_by(out, key="id"):
    return {r[key]: r for r in out["books"]}


# ---- the glossary book, end to end ---------------------------------------------------------
def test_blk_book_end_to_end_with_hand_computed_stock_pnl_and_residual(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    row = rows_by(out)[f"{METHOD}|2021-04-05:BLK:2021-04-16"]
    years = 10 / 365
    # delta in shares: +5 calls at 450, +1 call at 455, x100, spot terms (F/S chain rule)
    d450 = black76_delta("call", BLK_FORWARD, 450.0, BLK_VOL, years)
    d455 = black76_delta("call", BLK_FORWARD, 455.0, BLK_VOL, years)
    delta_sh = 100 * (5 * d450 + d455) * (BLK_FORWARD / BLK_SPOT)
    premium = -(5 * 1.65 * 100 + 1 * 1.10 * 100)                    # glossary: -935.00
    credit = premium - 6 * FEE                                       # fees 3.90 -> -938.90
    pnl = credit                                                     # both calls expire worthless
    assert (premium, round(credit, 2)) == (-935.0, -938.90)
    assert row["pnl"] == pytest.approx(-938.90)
    assert row["status"] == "ok" and row["forward_source"] == "parity"
    assert row["forward"] == pytest.approx(BLK_FORWARD, abs=1e-6)
    assert row["delta_sh"] == pytest.approx(delta_sh, rel=1e-9)
    assert row["static_delta_pnl"] == pytest.approx(delta_sh * (448.0 - 452.0), rel=1e-9)
    assert row["static_delta_pnl"] + row["beyond_static_delta"] == pytest.approx(pnl, abs=1e-9)
    assert (row["symbol"], row["rank"], row["cadence"], row["method"]) == (
        "BLK", 2, "weekly", METHOD)


# ---- the static delta-hedge column (ADR-0310 Amendment 1, A2) -----------------------------------
BLK_ID = f"{METHOD}|2021-04-05:BLK:2021-04-16"


def _blk_delta():
    years = 10 / 365
    d450 = black76_delta("call", BLK_FORWARD, 450.0, BLK_VOL, years)
    d455 = black76_delta("call", BLK_FORWARD, 455.0, BLK_VOL, years)
    return 100 * (5 * d450 + d455) * (BLK_FORWARD / BLK_SPOT)


def _hedged(delta, pnl, s_fill, s_settle, days, dividend):
    """The amendment's formula, restated independently of the core."""
    costs = abs(delta) * (2 * 0.005 + (s_fill + s_settle) * 5.0 / 1e4)
    borrow = max(delta, 0.0) * s_fill * 0.005 * days / 365
    return pnl - delta * (s_settle - s_fill) - costs - borrow - delta * dividend


def test_blk_hedged_pnl_equals_the_formula_restated_from_black76_deltas(world):
    run_dir, out_dir = world(dividends={"BLK": {}, "MMM": {}})
    row = rows_by(run_node(run_dir, out_dir))[BLK_ID]
    delta = _blk_delta()
    assert row["hedge_status"] == "ok" and row["dividend_adjust"] == 0.0
    assert row["hedge_sh"] == pytest.approx(-delta, rel=1e-9) and row["hedge_days"] == 10
    assert row["hedged_pnl"] == pytest.approx(_hedged(delta, -938.90, 452.0, 448.0, 10, 0.0),
                                              rel=1e-9)


def test_dividend_in_the_window_is_delta_times_the_cash_and_the_fill_day_is_not_counted(world):
    run_dir, out_dir = world(dividends={"BLK": {FILL: 5.0, "2021-04-09": 1.0, EXPIRY: 0.5},
                                        "MMM": {}})
    row = rows_by(run_node(run_dir, out_dir))[BLK_ID]
    delta = _blk_delta()
    assert row["dividend_adjust"] == pytest.approx(delta * 1.5, rel=1e-9)   # 04-09 and the expiry day
    assert row["hedged_pnl"] == pytest.approx(_hedged(delta, -938.90, 452.0, 448.0, 10, 1.5),
                                              rel=1e-9)


def test_bars_without_dividend_amount_flag_every_row_and_the_summary(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    assert {r["hedge_status"] for r in out["books"]} == {"dividends_absent"}
    assert all(r["dividend_adjust"] is None and r["hedged_pnl"] is None for r in out["books"])
    summary = out["summary"]
    assert summary["hedged_pnl_usd"] is None and summary["n_stock_hedged"] == 0
    assert summary["methods"][METHOD]["n_stock_hedged"] == 0
    assert all(c["hedged_pnl_usd"] is None and c["n_stock_hedged"] == 0
               and c["n_dividends_absent"] == c["n"] for c in out["cells"])
    assert summary["hedge_dividend_source"] == "absent"
    assert summary["hedge_status"] == {"dividends_absent": 3}
    assert summary["hedge_params"] == {"hedge_fee_per_share": 0.005,
                                       "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.005}
    assert summary["methods"][METHOD]["hedge_dividend_source"] == "absent"
    disk = json.loads((out_dir / "greeks-summary.json").read_text())
    assert disk["hedge_status"] == {"dividends_absent": 3}
    header = (out_dir / "books.csv").read_text().splitlines()[0].split(",")
    assert {"hedge_sh", "hedge_days", "stock_costs", "short_borrow", "dividend_adjust",
            "hedged_pnl", "hedge_status"} <= set(header)


def test_bars_with_dividend_amount_report_the_source_and_status_ok(world):
    run_dir, out_dir = world(dividends={"BLK": {}, "MMM": {}})
    out = run_node(run_dir, out_dir)
    assert {r["hedge_status"] for r in out["books"]} == {"ok"}
    assert out["summary"]["hedge_dividend_source"] == "bars"
    assert out["summary"]["hedge_status"] == {"ok": 3}


def test_a_null_dividend_amount_in_the_window_is_dividend_missing_and_nulls_the_cell(world):
    run_dir, out_dir = world(dividends={"BLK": {"2021-04-08": None}, "MMM": {}})
    out = run_node(run_dir, out_dir)
    row = rows_by(out)[BLK_ID]
    assert row["hedge_status"] == "dividend_missing"
    assert row["hedged_pnl"] is None and row["dividend_adjust"] is None
    blk = next(c for c in out["cells"] if c["symbol"] == "BLK")
    assert blk["hedged_pnl_usd"] is None and blk["n_stock_hedged"] == 0
    mmm = next(c for c in out["cells"] if c["symbol"] == "MMM")
    assert mmm["n_stock_hedged"] == mmm["n"] == 2
    assert mmm["hedged_pnl_usd"] == pytest.approx(
        sum(r["hedged_pnl"] for r in out["books"] if r["symbol"] == "MMM"))
    assert out["summary"]["hedge_status"] == {"dividend_missing": 1, "ok": 2}
    mmm_sum = sum(r["hedged_pnl"] for r in out["books"] if r["symbol"] == "MMM")
    assert out["summary"]["hedged_pnl_usd"] == pytest.approx(mmm_sum)   # sums the ok rows
    assert out["summary"]["n_stock_hedged"] == 2 and blk["n_dividend_missing"] == 1


def test_a_dividend_outside_the_window_is_ignored_and_nothing_past_the_seal_is_read(world):
    run_dir, out_dir = world(dividends={"BLK": {"2021-04-05": 9.0, "2021-04-20": 9.0}, "MMM": {}})
    row = rows_by(run_node(run_dir, out_dir))[BLK_ID]
    assert row["dividend_adjust"] == 0.0


def test_the_hedge_params_are_required_and_validated_by_the_child():
    for key in ("hedge_fee_per_share", "hedge_half_spread_bps", "hedge_borrow_rate"):
        short = {k: v for k, v in PARAMS.items() if k != key}
        problems = MIOGreeksDiagnostics.validate_params(
            {**short, "run_dir": "/a", "out_dir": "/b"})
        assert any(key in p for p in problems)
    bad = {**PARAMS, "run_dir": "/a", "out_dir": "/b", "hedge_borrow_rate": 1.0}
    assert any("hedge_borrow_rate" in p for p in MIOGreeksDiagnostics.validate_params(bad))


def test_the_four_files_are_written_atomically_with_inputs_and_counts(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "books.csv", "cells.csv", "exposure.csv", "greeks-summary.json"]
    summary = json.loads((out_dir / "greeks-summary.json").read_text())
    assert summary == json.loads(json.dumps(out["summary"], sort_keys=True))
    inputs = summary["inputs"]
    for name in ("result.json", "trades.csv", "daily-account.csv"):
        assert inputs[name] == _sha(run_dir / name)
    assert "run.log" not in inputs
    assert len(summary["params_hash"]) == 64 and summary["methods"][METHOD]["books"] == 3
    assert summary["refusals"] == {} and summary["rate"] == 0.0
    header = (out_dir / "books.csv").read_text().splitlines()[0].split(",")
    assert {"method", "id", "symbol", "rank", "static_delta_pnl", "beyond_static_delta", "status"} <= set(header)
    assert not list(out_dir.glob("*.tmp*"))


def test_cells_and_exposure_cover_the_selected_methods_separately(world):
    run_dir, out_dir = world(extra_methods=[{"id": "w1_003_shock10_lp",
                                              "shock": {"beta_lookback": 3}}])
    out = run_node(run_dir, out_dir, methods=[METHOD, "w1_003_shock10_lp"])
    assert {r["method"] for r in out["cells"]} == {METHOD, "w1_003_shock10_lp"}
    assert {r["method"] for r in out["exposure"]} == {METHOD, "w1_003_shock10_lp"}
    mine = [r for r in out["cells"] if r["method"] == METHOD]
    assert sum(r["pnl_usd"] for r in mine) == pytest.approx(
        sum(r["pnl"] for r in out["books"] if r["method"] == METHOD))


# ---- the A0006 rank (ADR-0310 A1: listed position, owner option (a)) ------------------------
D0 = "2021-04-05"


def _rank(decision, expiry, listed):
    return CellRank().of(decision, expiry, listed)


WEEKLY_LIST = ["2021-04-09", "2021-04-16", "2021-04-23", "2021-04-30", "2021-05-07",
               "2021-05-14", "2021-05-21", "2021-05-28", "2021-06-04"]       # nine Fridays


def test_a_skipped_listed_week_leaves_no_gap():
    listed = ["2021-04-09", "2021-04-16", "2021-04-23", "2021-05-07", "2021-05-21"]
    assert _rank(D0, "2021-05-07", listed) == ("weekly", 4, "ok")           # DTE window would say 5


def test_a_thursday_holiday_expiry_is_that_weeks_friday():
    listed = ["2021-04-01", "2021-04-09", "2021-04-16"]                      # Good Friday 04-02 unlisted
    assert _rank("2021-03-29", "2021-04-01", listed) == ("weekly", 1, "ok")
    assert _rank("2021-03-29", "2021-04-09", listed) == ("weekly", 2, "ok")


def test_a_thursday_beside_a_listed_friday_is_not_a_slot():
    listed = ["2021-04-01", "2021-04-02", "2021-04-09", "2021-04-16"]
    assert _rank("2021-03-29", "2021-04-01", listed) == ("weekly", None, "non_slot_expiry")
    assert _rank("2021-03-29", "2021-04-02", listed)[1:] == (1, "ok")


def test_a_holiday_thursday_monthly_is_standard():
    # 2022-04-14 is the holiday Thursday of the third week (Good Friday 04-15); with 05-20 it is
    # a monthly-only name: no non-standard slot at all.
    listed = ["2022-04-14", "2022-05-20", "2022-06-17"]
    assert _rank("2022-04-04", "2022-04-14", listed) == ("monthly", 1, "ok")


def test_a_monthly_name_with_a_quarter_end_stray_stays_monthly():
    listed = ["2021-03-19", "2021-03-31", "2021-04-16", "2021-05-21"]      # 03-31 is a Wednesday
    assert _rank("2021-03-17", "2021-04-16", listed) == ("monthly", 2, "ok")
    assert _rank("2021-03-17", "2021-03-31", listed) == ("monthly", None, "non_slot_expiry")
    with_friday = [*listed, "2021-03-26"]                                    # one non-standard slot
    assert _rank("2021-03-17", "2021-04-16", with_friday) == ("monthly", 3, "ok")


def test_two_non_standard_slots_in_56_days_make_a_weekly_name():
    listed = ["2021-04-09", "2021-04-16", "2021-04-23"]                      # 04-09 and 04-23 non-standard
    assert _rank(D0, "2021-04-16", listed)[0] == "weekly"
    far = ["2021-04-16", "2021-05-21", "2021-06-04", "2021-06-11"]           # both stray ones past 56 days
    assert _rank(D0, "2021-04-16", far)[0] == "monthly"


@pytest.mark.parametrize("position,expiry", [(8, "2021-05-28"), (9, "2021-06-04")])
def test_weekly_rank_cap(position, expiry):
    rank, reason = (position, "ok") if position == 8 else (None, "rank_out_of_range")
    assert _rank(D0, expiry, WEEKLY_LIST) == ("weekly", rank, reason)


def test_monthly_position_cap():
    listed = ["2021-04-16", "2021-05-21", "2021-06-18", "2021-07-16"]
    assert _rank(D0, "2021-06-18", listed) == ("monthly", 3, "ok")
    assert _rank(D0, "2021-07-16", listed) == ("monthly", None, "rank_out_of_range")


def test_an_expiry_not_listed_that_day_has_no_rank():
    assert _rank(D0, "2021-04-23", ["2021-04-09", "2021-04-16"])[1:] == (None, "expiry_not_listed")


def test_classification_reads_only_the_decision_day_listing():
    listed = ["2021-04-16", "2021-05-21"]
    assert _rank(D0, "2021-04-16", listed) == ("monthly", 1, "ok")
    # a later day's wider listing never reaches the call: only `listed` is read
    assert _rank("2021-04-06", "2021-04-16", [*listed, "2021-04-09", "2021-04-23"])[0] == "weekly"
    assert _rank(D0, "2021-04-16", listed) == ("monthly", 1, "ok")


# (decision, the boundary slot, one certain non-standard Friday, the cadence the boundary implies)
STANDARD_EDGES = [
    ("2021-04-26", "2021-05-14", "2021-04-30", "weekly"),     # Friday on day 14: not standard
    ("2021-10-04", "2021-10-15", "2021-10-08", "monthly"),    # Friday on day 15: standard
    ("2021-05-03", "2021-05-21", "2021-05-07", "monthly"),    # Friday on day 21: standard
    ("2021-10-04", "2021-10-22", "2021-10-08", "weekly"),     # Friday on day 22: not standard
    ("2021-05-03", "2021-05-13", "2021-05-07", "weekly"),     # Thursday on day 13: not standard
    ("2021-10-04", "2021-10-14", "2021-10-08", "monthly"),    # Thursday on day 14: holiday monthly
    ("2021-05-03", "2021-05-20", "2021-05-07", "monthly"),    # Thursday on day 20: holiday monthly
    ("2021-10-04", "2021-10-21", "2021-10-08", "weekly"),     # Thursday on day 21: not standard
]


@pytest.mark.parametrize("decision,boundary,stray,cadence", STANDARD_EDGES)
def test_the_standard_monthly_day_ranges_are_exact_at_both_edges(decision, boundary, stray, cadence):
    assert _rank(decision, boundary, [stray, boundary])[0] == cadence


@pytest.mark.parametrize("listed,cadence", [
    (["2021-04-09", "2021-06-03"], "weekly"),      # a Thursday slot exactly 56 days out counts
    (["2021-04-09", "2021-06-04"], "monthly"),     # 57 days out does not
])
def test_the_classification_horizon_is_inclusive_at_56_days(listed, cadence):
    assert _rank("2021-04-08", listed[-1], listed)[0] == cadence


def test_an_expiry_on_the_decision_day_is_not_a_slot_and_does_not_shift_the_rank():
    listed = ["2021-04-09", "2021-04-16", "2021-05-21"]               # decision day is a Friday
    assert _rank("2021-04-09", "2021-04-09", listed) == ("monthly", None, "non_slot_expiry")
    assert _rank("2021-04-09", "2021-04-16", listed) == ("monthly", 1, "ok")


@pytest.mark.parametrize("decision,expiry,listed", [
    ("2021-04-05", "not-a-date", ["2021-04-16"]),
    ("2021-04-05", "2021-04-16", ["2021-04-16", "junk"]),
    ("2021-04-05", "20210416", ["2021-04-16"]),
    ("04/05/2021", "2021-04-16", ["2021-04-16"]),
])
def test_a_malformed_date_is_a_bad_date_refusal_not_a_bare_value_error(decision, expiry, listed):
    with pytest.raises(GreeksRefusal) as exc:
        CellRank().of(decision, expiry, listed)
    assert exc.value.code == "bad_date"


def test_with_no_null_rank_book_every_reason_is_ok_and_no_pnl_is_set_aside(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    assert all(c["rank"] is not None for c in out["cells"])
    assert all(r["rank_reason"] == "ok" for r in out["books"])
    assert out["summary"]["rank_reasons"] == {"ok": len(out["books"])}
    assert out["summary"]["rank_null_pnl_usd"] == {}


def test_a_null_rank_book_leaves_the_cells_but_stays_in_books_csv_reasons_and_the_reconciliation(
        world, monkeypatch):
    monkeypatch.setattr(mio_greeks, "MONTHLY_MAX_POSITION", 1)   # MMM (monthly-only) lists 05-21 second
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    mmm = [r for r in out["books"] if r["symbol"] == "MMM"]
    assert {(r["rank"], r["rank_reason"]) for r in mmm} == {(None, "rank_out_of_range")}
    assert not [c for c in out["cells"] if c["symbol"] == "MMM"]
    assert [c["symbol"] for c in out["cells"]] == ["BLK"]
    csv_rows = (out_dir / "books.csv").read_text().splitlines()
    assert sum(",rank_out_of_range" in line for line in csv_rows) == 2
    summary = out["summary"]
    assert summary["rank_reasons"] == {"ok": 1, "rank_out_of_range": 2}
    set_aside = sum(r["pnl"] for r in mmm)
    assert summary["rank_null_pnl_usd"] == {"rank_out_of_range": pytest.approx(set_aside)}
    method = summary["methods"][METHOD]
    assert method["rank_null_pnl_usd"] == summary["rank_null_pnl_usd"]
    assert sum(c["pnl_usd"] for c in out["cells"]) + sum(method["rank_null_pnl_usd"].values()) == (
        pytest.approx(method["attributed_pnl_usd"]))
    on_disk = json.loads((out_dir / "greeks-summary.json").read_text())
    assert on_disk["rank_null_pnl_usd"] == {"rank_out_of_range": pytest.approx(set_aside)}


def test_monthly_rank_end_to_end(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    mmm = [r for r in out["books"] if r["symbol"] == "MMM"]
    assert {(r["rank"], r["cadence"]) for r in mmm} == {(2, "monthly")}


# ---- config ---------------------------------------------------------------------------------
@pytest.mark.parametrize("change,fragment", [
    ({"sealed_from": None}, "sealed_from"),
    ({"sealed_from": "2024-06-03"}, "sealed_from"),
    ({"sealed_from": "2021-07-02"}, "development_seal_exceeded"),
    ({"sealed_from": "2022-01-03"}, "development_seal_exceeded"),
    ({"chain_cache_days": 0}, "chain_cache_days"),
    ({"window": {"start": "2021-04-01", "end": "2021-07-01"}}, "window"),
    ({"window": {"start": "2021-05-01", "end": "2021-04-01"}}, "window"),
    ({"methods": []}, "methods"),
    ({"methods": ["a", "a"]}, "methods"),
    ({"surprise": 1}, "unknown param"),
    ({"run_dir": ""}, "run_dir"),
    ({"quarter": "2021Q5"}, "quarter"),
    ({"quarter": "2021-Q2"}, "quarter"),
    ({"quarter": 20212}, "quarter"),
    ({"dividends": "maybe"}, "dividends"),
    ({"seal_crossing": "drop"}, "seal_crossing"),
    ({"cell_by": ["symbol", "rank"]}, "cell_by_incomplete"),
    ({"cell_by": ["symbol", "cadence"]}, "cell_by_incomplete"),
    ({"out_dir": "/x/run/inside"}, "out_dir_inside_run_dir"),
])
def test_config_problems(change, fragment):
    params = {**PARAMS, "run_dir": "/x/run", "out_dir": "/x/out", **change}
    if change.get("sealed_from", 0) is None:
        del params["sealed_from"]
    assert any(fragment in p for p in MIOGreeksDiagnostics.validate_params(params))
    with pytest.raises(ConfigError):
        MIOGreeksDiagnostics("greeks", params)


def test_protected_start_matches_the_stress_replays_path_a0003_bound():
    pytest.importorskip("numpy")
    from index_options import mio_execution_stress
    assert mio_greeks.PROTECTED_FROM == mio_execution_stress._PROTECTED_FROM


def test_pnl_gate_is_half_a_cent_as_the_adr_states():
    assert mio_greeks.PNL_GATE_USD == 0.005


# ---- refusals ---------------------------------------------------------------------------------
def _set(table_fn):
    return table_fn


def test_a_2024_date_refuses(world):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0]["expiry"] = "2024-03-15"
            r["result"]["trades"][0]["close_date"] = "2024-03-15"
    run_dir, out_dir = world(mutate=move)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "window_outside_data_rights"
    assert not out_dir.exists()


@pytest.mark.parametrize("field,value", [
    ("expiry", "2021-07-02"), ("fill_date", "2021-07-01"), ("decision_date", "2021-03-30"),
    ("fill_date", "2021-03-31")])
def test_a_sealed_from_breach_or_a_date_outside_the_window_refuses(world, field, value):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0][field] = value
    run_dir, out_dir = world(mutate=move)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "window_outside_data_rights"


def test_the_chain_reader_itself_refuses_a_day_at_or_past_the_seal(world):
    run_dir, out_dir = world()
    quotes = mio_greeks.ChainQuotes(json.loads((run_dir / "result.json").read_text())[
        "plan"]["source_pins"]["chain"], "2021-07-01")
    with pytest.raises(GreeksRefusal) as exc:
        quotes.chain("BLK", "2021-07-01", EXPIRY)
    assert code_of(exc) == "window_outside_data_rights"


def test_out_dir_inside_run_dir_refuses_at_construction_even_through_a_symlink(world, tmp_path):
    run_dir, _ = world()
    link = tmp_path / "link"
    link.symlink_to(run_dir)
    for target in (run_dir / "sub", link / "sub"):
        with pytest.raises(ConfigError, match="out_dir_inside_run_dir"):
            MIOGreeksDiagnostics("g", {**PARAMS, "run_dir": str(run_dir), "out_dir": str(target)})


def test_out_dir_inside_run_dir_refuses_again_at_run_time(world):
    run_dir, out_dir = world()
    node = MIOGreeksDiagnostics("g", {**PARAMS, "run_dir": str(run_dir), "out_dir": str(out_dir)})
    node.params["out_dir"] = str(run_dir / "later")      # placement changed after validation
    with pytest.raises(GreeksRefusal) as exc:
        node.run(None, {})
    assert code_of(exc) == "out_dir_inside_run_dir"
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "daily-account.csv", "result.json", "run.log", "trades.csv"]


@pytest.mark.parametrize("how", ["no_result", "incomplete", "no_trades_csv", "no_daily_csv",
                                 "csv_ids_differ"])
def test_input_not_closed(world, how):
    run_dir, out_dir = world(mutate=(lambda t: t.update(complete=False)) if how == "incomplete"
                             else None)
    if how == "no_result":
        (run_dir / "result.json").unlink()
    elif how == "no_trades_csv":
        (run_dir / "trades.csv").unlink()
    elif how == "no_daily_csv":
        (run_dir / "daily-account.csv").unlink()
    elif how == "csv_ids_differ":
        (run_dir / "trades.csv").write_text("method,id,contracts,pnl\n")
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "input_not_closed"


def test_a_method_the_result_does_not_hold_is_not_closed_input(world):
    run_dir, out_dir = world()
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, methods=["w1_010_shock10_lp"])
    assert code_of(exc) == "input_not_closed"


@pytest.mark.parametrize("bad", [math.nan, math.inf, None, "12.5"])
def test_a_non_finite_or_non_numeric_saved_pnl_refuses_the_recompute_gate(world, bad):
    def corrupt(table):
        for r in table["results"]:
            r["result"]["trades"][0]["pnl"] = bad
    run_dir, out_dir = world(mutate=corrupt)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "pnl_recompute_mismatch"
    assert not out_dir.exists()


def test_pnl_recompute_gate_is_half_a_cent(world):
    def drift(delta):
        def fn(table):
            for r in table["results"]:
                r["result"]["trades"][0]["pnl"] += delta
        return fn
    run_dir, out_dir = world(mutate=drift(0.004))
    run_node(run_dir, out_dir)                       # inside the gate
    run_dir2, out2 = world(mutate=drift(0.0051))
    with pytest.raises(GreeksRefusal) as exc:
        run_node(Path(run_dir2), out2)
    assert code_of(exc) == "pnl_recompute_mismatch"


def test_beta_lookback_must_equal_a_methods_shock_lookback(world):
    run_dir, out_dir = world(extra_methods=[{"id": "w1_003_shock10_lp",
                                              "shock": {"beta_lookback": 5}}])
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, methods=["w1_003_shock10_lp"])
    assert code_of(exc) == "beta_lookback_mismatch"


def test_nominal_has_no_shock_so_the_configs_lookback_is_used_and_flagged(world):
    run_dir, out_dir = world(extra_methods=[{"id": "w1_003_shock10_lp",
                                              "shock": {"beta_lookback": 3}}])
    out = run_node(run_dir, out_dir, methods=[METHOD, "w1_003_shock10_lp"])
    flags = out["summary"]["beta_lookback_flags"]
    assert flags == {METHOD: "config_value_no_shock", "w1_003_shock10_lp": "matches_shock"}


@pytest.mark.parametrize("how", ["absent", "empty"])
def test_symbols_missing(world, how):
    def strip(table):
        params = table["plan"]["study"]["domain"]["params"]
        if how == "absent":
            del params["symbols"]
        else:
            params["symbols"] = []
    run_dir, out_dir = world(mutate=strip)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "symbols_missing"


def test_beta_min_pairs_must_equal_the_runs_correlation_min_pairs(world):
    run_dir, out_dir = world()
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, beta_min_pairs=3)
    assert code_of(exc) == "beta_min_pairs_mismatch"


def test_a_sealed_date_refuses_before_any_pnl_field_is_used(world):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0]["fill_date"] = "2021-07-01"
            r["result"]["trades"][0]["pnl"] += 1000.0     # would fail the P&L recompute gate
    run_dir, out_dir = world(mutate=move)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "window_outside_data_rights"


@pytest.mark.parametrize("field,value", [("fill_date", "2021-4-6"), ("expiry", "tomorrow"),
                                         ("decision_date", None)])
def test_bad_dates_refuse_before_comparison(world, field, value):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0][field] = value
    run_dir, out_dir = world(mutate=move)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "bad_date"


@pytest.mark.parametrize("header", ["id,contracts,pnl", "method,contracts,pnl"])
def test_trades_csv_without_method_or_id_is_a_schema_refusal(world, header):
    run_dir, out_dir = world()
    (run_dir / "trades.csv").write_text(header + "\n")
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir)
    assert code_of(exc) == "trades_schema"


def test_multiplier_must_equal_the_runs_own(world):
    run_dir, out_dir = world()
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, multiplier=10)
    assert code_of(exc) == "multiplier_mismatch"


# ---- per-book and per-day codes ------------------------------------------------------------
def test_leg_no_iv(world):
    run_dir, out_dir = world(no_iv=("BLK",))
    out = run_node(run_dir, out_dir)
    assert out["summary"]["refusals"] == {"leg_no_iv": 1}
    assert rows_by(out)[f"{METHOD}|2021-04-05:BLK:2021-04-16"]["static_delta_pnl"] is None


def test_no_fill_quote(world):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0]["legs"][0]["strike"] = 451.0
            trade = r["result"]["trades"][0]
            trade["credit"] = _credit(trade["legs"])
            trade["pnl"] = trade["credit"]                  # 451 and 455 calls both expire at 448
    run_dir, out_dir = world(mutate=move)
    assert run_node(run_dir, out_dir)["summary"]["refusals"] == {"no_fill_quote": 1}


def test_basis_changed_through_active_factor(world):
    def split(table):
        for r in table["results"]:
            r["result"]["trades"][1]["active_factor"] = 2.0
    run_dir, out_dir = world(mutate=split)
    out = run_node(run_dir, out_dir)
    assert out["summary"]["refusals"] == {"basis_changed": 1}


def test_no_settlement_bar_keeps_the_book_unattributed_not_refused_whole(world):
    def hole(table):
        pass
    run_dir, out_dir = world(mutate=hole)
    # drop BLK's expiry bar from the pinned bars after building (re-pin its digest)
    base = _FakePinned.base
    path = base / "bars/BLK.parquet"
    frame = pd.read_parquet(path)
    frame[frame["date"] != EXPIRY].to_parquet(path, index=False)
    table = json.loads((run_dir / "result.json").read_text())
    table["plan"]["source_pins"]["bars"]["pin"]["files"]["bars/BLK.parquet"] = _sha(path)
    (run_dir / "result.json").write_text(json.dumps(table, sort_keys=True))
    out = run_node(run_dir, out_dir)
    assert out["summary"]["refusals"] == {"no_settlement_bar": 1}
    assert out["summary"]["methods"][METHOD]["unattributed_pnl_usd"] == pytest.approx(-938.90)


def test_leg_below_min_years(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir, min_years=0.03)         # BLK has 10 days = 0.027 years
    assert out["summary"]["refusals"] == {"leg_below_min_years": 1}


def test_forward_spot_is_a_flag_and_the_book_is_still_attributed(world):
    run_dir, out_dir = world(calls_only=("BLK",))
    out = run_node(run_dir, out_dir)
    row = rows_by(out)[f"{METHOD}|2021-04-05:BLK:2021-04-16"]
    assert row["status"] == "forward_spot" and row["static_delta_pnl"] is not None
    assert out["summary"]["forward_spot"] == 1 and out["summary"]["refusals"] == {}


def test_a_missing_spot_is_no_spot(world):
    run_dir, out_dir = world()
    path = _FakePinned.base / "bars/BLK.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame["date"] == FILL, "close"] = 0.0
    frame.to_parquet(path, index=False)
    table = json.loads((run_dir / "result.json").read_text())
    table["plan"]["source_pins"]["bars"]["pin"]["files"]["bars/BLK.parquet"] = _sha(path)
    (run_dir / "result.json").write_text(json.dumps(table, sort_keys=True))
    assert run_node(run_dir, out_dir)["summary"]["refusals"] == {"no_spot": 1}


def test_a_duplicate_quote_row_refuses_the_book_and_is_counted(world):
    run_dir, out_dir = world()
    path = _FakePinned.base / "chain/BLK.parquet"
    frame = pd.read_parquet(path)
    row = frame[(frame["date"] == FILL) & (frame["expiration"] == EXPIRY)].iloc[:1]
    pd.concat([frame, row]).to_parquet(path, index=False)
    table = json.loads((run_dir / "result.json").read_text())
    table["plan"]["source_pins"]["chain"]["pin"]["files"]["chain/BLK.parquet"] = _sha(path)
    (run_dir / "result.json").write_text(json.dumps(table, sort_keys=True))
    assert run_node(run_dir, out_dir)["summary"]["refusals"] == {"duplicate_quote": 1}


def test_open_leg_unmarked_counts_per_day_and_leaves_the_entry_alone(world):
    run_dir, out_dir = world(drop_chain=(("BLK", LATER_DECISION),))
    out = run_node(run_dir, out_dir)
    day = [r for r in out["exposure"] if r["date"] == LATER_DECISION and r["bucket"] == "total"][0]
    assert day["excluded"] == {"open_leg_unmarked": 1}
    assert out["summary"]["open_leg_unmarked"] >= 1
    assert out["summary"]["refusals"] == {}


# ---- caching, development seal, output writing ---------------------------------------------
def test_the_chain_cache_stays_bounded_across_many_days(world):
    run_dir, _ = world()
    binding = json.loads((run_dir / "result.json").read_text())["plan"]["source_pins"]["chain"]
    quotes = mio_greeks.ChainQuotes(binding, "2021-07-01", cache_days=2)
    for day in (*DAYS, *DAYS, "2021-05-03", "2021-05-04"):
        quotes.chain("BLK", day, EXPIRY)
        assert len(quotes._days) <= 2
    assert quotes.chain("BLK", FILL, EXPIRY)                      # an evicted day is read again


def test_every_shipped_greeks_config_ends_before_a_seal_no_later_than_the_development_seal():
    configs = sorted((CHILD / "configs").glob("mio-greeks-*.json"))
    assert configs
    for path in configs:
        for node in json.loads(path.read_text())["pipeline"].values():
            params = node["params"]
            assert params["window"]["end"] < params["sealed_from"] <= mio_greeks.DEVELOPMENT_SEAL, path


def test_the_development_seal_is_one_named_constant():
    assert mio_greeks.DEVELOPMENT_SEAL == "2021-07-01"
    assert mio_greeks.DEVELOPMENT_SEAL < mio_greeks.PROTECTED_FROM


def test_an_empty_method_still_writes_every_csv_with_its_header(world):
    def empty(table):
        table["results"][0]["result"]["trades"] = []
    run_dir, out_dir = world(mutate=empty)
    run_node(run_dir, out_dir)
    for name in ("books.csv", "cells.csv", "exposure.csv"):
        lines = (out_dir / name).read_text().splitlines()
        assert len(lines) == 1 and lines[0].startswith(("method,", "symbol,", "date,")), name
    assert "static_delta_pnl" in (out_dir / "books.csv").read_text()


def test_nothing_is_written_when_any_output_cannot_be_serialized(tmp_path):
    node = MIOGreeksDiagnostics("g", {**PARAMS, "run_dir": str(tmp_path / "run"),
                                      "out_dir": str(tmp_path / "out")})
    with pytest.raises(ValueError):
        node._write(tmp_path / "out", {"books": [], "cells": [], "exposure": [],
                                       "summary": {"x": float("nan")}})
    assert not (tmp_path / "out").exists()


def test_the_summary_counts_mid_iv_disagreements_and_dropped_beta_names(world):
    run_dir, out_dir = world()
    summary = run_node(run_dir, out_dir)["summary"]
    assert summary["iv_disagree_legs"] == 0 and summary["beta_dropped_name_days"] == 0


# ---- read-only discipline -----------------------------------------------------------------
_OPENED = []


def _audit(event, args):
    if event == "open" and _OPENED is not None:
        _OPENED.append((str(args[0]), args[1], args[2]))


sys.addaudithook(_audit)


def test_run_log_is_never_opened_and_run_dir_is_never_written(world):
    run_dir, out_dir = world()
    log = run_dir / "run.log"
    before = _snapshot(run_dir)
    log.chmod(0)                                    # unreadable for any non-root user
    _OPENED.clear()
    try:
        run_node(run_dir, out_dir)
    finally:
        log.chmod(0o644)
    root = str(run_dir.resolve())
    inside = [(p, m, f) for p, m, f in _OPENED if str(p).startswith(root)]
    assert all(Path(p).name != "run.log" for p, _, _ in inside)
    assert all(_is_read(mode, flags) for _, mode, flags in inside), inside
    assert {Path(p).name for p, _, _ in inside} >= {"result.json", "trades.csv", "daily-account.csv"}
    assert _snapshot(run_dir) == before


def _is_read(mode, flags):
    if isinstance(mode, str):
        return not any(c in mode for c in "wax+")
    return not (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))


def _snapshot(path):
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size, _sha(p)) for p in sorted(path.iterdir())}


def test_a_read_only_run_dir_is_enough(world):
    run_dir, out_dir = world()
    modes = {run_dir: run_dir.stat().st_mode}
    for p in run_dir.iterdir():
        modes[p] = p.stat().st_mode
        p.chmod(stat.S_IRUSR)
    run_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        run_node(run_dir, out_dir)
    finally:
        for p, mode in modes.items():
            p.chmod(mode)
    assert (out_dir / "books.csv").exists()


def test_rerun_is_byte_identical(world):
    run_dir, out_dir = world()
    run_node(run_dir, out_dir)
    first = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    shutil.rmtree(out_dir)
    run_node(run_dir, out_dir)
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == first
    run_node(run_dir, out_dir)                      # and over its own outputs
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == first


def test_the_module_imports_no_solver_study_or_replay():
    tree = ast.parse((CHILD / "index_options/mio_greeks.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
            imported |= {f"{node.module}.{a.name}" for a in node.names}
    text = " ".join(sorted(imported))
    for banned in ("ledger_studies", "WeeklyOptionPortfolioStudy", "nodes", "highs", "pulp",
                   "scipy.optimize", "cvxpy", "mio_worker", "mio_execution_stress", "SavedReplay"):
        assert banned not in text, banned
    assert not any(isinstance(n, ast.Constant) and n.value == "run.log" for n in ast.walk(tree))
    assert "run.log" not in mio_greeks.INPUT_FILES


# ---- the shipped template ------------------------------------------------------------------
TEMPLATE = CHILD / "configs/mio-greeks-template.json"


def test_the_template_validates_and_ends_before_the_seal():
    doc = json.loads(TEMPLATE.read_text())
    node = doc["pipeline"]["greeks"]
    params = node["params"]
    assert doc["notes"].startswith("Template, not a run config: no MIO Greeks run is approved yet. "
                                   "Added by the orchestrator; awaiting owner confirmation.")
    assert "owner-approved deviation" not in doc["notes"] and node["notes"]
    assert node["uses"] == "index_options.mio_greeks:MIOGreeksDiagnostics"
    assert params["sealed_from"] == "2021-07-01"
    assert params["window"]["end"] == "2021-06-30" < params["sealed_from"]
    assert params["methods"] == ["w1_010_shock10_lp", "w1_003_shock10_lp",
                                 "w1_003_shock10_mad050_lp", "nominal_lp"]
    assert "PLACEHOLDER" in params["run_dir"] and "PLACEHOLDER" in params["out_dir"]
    assert MIOGreeksDiagnostics.validate_params(params) == []
    assert {k: params[k] for k in ("hedge_fee_per_share", "hedge_half_spread_bps",
                                   "hedge_borrow_rate")} == {
        "hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0, "hedge_borrow_rate": 0.005}
    assert "hedge_" in node["notes"] and "no default" in node["notes"]
    assert not (set(range(2022, 2026)) & {int(params["window"][k][:4]) for k in ("start", "end")})


def test_the_template_carries_every_frozen_core_key_with_the_placeholder_marker():
    from dskit.pipeline import option_exposure as ox
    params = json.loads(TEMPLATE.read_text())["pipeline"]["greeks"]["params"]
    for name in ox.BookGreeksReport.NEW_CORE_PARAMS:
        assert name in params, name
    assert params["hedge_cost_source"] == "placeholder"
    assert params["hedge_cost_source"] in ox.HEDGE_COST_SOURCES
    rates = tuple(params[k] for k in ox.StaticDeltaHedge._PARAMS)
    assert rates == ox.PLACEHOLDER_HEDGE_RATES
    bad = {**params, "hedge_cost_source": "measured"}
    assert any("hedge_cost_source_mismatch" in m for m in MIOGreeksDiagnostics.validate_params(bad))


# ---- review minors: dividends file, all-or-nothing outputs ------------------------------------
def test_a_symbol_with_no_bar_file_is_a_symbols_missing_refusal_for_the_dividend_read(world):
    world()
    node = MIOGreeksDiagnostics("greeks", {**PARAMS, "run_dir": "/x/run", "out_dir": "/x/out"})
    block = {"pin": {"files": {}}, "files": {}}
    with pytest.raises(GreeksRefusal) as exc:
        node._read_dividends(block, ["BLK"])
    assert code_of(exc) == "symbols_missing"


def _stale_outputs(out_dir):
    out_dir.mkdir()
    for name in mio_greeks.OUTPUT_FILES:
        (out_dir / name).write_text("OLD\n")


def test_a_failure_between_files_never_leaves_a_stale_summary_beside_new_csvs(world, monkeypatch):
    run_dir, out_dir = world()
    _stale_outputs(out_dir)
    real, landed = mio_greeks.atomic_write, []

    def dying(path, raw):
        if len(landed) == 2:
            raise OSError("killed between files")
        landed.append(Path(path).name)
        real(path, raw)
    monkeypatch.setattr(mio_greeks, "atomic_write", dying)
    with pytest.raises(OSError):
        run_node(run_dir, out_dir)
    assert not (out_dir / "greeks-summary.json").exists()          # the old summary went first
    assert landed == ["books.csv", "cells.csv"]


def test_the_summary_is_written_last_and_replaces_the_old_one(world, monkeypatch):
    run_dir, out_dir = world()
    _stale_outputs(out_dir)
    real, order = mio_greeks.atomic_write, []
    monkeypatch.setattr(mio_greeks, "atomic_write",
                        lambda path, raw: (order.append(Path(path).name), real(path, raw)))
    run_node(run_dir, out_dir)
    assert order[-1] == "greeks-summary.json" and sorted(order) == sorted(mio_greeks.OUTPUT_FILES)
    assert json.loads((out_dir / "greeks-summary.json").read_text())["methods"]


# ---- ADR-0310 A2 stage 1B: quarter, dividends census, seal crossing, cadence -----------------
CHILD_KEYS = ("quarter", "dividends", "seal_crossing")
POISON = "POISON"


def _poison(trade):
    """Make every economic field of a saved trade unusable (any arithmetic on it raises)."""
    for field in ("pnl", "credit", "legs", "factor", "premium", "fees", "contracts"):
        trade[field] = POISON


def _cross(table, only=None, poison=False):
    """Move the late-April MMM book's expiry past the seal (a settle in July)."""
    for r in table["results"]:
        if only is not None and r["id"] != only:
            continue
        r["result"]["trades"][2] = trade = copy.deepcopy(r["result"]["trades"][2])  # shared in the fixture
        trade["expiry"] = trade["close_date"] = "2021-07-16"
        if poison:
            _poison(trade)


def test_the_child_keys_are_declared_in_child_params_and_required():
    for key in CHILD_KEYS:
        assert key in MIOGreeksDiagnostics._CHILD_PARAMS
        short = {k: v for k, v in PARAMS.items() if k != key}
        problems = MIOGreeksDiagnostics.validate_params({**short, "run_dir": "/a", "out_dir": "/b"})
        assert any(key in p for p in problems), key


def test_quarter_bounds_are_the_calendar_quarter():
    assert MIOGreeksDiagnostics._quarter_bounds("2021Q2") == ("2021-04-01", "2021-06-30")
    assert MIOGreeksDiagnostics._quarter_bounds("2019Q4") == ("2019-10-01", "2019-12-31")
    assert MIOGreeksDiagnostics._quarter_bounds("2020Q1") == ("2020-01-01", "2020-03-31")


def test_the_quarter_is_echoed_in_the_run_summary(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    assert out["summary"]["quarter"] == "2021Q2"
    assert json.loads((out_dir / "greeks-summary.json").read_text())["quarter"] == "2021Q2"


def test_a_decision_outside_the_quarter_refuses_quarter_mismatch_before_any_pnl(world):
    def late(table):
        for r in table["results"]:
            r["result"]["trades"][0]["pnl"] += 1000.0     # would fail the recompute gate if read
    run_dir, out_dir = world(mutate=late)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, quarter="2021Q1", window={"start": "2021-01-01", "end": "2021-06-30"})
    assert code_of(exc) == "quarter_mismatch"
    assert "2021Q1" in str(exc.value) and "2021-04-05" in str(exc.value)
    assert not out_dir.exists()


def test_a_decision_in_the_quarter_that_settles_in_the_next_is_not_a_mismatch(world):
    run_dir, out_dir = world(mutate=lambda t: _cross(t, poison=True))
    out = run_node(run_dir, out_dir, seal_crossing="exclude")
    assert out["summary"]["quarter"] == "2021Q2"


def test_dividends_required_refuses_before_any_pnl_and_lists_every_symbol_without_the_column(world):
    def bad_pnl(table):
        for r in table["results"]:
            r["result"]["trades"][0]["pnl"] += 1000.0
    run_dir, out_dir = world(mutate=bad_pnl)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, dividends="required")
    assert code_of(exc) == "dividend_column_absent"
    assert "BLK" in str(exc.value) and "MMM" in str(exc.value)
    assert not out_dir.exists()


def test_dividends_required_lists_only_the_symbols_that_lack_the_column(world):
    run_dir, out_dir = world(dividends={"BLK": {}})
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, dividends="required")
    assert code_of(exc) == "dividend_column_absent"
    assert "MMM" in str(exc.value) and "BLK" not in str(exc.value)


def test_dividends_required_with_the_column_runs_and_echoes_the_choice(world):
    run_dir, out_dir = world(dividends={"BLK": {}, "MMM": {}})
    out = run_node(run_dir, out_dir, dividends="required")
    assert out["summary"]["dividends"] == "required"
    assert {r["hedge_status"] for r in out["books"]} == {"ok"}


def test_dividends_optional_proceeds_with_flags_without_the_column(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir, dividends="optional")
    assert out["summary"]["dividends"] == "optional"
    assert {r["hedge_status"] for r in out["books"]} == {"dividends_absent"}


def test_seal_crossing_refuse_is_a_census_of_dates_only(world):
    run_dir, out_dir = world(mutate=lambda t: _cross(t, poison=True),
                             extra_methods=[{"id": "alt"}])
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, methods=[METHOD, "alt"])
    assert code_of(exc) == "window_outside_data_rights"
    text = str(exc.value)
    assert "n_crossing" in text and "2" in text
    assert METHOD in text and "alt" in text
    assert "2021-04-12:MMM:2021-05-21" in text and "expiry" in text and "2021-07-16" in text
    assert POISON not in text and not out_dir.exists()


def test_seal_crossing_exclude_drops_the_books_before_any_economics_is_touched(world):
    run_dir, out_dir = world(mutate=lambda t: _cross(t, poison=True))
    out = run_node(run_dir, out_dir, seal_crossing="exclude")
    summary = out["summary"]
    assert {r["id"] for r in out["books"]} == {BLK_ID, f"{METHOD}|2021-04-05:MMM:2021-05-21"}
    assert summary["seal_crossing_excluded"] == [{
        "method": METHOD, "id": "2021-04-12:MMM:2021-05-21", "decision_date": LATER_DECISION,
        "fill_date": LATER_FILL, "expiry": "2021-07-16"}]
    assert summary["seal_crossing"] == "exclude"
    assert summary["total_excludes_seal_crossing"] is True
    assert summary["methods"][METHOD]["n_books_total"] == 3
    assert summary["methods"][METHOD]["n_books_excluded"] == 1
    assert POISON not in (out_dir / "books.csv").read_text()
    assert json.loads((out_dir / "greeks-summary.json").read_text())["seal_crossing_excluded"]


def test_seal_crossing_exclude_reports_unequal_method_sets_and_counts_shared_books_after(world):
    run_dir, out_dir = world(mutate=lambda t: _cross(t, only="alt", poison=True),
                             extra_methods=[{"id": "alt"}])
    out = run_node(run_dir, out_dir, methods=[METHOD, "alt"], seal_crossing="exclude")
    summary = out["summary"]
    assert summary["unequal_method_sets"] is True
    assert summary["total_excludes_seal_crossing"] is True
    assert summary["methods"][METHOD]["n_books_excluded"] == 0
    assert summary["methods"]["alt"]["n_books_excluded"] == 1
    assert summary["methods_compared"] == [METHOD, "alt"]
    assert summary["formulations_share_books"] is True
    assert summary["shared_book_count"] == 2          # the excluded book is shared by no one now


def test_shared_book_count_without_exclusion_counts_every_book_under_both_methods(world):
    run_dir, out_dir = world(extra_methods=[{"id": "alt"}])
    summary = run_node(run_dir, out_dir, methods=[METHOD, "alt"])["summary"]
    assert summary["shared_book_count"] == 3
    assert summary["unequal_method_sets"] is False
    assert summary["total_excludes_seal_crossing"] is False
    assert summary["seal_crossing_excluded"] == []


def test_a_single_method_shares_no_book(world):
    run_dir, out_dir = world()
    assert run_node(run_dir, out_dir)["summary"]["shared_book_count"] == 0


@pytest.mark.parametrize("field,value", [
    ("expiry", "2024-03-15"), ("fill_date", "2024-01-02"), ("decision_date", "2021-03-30"),
    ("fill_date", "2021-3-6")])
def test_exclude_never_admits_a_protected_or_outside_window_or_bad_date(world, field, value):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0][field] = value
    run_dir, out_dir = world(mutate=move)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, seal_crossing="exclude")
    assert code_of(exc) in ("window_outside_data_rights", "bad_date")
    assert not out_dir.exists()


def test_a_book_decided_after_the_seal_is_not_a_crossing_and_still_refuses(world):
    def move(table):
        for r in table["results"]:
            r["result"]["trades"][0]["decision_date"] = "2021-07-02"
    run_dir, out_dir = world(mutate=move)
    with pytest.raises(GreeksRefusal) as exc:
        run_node(run_dir, out_dir, seal_crossing="exclude")
    assert code_of(exc) == "window_outside_data_rights"


def test_cells_carry_the_cadence_of_their_names(world):
    run_dir, out_dir = world()
    cells = run_node(run_dir, out_dir)["cells"]
    assert {c["symbol"]: c["cadence"] for c in cells} == {"BLK": "weekly", "MMM": "monthly"}
    header = (out_dir / "cells.csv").read_text().splitlines()[0].split(",")
    assert {"symbol", "cadence", "rank"} <= set(header)


def test_the_template_carries_the_a2_child_keys_and_every_declared_param():
    params = json.loads(TEMPLATE.read_text())["pipeline"]["greeks"]["params"]
    assert set(MIOGreeksDiagnostics._CHILD_PARAMS) <= set(params) <= set(MIOGreeksDiagnostics._PARAMS)
    assert params["cell_by"] == ["symbol", "cadence", "rank"]
    assert params["dividends"] == "optional" and params["seal_crossing"] == "refuse"
    assert params["window"]["end"] < params["sealed_from"] <= mio_greeks.DEVELOPMENT_SEAL
    quarter = params["quarter"]
    start, end = MIOGreeksDiagnostics._quarter_bounds(quarter)
    assert params["window"]["start"] <= start and end <= params["window"]["end"]
    assert MIOGreeksDiagnostics.validate_params(params) == []
    notes = json.loads(TEMPLATE.read_text())["pipeline"]["greeks"]["notes"]
    for word in ("quarter", "dividends", "seal_crossing", "cadence", "A1"):
        assert word in notes, word


# ---- ADR-0310 A2 stage 2a: child wiring of the core outputs --------------------------------------
BOOK_PNLS = {BLK_ID: -938.90, f"{METHOD}|2021-04-05:MMM:2021-05-21": 398.7,
             f"{METHOD}|2021-04-12:MMM:2021-05-21": 398.7}


def test_the_quarter_is_stamped_on_every_row_of_every_output(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    for name in ("books", "cells", "exposure"):
        assert out[name] and {r["quarter"] for r in out[name]} == {"2021Q2"}, name
        header = (out_dir / f"{name}.csv").read_text().splitlines()[0].split(",")
        assert "quarter" in header, name


def test_an_empty_method_still_declares_the_quarter_column(world):
    def empty(table):
        for r in table["results"]:
            r["result"]["trades"] = []
    run_dir, out_dir = world(mutate=empty)
    run_node(run_dir, out_dir)
    for name in ("books", "cells", "exposure"):
        assert "quarter" in (out_dir / f"{name}.csv").read_text().splitlines()[0].split(","), name


def test_direction_read_is_the_method_by_quarter_block_with_the_tests_own_sums(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    read = out["summary"]["methods"][METHOD]["direction_read"]
    assert (read["quarter"], read["method"]) == ("2021Q2", METHOD)
    assert (read["n_books"], read["n_books_total"], read["n_books_excluded"]) == (3, 3, 0)
    assert read["pnl_usd"] == pytest.approx(sum(BOOK_PNLS.values()))
    assert read["gross_pnl_usd"] == pytest.approx(sum(abs(v) for v in BOOK_PNLS.values()))
    rows = out["books"]
    assert read["static"]["delta_part_usd"] == pytest.approx(sum(r["static_delta_pnl"] for r in rows))
    assert read["static"]["beyond_usd"] == pytest.approx(sum(r["beyond_static_delta"] for r in rows))
    assert read["payoff"]["delta_part_usd"] == pytest.approx(sum(r["payoff_delta_pnl"] for r in rows))
    for part in ("static", "payoff"):
        table = read[part]["sign_table"]
        assert sum(q["n"] for q in table.values()) == 3
        assert sum(q["pnl_usd"] for q in table.values()) == pytest.approx(read["pnl_usd"])
    assert read["descriptive_only"].startswith("descriptive only")
    json.dumps(read, allow_nan=False)


def test_direction_read_basis_usd_is_the_distribution_of_the_books_basis(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    basis = [r["delta_sh"] * (r["s_fill"] - r["forward"]) for r in out["books"]]
    got = out["summary"]["methods"][METHOD]["direction_read"]["basis_usd"]
    assert got["n"] == 3 and got["sum"] == pytest.approx(sum(basis))
    assert (got["min"], got["max"]) == (pytest.approx(min(basis)), pytest.approx(max(basis)))
    assert got["median"] == pytest.approx(sorted(basis)[1])
    gross = sum(abs(v) for v in BOOK_PNLS.values())
    assert got["abs_sum_over_gross"] == pytest.approx(sum(abs(b) for b in basis) / gross)


def test_direction_read_forward_hedge_block_echoes_min_books_and_the_cost_source(world):
    run_dir, out_dir = world()
    read = run_node(run_dir, out_dir)["summary"]["methods"][METHOD]["direction_read"]
    block = read["forward_hedge"]
    assert block["min_books"] == 5 and block["hedge_cost_source"] == "placeholder"
    assert block["n_decision_unmarked"] == 0 and block["n_not_attributed"] == 0
    # every attributed book is a candidate: nothing is hard-to-borrow in the fixture's calm closes
    assert block["n_hard_to_borrow"] == 0
    assert block["n_hedged"] + block["n_unbounded_above"] + block["n_classifier_pending"] == 3
    conc = read["concentration"]
    assert conc["min_books"] == 5 and conc["small_cell"] is True
    assert conc["n_books"] == block["n_hedged"]
    assert read["concentration_pnl"]["n_books"] == 3 and read["concentration_pnl"]["min_books"] == 5


def test_the_decision_session_sizes_the_forward_hedge_not_the_fill_session(world):
    run_dir, out_dir = world()
    row = rows_by(run_node(run_dir, out_dir))[BLK_ID]
    scale = 100 * (5 + 1) / 6
    d450 = black76_delta("call", BLK_FORWARD, 450.0, BLK_VOL, 11 / 365)       # decision: 11 days
    d455 = black76_delta("call", BLK_FORWARD, 455.0, BLK_VOL, 11 / 365)
    decision = 100 * (5 * d450 + d455)
    assert scale > 0 and row["delta_f_sh_decision"] == pytest.approx(decision, rel=1e-9)
    fill = 100 * (5 * black76_delta("call", BLK_FORWARD, 450.0, BLK_VOL, 10 / 365)
                  + black76_delta("call", BLK_FORWARD, 455.0, BLK_VOL, 10 / 365))
    assert row["delta_f_sh"] == pytest.approx(fill, rel=1e-9)
    assert row["delta_f_sh_decision"] != pytest.approx(row["delta_f_sh"], rel=1e-6)
    assert row["forward_hedge_units"] == math.trunc(-decision)
    assert row["hedge_rounding_sh"] == pytest.approx(row["forward_hedge_units"] + decision)
    assert row["trade_costs_usd"] == pytest.approx(
        abs(row["forward_hedge_units"]) * (2 * 0.005 + (BLK_FORWARD + BLK_SETTLE) * 5.0 / 1e4))
    assert row["borrow_usd"] == pytest.approx(
        abs(row["forward_hedge_units"]) * BLK_FORWARD * 0.005 * 10 / 365)


def test_a_book_the_decision_chain_cannot_mark_is_decision_unmarked_and_stays_attributed(world):
    run_dir, out_dir = world(drop_chain=(("BLK", DECISION),))
    out = run_node(run_dir, out_dir)
    row = rows_by(out)[BLK_ID]
    assert row["forward_hedge_status"] == "decision_unmarked"
    assert row["status"] == "ok" and row["static_delta_pnl"] is not None
    assert all(row[c] is None for c in ("forward_hedged_pnl", "hedge_pnl", "forward_hedge_costs"))
    summary = out["summary"]
    assert summary["decision_greeks_refusals"] == {"no_fill_quote": 1}
    assert summary["methods"][METHOD]["direction_read"]["forward_hedge"]["n_decision_unmarked"] == 1
    assert summary["methods"][METHOD]["direction_read"]["n_books"] == 3      # rank-null books count


def test_the_run_summary_totals_the_forward_hedge_statuses_and_decision_refusals(world):
    run_dir, out_dir = world(extra_methods=[{"id": "alt"}], drop_chain=(("BLK", DECISION),))
    summary = run_node(run_dir, out_dir, methods=[METHOD, "alt"])["summary"]
    assert summary["decision_greeks_refusals"] == {"no_fill_quote": 2}
    per = [s["forward_hedge_status"] for s in summary["methods"].values()]
    total = summary["forward_hedge_status"]
    assert sum(total.values()) == 6 and total["decision_unmarked"] == 2
    assert all(total[k] == sum(p.get(k, 0) for p in per) for k in total)


def test_direction_read_carries_the_exclusion_counts_of_a_seal_crossing_run(world):
    run_dir, out_dir = world(mutate=lambda t: _cross(t, poison=True))
    out = run_node(run_dir, out_dir, seal_crossing="exclude")
    read = out["summary"]["methods"][METHOD]["direction_read"]
    assert (read["n_books"], read["n_books_total"], read["n_books_excluded"]) == (2, 3, 1)
    assert read["pnl_usd"] == pytest.approx(-938.90 + 398.7)


def test_cells_carry_the_forward_hedge_columns_and_null_sums_do_not_propagate(world):
    run_dir, out_dir = world()
    out = run_node(run_dir, out_dir)
    header = (out_dir / "cells.csv").read_text().splitlines()[0].split(",")
    for name in ("n_hedged", "n_unbounded_above", "hedgeable_share_gross", "forward_hedged_pnl_usd",
                 "hedge_pnl_usd", "hedge_loss_reduction_usd", "hedge_costs_usd",
                 "loo_sign_stable_hedge_pnl", "small_cell", "top1_share_gross", "descriptive_only"):
        assert name in header, name
    summary = out["summary"]
    assert summary["hedged_pnl_usd"] is None and summary["n_stock_hedged"] == 0   # no dividend column
    assert all(c["loo_sign_stable_hedge_pnl"] is None for c in out["cells"])


def test_the_template_and_readme_describe_the_direction_read(world):
    notes = json.loads(TEMPLATE.read_text())["pipeline"]["greeks"]["notes"]
    for word in ("direction_read", "decision", "forward_hedge_status", "quarter"):
        assert word in notes, word
    readme = (CHILD / "README.md").read_text()
    assert "direction_read" in readme
