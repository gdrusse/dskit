#!/usr/bin/env python3
"""Tidy extraction of every closed MIO cell on the four selection quarters (development set only; 2021Q3+ never opened).

Writes beside this script:
  cells.csv   one row per (quarter, set, method)            (compare.py's columns)
  weeks.csv   one row per decision week per cell            (status, books, credit, reserve, settled P&L, cumulative, account)
  books.csv   one row per book (filled and unfilled)        (symbol, expiry, dte, legs summary, credit, reserve, pnl ...)
  curve.csv   one row per account day per cell
Read-only on the run roots; stdlib + csv only; starts no solve.
"""
import csv, json, pathlib, sys
from collections import defaultdict

sys.path.insert(0, "/home/russell/data/index_options/full-span-rerun-20261105/mio-wsl/sized-check")
import compare  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def closed_runs():
    import glob
    for rd in sorted(set(p for pattern in compare.ROOTS for p in glob.glob(pattern))):
        low = rd.lower()
        if any(k in low for k in compare.SKIP):
            continue
        run = pathlib.Path(rd)
        if not (run/"carry.json").exists():
            continue
        carry = json.loads((run/"carry.json").read_text())
        receipt = pathlib.Path(carry["close_packet"]["receipt"]["path"])
        if not receipt.is_absolute():
            receipt = run/receipt
        if not receipt.exists():
            continue
        art = json.loads(receipt.read_text())
        res = art["results"]
        methods = res if isinstance(res, list) else res.get("methods", res)
        match = compare.QUARTER.search(run.parent.parent.name) or compare.QUARTER.search(rd)
        quarter = match.group(1).upper() if match else "?"
        yield quarter, run.parent.parent.name, run, methods


def legs_summary(book):
    legs = book.get("legs", [])
    spot = book.get("spot") or 0.
    out = {"n_legs": len(legs),
           "n_long_calls": sum(l["quantity"] for l in legs if l["right"] == "call" and l["sign"] > 0),
           "n_short_calls": sum(l["quantity"] for l in legs if l["right"] == "call" and l["sign"] < 0),
           "n_long_puts": sum(l["quantity"] for l in legs if l["right"] == "put" and l["sign"] > 0),
           "n_short_puts": sum(l["quantity"] for l in legs if l["right"] == "put" and l["sign"] < 0)}
    strikes = [l["strike"] for l in legs]
    out["min_strike_rel"] = (min(strikes)/spot-1) if strikes and spot else None
    out["max_strike_rel"] = (max(strikes)/spot-1) if strikes and spot else None
    # signed premium per contract-unit of exposure: net long premium (debit) > 0 means a long-vol structure
    out["net_debit_per_contract"] = (-book.get("credit", 0.)/book["contracts"]) if book.get("contracts") else None
    sold = [l for l in legs if l["sign"] < 0]
    bought = [l for l in legs if l["sign"] > 0]
    # closest short strike relative to spot (where the risk sits) and widest wing
    out["short_strike_rel"] = (min(sold, key=lambda l: abs(l["strike"]-spot))["strike"]/spot-1) if sold and spot else None
    out["long_strike_rel"] = (min(bought, key=lambda l: abs(l["strike"]-spot))["strike"]/spot-1) if bought and spot else None
    out["fill_slip_per_contract"] = None
    slips = [(l.get("fill_price", l["price"])-l["price"])*l["sign"]*l["quantity"] for l in legs if l.get("fill_price") is not None]
    if slips and book.get("contracts"):
        out["fill_slip_per_contract"] = sum(slips)*100/book["contracts"]   # $ paid above decision price, per contract
    return out


def main():
    cells, weeks, books, curve = [], [], [], []
    for quarter, set_name, run, methods in closed_runs():
        for m in methods:
            if "error" in m or m.get("status") == "failed":
                continue
            method = m.get("id")
            r = m.get("result", m)
            cell = compare.row(quarter, set_name, method, r)
            cell["kelly_outcomes"] = json.dumps(cell.get("kelly_outcomes")) if cell.get("kelly_outcomes") else ""
            cells.append(cell)
            key = {"quarter": quarter, "set": set_name, "method": method}
            # books: filled trades and rejected (unfilled) ones
            pnl_by_week = defaultdict(float)
            for t in list(r.get("trades", [])) + list(r.get("rejected", [])):
                row = dict(key)
                for k in ("id", "decision_date", "fill_date", "expiry", "close_date", "dte", "symbol", "pair_id", "contracts",
                          "credit", "decision_credit", "reserve", "max_loss", "pnl", "fees", "spot", "expected", "factor",
                          "status", "reason", "stress_extra", "method_fitted"):
                    row[k] = t.get(k)
                row["family"] = (t.get("strategy") or {}).get("family")
                row["strategy"] = (t.get("strategy") or {}).get("name")
                row["units"] = (t.get("strategy") or {}).get("units")
                row.update(legs_summary(t))
                books.append(row)
                if t.get("status", "filled") == "filled":
                    pnl_by_week[t["decision_date"]] += t.get("pnl") or 0.
            # weeks
            cum = 0.
            for d in r.get("decisions", []):
                row = dict(key)
                row["date"] = d.get("date"); row["status"] = d.get("status")
                sel_books = d.get("books") or []
                row["n_books"] = len(sel_books)
                row["n_filled"] = sum(1 for t in r.get("trades", []) if t.get("decision_date") == d.get("date")
                                      and t.get("status", "filled") == "filled")
                row["credit"] = sum(b.get("credit", 0.) for b in sel_books)
                row["reserve"] = sum(b.get("reserve", 0.) for b in sel_books)
                row["contracts"] = sum(b.get("contracts", 0) for b in sel_books)
                row["symbols"] = " ".join(sorted(set(b.get("symbol", "") for b in sel_books)))
                row["candidate_books"] = d.get("candidate_books"); row["candidate_contracts"] = d.get("candidate_contracts")
                row["fitted"] = d.get("fitted"); row["funding"] = d.get("funding"); row["open_reserve"] = d.get("open_reserve")
                sel = d.get("selection") or {}
                row["sel_value"] = sel.get("value"); row["sel_bound"] = sel.get("bound"); row["sel_gap"] = sel.get("gap")
                row["sel_stage"] = sel.get("stage")
                gv = sel.get("group_values") or []
                row["n_groups_positive"] = sum(1 for v in gv if v and v > 1e-9)
                nv = sel.get("nominal_values") or []
                row["sum_nominal_selected"] = sum(v for v in nv if v) if nv else None
                row["sum_w1_selected"] = sum(v for v in gv if v) if gv else None
                certs = d.get("certificates") or []
                row["solve_seconds"] = sum(c.get("seconds", 0.) for c in certs)
                row["termination"] = ";".join(sorted(set(str(c.get("termination")) for c in certs)))
                row["timeout_reason"] = d.get("timeout_reason") or d.get("reason")
                sh = (d.get("shock") or {}).get("losses") or {}
                for mv in ("-0.1", "-0.05", "0.05", "0.1"):
                    row["shock_%s" % mv] = sh.get(mv)
                row["shock_limit"] = (d.get("shock") or {}).get("limit")
                sz = d.get("sizer") or {}
                for k in ("status", "fraction", "reserve", "stage1_reserve", "reserve_ratio", "stake_ratio", "cash_limit",
                          "sigma_daily", "exact", "shrink_applied"):
                    row["sizer_%s" % k] = sz.get(k)
                row["sizer_tilt"] = (sz.get("tilt") or {}).get("applied") if isinstance(sz.get("tilt"), dict) else sz.get("tilt")
                kl = d.get("kelly") or {}
                row["kelly_outcome"] = (kl.get("solve") or {}).get("strategy_outcome") if kl else None
                row["settled_pnl"] = pnl_by_week.get(d.get("date"), 0.)
                cum += row["settled_pnl"]
                row["cumulative_by_decision"] = cum
                weeks.append(row)
            for p in r.get("curve", []):
                row = dict(key)
                for k in ("date", "equity", "cash", "funding", "reserve", "realized", "drawdown", "nav", "contributions",
                          "carried_books", "deposit"):
                    row[k] = p.get(k)
                row["n_open"] = len(p.get("open_ids") or [])
                curve.append(row)
    for name, rows in (("cells", cells), ("weeks", weeks), ("books", books), ("curve", curve)):
        cols = []
        for rw in rows:
            for k in rw:
                if k not in cols:
                    cols.append(k)
        with open(HERE/f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(name, len(rows), "rows")


if __name__ == "__main__":
    main()
