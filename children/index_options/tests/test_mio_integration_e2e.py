"""ADR-0313 stage I8: the integrated MIO end to end on a tiny SYNTHETIC world (no real data, nothing dated >= 2021-07-01).

The REAL walk child writes the maps and the summary; one study then solves a base twin, a warm-up control, a
calibrated arm, a hedged-program arm, a combined arm and an exposure-limited arm; each result is replayed, read by
the Greeks diagnostics and by the hedge-evidence reads. What is synthetic and why:

* the world is ``edge_calibration_world.World`` (one name ``AAA``, 2019-08 to 2020-01), generated in memory;
* the walk's pooling is ``band two_block, min_blocks 2, min_dates 2, min_clusters 2`` (the production defaults need
  years); with 13 decision weeks the line WARMS on 2019-10-28, so the study's 3 first weeks are warm-up and 10 are warm;
* a tiny walk finds no edge (its fitted weights are 0) and cannot pass the gated reads, so the maps' weights are set
  to fixed values and the summary's read verdicts to PASS, in a COPY; the warm flags, regimes, buckets, calendar,
  direction params and every other key are the walk's own.
"""

import copy
import json
import re
import time

import pytest

from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL
from index_options import ledger_studies
from test_mio_calibration import Harness, untimed
from test_mio_hedge_calibration import (DIRECTION, build, combined, digest, hedged, limited)

pytestmark = pytest.mark.slow
WEIGHTS = {"w": 0.5, "wd": 0.5, "ws": 0.25}
BUDGET_SECONDS = 180.0


def weighted(maps):
    """The walk's maps with synthetic weights; every warm flag and every other key stays the walk's."""
    out = copy.deepcopy(maps)
    for body in out.values():
        for state in body["states"].values():
            for rec in (*state["buckets"], state["pooled"]):
                rec["w"] = rec["w_lcb"] = WEIGHTS["w"]
        for rec in body["origin_two"]["buckets"]:
            rec["w"] = [WEIGHTS["wd"], WEIGHTS["ws"]]
    return out


def methods_of(world):
    base = world.base
    return {
        "twin_base": {**base, "id": "twin_base"},
        "twin_warm": world.method("twin_warm", form="identity", control=True),
        "cal_arm": world.method("cal_arm"),
        "twin_warm2": world.method("twin_warm2", form="identity", control=True, regressors=2),
        "hedge_arm": hedged(world, "hedge_arm"),
        "combined": combined(world, "combined"),
        "limit_arm": limited(world, "limit_arm"),
    }


class E2E:
    """The walk's output, one study over every method, and each method's result and trace."""

    def __init__(self, root):
        self.started = time.perf_counter()
        self.world = Harness(root, direction=DIRECTION)
        self.maps = weighted(self.world.maps)
        self.methods = methods_of(self.world)
        self.study = build(self.world, list(self.methods.values()), maps=self.maps)
        self.results = {name: self.study._method(m, None) for name, m in self.methods.items()}
        self.seconds = time.perf_counter() - self.started


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    return E2E(tmp_path_factory.mktemp("e2e"))


def test_everything_runs_and_every_arm_trades(e2e):
    for name, result in e2e.results.items():
        assert result["trades"], name
    assert any(t["hedge"]["units"] for t in e2e.results["hedge_arm"]["trades"])
    assert any(t["hedge"]["units"] for t in e2e.results["combined"]["trades"])
    assert e2e.results["cal_arm"]["calibration"]["weeks"] == {"warmup": 3, "warm": 10}
    assert e2e.results["combined"]["calibration"]["counts_as_evidence"] is True
    assert all(d["exposure"]["status"] == "within" for d in e2e.results["limit_arm"]["decisions"]
               if d["status"] in ("selected", "cash"))


def test_every_result_replays_to_itself(e2e):
    for name, method in e2e.methods.items():
        replay = e2e.study._method(method, None, recorded=e2e.results[name])
        assert digest(replay) == digest(e2e.results[name]), name


def test_twin_warm_is_the_calibrated_arm_except_for_the_value_term(e2e, monkeypatch):
    """Same weeks in cash, same candidates, same program in every coefficient; only ``value_adjust`` differs."""
    from test_mio_calibration import programs_of

    arm, control = e2e.methods["cal_arm"], e2e.methods["twin_warm"]
    a_result, a_programs = programs_of(monkeypatch, e2e.study, arm)
    c_result, c_programs = programs_of(monkeypatch, e2e.study, control)
    assert a_programs and len(a_programs) == len(c_programs)
    differing = 0
    for n, (a, c) in enumerate(zip(a_programs, c_programs)):
        for ga, gc in zip(a["groups"], c["groups"]):
            assert set(gc.pop("value_adjust")) == {0.0}
            differing += ga.pop("value_adjust") != [0.0] * len(gc["credit"])
        assert a["groups"] == c["groups"], "every coefficient of every group, week after week"
        if n == 0:
            assert a == c, "on the first warm week the account is the same, so the whole program is"
    assert differing, "the arm's value term is not the control's"
    assert [(d["date"], d["status"] == "calibration_warmup") for d in a_result["decisions"]] == \
        [(d["date"], d["status"] == "calibration_warmup") for d in c_result["decisions"]]
    assert a_result["calibration"]["control"] is False and c_result["calibration"]["control"] is True
    base = e2e.results["twin_base"]
    assert any(d["status"] == "selected" and d["date"] in {x["date"] for x in a_result["decisions"] if x["status"] == "calibration_warmup"}
               for d in base["decisions"]), "the unblocked twin WOULD trade in the warm-up weeks"


def poisoned(world, maps, after):
    """Inputs and maps with everything dated after ``after`` set to absurd values."""
    inputs = copy.deepcopy(world.inputs)
    for symbol, data in inputs.items():
        for row in data["chain"]:
            if row["date"] > after:
                row["bid"], row["ask"], row["underlying_price"] = 777.0, 778.0, 99999.0
        for row in data["bars"]:
            if row["date"] > after:
                row["close"] = 12345.0
        for kind in ("forecasts", "contexts"):
            for row in data[kind]:
                if row["quote_date"] > after:
                    for key in row:
                        if key.startswith("method_cdf_"):
                            row[key] = 0.5
    maps = copy.deepcopy(maps)
    for day, body in maps.items():
        if day > after:
            for state in body["states"].values():
                state["warm"] = True
                for rec in (*state["buckets"], state["pooled"]):
                    rec["w"] = rec["w_lcb"] = 0.99
            body["origin_two"]["warm"] = True
            for rec in body["origin_two"]["buckets"]:
                rec["w"] = [0.99, 0.99]
    return inputs, maps


def test_nothing_after_a_decision_reaches_it(e2e, monkeypatch):
    """Poison every quote, bar, forecast and map dated after week k: the decisions up to k do not move."""
    from test_mio_calibration import programs_of

    after = e2e.study.weeks[6]
    inputs, maps = poisoned(e2e.world, e2e.maps, after)
    # An absurd later market breaks the account's own end-of-run checks (nonpositive equity); the decisions are what is
    # read, so the run's final accounting is skipped for both sides.
    monkeypatch.setattr(ledger_studies.WeeklyOptionPortfolioStudy, "_finish",
                        lambda self, method, account, decisions, rejected, snapshots, hedge=None: {"decisions": decisions})
    for name in ("cal_arm", "combined", "limit_arm"):
        method = e2e.methods[name]
        clean_result, clean = programs_of(monkeypatch, e2e.study, method)
        study = build(e2e.world, list(e2e.methods.values()), maps=maps, inputs=inputs)
        monkeypatch.setattr(ledger_studies.WeeklyOptionPortfolioStudy, "_finish",
                            lambda self, method, account, decisions, rejected, snapshots, hedge=None: {"decisions": decisions})
        dirty_result, dirty = programs_of(monkeypatch, study, method)
        known = [d for d in clean_result["decisions"] if d["date"] <= after]
        assert known and any(d["status"] == "selected" for d in known), name
        for a, b in zip(known, [d for d in dirty_result["decisions"] if d["date"] <= after]):
            for key in ("date", "status", "books", "selection"):
                assert untimed(a.get(key)) == untimed(b.get(key)), (name, a["date"], key)
        used = sum(1 for d in known if "selection" in d)
        assert clean[:used] == dirty[:used], f"{name}: the programs handed to the solver up to {after} moved"
        later = lambda r: [d for d in r["decisions"] if d["date"] > after]                         # noqa: E731
        assert untimed(later(clean_result)) != untimed(later(dirty_result)), f"{name}: the poison did not bite"


def test_no_real_data_and_nothing_dated_at_or_after_the_seal(e2e):
    dates = set()
    for result in e2e.results.values():
        dates |= set(re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", json.dumps(result)))
    for data in e2e.world.inputs.values():
        for rows in data.values():
            dates |= {v for row in rows for k, v in row.items() if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)}
    assert dates and max(dates) < DEVELOPMENT_SEAL and min(dates) >= "2019-01-01"
    assert set(e2e.world.params["symbols"]) == {"AAA"} and e2e.world.summary["sealed_from"] <= DEVELOPMENT_SEAL
    assert all(e2e.results[n]["trades"][-1]["close_date"] < DEVELOPMENT_SEAL for n in e2e.results)


def test_the_whole_chain_runs_inside_the_time_budget(e2e):
    assert e2e.seconds < BUDGET_SECONDS, e2e.seconds


def test_hedge_evidence_reads_the_program_arm_against_its_unhedged_twin(e2e, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import hedge_evidence as he

    control, program = e2e.results["twin_base"], e2e.results["hedge_arm"]
    read = he.program_quarter_read("2019Q4", control, program)
    assert {"dDD", "dPnl", "B", "contracts_ratio", "re_levered", "counts"} <= set(read)
    registry = tmp_path / "attempts.jsonl"
    out = he.read_program({"2019Q4": control}, {"2019Q4": program}, "w1_010_shock10_lp", registry, n_boot=50)
    assert registry.exists() and out["development_evidence_only"] is True and out["program"]["verdict"] in ("pass", "fail", "insufficient")
    # a quarter with one counting account can never pass: fewer than three counting quarters is insufficient
    assert out["program"]["verdict"] == "insufficient"
    cut = {**program, "decisions": [{**program["decisions"][0], "decision_weeks": ["2019-10-07"]}], "decision_weeks": ["2019-10-07"]}
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        he.read_program({"2019Q4": control}, {"2019Q4": cut}, "w1_010_shock10_lp", tmp_path / "x.jsonl", n_boot=50)


def saved_run(e2e, root):
    """Write the study's results as a closed run (``result.json``, ``trades.csv``, ``daily-account.csv``, parquet pins)."""
    import hashlib
    from pathlib import Path

    import pandas as pd

    root = Path(root)
    base, run = root / "source", root / "run"
    base.mkdir()
    run.mkdir()

    def block(folder, frames):
        (base / folder).mkdir()
        files, digests = {}, {}
        for symbol, frame in frames.items():
            relative = f"{folder}/{symbol}.parquet"
            frame.to_parquet(base / relative, index=False)
            files[symbol] = relative
            digests[relative] = hashlib.sha256((base / relative).read_bytes()).hexdigest()
        return {"files": files, "pin": {"root": "/r", "source": "s", "snapshot": folder, "stream": "files",
                                        "manifest_sha256": "0" * 64, "files": digests}}

    params = e2e.world.params
    bars = {s: pd.DataFrame([{"symbol": s, "date": r["date"], "close": r["close"]} for r in d["bars"]])
            for s, d in e2e.world.inputs.items()}
    chains = {s: pd.DataFrame(d["chain"]) for s, d in e2e.world.inputs.items()}
    names = ("twin_base", "cal_arm", "limit_arm")
    table = {"complete": True,
             "plan": {"source_pins": {"bars": block("bars", bars), "chain": block("chain", chains)},
                      "study": {"domain": {"params": {
                          "symbols": params["symbols"], "methods": [{"id": n} for n in names],
                          "correlation_min_pairs": params["correlation_min_pairs"],
                          "bridge": {"min_quote_size": params["bridge"]["min_quote_size"],
                                     "max_relative_spread": params["bridge"]["max_relative_spread"],
                                     "solver": params["bridge"]["solver"]}}}}},
             "results": [{"id": n, "result": {k: e2e.results[n][k] for k in ("trades", "rejected", "curve")}} for n in names]}
    (run / "result.json").write_text(json.dumps(table, sort_keys=True))
    (run / "trades.csv").write_text("method,id,contracts,pnl\n" + "".join(
        f"{n},{t['id']},{t['contracts']},{t['pnl']!r}\n" for n in names for t in e2e.results[n]["trades"]))
    (run / "daily-account.csv").write_text("method,date,equity\n" + "".join(f"{n},2019-10-07,10000.0\n" for n in names))
    return run, base


def test_the_greeks_diagnostics_read_the_saved_unhedged_results(e2e, tmp_path, monkeypatch):
    from index_options import mio_greeks, mio_run_events
    from index_options.mio_greeks import MIOGreeksDiagnostics
    from test_mio_greeks import PARAMS, _FakePinned

    run, base = saved_run(e2e, tmp_path)
    _FakePinned.base = base
    monkeypatch.setattr(mio_greeks, "PinnedPayloadSource", _FakePinned)
    monkeypatch.setattr(mio_run_events, "PinnedPayloadSource", _FakePinned)
    p = e2e.world.params
    over = {"methods": ["twin_base", "cal_arm", "limit_arm"], "window": {"start": "2019-10-01", "end": "2020-01-31"},
            "iv_source": "mid", "quarter": "2019Q4", "run_dir": str(run), "out_dir": str(tmp_path / "out"),
            "beta_min_pairs": p["correlation_min_pairs"], "iv_disagree_tol": 0.05, "dividends": "optional"}
    out = MIOGreeksDiagnostics("greeks", {**PARAMS, **over}).run(None, {})
    books = {}
    for row in out["books"]:
        books.setdefault(row["method"], []).append(row)
    assert {n: len(books[n]) for n in books} == {n: len(e2e.results[n]["trades"]) for n in over["methods"]}
    # The diagnostics recompute option-only P&L, so they read the option-only arms; a hedged arm's forward leg is
    # read by the hedge evidence instead (above).
    assert all(row["method"] in over["methods"] for row in out["books"]) and out["summary"]
