"""The real bid-ask cost behind ``fill-policy.json``'s flat ``spread_bps``.

The backtest fills a market order at the NEXT one-minute bar's open (a
trade print, split-adjusted SIP) and then charges a flat half-spread on
each side (``SchwabCostModel``, ``spread_bps: 2.2``). This measures,
from the ``alpaca-sip-quotes`` minute rows (one NBBO per minute, the last
two-sided quote in ``[t, t+60s)``), whether that number is right:

1. quoted half-spread, ``(ask - bid) / 2 / mid`` in bps, per symbol,
   overall and by time of day (first 30 min, midday, last 30 min);
2. the effective offset of the fill convention: bar ``m``'s open against
   the midpoint prevailing at ``m``'s opening boundary, which is the
   quote row stamped ``m - 60s``. If the open already sits on the
   trader's side of the book, adding a half-spread charges it twice;
3. a half-spread model, ``max(0.5 cent / price, fit)``, fit on name-days
   of the quoted names and applied to a declared cohort;
4. the spread share of a trades.csv's fees at 2.2 bp versus the model.

Readers are ``tools/p4_mid_diagnostics.py``'s: the same streaming
observation iterator and the same instant-keyed join, so the bar and
quote stamps' different spellings cannot silently match nothing.

Usage::

    python tools/spread_cost_measure.py --root <ob> \\
        --trades <report>/trades.csv --json out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p4_mid_diagnostics import (  # noqa: E402
    QUOTE_SOURCE,
    QUOTE_STREAM,
    RTH_END_MINUTES,
    RTH_START_MINUTES,
    SESSION_TZ,
    _iter_stream,
    _session_minute,
    _stamp_ms,
)

MINUTE_MS = 60000
#: The quote pull's window (source-alpaca-quotes-backfill.json), end exclusive.
WINDOW = ("2024-11-01", "2026-02-28")
#: Time-of-day buckets on the bar's session minute (NY).
BUCKETS = (("open30", 570, 600), ("midday", 600, 930), ("close30", 930, 960))
#: fill-policy.json's current value, bps of price per side.
FLAT_BPS = 2.2
#: A one-cent minimum quoted spread is a half-cent half-spread.
TICK_HALF = 0.005
#: A day whose median |open/mid - 1| exceeds this is a scale mismatch
#: (an unadjusted split), not a spread.
SCALE_TOL = 0.05
#: Bar source per cohort symbol, from run-retrain-simulation-template.json
#: (ADR-0185): bars_a carries LLY and XLK, bars_e the other nine.
COHORT = {
    "alpaca-sip-split": ("LLY", "XLK"),
    "alpaca-sip-split-e": (
        "ADBE", "CIEN", "LITE", "LRCX", "LULU", "MSTR", "NOW", "PANW", "TER",
    ),
}


def _bucket(minute):
    for name, lo, hi in BUCKETS:
        if lo <= minute < hi:
            return name
    return None


def _stats(values):
    import numpy as np

    x = np.asarray(values, dtype=np.float64)
    if not x.size:
        return {"n": 0}
    return {
        "n": int(x.size),
        "median": float(np.median(x)),
        "mean": float(x.mean()),
        "p90": float(np.percentile(x, 90)),
    }


def _read_quotes(root, zone):
    """Instant-keyed quote rows, plus rows refused as crossed/locked/zero."""
    quotes, dropped = {}, {}
    for row in _iter_stream(root, QUOTE_SOURCE, QUOTE_STREAM):
        day, _ = _session_minute(row["ts"], zone)
        if not WINDOW[0] <= day < WINDOW[1]:
            continue
        bid, ask, symbol = row.get("bid"), row.get("ask"), row["symbol"]
        if bid is None or ask is None or bid <= 0.0 or ask <= bid:
            dropped[symbol] = dropped.get(symbol, 0) + 1
            continue
        quotes[(symbol, _stamp_ms(row["ts"]))] = row
    return quotes, dropped


def _read_bars(root, source, symbols, zone):
    """RTH bars in the window, keyed per symbol on the instant."""
    bars = {}
    for row in _iter_stream(root, source, "bars", set(symbols)):
        day, minute = _session_minute(row["ts"], zone)
        if not WINDOW[0] <= day < WINDOW[1]:
            continue
        if not RTH_START_MINUTES <= minute < RTH_END_MINUTES:
            continue
        bars.setdefault(row["symbol"], {})[_stamp_ms(row["ts"])] = (
            day, minute, float(row["open"]), float(row["close"]),
            float(row.get("volume") or 0.0),
            float(row.get("vwap") or row["close"]),
        )
    return bars


def _day_features(rows):
    """Per session: median close, dollar volume, 1-min log-return stdev."""
    import numpy as np

    days = {}
    for instant in sorted(rows):
        day, _, _, close, volume, vwap = rows[instant]
        days.setdefault(day, []).append((close, volume * vwap))
    out = {}
    for day, items in days.items():
        closes = np.array([c for c, _ in items])
        if closes.size < 100:
            continue
        rets = np.diff(np.log(closes))
        out[day] = (
            float(np.median(closes)),
            float(sum(d for _, d in items)),
            float(rets.std()) * 1e4,
        )
    return out


def _design(price, dollars, sigma):
    import numpy as np

    return np.array([1.0, np.log(price), np.log(dollars), np.log(sigma)])


def main(argv=None):
    """Measure quoted and effective spreads; fit and apply a model."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--bar-source", default="alpaca-sip-split")
    parser.add_argument("--trades", default="")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    import numpy as np
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(SESSION_TZ)
    quotes, dropped = _read_quotes(args.root, zone)
    names = sorted({symbol for symbol, _ in quotes})
    bars = _read_bars(args.root, args.bar_source, names, zone)
    report = {"window": list(WINDOW), "flat_bps": FLAT_BPS, "symbols": {}}

    fit_x, fit_y, fit_g = [], [], []
    for symbol in names:
        rows = bars.get(symbol, {})
        qrows = {k: v for k, v in quotes.items() if k[0] == symbol}
        # 1. quoted half-spread, bucketed on the row's own minute.
        quoted = {"all": []}
        day_hs = {}
        for (_, instant), q in qrows.items():
            day, minute = _session_minute(q["ts"], zone)
            half = 0.5 * (q["ask"] - q["bid"]) / q["mid"] * 1e4
            quoted["all"].append(half)
            quoted.setdefault(_bucket(minute), []).append(half)
            day_hs.setdefault(day, []).append(half)
        bar_days = {v[0] for v in rows.values()}
        q_days = set(day_hs)
        covered = [v for v in rows.values() if v[0] in q_days]
        with_row = sum(
            1 for instant, v in rows.items()
            if v[0] in q_days and (symbol, instant) in quotes
        )
        # 2. bar m's open against the mid at m's opening boundary: the row
        # stamped m-60s. The session's first bar has no in-RTH row before
        # it and drops out; so does any minute whose boundary row is missing.
        signed = {"all": []}
        absolute = {"all": []}
        at_ask = {"all": 0}
        at_bid = {"all": 0}
        after_up, after_down = [], []
        ratio_by_day = {}
        for instant, (day, minute, opn, _, volume, _) in rows.items():
            q = quotes.get((symbol, instant - MINUTE_MS))
            if q is None or not volume:
                continue
            ratio_by_day.setdefault(day, []).append(abs(opn / q["mid"] - 1.0))
            s = (opn - q["mid"]) / q["mid"] * 1e4
            b = _bucket(minute)
            for key in ("all", b):
                signed.setdefault(key, []).append(s)
                absolute.setdefault(key, []).append(abs(s))
                at_ask[key] = at_ask.get(key, 0) + (opn >= q["ask"])
                at_bid[key] = at_bid.get(key, 0) + (opn <= q["bid"])
            prev = rows.get(instant - MINUTE_MS)
            if prev is not None:
                # The prior bar's close sits at the same boundary quote; a
                # close-driven signal sees which side it printed on.
                if prev[3] > q["mid"]:
                    after_up.append(s)
                elif prev[3] < q["mid"]:
                    after_down.append(s)
        bad_days = sorted(
            d for d, v in ratio_by_day.items() if np.median(v) > SCALE_TOL
        )
        eff = {}
        for key in signed:
            n = len(signed[key])
            eff[key] = {
                "n": n,
                "signed_mean": float(np.mean(signed[key])),
                "signed_median": float(np.median(signed[key])),
                "abs_mean": float(np.mean(absolute[key])),
                "abs_median": float(np.median(absolute[key])),
                "frac_at_or_above_ask": at_ask[key] / n,
                "frac_at_or_below_bid": at_bid[key] / n,
            }
        feats = _day_features(rows)
        for day, hs in day_hs.items():
            if day in feats and len(hs) >= 100:
                fit_x.append(_design(*feats[day]))
                fit_y.append(np.log(np.median(hs)))
                fit_g.append(symbol)
        report["symbols"][symbol] = {
            "sessions_quoted": len(q_days),
            "first_day": min(q_days), "last_day": max(q_days),
            "rth_bar_minutes_on_quoted_days": len(covered),
            "bar_minutes_without_quote_row": len(covered) - with_row,
            "rows_dropped_crossed_locked_zero": dropped.get(symbol, 0),
            "rows_whose_minute_saw_crossed": sum(
                1 for q in qrows.values() if q.get("n_crossed")),
            "rows_whose_minute_saw_locked": sum(
                1 for q in qrows.values() if q.get("n_locked")),
            "median_quote_age_ms": float(np.median(
                [q["quote_age_ms"] for q in qrows.values()])),
            "scale_mismatch_days": bad_days,
            "unquoted_bar_days": len(bar_days - q_days),
            "half_spread_bps": {k: _stats(v) for k, v in quoted.items()},
            "open_minus_mid_bps": eff,
            "open_minus_mid_after_close_above_mid": _stats(after_up),
            "open_minus_mid_after_close_below_mid": _stats(after_down),
            "median_features": dict(zip(
                ("price", "dollar_volume", "sigma_1m_bps"),
                np.median(np.array([feats[d] for d in feats]), axis=0).tolist(),
            )),
        }

    # 3. log half-spread on log price, log dollar volume, log 1-min vol,
    # one row per name-day; leave-one-name-out shows what the cross
    # section alone can support.
    X, y, g = np.array(fit_x), np.array(fit_y), np.array(fit_g)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    loo = {}
    for name in names:
        keep = g != name
        b = np.linalg.lstsq(X[keep], y[keep], rcond=None)[0]
        pred = float(np.exp(np.median(X[~keep] @ b)))
        loo[name] = {
            "predicted_median_bps": pred,
            "actual_median_bps":
                report["symbols"][name]["half_spread_bps"]["all"]["median"],
        }
    report["model"] = {
        "form": "log hs_bps = b0 + b1 log price + b2 log $vol + b3 log sigma_1m; "
                "hs = max(0.5c/price, fit)",
        "coef": dict(zip(("b0", "log_price", "log_dollar_volume", "log_sigma"),
                         beta.tolist())),
        "name_days": int(len(y)),
        "in_sample_resid_sd": float(np.std(y - X @ beta)),
        "leave_one_name_out": loo,
    }

    cohort = {}
    for source, symbols in COHORT.items():
        cb = _read_bars(args.root, source, symbols, zone)
        for symbol in symbols:
            feats = _day_features(cb.get(symbol, {}))
            if not feats:
                cohort[symbol] = {"error": f"no bars in {source}"}
                continue
            preds = [
                max(TICK_HALF / f[0] * 1e4, float(np.exp(_design(*f) @ beta)))
                for f in feats.values()
            ]
            med = np.median(np.array(list(feats.values())), axis=0)
            cohort[symbol] = {
                "sessions": len(feats),
                "median_price": float(med[0]),
                "median_dollar_volume": float(med[1]),
                "median_sigma_1m_bps": float(med[2]),
                "tick_floor_bps": TICK_HALF / float(med[0]) * 1e4,
                "predicted_half_spread_bps": float(np.median(preds)),
            }
    report["cohort"] = cohort

    # 4. What the spread line of a trades.csv costs at the flat rate
    # against the modelled per-name rate, on the same notional.
    if args.trades:
        rows = list(csv.DictReader(open(args.trades, encoding="utf-8")))
        by = {}
        for r in rows:
            notional = float(r["qty"]) * (
                float(r["entry_price"]) + float(r["exit_price"]))
            item = by.setdefault(r["instrument"], {"trips": 0, "notional": 0.0,
                                                   "gross": 0.0, "fees": 0.0})
            item["trips"] += 1
            item["notional"] += notional
            item["gross"] += float(r["gross_pnl"])
            item["fees"] += float(r["fees"])
        for symbol, item in by.items():
            hs = cohort.get(symbol, {}).get("predicted_half_spread_bps")
            item["spread_cost_flat"] = item["notional"] * FLAT_BPS * 1e-4
            item["spread_cost_model"] = (
                None if hs is None else item["notional"] * hs * 1e-4)
        report["trades"] = {
            "file": args.trades,
            "by_symbol": by,
            "gross": sum(v["gross"] for v in by.values()),
            "fees": sum(v["fees"] for v in by.values()),
            "spread_cost_flat": sum(v["spread_cost_flat"] for v in by.values()),
            "spread_cost_model": sum(
                v["spread_cost_model"] or 0.0 for v in by.values()),
        }

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
