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


def build_run(root, *, mutate=None, drop_chain=(), calls_only=(), no_iv=(), extra_methods=None):
    """Write a closed run, bars and chains under ``root``; return ``(run_dir, base)``."""
    base = Path(root) / "source"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(root) / "run"
    run_dir.mkdir()
    bars = {"BLK": _closes(450.0, 1.0, {FILL: BLK_SPOT, EXPIRY: BLK_SETTLE}),
            "MMM": _closes(100.0, 1.0, {FILL: MMM_SPOT, MONTHLY_EXPIRY: 105.0})}
    bar_frames = {s: pd.DataFrame({"symbol": s, "date": list(v), "close": list(v.values())})
                  for s, v in bars.items()}
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
    "max_pair_spread_frac": 0.5, "cell_by": ["symbol", "rank"], "min_total_usd": 1.0,
    "dte_edges": [0, 7, 14, 28, 60], "top_k": 1, "exposure_days": "both",
    "beta_lookback": 3, "beta_min_pairs": 2, "chain_cache_days": 8,
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
    assert row["stock_pnl"] == pytest.approx(delta_sh * (448.0 - 452.0), rel=1e-9)
    assert row["stock_pnl"] + row["residual"] == pytest.approx(pnl, abs=1e-9)
    assert (row["symbol"], row["rank"], row["cadence"], row["method"]) == (
        "BLK", 2, "weekly", METHOD)


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
    assert {"method", "id", "symbol", "rank", "stock_pnl", "residual", "status"} <= set(header)
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


# ---- the A0006 rank ------------------------------------------------------------------------
def _listed(days):
    return [(date(2021, 4, 5) + timedelta(days=d)).isoformat() for d in days]


@pytest.mark.parametrize("dte,rank", [(1, 1), (7, 1), (8, 2), (14, 2), (15, 3), (56, 8), (57, None)])
def test_weekly_rank_is_the_seven_day_window(dte, rank):
    listed = _listed([4, 11, 18, 25, 32, 39, 46, 53, 60])         # weekly Fridays
    expiry = (date(2021, 4, 5) + timedelta(days=dte)).isoformat()
    assert CellRank().of("2021-04-05", expiry, listed) == ("weekly", rank)


def test_monthly_only_names_use_the_expiry_position():
    listed = ["2021-04-16", "2021-05-21", "2021-06-18", "2021-07-16"]
    assert CellRank().of("2021-04-05", "2021-04-16", listed) == ("monthly", 1)
    assert CellRank().of("2021-04-05", "2021-06-18", listed) == ("monthly", 3)
    assert CellRank().of("2021-04-05", "2021-07-16", listed) == ("monthly", None)


def test_a_thursday_holiday_expiry_keeps_its_window():
    listed = ["2021-04-01", "2021-04-09", "2021-04-16"]            # 04-01 is a Thursday
    assert CellRank().of("2021-03-26", "2021-04-01", listed) == ("weekly", 1)


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
    assert rows_by(out)[f"{METHOD}|2021-04-05:BLK:2021-04-16"]["stock_pnl"] is None


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
    assert row["status"] == "forward_spot" and row["stock_pnl"] is not None
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
    assert "stock_pnl" in (out_dir / "books.csv").read_text()


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
    assert not (set(range(2022, 2026)) & {int(params["window"][k][:4]) for k in ("start", "end")})
