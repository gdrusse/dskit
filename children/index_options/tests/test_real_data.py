"""ADR-0182 real-data track: index reader, condor backtest, configs.

Black-76 and the smile quote model are dskit's (``dskit.pipeline.option_pricing``)
and tested in ``tests/pipeline/test_option_pricing.py``; this file keeps only
the child's use of them (the configs' calibration, the backtest's prices).
"""

import copy
import json
import math
import random
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import PipelineDocument, load_document
from dskit.pipeline.driver import resolve_json_artifact, run_walk_forward
from dskit.pipeline.kinds_flow import Join, KeyBy
from dskit.pipeline.option_pricing import VolIndexSmileQuotes, black76
from dskit.pipeline.planner import plan

from index_options.nodes import CondorBacktest
from index_options.observations import IndexCloseRows

YEARS = 21 / 252
REAL = ("run-real-distribution.json", "run-real-har.json", "run-real-lightgbm.json",
        "run-real-vix.json", "run-real-har-vix.json", "run-real-lightgbm-vix.json")


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


def test_index_projection_copies_a_dividend_and_refuses_a_split(tmp_path):
    """ADR-0187: the optionshist ``index_daily`` rows carry the two corporate-action columns."""
    node = IndexCloseRows("spy", {"root": str(tmp_path), "source": "optionshist-chain",
                                  "symbol": "SPY"})
    raw = [{"symbol": "SPY", "date": "2020-03-16", "close": 239.85, "asof_ms": 1,
            "dividend_amount": 0.0, "split_coefficient": 1.0},
           {"symbol": "SPY", "date": "2020-03-20", "close": 228.8, "asof_ms": 2,
            "dividend_amount": 1.406, "split_coefficient": 1.0},
           {"symbol": "SPY", "date": "2020-03-23", "close": 222.95, "asof_ms": 3,
            "dividend_amount": None, "split_coefficient": None}]
    out = node.project(raw)
    assert [r["dividend_amount"] for r in out] == [0.0, 1.406, None]
    assert all("split_coefficient" not in r for r in out)
    assert set(out[0]) == {"instrument", "contract", "group", "close", "asof_ms", "date",
                           "dividend_amount"}
    for coefficient in (2.0, 0.5):
        with pytest.raises(ValueError, match="split_coefficient"):
            node.project([dict(raw[0], split_coefficient=coefficient)])
    for dividend in (-1.0, "1.4", True):
        with pytest.raises(ValueError, match="dividend_amount"):
            node.project([dict(raw[0], dividend_amount=dividend)])
    # the dividend is copied whether or not the row carries a split, and the close is a float
    bare = node.project([{"symbol": "SPY", "date": "2020-03-24", "close": 240, "asof_ms": 4,
                          "dividend_amount": 0.5}])
    assert bare[0]["dividend_amount"] == 0.5
    assert bare[0]["close"] == 240.0 and isinstance(bare[0]["close"], float)


def test_index_reader_and_join_over_a_real_onboarding_store(store_factory):
    rows = _index_rows(6, missing_vix={1})
    store = store_factory({"index_daily": rows}, source="cboe-index",
                          effective_field="effective_date")
    assert store.acquire("index_daily")["records"] == 11
    spx = IndexCloseRows("spx", store.node_params(symbol="SPX")).run(None, {})
    vix = IndexCloseRows("vix", store.node_params(symbol="VIX")).run(None, {})
    assert [r["date"] for r in spx["records"]] == [_day(i) for i in range(6)]
    assert all(type(r["asof_ms"]) is int for r in spx["records"])
    assert set(IndexCloseRows.outputs) == {"records"}  # keying is dskit's keyby now
    by_date = KeyBy("vix_by_date", {"key": "date", "value": "close"}).run(
        SimpleNamespace(), {"records": vix["records"]})["table"]
    assert _day(1) not in by_date and len(by_date) == 5
    joined = Join("market", {"key": "date", "how": "left",
                             "unmatched_fill": {"iv_index": None}}).run(
        None, {"records": spx["records"], "iv_index": by_date})["records"]
    assert [r["iv_index"] for r in joined][:2] == [by_date[_day(0)], None]
    assert {k for r in joined for k in r} == {"instrument", "contract", "group", "close",
                                              "asof_ms", "date", "iv_index"}


# -- pricing ------------------------------------------------------------------------------


#: (atm_ratio, put_skew_per_z, call_skew_per_z, smile_curvature) for the proxy tests.
SMILE = (0.8, 0.2, -0.05, 0.04)
#: The 2026-09-23 SPXW chain: 30 DTE, VIX 14.21, forward ~7795.8.
CHAIN_VIX, CHAIN_FORWARD, CHAIN_YEARS = 14.21, 7795.8, 30 / 365


def _smile_iv(z, vix=20.0, atm_ratio=0.8, put=0.2, call=-0.05, curvature=0.04):
    """The proxy IV written out independently of ``VolIndexSmileQuotes``."""
    wing = put * -z if z < 0 else call * z
    return vix / 100 * (atm_ratio + wing + curvature * z ** 2)


def _strike(z, forward=1000.0, vix=20.0, years=YEARS):
    return forward * math.exp(z * vix / 100 * math.sqrt(years))


def test_config_smile_matches_the_recorded_chain(child_root):
    params = _load(child_root, REAL[0])["pipeline"]["backtest"]["params"]
    quotes = VolIndexSmileQuotes(*(params[k] for k in VolIndexSmileQuotes.KNOBS))
    assert params["iv_ceiling"] == 2.0
    for z, real in ((-2.0, 0.190), (1.0, 0.105)):
        strike = _strike(z, CHAIN_FORWARD, CHAIN_VIX, CHAIN_YEARS)
        iv = quotes.iv(CHAIN_FORWARD, strike, CHAIN_VIX, CHAIN_YEARS)
        assert abs(iv - real) < 0.01, (z, iv)


# -- CondorBacktest -----------------------------------------------------------------------

#: Draws whose 10%/90% lower quantiles are exactly -1 and +1 (index ceil(q n) - 1).
DRAWS = [-1.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]
BT = {"split": "val", "hold_steps": 3, "short_q": 0.1, "wing_points": 25,
      "strike_increment": 5, "multiplier": 100, "fee_per_leg": 0.65,
      **dict(zip(("atm_ratio", "put_skew_per_z", "call_skew_per_z", "smile_curvature"), SMILE)),
      "iv_floor": 0.05, "iv_ceiling": 2.0, "half_spread_min": 0.0, "half_spread_frac": 0.0,
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
    # each leg at its own smile IV (VIX 20, z in VIX/100 sqrt(T) units); zero spread
    ivs = [_smile_iv(math.log(s / 1000.0) / (0.2 * math.sqrt(YEARS))) for s in strikes]
    mids = [black76(r, 1000.0, s, iv, YEARS) for r, s, iv in
            zip(("put", "put", "call", "call"), strikes, ivs)]
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
    {"min_edge_usd": float("nan")}, {"cvar_alpha": 1.0}, {"put_skew_per_z": -1},
    {"atm_ratio": 0}, {"call_skew_per_z": float("nan")}, {"smile_curvature": -1},
    {"skew_per_z": 0.1},  # the pre-smile name is gone, no alias
    {"iv_floor": 0}, {"half_spread_frac": 1}, {"rate": "0"}, {"surprise": 1},
    {"iv_ceiling": 0.05},  # not above the floor
    {"call_skew_per_z": -1.0},  # the call wing would dip below zero before the floor
])
def test_backtest_knob_refusal(change):
    with pytest.raises(ConfigError):
        CondorBacktest("bt", {**BT, **change})


def test_backtest_requires_wings_and_proxy_knobs():
    for missing in ("wing_points", "atm_ratio", "put_skew_per_z", "call_skew_per_z",
                    "smile_curvature", "iv_ceiling", "multiplier", "split"):
        with pytest.raises(ConfigError):
            CondorBacktest("bt", {k: v for k, v in BT.items() if k != missing})


# -- configs ------------------------------------------------------------------------------


def _load(child_root, name):
    return json.loads((child_root / "configs" / name).read_text())


@pytest.mark.parametrize("name", REAL)
def test_real_configs_plan(child_root, monkeypatch, name):
    monkeypatch.chdir(child_root)
    planned = plan(load_document(str(child_root / "configs" / name)))
    assert {"spx", "vix", "vix_by_date", "market", "rv", "labels", "fwd", "model", "score",
            "condor", "backtest"} == set(planned.order)


def test_real_zoo_plans_and_lists_every_real_rung(child_root, monkeypatch):
    monkeypatch.chdir(child_root)
    zoo = _load(child_root, "run-real-zoo.json")
    candidates = zoo["stages"]["plan"]["params"]["candidates"]
    assert sorted(c["path"] for c in candidates) == sorted(REAL)
    assert {"pipeline.spx", "pipeline.vix", "pipeline.vix_by_date", "pipeline.market",
            "pipeline.backtest", "walkforward"} <= set(zoo["stages"]["plan"]["params"]["contract_paths"])
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
    assert pipe["market"]["inputs"] == {"records": "$spx.records",
                                        "iv_index": "$vix_by_date.table"}
    assert pipe["vix_by_date"] == {
        "uses": "dskit.pipeline.kinds_flow:KeyBy", "inputs": {"records": "$vix.records"},
        "params": {"key": "date", "value": "close"}, "notes": pipe["vix_by_date"]["notes"]}


def test_source_configs_check_against_the_cboe_pack(child_root):
    cboe = pytest.importorskip("dskit.onboarding.libs.cboe")
    from dskit.onboarding.connector import check_config

    for name in ("source-cboe-index.json", "source-cboe-chain.json"):
        check_config(cboe.CboeConnector(), _load(child_root, name))


def test_archive_source_config_pins_the_whole_mirror(child_root):
    """The options-dataset-hist source: the pack's own gate, and a pin per published file."""
    hist = pytest.importorskip("dskit.onboarding.libs.optionshist")
    from dskit.onboarding.connector import check_config

    config = _load(child_root, "source-optionshist-chain.json")
    check_config(hist.OptionsHistConnector(), config)
    knobs = hist.OptionsHistConnector().resolve_knobs(
        {k: v for k, v in config.items() if k not in ("notes", "storage")})
    # restated from the mirror's tree at the pinned commit, not read from the pack
    assert knobs["source_url"] == "https://github.com/anahatsingh-ui/options-dataset-hist"
    assert knobs["source_commit"] == "37f6c456fe1a4775c875673fb8ef907d5cd2fd66"
    assert knobs["symbols"] == ["SPY", "QQQ", "IWM"]
    first = {"spy": 2008, "qqq": 2011, "iwm": 2008}
    expected = {f"{sub}/options_{year}.parquet"
                for sub, start in first.items() for year in range(start, 2026)}
    expected |= {f"{sub}/underlying_prices.parquet" for sub in first}
    assert set(knobs["files"]) == expected and len(expected) == 54
    assert len(set(knobs["files"].values())) == 54  # no two files share a digest
    assert isinstance(knobs["max_days"], int) and 1 <= knobs["max_days"] <= 126
    assert config["storage"] == {"payload_codec": "gzip", "observations_codec": "gzip"}


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


# -- ADR-0187: one grid cell end to end over a scripted archive store ----------------------


def _archive_rows(n, seed=5):
    """SPY closes with a quarterly dividend, and one chain per day expiring 7 days out."""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    rng = random.Random(seed)
    closes, chain, spot = [], [], 300.0
    for i in range(n):
        spot *= math.exp(rng.gauss(0.0, 0.012))
        day = _day(i)
        closes.append({"symbol": "SPY", "date": day, "open": spot, "high": spot, "low": spot,
                       "close": round(spot, 2), "dividend_amount": 1.5 if i % 63 == 40 else 0.0,
                       "split_coefficient": 1.0, "effective_date": day})
        expiry = (date.fromisoformat(day) + timedelta(days=7)).isoformat()
        quote_time = datetime.combine(date.fromisoformat(day), time(16, 0),
                                      tzinfo=ZoneInfo("America/New_York")).astimezone(
            timezone.utc).isoformat()
        close = round(spot, 2)
        for k in range(int(close * 0.9), int(close * 1.1) + 1):
            for right in ("put", "call"):
                distance = (close - k) if right == "put" else (k - close)
                mid = max(0.05, 4.0 * math.exp(-max(distance, 0.0) / 6.0))
                occ = f"SPY{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{right[0].upper()}{k * 1000:08d}"
                chain.append({"underlying": "SPY", "option": occ, "root": "SPY",
                              "expiry": expiry, "right": right, "strike": float(k),
                              "bid": round(mid * 0.95, 2), "bid_size": 10,
                              "ask": round(mid * 1.05, 2), "ask_size": 10, "iv": 0.2,
                              "open_interest": 1, "volume": 1, "delta": None, "gamma": None,
                              "vega": None, "theta": None, "last_trade_price": None,
                              "last_trade_time": None, "underlying_price": close,
                              "quote_time": quote_time, "effective_date": quote_time})
    return closes, chain


def test_a_grid_cell_runs_its_walk_over_a_scripted_archive(child_root, store_factory, tmp_path,
                                                          monkeypatch):
    import dskit.onboarding.observations as seam

    closes, chain = _archive_rows(420)
    archive = store_factory({"index_daily": closes, "option_chain": chain}, "archive",
                            source="optionshist-chain", effective_field="effective_date")
    archive.acquire("index_daily")
    archive.acquire("option_chain")
    vix = store_factory({"index_daily": [r for r in _index_rows(420) if r["symbol"] == "VIX"]},
                        "vix", source="cboe-index", effective_field="effective_date")
    vix.acquire("index_daily")
    obj = _load(child_root, "grid/spy-7-10.json")
    for key in ("underlying", "chain"):
        obj["pipeline"][key]["params"]["root"] = archive.root.root
    obj["pipeline"]["underlying"]["params"]["since_ms"] = 0
    obj["pipeline"]["vix"]["params"]["root"] = vix.root.root
    obj["walkforward"].update(first=_day(300), step_days=45, count=2, val_days=45)
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    real, scans = seam.scan_stream, []

    def counting(*args, **kwargs):
        scans.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr(seam, "scan_stream", counting)
    result = run_walk_forward(PipelineDocument.from_obj(obj), asof=_day(419))
    assert len(result.folds) == 2 and all(f["state"] == "ran" for f in result.folds)
    # the chain is parsed once per process, the closes once per reader per fold
    assert scans.count("option_chain") == 1 and scans.count("index_daily") == 4
    for fold in result.folds:
        carry = json.loads((tmp_path / "runs" / fold["run_dir"] / "carry.json").read_text())
        metrics = carry["backtest"]["metrics"]
        assert metrics["n_entries"] >= 3 and metrics["always_n_trades"] >= 1
        assert metrics["n_skipped_no_chain"] == 0 and carry["score"]["metrics"]["n"] > 0
        report = resolve_json_artifact(str(tmp_path / "runs" / fold["run_dir"]),
                                       carry["backtest"]["report"])
        assert report["kind"] == "archived_quote_condor_backtest"
        entry = report["ledger"][0]
        assert entry["dte"] == 7 and entry["sessions"] == 5
        assert entry["settlement_date"] == entry["settle_date"]
