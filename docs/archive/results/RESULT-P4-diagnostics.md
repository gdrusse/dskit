# RESULT — P4 free bounce diagnostics

Date: 2026-09-03. Branch `feat/p4-bounce-diagnostics`, worktree `~/wt/p4-bounce`.
Plan item **P4** of `docs/plans/2026-09-horizon-search.md`.

Nothing was pulled and no pipeline was run. Every number below comes from
minute bars already in `children/intraday_equities/ob` (alpaca-sip), regular
hours only, **stopping at 2026-02-27 15:59 ET** — the last bar before the
2026-02-28 cut. The one exception is Table 6, which re-reads `carry.json`
from the twenty h01 folds that were already on disk.

Re-run with:

```bash
cd ~/dskit && source .venv/bin/activate
cd children/intraday_equities
python ../../tools/p4_bounce_diagnostics.py --root ./ob --runs ./pipeline_runs \
  --json /tmp/p4.json
```

Two windows are reported. **"WF era" is 2020-05-01 onward** — the first h01
fold validates 2022-05-06 on a 730-day training window, so no fold reads a
bar before that date. It is the window the H=1 result actually came from.
"Full" is 2016-01-04 onward and is shown because the tape spans a 5x move in
price, which moves every relative spread.

---

## Table 1 — Check 1. Lag-1 autocorrelation of 1-minute last-trade returns

Pure bid-ask bounce makes this negative. Standard error is about 0.0013 in
the WF era and 0.0010 on the full tape, so anything under ±0.003 is noise.

**WF era (2020-05-01 → 2026-02-27)**

| stock | whole day | open 30m | midday | close 30m | pairs |
|---|---|---|---|---|---|
| JPM | −0.0064 | −0.0033 | −0.0069 | −0.0152 | 564,741 |
| LLY | **−0.0455** | −0.0377 | −0.0502 | −0.0406 | 553,199 |
| XOM | **+0.0003** | −0.0035 | +0.0018 | +0.0026 | 564,392 |

**Full tape (2016-01-04 → 2026-02-27)**

| stock | whole day | open 30m | midday | close 30m | autocovariance (whole day) |
|---|---|---|---|---|---|
| JPM | −0.0089 | +0.0055 | −0.0103 | −0.0463 | −4.15e−09 |
| LLY | −0.0432 | −0.0453 | −0.0416 | −0.0494 | −2.71e−08 |
| XOM | −0.0022 | −0.0005 | −0.0026 | −0.0054 | −1.15e−09 |

Reading: bounce is **present and material in LLY only**. JPM's is real but
tiny. XOM's is indistinguishable from zero in the WF era and turns *positive*
midday and into the close — genuine continuation there is large enough to
cancel the bounce outright. Year by year, both JPM and XOM go positive in
2020 and 2021 (JPM +0.012 and +0.008; XOM +0.013 and +0.015), when volatility
was high enough to swamp the spread. LLY is negative in every one of the
eleven years.

## Table 2 — Check 2. Roll-implied spread against a real spread

Roll: half-spread = √(−autocovariance), in relative terms; multiplied by the
period's median price to get dollars. Undefined where the autocovariance is
non-negative.

**WF era, whole day**

| stock | median price | implied spread (bps) | implied spread ($) | plausible real spread |
|---|---|---|---|---|
| JPM | $154.9 | 1.07 | **$0.017** | ~$0.01 (one tick) |
| LLY | $367.6 | 3.63 | **$0.133** | ~$0.05–0.25 |
| XOM | $104.2 | — (autocov ≥ 0) | — | ~$0.01 (one tick) |

**LLY by year** — the clearest case, because LLY's price went from $76 to
$1,046 across the sample:

| year | median price | implied spread ($) | implied spread (bps) | lag-1 |
|---|---|---|---|---|
| 2016 | 76.44 | 0.018 | 2.38 | −0.034 |
| 2019 | 115.39 | 0.024 | 2.07 | −0.030 |
| 2021 | 229.38 | 0.101 | 4.42 | −0.082 |
| 2023 | 452.10 | 0.149 | 3.30 | −0.048 |
| 2024 | 796.50 | 0.311 | 3.90 | −0.049 |
| 2025 | 804.25 | 0.325 | 4.04 | −0.043 |

**Verdict on size: yes, consistent.** Every implied spread lands between half
a cent and a third of a dollar, and each tracks its own price level — a
roughly constant 1–4 bps, which is what these names really trade at. Nothing
here is off by an order of magnitude in either direction, so bounce is a real
component of the lag-1 autocovariance, not a rounding artefact. But it is
also *small*: at JPM and XOM it accounts for a correlation of well under one
percent, which caps how much predictability it can possibly carry.

## Table 3 — Check 3. The same test on `vwap` (the minute's average trade)

Bounce in an average of *n* prints shrinks like 1/n. With 127–318 trades in a
typical minute, the bounce term should fall by more than two orders of
magnitude — a predicted lag-1 of about −0.0002 for LLY.

**WF era, whole day**

| stock | lag-1 on last trade | lag-1 on vwap | predicted vwap bounce | mean trades/min |
|---|---|---|---|---|
| JPM | −0.0064 | **+0.2102** | ≈ −0.00002 | 345 |
| LLY | −0.0455 | **+0.1890** | ≈ −0.0002 | 189 |
| XOM | +0.0003 | **+0.2078** | ≈ 0 | 408 |

The bounce does vanish, exactly as predicted. **But `vwap` is not usable as a
substitute price.** All three names, in every time-of-day bucket, sit at
+0.19 to +0.25 — which is Working's (1960) ceiling of +0.25 for first
differences of an *averaged* random-walk price. It is a manufactured momentum
whose lag-1 R² is about **4.4%** — more than twenty times the largest real
one measured here (LLY's 0.21%) and some 20–50x the 0.09–0.23% gain we are
trying to explain. A surviving gain under `price_field: vwap` would prove
nothing at all.

**This kills arm B of the P4 recommended design.** Only quote-midpoint (arm
C) can settle the question by re-running.

## Table 4 — Trading intensity

| stock | window | median trades/min | mean | % of minutes ≤5 | ≤10 | ≤30 |
|---|---|---|---|---|---|---|
| JPM | full | 230 | 305 | 0.037% | 0.045% | 0.111% |
| LLY | full | **92** | 145 | 0.174% | 0.747% | **8.52%** |
| XOM | full | 240 | 319 | 0.039% | 0.053% | 0.376% |
| JPM | WF era | 263 | 345 | 0.035% | 0.046% | 0.052% |
| LLY | WF era | **127** | 189 | 0.017% | 0.066% | **2.65%** |
| XOM | WF era | 318 | 408 | 0.044% | 0.063% | 0.075% |

Genuinely thin minutes are rare everywhere — under 0.1% of minutes have ten
or fewer prints. LLY is the outlier at every threshold: 2.5x fewer trades per
minute than the other two, and 35–50x more minutes under thirty trades.

## Table 5 — Check 4. How much of the H=1 gain the shared noise can explain

The label at H=1 is `−px[t] + px[t+1]`, residual to SPY and divided by the
causal 390-bar sigma; `ret_lag_0` is `+px[t] − px[t−1]`. They share the flip
at `t`. This was measured **in the run's own label space**, by importing the
pipeline's `_LeadLabel` with the h01 documents' knobs, and **on the run's own
5-minute grid rows**. `ret_lag_0` is the minimum-variance carrier of that
shared term, so its R² bounds what any model can extract from it. Everything
is in percent, the same units as the Clark–West gain.

**WF era, grid rows**

| stock | corr(label, ret_lag_0) | R², one slope | R², refit per volatility decile* |
|---|---|---|---|
| JPM | −0.0082 | 0.0067% | 0.0196% |
| LLY | −0.0259 | **0.0670%** | **0.1926%** |
| XOM | −0.0003 | 0.00001% | 0.0033% |

\* the per-decile column is measured on all rows, not just grid rows, so it
is generous.

**Pooled the way the scan pools, WF era, grid rows (n = 334,008)**

| what the model is allowed | ceiling | vs ridge +0.0898% | vs LightGBM +0.2337% |
|---|---|---|---|
| one slope shared by all three names | **0.0124%** | 14% | 5% |
| a slope per name | **0.0243%** | 27% | 10% |
| a slope per name **and** per volatility decile | **0.0433%** | **48%** | **19%** |

The correlation is negative in all three names — the right sign for bounce.
The ceiling is in-sample and measured over the whole era, whereas the gain is
out-of-sample per fold, so it is if anything an over-estimate. Taken at face
value, the shared flip explains at most about half the ridge gain and a fifth
of the LightGBM gain.

## Table 6 — What the pooled gain was actually made of

The pooled numbers above hide the finding. Re-reading the twenty h01 folds
already on disk (`carry.json`, `(mspe_mean − mspe_model)/mspe_mean` per symbol
per fold — no pipeline was run):

| stock | ridge gain | folds positive | LightGBM gain | folds positive | lag-1 (WF era) | bounce ceiling |
|---|---|---|---|---|---|---|
| **LLY** | **+0.351%** | **20/20** | **+0.694%** | **20/20** | −0.0455 | 0.067–0.193% |
| JPM | +0.046% | 12/20 | +0.073% | 13/20 | −0.0064 | 0.007–0.020% |
| XOM | **−0.119%** | 3/20 | **−0.047%** | 8/20 | +0.0003 | ~0% |
| pooled | +0.093% | — | +0.240% | — | — | 0.012–0.043% |

**The ordering is exact.** The stock with the most bounce has all the gain
and wins every fold. The stock with measurably no bounce has a *negative*
gain and loses most folds. JPM sits in the middle on both, at a coin flip.
The "positive result at H=1" is a single stock, and it is the one stock whose
price flips hardest — the widest spread ($0.13), the fewest trades per minute
(127), and the only name negative in all eleven years.

---

## Verdict

**Consistent with bounce — the H=1 result is not established as a real edge,
and it is not a three-stock result at all.** All of the positive H=1 number is
LLY: it wins 20 folds out of 20 in both models, while XOM loses and JPM is a
coin flip. LLY is also, by every measurement here, the stock whose price flips
hardest between the buyer's and the seller's side: the widest spread at about
thirteen cents, the fewest trades per minute, the largest negative one-minute
autocorrelation, and the only one of the three that is negative in every year
of the tape. XOM, whose flip measures to zero, has no edge to explain. The
ranking of the edge across the three stocks is exactly the ranking of the
flip, which is what an artefact looks like and is not what a real signal
looks like. On size alone the case is not airtight — the flip in `ret_lag_0`
accounts for roughly a fifth to a half of what the models earn, not all of it
— but `ret_lag_0` only carries the flip in the *last* bar, and every other
transitory effect that makes a wide-spread, thinly-traded stock look
mean-reverting scales with exactly the same thing and cannot be separated
from it with trade prices alone. **The honest position is that no horizon is
proven yet.** Strictly, separating the last two-thirds needs quote data; but
the burden of proof has moved decisively, and a 1-minute edge that exists in
one stock and points the wrong way in another should not be built on. Two
things follow: the vwap re-run must be dropped (it manufactures a fake
momentum more than twenty times larger than the effect being tested), and the
next useful step is either the quote-midpoint arm or the free ablation — drop
`ret_lag_0` and `ret_lag_1` and see what is left of LLY.

## Sources

- `tools/p4_bounce_diagnostics.py` (this branch) — every number above.
- `children/intraday_equities/docs/research/p4-price-definition-bounce-diagnostic-and-the-close-vwap-mid-comparison.md` — the four checks.
- Roll (1984), J. Finance 39(4) 1127–1139.
- Working (1960), Econometrica 28(4) 916–918 — the +0.25 ceiling in Table 3.
- `children/intraday_equities/pipeline_runs/intraday-equities-multi3-h01-{ridge,lgbm}-wf-*/carry.json` — Table 6.
