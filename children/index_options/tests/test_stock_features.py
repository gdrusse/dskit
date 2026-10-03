"""The stock feature tables: both templates end to end on a tiny real store, no network.

The manifest ``configs/workflow-features.json`` is run by the production runner over an
onboarding store built here (bars and a benchmark as ``localblobs`` parquet, option-trade bars as a
``localfiles`` stream). Expected values are restated from the fixture's own arithmetic, never read
back from the nodes under test.
"""

import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.pipeline.libs.observation_tables import ObservationTables
from dskit.pipeline.workflow import expand

CHILD = Path(__file__).resolve().parents[1]
CONFIGS = CHILD / "configs"
MANIFEST = json.loads((CONFIGS / "workflow-features.json").read_text(encoding="utf-8"))
ARGS = MANIFEST["args"]

TICKERS = ("AAA", "BBB")
N_DAYS = 30
VOL_WINDOW = 3
BETA_WINDOW = 5
CUT = 20                      # leak test: bars and trades from this day on are altered
EXPIRY = "2024-12-20"
SPLIT_DAY = 15                # AAA does a 10:1 split on this day: stored (split-adjusted) history is rescaled
SPLIT_RATIO = 10.0


def _day(i):
    return (date(2024, 1, 1) + timedelta(days=i)).isoformat()      # consecutive, 1 Jan 2024 is a Monday


DAYS = [_day(i) for i in range(N_DAYS)]


def _close(sym, i):
    return {"AAA": 100.0 + i + 0.3 * math.sin(i), "BBB": 50.0 + 0.5 * i, "MKT": 400.0 + 2 * i + math.cos(i)}[sym]


def _volume(sym, i):
    return float({"AAA": 1000 + 10 * i, "BBB": 500 + 7 * i}[sym])


def _factor(sym, i):
    """Adjustment of the STORED close/volume at day i: pre-split history of AAA is divided/multiplied."""
    return SPLIT_RATIO if sym == "AAA" and i < SPLIT_DAY else 1.0


def _stored_close(sym, i):
    return _close(sym, i) / _factor(sym, i)


def _stored_volume(sym, i):
    return _volume(sym, i) * _factor(sym, i)


def _call_size(i):
    return float(i + 1)


PUT_SIZE = 2.0


def _bar_rows(sym, i, bump):
    """Trade bars of one symbol-day: a call and a put at strike 100, one extra call at 110."""
    base = {"symbol": sym, "quote_date": _day(i), "expiry": EXPIRY}
    factor = 1.0 + bump
    return [
        {**base, "contract": f"{sym}C100", "type": "call", "strike": 100.0, "volume": _call_size(i) * factor, "vwap": 5.0},
        {**base, "contract": f"{sym}P100", "type": "put", "strike": 100.0, "volume": PUT_SIZE * factor, "vwap": 4.0},
        {**base, "contract": f"{sym}C110", "type": "call", "strike": 110.0, "volume": 1.0 * factor, "vwap": 2.0},
    ]


def _build_store(tmp_path, name, bump_from=None):
    """A store with bars, benchmark and option bars; days from ``bump_from`` on are altered."""
    root = OnboardingRoot.create(str(tmp_path / name / "ob"))
    registry = root.registry()
    data = tmp_path / name / "data"

    def altered(i):
        return bump_from is not None and i >= bump_from

    def parquet(directory, sym, key, tag):
        rows = [{"symbol": sym, "date": DAYS[i], "close": _stored_close(tag, i) * (1.5 if altered(i) else 1.0),
                 "volume": int(_stored_volume(tag, i) * (3 if altered(i) else 1)) if tag != "MKT" else 1,
                 "split_coefficient": SPLIT_RATIO if (tag == "AAA" and i == SPLIT_DAY) else 1.0}
                for i in range(N_DAYS)]
        (directory / key).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(directory / key)

    for sym in TICKERS:
        parquet(data / "bars", sym, f"{sym.lower()}/underlying_prices.parquet", sym)
    parquet(data / "bench", "MKT", "mkt/underlying_prices.parquet", "MKT")
    options = data / "options"
    options.mkdir(parents=True)
    with open(options / "bars.jsonl", "w", encoding="utf-8") as fh:
        for sym in TICKERS:
            for i in range(N_DAYS):
                for row in _bar_rows(sym, i, 0.5 if altered(i) else 0.0):
                    fh.write(json.dumps(row) + "\n")
    for src, connector, config in (
            ("bars", "localblobs", {"path": str(data / "bars"), "as_of": "2026-01-01T00:00:00+00:00"}),
            ("bench", "localblobs", {"path": str(data / "bench"), "as_of": "2026-01-01T00:00:00+00:00"}),
            ("options", "localfiles", {"path": str(options), "effective_field": "quote_date"})):
        vid = registry.register("source_config", {
            "name": src, "catalog_source": f"{src}-src", "connector": connector, "config": config},
            origin="test")
        registry.transition(vid, "active", origin="test")
        run_acquisition(root, registry, src, "bars" if src == "options" else "files", "backfill")
    return root.root


def _overlay(store, work):
    return {
        "work_dir": str(work), "tickers": list(TICKERS), "A": "2026-01-01",
        "bars": {"root": store, "source": "bars",
                 "relpath": {s: f"{s.lower()}/underlying_prices.parquet" for s in TICKERS}},
        "benchmark": {"symbol": "MKT", "root": store, "source": "bench",
                      "relpath": {"MKT": "mkt/underlying_prices.parquet"}},
        "daily_features": {"volume_liquidity": {"window": VOL_WINDOW}, "market_relative": {
            "windows": [1, 5], "beta_window": BETA_WINDOW}},
        "options": {"root": store, "keep_values": {"symbol": list(TICKERS)}},
        "trade_features": {"moneyness_band": 0.2},
        "targets": {"root": store},
    }


def _run(tmp_path, name, bump_from=None):
    """Run both steps on a fresh store; return {table: [rows]} and the files' sha256."""
    store = _build_store(tmp_path, name, bump_from)
    overlay = tmp_path / name / "overlay.json"
    overlay.write_text(json.dumps(_overlay(store, tmp_path / name / "work")))
    run = subprocess.run([sys.executable, "-m", "dskit.pipeline", "workflow",
                          str(CONFIGS / "workflow-features.json"), "--args", str(overlay)],
                         capture_output=True, text=True, cwd=CHILD)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    out, digests = {}, {}
    for step, stream in (("daily", ARGS["tables"]["daily"]), ("trades", ARGS["tables"]["trades"])):
        path = tmp_path / name / "work" / step / f"{stream}.jsonl"
        out[step] = [json.loads(line) for line in path.read_text().splitlines()]
        digests[step] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out, digests, store


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("sf"), "base")


def _index(rows):
    return {(r["symbol"], r["quote_date"]): r for r in rows}


# -- values ------------------------------------------------------------------------------------


def _returns(sym, i, n):
    """The n one-bar log returns ending at day i, on the stored basis."""
    return [math.log(_stored_close(sym, j) / _stored_close(sym, j - 1)) for j in range(i - n + 1, i + 1)]


def test_daily_values_match_hand_arithmetic(built):
    rows = _index(built[0]["daily"])
    vl, mr = ARGS["daily_features"]["volume_liquidity"]["fields"], ARGS["daily_features"]["market_relative"]
    assert len(rows) == len(TICKERS) * N_DAYS
    i = 25                                    # after the split: every window is on one basis
    for sym in TICKERS:
        row = rows[(sym, _day(i))]
        vol, close = _stored_volume(sym, i), _stored_close(sym, i)
        assert row[vl["log_volume"]] == pytest.approx(math.log(vol))
        assert row[vl["log_dollar_volume"]] == pytest.approx(math.log(close * vol))
        mean = sum(_stored_volume(sym, j) for j in range(i - VOL_WINDOW + 1, i + 1)) / VOL_WINDOW
        assert row[vl["log_volume_ratio"]] == pytest.approx(math.log(vol / mean))
        amihud = sum(abs(r) / (_stored_close(sym, j) * _stored_volume(sym, j))
                     for r, j in zip(_returns(sym, i, VOL_WINDOW), range(i - VOL_WINDOW + 1, i + 1))) / VOL_WINDOW
        assert row[vl["amihud"]] == pytest.approx(amihud)
        own, mkt = _returns(sym, i, 1)[0], _returns("MKT", i, 1)[0]
        assert row[mr["relative_field"].replace("{window}", "1")] == pytest.approx(own - mkt)
        own5 = math.log(_stored_close(sym, i) / _stored_close(sym, i - 5))
        mkt5 = math.log(_stored_close("MKT", i) / _stored_close("MKT", i - 5))
        assert row[mr["relative_field"].replace("{window}", "5")] == pytest.approx(own5 - mkt5)
        x, y = _returns("MKT", i, BETA_WINDOW), _returns(sym, i, BETA_WINDOW)
        mx, my = sum(x) / BETA_WINDOW, sum(y) / BETA_WINDOW
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
        varx, vary = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
        assert row[mr["beta_field"]] == pytest.approx(cov / varx)
        assert row[mr["corr_field"]] == pytest.approx(cov / math.sqrt(varx * vary))
        assert rows[(sym, _day(0))][vl["log_volume_ratio"]] is None      # window not yet full: null, not filled
        assert rows[(sym, _day(2))][vl["log_volume_ratio"]] is not None


def test_stored_volume_level_shifts_across_a_split_but_dollar_volume_does_not(built):
    """The documented hindsight effect: pre-split log volume is shifted by ln(ratio); dollar volume is invariant."""
    rows = _index(built[0]["daily"])
    vl = ARGS["daily_features"]["volume_liquidity"]["fields"]
    pre = rows[("AAA", _day(SPLIT_DAY - 1))]
    assert pre[vl["log_volume"]] == pytest.approx(math.log(_volume("AAA", SPLIT_DAY - 1) * SPLIT_RATIO))
    assert pre[vl["log_dollar_volume"]] == pytest.approx(
        math.log(_close("AAA", SPLIT_DAY - 1) * _volume("AAA", SPLIT_DAY - 1)))


def test_trade_rows_carry_the_previous_days_trades(built):
    """The row dated t holds t-1's trades (strict prior): put share = 2 / (call + put volume of t-1)."""
    rows = _index(built[0]["trades"])
    name = ARGS["trade_features"]["fields"]["put_share"]
    for sym in TICKERS:
        for i in (1, 7, 19):
            calls = _call_size(i - 1) + 1.0
            assert rows[(sym, _day(i))][name] == pytest.approx(PUT_SIZE / (calls + PUT_SIZE))
        assert (sym, _day(0)) not in rows             # nothing before day 0 to carry


def test_trade_features_hold_their_values_across_a_split(built):
    """Strikes are as traded: AAA's pre-split rows (stored close / 10) still get moneyness and a straddle."""
    rows = _index(built[0]["trades"])
    f = ARGS["trade_features"]["fields"]
    i = SPLIT_DAY - 6                         # trade day, well before the split; the row is dated i + 1
    row = rows[("AAA", _day(i + 1))]
    close = _close("AAA", i)                  # as traded
    calls, put = _call_size(i) + 1.0, PUT_SIZE
    assert row[f["n_strikes"]] == 2
    assert row[f["log_call_put_ratio"]] == pytest.approx(math.log(calls / put))
    weighted = (_call_size(i) * math.log(100 / close) + put * math.log(100 / close) + 1.0 * math.log(110 / close))
    assert row[f["vw_log_moneyness"]] == pytest.approx(weighted / (calls + put))
    assert row[f["atm_straddle_ratio"]] == pytest.approx((5.0 + 4.0) / close)       # non-null across the split
    assert row[f["log_total_volume"]] == pytest.approx(math.log(calls + put))
    assert row[f["log_trade_count"]] == pytest.approx(math.log(3))


def test_no_straddle_when_the_band_excludes_both_strikes(built):
    rows = _index(built[0]["trades"])
    name = ARGS["trade_features"]["fields"]["atm_straddle_ratio"]
    row = rows[("BBB", _day(5))]       # BBB close ~52: |ln(100/52)| > the 0.2 band, so no straddle
    assert row[name] is None


# -- leak and determinism ----------------------------------------------------------------------


def test_nothing_before_the_cut_moves_when_later_data_changes(tmp_path, built):
    """Pipeline-level leak test: alter bars and trades from day CUT on; rows dated <= CUT are unchanged
    (daily rows at CUT use CUT's own bar, so only strictly earlier daily dates are compared)."""
    changed = _run(tmp_path, "bumped", bump_from=CUT)[0]
    for step, last_kept in (("daily", CUT - 1), ("trades", CUT)):
        before = {k: v for k, v in _index(built[0][step]).items() if k[1] <= _day(last_kept)}
        after = {k: v for k, v in _index(changed[step]).items() if k[1] <= _day(last_kept)}
        assert before == after and before
    later = _index(changed["daily"])[("AAA", _day(CUT + 2))]
    assert later != _index(built[0]["daily"])[("AAA", _day(CUT + 2))]       # the alteration is real


def test_the_pipeline_is_deterministic(tmp_path, built):
    again = _run(tmp_path, "again")
    assert again[1] == built[1]


# -- the keyed-table contract -------------------------------------------------------------------


def test_the_tables_attach_through_observation_tables(tmp_path, built):
    """Each table, onboarded by its step's after commands, attaches same-day by (symbol, quote_date)."""
    store = built[2]
    name_vol = ARGS["daily_features"]["volume_liquidity"]["fields"]["log_volume"]
    name_put = ARGS["trade_features"]["fields"]["put_share"]
    node = ObservationTables("keyed", {
        "root": store, "key": [ARGS["schema"]["entity"], ARGS["schema"]["date"]],
        "tables": {
            "vol": {"source": ARGS["targets"]["daily"], "stream": ARGS["tables"]["daily"], "columns": {name_vol: name_vol},
                    "strict_prior": False, "max_age_days": 4},
            "opt": {"source": ARGS["targets"]["trades"], "stream": ARGS["tables"]["trades"], "columns": {name_put: name_put},
                    "strict_prior": False, "max_age_days": 4}}})
    entity, day = ARGS["schema"]["entity"], ARGS["schema"]["date"]
    probe = [{entity: "AAA", day: _day(8)}]
    got = node.run(None, {"records": probe})["records"][0]
    assert got[name_vol] == pytest.approx(math.log(_stored_volume("AAA", 8)))
    assert got[name_put] == pytest.approx(PUT_SIZE / (_call_size(7) + 1.0 + PUT_SIZE))
    assert got[name_put + "_missing"] in (0, False)


# -- the config carries no project value -------------------------------------------------------

HOLE = re.compile(r"\$(\$|\{([A-Za-z_]\w*(?:\.\w+)*)\})")
PROSE = ("notes", "provenance_waiver", "source")


def _strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in PROSE:
                yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


@pytest.mark.parametrize("name", ["features-stock-daily.json", "features-stock-option-trades.json"])
def test_no_project_literal_survives_in_a_template(name):
    template = json.loads((CONFIGS / "templates" / name).read_text())
    forbidden = (set(TICKERS) | set(ARGS["tickers"]) | {ARGS["benchmark"]["symbol"]}
                 | set(ARGS["schema"].values()) | set(ARGS["options"]["fields"].values())
                 | set(ARGS["daily_features"]["volume_liquidity"]["fields"].values())
                 | set(ARGS["trade_features"]["fields"].values()) | set(ARGS["tables"].values()))
    for text in _strings(template):
        bare = HOLE.sub("", text).replace("$each", "")
        assert not (set(re.findall(r"[A-Za-z0-9_-]+", bare)) & forbidden), text


def test_the_manifest_agrees_with_itself():
    """Pinned duplicates: key names, and the entity list wherever it is restated."""
    assert ARGS["options"]["fields"]["entity"] == ARGS["schema"]["entity"]
    assert ARGS["options"]["fields"]["date"] == ARGS["schema"]["date"]
    assert ARGS["bars"]["fields"]["date"] == ARGS["schema"]["date"]
    assert set(ARGS["bars"]["relpath"]) == set(ARGS["tickers"])
    assert set(ARGS["options"]["keep_values"][ARGS["options"]["fields"]["entity"]]) == set(ARGS["tickers"])
    assert set(ARGS["options"]["projection"]) >= set(ARGS["options"]["fields"].values())
    assert set(ARGS["bars"]["columns"].values()) == set(ARGS["bars"]["fields"].values())
    assert set(ARGS["benchmark"]["columns"].values()) == set(ARGS["benchmark"]["fields"].values())


def test_every_declared_output_name_is_unique():
    names = list(ARGS["daily_features"]["volume_liquidity"]["fields"].values()) + [
        ARGS["daily_features"]["market_relative"]["relative_field"].replace("{window}", str(w))
        for w in ARGS["daily_features"]["market_relative"]["windows"]] + [
        ARGS["daily_features"]["market_relative"][k] for k in ("beta_field", "corr_field")] + list(
        ARGS["trade_features"]["fields"].values())
    assert len(names) == len(set(names))


def test_the_clock_note_is_dropped_when_strict(tmp_path):
    out = expand(json.loads((CONFIGS / "templates" / "features-stock-option-trades.json").read_text()),
                 _expansion_values())
    assert "clock_note" not in out["pipeline"]["features"]["params"]
    assert out["pipeline"]["features"]["params"]["strict_prior"] is True


def _expansion_values():
    values = copy.deepcopy({k: ARGS[k] for k in ("options", "bars", "schema")})
    return {"T": ARGS["tickers"], "B": values["bars"], "O": values["options"], "Q": values["schema"],
            "F": ARGS["trade_features"], "R": ARGS["as_traded"], "W": "w", "L": {"trades": {"table": "t.jsonl"}}}
