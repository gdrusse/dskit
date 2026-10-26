"""2020 validation-year weekly study: fitted M51 (fold 2 val) with the best MIO, with and without shock rows."""
import datetime, json, sys, time
from pathlib import Path
import pyarrow.parquet as pq
from index_options.ledger_studies import WeeklyOptionPortfolioStudy
from dskit.pipeline.base import value_hash
from dskit.pipeline.records import price_ok
from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

S = Path(__file__).parent
START, END = "2020-01-02", "2021-01-01"
FOLD = 2
root = Path("/home/russell/data/index_options/theta-strike-screen-20261018")
report = Path("/home/russell/.cursor/projects/home-russell-dskit/canvases/index-options-2019-condor-oracle.canvas.tsx")
text = report.read_text()
v11 = json.loads(next(l[len("const weeklyStudyConfig = "):-1] for l in text.splitlines()
                      if l.startswith("const weeklyStudyConfig = ")))
params = json.loads(json.dumps(v11))
params["bridge"].update(start=START, end_before=END, protected_end_before=END, fold=FOLD)
params["solver"]["group_screen"] = "lp"
base = {"rho": .03, "portfolio_fraction": 1., "name_fraction": 1., "component_fraction": None,
        "short_notional_fraction": .2}
params["methods"] = [
    {**base, "id": "M05 W1 .03 screen sn.2"},
    {**base, "id": "M05 W1 .03 screen sn.2 shock10",
     "shock": {"moves": [-.1, -.05, .05, .1], "loss_fraction": .1, "beta_lookback": 60}},
]
if len(sys.argv) > 1:
    params["methods"] = [m for m in params["methods"] if m["id"] in sys.argv[1:]]
problems = TransportPortfolioSolve.validate_params(params["solver"])
if problems:
    raise ValueError("; ".join(problems))
columns = ["symbol", "quote_date", "expiry", "fold", "band"]
labels = params["bridge"]["grid_labels"]
inputs, provenance = {}, {}
t0 = time.perf_counter()
for symbol in params["symbols"]:
    filters = [("symbol", "=", symbol), ("quote_date", ">=", START), ("quote_date", "<", END),
               ("expiry", "<", END), ("fold", "=", FOLD), ("band", "=", "val")]
    forecasts = pq.read_table(root/"scores/edge-method-51-qa-mlp64-by-dispersion/forecasts.parquet",
        columns=columns+["fitted"]+["method_cdf_"+s for s in labels], filters=filters).to_pylist()
    contexts = pq.read_table(root/"scores/strike-region-scores-wf-wide-mlp64/rows.parquet",
        columns=columns+["spot", "reference_scale", "settlement_date"],
        filters=filters+[("settlement_date", "<", END)]).to_pylist()
    archive = root/"exact-expiry-tables/archive"/symbol.lower()/f"options_{START[:4]}.parquet"
    chain = pq.read_table(archive,
        columns=["symbol", "date", "expiration", "type", "strike_raw", "strike", "bid", "ask",
                 "bid_size", "ask_size", "basis_factor", "underlying_price"],
        filters=[("symbol", "=", symbol), ("date", ">=", START), ("date", "<", END),
                 ("expiration", "<", END)]).to_pylist()
    bars = pq.read_table(root/"exact-expiry-tables/prices/index_daily"/(symbol+".parquet"),
        columns=["symbol", "date", "close"],
        filters=[("symbol", "=", symbol), ("date", ">=", START), ("date", "<", END)]).to_pylist()
    basis_rows = pq.read_table(archive, columns=["symbol", "date", "basis_factor"],
        filters=[("symbol", "=", symbol), ("date", ">=", START), ("date", "<", END)],
        use_threads=False).to_pylist()
    daily_basis = {}
    for row in basis_rows:
        day, factor = row.get("date"), row.get("basis_factor")
        if (row.get("symbol") != symbol or not isinstance(day, str) or not START <= day < END
                or datetime.date.fromisoformat(day).isoformat() != day or not price_ok(factor)):
            raise ValueError("invalid or unprotected daily basis metadata")
        if day in daily_basis and daily_basis[day] != factor:
            raise ValueError("inconsistent daily basis metadata")
        daily_basis[day] = float(factor)
    for bar in bars:
        if bar["date"] in daily_basis:
            bar["basis_factor"] = daily_basis[bar["date"]]
    inputs[symbol] = {"forecasts": forecasts, "contexts": contexts, "chain": chain, "bars": bars}
    provenance[symbol] = {"forecasts": len(forecasts), "fitted": sum(bool(f["fitted"]) for f in forecasts),
                          "contexts": len(contexts), "chain": len(chain), "bars": len(bars)}
    print("LOAD", symbol, provenance[symbol], round(time.perf_counter()-t0), "s", flush=True)
identity = value_hash({"params": params, "provenance": provenance})
print("IDENTITY", identity, flush=True)
study = WeeklyOptionPortfolioStudy(params, inputs)
print("STUDY", len(study.weeks), "weeks", len(study.calendar), "sessions", flush=True)
import os, resource
if os.environ.get("DRY"):
    for week in (study.weeks[0], study.weeks[12], study.weeks[-2]):
        t1 = time.perf_counter()
        groups, metadata, refusals = study._prepare(week)
        print("PREPARE", week, len(groups), "groups", sum(len(g["capacity"]) for g in groups), "items",
              len(refusals), "refusals", round(time.perf_counter()-t1, 1), "s",
              "rss_mb", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024, flush=True)
    sys.exit(0)
def progress(method, done, total, status):
    print("WEEK", method, done, total, status, round(time.perf_counter()-t0), "s", flush=True)
results = []
for method in params["methods"]:
    result = study._method(method, progress)
    print("DONE", method["id"], {k: result[k] for k in ("pnl", "stress_pnl", "final_equity", "twr",
          "max_drawdown", "contracts", "unfilled", "wins", "losses", "fees")}, flush=True)
    replay = study._method(method, None, recorded=result)
    print("REPLAY", method["id"], replay == result, flush=True)
    results.append(result)
    (S/f"result-2020-{identity[:12]}.json").write_text(json.dumps(
        {"identity": identity, "params": params, "provenance": provenance, "methods": results},
        allow_nan=False))
print("ALL DONE", round(time.perf_counter()-t0), "s", flush=True)
