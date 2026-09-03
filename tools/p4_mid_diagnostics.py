"""P4 arm C: quality of the minute-mid asset, and the bounce test redone on it.

Reads the ``alpaca-sip-quotes`` minute rows and the raw ``alpaca-sip``
bars from one onboarding root, joins them on ``(symbol, ts)``, and
reports per symbol:

* coverage — the share of regular-hours bar minutes with no usable quote;
* market quality — crossed, locked and implausibly wide minutes;
* consistency — how often the midpoint falls outside the minute's own
  high-low range of trades;
* the decisive number — the lag-one autocorrelation of one-minute
  MIDPOINT returns beside the same statistic on last-trade returns, over
  exactly the same minutes. Bounce lives in the trade price and not in
  the midpoint, so if it caused the H=1 result the midpoint's figure
  collapses toward zero while the trade price's stays where P4 found it.

Usage::

    python tools/p4_mid_diagnostics.py --root <ob> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

BAR_SOURCE = "alpaca-sip"
QUOTE_SOURCE = "alpaca-sip-quotes"
QUOTE_STREAM = "quote_minutes"
RTH_START_MINUTES = 570
RTH_END_MINUTES = 960
SESSION_TZ = "America/New_York"
#: A minute whose quoted spread exceeds this is not a market anyone is
#: reading a price off; reported, never silently dropped.
WIDE_BPS = 100.0
VERY_WIDE_BPS = 300.0


def _stamp_ms(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def _session_minute(text, zone):
    when = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(zone)
    return when.date().isoformat(), when.hour * 60 + when.minute


def _load(root, source, stream, key_fields):
    from dskit.onboarding.observations import scan_stream

    return scan_stream(
        root, source, stream,
        key_fields=key_fields, ts_field="ts", shared_fields=("symbol",),
    )


def _lag_one(values, groups):
    """Lag-one autocorrelation of within-group first differences of logs."""
    import numpy as np

    prior = None
    diffs = []
    for value, group in zip(values, groups):
        if prior is not None and prior[1] == group:
            diffs.append(np.log(value) - np.log(prior[0]))
        else:
            diffs.append(np.nan)
        prior = (value, group)
    x = np.asarray(diffs, dtype=np.float64)
    pairs = np.isfinite(x[:-1]) & np.isfinite(x[1:])
    a, b = x[:-1][pairs], x[1:][pairs]
    if a.size < 100:
        return float("nan"), 0
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom <= 0.0:
        return float("nan"), int(a.size)
    return float((a * b).sum() / denom), int(a.size)


def main(argv=None):
    """Report coverage, market quality and the redone bounce test."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    import numpy as np
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(SESSION_TZ)
    quotes = {}
    covered = {}
    for row in _load(args.root, QUOTE_SOURCE, QUOTE_STREAM, ("symbol", "ts")):
        quotes[(row["symbol"], row["ts"])] = row
        day, _ = _session_minute(row["ts"], zone)
        covered.setdefault(row["symbol"], set()).add(day)
    if not quotes:
        print("no quote minutes in", args.root, file=sys.stderr)
        return 1

    bars = {}
    for row in _load(args.root, BAR_SOURCE, "bars", ("symbol", "ts")):
        symbol = row["symbol"]
        if symbol not in covered:
            continue
        day, minute = _session_minute(row["ts"], zone)
        if day not in covered[symbol]:
            continue
        if not RTH_START_MINUTES <= minute < RTH_END_MINUTES:
            continue
        bars.setdefault(symbol, []).append((row["ts"], day, row))

    report = {}
    for symbol in sorted(bars):
        rows = sorted(bars[symbol], key=lambda item: _stamp_ms(item[0]))
        days = sorted({item[1] for item in rows})
        n_bars = len(rows)
        matched = [
            (day, bar, quotes.get((symbol, stamp)))
            for stamp, day, bar in rows
        ]
        hit = [item for item in matched if item[2] is not None]
        n_hit = len(hit)
        spreads = np.array([q["spread_bps"] for _, _, q in hit], dtype=np.float64)
        crossed = sum(1 for _, _, q in hit if q.get("n_crossed", 0))
        locked = sum(1 for _, _, q in hit if q.get("n_locked", 0))
        outside = sum(
            1 for _, bar, q in hit
            if bar.get("low") is not None and bar.get("high") is not None
            and bar.get("volume")
            and not (float(bar["low"]) <= q["mid"] <= float(bar["high"]))
        )
        traded = sum(1 for _, bar, _ in hit if bar.get("volume"))
        mid_rho, mid_n = _lag_one(
            [q["mid"] for _, _, q in hit], [day for day, _, _ in hit]
        )
        close_rho, close_n = _lag_one(
            [float(bar["close"]) for _, bar, _ in hit], [day for day, _, _ in hit]
        )
        report[symbol] = {
            "sessions": len(days),
            "first_session": days[0],
            "last_session": days[-1],
            "rth_bar_minutes": n_bars,
            "minutes_with_quote": n_hit,
            "missing_quote_frac": (n_bars - n_hit) / n_bars if n_bars else None,
            "median_spread_bps": float(np.median(spreads)) if n_hit else None,
            "p99_spread_bps": float(np.percentile(spreads, 99)) if n_hit else None,
            "wide_gt_100bps_frac": float((spreads > WIDE_BPS).mean()) if n_hit else None,
            "wide_gt_300bps_frac": (
                float((spreads > VERY_WIDE_BPS).mean()) if n_hit else None
            ),
            "minutes_with_a_crossed_quote_frac": crossed / n_hit if n_hit else None,
            "minutes_with_a_locked_quote_frac": locked / n_hit if n_hit else None,
            "mid_outside_bar_range_frac": outside / traded if traded else None,
            "lag1_mid": mid_rho,
            "lag1_close": close_rho,
            "lag1_pairs": mid_n,
            "lag1_se": (1.0 / np.sqrt(mid_n)) if mid_n else None,
            "close_pairs": close_n,
        }

    header = (
        "symbol  sess   minutes  missing%  medSpr  >1%   crossed%  locked%  "
        "outside%   lag1_close   lag1_mid    se"
    )
    print(header)
    print("-" * len(header))
    for symbol, r in report.items():
        print(
            "%-6s %5d %9d %8.3f %7.2f %5.3f %9.4f %8.4f %9.4f %12.4f %11.4f %6.4f"
            % (
                symbol, r["sessions"], r["rth_bar_minutes"],
                100.0 * r["missing_quote_frac"], r["median_spread_bps"],
                100.0 * r["wide_gt_100bps_frac"],
                100.0 * r["minutes_with_a_crossed_quote_frac"],
                100.0 * r["minutes_with_a_locked_quote_frac"],
                100.0 * (r["mid_outside_bar_range_frac"] or 0.0),
                r["lag1_close"], r["lag1_mid"], r["lag1_se"],
            )
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
        print("wrote", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
