#!/usr/bin/env python3
"""Summarise h04_oracle.py's outputs into 04-oracle.md: universe base rates per quarter, the bounded oracle per cell
(full universe, same names, same groups), and the selections' per-reserve return against the universe's."""
import glob, pathlib
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
    uni = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(HERE/"oracle_universe_*.csv")))])
    orc = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(HERE/"oracle_*.csv"))) if "universe" not in f])
    filled = pd.read_csv(HERE/"d_filled_books.csv")
    h("# Bounded hindsight oracle and universe base rates (development quarters, in-sample)")
    h("Universe per decision week: every eligible long single contract (documents' quote rule) in the quoted (symbol, expiry) "
      "groups with 1-60 DTE, bought at the next session's ask, held to expiry, settled at the expiry close over the basis "
      "factor (the ledger's rule). `ret` is P&L per premium. Short structures are not enumerated (see the note).")
    h("## Universe base rates (mean over the quarter's decision weeks)")
    cols = ["contracts", "groups", "share_positive", "share_total_loss", "share_ret_gt3", "median_ret", "mean_ret", "mean_ret_31_60",
            "mean_ret_le7", "share_pos_31_60", "share_pos_le7", "mean_ret_otm_gt10", "mean_ret_atm3", "equal_weight_pnl_per_dollar"]
    g = uni.groupby("quarter")[cols].mean()
    md(g.T, "%.3f")
    h("Per week (equal-weight long portfolio P&L per dollar of premium, and the share of contracts ending positive):")
    md(uni[["quarter", "date", "contracts", "share_positive", "share_total_loss", "median_ret", "mean_ret", "mean_ret_31_60", "mean_ret_le7", "equal_weight_pnl_per_dollar"]], "%.3f", index=False)
    h("## Selections against the universe")
    f = filled.copy()
    sel = f.groupby("quarter").agg(rows=("pnl", "size"), sel_mean_ret=("ret_per_reserve", "mean"), sel_median_ret=("ret_per_reserve", "median"),
                                   sel_win=("pnl", lambda s: (s > 0).mean()))
    lv = f[f.long_vol].groupby("quarter").agg(long_rows=("pnl", "size"), long_mean_ret=("ret_per_reserve", "mean"), long_win=("pnl", lambda s: (s > 0).mean()))
    comp = sel.join(lv).join(g[["mean_ret", "share_positive", "mean_ret_31_60", "share_pos_31_60"]].rename(
        columns={"mean_ret": "uni_mean_ret", "share_positive": "uni_win", "mean_ret_31_60": "uni_mean_ret_31_60", "share_pos_31_60": "uni_win_31_60"}))
    h("Selected books' mean per-reserve return and win rate (all filled rows, and long-premium rows alone) against the universe's "
      "mean per-premium return and share positive (equal weight over contracts):")
    md(comp, "%.3f")
    h("## Oracle per cell (sum over the quarter's decision weeks of the best knapsack under 25% of funding and fill depth)")
    o = orc.groupby(["quarter", "method"]).agg(weeks=("date", "size"), actual=("actual_pnl", "sum"), actual_reserve=("actual_reserve", "sum"),
                                               oracle_full=("oracle_pnl", "sum"), oracle_same_names=("oracle_same_names_pnl", "sum"),
                                               oracle_same_groups=("oracle_same_groups_pnl", "sum"), names_overlap=("names_overlap", "sum"),
                                               selected_weeks=("status", lambda s: (s == "selected").sum()))
    o["log10_full_over_budget"] = np.log10(o.oracle_full/(orc.groupby(["quarter", "method"]).budget.sum()))
    h("`oracle_full` is astronomically large by construction (the single best contract each week at depth): it bounds nothing useful "
      "except to say the universe always contained a 10x-100x weekly winner. `oracle_same_names` restricts the knapsack to the names "
      "the method selected that week, `oracle_same_groups` to its (symbol, expiry) groups: how much of the name's or the expiry's "
      "move the chosen structure captured. `names_overlap` counts weeks' selected names that were also in the full oracle's picks.")
    md(o, "%.0f")
    h("Capture ratio per quarter: actual P&L over the same-groups oracle (what the chosen structures took of what their own groups offered), "
      "pooled over the quarter's cells:")
    cap = o.groupby("quarter").agg(actual=("actual", "sum"), same_groups=("oracle_same_groups", "sum"), same_names=("oracle_same_names", "sum"))
    cap["actual_over_same_groups"] = cap.actual/cap.same_groups
    md(cap, "%.3f")
    h("Weeks where the method traded nothing (cash/timeout) and what the full oracle had: ")
    nt = orc[orc.status != "selected"].groupby("quarter").agg(weeks=("date", "size"), oracle_full=("oracle_pnl", "sum"))
    md(nt, "%.0f")
    (HERE/"04-oracle.md").write_text("\n".join(OUT))
    print("wrote 04-oracle.md")


if __name__ == "__main__":
    main()
