#!/usr/bin/env python3
"""Print full examples of nested records (trade legs, decision books, certificates, stage1 selection, curve). Read-only."""
import json, pathlib, sys, glob


def load(run_glob, method_idx=0):
    rd = sorted(glob.glob(run_glob))[0]
    run = pathlib.Path(rd)
    carry = json.loads((run/"carry.json").read_text())
    receipt = pathlib.Path(carry["close_packet"]["receipt"]["path"])
    if not receipt.is_absolute():
        receipt = run/receipt
    art = json.loads(receipt.read_text())
    res = art["results"]
    methods = res if isinstance(res, list) else res.get("methods", res)
    return run, art, methods


def trunc(obj, n=2500):
    s = json.dumps(obj, indent=1, default=str)
    return s[:n] + ("...[%d more]" % (len(s)-n) if len(s) > n else "")


def main(run_glob, which):
    run, art, methods = load(run_glob)
    print("methods:", [m["id"] for m in methods])
    m = methods[0]
    r = m.get("result", m)
    if which == "trade":
        print(trunc(r["trades"][0], 4000))
        print("REJECTED", trunc(r.get("rejected", [])[:1], 1500))
    elif which == "decision":
        sel = [d for d in r["decisions"] if d.get("status") == "selected"][0]
        print("BOOK", trunc(sel["books"][0], 3500))
        print("CERT", trunc(sel["certificates"][0], 1500))
        print("REFUSALS", trunc(sel["refusals"][:2], 800))
        print("STAGE1 sel keys", list(sel["stage1"]["selection"].keys()))
        s1 = dict(sel["stage1"]["selection"])
        for k in list(s1):
            if isinstance(s1[k], list) and len(s1[k]) > 5:
                s1[k] = "list[%d] e.g. %s" % (len(s1[k]), s1[k][:3])
        print("STAGE1 sel", trunc(s1, 1500))
        print("SHOCK losses", trunc(sel["shock"]["losses"], 600))
        print("SIZER solve", trunc(sel["sizer"]["solve"], 800))
        print("SIZER alternates", trunc(sel["sizer"]["alternates"][0], 600))
        print("SIZER units", trunc(sel["sizer"].get("units", [])[:2], 800))
        print("SIZER correlations", trunc(sel["sizer"]["correlations"], 400))
        print("CURVE", r["curve"][0], r["curve"][5])
        print("LEDGER", trunc(r["ledger_summary"], 800))
        print("METHOD", trunc(r["method"], 1200))
        print("DEPOSITS", r["deposits"][:2])
        other = [d for d in r["decisions"] if d.get("status") != "selected"]
        if other:
            o = dict(other[0]);
            for k in ("eligible_by_pair", "eligible_by_symbol", "selection", "shock", "books", "stage1", "sizer", "certificates"):
                o.pop(k, None)
            print("NON-SELECTED decision", trunc(o, 1500))
    elif which == "config":
        cfg = json.loads((run/"config.json").read_text())
        print(trunc(cfg, 6000))
    elif which == "plan":
        print(trunc(art["plan"], 3000))
        print("PROV", trunc(art["provenance"], 1500))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
