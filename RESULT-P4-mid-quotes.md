# RESULT — P4 arm C: the minute midpoint, pulled and tested

Date: 2026-09-03. Branch `feat/quote-mid-pull`, worktree `~/wt/quotes`.
Plan item **P4** of `docs/plans/2026-09-horizon-search.md`; ADR-0065.

New data was pulled. **No pipeline was run.** Nothing at or after
2026-02-28 was requested: the source config's `end` is
`2026-02-28T00:00:00+00:00` and it bounds the fetch, so the last session
touched is 2026-02-27 (verified below).

Reproduce with:

```bash
cd ~/wt/quotes/children/intraday_equities
set -a && . ~/dskit/children/intraday_equities/.env && set +a
export PYTHONPATH=$HOME/wt/quotes:$PWD
python $HOME/wt/quotes/tools/pull_quote_minutes.py \
  --root $HOME/dskit/children/intraday_equities/ob --until LLY,XOM
python $HOME/wt/quotes/tools/p4_mid_diagnostics.py \
  --root $HOME/dskit/children/intraday_equities/ob --json p4-mid-results.json
```

---

## 1. What the connector can do, and what it cost

The existing `AlpacaBarsConnector` is bars-only — no quote vocabulary
anywhere in the tree. Alpaca *does* serve historical SIP quotes
(`/v2/stocks/quotes`, consolidated NBBO, back to 2016) and it *does*
accept `sort=desc`, so a single last-quote-before-a-boundary request is
expressible. It is not usable: one request per minute is 343,000
requests for one name-window, and the free tier serves 200 a minute.

Measured on this cohort before pulling anything (five names, regular
hours, one request per page of 10,000):

| session | NBBO updates, 5 names | AAPL | JPM | LLY | WMT | XOM |
|---|---|---|---|---|---|---|
| 2022-06-15 | 5,812,515 | 2,501,955 | 641,287 | 129,345 | 451,350 | 2,088,577 |
| 2023-11-15 | 3,542,597 | 1,477,620 | 528,562 | 231,440 | 225,162 | 1,079,812 |
| 2024-06-12 | 2,902,567 | 1,387,457 | 307,480 | 78,555 | 248,512 | 880,562 |
| 2025-11-12 | 1,784,522 | 861,330 | 135,285 | 98,537 | 336,227 | 353,142 |
| 2026-02-25 | 1,432,425 | 701,890 | 105,607 | 48,570 | 181,507 | 394,850 |

Sustained throughput is the request limit, not bandwidth: six parallel
workers earned a 429 after 234 requests in ten seconds, so the ceiling
is ~200 requests/min = **2 M quotes/min**. The full walk-forward era
(2022-05-06 →) for five names is ≈2.7 B quotes ≈ **23 hours** and
hundreds of GB in flight, to yield 1.7 M minute rows.

**So the pull was scoped to 2024-11-01 → 2026-02-27** (346 weekday
sessions, ~335 trading days) — the last five-and-a-bit fold validation
windows plus the whole holdout tail to the cut — with symbols in pull
order `LLY, XOM, JPM, WMT, AAPL` so the decisive pair lands first.
Estimated before starting: LLY ≈ 22 M quotes ≈ 12 min; XOM ≈ 140 M ≈
75 min; JPM ≈ 25 min; WMT ≈ 55 min; AAPL ≈ 3 h. Actual: LLY finished in
**7 minutes**; XOM ran at 4–6 sessions/min (slowed for a stretch when
another agent's walk-forward held 14.4 GB of the box's 17 GB).

Raw quotes are never stored (ADR-0065): pages are folded to one row per
minute boundary in flight. Peak RSS of the pull was **46 MB**; peak RSS
of the diagnostic was **638 MB**. Nowhere near the 10 GB bound.

## 2. Coverage and quality

`alpaca-sip-quotes`, joined to the split-adjusted bar tree on
`(symbol, instant)`. Regular hours only.

| | LLY | XOM (partial) |
|---|---|---|
| sessions | 330 | 155 |
| first → last | 2024-11-01 → **2026-02-27** | 2024-11-01 → 2025-06-17 |
| RTH bar minutes | 126,950 | 60,150 |
| minutes with a quote | 126,921 | 60,126 |
| **missing-quote share** | **0.023%** | **0.040%** |
| median mid | $810.71 | $109.15 |
| median spread | **$0.76 (9.00 bps)** | **$0.02 (1.79 bps)** |
| 99th-pct spread | 31.1 bps | 6.5 bps |
| spread > 1% of price | 0.001% | 0.002% |
| spread > 3% of price | 0 | 0 |
| minutes containing a crossed quote | 0.73% | 2.60% |
| minutes containing a locked quote | 1.35% | 83.2% |
| mid outside the minute's traded high-low | 12.7% | 7.2% |

Reading:

- **Coverage is essentially complete.** Two hundredths of one percent of
  regular-hours minutes have no usable two-sided quote inside their own
  sixty seconds. Nothing needs interpolating.
- **Implausible spreads are absent.** One minute in 100,000 quotes wider
  than 1% of price; none wider than 3%. The wide tail is the open, which
  is where it belongs.
- **Crossed and locked are counted, never used.** The stored bid/ask is
  the last quote in the minute with `ask > bid`; a minute is flagged if
  it merely *contained* a crossed or locked quote. XOM's 83% locked is
  not a defect — it is a two-cent stock at $109 where the book locks
  constantly — and it is exactly why the selection rule exists.
- **The observed spread is far wider than Roll implied.** P4's Roll
  estimate for LLY was 3.63 bps ($0.133 at a $367 median). The real
  quoted spread is **9.0 bps** — 2.5x wider. Roll is a lower bound when
  genuine price movement is present, so the earlier diagnostic *under*
  stated how much bounce LLY's price can carry, not over.
- **Mid outside the traded range 12.7% of the time (LLY) is expected,
  not a fault.** The mid is read at the boundary; the high and low bound
  trades *inside* the minute. With a 76-cent spread on a name that
  prints 127 times a minute, a boundary mid sitting a few cents outside
  the minute's own trade range is the normal case, and it is more common
  for LLY (wide spread) than XOM (7.2%, two-cent spread) exactly as it
  should be. No split falls inside this window for these names, so this
  is not a scale mismatch — a scale mismatch would read ~100%.

## 3. The decisive test — the bounce diagnostic redone on the midpoint

Lag-one autocorrelation of one-minute returns, within session, over the
*same minutes* for both price definitions.

| stock | last trade | **midpoint** | standard error | pairs |
|---|---|---|---|---|
| **LLY** | **−0.0456** | **+0.0134** | 0.0028 | 126,261 |
| XOM (partial) | −0.0095 | −0.0052 | 0.0041 | 59,816 |

**LLY's −0.0456 reproduces P4's −0.0455 to four decimals** on a
different window, which says the two measurements agree about what the
trade price does.

**On the midpoint it is gone.** −0.0456 → +0.0134: not merely smaller,
but the other side of zero, a move of sixteen standard errors. The
one-minute mean reversion that made LLY look predictable is a property
of *which side of the spread the last print landed on*, and it does not
exist in the value the market was quoting.

XOM has no flip to remove in either price, which is what P4 found and
what its zero gain reflected.

## 4. What this means

P4 established that the entire H=1 gain is LLY (20 folds of 20 in both
models) while XOM loses, and that the ranking of the gain across names
is the ranking of the flip. This adds the missing half: the flip itself
is not in the price, only in the print. So the ordering P4 found is an
ordering of an artefact, and the artefact vanishes under the price
definition the microstructure literature uses as its target.

That is **not yet proof the H=1 gain vanishes** — that needs the run,
under `price_field: "mid"` against the `close` control, everything else
byte-identical. Two things stand between here and that run:

1. **XOM, JPM, WMT and AAPL are still pulling.** XOM is half done and
   completes tonight; the other three are ~4 hours behind it.
2. **The price field does not reach the scan yet.** `BarsFromStore` now
   attaches `mid`/`bid`/`ask`/`spread` to every bar (ADR-0065), but
   `SessionFeatureRows` lifts only OHLCV into its frames and
   `_tapes_from_bars` hardcodes `frame["close"]` in the frame branch
   while honouring `price_field` in the dict branch. Both need widening
   before a mid run is possible.

The honest position is unchanged in direction and much firmer in
degree: **treat H=1 as an artefact until the mid run says otherwise.**

## Sources

- `tools/pull_quote_minutes.py`, `tools/p4_mid_diagnostics.py`,
  `p4-mid-results.json` (this branch) — every number above.
- `docs/architecture/decision-log.md`, ADR-0065 — the reduction and its
  cost.
- `children/intraday_equities/configs/source-alpaca-quotes-backfill.json`
  — the declared window, the pull order, and the data cut.
- `RESULT-P4-diagnostics.md` on `feat/p4-bounce-diagnostics` — the
  −0.0455 this reproduces, and the per-name split of the H=1 gain.
- Roll (1984); Working (1960); Chordia–Roll–Subrahmanyam on midquote
  returns, via
  `children/intraday_equities/docs/research/p4-price-definition-bounce-diagnostic-and-the-close-vwap-mid-comparison.md`.
