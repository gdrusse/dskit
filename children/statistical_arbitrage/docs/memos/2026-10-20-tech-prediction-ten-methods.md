## TL;DR

Ten pre-registered additions to the ADR-0272 lagged-spread model, now drawing on Theta's EOD Greeks, vendor IV, open interest, volume, quote sizes, term structure and stock realized volatility, were tuned on 2020 and validated once on 2021–2025. **On the primary next-session label none meets the pre-registered "improves the model" rule** (pooled R²oos above the baseline, BH-rejected Diebold–Mariano, and a per-fold win in at least four of five years). The one method that comes close is the 30→90-day **term-structure slope difference**: ATM 20-session R²oos +0.283 against the baseline's +0.214 (+0.069, four of five folds, DM p 0.035, not BH-significant among ten); on the secondary same-day label it is +0.404 against +0.319 and BH-significant. Vendor-Greek positioning, realized volatility, Lasso and boosting are worse than the baseline in every cell. This is a forecastability result about a measured mid-surface series, not a trade rule, a P&L or an edge.

## Execution contract

- **Authority:** ADR-0273 (proposed 2026-10-20, written before code under the owner's request "try 10 more methods; check the Theta data store for Greeks; change the JSON"), on top of ADR-0272 and its Amendment 1. No trade, sizing, fill, P&L, paper or live action.
- **Theta data used (new):** the same 444 pinned Parquet members as every prior run, now decoding the vendor's end-of-day Greeks (`delta, gamma, vega, theta, vanna, charm, vomma`), `implied_vol` and its solver residual `iv_error`, `open_interest`, `volume`, trade `count`, and quote sizes beside the NBBO. One data pass, `configs/run-tech-structural-greeks.json` (hash `07cad7c0…0957`, run `tech-structural-greeks-20261020-2026-10-20-a15af4ce`), writes two immutable artifacts under `/home/russell/data/statistical_arbitrage/structural-greeks-20261020/`: `surface_term_rows.jsonl` (12,000 rows: 30±7 and 90±15 DTE surfaces with the 30→90 term slope, straddle ratio, parity put-call IV spread and expiry-scope OI/volume aggregates; SHA-256 `11914406de22…`) and `positioning_rows.jsonl` (6,000 symbol-days, all usable, about 400 contracts each after filters: OI-weighted vendor Greeks per right, net and signed total, with gamma scaled by spot² and delta/vanna/charm by spot so the sums survive the three splits; OI-vega-weighted vendor IV; OI and volume put/call ratios; quoted-size imbalance; SHA-256 `4cf5578a36ed…`). Contracts with a placeholder IV (≤ 0, about 4%), a solver residual above 0.01 or no open interest are excluded and counted. Vendor numbers are summed and ratioed, never re-derived.
- **The ten methods** (each is the frozen `lagged` features, prior-60 Z and 5-session change, plus the listed columns; linear methods use `StandardizedEstimator(Ridge)`, train-only standardization): (1) multi-scale Z at 20 and 120 sessions plus the 60-session spread sd; (2) each leg's own 60-session Z; (3) smile shape: butterfly spread, half-spread-over-premium spread, and the other surface measure's spread; (4) 1- and 20-session spread changes; (5) term structure: differences of the 30→90 slope, the log expiry-scope OI put/call ratio and the straddle ratio; (6) positioning: differences and 60-session Z of dollar net gamma, log OI-vega, net vanna, log OI put/call and log volume put/call, plus the vega-weighted-IV and size-imbalance differences; (7) realized vol: each leg's 20-session realized vol of adjusted-close log returns and each leg's implied-minus-realized; (8) one pooled Ridge across the three pairs on (1)+(4); (9) `StandardizedEstimator(Lasso)` on the union of (1)–(7); (10) `HistGradientBoostingRegressor` (depth 3, rate 0.05, 20-row leaves) on the same union. Baselines: `mean` and the exact ADR-0272 `lagged` model (raw features, alpha 100 ATM / 0.1 skew).
- **Clock, folds, tests:** as ADR-0272: decision after the t close, weekly expanding refits from 2018-12-31 on labels known strictly before each block, 2019 warm-up, 2020 development, 2021–2025 annual validation run once per clock, Newey–West lags h−1, DM h_steps = h, 21-day block bootstrap for IC. **Primary label** is the next-session label (`label_offset` 1); the same-day label is a secondary, reported, not in the family. Each cell's scorer drops a row that lacks any model's forecast, so all twelve models are scored on the same rows; the union-feature models (9, 10) lose the most rows (589 per ATM cell), which is why the baseline's numbers here differ slightly from ADR-0272's.
- **Tuning:** one knob per method on the 2020 fold of the primary clock, grid declared in ADR-0273, rule as ADR-0272; frozen in `configs/prediction-methods-selection.json` (digest `436cdad1b421…`) with every development mean recorded. Chosen: ATM Ridge alphas 100/10/0.1/0.1/100/100/0.1/100, Lasso 0.01, boosting 50 iterations; skew 100/10/0.1/100/10/100/100/100, Lasso 0.001, 50.
- **Family (pre-registered, per target × horizon, primary clock):** ten method-versus-lagged DM comparisons and ten method-versus-mean gaps, BH 5%. "Improves the model" = pooled R²oos above `lagged`, BH-rejected comparison, and the per-fold R²oos above `lagged` in at least four of five folds.
- **Development attempts (disclosed):** the development document ran three times. Attempt 1 failed at the scorer because the planned 60-session Z of the term slope was null on every row (the 90-day expiry is missing on about a tenth of days and the no-bridging window rule refuses any hole); the feature was dropped. Attempt 2 completed but the positioning method's R²oos was −86 (ATM) and −600 (skew): on the 2020-08-31 AAPL split day the vendor's open interest resets and the raw OI put/call ratio difference reached +1,220 (a 5,800-sigma value), so ratios are now differenced in log space (a per-field `transform` on `PairPanelFeatures`) and the OI-ratio change was dropped. Attempt 3 (hash `b9c46393…b740`, run `…-ac9633d0`) is the one the selection rests on. Both fixes preceded any validation run and are recorded in ADR-0273.
- **Commands** (worktree root, `PYTHONPATH=children/statistical_arbitrage:.`, `/home/russell/dskit/.venv/bin/python`): `python -m dskit.pipeline run children/statistical_arbitrage/configs/run-tech-structural-greeks.json --asof 2026-10-20`, then `run-tech-prediction-methods-{development,validation,validation-sameday}.json`.

## Implementation evidence

Three generic nodes by import path (ADR-0273): `libs/chain_greeks.py:ChainDayPositioning` (6 unit tests), `libs/pair_spread.py:PairPanelFeatures` (5 tests, including the log transform), `libs/sklearn.py:StandardizedEstimator` (2 tests); child pins extended by 8 tests (same Theta members and digests, frozen baselines, shared clock, nested features, frozen selection, no ledger/order kinds). Focused suite: 1,298 passed; the one failure is main's pre-existing lightgbm-extra pin. Ruff clean on touched files. Document hashes: development `b9c46393…b740`, validation `fb61fad5…2c17` (run `…-e7beb0a7`), same-day `2d65b9a7…be5e` (run `…-23ac087c`); each run's `resolved.json` carries its hash.

## Validation 2021–2025, primary next-session label

Pooled R²oos against the expanding mean; Δ is the pooled R²oos minus `lagged` on the same rows; "folds" counts years in which the method's R²oos exceeds `lagged`'s; DM is the pooled Diebold–Mariano t and one-sided p of the squared-error gap against `lagged` (lags h−1); BH is the family decision. The baseline `lagged` row shows its own per-fold R²oos.

**ATM, 5 sessions** (3,109 rows; `lagged` +0.085, folds +0.036 / +0.023 / +0.059 / +0.119 / +0.120; IC +0.242, hit 0.567)

| Method | R²oos | Δ | IC | folds | DM t | DM p | BH |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 multi-scale Z | +0.082 | −0.003 | +0.247 | 2/5 | −1.15 | 0.876 | no |
| 2 leg Z | +0.083 | −0.002 | +0.244 | 2/5 | −0.59 | 0.724 | no |
| 3 smile | +0.081 | −0.003 | +0.244 | 2/5 | −1.23 | 0.891 | no |
| 4 changes | +0.084 | −0.000 | +0.245 | 3/5 | −0.34 | 0.635 | no |
| 5 term | +0.094 | +0.009 | +0.257 | 3/5 | +0.89 | 0.187 | no |
| 6 positioning | +0.080 | −0.005 | +0.240 | 2/5 | −0.74 | 0.772 | no |
| 7 realized | +0.075 | −0.010 | +0.229 | 2/5 | −1.30 | 0.903 | no |
| 8 pooled | +0.084 | −0.000 | +0.250 | 2/5 | −0.51 | 0.695 | no |
| 9 Lasso | +0.015 | −0.069 | +0.156 | 0/5 | −3.55 | 1.000 | no |
| 10 boosting | +0.091 | +0.006 | +0.241 | 2/5 | +0.05 | 0.481 | no |

**ATM, 20 sessions** (3,064 rows; `lagged` +0.214, folds +0.165 / +0.128 / +0.258 / +0.312 / +0.097; IC +0.477, hit 0.660)

| Method | R²oos | Δ | IC | folds | DM t | DM p | BH |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 multi-scale Z | +0.182 | −0.032 | +0.458 | 2/5 | −1.91 | 0.972 | no |
| 2 leg Z | +0.239 | +0.025 | +0.505 | 3/5 | +2.39 | 0.008 | **yes** |
| 3 smile | +0.214 | +0.000 | +0.488 | 2/5 | −0.19 | 0.575 | no |
| 4 changes | +0.217 | +0.003 | +0.471 | 2/5 | +0.06 | 0.476 | no |
| 5 term | +0.283 | +0.069 | +0.550 | 4/5 | +1.81 | 0.035 | no |
| 6 positioning | +0.177 | −0.037 | +0.432 | 3/5 | −1.72 | 0.957 | no |
| 7 realized | +0.173 | −0.041 | +0.472 | 2/5 | −1.32 | 0.906 | no |
| 8 pooled | +0.192 | −0.022 | +0.456 | 1/5 | −1.91 | 0.972 | no |
| 9 Lasso | +0.238 | +0.024 | +0.503 | 4/5 | +0.03 | 0.489 | no |
| 10 boosting | +0.155 | −0.059 | +0.449 | 2/5 | −1.64 | 0.949 | no |

Method 2 is BH-rejected but wins only three folds (its 2021 and 2022 R²oos trail the baseline); method 5 wins four folds and adds +0.069 but its DM p of 0.035 does not survive BH among ten comparisons (its 2022 fold is −0.156 against the baseline's +0.128, a single bad year). Neither satisfies all three pre-registered conditions. Term-slope coefficients over the 773 validation blocks: the slope-difference coefficient is positive in every block (mean +0.0147 in standardized units, sd 0.0035): a pair whose front end is steeper relative to its back end sees its ATM spread rise over the next 20 sessions, consistent with term-structure mean reversion rather than noise.

**Skew, 5 sessions** (2,755 rows; `lagged` +0.006, folds +0.045 / +0.016 / −0.005 / +0.019 / +0.036) and **skew, 20 sessions** (2,710 rows; `lagged` +0.022, folds +0.040 / +0.018 / +0.001 / +0.045 / +0.074): no method is BH-rejected. The closest are method 4 (skew 20: +0.038, Δ +0.016, 5/5 folds, DM p 0.082) and method 8 (skew 20: +0.046, Δ +0.024, 5/5 folds, DM p 0.044); methods 2, 6, 9 and 10 are significantly worse than the baseline (DM t −2.7 to −4.2). The skew spreads remain essentially unforecastable on the next-session label, as ADR-0272 found.

## Secondary same-day label (reported, outside the family)

On the same-day label (which includes the reversal of the decision day's own surface measurement) the baseline is far stronger (ATM +0.193 / +0.319, skew +0.264 / +0.331) and several methods are BH-rejected: ATM 5-session, methods 2, 3, 4, 5 and 8 (Δ +0.022 to +0.033, four of five folds each); ATM 20-session, methods 2 (+0.021, 3/5) and 5 (**+0.086, 4/5, DM p 0.021**); skew 5-session, methods 4 and 8 (+0.045 / +0.051, 3/5 and 4/5). Method 5 (term structure) is therefore the only addition that is positive on both clocks for the ATM 20-session target with four winning folds on each, and it is the only candidate this memo would carry to a separate confirmation. Positioning (6) is worse than the baseline in all eight cells on both clocks; boosting (10) and Lasso (9) are worse in six and five of eight.

## What this does and does not say

- Theta's vendor Greeks, open interest, volume and quote sizes are now read, cleaned and summed correctly (split-safe), and they do not improve the forecast of either pair spread at 5 or 20 sessions with these aggregates and this linear/boosted family. That is a negative result about these features as used, not about the data: contract-level positioning by strike (a gamma profile rather than a net sum), flow (volume signed by side) and intraday timing were not tried.
- The term-structure result is a candidate, not a finding: one of ten, p 0.035 before correction, one bad year, and the same-day confirmation is on a noisier label. A separately registered single-hypothesis check on the untouched later data would be the next step if the owner wants one; re-tuning on 2021–2025 is not.
- Development selection rested on one year (2020) in which the skew folds are mostly one pair; the Ridge alpha grid barely matters for the linear methods (differences of 0.01), but the boosting and Lasso knobs do, and both were chosen on thin evidence.
- No row is imputed; models are compared on exactly the rows every model could score, which removes the 2020-08-31 split neighbourhood and the days without a 90-day expiry from every comparison.

## Reproducibility and handoff

- Artifacts: `/home/russell/data/statistical_arbitrage/prediction-methods-20261020/{development,validation,validation-sameday}/` (`rows_*.jsonl` with every forecast column, `fits_*.jsonl` per block, `skill_*.jsonl` and `skill_*.json`); summaries SHA-256 (validation) ATM h5 `0451c88f93b3…`, ATM h20 `58578c5e6d3d…`, skew h5 `bcfba28a3f6c…`, skew h20 `2ed3876571d3…`; (development) `a1317151f672…`, `7f56c4bb6d52…`, `2c4a1788b8af…`, `ec5ce0b110c5…`. Config digests: Greeks pass `6980c8aebb42…`, development `47f864e5820d…`, validation `4f937dd8bc66…`, same-day `f5cdcfecc523…`, selection `436cdad1b421…`. Every write node declares `overwrite: false`; a rerun needs new output paths. Two failed development attempts' run directories were removed and their partial outputs deleted before the third attempt; nothing reviewed was touched.
- Review record and next authorized action: filled at closure in the lane note; this memo authorizes nothing.
