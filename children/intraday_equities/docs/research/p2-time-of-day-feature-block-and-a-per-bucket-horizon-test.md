# P2 — time of day: feature block and a per-bucket horizon test

Date: 2026-09-03. Research only; nothing run. Plan item P2 of
`docs/plans/2026-09-horizon-search.md`.

## Question

What is known about intraday seasonality, how do short-horizon models encode
time of day, should labels cross the close, and how do we test whether the
best look-ahead differs by time of day?

## What the tree already has (read before building)

`session_feature_names` in `intraday_equities/nodes.py` already emits
`minutes_from_open`, `minutes_to_close`, `tod_sin/cos`, `dow_sin/cos`,
`month_sin/cos`, `is_first_rth`, `is_last_rth`, `overnight_gap`,
`session_gap_days`, `after_holiday`. Three gaps: (1) `tod_sin/cos` uses
period = RTH span, so 09:30 and 16:00 sit at the same point on the circle;
(2) `is_first_rth`/`is_last_rth` flag one bar, not a window; (3) no
training-only time-of-day statistics exist. `_LeadLabel` sets the 1-minute
return NaN across any gap over two bars, so a label that would cross the
close is already dropped. Side effect: for lead h the last h minutes of every
session have no rows, so horizon curves are scored on different clocks.

## Finding

(a) Seasonality. Volume and volatility are U-shaped over the day — high at
the open, a trough around lunch, rising into the close (Andersen–Bollerslev
1997; Boudt–Croux–Laurent 2011). The close has grown: the closing auction is
~10% of daily volume and the last half hour >25% for S&P 500 names (NYSE
2024). The first half-hour market return predicts the last half-hour return
(Gao–Han–Li–Zhou 2018, JFE). Cross-sectionally, returns continue at
half-hour lags that are whole days apart, for at least 40 days
(Heston–Korajczyk–Sadka 2010, JF) — Bogousslavsky 2016 ties it to
periodic rebalancing. Overnight and intraday returns have opposite signs
for many strategies (Lou–Polk–Skouras 2019); a high overnight return tends
to be followed by a low intraday return (Berkman et al. 2012). Day-of-week
effects in the market are weak since the 1990s but survive in
cross-sectional anomalies (Birru 2018); month-end reversals are driven by
institutional cash needs (Etula et al. 2020, RFS).

(b) Encoding. Practice uses minutes-since-open, minutes-to-close, half-hour
dummies (the HKS unit), and cyclic sin/cos of the clock. For 1-minute work
the strongest single fix is Andersen–Bollerslev deseasonalisation: divide
returns by a time-of-day volatility factor s(m) estimated on training data
only. With ADR-0059's `sigma_t` (390-bar rolling std, a daily level) the
right label scale is `sigma_t * sqrt(sum_{j=1..h} s(m+j)^2)`, i.e. daily level
times the seasonal shape of the next h minutes.

(c) Labels crossing the close. Drop, as the tree already does: the overnight
move is a different process. Keep `overnight_gap` as a feature at the open,
and add the last-h-minutes coverage fix below so horizons are comparable.

(d) Predictability by time of day. Machine-learning studies find market and
stock predictability is short-lived and highest in the middle of the day
(Huddleston–Liu–Stentoft 2023, JFEc; Liu–Stentoft 2023). No source reports
the best horizon per bucket directly — that is the test to run.

## Recommended feature block (all from bar timestamps; training-only marked *)

- `tod_frac` = minutes_from_open / 390. Replace `tod_sin/cos` with period
  2×span (half circle) so open and close differ.
- `hh_bucket` = minutes_from_open // 30 (0–12; integer for LightGBM,
  13 one-hots `hh_00`…`hh_12` for ridge).
- `is_open30`, `is_lunch` (12:00–13:00), `is_close30` (≥15:30),
  `is_close5` (≥15:55): the three named windows plus the auction run-in.
- `gap_x_open30` = overnight_gap × is_open30 (open reversal).
- `ret_since_open` (cross_session false) and `ret_first30` frozen after
  10:00 (Gao et al. momentum into the close).
- `dow_mon`…`dow_fri` one-hots; `is_month_last3`, `is_month_first2`.
- * `tod_vol[m]`: std of 1-minute returns by minute-of-day, per symbol,
  training fold only, smoothed 5 minutes. Emit `tod_vol_now` and
  `tod_vol_lead_h` = sqrt(sum s(m+j)^2). Use both as inputs and as the
  ADR-0059 label scale.
- * `tod_mean[m]`: mean 1-minute return by half-hour bucket, training fold.

## Test: does the best look-ahead differ by time of day

1. Score every walk fold by `hh_bucket` × h: Clark–West gain and IC,
   on the common row set (rows where the longest h label exists), so
   every h sees the same clocks.
2. Per bucket, best h = argmax gain; block-bootstrap by session for a CI.
3. Headline: fit "one h for all" vs "h per bucket" on val; compare test
   gain. A per-bucket rule must beat pooled after P8's multiplicity bar.
4. Report a 13×6 grid (buckets × H) once per model; a bucket whose whole
   row is at the null is dropped from the trading clock.

## Sources

- Andersen & Bollerslev 1997, J. Empirical Finance — https://econpapers.repec.org/RePEc:eee:empfin:v:4:y:1997:i:2-3:p:115-158
- Boudt, Croux & Laurent 2011, J. Empirical Finance — https://www.sciencedirect.com/science/article/abs/pii/S0927539810000836
- Gao, Han, Li & Zhou 2018, JFE — https://www.sciencedirect.com/science/article/abs/pii/S0304405X18301351
- Heston, Korajczyk & Sadka 2010, JF — https://arxiv.org/abs/1005.3535
- Bogousslavsky 2016, JF — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12436
- Lou, Polk & Skouras 2019, JFE — https://personal.lse.ac.uk/polk/research/TugOfWar.pdf
- Huddleston, Liu & Stentoft 2023, J. Financial Econometrics — https://academic.oup.com/jfec/article-abstract/21/2/485/6400345
- Liu & Stentoft 2023, SSRN 4496917 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4496917
- Birru 2018, JFE — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2715063
- Etula, Rinne, Suominen & Vaittinen 2020, RFS — https://ideas.repec.org/a/oup/rfinst/v33y2020i1p75-111..html
- NYSE closing-auction share 2024 — https://www.nyse.com/data-insights/nyse-closing-auction-price-discovery-opportunities-reach-new-highs
- Intraday volume seasonality (arXiv 1810.12099) — https://arxiv.org/pdf/1810.12099
