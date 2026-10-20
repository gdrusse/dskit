"""ADR-0270 walk-forward analysis: model vs raw market vs baselines, per window, rank, ticker; gate; training curves.

Reads the StrikeRegionScores output and the fit queue output of ONE walk-forward run, writes long CSVs.
Run: python -B analysis_wf.py --scores DIR --fits DIR --panel FRAME.parquet --out DIR [--tag TAG] [--candidate NAME] [--arm NAME] [--flips N]

Score (Path A0002): threshold-weighted CRPS on the 13-point z grid -3..3 step 0.5, trapezoid rule, weight 1 on |z| in [0.5, 2.5]
tapering to 0.25 at z = 0 and |z| >= 3; plain CRPS on the same grid and the scorer's own tail CRPS, strike Brier and wing twCRPS beside it.
Edge = (comparator - model) / comparator on mean scores (positive = the model has the lower loss). Comparators: the raw market-implied
curve (market-eligible rows only), the per-name horizon baseline ("baseline"), the pooled standardized empirical ("pooled").
Uncertainty: cluster-robust SE of the paired mean difference, the larger of quote-date and symbol clustering.
Warm-up fold 1 (2019) is reported but never ranked or gated (Path A0008).
Gate (Path A0007): a (ticker, rank) cell passes if the model beats market AND baseline in >= 3 of 4 yearly folds and >= 3 of 5 regimes;
the number expected by luck comes from sign-flipping each quote date's paired difference (randomization, --flips replications).
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

GRID = ["zm3", "zm2.5", "zm2", "zm1.5", "zm1", "zm0.5", "zp0", "zp0.5", "zp1", "zp1.5", "zp2", "zp2.5", "zp3"]
ZS = np.array([-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3], float)
W_TW = np.clip(np.where(np.abs(ZS) < 0.5, 0.25 + 1.5 * np.abs(ZS), np.where(np.abs(ZS) <= 2.5, 1.0, 1 - 1.5 * (np.abs(ZS) - 2.5))), 0.25, 1.0)
FORECASTERS = ("model", "baseline", "pooled", "market")
COMPARATORS = ("market", "baseline", "pooled")
SCORER_METRICS = ("tail_crps", "decision_strike_brier", "decision_wing_twcrps")
REGIMES = {"S0_pre_crash": ("2020-01-02", "2020-02-14"), "S1_covid_crash": ("2020-02-15", "2020-06-30"),
           "S2_recovery": ("2020-07-01", "2021-12-31"), "S3_rate_shock": ("2022-01-03", "2022-12-30"),
           "S4_2023": ("2023-01-03", "2023-12-29")}


def trapezoid(b, weights):
    """Return the weighted trapezoid integral over the grid of the per-threshold Brier matrix b (rows x 13)."""
    f = b * weights[None, :]
    return 0.5 * (f.sum(1) - 0.5 * (f[:, 0] + f[:, -1]))


def load_rows(path, arm, candidate):
    """Read the needed columns of one arm and candidate from rows.parquet."""
    names = pq.ParquetFile(path).schema_arrow.names
    cols = ["symbol", "quote_date", "expiry", "arm", "candidate", "fold", "band", "actual_calendar_dte", "market_eligible", "y"]
    for who in FORECASTERS:
        cols += [f"{who}_cdf_{g}" for g in GRID] + [f"{who}_{m}" for m in SCORER_METRICS] + [f"{who}_below_05", f"{who}_above_95"]
    cols = [c for c in cols if c in names]
    filters = [("arm", "=", arm), ("candidate", "=", candidate)] if arm and candidate else None
    return pq.read_table(path, columns=cols, filters=filters).to_pandas()


def add_scores(d):
    """Add per-forecaster twcrps and crps13 columns (the market's only on market-eligible rows)."""
    ind = (d["y"].to_numpy()[:, None] <= ZS[None, :]).astype(float)
    for who in FORECASTERS:
        cdf = np.stack([d[f"{who}_cdf_{g}"].to_numpy(float) for g in GRID], 1)
        b = (cdf - ind) ** 2
        d[f"{who}_twcrps"] = trapezoid(b, W_TW)
        d[f"{who}_crps13"] = trapezoid(b, np.ones(len(ZS)))
    ok = d["market_eligible"].astype(bool) & d["market_twcrps"].notna()
    d["common"] = ok
    return d


def add_ranks(d, panel_path):
    """Attach the expiry position and rank (weekly 1-8, monthly 1-3) from the panel; drop other names."""
    p = pq.read_table(panel_path, columns=["symbol", "quote_date", "expiry", "calendar_dte"], filters=[("quote_date", "<", "2024-01-01")]).to_pandas()
    p = p.sort_values(["symbol", "quote_date", "calendar_dte"])
    p["pos"] = p.groupby(["symbol", "quote_date"]).cumcount() + 1
    per_day = p.groupby(["symbol", "quote_date"]).size().groupby("symbol").median()
    kind = pd.Series(np.where(per_day >= 7, "weekly", np.where(per_day < 4, "monthly", "other")), index=per_day.index)
    d = d.merge(p[["symbol", "quote_date", "expiry", "pos"]], on=["symbol", "quote_date", "expiry"], how="left")
    d["kind"] = d["symbol"].map(kind)
    d = d[d["kind"].isin(["weekly", "monthly"]) & d["pos"].notna()].copy()
    d["rank"] = np.where(d["kind"] == "weekly", np.minimum(d["pos"], 8), d["pos"]).astype(int)
    return d


def cluster_se(values, dates, symbols):
    """SE of the mean of values clustered by date and by symbol; returns the larger of the two."""
    n = len(values)
    if n < 3:
        return np.nan
    r = values - values.mean()
    out = []
    for key in (dates, symbols):
        s = pd.Series(r).groupby(np.asarray(key)).sum()
        out.append(math.sqrt((s ** 2).sum()) / n)
    return max(out)


def edge_row(g, metric, comp):
    """Paired edge of the model against one comparator on the rows of g."""
    m, c = g[f"model_{metric}"].to_numpy(float), g[f"{comp}_{metric}"].to_numpy(float)
    ok = np.isfinite(m) & np.isfinite(c)
    if ok.sum() < 3:
        return None
    m, c = m[ok], c[ok]
    dd = c - m
    cs = c.mean()
    se = cluster_se(dd, g["quote_date"].to_numpy()[ok], g["symbol"].to_numpy()[ok])
    return {"rows": int(ok.sum()), "dates": int(g["quote_date"].to_numpy()[ok].size and len(set(g["quote_date"].to_numpy()[ok]))),
            "symbols": int(len(set(g["symbol"].to_numpy()[ok]))), "model": m.mean(), "comparator_score": cs,
            "edge_pct": 100 * dd.mean() / cs if cs else np.nan, "se_pct": 100 * se / cs if cs and se == se else np.nan,
            "t": dd.mean() / se if se and se == se else np.nan}


def windows(d):
    """Yield (band, fold, kind, name, frame) for every training-window fold, validation year and regime."""
    for (band, fold), g in d.groupby(["band", "fold"]):
        yield band, int(fold), "fold", f"fold{int(fold)}", g
    v = d[d["band"] == "val"]
    for year, g in v.groupby(v["quote_date"].str[:4]):
        yield "val", int(g["fold"].iloc[0]), "year", f"y{year}", g
    for name, (a, b) in REGIMES.items():
        g = v[(v["quote_date"] >= a) & (v["quote_date"] <= b)]
        if len(g):
            yield "val", -1, "regime", name, g
    ranked = d[d["fold"] >= 2]
    for band in ("fit", "val"):
        g = ranked[ranked["band"] == band]
        if len(g):
            yield band, -2, "pooled", "ranked_folds_2_5", g


def perf_by_window(d):
    """Edge of the model against each comparator for every window, metric and row set."""
    rows = []
    metrics = ["twcrps", "crps13", *SCORER_METRICS]
    for band, fold, kind, name, g in windows(d):
        for comp in COMPARATORS:
            sets = [("market_eligible", g[g["common"]])] + ([("all", g)] if comp != "market" else [])
            for sname, gg in sets:
                for metric in metrics:
                    r = edge_row(gg, metric, comp)
                    if r:
                        rows.append({"band": band, "fold": fold, "window_kind": kind, "window": name, "ranked": not (fold == 1 or name == "y2019"),
                                     "rowset": sname, "comparator": comp, "metric": metric, **r})
    return pd.DataFrame(rows)


def perf_by_rank(d):
    """Edge on twCRPS by expiry rank and validation year (ranked folds only)."""
    rows = []
    v = d[(d["band"] == "val") & d["common"] & (d["fold"] >= 2)]     # fold 1 is the warm-up: never ranked (Path A0008)
    for (kind, rank), g in v.groupby(["kind", "rank"]):
        for year, gy in [("all", g)] + [(f"y{y}", x) for y, x in g.groupby(g["quote_date"].str[:4])]:
            for comp in COMPARATORS:
                r = edge_row(gy, "twcrps", comp)
                if r:
                    rows.append({"kind": kind, "rank": int(rank), "year": year, "comparator": comp, "metric": "twcrps", **r})
    return pd.DataFrame(rows)


def cells(d):
    """Edge on twCRPS per (ticker, rank) and year (ranked folds only)."""
    rows = []
    v = d[(d["band"] == "val") & d["common"] & (d["fold"] >= 2)]
    for (sym, kind, rank), g in v.groupby(["symbol", "kind", "rank"]):
        for year, gy in [("all", g)] + [(f"y{y}", x) for y, x in g.groupby(g["quote_date"].str[:4])]:
            rec = {"symbol": sym, "kind": kind, "rank": int(rank), "year": year}
            for comp in COMPARATORS:
                r = edge_row(gy, "twcrps", comp)
                if r:
                    rec.update({f"{comp}_{k}": r[k] for k in ("edge_pct", "se_pct", "t")})
                    rec["rows"] = r["rows"]
                    rec["dates"] = r["dates"]
            rows.append(rec)
    return pd.DataFrame(rows)


def gate(d, flips, seed=829):
    """Per (ticker, rank): years and regimes positive vs market and baseline; sign-flip randomization for the luck count."""
    v = d[(d["band"] == "val") & d["common"] & (d["fold"] >= 2)].copy()
    v["dm"] = v["market_twcrps"] - v["model_twcrps"]
    v["db"] = v["baseline_twcrps"] - v["model_twcrps"]
    v["year"] = v["quote_date"].str[:4]
    v["regime"] = ""
    for name, (a, b) in REGIMES.items():
        v.loc[(v["quote_date"] >= a) & (v["quote_date"] <= b), "regime"] = name
    rng = np.random.default_rng(seed)
    out = []
    keys = ["symbol", "kind", "rank"]
    groups = [(k, g) for k, g in v.groupby(keys) if len(g)]
    pass_null = np.zeros(max(flips, 1), int)
    for k, g in groups:
        dates = g["quote_date"].to_numpy()
        ud, inv = np.unique(dates, return_inverse=True)
        yr = g["year"].to_numpy()
        rg = g["regime"].to_numpy()
        years = sorted(set(yr))
        regs = [r for r in REGIMES if (rg == r).any()]

        def positives(sign):
            """Count positive years and regimes for market and baseline under per-date signs (sign: one per unique date)."""
            res = {}
            for comp, col in (("market", "dm"), ("baseline", "db")):
                x = g[col].to_numpy() * sign[inv]
                res[comp] = (sum(x[yr == y].mean() > 0 for y in years),
                             sum(x[rg == r].mean() > 0 for r in regs))
            return res
        base = positives(np.ones(len(ud)))
        years_ok = len(years) >= 3
        def passed(p):
            return p["market"][0] >= 3 and p["baseline"][0] >= 3 and p["market"][1] >= 3 and p["baseline"][1] >= 3
        rec = {"symbol": k[0], "kind": k[1], "rank": int(k[2]), "dates": len(ud), "years": len(years), "regimes": len(regs),
               "years_pos_market": base["market"][0], "years_pos_baseline": base["baseline"][0],
               "regimes_pos_market": base["market"][1], "regimes_pos_baseline": base["baseline"][1],
               "edge_market_pct": 100 * g["dm"].mean() / g["market_twcrps"].mean(), "edge_baseline_pct": 100 * g["db"].mean() / g["baseline_twcrps"].mean(),
               "pass": bool(passed(base) and years_ok)}
        out.append(rec)
        if flips and rec["years"] >= 3:
            for f in range(flips):
                s = rng.choice([-1.0, 1.0], len(ud))
                pass_null[f] += passed(positives(s))
    gt = pd.DataFrame(out)
    summ = []
    for label, sub in [("all", gt)] + [(f"kind={k}", x) for k, x in gt.groupby("kind")]:
        summ.append({"group": label, "cells": len(sub), "pass": int(sub["pass"].sum()),
                     "expected_by_luck": float(pass_null.mean()) if label == "all" and flips else np.nan,
                     "luck_p95": float(np.percentile(pass_null, 95)) if label == "all" and flips else np.nan,
                     "beat_market_all_years": int(((sub["years_pos_market"] == sub["years"]) & (sub["years"] >= 3)).sum()),
                     "beat_baseline_all_years": int(((sub["years_pos_baseline"] == sub["years"]) & (sub["years"] >= 3)).sum())})
    return gt, pd.DataFrame(summ)


def curves(fits):
    """Per-fold, per-epoch train and monitor losses from the fit checkpoints."""
    rows = []
    for state in sorted(Path(fits).glob("pooled/*/*/*/pooled/fit/model/state.json")):
        parts = state.parts
        fold = int(parts[parts.index("pooled") + 3])
        st = json.loads(state.read_text())
        meta = json.loads((state.parent.parent / "metadata.json").read_text())
        tr, mo = st["losses"], st["monitor_losses"]
        tr = tr[0] if tr and isinstance(tr[0], list) else tr
        mo = mo[0] if mo and isinstance(mo[0], list) else mo
        best = st["best_epochs"][0] if st.get("best_epochs") else None
        diag = (st.get("diagnostics") or [{}])[0]
        for e, t in enumerate(tr, start=1):
            rows.append({"fold": fold, "epoch": e, "train_loss": t, "monitor_loss": mo[e - 1] if e - 1 < len(mo) else np.nan,
                         "is_best_epoch": best is not None and e == best, "best_epoch": best, "epochs_run": len(tr),
                         "epoch_zero_monitor_loss": diag.get("epoch_zero_monitor_loss"), "parameters": diag.get("parameters"),
                         "fit_seconds": meta.get("elapsed_seconds"), "step_path": diag.get("step_path")})
    return pd.DataFrame(rows)


def calibration(d):
    """Share of outcomes below the 5th and above the 95th percentile per forecaster and window."""
    rows = []
    for band, fold, kind, name, g in windows(d):
        if kind == "regime":
            continue
        for who in FORECASTERS:
            gg = g[g["common"]] if who == "market" else g
            if f"{who}_below_05" not in gg:
                continue
            rows.append({"band": band, "fold": fold, "window_kind": kind, "window": name, "forecaster": who, "rows": len(gg),
                         "below_05": gg[f"{who}_below_05"].mean(), "above_95": gg[f"{who}_above_95"].mean(),
                         "nominal": 0.05})
    return pd.DataFrame(rows)


def main(argv=None):
    """Parse arguments, read the scored rows and write the CSV tables."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True)
    ap.add_argument("--fits", required=True)
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="wf")
    ap.add_argument("--arm", default="context_chain")
    ap.add_argument("--candidate", default="tcn")
    ap.add_argument("--flips", type=int, default=200)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = load_rows(Path(a.scores) / "rows.parquet", a.arm, a.candidate)
    d = add_ranks(add_scores(d), a.panel)
    tables = {"perf_by_window": perf_by_window(d), "perf_by_rank": perf_by_rank(d), "cells_ticker_rank_year": cells(d),
              "training_curves": curves(a.fits), "calibration_by_window": calibration(d)}
    gt, gs = gate(d, a.flips)
    tables["gate_ticker_rank"], tables["gate_summary"] = gt, gs
    for name, t in tables.items():
        t.round(6).to_csv(out / f"{a.tag}_{name}.csv", index=False)
        print(name, len(t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
