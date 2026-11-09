#!/usr/bin/env python3
"""Dump the record layout of one closed MIO cell (keys, one example trade / decision / curve point). Read-only, stdlib."""
import json, pathlib, sys, glob

sys.path.insert(0, "/home/russell/data/index_options/full-span-rerun-20261105/mio-wsl/sized-check")
import compare  # noqa: E402


def short(v, depth=0):
    if isinstance(v, dict):
        if depth >= 2:
            return "{...%d keys}" % len(v)
        return {k: short(x, depth+1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return ["list[%d]" % len(v)] + ([short(v[0], depth+1)] if v else [])
    if isinstance(v, str) and len(v) > 60:
        return v[:60]+"..."
    return v


def main(run_glob):
    rd = sorted(glob.glob(run_glob))[0]
    run = pathlib.Path(rd)
    print("run", run)
    print("files", sorted(p.name for p in run.iterdir()))
    carry = json.loads((run/"carry.json").read_text())
    receipt = pathlib.Path(carry["close_packet"]["receipt"]["path"])
    if not receipt.is_absolute():
        receipt = run/receipt
    print("receipt", receipt)
    art = json.loads(receipt.read_text())
    print("art keys", list(art.keys()))
    res = art["results"]
    methods = res if isinstance(res, list) else res.get("methods", res)
    print("n methods", len(methods))
    m = methods[0]
    print("method keys", list(m.keys()))
    r = m.get("result", m)
    print("result keys", {k: (type(v).__name__, len(v) if hasattr(v, "__len__") else v) for k, v in r.items()})
    tr = r.get("trades", [])
    if tr:
        print("TRADE example:", json.dumps(short(tr[0]), indent=1)[:3000])
    de = r.get("decisions", [])
    if de:
        sel = [d for d in de if d.get("status") == "selected"] or de
        print("DECISION keys:", list(sel[0].keys()))
        print("DECISION example:", json.dumps(short(sel[0]), indent=1)[:5000])
    cu = r.get("curve", [])
    if cu:
        print("CURVE example:", cu[0], cu[-1])
    for k in ("settlements", "ledger", "books"):
        if k in r:
            print(k, json.dumps(short(r[k]), indent=1)[:1500])


if __name__ == "__main__":
    main(sys.argv[1])
