#!/usr/bin/env python3
"""Decomposition of realized P&L on the four selection quarters (development set; in-sample by construction).

Reads books.csv / weeks.csv / cells.csv / curve.csv (h02_extract.py) and the locked names' daily closes (the documents'
pinned bars) for a basket regime series. Writes 01-decomposition.md and a few CSVs beside this script. Pandas only, no solve.
"""
import pathlib, glob, math
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
BARS = glob.glob("/home/russell/data/index_options/full-span-rerun-20261105/mio-wsl/onboarding/raw/"
                 "mio-wsl-market-inputs-20261105/*/payload/files/bars/*.parquet")
OUT = []


def md(df, floatfmt="%.2f", index=True):
    """Plain markdown table without tabulate."""
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
    OUT.append("\n".join(lines))
    OUT.append("")


def h(s):
    OUT.append(s); OUT.append("")


def basket():
    frames = [pd.read_parquet(f) for f in BARS]
    px = pd.concat(frames).pivot(index="date", columns="symbol", values="close").sort_index()
    px.index = pd.to_datetime(px.index)
    rets = np.log(px).diff()
    ew = rets.mean(axis=1)                      # equal-weight basket log return
    idx = (ew.fillna(0).cumsum()).rename("basket_log")
    vol20 = ew.rolling(20).std()*math.sqrt(252)
    disp = rets.std(axis=1)                     # cross-sectional dispersion of daily returns
    return pd.DataFrame({"basket_log": idx, "vol20": vol20, "disp": disp, "ew_ret": ew})


def kelly_fraction(returns):
    """Growth-optimal fraction f of capital staked per book on the realized per-reserve returns (each book = one bet
    of f on its reserve). Grid over f in [0, 1/|min r|)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0 or (r <= 0).all():
        return 0., 0.
    fmax = 0.999/abs(r.min()) if r.min() < 0 else 1.
    grid = np.linspace(0, fmax, 2001)[1:]
    g = np.array([np.mean(np.log1p(f*r)) for f in grid])
    i = int(np.nanargmax(g))
    return float(grid[i]), float(g[i])


def main():
    books = pd.read_csv(HERE/"books.csv")
    weeks = pd.read_csv(HERE/"weeks.csv")
    cells = pd.read_csv(HERE/"cells.csv")
    curve = pd.read_csv(HERE/"curve.csv")
    filled = books[books.status == "filled"].copy()
    filled["ret_per_reserve"] = filled.pnl/filled.reserve.replace(0, np.nan)
    filled["long_vol"] = filled.credit < 0          # net debit paid: long-premium structure
    filled["dte_bucket"] = pd.cut(filled.dte, [0, 7, 14, 30, 60, 400], labels=["<=7", "8-14", "15-30", "31-60", ">60"])
    filled["cell"] = filled.quarter+"/"+filled.method
    h("# Decomposition of realized P&L: four selection quarters, 36 closed cells (in-sample)")
    h("Every number here is read off the development quarters 2019Q4, 2020Q1, 2021Q1, 2021Q2 (owner-opened). Nothing "
      "from 2021Q3 onward was read. Counts are small (55 decision weeks per cell, 4 to 23 filled books per cell); treat "
      "effect sizes as descriptions, not discoveries.")

    # ---- A. concentration per cell
    rows = []
    for (q, m), g in filled.groupby(["quarter", "method"]):
        p = g.pnl.sort_values()
        rows.append({"quarter": q, "method": m, "books": len(g), "pnl": p.sum(), "top1": p.iloc[-1], "top1_share": p.iloc[-1]/p.sum() if p.sum() > 0 else np.nan,
                     "ex_top1": p.iloc[:-1].sum(), "ex_worst1": p.iloc[1:].sum(), "median": p.median(),
                     "win_rate": (p > 0).mean(), "pnl_per_reserve": g.pnl.sum()/g.reserve.sum(),
                     "mean_reserve": g.reserve.mean(), "long_vol_share": g.long_vol.mean(),
                     "mean_ret": g.ret_per_reserve.mean(), "median_ret": g.ret_per_reserve.median()})
    conc = pd.DataFrame(rows).sort_values(["quarter", "pnl"], ascending=[True, False])
    h("## A. Concentration per cell (filled books)")
    h("`top1_share` = best book's P&L over the cell's total (when positive); `ex_worst1` drops the worst book; "
      "`long_vol_share` = share of books that paid a net debit (long-premium structures).")
    md(conc, "%.2f", index=False)
    conc.to_csv(HERE/"d_concentration.csv", index=False)
    h("Cells whose total P&L is positive and whose ex-top1 is negative: %d of %d positive cells." %
      (((conc.pnl > 0) & (conc.ex_top1 < 0)).sum(), (conc.pnl > 0).sum()))

    # ---- B. distinct books (methods overlap heavily): (quarter, decision_date, symbol, expiry)
    filled["book_key"] = filled.quarter+"|"+filled.decision_date+"|"+filled.symbol+"|"+filled.expiry
    dist = filled.groupby("book_key").agg(quarter=("quarter", "first"), date=("decision_date", "first"), symbol=("symbol", "first"),
                                          expiry=("expiry", "first"), dte=("dte", "first"), n_methods=("method", "nunique"),
                                          mean_pnl=("pnl", "mean"), mean_ret=("ret_per_reserve", "mean"),
                                          long_vol=("long_vol", "mean"), spot=("spot", "first"))
    h("## B. Distinct (date, symbol, expiry) books across all cells")
    h("Methods share most of their books. %d distinct books carry the %d filled rows; %d are chosen by 3+ methods." %
      (len(dist), len(filled), (dist.n_methods >= 3).sum()))
    top = dist.sort_values("mean_pnl", ascending=False)
    h("Top 12 distinct books by mean P&L (per cell that held it):")
    md(top.head(12), "%.2f")
    h("Bottom 8:")
    md(top.tail(8), "%.2f")
    dist.to_csv(HERE/"d_distinct_books.csv")
    # symbol pooled
    sym = filled.groupby(["quarter", "symbol"]).agg(rows=("pnl", "size"), pnl=("pnl", "sum"), ret=("ret_per_reserve", "mean")).reset_index()
    h("Symbol contribution pooled over cells (sum of P&L over every cell-book that held the name; top/bottom 8 per quarter):")
    for q, g in sym.groupby("quarter"):
        g = g.sort_values("pnl")
        md(pd.concat([g.head(5), g.tail(8)]), "%.0f", index=False)

    # ---- C. structure
    h("## C. By structure")
    for col in ("dte_bucket", "family", "long_vol", "n_legs"):
        g = filled.groupby(col, observed=True).agg(rows=("pnl", "size"), distinct=("book_key", "nunique"), pnl=("pnl", "sum"),
                                                   win_rate=("pnl", lambda s: (s > 0).mean()), mean_ret=("ret_per_reserve", "mean"),
                                                   median_ret=("ret_per_reserve", "median"), reserve=("reserve", "sum"))
        g["pnl_per_reserve"] = g.pnl/g.reserve
        h(f"### by {col}")
        md(g, "%.2f")
    h("### long-premium vs short-premium by quarter")
    g = filled.groupby(["quarter", "long_vol"]).agg(rows=("pnl", "size"), pnl=("pnl", "sum"), win_rate=("pnl", lambda s: (s > 0).mean()),
                                                    reserve=("reserve", "sum"))
    g["pnl_per_reserve"] = g.pnl/g.reserve
    md(g, "%.2f")
    h("### moneyness of the nearest short strike (long-premium books have none)")
    filled["short_mny"] = pd.cut(filled.short_strike_rel, [-1, -0.1, -0.03, 0.03, 0.1, 5], labels=["<-10%", "-10..-3%", "ATM±3%", "+3..10%", ">+10%"])
    g = filled.groupby("short_mny", observed=True).agg(rows=("pnl", "size"), pnl=("pnl", "sum"), win_rate=("pnl", lambda s: (s > 0).mean()),
                                                       mean_ret=("ret_per_reserve", "mean"))
    md(g, "%.2f")
    h("### fill slippage (fill price vs decision price, $ per contract, filled books)")
    md(filled.groupby("quarter").fill_slip_per_contract.describe()[["count", "mean", "50%", "max"]], "%.1f")

    # ---- D. regime
    h("## D. Week regime: equal-weight basket of the locked names (the documents' own bars; no SPY/VIX in the inputs)")
    bk = basket()
    def fwd(d0, n):
        i = bk.index.searchsorted(pd.Timestamp(d0))
        if i+n >= len(bk) or i >= len(bk):
            return np.nan
        return bk.basket_log.iloc[i+n]-bk.basket_log.iloc[i]
    wk = weeks.copy()
    wk["basket_fwd5"] = wk.date.map(lambda d: fwd(d, 5))
    wk["basket_fwd10"] = wk.date.map(lambda d: fwd(d, 10))
    wk["vol20"] = wk.date.map(lambda d: bk.vol20.asof(pd.Timestamp(d)))
    wk["disp"] = wk.date.map(lambda d: bk.disp.asof(pd.Timestamp(d)))
    wk.to_csv(HERE/"d_weeks_regime.csv", index=False)
    # distinct weeks pooled: mean settled pnl over cells in that quarter-week
    wq = wk.groupby(["quarter", "date"]).agg(cells=("method", "nunique"), mean_pnl=("settled_pnl", "mean"), n_sel=("status", lambda s: (s == "selected").sum()),
                                             fwd5=("basket_fwd5", "first"), fwd10=("basket_fwd10", "first"), vol20=("vol20", "first"), disp=("disp", "first")).reset_index()
    h("Per decision week, mean settled P&L of the books decided that week averaged over the quarter's cells, with the basket's "
      "forward 5- and 10-session log return and trailing 20-session annualised vol at the decision date:")
    md(wq, "%.3f", index=False)
    h("Correlation (Spearman) of the week's mean P&L with |fwd10| basket move: %.2f; with vol20: %.2f (n=%d weeks)." % (
        wq[["mean_pnl", "fwd10"]].assign(a=lambda d: d.fwd10.abs()).corr(method="spearman").loc["mean_pnl", "a"],
        wq[["mean_pnl", "vol20"]].corr(method="spearman").iloc[0, 1], len(wq)))
    # per filled book: basket move over the hold
    def hold_move(r):
        i0 = bk.index.searchsorted(pd.Timestamp(r.decision_date)); i1 = bk.index.searchsorted(pd.Timestamp(r.expiry))
        if i1 >= len(bk) or i0 >= len(bk):
            return np.nan
        return bk.basket_log.iloc[i1]-bk.basket_log.iloc[i0]
    filled["basket_hold_move"] = filled.apply(hold_move, axis=1)
    filled["own_move"] = np.nan
    px = {}
    for f in BARS:
        d = pd.read_parquet(f); px[d.symbol.iloc[0]] = d.set_index("date").close
    def own(r):
        s = px.get(r.symbol)
        if s is None or r.expiry not in s.index or r.decision_date not in s.index:
            return np.nan
        return s[r.expiry]/s[r.decision_date]-1
    filled["own_move"] = filled.apply(own, axis=1)
    filled["abs_own_bucket"] = pd.cut(filled.own_move.abs(), [0, 0.03, 0.07, 0.15, 10], labels=["<3%", "3-7%", "7-15%", ">15%"])
    h("### Filled books by the underlying's own move decision date -> expiry (close to close, unadjusted)")
    g = filled.groupby(["long_vol", "abs_own_bucket"], observed=True).agg(rows=("pnl", "size"), pnl=("pnl", "sum"),
                                                                           win_rate=("pnl", lambda s: (s > 0).mean()), mean_ret=("ret_per_reserve", "mean"))
    md(g, "%.2f")
    filled.to_csv(HERE/"d_filled_books.csv", index=False)

    # ---- E. within-week co-movement
    h("## E. Within-week co-movement of book outcomes")
    rows = []
    for (q, m, d), g in filled.groupby(["quarter", "method", "decision_date"]):
        if len(g) >= 2:
            signs = np.sign(g.pnl)
            rows.append({"quarter": q, "n_books": len(g), "all_same_sign": float(abs(signs.sum()) == len(g)),
                         "pnl": g.pnl.sum(), "worst_share": g.pnl.min()/g.reserve.sum()})
    cw = pd.DataFrame(rows)
    if len(cw):
        h("Cell-weeks with 2+ filled books: %d; share where every book had the same P&L sign: %.2f (independent coin flips "
          "with the pooled win rate %.2f would give %.2f for 2 books)." % (
              len(cw), cw.all_same_sign.mean(), (filled.pnl > 0).mean(),
              (filled.pnl > 0).mean()**2+(1-(filled.pnl > 0).mean())**2))
        md(cw.groupby("quarter").agg(cell_weeks=("pnl", "size"), same_sign=("all_same_sign", "mean"), mean_books=("n_books", "mean")), "%.2f")
    # pairwise correlation of distinct-book returns within the same week (pooled)
    pairs = []
    for (q, d), g in dist.groupby(["quarter", "date"]):
        r = g.mean_ret.values
        for i in range(len(r)):
            for j in range(i+1, len(r)):
                pairs.append((r[i], r[j]))
    if pairs:
        pr = np.array(pairs)
        h("Pairs of distinct books decided the same week (pooled over cells): %d; correlation of their per-reserve returns %.2f; "
          "share with the same sign %.2f." % (len(pr), np.corrcoef(pr[:, 0], pr[:, 1])[0, 1], (np.sign(pr[:, 0]) == np.sign(pr[:, 1])).mean()))

    # ---- F. sizing: P&L per reserve vs books; ex-post Kelly
    h("## F. Sizing: reserve fraction used vs the ex-post growth-optimal fraction")
    rows = []
    for (q, m), g in filled.groupby(["quarter", "method"]):
        f_star, growth = kelly_fraction(g.ret_per_reserve.values)
        # fraction actually used: each book's reserve over the funding at its decision date
        w = weeks[(weeks.quarter == q) & (weeks.method == m)].set_index("date").funding
        used = (g.reserve/g.decision_date.map(w)).mean()
        rows.append({"quarter": q, "method": m, "books": len(g), "mean_ret": g.ret_per_reserve.mean(), "sd_ret": g.ret_per_reserve.std(),
                     "min_ret": g.ret_per_reserve.min(), "max_ret": g.ret_per_reserve.max(), "f_used_per_book": used,
                     "f_kelly_expost": f_star, "growth_at_f_star": growth})
    kf = pd.DataFrame(rows)
    h("`f_used_per_book` = mean book reserve over the account's funding at decision; `f_kelly_expost` = fraction of capital per "
      "book that maximises mean log(1+f*r) on the cell's own realized per-reserve returns (returns are bounded below by -1, "
      "so a cell with no losing book has f* = 1 by construction).")
    md(kf.sort_values(["quarter", "method"]), "%.3f", index=False)
    # pooled per quarter across the distinct books
    rows = []
    for q, g in dist.groupby("quarter"):
        f_star, growth = kelly_fraction(g.mean_ret.values)
        rows.append({"quarter": q, "distinct_books": len(g), "mean_ret": g.mean_ret.mean(), "median_ret": g.mean_ret.median(),
                     "win_rate": (g.mean_pnl > 0).mean(), "f_kelly_expost": f_star, "growth": growth})
    f_all, g_all = kelly_fraction(dist.mean_ret.values)
    rows.append({"quarter": "ALL", "distinct_books": len(dist), "mean_ret": dist.mean_ret.mean(), "median_ret": dist.mean_ret.median(),
                 "win_rate": (dist.mean_pnl > 0).mean(), "f_kelly_expost": f_all, "growth": g_all})
    md(pd.DataFrame(rows), "%.3f", index=False)
    # weeks: P&L per reserve vs books per week
    sel = weeks[weeks.status == "selected"]
    h("### Selected weeks: books per week vs settled P&L per committed reserve")
    g = sel.assign(nb=pd.cut(sel.n_books, [0, 1, 2, 3, 100], labels=["1", "2", "3", "4+"])).groupby("nb", observed=True).agg(
        weeks=("settled_pnl", "size"), pnl=("settled_pnl", "sum"), reserve=("reserve", "sum"), win=("settled_pnl", lambda s: (s > 0).mean()))
    g["pnl_per_reserve"] = g.pnl/g.reserve
    md(g, "%.2f")
    h("### Decision status counts per quarter (all cells)")
    md(weeks.pivot_table(index="quarter", columns="status", values="date", aggfunc="count", fill_value=0), "%.0f")
    h("### Timeout reasons")
    md(weeks[weeks.status != "selected"].groupby(["quarter", "status", "timeout_reason"], dropna=False).size().rename("n").reset_index(), "%.0f", index=False)

    # ---- G. drawdown weeks
    h("## G. Drawdown: worst marked drawdown and the weeks that produced it")
    rows = []
    for (q, m), g in curve.groupby(["quarter", "method"]):
        g = g.sort_values("date")
        i = g.drawdown.idxmin()
        peak_before = g.loc[:i].equity.idxmax()
        rows.append({"quarter": q, "method": m, "max_dd": g.drawdown.min(), "trough": g.loc[i, "date"], "peak": g.loc[peak_before, "date"],
                     "equity_peak": g.loc[peak_before, "equity"], "equity_trough": g.loc[i, "equity"], "final": g.equity.iloc[-1],
                     "max_reserve_share": (g.reserve/g.funding.replace(0, np.nan)).max()})
    dd = pd.DataFrame(rows).sort_values(["quarter", "max_dd"])
    md(dd, "%.2f", index=False)
    dd.to_csv(HERE/"d_drawdowns.csv", index=False)
    (HERE/"01-decomposition.md").write_text("\n".join(OUT))
    print("wrote 01-decomposition.md", len(OUT), "blocks")


if __name__ == "__main__":
    main()
