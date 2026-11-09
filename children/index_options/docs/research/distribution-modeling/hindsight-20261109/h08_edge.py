#!/usr/bin/env python3
"""Decision-time edge vs realized: each filled book's recorded `expected` (the method's valuation of the book in dollars at the
decision, W1-worst for the dollar arms) per dollar of reserve, against its realized per-reserve return. Writes 05-edge.md."""
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
    f = pd.read_csv(HERE/"d_filled_books.csv")
    f = f[f.reserve > 0].copy()
    f["edge"] = f.expected/f.reserve
    f["realized"] = f.pnl/f.reserve
    h("# Decision-time edge against realized return (filled books, development quarters)")
    h("`edge` = the method's recorded dollar valuation of the book at the decision (W1-worst expected profit for the dollar arms, "
      "the nominal target for sizer arms) over its reserve; `realized` = settled P&L over reserve. A method that knew its edge would "
      "show realized rising with edge.")
    f["edge_q"] = pd.qcut(f.edge.rank(method="first"), 5, labels=["q1 low", "q2", "q3", "q4", "q5 high"])
    g = f.groupby("edge_q", observed=True).agg(rows=("pnl", "size"), edge=("edge", "mean"), realized_mean=("realized", "mean"),
                                               realized_median=("realized", "median"), win=("pnl", lambda s: (s > 0).mean()), pnl=("pnl", "sum"))
    md(g, "%.3f")
    h("Spearman(edge, realized) = %.3f over %d rows; by quarter: %s" % (
        f[["edge", "realized"]].corr(method="spearman").iloc[0, 1], len(f),
        ", ".join("%s %.2f (n=%d)" % (q, d[["edge", "realized"]].corr(method="spearman").iloc[0, 1], len(d)) for q, d in f.groupby("quarter"))))
    h("Edge by structure (mean edge the methods ascribed vs what was realized):")
    g = f.groupby(["long_vol", "dte_bucket"], observed=True).agg(rows=("pnl", "size"), edge=("edge", "mean"), realized=("realized", "mean"), win=("pnl", lambda s: (s > 0).mean()))
    md(g, "%.3f")
    h("Edge by family:")
    g = f.groupby("family").agg(rows=("pnl", "size"), edge=("edge", "mean"), realized=("realized", "mean"), win=("pnl", lambda s: (s > 0).mean()))
    md(g, "%.3f")
    h("Calibration: mean edge vs mean realized per quarter (the methods' own valuations against outcomes):")
    g = f.groupby("quarter").agg(rows=("pnl", "size"), edge=("edge", "mean"), realized=("realized", "mean"), edge_dollars=("expected", "sum"), realized_dollars=("pnl", "sum"))
    md(g, "%.3f")
    (HERE/"05-edge.md").write_text("\n".join(OUT))
    print("wrote 05-edge.md")


if __name__ == "__main__":
    main()
