#!/usr/bin/env python3
"""Executable-side liquidation paths for every filled book, from the quotes its own document saw.

For each filled book: on every session strictly between the fill and the expiry, value the book at the side you could
trade (sell longs at the bid, buy back shorts at the ask), in the ledger's units (quote / basis_factor, multiplier 100,
0.65 per contract to close). liq_pnl(d) = credit + 100 * sum(sign * qty * exec_price) - 0.65 * contracts.
Then exit-rule counterfactuals per book: take-profit at theta x reserve, stop-loss at sigma x reserve, both, and the
best-day exit (an upper bound). Books whose quotes are missing on a day simply cannot be closed that day.
Writes exit_books.csv and 03-exit.md beside this script. Read-only; development quarters only.
"""
import glob, json, pathlib, sqlite3, sys
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from h02_extract import closed_runs  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
MULT, FEE = 100, 0.65
THETAS = (0.5, 1.0, 2.0, 3.0)
SIGMAS = (0.5, 0.75)
OUT = []


def md(df, floatfmt="%.2f", index=True):
    if isinstance(df, pd.Series):
        df = df.to_frame()
    d = df.reset_index() if index else df
    def cell(v):
        if isinstance(v, (float, np.floating)):
            return "-" if not np.isfinite(v) else (floatfmt % v)
        return str(v)
    cols = [str(c) for c in d.columns]
    lines = ["| "+" | ".join(cols)+" |", "|"+"---|"*len(cols)]
    for _, r in d.iterrows():
        lines.append("| "+" | ".join(cell(v) for v in r.values)+" |")
    OUT.append("\n".join(lines)); OUT.append("")


def h(s):
    OUT.append(s); OUT.append("")


def liq_path(con, book):
    """Daily liquidation P&L of a filled book from its symbol's quotes sqlite."""
    legs = book["legs"]; factor = book.get("factor") or 1.
    rows = con.execute("select date, payload from rows where expiration=? and date>? and date<? order by date",
                       (book["expiry"], book["fill_date"], book["expiry"])).fetchall()
    by_day = {}
    for d, p in rows:
        r = json.loads(p)
        by_day.setdefault(d, {})[(r["type"], r["strike_raw"])] = r
    path = []
    for d in sorted(by_day):
        q = by_day[d]; total = 0.; ok = True
        for leg in legs:
            r = q.get((leg["right"], leg["strike"]))
            if r is None:
                ok = False; break
            price = r["bid"] if leg["sign"] > 0 else r["ask"]
            if price is None or (leg["sign"] < 0 and price <= 0):
                ok = False; break
            total += leg["sign"]*leg["quantity"]*price/factor
        if ok:
            path.append((d, book["credit"]+MULT*total-FEE*book["contracts"]))
    return path


def rule_pnl(path, final, reserve, theta=None, sigma=None):
    for d, v in path:
        if theta is not None and v >= theta*reserve:
            return v, d, "tp"
        if sigma is not None and v <= -sigma*reserve:
            return v, d, "sl"
    return final, None, "expiry"


def main():
    rows = []
    cache = {}
    for quarter, set_name, run, methods in closed_runs():
        qdir = run/"artifacts"/"mio-quotes"
        for m in methods:
            if "error" in m or m.get("status") == "failed":
                continue
            r = m.get("result", m)
            for t in r.get("trades", []):
                if t.get("status", "filled") != "filled":
                    continue
                key = (str(run), t["id"])
                if key not in cache:
                    f = qdir/f"{t['symbol']}.sqlite"
                    if not f.exists():
                        cache[key] = []
                    else:
                        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
                        cache[key] = liq_path(con, t)
                        con.close()
                path = cache[key]
                final, reserve = t["pnl"], t["reserve"]
                row = {"quarter": quarter, "set": set_name, "method": m["id"], "id": t["id"], "symbol": t["symbol"], "expiry": t["expiry"],
                       "dte": t["dte"], "contracts": t["contracts"], "credit": t["credit"], "reserve": reserve, "final_pnl": final,
                       "long_vol": t["credit"] < 0, "days_quoted": len(path),
                       "best_liq_pnl": max([v for _, v in path], default=np.nan), "best_day": (max(path, key=lambda x: x[1])[0] if path else None),
                       "worst_liq_pnl": min([v for _, v in path], default=np.nan),
                       "first_day_liq_pnl": path[0][1] if path else np.nan}
                row["best_over_reserve"] = row["best_liq_pnl"]/reserve if reserve else np.nan
                for th in THETAS:
                    v, d, how = rule_pnl(path, final, reserve, theta=th)
                    row[f"tp{th}"] = v; row[f"tp{th}_how"] = how
                for sg in SIGMAS:
                    v, d, how = rule_pnl(path, final, reserve, sigma=sg)
                    row[f"sl{sg}"] = v
                for th in THETAS:
                    for sg in SIGMAS:
                        v, d, how = rule_pnl(path, final, reserve, theta=th, sigma=sg)
                        row[f"tp{th}_sl{sg}"] = v
                rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(HERE/"exit_books.csv", index=False)
    h("# Exit-rule counterfactuals at the executable side (development quarters, in-sample)")
    h("%d filled book rows (%d distinct book paths); %d rows had no quoted day between fill and expiry." % (
        len(df), len(cache), (df.days_quoted == 0).sum()))
    h("## Liquidation value peak vs settlement")
    g = df.groupby(["quarter", "long_vol"]).agg(rows=("id", "size"), final=("final_pnl", "sum"), best_day_exit=("best_liq_pnl", lambda s: s.fillna(0).sum()),
                                                 share_best_gt_reserve=("best_over_reserve", lambda s: (s >= 1).mean()),
                                                 share_best_gt_half=("best_over_reserve", lambda s: (s >= 0.5).mean()),
                                                 median_best_over_reserve=("best_over_reserve", "median"))
    h("`best_day_exit` is the sum of each book's best executable liquidation P&L (NaN -> 0): the per-book upper bound, not a rule. "
      "`share_best_gt_reserve`: share of books that at some day could have been closed for a gain of at least 1x their reserve.")
    md(g, "%.2f")
    h("Books that ended as total losses (final <= -0.95 x reserve) but were at some point closable at a gain:")
    tl = df[df.final_pnl <= -0.95*df.reserve]
    md(pd.DataFrame({"total_loss_rows": [len(tl)], "closable_at_gain": [(tl.best_liq_pnl > 0).sum()], "closable_at_half_reserve": [(tl.best_liq_pnl >= 0.5*tl.reserve).sum()],
                     "closable_at_1x_reserve": [(tl.best_liq_pnl >= tl.reserve).sum()], "sum_final": [tl.final_pnl.sum()], "sum_best": [tl.best_liq_pnl.fillna(0).sum()]}), "%.0f", index=False)
    h("## Rules, totals over all filled rows (each book treated on its own; freed cash not reinvested)")
    cols = ["final_pnl"]+[f"tp{th}" for th in THETAS]+[f"sl{sg}" for sg in SIGMAS]+[f"tp{th}_sl{sg}" for th in THETAS for sg in SIGMAS]
    tot = df.groupby("quarter")[cols].sum().T
    tot["ALL"] = tot.sum(axis=1)
    md(tot, "%.0f")
    h("Per cell: actual vs tp1.0, tp2.0, tp1.0_sl0.5 (sum over the cell's books):")
    pc = df.groupby(["quarter", "method"])[["final_pnl", "tp0.5", "tp1.0", "tp2.0", "tp3.0", "sl0.5", "tp1.0_sl0.5", "tp2.0_sl0.5"]].sum()
    md(pc, "%.0f")
    for col in ("tp1.0", "tp2.0", "tp1.0_sl0.5"):
        h("%s: cells improved %d, worse %d of %d; quarters improved %d of 4." % (
            col, (pc[col] > pc.final_pnl+1).sum(), (pc[col] < pc.final_pnl-1).sum(), len(pc),
            (df.groupby("quarter")[col].sum() > df.groupby("quarter").final_pnl.sum()).sum()))
    h("How often each rule fired (share of rows closed early):")
    fired = {f"tp{th}": (df[f"tp{th}_how"] == "tp").mean() for th in THETAS}
    md(pd.Series(fired, name="share_fired"), "%.2f")
    h("## Long-premium vs short-premium under tp1.0 and sl0.5")
    g = df.groupby("long_vol")[["final_pnl", "tp1.0", "tp2.0", "sl0.5", "tp1.0_sl0.5"]].sum()
    md(g, "%.0f")
    h("## By DTE bucket under tp1.0")
    df["dte_b"] = pd.cut(df.dte, [0, 7, 14, 30, 60, 400], labels=["<=7", "8-14", "15-30", "31-60", ">60"])
    md(df.groupby("dte_b", observed=True)[["final_pnl", "tp1.0", "tp2.0", "sl0.5"]].sum(), "%.0f")
    h("## Distinct books: the ten largest gaps between best executable exit and settlement")
    dd = df.drop_duplicates("id").assign(gap=lambda d: d.best_liq_pnl-d.final_pnl).sort_values("gap", ascending=False)
    md(dd.head(10)[["quarter", "id", "dte", "reserve", "final_pnl", "best_liq_pnl", "best_day", "best_over_reserve"]], "%.0f", index=False)
    (HERE/"03-exit.md").write_text("\n".join(OUT))
    print("wrote 03-exit.md", len(df))


if __name__ == "__main__":
    main()
