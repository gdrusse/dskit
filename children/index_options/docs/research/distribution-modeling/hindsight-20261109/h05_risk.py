#!/usr/bin/env python3
"""Risk, hedging and exit counterfactuals on the closed cells (development quarters; in-sample).

1. Idiosyncratic vs basket: for every filled book, the underlying's own move over the hold against the equal-weight
   basket move; share of the move a beta-1 index hedge would have removed (no index option quotes exist in the
   documents' inputs, so a hedge is assessed on moves, not priced).
2. Mark path: per cell, unrealized = equity - cash from the daily curve; peak unrealized vs what the open books finally
   settled at; an account-level take-profit counterfactual (close every open book at the mid when unrealized >= theta x
   open reserve), raw and with a 10% haircut on the mark.
3. Sizer: reserve_ratio / stake_ratio per sized week against the week's settled P&L.
Writes 02-risk.md beside this script.
"""
import pathlib
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
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


def main():
    filled = pd.read_csv(HERE/"d_filled_books.csv")
    curve = pd.read_csv(HERE/"curve.csv")
    weeks = pd.read_csv(HERE/"weeks.csv")
    books = pd.read_csv(HERE/"books.csv")
    h("# Risk, hedging and exit counterfactuals (development quarters, in-sample)")
    # ---- 1 idiosyncratic
    h("## 1. Own move vs basket move over the hold")
    f = filled.dropna(subset=["own_move", "basket_hold_move"]).copy()
    f["basket_simple"] = np.expm1(f.basket_hold_move)
    f["resid"] = f.own_move-f.basket_simple          # beta-1 hedge residual
    f["loser"] = f.pnl < 0
    f["big_loser"] = f.pnl < -500
    g = f.groupby(["long_vol", "loser"]).agg(rows=("pnl", "size"), mean_abs_own=("own_move", lambda s: s.abs().mean()),
                                              mean_abs_basket=("basket_simple", lambda s: s.abs().mean()),
                                              mean_abs_resid=("resid", lambda s: s.abs().mean()),
                                              corr_own_basket=("own_move", lambda s: np.corrcoef(s, f.loc[s.index, "basket_simple"])[0, 1] if len(s) > 2 else np.nan))
    h("Mean |own move| of the underlying decision->expiry, mean |basket move| over the same days, and the residual after a "
      "beta-1 index hedge (|own - basket|). If hedging the index helped, the residual would be well below the own move.")
    md(g, "%.3f")
    h("Books losing more than $500 (the drawdown makers):")
    big = f[f.big_loser].sort_values("pnl")[["quarter", "method", "symbol", "expiry", "dte", "long_vol", "pnl", "reserve", "own_move", "basket_simple", "resid", "n_legs", "strategy"]]
    md(big.head(25), "%.3f", index=False)
    h("Share of the big losers' own move that is basket (beta-1) rather than residual: %.2f (sum |basket| / sum |own|). "
      "Short-premium big losers: %d of %d; their own moves averaged %.1f%% against basket %.1f%%." % (
          big.basket_simple.abs().sum()/big.own_move.abs().sum(), (~big.long_vol.astype(bool)).sum(), len(big),
          100*big[~big.long_vol.astype(bool)].own_move.abs().mean() if (~big.long_vol.astype(bool)).any() else np.nan,
          100*big[~big.long_vol.astype(bool)].basket_simple.abs().mean() if (~big.long_vol.astype(bool)).any() else np.nan))

    # ---- 2 mark path and take-profit
    h("## 2. Mark path: unrealized peak vs settled, and an account-level take-profit counterfactual")
    rows, tp_rows = [], []
    thetas = (0.5, 1.0, 2.0, 3.0, 5.0)
    for (q, m), c in curve.groupby(["quarter", "method"]):
        c = c.sort_values("date").reset_index(drop=True)
        c["unreal"] = c.equity-c.cash
        c["open_ids"] = c.n_open
        bk = books[(books.quarter == q) & (books.method == m) & (books.status == "filled")].set_index("id")
        peak_i = c.unreal.idxmax()
        rows.append({"quarter": q, "method": m, "peak_unrealized": c.unreal.max(), "peak_date": c.loc[peak_i, "date"],
                     "min_unrealized": c.unreal.min(), "final_pnl": c.realized.iloc[-1],
                     "peak_over_final": c.unreal.max()/c.realized.iloc[-1] if c.realized.iloc[-1] > 0 else np.nan,
                     "days_unreal_gt_2x_reserve": int(((c.unreal > 2*c.reserve) & (c.reserve > 0)).sum())})
        # take-profit counterfactual, account level: when unreal >= theta*reserve (reserve>0), close everything open at the mark.
        # Books closed this way contribute the mark (optionally haircut); their later settlement is removed. Books opened later are unchanged.
        realized_by_id = bk.pnl.to_dict()
        open_sets = {}
        # reconstruct open ids per day from the receipt curve is not in curve.csv (only the count); approximate with books open by dates
        for theta in thetas:
            for haircut in (0.0, 0.10):
                closed = set(); extra = 0.
                for _, r in c.iterrows():
                    if r.reserve > 0 and r.unreal >= theta*r.reserve:
                        open_now = [i for i, b in bk.iterrows() if b.fill_date <= r.date < b.expiry and i not in closed]
                        if not open_now:
                            continue
                        mark = r.unreal*(1-haircut)
                        settled = sum(realized_by_id[i] for i in open_now)
                        extra += mark-settled
                        closed.update(open_now)
                tp_rows.append({"quarter": q, "method": m, "theta": theta, "haircut": haircut, "actual_pnl": c.realized.iloc[-1],
                                "counterfactual_pnl": c.realized.iloc[-1]+extra, "books_closed_early": len(closed)})
    mp = pd.DataFrame(rows).sort_values(["quarter", "peak_unrealized"], ascending=[True, False])
    h("`peak_unrealized` is the largest mark-to-mid of the open books on any day; `final_pnl` the cell's settled P&L. A ratio "
      "far above 1 means the account was once marked far richer than it ended (no exit exists: hold to expiry).")
    md(mp, "%.2f", index=False)
    tp = pd.DataFrame(tp_rows)
    tp["theta"] = tp.theta.map(lambda t: "theta=%.1f" % t)
    tp["haircut"] = tp.haircut.map(lambda t: "haircut=%.0f%%" % (100*t))
    piv = tp[tp.haircut == "haircut=0%"].pivot_table(index=["quarter", "method"], columns="theta", values="counterfactual_pnl")
    piv.insert(0, "actual", tp[tp.haircut == "haircut=0%"].groupby(["quarter", "method"]).actual_pnl.first())
    h("Take-profit counterfactual (close all open books at the mid when unrealized >= theta x open reserve; later weeks unchanged; "
      "freed cash not reinvested): settled P&L per cell by theta, no haircut:")
    md(piv, "%.0f")
    tot = tp.groupby(["theta", "haircut"]).agg(actual=("actual_pnl", "sum"), counterfactual=("counterfactual_pnl", "sum"),
                                                cells_improved=("counterfactual_pnl", lambda s: (s.values > tp.loc[s.index, "actual_pnl"].values+1).sum()),
                                                cells_worse=("counterfactual_pnl", lambda s: (s.values < tp.loc[s.index, "actual_pnl"].values-1).sum()))
    h("Totals over the 36 cells (actual vs counterfactual), with a 10% haircut row for the mid-mark optimism:")
    md(tot, "%.0f")
    per_q = tp[tp.haircut == "haircut=10%"].groupby(["quarter", "theta"]).agg(actual=("actual_pnl", "sum"), counterfactual=("counterfactual_pnl", "sum"))
    h("By quarter at the 10% haircut:")
    md(per_q, "%.0f")
    tp.to_csv(HERE/"r_takeprofit.csv", index=False)

    # ---- 3 sizer
    h("## 3. Sizer weeks: reserve_ratio against the week's outcome (2021Q2 sized arms)")
    s = weeks[weeks.sizer_status.notna() & (weeks.status == "selected")].copy()
    s["ratio_b"] = pd.cut(s.sizer_reserve_ratio, [-0.01, 0.25, 0.5, 1.0, 10], labels=["<=.25", ".25-.5", ".5-1", ">1"])
    g = s.groupby("ratio_b", observed=True).agg(weeks=("settled_pnl", "size"), pnl=("settled_pnl", "sum"), reserve=("reserve", "sum"),
                                                win=("settled_pnl", lambda x: (x > 0).mean()), stage1_reserve=("sizer_stage1_reserve", "sum"))
    g["pnl_per_reserve"] = g.pnl/g.reserve
    md(g, "%.2f")
    h("Sized weeks, per method: mean reserve_ratio, P&L, and what stage 1's own sizing would have made on the same books "
      "(scaling each week's settled P&L by stage1_reserve / sizer reserve, i.e. assuming the same books at stage-1 counts):")
    s["stage1_scaled_pnl"] = s.settled_pnl*(s.sizer_stage1_reserve/s.sizer_reserve.replace(0, np.nan))
    g = s.groupby("method").agg(weeks=("settled_pnl", "size"), reserve_ratio=("sizer_reserve_ratio", "mean"), stake_ratio=("sizer_stake_ratio", "mean"),
                                pnl=("settled_pnl", "sum"), stage1_scaled_pnl=("stage1_scaled_pnl", "sum"), sigma_daily=("sizer_sigma_daily", "mean"))
    md(g, "%.2f")
    h("Weeks where the sizer scaled stage 1 up (ratio > 1) vs down:")
    md(s.assign(up=s.sizer_reserve_ratio > 1).groupby("up").agg(weeks=("settled_pnl", "size"), pnl=("settled_pnl", "sum"), win=("settled_pnl", lambda x: (x > 0).mean())), "%.2f")
    h("### Shock-gate slack at the decision (all selected weeks with a shock block): the binding move and slack to the limit")
    w = weeks[weeks.status == "selected"].dropna(subset=["shock_limit"]).copy()
    sh = w[["shock_-0.1", "shock_-0.05", "shock_0.05", "shock_0.1"]]
    w["worst_shock_loss"] = sh.max(axis=1)      # the recorded 'losses' are positive when the shocked settlement loses money
    w["slack"] = w.shock_limit-w.worst_shock_loss
    w["binding"] = w.slack < 0.05*w.shock_limit
    g = w.groupby("quarter").agg(weeks=("slack", "size"), binding_weeks=("binding", "sum"), mean_slack_share=("slack", lambda x: (x/w.loc[x.index, "shock_limit"]).mean()))
    md(g, "%.2f")
    h("Binding-week outcomes vs slack weeks (settled P&L per reserve):")
    g = w.groupby("binding").agg(weeks=("settled_pnl", "size"), pnl=("settled_pnl", "sum"), reserve=("reserve", "sum"), win=("settled_pnl", lambda x: (x > 0).mean()))
    g["pnl_per_reserve"] = g.pnl/g.reserve
    md(g, "%.2f")
    (HERE/"02-risk.md").write_text("\n".join(OUT))
    print("wrote 02-risk.md")


if __name__ == "__main__":
    main()
