## TL;DR

Information available at the date-t close does predict later changes of the AAPL/AMZN/GOOGL pair spreads of 30-DTE ATM implied volatility, but almost all of that predictability is the pair spread's own mean reversion carried by the lagged spread Z-score; the precision-network edge adds nothing and the correlation-gate edge adds a statistically detectable but practically negligible amount. On the pre-registered same-day label every model beats the mean in every cell, but a **post-hoc** next-session-label run (declared after the validation run was read, so that the decision day's own surface noise cannot be "predicted") is the honest reading: the ATM 20-session spread change keeps a pooled out-of-sample R² of **+0.20** (five annual folds all positive), the ATM 5-session change keeps **+0.08**, and the skew spreads keep almost nothing (**+0.005** at 5 sessions, where no model passes the pre-registered rule, and **+0.03** at 20, where the lagged and correlation models still pass it formally). This is a statement about forecastability of a measured mid-surface series, not a trade rule, a P&L or an arbitrage claim.

## Execution contract

- **Authority:** ADR-0272 (proposed 2026-10-20, written before code under the owner's instruction to proceed autonomously; owner ratification pending) with its Amendment 1. No trade, sizing, fill, P&L or paper/live action was authorized or produced.
- **Question:** for each unordered pair and session t, does date-t information predict `y_h = s(t+h) − s(t)`, where `s` is the pair difference of the 30-DTE ATM IV (vol points) or of the 25-delta put-minus-call skew, for h = 5 and 20 sessions?
- **Information sets, nested:** `mean` (expanding training mean of the label, the no-information benchmark), `lagged` (prior-60-session Z of the spread and its 5-session change), `correlation` (lagged plus the QQQ-residual Pearson-gate edge weight of the pair carried from the latest prior yearly fit, and its product with Z), `precision` (lagged plus the GraphicalLassoCV signed-partial-correlation edge weight, absolute, and its product with Z). The edge sets are the pinned ADR-0271 fits; nothing was refitted.
- **Evidence read:** `surface_rows.jsonl`, `graph_edges.jsonl`, `corr_graph_edges.jsonl` of the audited structural run, each pinned by the SHA-256 the v6 replay pinned (`RecordsFile`, digest verified on read; 6,000 / 2,980 / 4,997 rows). No replay artifact, ledger or quote is read.
- **Clock and leakage controls:** decision after the t close; features use the t surface, the 60 prior sessions and the yearly edge set fitted on the 252 sessions before its fit date. Labels are known at the close of session t+h and train only refit blocks that start strictly after that date (`SklearnExpandingRefit`, `known_ms_<h>` purge). Refits are weekly (7-day blocks from 2018-12-31), expanding, one Ridge per pair; 2019 is warm-up (first predicted block starts 2019-12-30). Overlapping labels: Newey–West lags h−1, Diebold–Mariano h_steps = h; IC intervals from a 21-calendar-day block bootstrap (1,000 replicates, seed 20261020, 90% level).
- **Tuning versus evaluation:** the development document scores only the 2020 fold, for one tuned quantity: the Ridge alpha of the `lagged` model from the grid {0.1, 1, 10, 100}, chosen per target as the argmax of the mean over both horizons of the pooled 2020 R²oos. Result: ATM 100 (0.1184 / 0.1192 / 0.1194 / 0.1234), skew 0.1 (0.1413 / 0.1381 / 0.1363 / 0.1289). The choice is frozen in `configs/prediction-development-selection.json` and pinned by digest in the validation document, which scores 2021–2025 once with that alpha applied unchanged to all three models.
- **Pre-registered family per target × horizon (BH 5%, one-sided):** `lagged`, `correlation`, `precision` versus `mean` (pooled Newey–West loss gap), and `precision` versus `lagged`, `correlation` versus `lagged` (Diebold–Mariano on squared-error gaps). A model "predicts" only when pooled 2021–2025 R²oos > 0, `skill_vs_mean` passes (pooled DM and across-fold t) and BH rejects.
- **Post-hoc robustness (disclosed, Amendment 1):** after reading the validation run, a third document identical except `label_offset` 1 (`y_h = s(t+1+h) − s(t+1)`) was declared and run once. It answers whether the predictability survives when the decision day's own surface measurement is excluded from the label. "Next-session label" moves only the label's start; the base `s(t+1)` is still a mid-surface mark with no bid/ask or fill, so it is not an executability claim.
- **Commands** (worktree root, `PYTHONPATH=children/statistical_arbitrage:.`, `/home/russell/dskit/.venv/bin/python`, scikit-learn 1.9.0): `python -m dskit.pipeline run children/statistical_arbitrage/configs/run-tech-prediction-{development,validation,validation-offset1}.json --asof 2026-10-20`.

## Implementation evidence

Three generic nodes were added by import path (ADR-0272): `kinds_table.RecordsFile`, `libs/pair_spread.PairSpreadHorizonRows`, `kinds_stats.ChronologicalForecastSkill`; the child adds no code, only JSON. Focused tests (file totals after the candidate): 14 pair-spread, 47 table-kind and 5 new forecast-skill unit tests plus 19 child pins (same pinned digests as the replay, folds equal `validation-calendar.json`, one shared refit clock, frozen alpha equals the selection file, offset document differs from validation only by the label clock, no document names a ledger/order/backtest kind). Broad subset `tests/pipeline`, the relevant `tests/pipeline_libs`, `tests/docs` and the child suite: 7,816 passed, 1 failed (main's known purity red, `libs/parquet.py` importing `dskit.onboarding.snapshot`, unrelated), 108 skipped. Ruff clean on the touched files.

Development document hash `dd0d3918…993b` (run `tech-prediction-development-20261020-2026-10-20-6f0ae301`); an earlier development run with the fold start written 2020-01-01 instead of the calendar's 2020-01-02 (hash `4b099e30…`, run `…-4f406456`) produced byte-identical skill and row files and is superseded. Validation hash `863372d3…9f` (run `…-67c2a3ac`); offset-1 hash `57cd689a…cba` (run `…-e17e0fce`). Each run's `resolved.json` carries these hashes; the configs' file digests are in the handoff section.

## Model fit and training diagnostics

Ridge has no epochs; each weekly block is one closed-form fit. Per target × horizon × model there are 774 (ATM) or 694 (skew) scored-period block fits across the three pairs (about 258 and 231 per pair), with 479–1,858 training rows each (the expanding set of labels known before the block). Coefficient paths over the 2021–2025 blocks (mean, standard deviation, share of blocks with a negative sign):

| Cell | Z coefficient | 5-session change | Edge weight | Z × edge |
|---|---|---|---|---|
| ATM h5, lagged | −0.0108, 0.0013, 100% | −0.0010, 0.0008, 94% | — | — |
| ATM h5, precision | −0.0108, 0.0012, 100% | −0.0010, 0.0008, 94% | 0.0000, 0.0007, 54% | −0.0012, 0.0021, 64% |
| ATM h20, lagged | −0.0225, 0.0044, 100% | +0.0006, 0.0015, 58% | — | — |
| ATM h20, precision | −0.0223, 0.0042, 100% | +0.0007, 0.0015, 58% | +0.0001, 0.0016, 54% | −0.0027, 0.0048, 60% |
| Skew h5, lagged | −0.0058, 0.0008, 100% | −0.1376, 0.0672, 100% | — | — |
| Skew h20, lagged | −0.0093, 0.0014, 100% | −0.1109, 0.0555, 100% | — | — |
| Skew h20, precision | −0.0096, 0.0014, 100% | −0.1057, 0.0560, 100% | +0.0281, 0.0546, 28% | −0.0095, 0.0310, 52% |

The Z coefficient is negative in every block of every cell: a spread one standard deviation above its trailing mean is forecast to fall by about 1.1 ATM vol points over 5 sessions and 2.2 over 20. The edge coefficients hover around zero with no stable sign. The expanding-mean benchmark has a negative pooled IC in every cell (−0.03 to −0.16; some pair-fold ICs are positive): the past mean of the label is a slightly contrarian indicator of the next change. Its skill against a zero forecast is between −0.0006 and −0.0091, so it flatters R²oos by at most 0.009. The precision weight is identically zero for every pair in 2019 and 2021 (no-edge and disconnected fits) and covers two or three pairs in the other years, so the precision feature is piecewise constant and sparse; the mean model trains on a few more rows than the others because it needs no 5-session lag, but all models are scored on exactly the same rows.

## Validation 2021–2025, same-day label (the pre-registered documents)

Rows per fold are three pairs × sessions with a usable Z and label; `n` is given per fold. R²oos is 1 − MSE/MSE_mean. IC is Spearman over the fold's rows (all pairs); the 90% block-bootstrap interval is shown only where `CalendarBlockBootstrap` supports the stamp series, which requires the last stamp minus 20 calendar days to be an observed session (the pooled ATM h20 and skew h20 series end 2025-11-14 and fail that test; this availability is arbitrary and a future study should use the edge-padded sampler). Hit is the sign-agreement rate. "gap p" is the one-sided Newey–West p of the loss gap versus the mean model.

| Cell | Fold | n | lagged R²oos | lagged IC | hit | gap p | correlation R²oos | precision R²oos |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ATM h5 | 2021 | 730 | +0.063 | +0.246 | 0.538 | 0.217 | +0.065 | +0.062 |
| ATM h5 | 2022 | 753 | +0.191 | +0.282 | 0.570 | 0.031 | +0.191 | +0.191 |
| ATM h5 | 2023 | 750 | +0.192 | +0.432 | 0.627 | 0.000 | +0.192 | +0.191 |
| ATM h5 | 2024 | 756 | +0.232 | +0.387 [+0.259, +0.512] | 0.606 | 0.003 | +0.234 | +0.233 |
| ATM h5 | 2025 | 702 | +0.210 | +0.395 [+0.239, +0.528] | 0.637 | 0.004 | +0.212 | +0.213 |
| ATM h5 | pooled | 3,691 | +0.195 | +0.351 [+0.298, +0.411] | 0.595 | <0.001 | +0.197 | +0.196 |
| ATM h20 | 2021 | 730 | +0.078 | +0.416 | 0.595 | 0.280 | +0.078 | +0.078 |
| ATM h20 | 2022 | 753 | +0.259 | +0.443 | 0.631 | 0.003 | +0.260 | +0.258 |
| ATM h20 | 2023 | 750 | +0.386 | +0.680 | 0.732 | 0.000 | +0.389 | +0.389 |
| ATM h20 | 2024 | 756 | +0.432 | +0.693 [+0.573, +0.783] | 0.765 | 0.000 | +0.438 | +0.438 |
| ATM h20 | 2025 | 657 | +0.182 | +0.442 | 0.648 | 0.058 | +0.183 | +0.177 |
| ATM h20 | pooled | 3,646 | +0.307 | +0.540 | 0.675 | <0.001 | +0.310 | +0.308 |
| Skew h5 | 2021 | 336 | +0.139 | +0.421 | 0.676 | 0.039 | +0.108 | +0.140 |
| Skew h5 | 2022 | 753 | +0.223 | +0.417 | 0.645 | 0.000 | +0.219 | +0.199 |
| Skew h5 | 2023 | 750 | +0.284 | +0.489 | 0.671 | 0.000 | +0.293 | +0.278 |
| Skew h5 | 2024 | 756 | +0.323 | +0.507 [+0.435, +0.554] | 0.665 | 0.000 | +0.323 | +0.311 |
| Skew h5 | 2025 | 702 | +0.306 | +0.511 [+0.446, +0.590] | 0.648 | 0.000 | +0.290 | +0.283 |
| Skew h5 | pooled | 3,297 | +0.273 | +0.472 [+0.442, +0.503] | 0.659 | <0.001 | +0.275 | +0.263 |
| Skew h20 | 2021 | 336 | +0.384 | +0.669 | 0.762 | 0.001 | +0.359 | +0.387 |
| Skew h20 | 2022 | 753 | +0.292 | +0.512 | 0.697 | 0.000 | +0.302 | +0.052 |
| Skew h20 | 2023 | 750 | +0.325 | +0.559 | 0.696 | 0.000 | +0.318 | +0.324 |
| Skew h20 | 2024 | 756 | +0.398 | +0.575 [+0.493, +0.647] | 0.709 | 0.000 | +0.407 | +0.418 |
| Skew h20 | 2025 | 657 | +0.451 | +0.644 | 0.717 | 0.000 | +0.442 | +0.421 |
| Skew h20 | pooled | 3,252 | +0.349 | +0.573 | 0.710 | <0.001 | +0.344 | +0.306 |

Skew 2021 has 336 rows because AMZN's 25-delta risk reversal is null on 28 days between 2020-08-03 and 2021-07-30 (mostly around earnings), and each gap restarts the 60-session void of both AMZN pairs under the pinned `RollingPairSpread` rule (kept for consistency with the replay); until about October 2021 the skew folds are effectively the single pair AAPL–GOOGL, and the 2020 skew alpha tuning rests mostly on that pair too. Dropped rows are counted, never filled: per cell 31–473 rows lacked a label (end of panel or a voided session) and 10–16 lacked a feature.

**Family decisions (BH and Bonferroni agree in every cell).** All three models beat the mean in all four cells (pooled p ≤ 1.0e-7; `skill_vs_mean` passes with pooled DM t 4.6–9.0 and across-fold t 4.2–10.6; the across-fold t, p 0.0002–0.007, is the binding criterion, since the pooled Newey–West p-values near 1e-10 on five folds of three overlapping pairs are optimistic). `precision` versus `lagged`: never rejected (ATM h5 t +1.50 p 0.066; ATM h20 t +1.25 p 0.105; skew h5 t −2.03; skew h20 t −2.47, i.e. the precision edge makes skew forecasts worse). `correlation` versus `lagged`: rejected for ATM (h5 t +2.42 p 0.008; h20 t +2.79 p 0.003) and not for skew (p 0.43, 0.75). The ATM improvement is +0.001 to +0.003 R²oos, detectable only because the two forecasts share almost every row; it is not a usable difference. Per pair the pattern is the same: pooled `lagged` R²oos is +0.145/+0.189/+0.245 (ATM h5), +0.310/+0.286/+0.323 (ATM h20), +0.257/+0.271/+0.281 (skew h5), +0.361/+0.357/+0.333 (skew h20) for AAPL–AMZN / AAPL–GOOGL / AMZN–GOOGL.

## Next-session-label robustness (post hoc, disclosed)

Same models, alphas, folds and family; only the label starts at t+1. The same-day numbers above include the reversal of the decision day's own surface measurement, which a decision made after the close can never capture.

| Cell | lagged R²oos, same-day → offset 1 | lagged IC | per-fold R²oos (offset 1) | precision vs lagged p | correlation vs lagged p | BH: model beats mean (offset 1) |
|---|---:|---:|---|---:|---:|---|
| ATM h5 | +0.195 → +0.081 | +0.351 → +0.228 | +0.025 / +0.044 / +0.058 / +0.118 / +0.114 | 0.178 | 0.070 | all three |
| ATM h20 | +0.307 → +0.199 | +0.540 → +0.464 | +0.065 / +0.095 / +0.247 / +0.323 / +0.112 | 0.107 | 0.002 | all three |
| Skew h5 | +0.273 → +0.005 | +0.472 → +0.129 | +0.038 / −0.001 / −0.003 / +0.017 / +0.024 | 0.903 | 0.889 | none |
| Skew h20 | +0.349 → +0.034 | +0.573 → +0.256 | +0.105 / +0.027 / +0.006 / +0.049 / +0.073 | 0.948 | 0.851 | lagged, correlation |

So: the skew spreads' apparent forecastability is almost entirely one-day measurement reversal (the 25-delta wings are noisy day to day, and the fitted 5-session-change coefficient of about −0.13 is a noise-cancelling term); a reviewer's offset sweep (0/1/2/3/5 sessions) gives skew h5 R²oos 0.273/0.005/0.001/−0.000/0.003 and h20 0.348/0.034/0.031/0.006/0.007. Formally, skew h20 `lagged` and `correlation` still satisfy the pre-registered "predicts" rule at offset 1 (R²oos +0.034/+0.029, `skill_vs_mean` passes, BH rejects); the size says it is residual. The ATM spread retains genuine multi-week mean reversion: with the next-session label the 20-session change keeps R²oos +0.20 pooled and positive in every fold (hit rate 0.66, IC +0.46 with 90% interval [+0.40, +0.52]), and the 5-session change keeps +0.08. The network terms remain uninformative on this label as well (precision never rejected; correlation rejected only for ATM h20, again by about +0.003 R²oos). The network test is weak by construction: one Ridge per pair on raw-scaled features with the alpha tuned on the lagged model (a reviewer's probe found at most +0.008 R²oos for the correlation term with standardized features), so "adds nothing usable" is the claim, not "carries no information".

## What this does and does not say

- It says the ATM-IV pair spread of these three names is a mean-reverting measured series whose trailing Z carries real information about its own multi-week change, and that the structural-network edge carries none beyond that. It does not say the reversion can be captured: converting a vol-point forecast into P&L requires crossing two straddle spreads, hedging, and surviving the paths the v6 replay already measured as net losers. The v6 result and this one are consistent: a predictable spread and an unprofitable book.
- Three pairs sharing three names are not independent; the date-block bootstrap and the per-stamp gap averaging handle the cross-pair dependence per date but the effective sample is five folds of one small panel. The 2021 fold is the weakest in every ATM cell.
- The expanding-mean benchmark is a weak comparator (negative IC), which flatters R²oos by at most 0.009; the next-session-label table and the per-fold rows are the honest reading. The records' R²oos is row-level; `skill_vs_mean`'s `r2oos_pool` uses stamp-averaged gaps and differs when a stamp has fewer than three pairs (skew h5 lagged 0.262 vs 0.273); the memo cites the row-level values.
- The alpha was tuned on 2020 only and the grid barely matters (R²oos differences of 0.01 across the grid); the finding is not a tuning artifact.
- No untouched holdout exists beyond 2025-12-15; 2026 chains are sealed. Any later study starts from a new registration.

## Reproducibility and handoff

- Artifacts: `/home/russell/data/statistical_arbitrage/prediction-20261020/{development,validation,validation-offset1}/` with `rows_<target>_h<h>.jsonl` (every scored row with every forecast), `fits_<target>_h<h>_<model>.jsonl` (per-block fits), `skill_<target>_h<h>.jsonl` (per fold/model/group records) and `skill_<target>_h<h>.json` (summary with `skill_vs_mean`, comparisons and family). The superseded first development attempt sits one level up and is byte-identical in content.
- Validation summaries SHA-256: ATM h5 `d0274974…1794`, ATM h20 `d1600cda…bd0c`, skew h5 `a37a70f2…2096`, skew h20 `9a5eb624…6124`. Offset-1: ATM h5 `61a0eee6…dd41`, ATM h20 `868716a0…d7b8`, skew h5 `d7b421c8…be6d2`, skew h20 `58a67662…4ff5`. Development: ATM h5 `54dd636b…7405`, ATM h20 `24936e44…e9e5`, skew h5 `058668f8…19ea`, skew h20 `560426e1…a62a`.
- Config file digests: development `49e1df09…34c8`, validation `c7b2cef3…fd12`, offset-1 `49a6a901…77b1`, selection `318a9e17…1eb1`. Run directories under `/home/russell/data/statistical_arbitrage/pipeline_runs/`. Every write node declares `overwrite: false`, so a naive rerun refuses; a rerun needs new output paths, which changes the document hash. Errata: the selection file's `notes` quote the development means to four decimals rounded (ATM 0.118322/0.119116/0.119341/0.123325, skew a100 0.128837); the argmax is unchanged and the file is left untouched because both frozen documents pin its digest. The offset-1 document's top-level `notes` still end with the validation sentence and its `rows_*` node notes describe the offset-0 label; notes are outside the identity hash.
- Review record: frozen candidate `626405a2`, two fresh Sonnet skeptic lenses (correctness/authority; tests/integration), both zero Critical/Major. Correctness lens: labels, `known_ms`, Z, purge and the weekly Ridge fits reproduced independently (predictions equal `f_lagged` exactly), NW/DM/`skill_vs_mean` wiring verified to every printed digit, config and artifact identities verified; eight Minor (disclosure and wording, two wrong explanations, now corrected above; `r2oos_pool` definition and the opt-in `fit_date`/`trained_through` guard as docstring backlog) and eight Nit. Tests lens: 36 node mutations caught, 20 missed (reversed-symbol sign, fold-boundary rows, seeded bootstrap with noisy data, per-stamp averaging with unequal group counts, DM `h_steps`, alpha/corrections knobs; all correct on probe), child pins miss wiring and tuned-input edits across all three documents, stdlib planning confirmed without sklearn, `tests/pipeline` + `tests/pipeline_libs` 12,757 passed with three pre-existing reds (purity, a lightgbm-extra pin, a torch.py digest pin) on files this candidate never touched. Both backlogs are deferred, not fixed after the lock. The audit memo's A0002 and this memo's A0003 journal rows summarize the same-day and post-hoc results together; the post-hoc status is stated here and in ADR-0272 Amendment 1.
- Next authorized action: none. The owner may ratify ADR-0272 (the code and runs proceeded under the owner's 2026-10-20 instruction to continue autonomously, which the correctness lens notes it could not verify), and may decide whether the ATM 20-session finding is worth a separately registered cost-aware study; this memo authorizes nothing.
