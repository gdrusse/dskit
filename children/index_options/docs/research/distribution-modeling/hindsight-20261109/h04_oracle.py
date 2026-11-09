#!/usr/bin/env python3
"""Bounded hindsight oracle per decision week, from the quotes each document saw plus the pinned bars (settlement).

Universe: every long single contract (buy at the next session's ask, hold to expiry, settle at the expiry close / basis
factor as the ledger does) in the (symbol, expiry) groups quoted on the decision date with 1..60 DTE, eligible by the
documents' quote rule (bid > 0, ask > bid, sizes >= 1, relative spread <= .5). Reserve of a long = premium paid.
Oracle = best integer knapsack of these under the weekly budget (25% of funding) and the fill-session ask depth
(greedy by return per dollar, which is exact up to the last item). Short structures are not enumerated: their reserve
rule (max loss plus the 20% sold-notional floor) and the no-naked-call row make the ex-post best short book a MILP
per week; stated as not reconstructed. Development quarters only; read-only; one process, small memory.

  python3 h04_oracle.py 2021Q2 /mnt/d/.../runs/2021Q2-sized/mio/<run>   [more quarter run pairs]
Writes oracle_<quarter>.csv (per week, per contract summary) and oracle_universe_<quarter>.csv (base rates).
"""
import csv, glob, json, pathlib, sqlite3, sys
from datetime import date
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
BARS = glob.glob("/home/russell/data/index_options/full-span-rerun-20261105/mio-wsl/onboarding/raw/"
                 "mio-wsl-market-inputs-20261105/*/payload/files/bars/*.parquet")
BUDGET_FRACTION = 0.25     # weekly_fraction of funding for new reserve
MULT = 100


def load_bars():
    px = {}
    for f in BARS:
        d = pd.read_parquet(f)
        px[d.symbol.iloc[0]] = d.set_index("date").close
    sessions = sorted(set().union(*[set(s.index) for s in px.values()]))
    return px, sessions


def eligible(r):
    if any(r.get(k) is None for k in ("bid", "ask", "bid_size", "ask_size", "underlying_price", "basis_factor")):
        return False
    mid = (r["bid"]+r["ask"])/2
    return r["bid"] > 0 and r["ask"] > r["bid"] and r["bid_size"] >= 1 and r["ask_size"] >= 1 and (r["ask"]-r["bid"])/mid <= 0.5


def week_universe(quotes_dir, px, sessions, day, max_dte=60):
    """Every eligible long contract on decision day `day` with its fill ask (next session) and settlement payoff."""
    i = sessions.index(day) if day in sessions else None
    if i is None or i+1 >= len(sessions):
        return []
    fill_day = sessions[i+1]
    out = []
    for f in sorted(glob.glob(str(pathlib.Path(quotes_dir)/"*.sqlite"))):
        sym = pathlib.Path(f).stem
        bars = px.get(sym)
        if bars is None or day not in bars.index:
            continue
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        dec = {}
        for p, in con.execute("select payload from rows where date=?", (day,)):
            r = json.loads(p)
            dte = (date.fromisoformat(r["expiration"])-date.fromisoformat(day)).days
            if 1 <= dte <= max_dte and r["expiration"] in bars.index and eligible(r):
                dec[(r["expiration"], r["type"], r["strike_raw"])] = r
        if not dec:
            con.close(); continue
        fill = {}
        for p, in con.execute("select payload from rows where date=?", (fill_day,)):
            r = json.loads(p)
            k = (r["expiration"], r["type"], r["strike_raw"])
            if k in dec:
                fill[k] = r
        con.close()
        for k, r in dec.items():
            fr = fill.get(k)
            if fr is None or fr.get("ask") is None or fr["ask"] <= 0 or (fr.get("ask_size") or 0) < 1:
                continue
            factor = r["basis_factor"]
            strike = r["strike_raw"]
            spot = r["underlying_price"]/factor
            settle = bars[r["expiration"]]/factor
            intrinsic = max(0., settle-strike) if r["type"] == "call" else max(0., strike-settle)
            price_dec = r["ask"]/factor
            price_fill = fr["ask"]/factor
            cap = int(min(r["ask_size"], fr["ask_size"]))
            out.append({"symbol": sym, "expiry": k[0], "right": k[1], "strike": strike, "spot": spot, "mny": strike/spot-1,
                        "dte": (date.fromisoformat(k[0])-date.fromisoformat(day)).days, "price_dec": price_dec,
                        "price_fill": price_fill, "capacity": cap, "settle": settle,
                        "pnl_per_contract": MULT*(intrinsic-price_fill)-0.65, "premium": MULT*price_fill,
                        "ret": (intrinsic-price_fill)/price_fill})
    return out


def knapsack(items, budget):
    """Greedy by return per dollar with integer contracts under capacity; returns (pnl, spent, picks)."""
    pnl = spent = 0.
    picks = []
    for it in sorted(items, key=lambda x: -x["ret"]):
        if it["ret"] <= 0 or spent >= budget:
            break
        n = min(it["capacity"], int((budget-spent)//it["premium"]))
        if n <= 0:
            continue
        pnl += n*it["pnl_per_contract"]; spent += n*it["premium"]
        picks.append((it["symbol"], it["expiry"], it["right"], it["strike"], n, round(n*it["pnl_per_contract"])))
    return pnl, spent, picks


def main(pairs):
    px, sessions = load_bars()
    weeks = pd.read_csv(HERE/"weeks.csv")
    books = pd.read_csv(HERE/"books.csv")
    for quarter, run in pairs:
        qdir = pathlib.Path(run)/"artifacts"/"mio-quotes"
        wq = weeks[weeks.quarter == quarter]
        rows, base = [], []
        for day in sorted(wq.date.unique()):
            uni = week_universe(qdir, px, sessions, day)
            if not uni:
                print(quarter, day, "no universe"); continue
            u = pd.DataFrame(uni)
            u["mny_b"] = pd.cut(u.mny.abs(), [-1, 0.03, 0.1, 0.25, 10], labels=["<3%", "3-10%", "10-25%", ">25%"])
            u["dte_b"] = pd.cut(u.dte, [0, 7, 14, 30, 60], labels=["<=7", "8-14", "15-30", "31-60"])
            base.append({"quarter": quarter, "date": day, "contracts": len(u), "symbols": u.symbol.nunique(), "groups": u.groupby(["symbol", "expiry"]).ngroups,
                         "share_positive": (u.pnl_per_contract > 0).mean(), "share_total_loss": (u.ret <= -0.999).mean(),
                         "share_ret_gt3": (u.ret > 3).mean(), "median_ret": u.ret.median(), "mean_ret": u.ret.mean(),
                         "mean_ret_31_60": u[u.dte_b == "31-60"].ret.mean(), "mean_ret_le7": u[u.dte_b == "<=7"].ret.mean(),
                         "share_pos_31_60": (u[u.dte_b == "31-60"].pnl_per_contract > 0).mean(),
                         "share_pos_le7": (u[u.dte_b == "<=7"].pnl_per_contract > 0).mean(),
                         "mean_ret_otm_gt10": u[u.mny.abs() > 0.1].ret.mean(), "mean_ret_atm3": u[u.mny.abs() <= 0.03].ret.mean(),
                         "equal_weight_pnl_per_dollar": (u.pnl_per_contract.sum()/u.premium.sum())})
            # cells of this quarter decided this week
            for _, w in wq[wq.date == day].iterrows():
                budget = BUDGET_FRACTION*(w.funding if np.isfinite(w.funding) else 10000.)
                o_pnl, o_spent, picks = knapsack(uni, budget)
                sel_syms = set(str(w.symbols).split()) if isinstance(w.symbols, str) and w.symbols else set()
                sel_books = books[(books.quarter == quarter) & (books.method == w.method) & (books.decision_date == day) & (books.status == "filled")]
                sel_groups = set(zip(sel_books.symbol, sel_books.expiry))
                n_pnl, n_spent, _ = knapsack([x for x in uni if x["symbol"] in sel_syms], budget) if sel_syms else (0., 0., [])
                g_pnl, g_spent, _ = knapsack([x for x in uni if (x["symbol"], x["expiry"]) in sel_groups], budget) if sel_groups else (0., 0., [])
                # the oracle's names: were they in the selection?
                o_syms = set(p[0] for p in picks)
                rows.append({"quarter": quarter, "date": day, "method": w.method, "status": w.status, "funding": w.funding, "budget": budget,
                             "actual_reserve": w.reserve, "actual_pnl": w.settled_pnl, "n_books": w.n_books,
                             "oracle_pnl": o_pnl, "oracle_spent": o_spent, "oracle_names": " ".join(sorted(o_syms)),
                             "oracle_same_names_pnl": n_pnl, "oracle_same_groups_pnl": g_pnl,
                             "selected_names": " ".join(sorted(sel_syms)), "names_overlap": len(o_syms & sel_syms),
                             "oracle_top_pick": str(picks[0]) if picks else ""})
            print(quarter, day, "contracts", len(u), "oracle(2500)", round(knapsack(uni, 2500.)[0]))
        pd.DataFrame(rows).to_csv(HERE/f"oracle_{quarter}.csv", index=False)
        pd.DataFrame(base).to_csv(HERE/f"oracle_universe_{quarter}.csv", index=False)


if __name__ == "__main__":
    args = sys.argv[1:]
    main(list(zip(args[0::2], args[1::2])))
