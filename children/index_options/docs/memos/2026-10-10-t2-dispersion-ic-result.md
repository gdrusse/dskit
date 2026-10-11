# T2 forecast-dispersion IC — result, 2026-10-10

## TL;DR

**T2 FAILS its pre-registered rule.** `beyond_market` (partial rank IC of forecast sd with |realized 31-day log move| given the straddle ratio): **IC 0.0331, t 1.15** over 91 weekly stamps (45 names per stamp, median), needs t >= 2.5. The forecast's scale carries **no demonstrated information beyond the option market's**. Both controls are strong and near each other: `forecast_alone` IC 0.411 (t 22.0), `implied_alone` IC 0.453 (t 29.2) — the forecast orders names well on its own, and the market orders them better.

Run `forecast-dispersion-ic-2025-12-31-a3f9db74` (document `c3a202f0…`, 18 nodes, origin/main `d7d309d` + amendment 2), under `/home/russell/data/index_options/production-universe-rerun-20261008/forecast-dispersion-ic/`. Nothing fitted, no 2026 data, no band / `h_steps` / threshold touched.

## Numbers (4,299 scored rows, 91 stamps, all `usable`, nothing dropped inside the IC)

| measure | IC | t | frac_pos |
|---|---|---|---|
| beyond_market (T2) | 0.0331 | 1.15 | 0.60 |
| forecast_alone | 0.4113 | 22.05 | 1.00 |
| implied_alone | 0.4527 | 29.18 | 1.00 |

By phase (IC, t, stamps): beyond_market calibration −0.014 (−0.36, 48), **entry 0.086 (2.20, 43)**; forecast_alone 0.392 / 0.433; implied_alone 0.448 / 0.458.
By month, beyond_market mean IC only (21 months, 12 positive, each month 4-5 stamps): 24-02 .041, 03 −.103, 04 .008, 05 −.128, 06 −.087, 07 −.019, 08 −.138, 09 .192, 10 .171, 11 .089, 12 −.187; 25-02 .105, 03 .120, 04 .276, 05 .228, 06 .030, 07 −.116, 08 −.030, 09 .047, 10 −.007, 11 .217. Controls positive in all 21.

Drop census: 5,723 straddle rows; 155 without a basis-reconciled forecast (2.7%, under the 10% guard), 0 without a realized pair; `scored` kept 4,299 (1,424 left, the 155 among them); `phase` "outside" matched nothing (expected).

## How the first run's refusal was resolved (ADR-0259 amendment 2)

The first run REFUSED at `with_forecast` (ANET 2024-11-05: `spot` 405.12 on the stream, None in the table; A1131). A read-only census showed `spot` null on 80,112 of 99,225 saved forecast rows (80.7%, every name) by design: the replay's basis gate refused an as-traded price (unknown_historical_price_basis 74,062; outside_declared_phase 4,579; irregular_postdate_split_basis 982; source_vintage_unreconciled 247; split_inside_holding_window 242). Fix: a counted `forecast_priced` filter (`spot > 0`) before the attachment, as `implied_priced` does on the straddle side; the exact `spot` agreement and the 0.1 guard stand. A scratch run through `with_forecast` only (A1133, no IC) measured the 2.7% absence before the real run. The amendment was written before any IC existed.

## Limits (read before using this)

- Scored on names/dates whose price basis the replay could reconcile (~19% of forecast rows, but 97% of straddle rows); the unknown-basis names wait on the owner's corporate-action inventories.
- The phases are the v2 replay's windows (calibration 2024-02..12, entry 2025-02..11). Entry reads beyond_market 0.086 / t 2.20: below the rule, not zero. That is a post-hoc split reading and does not rescue the pass rule; whether the forecasts in each window were out of sample for the scale was not checked here.
- Overlapping 31-day labels, weekly stamps: HAC `h_steps` 5 as pre-registered. n per stamp is 32-71 names.
- Single run, single configuration. No multiple-comparison budget was spent beyond the one pre-registered rule.

## Reading

Per the lane note: T2 fails, so the premium program with the forecast as a risk filter is the branch; the forecast leaves selection. The dispersion book (T5 S1) is not justified by this test.
