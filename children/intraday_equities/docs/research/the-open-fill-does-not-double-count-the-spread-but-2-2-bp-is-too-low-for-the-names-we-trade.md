# The open fill does not double-count the spread, but 2.2 bp is too low for the names we trade

Date: 2026-09-25. Reads nothing at or after 2026-02-28. No config changed.

## Question

The backtest fills a market order at the next minute's opening trade and
then charges a flat half-spread of 2.2 bp on each side
(`configs/fill-policy.json`, `SchwabCostModel`). Is 2.2 bp the real cost,
and does the opening trade already carry the spread, so that 2.2 bp is
charged twice?

## Data

The minute quotes pulled for P4 (`source-alpaca-quotes-backfill.json`):
the last two-sided NBBO of each regular-hours minute. Only LLY and XOM
are complete (330 sessions, 2024-11-01 to 2026-02-27); JPM stopped at
2025-03-27 (99 sessions); WMT and AAPL were never pulled. Every stored
row already has ask > bid > 0, so the analysis dropped none; the pull
itself refused crossed and locked quotes (minutes that *contained* one:
LLY 0.7% / 1.3%, XOM 2.9% / 89%, JPM 1.1% / 5.7%). Minutes with a bar
but no quote row: LLY 29, XOM 57, JPM 7. Quotes are unadjusted and bars
split-adjusted; no split falls in the window, and no day's median
|open/mid - 1| exceeds 5%, so no rescaling was needed.

## Finding 1: the quoted half-spread

Half-spread in bp, median / mean / p90.

| | all | first 30 min | midday | last 30 min |
|---|---|---|---|---|
| LLY | 4.51 / 5.10 / 8.45 | 7.66 / 8.53 / 14.07 | 4.49 / 4.95 / 7.97 | 2.91 / 3.24 / 5.36 |
| XOM | 0.77 / 0.86 / 1.39 | 1.42 / 2.19 / 3.48 | 0.69 / 0.77 / 1.34 | 0.45 / 0.57 / 0.91 |
| JPM (partial) | 1.54 / 1.79 / 3.06 | 3.11 / 3.36 / 5.23 | 1.51 / 1.71 / 2.83 | 0.96 / 1.07 / 1.80 |

The 0.1 to 0.5 bp figure applies to one-cent books on cheap, very liquid
names. It does not apply to LLY, which quotes about 9 bp wide at $810.
The first 30 minutes are about twice midday, and the last 30 minutes
are about 0.65 times midday.

## Finding 2: the opening trade sits at the midpoint on average

This compares bar m's open with the midpoint at m's opening boundary
(the quote row stamped m - 1 minute), in bp.

| | signed mean | abs median | abs mean | open >= ask | open <= bid |
|---|---|---|---|---|---|
| LLY | -0.06 | 1.34 | 2.12 | 7.9% | 9.0% |
| XOM | -0.00 | 0.44 | 0.50 | 24.9% | 25.2% |
| JPM | -0.07 | 0.63 | 0.89 | 14.0% | 17.2% |

The signed offset is zero, and most opens print inside the quote, so
the open does not already carry our side's spread. Charging a half-spread
on top of it therefore does not double-count. It is the only spread
charge the backtest makes. A real market order pays the far side, about
one full half-spread from the midpoint.

There is one bias. The open tends to print on the same side as the
previous close. For LLY, the offset is +0.66 bp after a close above the
midpoint and -0.72 bp after a close below it (XOM ±0.07). A model that
buys after a down-print is filled below the midpoint in the backtest,
but at the ask in reality. The open fill therefore flatters
bounce-reverting signals by about 0.7 bp per side on LLY.

## Finding 3: a model for the names without quotes

The fit uses 759 name-days: log half-spread on log price, log dollar
volume and log one-minute volatility (b = +0.86, -0.54, +1.07; residual
sd 0.29). The prediction is then max(0.5 cent / price, fit). With three
names, the model is only indicative. Leaving one name out predicts LLY
at 8.0 against 4.5 actual, XOM at 1.06 against 0.77, and JPM at 1.16
against 1.54. Predicted median half-spreads for the ADR-0185 cohort,
using features from the same window:

| XLK | LRCX | NOW | PANW | ADBE | MSTR | TER | LULU | CIEN | LLY | LITE |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.59 | 1.40 | 1.96 | 2.13 | 2.72 | 2.96 | 3.54 | 4.07 | 4.12 | 4.47 | 5.62 |

## Recommendation

Use per-name values, not one flat number. They range from 0.6 to 5.6 bp,
and the names that trade most (MSTR, LITE) are above 2.2. As a second
step, apply a time-of-day multiplier (about 2x in the first 30 minutes
and 0.65x in the last 30). The 2026-09-25 full-training trades have
$6.58 M of notional. The spread charge on them is $1,446 at 2.2 bp
(4.4 bp round trip) and $2,389 at the modelled per-name rates (an
average of 7.3 bp round trip). That cuts net P&L from +$1,486 to about
+$543. The rates are the 2024-11 to 2026-02 ones; MSTR and LITE traded
earlier at different prices, so treat this as an order of magnitude.

The change needed is small but touches two places. `SchwabCostModel`
takes a scalar `spread_bps`. It would take a `{symbol: bps}` map with a
default (plus optional time-of-day multipliers), and
`buy_per_share`/`sell_per_share` would take the symbol (and ts). The
three call sites in `replay.py` (~1779, 1808, 1823) would pass them, and
`EquityKellyMIO`'s own `spread_bps` (sizing, `nodes_capital.py` ~1233)
must read the same map so that sizing and fills agree. Before trusting
the model, pull quotes for MSTR and LITE with the existing connector
(`tools/pull_quote_minutes.py`) and measure them directly. The pull
time depends on each name's quote count.

## Sources

- `tools/spread_cost_measure.py`, `tools/spread-cost-results.json`
  (readers reused from `tools/p4_mid_diagnostics.py`).
- `RESULT-P4-mid-quotes.md`, the pull and its quality table.
- `docs/reports/2026-09-25-full-training-execution-evaluation/trades.csv`.
