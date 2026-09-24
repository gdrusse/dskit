"""ADR-0182 real-data track: index reader, VIX-proxy pricing, condor backtest, configs."""

import copy
import json
import math
import random
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import run_walk_forward
from dskit.pipeline.kinds_flow import Join
from dskit.pipeline.planner import plan

from index_options.nodes import CondorBacktest
from index_options.observations import IndexCloseRows
from index_options.pricing import VixProxyQuotes, black76

YEARS = 21 / 252
REAL = ("run-real-distribution.json", "run-real-har.json", "run-real-lightgbm.json")


def _day(i, start=date(2000, 1, 3)):
    """The i-th weekday from ``start`` (a trading-day stand-in; no holidays)."""
    weeks, rem = divmod(i, 5)
    return (start + timedelta(days=7 * weeks + rem)).isoformat()


def _index_rows(n, missing_vix=(), seed=3):
    rng = random.Random(seed)
    rows, spx = [], 1000.0
    for i in range(n):
        spx *= math.exp(rng.gauss(0.0, 0.01))
        day = _day(i)
        rows.append({"symbol": "SPX", "date": day, "open": None, "high": None, "low": None,
                     "close": round(spx, 2), "effective_date": day})
        if i not in missing_vix:
            rows.append({"symbol": "VIX", "date": day, "open": 20.0, "high": 21.0,
                         "low": None, "close": round(18 + 4 * rng.random(), 2),
                         "effective_date": day})
    return rows


# -- IndexCloseRows ---------------------------------------------------------------------


def test_index_reader_knobs_are_narrowed_and_serving_refused(tmp_path):
    assert IndexCloseRows._PARAMS == ("root", "source", "since_ms", "as_of_acquisition_ms",
                                      "symbol")
    assert IndexCloseRows.serving_effect({}, {}) == "forbidden"
    for knob in ("stream", "key_fields", "ts_field", "ts_unit", "ts_out", "shared_fields",
                 "iv_symbol"):
        with pytest.raises(ConfigError, match=knob):
            IndexCloseRows("x", {"root": str(tmp_path), "source": "c", "symbol": "SPX",
                                 knob: "y"})
    with pytest.raises(ConfigError, match="symbol"):
        IndexCloseRows("x", {"root": str(tmp_path), "source": "c"})


def test_index_projection_keeps_one_symbol_in_time_order():
    node = IndexCloseRows("spx", {"root": ".", "source": "c", "symbol": "SPX"})
    raw = [{"symbol": "VIX", "date": "2020-01-02", "close": 13.0, "asof_ms": 2},
           {"symbol": "SPX", "date": "2020-01-03", "close": 3234, "asof_ms": 3, "low": None},
           {"symbol": "SPX", "date": "2020-01-02", "close": 3257.85, "asof_ms": 2}]
    assert node.project(raw) == [
        {"instrument": "SPX", "contract": "SPX", "group": "SPX", "close": 3257.85,
         "asof_ms": 2, "date": "2020-01-02"},
        {"instrument": "SPX", "contract": "SPX", "group": "SPX", "close": 3234.0,
         "asof_ms": 3, "date": "2020-01-03"}]
    for bad in (0, None, float("nan"), "3234"):
        with pytest.raises(ValueError, match="close"):
            node.project([dict(raw[1], close=bad)])


def test_index_reader_and_join_over_a_real_onboarding_store(store_factory):
    rows = _index_rows(6, missing_vix={1})
    store = store_factory({"index_daily": rows}, source="cboe-index",
                          effective_field="effective_date")
    assert store.acquire("index_daily")["records"] == 11
    spx = IndexCloseRows("spx", store.node_params(symbol="SPX")).run(None, {})
    vix = IndexCloseRows("vix", store.node_params(symbol="VIX")).run(None, {})
    assert [r["date"] for r in spx["records"]] == [_day(i) for i in range(6)]
    assert all(type(r["asof_ms"]) is int for r in spx["records"])
    assert _day(1) not in vix["by_date"] and len(vix["by_date"]) == 5
    joined = Join("market", {"key": "date", "how": "left",
                             "unmatched_fill": {"iv_index": None}}).run(
        None, {"records": spx["records"], "iv_index": vix["by_date"]})["records"]
    assert [r["iv_index"] for r in joined][:2] == [vix["by_date"][_day(0)], None]
    assert {k for r in joined for k in r} == {"instrument", "contract", "group", "close",
                                              "asof_ms", "date", "iv_index"}


# -- pricing ------------------------------------------------------------------------------


def test_black76_known_value_and_put_call_parity():
    # ATM, F=K=100, vol 20%, 1y: 100 * (2 N(0.1) - 1)
    assert black76("call", 100.0, 100.0, 0.2, 1.0) == pytest.approx(7.965567, abs=1e-6)
    for strike in (80.0, 100.0, 125.0):
        call = black76("call", 100.0, strike, 0.3, 0.5, rate=0.04)
        put = black76("put", 100.0, strike, 0.3, 0.5, rate=0.04)
        assert call - put == pytest.approx(math.exp(-0.02) * (100.0 - strike))


def test_black76_monotonicity_and_refusals():
    calls = [black76("call", 100.0, k, 0.2, YEARS) for k in (90, 95, 100, 105, 110)]
    puts = [black76("put", 100.0, k, 0.2, YEARS) for k in (90, 95, 100, 105, 110)]
    assert calls == sorted(calls, reverse=True) and puts == sorted(puts)
    assert black76("put", 100.0, 95.0, 0.3, YEARS) > black76("put", 100.0, 95.0, 0.2, YEARS)
    for args in (("fwd", 100.0, 100.0, 0.2, 1.0), ("call", 0.0, 100.0, 0.2, 1.0),
                 ("call", 100.0, 100.0, 0.0, 1.0), ("call", 100.0, 100.0, 0.2, -1.0),
                 ("call", 100.0, float("nan"), 0.2, 1.0)):
        with pytest.raises(ValueError):
            black76(*args)


def test_proxy_skew_floor_and_spread():
    quotes = VixProxyQuotes(0.1, 0.05, 0.05, 0.03, 0.0)
    atm = 0.2
    sd = atm * math.sqrt(YEARS)
    assert quotes.iv(1000.0, 1000.0, 20.0, YEARS) == pytest.approx(atm)
    assert quotes.iv(1000.0, 1100.0, 20.0, YEARS) == pytest.approx(atm)  # calls: no skew
    z = math.log(900 / 1000) / sd
    assert quotes.iv(1000.0, 900.0, 20.0, YEARS) == pytest.approx(atm * (1 - 0.1 * z))
    assert VixProxyQuotes(0.0, 0.3, 0, 0, 0).iv(1000.0, 1000.0, 20.0, YEARS) == 0.3
    bid, mid, ask = quotes.quote("put", 1000.0, 950.0, 20.0, YEARS)
    assert mid == pytest.approx(black76("put", 1000.0, 950.0,
                                        quotes.iv(1000.0, 950.0, 20.0, YEARS), YEARS))
    assert ask - mid == pytest.approx(max(0.05, 0.03 * mid)) == pytest.approx(mid - bid)
    far_bid, far_mid, far_ask = quotes.quote("call", 1000.0, 1400.0, 20.0, YEARS)
    assert far_bid == 0.0 and far_ask == pytest.approx(far_mid + 0.05)


@pytest.mark.parametrize("knobs", [
    (-0.1, 0.05, 0.05, 0.03, 0.0), (0.1, 0.0, 0.05, 0.03, 0.0), (0.1, 0.05, -1, 0.03, 0.0),
    (0.1, 0.05, 0.05, 1.0, 0.0), (0.1, 0.05, 0.05, 0.03, float("inf")),
    (True, 0.05, 0.05, 0.03, 0.0),
])
def test_proxy_knob_refusal(knobs):
    assert VixProxyQuotes.problems(*knobs)
    with pytest.raises(ValueError):
        VixProxyQuotes(*knobs)


# -- CondorBacktest -----------------------------------------------------------------------

#: Draws whose 10%/90% lower quantiles are exactly -1 and +1 (index ceil(q n) - 1).
DRAWS = [-1.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]
BT = {"split": "val", "hold_steps": 3, "short_q": 0.1, "wing_points": 25,
      "strike_increment": 5, "multiplier": 100, "fee_per_leg": 0.65, "skew_per_z": 0.0,
      "iv_floor": 0.05, "half_spread_min": 0.0, "half_spread_frac": 0.0,
      "trading_days_per_year": 252 * 3 // 21}  # T = 3 / 36 = 21 / 252


class _Split:
    def split_of(self, frame):
        return "val" if frame.asof_ms >= 100 else "train"


CTX = SimpleNamespace(splits=_Split())


def _row(i, outcome_z=0.0, **extra):
    return {"asof_ms": 100 + i, "instrument": "SPX", "date": f"d{i}", "close": 1000.0,
            "iv_index": 20.0, "reference_scale": 0.05, "samples": list(DRAWS),
            "outcome": outcome_z, **extra}


def _z(level):
    return math.log(level / 1000.0) / 0.05


def _credit(strikes):
    k = strikes
    mids = [black76(r, 1000.0, s, 0.2, YEARS) for r, s in
            zip(("put", "put", "call", "call"), k)]
    return (mids[1] + mids[2] - mids[0] - mids[3]) * 100 - 4 * 0.65


def test_backtest_books_hand_checked():
    rows = [_row(0, 0.0), _row(3, _z(900.0)), _row(6, _z(940.0))]
    rows += [_row(i) for i in (1, 2, 4, 5, 7, 8)] + [_row(-50)]  # fillers + one train row
    out = CondorBacktest("bt", dict(BT)).run(CTX, {"forecasts": rows})
    report, m = out["report"].value, out["metrics"]
    assert report["kind"] == "vix_proxy_condor_backtest" and report["pricing"] == "vix_proxy"
    assert report["decision_eligible"] is False
    assert [e["date"] for e in report["ledger"]] == ["d0", "d3", "d6"]
    # outward rounding (puts down, calls up). model shorts: 1000 e^{-0.05} = 951.2 -> 950,
    # 1000 e^{0.05} = 1051.3 -> 1055; wings 25 beyond the unrounded shorts
    model = (925, 950, 1055, 1080)
    # implied: s = 0.2 sqrt(21/252); ln(K/F) = -s^2/2 +- s z_0.1 -> 927.1 / 1075.003; wings 25
    implied = (900, 925, 1080, 1105)
    c_model, c_implied = _credit(model), _credit(implied)
    first = report["ledger"][0]["books"]
    assert first["model"]["strikes"] == list(model) == first["always"]["strikes"]
    assert first["implied"]["strikes"] == list(implied)
    assert first["model"]["credit_usd"] == pytest.approx(c_model)
    assert first["implied"]["credit_usd"] == pytest.approx(c_implied)
    # expected under DRAWS: every draw (951.2 .. 1051.3) settles inside 950 / 1055
    assert report["ledger"][0]["model_expected_pnl_usd"] == pytest.approx(c_model)
    model_pnls = [c_model, c_model - 2500, c_model - 1000]  # S_T = 1000, 900, 940
    implied_pnls = [c_implied, c_implied - 2500, c_implied]  # 940 sits above the 925 short
    assert [e["books"]["model"]["pnl_usd"] for e in report["ledger"]] == \
        pytest.approx(model_pnls)
    assert [e["books"]["implied"]["pnl_usd"] for e in report["ledger"]] == \
        pytest.approx(implied_pnls)
    assert report["ledger"][1]["settlement"] == pytest.approx(900.0)
    assert m["n_entries"] == 3 and m["n_rows_in_split"] == 9
    for book, pnls in (("model", model_pnls), ("always", model_pnls),
                       ("implied", implied_pnls)):
        assert m[f"{book}_n_trades"] == 3
        assert m[f"{book}_total_pnl_usd"] == pytest.approx(sum(pnls))
        assert m[f"{book}_mean_pnl_usd"] == pytest.approx(sum(pnls) / 3)
        assert m[f"{book}_hit_rate"] == pytest.approx(sum(p > 0 for p in pnls) / 3)
        assert m[f"{book}_cvar_usd"] == pytest.approx(min(pnls))  # 5% of 3 rounds up to 1
    assert m["model_max_drawdown_usd"] == pytest.approx(3500 - 2 * c_model)
    assert m["implied_max_drawdown_usd"] == pytest.approx(2500 - c_implied)
    assert m["model_mean_credit_usd"] == pytest.approx(c_model)


def test_min_edge_gates_only_the_model_book():
    rows = [_row(0)]
    out = CondorBacktest("bt", dict(BT, min_edge_usd=1e6)).run(CTX, {"forecasts": rows})
    m, cell = out["metrics"], out["report"].value["ledger"][0]["books"]["model"]
    assert m["model_n_trades"] == 0 and m["model_n_skipped_below_min_edge"] == 1
    assert m["model_total_pnl_usd"] == 0.0 and m["model_cvar_usd"] == 0.0
    assert cell["entered"] is False and cell["pnl_usd"] is None
    assert m["always_n_trades"] == 1 and m["implied_n_trades"] == 1


def test_entries_never_overlap_and_skip_reasons_advance_one_row():
    rows = [_row(i) for i in range(12)]
    rows[3] = _row(3, iv_index=None)
    rows[4] = _row(4, outcome=None)
    rows[5] = _row(5, samples=None)
    rows[6] = _row(6, close=0.0)
    rows[7] = _row(7, reference_scale=None)
    m = CondorBacktest("bt", dict(BT)).run(CTX, {"forecasts": list(reversed(rows))})
    ledger = m["report"].value["ledger"]
    assert [e["date"] for e in ledger] == ["d0", "d8", "d11"]
    assert {k: v for k, v in m["metrics"].items() if k.startswith("n_skipped_")} == {
        "n_skipped_no_iv": 1, "n_skipped_no_outcome": 1, "n_skipped_no_forecast": 1,
        "n_skipped_no_forward": 1, "n_skipped_no_scale": 1}


def test_default_hold_is_twenty_one_rows_per_instrument():
    rows = [_row(i) for i in range(50)] + [_row(i, instrument="XSP") for i in range(22)]
    params = {k: v for k, v in BT.items() if k not in ("hold_steps", "trading_days_per_year")}
    ledger = CondorBacktest("bt", params).run(CTX, {"forecasts": rows})["report"].value["ledger"]
    assert [(e["instrument"], e["date"]) for e in ledger] == [
        ("SPX", "d0"), ("SPX", "d21"), ("SPX", "d42"), ("XSP", "d0"), ("XSP", "d21")]


def test_degenerate_strikes_and_nonpositive_credit_are_counted():
    narrow = CondorBacktest("bt", dict(BT, wing_points=1)).run(CTX, {"forecasts": [_row(0)]})
    assert narrow["metrics"]["model_n_skipped_degenerate_strikes"] == 1
    assert narrow["metrics"]["implied_n_skipped_degenerate_strikes"] == 1
    assert narrow["metrics"]["always_n_trades"] == 0
    costly = CondorBacktest("bt", dict(BT, fee_per_leg=1000)).run(CTX, {"forecasts": [_row(0)]})
    for book in CondorBacktest.BOOKS:
        assert costly["metrics"][f"{book}_n_skipped_nonpositive_credit"] == 1


def test_wing_z_scales_wings_with_each_book():
    out = CondorBacktest("bt", {**{k: v for k, v in BT.items() if k != "wing_points"},
                                "wing_z": 0.5}).run(CTX, {"forecasts": [_row(0)]})
    books = out["report"].value["ledger"][0]["books"]
    # model: 1000 e^{0.05 * (-1.5)} = 927.7 -> 925; 1000 e^{0.075} = 1077.9 -> 1080
    assert books["model"]["strikes"] == [925, 950, 1055, 1080]


def test_no_usable_entry_refuses():
    with pytest.raises(ValueError, match="no in-split row"):
        CondorBacktest("bt", dict(BT)).run(CTX, {"forecasts": [_row(-50), _row(0, iv_index=None)]})


@pytest.mark.parametrize("change", [
    {"split": "nope"}, {"short_q": 0.5}, {"short_q": 0}, {"wing_z": 0.5},
    {"wing_points": 0}, {"strike_increment": -5}, {"multiplier": 0}, {"multiplier": True},
    {"fee_per_leg": -1}, {"hold_steps": 0}, {"trading_days_per_year": 2.5},
    {"min_edge_usd": float("nan")}, {"cvar_alpha": 1.0}, {"skew_per_z": -1},
    {"iv_floor": 0}, {"half_spread_frac": 1}, {"rate": "0"}, {"surprise": 1},
])
def test_backtest_knob_refusal(change):
    with pytest.raises(ConfigError):
        CondorBacktest("bt", {**BT, **change})


def test_backtest_requires_wings_and_proxy_knobs():
    for missing in ("wing_points", "skew_per_z", "multiplier", "split"):
        with pytest.raises(ConfigError):
            CondorBacktest("bt", {k: v for k, v in BT.items() if k != missing})


# -- configs ------------------------------------------------------------------------------


def _load(child_root, name):
    return json.loads((child_root / "configs" / name).read_text())


@pytest.mark.parametrize("name", REAL)
def test_real_configs_plan(child_root, monkeypatch, name):
    monkeypatch.chdir(child_root)
    planned = plan(load_document(str(child_root / "configs" / name)))
    assert {"spx", "vix", "market", "rv", "labels", "fwd", "model", "score", "condor",
            "backtest"} == set(planned.order)


def test_real_zoo_plans_and_lists_every_real_rung(child_root, monkeypatch):
    monkeypatch.chdir(child_root)
    zoo = _load(child_root, "run-real-zoo.json")
    candidates = zoo["stages"]["plan"]["params"]["candidates"]
    assert sorted(c["path"] for c in candidates) == sorted(REAL)
    assert {"pipeline.spx", "pipeline.vix", "pipeline.market", "pipeline.backtest",
            "walkforward"} <= set(zoo["stages"]["plan"]["params"]["contract_paths"])
    assert zoo["stages"]["approval"]["params"]["approved_by"] == "PENDING-PLAN-REVIEW"
    PipelineDocument.from_obj(zoo)


def test_real_rungs_mirror_the_synthetic_rungs(child_root):
    base = _load(child_root, REAL[0])
    for name in REAL[1:]:
        rung = _load(child_root, name)
        stripped = [copy.deepcopy(d) for d in (base, rung)]
        for d in stripped:
            for key in ("name", "notes"):
                d.pop(key)
            d["pipeline"].pop("model")
        assert stripped[0] == stripped[1], name
    for kind in ("distribution", "har", "lightgbm"):
        real, synth = (_load(child_root, f"run-{t}-{kind}.json") for t in ("real", "synthetic"))
        assert real["pipeline"]["model"] == synth["pipeline"]["model"]
        for key in ("score", "condor"):
            assert real["pipeline"][key] == synth["pipeline"][key]


def test_real_config_agreements(child_root):
    doc = _load(child_root, REAL[0])
    pipe = doc["pipeline"]
    horizon = pipe["labels"]["params"]["horizon"]
    backtest = pipe["backtest"]["params"]
    assert backtest["hold_steps"] == horizon == pipe["fwd"]["params"]["horizon"]
    for key in ("rv", "labels", "fwd"):
        assert {"date", "iv_index", "close"} <= set(pipe[key]["params"]["carry_fields"])
    # 21 trading days can span ~31 calendar days (more around closures)
    assert doc["walkforward"]["embargo_days"] >= 35
    assert backtest["split"] == pipe["score"]["params"]["split"] != \
        pipe["model"]["params"]["fit_split"]
    assert pipe["market"]["inputs"] == {"records": "$spx.records", "iv_index": "$vix.by_date"}


def test_source_configs_check_against_the_cboe_pack(child_root):
    cboe = pytest.importorskip("dskit.onboarding.libs.cboe")
    from dskit.onboarding.connector import check_config

    for name in ("source-cboe-index.json", "source-cboe-chain.json"):
        check_config(cboe.CboeConnector(), _load(child_root, name))


def test_real_har_rung_runs_over_a_scripted_store(child_root, store_factory, tmp_path):
    rows = _index_rows(700, missing_vix=set(range(400, 420)))
    store = store_factory({"index_daily": rows}, source="cboe-index",
                          effective_field="effective_date")
    store.acquire("index_daily")
    obj = _load(child_root, "run-real-har.json")
    for key in ("spx", "vix"):
        obj["pipeline"][key]["params"]["root"] = store.root.root
    obj["walkforward"].update(first=_day(560), step_days=60, count=2, val_days=60)
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    result = run_walk_forward(PipelineDocument.from_obj(obj), asof=_day(699))
    assert len(result.folds) == 2 and all(f["state"] == "ran" for f in result.folds)
    carry = json.loads((tmp_path / "runs" / result.folds[0]["run_dir"] / "carry.json").read_text())
    metrics = carry["backtest"]["metrics"]
    assert metrics["n_entries"] >= 2 and metrics["always_n_trades"] >= 1
    assert "model_total_pnl_usd" in metrics and "implied_cvar_usd" in metrics
