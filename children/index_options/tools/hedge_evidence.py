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
* program mode (amendment section 3) has its OWN read, identical in form to E2: ``program_quarter_read`` /
  ``program_verdict`` read the program twin against its unhedged twin with the same ``B_q``, the same numbers and
  the same bar; the books differ by design (the program re-chooses), so only ``mark_sensitive`` stops a quarter
  counting. Beside it, information only, ``contracts_ratio`` and ``peak_reserve_ratio`` (hedged over unhedged); a
  quarter whose drawdown is not cut (``dDD <= 0``) while contracts rose is ``re_levered`` and the twin FAILS whatever
  its P&L. ``timing_check`` (M7) evaluates, from two runs' ``decision_weeks`` check documents, the median solved-week
  slowdown limit (1.5x) and "no week that solved unhedged may time out hedged";
* ``control_block_mismatch`` (a control twin whose blocks differ from its arm's) belongs to ADR-0313's read tool and
  is deferred there, not checked here;
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
           "e2_verdict", "bar_reads", "e1_read", "e1b_rho2", "e4_ratio", "register_attempts", "program_quarter_read",
           "program_verdict", "register_program_attempt", "timing_check", "read_program", "g5_status_weeks", "main"]

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


def _ratio(numerator, denominator):
    """Return ``numerator / denominator``, ``None`` when the denominator is missing or zero."""
    return None if not denominator or numerator is None else numerator / denominator


def program_quarter_read(quarter, control, program, carry=None):
    """Return one account's program-mode read: the E2 numbers plus the re-levering reads.

    The program twin chooses its own books, so ``books_differ`` is reported and does not stop a quarter counting; a
    quarter counts unless ``mark_sensitive``. ``contracts_ratio`` and ``peak_reserve_ratio`` are the result's
    ``contracts`` and ``peak_reserve`` hedged over unhedged (information only); ``re_levered`` is the amendment's
    fail condition: the drawdown is not cut (``dDD <= 0``) while contracts rose, read on COUNTING quarters only (a
    ``mark_sensitive`` quarter is uncounted and never fails the verdict).

    Known conservative bias (review of d864c39): program mode charges a self-hedged book's forward leg on GROSS
    shares per item (``program_gross_units``), not the net units the replay trades, so ``program_cost_excess_usd``
    is positive (3,605 gross against 42 net shares in the review's run, +392 dollars); the program twin's cost is
    overstated, never understated.
    """
    out = quarter_read(quarter, control, program, carry)
    out["contracts_ratio"] = _ratio(program.get("contracts"), control.get("contracts"))
    out["peak_reserve_ratio"] = _ratio(program.get("peak_reserve"), control.get("peak_reserve"))
    rose = program.get("contracts") is not None and control.get("contracts") is not None \
        and program["contracts"] > control["contracts"]
    out["counts"] = not out["mark_sensitive"]
    out["re_levered"] = bool(out["counts"] and out["dDD"] <= 0 and rose)     # a COUNTING quarter only: a mark-sensitive one is not read
    return out


def program_verdict(reads):
    """Return the program twin's verdict: the E2 rule over the reads, forced to ``fail`` by any re-levered quarter."""
    out = e2_verdict(reads)
    levered = [r["quarter"] for r in reads if r.get("re_levered")]
    if levered:
        out["reasons"].append(f"re_levered: drawdown not cut while contracts rose in {levered}")
        out["verdict"] = "fail"
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


def register_program_attempt(path, formulation, ratio=None):
    """Record the program twin's attempt at ``ratio`` (``None``: the one :func:`register_attempts` declared) and count the family."""
    registry = AttemptRegistry(path)
    registry.record({"family": FAMILY, "formulation": formulation, "kind": "program_twin", "mode": "program",
                     "ratio": ratio, "condition": "none", "read": "E2"})
    return registry.count(family=FAMILY, formulation=formulation)


TIMING_SLOWDOWN, SOLVED_STATUSES = 1.5, ("selected", "cash")
TIMEOUT_STATUS = "timeout_skipped"


def _week_timing(result):
    """Return ``(status, seconds)`` of the one solved week of a ``decision_weeks`` check document."""
    if "decision_weeks" not in result:
        raise ValueError("timing_needs_check_documents: a timing pair is two decision_weeks check documents")
    week = result["decision_weeks"][0]
    decision = next(d for d in result["decisions"] if d["date"] == week)
    seconds = math.fsum(c["seconds"] for c in decision.get("certificates", []) if c.get("seconds") is not None)
    return decision["status"], seconds if decision.get("certificates") else None


def timing_check(pairs, limit=TIMING_SLOWDOWN):
    """Return the M7 timing check over ``pairs`` of ``{week, unhedged, hedged}`` one-week check documents.

    PASS when the median solved-week time of the hedged twin is at most ``limit`` times the unhedged twin's (weeks
    solved by BOTH) and no week that solved unhedged timed out hedged; ``insufficient`` without a week solved by both.
    No real run is made here: the pairs come from the pre-registered set run through the ADR-0297 ``--check`` route.
    """
    solved, new_timeouts = [], []
    for pair in pairs:
        (u_status, u_s), (h_status, h_s) = _week_timing(pair["unhedged"]), _week_timing(pair["hedged"])
        if u_status in SOLVED_STATUSES and h_status == TIMEOUT_STATUS:
            new_timeouts.append(pair["week"])
        if u_status in SOLVED_STATUSES and h_status in SOLVED_STATUSES and u_s is not None and h_s is not None:
            solved.append((u_s, h_s))
    out = {"weeks": len(pairs), "solved_by_both": len(solved), "new_timeouts": new_timeouts, "limit": limit,
           "median_unhedged_s": None, "median_hedged_s": None, "slowdown": None}
    if not solved:
        out["verdict"] = "fail" if new_timeouts else "insufficient"
        return out
    out["median_unhedged_s"] = statistics.median(u for u, _ in solved)
    out["median_hedged_s"] = statistics.median(h for _, h in solved)
    out["slowdown"] = _ratio(out["median_hedged_s"], out["median_unhedged_s"])
    out["verdict"] = "pass" if not new_timeouts and out["slowdown"] is not None and out["slowdown"] <= limit else "fail"
    return out


def read_program(controls, programs, formulation, registry_path, carries=None, others=None, timing=None, n_boot=10000):
    """Return the program twin's verdict document: ``B_q`` per quarter, the re-levering reads, the bar and the timing check.

    Parameters
    ----------
    controls, programs : dict
        ``{YYYYQn: method result}`` of the unhedged twin and the program-mode twin (same account, same clocks).
    carries : dict, optional
        ``{YYYYQn: program twin with every mark on the carry ratio}`` for the ``mark_sensitive`` read.
    others : dict, optional
        ``{attempt name: {account: [(date, d_t)]}}`` of the other declared attempts that were read, so the bar's
        max statistic spans them; the program twin's cell is ``program_twin``.
    timing : list, optional
        ``timing_check`` pairs.
    """
    k_declared = register_program_attempt(registry_path, formulation)       # declared before anything is read
    reads = [program_quarter_read(q, controls[q], programs[q], (carries or {}).get(q)) for q in sorted(programs)]
    out = {"formulation": formulation, "family": FAMILY, "mode": "program", "k_declared": k_declared,
           "development_evidence_only": True, "one_program_ratio_only": True,
           "reads": [{k: v for k, v in r.items() if k != "d_t"} for r in reads], "program": program_verdict(reads)}
    series = {"program_twin": {r["quarter"]: r["d_t"] for r in reads}, **(others or {})}
    out["bar"] = bar_reads(series, "program_twin", k_declared, n_boot=n_boot)
    out["adopted"] = bool(out["program"]["verdict"] == "pass" and out["bar"]["passed"])
    if timing is not None:
        out["timing"] = timing_check(timing)
    return out


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


def _result_of(path):
    """Return the ``result`` of a method document file (the shape ``read`` takes), refusing a check-week document."""
    document = _load(path)
    if document.get("result") is None:
        raise ValueError(f"scenario_failed: {path} carries no result ({document.get('error') or document.get('status')})")
    return document["result"]


def _pairs(items):
    """Parse ``KEY=path`` words into a dict."""
    return dict(item.split("=", 1) for item in items)


def main_program(argv):
    """Write the program twin's verdict JSON (``hedge_evidence.py program FORMULATION --controls Q=path ... --out V``)."""
    parser = argparse.ArgumentParser(prog="hedge_evidence.py program", description=read_program.__doc__)
    parser.add_argument("formulation")
    parser.add_argument("--controls", nargs="+", required=True, help="YYYYQn=path to the unhedged twin's method document")
    parser.add_argument("--programs", nargs="+", required=True, help="YYYYQn=path to the program-mode twin's document")
    parser.add_argument("--carries", nargs="*", default=[], help="YYYYQn=path to the program twin on the carry-ratio marks")
    parser.add_argument("--timing", nargs="*", default=[], help="WEEK=unhedged_path,hedged_path (decision_weeks check documents)")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        sys.stderr.write("verdict file exists: a fresh path is required\n")
        return 1
    controls = {q: _result_of(p) for q, p in _pairs(args.controls).items()}
    programs = {q: _result_of(p) for q, p in _pairs(args.programs).items()}
    if set(controls) != set(programs):
        sys.stderr.write("controls and programs must name the same quarters\n")
        return 1
    timing = [{"week": week, "unhedged": _result_of(u), "hedged": _result_of(h)}
              for week, both in _pairs(args.timing).items() for u, h in [both.split(",", 1)]]
    verdict = read_program(controls, programs, args.formulation, args.registry,
                           carries={q: _result_of(p) for q, p in _pairs(args.carries).items()} or None,
                           timing=timing or None)
    out.write_text(json.dumps(verdict, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")
    return 0

_G5_METHODS = ("w1_010_shock10_lp", "w1_003_shock10_lp")
_G5_SOLVED = ("selected", "cash")
_G5_TIMEOUT = "timeout_skipped"
_G5_STATUSES = (*_G5_SOLVED, _G5_TIMEOUT, "existing_cap_breach_cash", "no_entry_fill_session",
                "no_eligible_candidates", "shock_risk_input", "shock_held_breach")
_G5_QUARTERS = ("2019Q4", "2020Q1", "2021Q1", "2021Q2")
_G5_SOURCE_ROOT = Path("/home/russell/data/index_options/full-span-rerun-20261105/adr0313-integrated-20261110")
_G5_SOURCES = {
    "2019Q4": (
        "docs/base-stitched/2019Q4-run.json",
        "dc6a02293a19afe6488b69d9537708befc1b633a5d3ee876ec2aa7ee9d17e9bd",
        "runs/base/2019Q4-stitched/run/adr0313-a0010-2019q4-stitched-run-2019-12-31-dc533644",
        "4a038c7db59e7875e35ac21e44c5bdb7052c5a851450cc784014df9fd8774b75",
        "3a0540a3ff5188c9530a2033dd6812dc8de948edfd8b5cc65f09fd5659a2fdbe",
    ),
    "2020Q1": (
        "docs/base-restart1/2020Q1-run.json",
        "d38b81e2cfae8751bc990babe074b06328accae9c84d92beda67d99bff565ddf",
        "runs/base/2020Q1/restart1/run/adr0313-a0010-2020q1-restart1-run-2020-03-31-94c5fb48",
        "691b92d6680487e06807eaa453ba538c4c162d7091bcd45a1f6137d474dac0b1",
        "014c103e57aa19444fb46486efecf60cc02d9e8974e119c7135809106fd67ba7",
    ),
}


def _g5_method_rows(decisions, method_id, quarter):
    if not isinstance(decisions, list):
        raise ValueError("g5_decisions")
    projected = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("g5_decisions")
        day = decision.get("date")
        try:
            parsed = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise ValueError("g5_decision_date") from None
        if (parsed.isoformat() != day or day >= DEVELOPMENT_SEAL
                or f"{parsed.year}Q{(parsed.month - 1) // 3 + 1}" != quarter):
            raise ValueError("g5_decision_date")
        if day in projected:
            raise ValueError("g5_duplicate_decision")
        status = decision.get("status")
        if status not in _G5_STATUSES:
            raise ValueError("g5_decision_status")
        selection, certificates = decision.get("selection"), decision.get("certificates")
        solve_present = (isinstance(selection, dict) and isinstance(certificates, list)
                         and bool(certificates) and all(isinstance(cert, dict) for cert in certificates))
        if (not solve_present and (selection is not None or certificates is not None or status == "selected")
                or solve_present and status not in (*_G5_SOLVED, _G5_TIMEOUT)):
            raise ValueError("g5_solve_evidence")
        projected[day] = {"method_id": method_id, "date": day, "status": status,
                          "solve_present": solve_present}
    return projected


def g5_status_weeks(receipt, quarter):
    """Project the two registered base methods and declare their deterministic G5 dates."""
    if quarter not in _G5_QUARTERS:
        raise ValueError("g5_quarter")
    if not isinstance(receipt, dict) or receipt.get("complete") is not True:
        raise ValueError("g5_incomplete_close")
    results = receipt.get("results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise ValueError("g5_results")
    by_method = {}
    for method_id in _G5_METHODS:
        matches = [item for item in results if item.get("id") == method_id]
        if len(matches) != 1:
            raise ValueError("g5_method_missing" if not matches else "g5_method_duplicate")
        method, = matches
        result = method.get("result")
        if (method.get("status") != "completed" or method.get("replay_parity") is not True
                or not isinstance(result, dict)):
            raise ValueError("g5_incomplete_replay")
        by_method[method_id] = _g5_method_rows(result.get("decisions"), method_id, quarter)
    first, second = by_method.values()
    if first.keys() != second.keys():
        raise ValueError("g5_calendar_mismatch")
    dates = sorted(first)
    joint_solves = [day for day in dates if all(rows[day]["solve_present"] and rows[day]["status"] in _G5_SOLVED
                                               for rows in (first, second))]
    if len(joint_solves) < 3:
        raise ValueError("fewer_than_three_joint_solves")
    timeouts = {day for day in dates if any(rows[day]["status"] == _G5_TIMEOUT for rows in (first, second))}
    return {"weeks": sorted(set(joint_solves[:3]) | timeouts),
            "rows": [rows[day] for rows in by_method.values() for day in dates]}


def _g5_attested_weeks(root, source, quarter):
    from hashlib import sha256

    from dskit.pipeline.driver import RunAttestation, resolve_json_artifact

    doc, doc_sha, run, doc_identity, receipt_sha = source
    try:
        if sha256((root / doc).read_bytes()).hexdigest() != doc_sha:
            raise ValueError("g5_document_pin")
    except OSError:
        raise ValueError("g5_document_pin") from None
    run_dir = root / run
    manifest = RunAttestation(run_dir).attested_output("close_packet", "receipt", doc_identity)
    if not isinstance(manifest, dict) or manifest.get("sha256") != receipt_sha:
        raise ValueError("g5_close_attestation")
    try:
        receipt = resolve_json_artifact(run_dir, manifest)
    except (OSError, ValueError):
        raise ValueError("g5_close_artifact") from None
    return {"document_sha256": doc_sha, "close_sha256": receipt_sha, **g5_status_weeks(receipt, quarter)}


def _main_g5_status(argv):
    parser = argparse.ArgumentParser(prog="hedge_evidence.py g5-status")
    parser.parse_args(argv)
    declaration = {"pending": [quarter for quarter in _G5_QUARTERS if quarter not in _G5_SOURCES],
                   "retrospective_timing": ["2020-03-09"], "quarters": {}}
    try:
        for quarter, source in _G5_SOURCES.items():
            declaration["quarters"][quarter] = _g5_attested_weeks(_G5_SOURCE_ROOT, source, quarter)
    except ValueError as exc:
        sys.stderr.write(f"{quarter}: {exc}\n")
        return 1
    print(json.dumps(declaration, sort_keys=True, allow_nan=False))
    return 0


# ADR-0313 requires independent clearance of the exact operational bundle.
_G5_CLEAN_RETRY_BUNDLE = (
    "926686466516a18bf7364fcfb8b20e16388e1f8ee8773dda4a6f9a74400be652",
    "637c8c16d1b93b9cf38668041cf50e17d1e2fac6d924b3eca908768965314ad8",
)
_G5_PROSPECTIVE_BUNDLE = (
    "5b199a4683b1de6ff5d209d7ae2c24e4aa236a3fd623290dbaf9d4b5fbf4569c",
    "0095f89e36553ac16093f8a56df2384fbb578a11ea6fc4982afcebd74b906a7b",
)
_G5_APPROVED_BUNDLES = frozenset({_G5_CLEAN_RETRY_BUNDLE, _G5_PROSPECTIVE_BUNDLE})
_G5_CLEAN_RETRY_WEEKS = {"2020Q1": {"2020-03-09": "retrospective_clean_rerun"}}

# ADR-0313's status-only declaration receipt is the prospective matrix authority.
_G5_TIMING_WEEKS = {
    "2019Q4": dict.fromkeys(("2019-10-01", "2019-10-07", "2019-10-14"), "jointly_solved"),
    "2020Q1": {
        **dict.fromkeys(("2020-01-02", "2020-01-06", "2020-01-13"), "jointly_solved"),
        **dict.fromkeys(("2020-03-23", "2020-03-30"), "timeout_union"),
        "2020-03-09": "retrospective_timeout",
    },
}

_G5_TIMING_ROLES = {
    "replay-hedge": "kelly025_top100_search_replay_hedge_lp",
    "exact-search": "kelly025_top100_search_timing_twin_lp",
    "no-search": "kelly025_top100_nosearch_timing_control_lp",
}
_G5_TIMEOUT_REASONS = ("gap", "missing_bound", "no_incumbent")
_G5_UTILITY_OUTCOMES = ("relaxation_certified", "certified", "declined", "relaxation_failed")


class _G5TimingRefusal(ValueError):
    """A fixed diagnostic from the timing evidence reader."""


def _g5_pinned_bytes(root, pin, refusal):
    from hashlib import sha256

    try:
        content = (root / pin["path"]).read_bytes()
        if sha256(content).hexdigest() == pin["sha256"]:
            return content
    except (OSError, TypeError, KeyError, ValueError):
        pass
    raise _G5TimingRefusal(refusal)


def _g5_timing_json(content):
    def unique(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise _G5TimingRefusal("g5_timing_duplicate_key")
        return obj

    return json.loads(content, object_pairs_hook=unique)


def _g5_timing_strategy(decision, kelly):
    selection, certificates = decision.get("selection"), decision.get("certificates")
    if selection is None and certificates is None and decision["status"] == "cash":
        return {"solve_present": False, "certificate_present": False, "timeout": None,
                "certification_outcome": None, "certification_gap_available": False, "uncertified_accepted": False,
                "search": None, "warm_termination": None, "explored_nodes": None}
    if (not isinstance(selection, dict) or not isinstance(certificates, list)
            or len(certificates) != 1 or not isinstance(certificates[0], dict)):
        raise _G5TimingRefusal("g5_timing_solve")
    certificate, = certificates
    outcome, gap = certificate["strategy_outcome"], certificate["gap"]
    if outcome not in _G5_UTILITY_OUTCOMES or (gap is not None and not _g5_number(gap)):
        raise _G5TimingRefusal("g5_timing_certificate")
    timeout = selection.get("timeout")
    strategy = decision["kelly"]
    uncertified = strategy.get("uncertified")
    accepted = (kelly.get("accept_uncertified") is True and timeout == "gap" and outcome == "declined"
                and isinstance(selection.get("candidate"), dict)
                and isinstance(uncertified, dict) and uncertified.get("outcome") == "declined")
    if ("timeout" in selection and timeout not in _G5_TIMEOUT_REASONS
            or (decision["status"] == "timeout_skipped") != (timeout is not None and not accepted)
            or timeout is not None and certificate["termination"] != "maxTimeLimit"
            or uncertified is not None and not accepted):
        raise _G5TimingRefusal("g5_timing_timeout")
    search, warm = strategy.get("search"), strategy.get("warm_start")
    if "search" in kelly:
        if ("search" not in strategy or "warm_start" not in strategy
                or (search is None) != (outcome == "relaxation_failed")
                or outcome == "certified" and warm is None
                or outcome in ("relaxation_certified", "relaxation_failed") and warm is not None):
            raise _G5TimingRefusal("g5_timing_strategy")
    elif "search" in strategy or "warm_start" in strategy:
        raise _G5TimingRefusal("g5_timing_strategy")
    if search is not None:
        iterations, failed, seconds = (search[key] for key in ("iterations", "failed", "seconds"))
        if (type(iterations) is not int or type(failed) is not int
                or not 0 <= failed <= iterations <= kelly["search"]["iterations"] or not _g5_number(seconds)):
            raise _G5TimingRefusal("g5_timing_search")
        search = {"iterations": iterations, "failed": failed, "seconds": seconds}
    termination = None if warm is None else warm["termination"]
    if warm is not None and termination not in (
            "optimal", "maxIterations", "maxTimeLimit", "infeasible", "infeasibleOrUnbounded",
            "unbounded", "objectiveLimit", "minStepLength", "interrupted", "error", "unknown"):
        raise _G5TimingRefusal("g5_timing_warm")
    return {"solve_present": True, "certificate_present": True, "timeout": timeout,
            "certification_outcome": outcome, "certification_gap_available": gap is not None,
            "uncertified_accepted": accepted,
            "search": search, "warm_termination": termination, "explored_nodes": None}


def _g5_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _g5_timing_close(receipt, quarter, week, method, expected_tail, capped, expected_calendar):
    if receipt.get("complete") is not True:
        raise _G5TimingRefusal("g5_timing_incomplete")
    entry, = receipt["results"]
    result, audit = entry["result"], receipt["audit"]
    if (entry["id"] != method["id"] or audit["method_ids"] != [method["id"]]
            or result["method"] != method):
        raise _G5TimingRefusal("g5_timing_role")
    if (entry["status"] != "completed" or entry["replay_parity"] is not True
            or type(result["unbounded_books"]) is not int or result["unbounded_books"] != 0
            or audit.get("tail_capped_at") != (expected_tail if capped else None)):
        raise _G5TimingRefusal("g5_timing_incomplete")
    if result["decision_weeks"] != [week]:
        raise _G5TimingRefusal("g5_timing_week")
    calendar = audit["calendar"]
    if calendar != expected_calendar or not calendar:
        raise _G5TimingRefusal("g5_timing_tail")
    decisions = result["decisions"]
    dates = [row["date"] for row in decisions]
    if (not dates or dates != audit["decision_dates"] or dates != sorted(set(dates))
            or not set(dates) <= set(calendar) or week not in dates
            or any(f"{day[:4]}Q{(int(day[5:7]) - 1) // 3 + 1}" != quarter for day in dates)):
        raise _G5TimingRefusal("g5_timing_week")
    selected = None
    for row in decisions:
        if row["date"] == week:
            selected = row
        elif (row["status"] != "week_not_selected" or row.get("selection") is not None
              or row.get("certificates") is not None):
            raise _G5TimingRefusal("g5_timing_week")
    if selected["status"] not in (*_G5_SOLVED, _G5_TIMEOUT):
        raise _G5TimingRefusal("g5_timing_status")
    return {"status": selected["status"], "settlement_tail_complete": True,
            "tail_end_before": expected_tail, "tail_last_session": calendar[-1],
            **_g5_timing_strategy(selected, method["kelly"])}


def _g5_timing_document(root, cell, role, week, checkout_path):
    from copy import deepcopy

    from dskit.pipeline.document import PipelineDocument
    from index_options.ledger_studies import _is_exact_kelly_search
    from index_options.mio_contract import MIOStudyContract

    pin = cell["files"]["run"]
    raw = _g5_timing_json(_g5_pinned_bytes(root, pin, "g5_timing_document_pin"))
    if PipelineDocument.from_obj(raw).hash != pin["identity"]:
        raise _G5TimingRefusal("g5_timing_document_pin")
    pipeline = raw["pipeline"]
    study = pipeline["plan"]["params"]["study"]
    close_node = pipeline["close_packet"]
    if (close_node["uses"] != "dskit.pipeline.study_protocol:StudyPacketClose"
            or close_node["params"]["study"] != study
            or study["registry"]["path"] != cell["registry"]
            or raw["outputs"]["run_root"] != cell["paths"]["run"]["root"]
            or any(node["params"]["study"] != study for node in pipeline.values()
                   if "study" in node.get("params", {}))
            or pipeline.get("study", {}).get("params", {}).get("recorded") is not None):
        raise _G5TimingRefusal("g5_timing_document")
    code = study["code"]
    if not code or any(not Path(path).is_absolute() or not Path(path).resolve().is_relative_to(checkout_path)
                       for path in code):
        raise _G5TimingRefusal("g5_timing_build")
    domain = study["domain"]
    params = domain["params"]
    method, = params["methods"]
    if (method["id"] != _G5_TIMING_ROLES[role] or cell["method"] != method["id"]
            or params["decision_weeks"] != [week]):
        raise _G5TimingRefusal("g5_timing_role")
    if params["bridge"]["protected_end_before"] != DEVELOPMENT_SEAL:
        raise _G5TimingRefusal("g5_timing_seal")
    tail, capped = MIOStudyContract.settlement_tail(study)
    if tail != cell["tail_end_before"] or tail > DEVELOPMENT_SEAL:
        raise _G5TimingRefusal("g5_timing_tail")
    kelly = method["kelly"]
    if role == "replay-hedge":
        if method["hedge"]["mode"] != "replay" or not _is_exact_kelly_search(method, "hedge"):
            raise _G5TimingRefusal("g5_timing_role")
    elif "hedge" in method or "hedge_parity" in params:
        raise _G5TimingRefusal("g5_timing_role")
    elif role == "exact-search":
        if not _is_exact_kelly_search(method):
            raise _G5TimingRefusal("g5_timing_role")
    elif "search" in kelly or "warm_start" in kelly:
        raise _G5TimingRefusal("g5_timing_role")
    base = deepcopy(study)
    base.pop("registry")  # Fresh timing arms have separate registries.
    base_params = base["domain"]["params"]
    base_params.pop("hedge_parity", None)
    base_method, = base_params.pop("methods")
    base_method.pop("id")
    base_method.pop("hedge", None)
    base_method["kelly"].pop("search", None)
    base_method["kelly"].pop("warm_start", None)
    return pin, study, method, tail, capped, (base, base_method, cell["asof"], raw.get("clock"), raw.get("schedule"))


def _g5_timing_calendar(study, tail):
    from index_options.mio_contract import MIOStudyContract
    from index_options.mio_execution_stress import _packet_sessions

    domain = study["domain"]
    packet = MIOStudyContract.packet(study)
    days = _packet_sessions(domain["sources"]["sessions"], domain["history_start"], tail)
    return [day for day in days if packet["start"] <= day < tail]


def _g5_timing_command(matrix, root, matrix_sha):
    from shlex import join

    scripts = matrix["scripts"]
    mount, interpreter = matrix["bundle_mount"], matrix["interpreter"]
    checkout_path = Path(matrix["checkout_path"])
    if not all(Path(path).is_absolute() for path in (mount["path"], interpreter, checkout_path)):
        raise _G5TimingRefusal("g5_timing_command")
    command = join(["findmnt", "--mountpoint", mount["path"], "-n", "-o", "UUID"])
    command += " | " + join(["grep", "-qx", mount["uuid"]])
    for role in ("preflight", "matrix"):
        pin = scripts[role]
        script = Path(pin["path"])
        if not script.is_absolute() or script.parent.resolve() != (root / "ops").resolve() or script.name != f"{role}.py":
            raise _G5TimingRefusal("g5_timing_command")
        _g5_pinned_bytes(root, pin, "g5_timing_script_pin")
        command += " && " + join(["printf", "%s\\n", f"{pin['sha256']}  {script}"])
        command += " | sha256sum --status -c -"
    invoke = [interpreter, "-B", scripts["preflight"]["path"]]
    hashes = {"checkout": matrix["checkout"], "manifest": matrix_sha,
              **{role: scripts[role]["sha256"] for role in ("preflight", "matrix")}}
    return checkout_path.resolve(), command, invoke, hashes


def _g5_timing_usage(root, matrix_root, jobs, cell_key, cell, run_id, run_pin, invocation):
    import re
    from shlex import join
    from hashlib import sha256

    job_id = cell["job_ids"]["run"]
    job = jobs[job_id]
    receipt = _g5_timing_json(_g5_pinned_bytes(root, run_pin["receipt"], "g5_timing_scheduler_pin"))
    command = job["command"]
    command_sha256 = sha256((command + "\0" + str(job.get("mac_command", ""))).encode()).hexdigest()
    hashes = job.get("hashes", {})
    elapsed = receipt["elapsed_s"]
    if (receipt["job_id"] != job_id or receipt["run_id"] != run_id
            or receipt["command_sha256"] != command_sha256 or receipt["hashes"] != hashes
            or receipt["host"] != "wsl" or receipt["status"] != "done"
            or type(receipt["exit_code"]) is not int or receipt["exit_code"] != 0
            or not _g5_number(elapsed) or elapsed == 0 or job["allowed_hosts"] != ["wsl"]):
        raise _G5TimingRefusal("g5_timing_scheduler")
    paths = cell["paths"]["run"]
    run = (root / run_pin["run_dir"]).resolve()
    declared_root = (matrix_root / paths["root"]).resolve()
    usage = (root / run_pin["usage"]["path"]).resolve()
    if (run.parent != declared_root or usage != (matrix_root / paths["usage"]).resolve()):
        raise _G5TimingRefusal("g5_timing_path")
    checkout_path, prechecks, invoke, common_hashes = invocation
    expected = prechecks + " && " + join([*invoke, cell_key, "run", common_hashes["manifest"]])
    if hashes != {**common_hashes, "document": cell["files"]["run"]["sha256"]}:
        raise _G5TimingRefusal("g5_timing_scheduler_pins")
    env = {"PYTHONPATH": f"{checkout_path}:{checkout_path}/children/index_options",
           "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "LC_ALL": "C"}
    if command != expected or Path(job["cwd"]).resolve() != checkout_path or job["env"] != env:
        raise _G5TimingRefusal("g5_timing_command")
    usage_path = paths["usage"]
    if job["output_files"] != [usage_path]:
        raise _G5TimingRefusal("g5_timing_scheduler_usage")
    last = receipt["history"][-1]
    if (any(last.get(key) != receipt[key] for key in ("host", "status", "attempt", "exit_code", "elapsed_s"))
            or last["output_hashes"] != {usage_path: run_pin["usage"]["sha256"]}):
        raise _G5TimingRefusal("g5_timing_scheduler_usage")
    data = _g5_pinned_bytes(root, run_pin["usage"], "g5_timing_usage_pin")
    match = re.fullmatch(rb"peak_rss_kib=([1-9][0-9]*) wall_s=([0-9]+\.[0-9]{2})\n", data)
    if match is None:
        raise _G5TimingRefusal("g5_timing_usage")
    peak_rss_kib, wall_seconds = int(match[1]), float(match[2])
    if not _g5_number(wall_seconds) or wall_seconds > elapsed + 0.01:
        raise _G5TimingRefusal("g5_timing_usage")
    return run, {"host": "wsl", "job_id": job_id, "command_sha256": command_sha256,
                 "scheduler_elapsed_s": elapsed, "wall_s": wall_seconds, "peak_rss_kib": peak_rss_kib}


def _g5_timing_read(root, evidence):
    import re

    from dskit.pipeline.base import value_hash
    from dskit.pipeline.driver import RunAttestation, resolve_json_artifact

    matrix_pin, jobs_pin = evidence["matrix"], evidence["jobs"]
    bundle = matrix_pin["sha256"], jobs_pin["sha256"]
    if bundle not in _G5_APPROVED_BUNDLES:
        raise _G5TimingRefusal("g5_timing_bundle_approval")
    matrix = _g5_timing_json(_g5_pinned_bytes(root, matrix_pin, "g5_timing_matrix_pin"))
    jobs_manifest = _g5_timing_json(_g5_pinned_bytes(root, jobs_pin, "g5_timing_jobs_pin"))
    code = evidence["code_commit"]
    if re.fullmatch(r"[0-9a-f]{40}", code) is None:
        raise _G5TimingRefusal("g5_timing_build")
    run_id = jobs_manifest["run_id"]
    if code != matrix["checkout"] or run_id != matrix["scheduler_run_id"]:
        raise _G5TimingRefusal("g5_timing_build")
    clean_retry = bundle == _G5_CLEAN_RETRY_BUNDLE
    declaration = _G5_CLEAN_RETRY_WEEKS if clean_retry else _G5_TIMING_WEEKS
    if matrix["weeks"] != declaration:
        raise _G5TimingRefusal("g5_timing_declaration")
    declared = {(quarter, week) for quarter, weeks in declaration.items()
                for week, designation in weeks.items() if designation != "retrospective_timeout"}
    jobs = {job["id"]: job for job in jobs_manifest["jobs"]}
    if len(jobs) != len(jobs_manifest["jobs"]):
        raise _G5TimingRefusal("g5_timing_duplicate_job")
    matrix_root = (root / evidence["matrix"]["path"]).parent
    invocation = _g5_timing_command(matrix, matrix_root, evidence["matrix"]["sha256"])
    cells, runs = matrix["cells"], evidence["runs"]
    if not cells or cells.keys() != runs.keys():
        raise _G5TimingRefusal("g5_timing_matrix")
    grouped, used_runs, used_jobs, used_registries = {}, set(), set(), set()
    for cell_key, cell in sorted(cells.items()):
        quarter, week, role = (cell[key] for key in ("quarter", "week", "role"))
        if (quarter not in _G5_QUARTERS or role not in _G5_TIMING_ROLES
                or (quarter, week) not in declared or cell["designation"] != matrix["weeks"][quarter][week]):
            raise _G5TimingRefusal("g5_timing_role")
        rows = grouped.setdefault((quarter, week), {})
        if role in rows:
            raise _G5TimingRefusal("g5_timing_duplicate_cell")
        document, study, method, tail, capped, base = _g5_timing_document(matrix_root, cell, role, week, invocation[0])
        pin = runs[cell_key]
        run, usage = _g5_timing_usage(root, matrix_root, jobs, cell_key, cell, run_id, pin, invocation)
        registry = (matrix_root / cell["registry"]).resolve()
        if run in used_runs or usage["job_id"] in used_jobs or registry in used_registries:
            raise _G5TimingRefusal("g5_timing_duplicate_run")
        used_runs.add(run)
        used_jobs.add(usage["job_id"])
        used_registries.add(registry)
        resolved = _g5_timing_json((run / "resolved.json").read_bytes())
        result = _g5_timing_json((run / "result.json").read_bytes())
        if (result["asof"] != cell["asof"] or resolved["asof"] != cell["asof"]
                or result["run_hash"] != resolved["run_hash"]
                or re.fullmatch(r"[0-9a-f]{64}", resolved["run_hash"]) is None
                or result["document_hash"] != document["identity"]
                or type(result["exit_code"]) is not int or result["exit_code"] != 0
                or result["prev_run"] or resolved["prev_run"]):
            raise _G5TimingRefusal("g5_timing_clock")
        attestation = RunAttestation(run)
        close = attestation.attested_output("close_packet", "receipt", document["identity"])
        if not isinstance(close, dict) or close.get("sha256") != pin["close_sha256"]:
            raise _G5TimingRefusal("g5_timing_attestation")
        receipt = resolve_json_artifact(run, close)
        row = _g5_timing_close(receipt, quarter, week, method, tail, capped,
                               _g5_timing_calendar(study, tail))
        if attestation.attested_output("close_packet", "receipt", document["identity"]) != close:
            raise _G5TimingRefusal("g5_timing_unstable")
        audit, provenance, plan = (receipt[key] for key in ("audit", "provenance", "plan"))
        inputs_sha = audit["inputs_sha256"]
        source_sha = value_hash(plan["source_pins"])
        bounds = plan["read_bounds"]
        if (plan["study"] != study or plan["runnable"] is not True
                or plan["sha256"] != provenance["plan_sha256"] or bounds != provenance["read_bounds"]
                or source_sha != provenance["source_pins_sha256"]
                or any(bounds[key]["end_before"] != tail for key in ("chain", "bars", "sessions"))
                or re.fullmatch(r"[0-9a-f]{64}", inputs_sha) is None or inputs_sha != provenance["inputs_sha256"]
                or audit.get("adjustments_sha256") != provenance.get("adjustments_sha256")
                or "recorded_from" in audit):
            raise _G5TimingRefusal("g5_timing_inputs")
        # inputs_sha256 includes local quote-index paths, so match the immutable source pins and bounds.
        identity = (base, audit["calendar"], audit["decision_dates"], source_sha, bounds,
                    audit.get("adjustments_sha256"))
        kelly = method["kelly"]
        rows[role] = identity, {
            **row, **usage, "method_id": method["id"], "document_sha256": document["sha256"],
            "document_identity": document["identity"], "close_sha256": pin["close_sha256"],
            "source_pins_sha256": source_sha, "inputs_sha256": inputs_sha,
            "search_node_cap": kelly.get("search", {}).get("node_limit"),
            "warm_cert_node_cap": kelly.get("warm_start", {}).get("node_limit"),
        }
    if grouped.keys() != declared:
        raise _G5TimingRefusal("g5_timing_missing_week")
    projected = []
    for (quarter, week), roles in sorted(grouped.items()):
        if roles.keys() != _G5_TIMING_ROLES.keys():
            raise _G5TimingRefusal("g5_timing_incomplete_triplet")
        identity = roles["exact-search"][0]
        if any(match != identity for match, _ in roles.values()):
            raise _G5TimingRefusal("g5_timing_unmatched")
        projected.append({"quarter": quarter, "decision_week": week, "match_sha256": value_hash(identity),
                          "roles": {role: row for role, (_, row) in roles.items()}})
    report = {"code_commit": code, "scheduler_run_id": run_id, "weeks": projected}
    if clean_retry:
        retry, = projected
        report.update(weeks=[], retrospective_clean_retry=retry)
    return report


def _main_g5_timing(argv):
    parser = argparse.ArgumentParser(prog="hedge_evidence.py g5-timing")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    manifest = args.manifest.resolve()
    try:
        frozen = _g5_pinned_bytes(manifest.parent, {"path": manifest.name, "sha256": args.sha256},
                                  "g5_timing_manifest_pin")
        result = _g5_timing_read(manifest.parent, _g5_timing_json(frozen))
        output = json.dumps(result, sort_keys=True, allow_nan=False)
    except _G5TimingRefusal as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    except (ValueError, OSError, KeyError, IndexError, TypeError, AttributeError, OverflowError):
        sys.stderr.write("g5_timing_invalid\n")
        return 1
    print(output)
    return 0


def main(argv=None):
    """Write the verdict JSON: ``hedge_evidence.py ROOT FORMULATION COHORT=QUARTER... --registry R --out V``.

    ``hedge_evidence.py program FORMULATION --controls ... --programs ... --registry R --out V`` reads the program twin.
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "g5-timing":
        return _main_g5_timing(argv[1:])
    if argv and argv[0] == "g5-status":
        return _main_g5_status(argv[1:])
    if argv and argv[0] == "program":
        return main_program(argv[1:])
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
