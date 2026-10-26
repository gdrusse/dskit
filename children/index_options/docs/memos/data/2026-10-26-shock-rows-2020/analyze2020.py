import json, glob, collections
from pathlib import Path
S = Path(__file__).parent
f = sorted(glob.glob(str(S/"result-2020-*.json")))[-1]
R = json.load(open(f)); print(f, R["identity"][:12])
for m in R["methods"]:
    d = m["decisions"]; st = collections.Counter(x["status"] for x in d)
    sel = [x for x in d if x["status"] == "selected"]
    secs = [x["certificates"][0]["seconds"] for x in d if "certificates" in x]
    gaps = [x["selection"]["gap"] for x in d if "selection" in x and "gap" in x["selection"]]
    names = collections.Counter(b["symbol"] for x in sel for b in x["books"])
    strat = collections.Counter(b["strategy"]["name"] if isinstance(b.get("strategy"), dict) else str(b.get("strategy"))[:30] for x in sel for b in x["books"])
    print("\n==", m["method"]["id"], dict(st), "solve s: max %.0f mean %.1f" % (max(secs), sum(secs)/len(secs)), "max gap %.1f" % max(gaps))
    print("books/week", collections.Counter(len(x["books"]) for x in sel), "names", names.most_common(8))
    print("strategies", strat.most_common(6))
    tr = m["trades"]; print("trades", len(tr), "pnl by symbol", sorted(((round(sum(t["pnl"] for t in tr if t["symbol"]==s)), s) for s in {t["symbol"] for t in tr}), reverse=True)[:12])
    print("rejected reasons", collections.Counter(r["reason"] for r in m["rejected"]))
    worst = sorted(tr, key=lambda t: t["pnl"])[:4]
    print("worst", [(t["symbol"], t["decision_date"], t["expiry"], round(t["pnl"]), t["contracts"]) for t in worst])
    best = sorted(tr, key=lambda t: -t["pnl"])[:4]
    print("best", [(t["symbol"], t["decision_date"], t["expiry"], round(t["pnl"]), t["contracts"]) for t in best])
    if any("shock" in x for x in d):
        binds = 0; rows = []
        for x in d:
            if "shock" in x and x["shock"]["losses"] and all(v is not None for v in x["shock"]["losses"].values()):
                lim = x["shock"]["limit"]; mx = max(x["shock"]["losses"].items(), key=lambda kv: kv[1])
                if mx[1] > lim - 1e-6: binds += 1
                rows.append((x["date"], round(lim), {k: round(v) for k, v in x["shock"]["losses"].items()}, x["shock"]["observations"], x["status"]))
        print("shock binding weeks", binds, "of", len(rows))
        for r in rows[:6] + rows[9:16]: print("  ", r)
    for x in d:
        if x["date"][:7] in ("2020-02", "2020-03"):
            print("  ", x["date"], x["status"], [(b["symbol"], b["strategy"]["name"] if isinstance(b.get("strategy"), dict) else "", b["contracts"], round(b["reserve"])) for b in x.get("books", [])], "funding", round(x["funding"]))
curves = {m["method"]["id"]: m["curve"] for m in R["methods"]}
for k, c in curves.items():
    lows = min(c, key=lambda s: s["nav"]); print(k, "nav low", round(lows["nav"], 3), lows["date"], "end", round(c[-1]["nav"], 3))
