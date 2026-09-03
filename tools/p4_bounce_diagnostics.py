"""P4 free bounce diagnostics: the H=1 gain, next print or next value.

Reads ONLY bars already in the onboarding store (no quotes, no pipeline, no
walk-forward) and stops at 2026-02-28. Four checks from
``docs/research/p4-price-definition-bounce-diagnostic-and-the-close-vwap-mid-comparison.md``:

1. lag-1 autocovariance / autocorrelation of 1-minute last-trade (close) log
   returns, whole sample and by time of day;
2. the Roll (1984) implied spread from that autocovariance, against a real
   penny-or-two spread;
3. the same autocorrelation on ``vwap`` (the minute's average print), which
   under pure bounce shrinks by roughly the trade count;
4. how much of the H=1 skill the label/feature shared noise term can explain,
   measured in the run's OWN label space by reusing ``_LeadLabel``.

Run from the child root::

    cd ~/dskit/children/intraday_equities
    python ../../tools/p4_bounce_diagnostics.py --root ./ob
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys

import numpy as np

NAMES = ("JPM", "LLY", "XOM")
REFERENCE = "SPY"
WANTED = set(NAMES) | {REFERENCE}
#: No data after this date is read (docs/RE-ENTRY.md, "Locked").
CUT_UTC = "2026-03-01"
RTH_START_MINUTES = 570
RTH_END_MINUTES = 960
TZ = "America/New_York"
#: What the h01 documents declare (run-multi3-h01-{ridge,lgbm}.json).
PERIOD_MS = 300_000
VOL_WINDOW = 390
BETA_WINDOW = 3900
VOL_FLOOR = 1e-8
#: Clark-West gain at H=1 from docs/RE-ENTRY.md, in percent of the mean's MSPE.
OBSERVED_GAIN_PCT = {"ridge": 0.0898, "lgbm": 0.2337}
BUCKETS = (
    ("open30", RTH_START_MINUTES, RTH_START_MINUTES + 30),
    ("midday", RTH_START_MINUTES + 30, RTH_END_MINUTES - 30),
    ("close30", RTH_END_MINUTES - 30, RTH_END_MINUTES),
)
#: The walk-forward era: first validation 2022-05-06 less a 730-day training
#: window, so no fold in the h01 runs reads a bar before this.
WF_ERA_MS = 1_588_291_200_000  # 2020-05-01T00:00:00Z


def load_bars(root, source="alpaca-sip", cache=None):
    """Per-symbol RTH minute arrays for the cohort, up to the cut.

    Returns ``{symbol: dict of numpy arrays}`` with ``ms``, ``close``,
    ``vwap``, ``trade_count``, ``volume`` and ``ny_minute``.
    """
    if cache and os.path.exists(cache):
        blob = np.load(cache)
        return {
            symbol: {
                key: blob[f"{symbol}:{key}"]
                for key in ("ms", "close", "vwap", "trade_count", "volume",
                            "ny_minute")
            }
            for symbol in sorted(WANTED)
            if f"{symbol}:ms" in blob
        }

    import pandas as pd

    streams = []
    base = os.path.join(root, "observations", source)
    for entry in sorted(os.listdir(base)):
        path = os.path.join(base, entry, "bars.jsonl.gz")
        if os.path.exists(path):
            streams.append(path)
    rows = {symbol: [] for symbol in WANTED}
    for path in streams:
        with gzip.open(path, "rt") as handle:
            for line in handle:
                record = json.loads(line)
                data = record.get("data") or {}
                symbol = data.get("symbol")
                if symbol not in WANTED:
                    continue
                stamp = data.get("ts") or ""
                if stamp[:10] >= CUT_UTC:
                    continue
                rows[symbol].append((
                    stamp,
                    data.get("close"),
                    data.get("vwap"),
                    data.get("trade_count"),
                    data.get("volume"),
                ))

    out = {}
    for symbol, items in rows.items():
        if not items:
            continue
        frame = pd.DataFrame(
            items, columns=["ts", "close", "vwap", "trade_count", "volume"],
        )
        stamps = pd.to_datetime(frame["ts"], utc=True, format="ISO8601")
        local = stamps.dt.tz_convert(TZ)
        minute = local.dt.hour.to_numpy() * 60 + local.dt.minute.to_numpy()
        keep = (minute >= RTH_START_MINUTES) & (minute < RTH_END_MINUTES)
        frame = frame.loc[keep]
        ms = (stamps[keep].astype("int64").to_numpy() // 1_000_000)
        order = np.argsort(ms, kind="stable")
        out[symbol] = {
            "ms": ms[order],
            "close": frame["close"].to_numpy(dtype=np.float64)[order],
            "vwap": frame["vwap"].to_numpy(dtype=np.float64)[order],
            "trade_count": frame["trade_count"].to_numpy(
                dtype=np.float64)[order],
            "volume": frame["volume"].to_numpy(dtype=np.float64)[order],
            "ny_minute": minute[keep][order].astype(np.float64),
        }
    if cache:
        np.savez_compressed(cache, **{
            f"{symbol}:{key}": value
            for symbol, arrays in out.items()
            for key, value in arrays.items()
        })
    return out


def minute_returns(ms, price):
    """One-minute log returns, NaN wherever the previous bar is not t-1min."""
    out = np.full(price.size, np.nan)
    if price.size < 2:
        return out
    good = (np.diff(ms) == 60_000) & (price[1:] > 0) & (price[:-1] > 0)
    out[1:][good] = np.log(price[1:][good] / price[:-1][good])
    return out


def lag1(returns, mask=None):
    """Paired lag-1 autocovariance and autocorrelation of ``returns``.

    ``mask`` selects which *later* bar of each pair is counted, so a
    time-of-day bucket is a mask on ``t``.
    """
    later, earlier = returns[1:], returns[:-1]
    ok = np.isfinite(later) & np.isfinite(earlier)
    if mask is not None:
        ok &= mask[1:]
    n = int(ok.sum())
    if n < 100:
        return {"n": n, "acov": float("nan"), "acorr": float("nan"),
                "sd": float("nan"), "se": float("nan")}
    a, b = later[ok], earlier[ok]
    acov = float(np.mean(a * b) - np.mean(a) * np.mean(b))
    acorr = float(acov / (np.std(a) * np.std(b)))
    return {
        "n": n,
        "acov": acov,
        "acorr": acorr,
        "sd": float(np.std(np.concatenate([a, b]))),
        "se": float(1.0 / math.sqrt(n)),
    }


def roll_spread(acov, price):
    """Invert an autocovariance into Roll's half-spread and full spread.

    ``acov`` is in log-return units, so the half-spread is relative;
    ``price`` converts it to dollars. NaN when ``acov >= 0`` (Roll is
    undefined there — a non-negative autocovariance is not bounce).
    """
    if not np.isfinite(acov) or acov >= 0:
        return float("nan"), float("nan"), float("nan")
    half = math.sqrt(-acov)
    return half, 2.0 * half * 1e4, 2.0 * half * price


def conditional_r2(y, x, by, n_bins=10):
    """Variance-weighted R^2 of ``y`` on ``x`` fitted separately per bin.

    A tree is not held to one slope, so the linear bound understates what
    LightGBM could pull out of the shared bounce term. Binning on the
    local volatility (where the spread's share of variance moves most)
    and refitting inside each bin is the conditionally-linear ceiling.
    """
    ok = np.isfinite(by)
    if int(ok.sum()) < 5000:
        return float("nan")
    edges = np.quantile(by[ok], np.linspace(0, 1, n_bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    which = np.digitize(by, edges) - 1
    total, weighted = 0, 0.0
    for b in range(n_bins):
        sel = ok & (which == b)
        n = int(sel.sum())
        if n < 500:
            continue
        c = float(np.corrcoef(y[sel], x[sel])[0, 1])
        if np.isfinite(c):
            weighted += n * c * c
            total += n
    return weighted / total if total else float("nan")


def label_space_check(arrays, symbol, since_ms=0, collect=None):
    """Check 4, in the run's own label space.

    Builds the h01 documents' label (SPY-residual, divided by the causal
    390-bar sigma) with the pipeline's own ``_LeadLabel``, pairs it with
    ``ret_lag_0`` at the same bar, and returns the shared-signal
    correlation plus the bounce-only correlation implied by a pinned
    penny/two-penny spread.
    """
    from intraday_equities.nodes import _LeadLabel

    tapes = {
        name: (arrays[name]["ms"], arrays[name]["close"])
        for name in arrays
    }
    label = _LeadLabel(
        tapes, PERIOD_MS, scale="vol", residual=REFERENCE,
        vol_window=VOL_WINDOW, beta_window=BETA_WINDOW, vol_floor=VOL_FLOOR,
    )
    ms = arrays[symbol]["ms"]
    price = arrays[symbol]["close"]
    x = minute_returns(ms, price)
    loc = np.arange(ms.size - 1)
    # Only pairs where t+1 really is the next minute, so the label is a
    # one-minute-ahead return and not a move across a gap.
    contiguous = (ms[1:] - ms[:-1]) == 60_000
    y = label.values(symbol, loc, loc + 1)
    ok = np.isfinite(y) & np.isfinite(x[loc]) & contiguous & (ms[loc] >= since_ms)
    out = {"n": int(ok.sum())}
    if out["n"] < 1000:
        return out
    yv, xv = y[ok], x[loc][ok]
    corr = float(np.corrcoef(yv, xv)[0, 1])
    out["corr_label_lag0"] = corr
    out["r2_label_lag0"] = corr * corr
    out["sd_label"] = float(np.std(yv))
    out["sd_ret"] = float(np.std(xv))

    # Rows on the run's 5-minute grid only (period_ms = 300000, offset 0).
    grid = ok & ((ms[loc] % PERIOD_MS) == 0)
    if int(grid.sum()) > 1000:
        gy, gx = y[grid], x[loc][grid]
        gc = float(np.corrcoef(gy, gx)[0, 1])
        out["n_grid"] = int(grid.sum())
        out["corr_grid"] = gc
        out["r2_grid"] = gc * gc

    # Bounce-only prediction. The shared term is -u_t in the label and
    # +u_t in ret_lag_0, so cov = -s^2 * E[1/sigma_t] once the label is
    # divided by sigma_t; s is the RELATIVE half-spread. E[u^2 / sigma]
    # is taken over the same rows the correlation used.
    _, sigma = label._prepare(symbol)
    inv_sigma = np.full(ms.size, np.nan)
    good = np.isfinite(sigma) & (sigma > VOL_FLOOR)
    inv_sigma[good] = 1.0 / sigma[good]
    inv_sig_ok = inv_sigma[loc][ok]
    inv_px_ok = 1.0 / price[loc][ok]
    for cents, tag in ((1.0, "penny"), (2.0, "twopenny")):
        # s_t is a PER-BAR relative half-spread: half of one tick over
        # that bar's own price, so a $60 tape and a $900 tape are not
        # averaged into one relative spread.
        cov = -float(np.nanmean((0.5 * cents / 100.0 * inv_px_ok) ** 2
                                * inv_sig_ok))
        corr_b = cov / (out["sd_label"] * out["sd_ret"])
        out[f"corr_bounce_{tag}"] = float(corr_b)
        out[f"r2_bounce_{tag}"] = float(corr_b * corr_b)
    # And with the spread the Roll estimator itself implies, on the same rows.
    mask = np.zeros(ms.size, dtype=bool)
    mask[loc[ok]] = True
    acov = lag1(x, mask)["acov"]
    if np.isfinite(acov) and acov < 0:
        cov = -(-acov) * float(np.nanmean(inv_sig_ok))
        corr_b = cov / (out["sd_label"] * out["sd_ret"])
        out["roll_half_spread_rel"] = math.sqrt(-acov)
        out["corr_bounce_roll"] = float(corr_b)
        out["r2_bounce_roll"] = float(corr_b * corr_b)

    # A tree refits inside regimes; bin on the local sigma and refit.
    sig_ok = sigma[loc][ok]
    out["r2_label_lag0_by_vol"] = conditional_r2(yv, xv, sig_ok)
    if collect is not None:
        # Standardised so three names can be pooled the way the scan
        # pools them: one fit over all rows at once.
        collect.append({
            "symbol": symbol, "y": yv, "x": xv / np.std(xv), "sigma": sig_ok,
            "grid": (ms[loc][ok] % PERIOD_MS) == 0,
        })
    return out


def per_name_gain(runs_root):
    """Per-name Clark-West gain at H=1, from walk-forwards already on disk.

    Reads nothing but ``carry.json`` — no pipeline is run. The gain is
    ``(mspe_mean - mspe_model) / mspe_mean`` per (symbol, fold), which is
    what ``docs/RE-ENTRY.md`` reports pooled.
    """
    import glob

    out = {}
    for model in ("ridge", "lgbm"):
        pattern = os.path.join(
            runs_root, f"intraday-equities-multi3-h01-{model}-wf-*",
            "carry.json",
        )
        per = {}
        folds = sorted(glob.glob(pattern))
        for path in folds:
            with open(path) as handle:
                carry = json.load(handle)
            for row in (carry.get("scan") or {}).get("records") or []:
                symbol, mean, model_ = (
                    row.get("symbol"), row.get("mspe_mean"),
                    row.get("mspe_model"),
                )
                if symbol is None or not mean:
                    continue
                per.setdefault(symbol, []).append(
                    100.0 * (mean - model_) / mean)
        if not per:
            continue
        every = []
        cell = {"n_folds": len(folds)}
        for symbol, values in sorted(per.items()):
            cell[symbol] = {
                "n": len(values),
                "mean_gain_pct": sum(values) / len(values),
                "folds_positive": sum(1 for v in values if v > 0),
                "min_gain_pct": min(values),
                "max_gain_pct": max(values),
            }
            every += values
        cell["pooled_mean_gain_pct"] = sum(every) / len(every)
        out[model] = cell
    return out


def main(argv=None):
    """Run every check and print (optionally write) the results as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="./ob")
    parser.add_argument(
        "--runs", default="./pipeline_runs",
        help="walk-forward output root, read only for the per-name gain",
    )
    parser.add_argument("--cache", default=os.path.expanduser(
        "~/p4-bounce-cache/bars.npz"))
    parser.add_argument("--json", default=None, help="write results here")
    args = parser.parse_args(argv)

    if args.cache:
        os.makedirs(os.path.dirname(args.cache), exist_ok=True)
    arrays = load_bars(args.root, cache=args.cache)
    missing = sorted(WANTED - set(arrays))
    if missing:
        raise SystemExit(f"store has no bars for {missing}")

    results = {"cut": CUT_UTC, "windows": {}}
    windows = (("full", 0), ("wf_era", WF_ERA_MS))
    for window, since in windows:
        results["windows"][window] = {"since_ms": since, "symbols": {}}
        collected = []
        for symbol in NAMES:
            bars = arrays[symbol]
            ms, minute = bars["ms"], bars["ny_minute"]
            era = ms >= since
            close_r = minute_returns(ms, bars["close"])
            vwap_r = minute_returns(ms, bars["vwap"])
            median_price = float(np.median(bars["close"][era]))
            counts = bars["trade_count"][era]
            entry = {
                "n_bars": int(era.sum()),
                "first_ms": int(ms[era][0]),
                "last_ms": int(ms[era][-1]),
                "median_price": median_price,
                "median_trades_per_minute": float(np.median(counts)),
                "mean_trades_per_minute": float(np.mean(counts)),
                "frac_trades_le_2": float(np.mean(counts <= 2)),
                "frac_trades_le_5": float(np.mean(counts <= 5)),
                "frac_trades_le_10": float(np.mean(counts <= 10)),
                "frac_trades_le_30": float(np.mean(counts <= 30)),
                "close": {}, "vwap": {},
            }
            for field, series in (("close", close_r), ("vwap", vwap_r)):
                cells = [("all", era)]
                cells += [
                    (tag, era & (minute >= lo) & (minute < hi))
                    for tag, lo, hi in BUCKETS
                ]
                for tag, mask in cells:
                    cell = lag1(series, mask)
                    price_here = float(np.median(bars["close"][mask]))
                    half, bps, dollars = roll_spread(cell["acov"], price_here)
                    cell.update({
                        "median_price": price_here,
                        "roll_half_spread_rel": half,
                        "roll_spread_bps": bps,
                        "roll_spread_dollars": dollars,
                        "r2_from_lag1": cell["acorr"] ** 2,
                    })
                    entry[field][tag] = cell
            entry["label_space"] = label_space_check(
                arrays, symbol, since, collect=collected)
            results["windows"][window]["symbols"][symbol] = entry

        # Pooled the way the scan pools: one fit over all three names.
        pooled = {}
        for tag, use_grid in (("all_rows", False), ("grid_rows", True)):
            ys, xs, sg, per = [], [], [], []
            for item in collected:
                sel = item["grid"] if use_grid else slice(None)
                y, x, s = item["y"][sel], item["x"][sel], item["sigma"][sel]
                ys.append(y)
                xs.append(x)
                sg.append(s)
                c = float(np.corrcoef(y, x)[0, 1])
                per.append((y.size, c * c))
            y = np.concatenate(ys)
            x = np.concatenate(xs)
            s = np.concatenate(sg)
            c = float(np.corrcoef(y, x)[0, 1])
            n_all = sum(n for n, _ in per)
            pooled[tag] = {
                "n": int(y.size),
                # One slope shared by all three names.
                "corr_one_slope": c,
                "r2_one_slope_pct": 100.0 * c * c,
                # A slope per name (what a symbol-coded tree can reach).
                "r2_per_name_pct": 100.0 * sum(
                    n * r for n, r in per) / n_all,
                # A slope per name AND per volatility decile.
                "r2_per_name_by_vol_pct": 100.0 * sum(
                    item["y"][item["grid"] if use_grid else slice(None)].size
                    * conditional_r2(
                        item["y"][item["grid"] if use_grid else slice(None)],
                        item["x"][item["grid"] if use_grid else slice(None)],
                        item["sigma"][item["grid"] if use_grid else slice(None)],
                    )
                    for item in collected) / n_all,
                "observed_gain_pct": OBSERVED_GAIN_PCT,
            }
        results["windows"][window]["pooled_lag0_ceiling"] = pooled

    # Per calendar year: the tape spans a 5x move in price, so one
    # relative spread over ten years hides the level it came from.
    import pandas as pd

    results["per_year"] = {}
    for symbol in NAMES:
        bars = arrays[symbol]
        ms = bars["ms"]
        years = pd.to_datetime(ms, unit="ms", utc=True).year.to_numpy()
        close_r = minute_returns(ms, bars["close"])
        rows = {}
        for year in sorted(set(years.tolist())):
            mask = years == year
            cell = lag1(close_r, mask)
            price_here = float(np.median(bars["close"][mask]))
            half, bps, dollars = roll_spread(cell["acov"], price_here)
            rows[str(year)] = {
                "n": cell["n"], "acorr": cell["acorr"], "acov": cell["acov"],
                "median_price": price_here, "roll_spread_bps": bps,
                "roll_spread_dollars": dollars,
                "median_trades_per_minute": float(
                    np.median(bars["trade_count"][mask])),
            }
        results["per_year"][symbol] = rows

    if args.runs and os.path.isdir(args.runs):
        results["per_name_gain_h01"] = per_name_gain(args.runs)

    text = json.dumps(results, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w") as handle:
            handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
