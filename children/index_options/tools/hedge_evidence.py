"""ADR-0312 Amendment 1 S3: the read-only evidence reads E1-E4 over a finished no-solve hedge stress run.

Reads ``mio_execution_stress`` v6 outputs (``<root>/<cohort>/<scenario>/<method>.json``: the unhedged ``control``
twin and the ``static_forward_hedge_r*`` twins, each with a carry-ratio mark twin) and, for E1, ADR-0310's
``greeks-summary.json`` / books rows. It writes one verdict file and never anything else. Nothing is solved.

Fixed before any read (the amendment's section 2; the owner may change the numbers only before the first read):

* per account (each selection quarter is its own account, A0010) the CONTRIBUTION-FREE marked curve
  ``equity - contributions`` of both twins, one running peak from the account's first session, sessions through
  the last settlement and never at or past ``DEVELOPMENT_SEAL``;
* ``dDD_q`` = unhedged minus hedged maximum depth below the running peak, ``dPnl_q`` = hedged minus unhedged
  settled P&L, ``B_q = dDD_q + min(dPnl_q, 0)``;
* E2 passes when at least 3 quarters count (not ``mark_sensitive``, same books in both twins, at least half of
  the gross P&L hedgeable), the median ``B`` over them is >= 2,000, the median over those other than 2020Q1 is
  >= 1,000 (two or more such quarters) and no quarter has ``dPnl < -4,000``; fewer counting quarters is
  ``insufficient``;
* the adopted ratio is the SMALLEST passing ratio in the order 0.25, 0.5, 0.75, 1.0, never the best median, and
  it must also clear ``attempts.max_bar`` on the session-level depth gap ``d_t = D^u_t - D^h_t`` AND on the same
  gaps pre-aggregated to one block per ``(account, ISO week)``: ``t >= pass_mark``, ``adj_p <= alpha``,
  ``mean > 0``, ``lower > 0``;
* E1 reads ADR-0310's ``direction_read`` per decision quarter; E1b is information only; E3 and E4 are reported
  as ``not_run`` / ``not available`` unless their inputs exist.

Every reader refuses a document that carries ``decision_weeks`` (a timing check is never evidence).
"""

import argparse
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

from dskit.pipeline.attempts import AttemptRegistry, max_bar, session_totals
from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL

__all__ = ["B_MEDIAN", "B_MEDIAN_REST", "RUIN", "FAMILY", "contribution_free", "depths", "quarter_read",
           "e2_verdict", "bar_reads", "e1_read", "e1b_rho2", "e4_ratio", "register_attempts", "main"]

#: The pre-registered E2 numbers (dollars).
B_MEDIAN, B_MEDIAN_REST, RUIN = 2000.0, 1000.0, -4000.0
#: A quarter whose carry-ratio dDD differs from the primary by more than max(MARK_FRACTION x |B|, MARK_FLOOR).
MARK_FRACTION, MARK_FLOOR = 0.25, 500.0
MIN_COUNTING, MIN_COUNTING_REST = 3, 2
HEDGEABLE_SHARE = 0.5
E1_MEDIAN, E1_MIN_QUARTERS = 1000.0, 3
CRASH_QUARTER = "2020Q1"
RATIOS = (0.25, 0.5, 0.75, 1.0)
FAMILY = "mio_hedge_overlay"
RHO2_PASS, RHO2_QUARTERS, RHO2_MIN_BOOKS = 0.67, 3, 5
E4_LEVEL, E4_UPPER = "0.9", 0.25            # the walk keys a level f"{lvl:g}"
SCENARIO_PREFIX = "static_forward_hedge_r"


def _refuse_check_week(document):
    """Refuse a result or run document cut to a ``decision_weeks`` check week."""
    from index_options.ledger_studies import WeeklyOptionPortfolioStudy

    WeeklyOptionPortfolioStudy.check_document_not_evidence(document)


def contribution_free(curve):
    """Return ``[(date, equity - contributions)]`` of a result's marked curve (K1: deposits change no depth)."""
    return [(point["date"], point["equity"] - point["contributions"]) for point in curve]


def depths(series):
    """Return ``[(date, depth)]``: dollars below the running peak, the peak starting at the first value."""
    peak, out = None, []
    for day, value in series:
        peak = value if peak is None else max(peak, value)
        out.append((day, peak - value))
    return out


def _last_settlement(result):
    days = [t["close_date"] for t in result["trades"]]
    return max(days) if days else None


def _clock(results):
    """Return the account's session dates: first curve session through the last settlement of either twin, below the seal."""
    for result in results:
        crossing = [t["id"] for t in result["trades"] if t["close_date"] >= DEVELOPMENT_SEAL]
        if crossing:
            raise ValueError(f"seal_crossing: {crossing} settle at or past {DEVELOPMENT_SEAL}; exclude them from BOTH "
                             "twins and rerun")
    last = max(filter(None, (_last_settlement(r) for r in results)), default=None)
    first = results[0]["curve"][0]["date"]
    return [p["date"] for p in results[0]["curve"] if first <= p["date"] <= (last or first) and p["date"] < DEVELOPMENT_SEAL]


def _twin_depths(result, dates):
    keep = set(dates)
    return [(d, depth) for d, depth in depths(contribution_free(result["curve"])) if d in keep]


def _hedgeable_share(unhedged, hedged):
    """Return the share of the unhedged gross P&L that sits in books the hedge actually covered (status ok, units != 0)."""
    gross = math.fsum(abs(t["pnl"]) for t in unhedged["trades"])
    covered = {t["id"] for t in hedged["trades"] if t.get("hedge", {}).get("units")}
    got = math.fsum(abs(t["pnl"]) for t in unhedged["trades"] if t["id"] in covered)
    return got / gross if gross > 0 else None


def _carried_share(result):
    """Return the share of the hedged twin's forward-leg marks that fell back to the carry ratio (None without marks)."""
    counts = result.get("hedge", {}).get("counts", {})
    return counts.get("mark_forward_carried", 0) / counts["hedge_marks"] if counts.get("hedge_marks") else None


def quarter_read(quarter, control, hedged, carry=None):
    """Return one account's E2 read: ``dDD``, ``dPnl``, ``B``, the ``d_t`` series and the flags that decide counting.

    Parameters
    ----------
    quarter : str
        ``YYYYQn`` label of the account.
    control, hedged : dict
        The unhedged and hedged twins' ``result`` (curve, trades, pnl), same account.
    carry : dict, optional
        The hedged twin with EVERY mark on the carry-ratio rule: the ``mark_sensitive`` robustness read.
    """
    for result in (control, hedged, *([carry] if carry else [])):
        _refuse_check_week(result)
    dates = _clock([control, hedged])
    d_u, d_h = _twin_depths(control, dates), _twin_depths(hedged, dates)
    gap = [(day, u - h) for (day, u), (_, h) in zip(d_u, d_h, strict=True)]
    d_dd = max(u for _, u in d_u) - max(h for _, h in d_h)
    d_pnl = hedged["pnl"] - control["pnl"]
    b = d_dd + min(d_pnl, 0.0)
    out = {"quarter": quarter, "dDD": d_dd, "dPnl": d_pnl, "B": b, "d_t": gap, "sessions": len(dates),
           "books_differ": {t["id"] for t in control["trades"]} != {t["id"] for t in hedged["trades"]},
           "hedgeable_share_gross": _hedgeable_share(control, hedged), "mark_sensitive": None,
           "mark_forward_carried_share": _carried_share(hedged)}
    share = out["hedgeable_share_gross"]
    out["under_half_hedgeable"] = share is None or share < HEDGEABLE_SHARE
    if carry is not None:
        c_dd = max(u for _, u in d_u) - max(h for _, h in _twin_depths(carry, dates))
        out["dDD_carry"] = c_dd
        out["mark_sensitive"] = ((c_dd > 0) != (d_dd > 0) and not (c_dd == d_dd == 0)) or \
            abs(c_dd - d_dd) > max(MARK_FRACTION * abs(b), MARK_FLOOR)
    out["counts"] = not (out["mark_sensitive"] or out["books_differ"] or out["under_half_hedgeable"])
    return out


def e2_verdict(reads):
    """Return the E2 verdict ``{verdict, counting, median_B, median_B_rest, ruin_breaches, reasons}`` over quarter reads."""
    counting = [r for r in reads if r["counts"]]
    rest = [r for r in counting if r["quarter"] != CRASH_QUARTER]
    out = {"counting": [r["quarter"] for r in counting], "reasons": [],
           "ruin_breaches": [r["quarter"] for r in reads if r["dPnl"] < RUIN],
           "median_B": statistics.median(r["B"] for r in counting) if counting else None,
           "median_B_rest": statistics.median(r["B"] for r in rest) if rest else None}
    if len(counting) < MIN_COUNTING or len(rest) < MIN_COUNTING_REST:
        out["verdict"] = "insufficient"
        return out
    for ok, why in ((out["median_B"] >= B_MEDIAN, f"median B {out['median_B']:.2f} < {B_MEDIAN}"),
                    (out["median_B_rest"] >= B_MEDIAN_REST, f"non-2020Q1 median B {out['median_B_rest']:.2f} < {B_MEDIAN_REST}"),
                    (not out["ruin_breaches"], f"dPnl below {RUIN} in {out['ruin_breaches']}")):
        if not ok:
            out["reasons"].append(why)
    out["verdict"] = "fail" if out["reasons"] else "pass"
    return out


def _weekly(series_by_account):
    """Return ``{(account, iso_week): (sum d_t, n)}`` through the reduction hook ``session_totals``."""
    stamps = [(account, day) for account, series in series_by_account.items() for day, _ in series]
    values = [d for series in series_by_account.values() for _, d in series]
    return {key: tuple(v) for key, v in session_totals(
        stamps, values, session_of=lambda s: (s[0], date.fromisoformat(s[1]).isocalendar()[:2])).items()}


def _per_session(series_by_account):
    return {(account, day): (d, 1) for account, series in series_by_account.items() for day, d in series}


def _clears(row, pass_mark, alpha):
    """Return the explicit verdict on one ``max_bar`` row: ``t >= pass_mark and adj_p <= alpha and mean > 0 and lower > 0``."""
    return bool(row and row["t"] is not None and row["t"] >= pass_mark and row["adj_p"] is not None
                and row["adj_p"] <= alpha and row["mean"] > 0 and row["lower"] > 0)


def bar_reads(attempts, adopted, k_declared, n_boot=10000, seed=0, alpha=0.05, also=()):
    """Run ``attempts.max_bar`` DIRECTLY on every attempt's gaps, per session and per (account, week); judge the adopted row.

    Parameters
    ----------
    attempts : dict
        ``{attempt name: {account: [(date, d_t)]}}``.
    adopted : str or None
        The attempt whose own row is judged (explicit verdict: ``t >= pass_mark``, ``adj_p <= alpha``, ``mean > 0``,
        ``lower > 0``; not ``bar_verdict``, which carries ADR-0067's forecast-skill test).
    k_declared : int
        ``AttemptRegistry.count`` of the family.
    """
    out = {}
    for name, cells in (("session", {a: _per_session(s) for a, s in attempts.items()}),
                        ("week", {a: _weekly(s) for a, s in attempts.items()})):
        try:
            bar = max_bar(cells, n_boot=n_boot, seed=seed, alpha=alpha, k_declared=k_declared)
        except ValueError as error:                     # constant gaps: no statistic exists, so nothing can pass
            out[name] = {"passed": False, "unavailable": str(error)}
            continue
        row = next((r for r in bar["rows"] if r["cell"] == adopted), None)
        passed = _clears(row, bar["pass_mark"], alpha)
        for extra in also:
            other = next((r for r in bar["rows"] if r["cell"] == extra), None)
            out.setdefault("also", {}).setdefault(extra, {})[name] = {"row": other, "passed": _clears(other, bar["pass_mark"], alpha)}
        out[name] = {"pass_mark": bar["pass_mark"], "c_star": bar["c_star"], "k": bar["k"], "k_declared": bar["k_declared"],
                     "adopted_row": row, "passed": passed, "notes": bar["notes"]}
    out["passed"] = bool(adopted) and out["session"]["passed"] and out["week"]["passed"]
    return out


def e1_read(reads, direction):
    """Return the E1 verdict from per-quarter ``direction_read`` blocks (ADR-0310 A2.1.3) of the named formulation.

    A quarter COUNTS when ``forward_hedge.n_hedged >= min_books`` and ``concentration.loo_sign_stable_hedge_pnl``
    is true. PASS: at least 3 counting quarters, median ``H_q`` >= 1,000 and every counting ``H_q > 0``;
    fewer than 3 counting is ``insufficient``; a median at or below 0 is ``not_a_profit_lever``.
    """
    rows = []
    for quarter, block in sorted(direction.items()):
        if block.get("quarter") != quarter:
            raise ValueError(f"e1_quarter_mismatch: the file named {quarter} carries quarter {block.get('quarter')!r}")
        fh, conc = block["forward_hedge"], block["concentration"]
        counts = (fh["n_hedged"] >= fh["min_books"] and conc.get("loo_sign_stable_hedge_pnl") is True)
        rows.append({"quarter": quarter, "H": fh["hedge_pnl_usd"], "n_hedged": fh["n_hedged"], "counts": counts})
    counting = [r for r in rows if r["counts"] and r["H"] is not None]
    out = {"rows": rows, "counting": [r["quarter"] for r in counting], "median_H": None}
    if len(counting) < E1_MIN_QUARTERS:
        out["verdict"] = "insufficient"
        return out
    out["median_H"] = statistics.median(r["H"] for r in counting)
    if out["median_H"] <= 0:
        out["verdict"] = "not_a_profit_lever"
    else:
        out["verdict"] = "pass" if out["median_H"] >= E1_MEDIAN and all(r["H"] > 0 for r in counting) else "fail"
    return out


def e1_series(books, dates):
    """Return E1's ``d_t``: the hedge-leg P&L of ok-hedged books settling on each session, an explicit zero elsewhere."""
    settle = {}
    for book in books:
        if book.get("forward_hedge_status") == "ok" and book.get("hedge_pnl") is not None:
            settle[book["expiry"]] = settle.get(book["expiry"], 0.0) + float(book["hedge_pnl"])
    return [(day, settle.get(day, 0.0)) for day in dates]


def books_from_csv(path, formulation):
    """Return the ``books.csv`` rows (ADR-0310's ``mio_greeks`` output) of one formulation, numbers parsed."""
    import csv

    rows = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("method") not in (None, formulation):
                continue
            for name in ("hedge_pnl", "forward_hedged_pnl", "delta_f_sh", "forward", "s_fill", "s_settle"):
                text = row.get(name)
                row[name] = float(text) if text not in (None, "") else None
            rows.append(row)
    return rows


E1B_NEEDS = ("beta", "basket_return")


def _weighted_rho2(x, y, w):
    sw = math.fsum(w)
    mx, my = math.fsum(a * c for a, c in zip(x, w)) / sw, math.fsum(b * c for b, c in zip(y, w)) / sw
    cov = math.fsum(c * (a - mx) * (b - my) for a, b, c in zip(x, y, w))
    vx, vy = math.fsum(c * (a - mx) ** 2 for a, c in zip(x, w)), math.fsum(c * (b - my) ** 2 for b, c in zip(y, w))
    return None if vx <= 0 or vy <= 0 else cov * cov / (vx * vy)


def e1b_rho2(quarters):
    """Return E1b (information only): the |delta_f_sh| x F_fill weighted squared correlation per quarter.

    ``quarters`` maps a quarter to its ok-hedged books with ``delta_f_sh``, ``forward`` (F_fill), ``s_fill``,
    ``s_settle`` and the caller's ``beta`` and ``basket_return`` (the run's shock-gate beta and the equal-weight basket's
    fill-to-settle return). The correlated moves are ``(S_settle - F_fill) / F_fill`` and ``beta x R_m``.
    ``IndexForwardHedge`` becomes the second kind when ``rho2 >= 0.67`` in at least 3 of 4 quarters.
    """
    out = {}
    for quarter, books in quarters.items():
        usable = [b for b in books if all(b.get(k) is not None for k in ("delta_f_sh", "forward", "s_settle", "beta", "basket_return"))]
        if len(usable) < RHO2_MIN_BOOKS:
            out[quarter] = None
            continue
        out[quarter] = _weighted_rho2([(b["s_settle"] - b["forward"]) / b["forward"] for b in usable],
                                      [b["beta"] * b["basket_return"] for b in usable],
                                      [abs(b["delta_f_sh"]) * b["forward"] for b in usable])
    got = [v for v in out.values() if v is not None]
    return {"rho2": out, "second_kind": len(got) >= RHO2_QUARTERS and sum(v >= RHO2_PASS for v in got) >= RHO2_QUARTERS,
            "information_only": True}


def e4_ratio(e4_input, e2_ratio):
    """Return the single pooled E4 ratio (ADR-0312 A1 section 8) from the walk's ``e4_input``, else ``not available``.

    ``e4_input`` is ``summary["direction"]["e4_input"]`` of the edge walk (or the walk summary holding it):
    ``{"warm": bool, "w_dir_shrunk": None | {"value", "se", "df", "bands": {"0.9": {"lcb", "ucb"}}}, ...}``.
    ``None`` anywhere on the path (no input, or a pool that is not warm) is ``E4 not available: pool not warm``.
    Lower band above 0: ``clip(1 - lcb, 0, 1)``; else upper band below 0.25: 1; else the E2 ratio.
    """
    if isinstance(e4_input, dict) and "direction" in e4_input:
        e4_input = (e4_input["direction"] or {}).get("e4_input")
    shrunk = e4_input.get("w_dir_shrunk") if isinstance(e4_input, dict) else None
    if not shrunk:
        return {"status": "E4 not available: pool not warm", "ratio": e2_ratio}
    band = shrunk["bands"][E4_LEVEL]
    if band["lcb"] > 0:
        return {"status": "lower_band_positive", "ratio": min(max(1.0 - band["lcb"], 0.0), 1.0)}
    if band["ucb"] < E4_UPPER:
        return {"status": "upper_band_small", "ratio": 1.0}
    return {"status": "inconclusive", "ratio": e2_ratio}


CONDITIONS = ("none", "shock_held_share", "vol_regime")


def register_attempts(path, formulation, ratios=RATIOS):
    """Record every declared attempt BEFORE any is read: 4 ratios x 3 conditions, the program twin, E1 (14).

    The registry id is derived from the knobs, so a rerun adds none. Only the ``none`` condition is READ here
    (E3 is ``not_run``); the others are declared because the multiplicity guard counts what was declared.
    """
    registry = AttemptRegistry(path)
    base = {"family": FAMILY, "formulation": formulation}
    for ratio in ratios:
        for condition in CONDITIONS:
            registry.record({**base, "kind": "replay_sweep", "mode": "replay", "ratio": ratio, "condition": condition,
                             "read": "E2"})
    registry.record({**base, "kind": "program_twin", "mode": "program", "ratio": None, "condition": "none",
                     "read": "E2"})
    registry.record({**base, "kind": "e1", "mode": "replay", "ratio": 1.0, "condition": "none", "read": "E1"})
    return registry.count(family=FAMILY, formulation=formulation)


def _load(path):
    document = json.loads(Path(path).read_text())
    _refuse_check_week(document)
    return document


def _ratio_name(ratio):
    return f"e2_r{round(ratio * 100):03d}"


def read(stress_root, formulation, quarters, registry_path, greeks=None, books=None, e4_input=None, n_boot=10000):
    """Return the verdict document for one formulation over the quarters' stress outputs.

    ``quarters`` maps a cohort directory name to its ``YYYYQn`` label; ``greeks`` maps a quarter to its
    ``greeks-summary.json``; ``books`` maps a quarter to its ok-hedged book rows (for E1's ``d_t`` and E1b).
    """
    k_declared = register_attempts(registry_path, formulation)       # declared before anything is read
    root = Path(stress_root)

    def result(cohort, scenario):
        document = _load(root / cohort / scenario / f"{formulation}.json")
        if document.get("result") is None:
            raise ValueError(f"scenario_failed: {cohort}/{scenario} carries no result ({document.get('error') or document.get('status')})")
        return document["result"]

    sweep, series = {}, {}
    for ratio in RATIOS:
        scenario = SCENARIO_PREFIX + f"{round(ratio * 100):03d}"
        reads = []
        for cohort, quarter in quarters.items():
            reads.append(quarter_read(quarter, result(cohort, "control"), result(cohort, scenario),
                                      result(cohort, scenario + "_carry")))
        sweep[ratio] = {"reads": reads, "e2": e2_verdict(reads)}
        series[_ratio_name(ratio)] = {r["quarter"]: r["d_t"] for r in reads}
    passing = [r for r in RATIOS if sweep[r]["e2"]["verdict"] == "pass"]
    adopted_ratio = passing[0] if passing else None                  # the SMALLEST passing ratio, never the best median
    out = {"formulation": formulation, "family": FAMILY, "k_declared": k_declared,
           "development_evidence_only": True, "attempts_not_run": ["conditions (E3)", "program twin"],
           "sweep": {str(r): {"e2": v["e2"], "reads": [{k: x for k, x in rd.items() if k != "d_t"} for rd in v["reads"]]}
                     for r, v in sweep.items()},
           "adopted_ratio": adopted_ratio}
    if greeks:
        direction = {q: _load(p)["methods"][formulation]["direction_read"] for q, p in greeks.items()}
        out["e1"] = e1_read(None, direction)
        if books:
            clock = {r["quarter"]: [d for d, _ in r["d_t"]] for r in sweep[RATIOS[-1]]["reads"]}
            rows = {q: books_from_csv(p, formulation) for q, p in books.items()}
            series["e1"] = {q: e1_series(rows[q], clock[q]) for q in rows}
            absent = [f for f in E1B_NEEDS if not all(f in r for rs in rows.values() for r in rs)]
            out["e1b"] = ({"status": "E1b unattached: needs " + ", ".join(absent) + " from mio_greeks"} if absent else
                          e1b_rho2({q: [b for b in rs if b.get("forward_hedge_status") == "ok"] for q, rs in rows.items()}))
    out["e3"] = {"status": "not_run", "why": "conditions need decision-time inputs (shock-held share, vol regime) that the "
                 "replay does not attach in S3; the condition attempts are declared, not read"}
    out["e4"] = e4_ratio(_load(e4_input) if e4_input else None, adopted_ratio)
    adopted_name = _ratio_name(adopted_ratio) if adopted_ratio else None
    out["bar"] = bar_reads(series, adopted_name, k_declared, n_boot=n_boot, also=("e1",) if "e1" in series else ())
    out["adopted"] = bool(adopted_ratio and out["bar"]["passed"])
    return out


def main(argv=None):
    """Write the verdict JSON: ``hedge_evidence.py ROOT FORMULATION COHORT=QUARTER... --registry R --out V``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stress_root")
    parser.add_argument("formulation")
    parser.add_argument("quarters", nargs="+", help="COHORT=YYYYQn")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--greeks", nargs="*", default=[], help="YYYYQn=path/to/greeks-summary.json")
    parser.add_argument("--books", nargs="*", default=[], help="YYYYQn=path/to/books.csv (mio_greeks output)")
    parser.add_argument("--e4-input")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        sys.stderr.write("verdict file exists: a fresh path is required\n")
        return 1
    verdict = read(args.stress_root, args.formulation, dict(q.split("=") for q in args.quarters), args.registry,
                   greeks=dict(g.split("=") for g in args.greeks) or None,
                   books=dict(b.split("=") for b in args.books) or None, e4_input=args.e4_input)
    out.write_text(json.dumps(verdict, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
