## TL;DR

On 60 reused 2025 entry/expiry cases, the center-conditioned CDF improved the decision-region score slightly over the incumbent, but had worse indicative selection regret where the ETF exercise charge could be evaluated. A positive-radius stress test chose no trade on one SPY case. Neither test validates a tradable rule: quotes have dates but no timestamps, and the uncertainty radius is not calibrated.

## Execution contract

- Owner: `children/index_options`, ADR-0204; offline, posthoc research only.
- Date and base: 2026-09-30, worktree `codex/condor-robustification` from `13b3cfc8`.
- Frozen forecast: `predictive_cdf_center_transport_20260930`, completed `late` partition, raw `horizon_empirical`, `research_incumbent`, and `center_index_horizon_transport_prior50`. The 2019–2025 history was already inspected, so this is descriptive, not a new holdout.
- Main JSON: `configs/run-cdf-decision-regions.json`, SHA-256 `897966492358132916ccdb0cfc2fcd8520a1b5dd493db296277552bd5abb7d5d`; 20 spaced rows per SPY, QQQ, IWM; 5-point wings; ±0.1 log-moneyness band; 0.5-dollar mesh; radius zero. The `prepare/manifest.json` pins source file hashes and paired OOF identities. The pilot admits raw `GridCurve` archives only; exact atom-aware integration is not yet available for other curve families.
- Chain: 2025 annual options archive, dated snapshots without source quote times. Research clock is **after the date's close and complete snapshot**. Bid/ask credit is indicative, not an executable fill. ETF options are American and physically settled; the payoff diagnostic is European expiry loss with the repository's separate conservative American-short charge where underlying/dividend data permit it.
- Each CLI stage has JSON-enforced caps of 30 minutes and 6 GiB address space in WSL2, plus chain-row/grid-node caps. Results are dollars per ETF share unless a score is dimensionless; no multiplier-adjusted profit claim is made.

## Implementation evidence

The audit constructs the same complete entry-known, quote-side candidate universe for all three frozen curves. It scores every eligible strike and wing with fixed positive-floor weights, integrates capped put/call loss directly from each archived curve without smearing tied-knot probability atoms, checks the clipped-grid approximation, and includes no trade. A shared-grid 1-Wasserstein loss maximization provides the robust selector. Source completion, file hashes, paired forecast identities, entry/settlement clocks, and close prices are checked before evaluation. Unknown exercise charge withholds regret rather than treating it as zero.

Main `prepare`, `evaluate`, and `report` completed: 60 entry identities, 180 model rows, 177 eligible model rows (59 identities), and 123,873 candidate/model rows. American charges were evaluated for 117 SPY/QQQ model rows; regret was withheld for 60 IWM model rows, and three QQQ model rows for one entry had no eligible condor. Focused tests: 140 passed; `git diff --check` passed. This was not the full suite.

Two earlier execution attempts did not complete: an invalid underlying node key and a start date crossing a QQQ split were corrected in the final JSON/code path. Their ignored run outputs are not evidence for the result below.

## Empirical result

The main run's mean weighted threshold score was 0.080899 for center-conditioned versus 0.081172 for incumbent, a `(0.081172 − 0.080899) / 0.081172 = 0.336%` lower score. Mean strike Brier was 0.091215 versus 0.091566; mean capped condor-loss absolute error was 1.445475 versus 1.445811 dollars/share. IWM's center-conditioned threshold score was worse (0.089332 versus 0.088814). No uncertainty interval or independent holdout establishes a model improvement.

On the charge-complete SPY/QQQ subset, mean indicative nominal regret was **2.636599** versus **2.516342** dollars/share for incumbent: center-conditioned was worse by 0.120257 dollars/share. Regret compares selected realized net value with the best hindsight candidate or no trade on the same entry; it is not a forecast of future profit. There is no basis for model promotion.

The separate `configs/run-cdf-decision-regions-w1-smoke.json` (SHA-256 `df10220fb57f4c0bb14d56e523f23d6a729109bc6ec0a629102fbe68c004aa01`) completed all three stages on one SPY entry, 326 candidates/model. Its illustrative spot-scaled radius 0.005 made each model choose no trade, whereas radius zero chose `579-584-585-590`. For this *single realized entry*, regret was 1.40 rather than 3.33 dollars/share. The radius was not selected using prior settled errors and this one outcome proves no robust advantage.

## Unrun work and limits

The adaptive radius rule required by ADR-0204 is **not implemented or calibrated**. No date-block coverage check, future-entry evaluation, timestamped executable snapshot, genuine index-option replay, local-CDF-band ablation, or trading/paper execution ran. An annual date-only archive cannot establish quote availability at a historical intraday decision. The mesh audit's main-run mean maximum direct/grid loss gap was 0.040442–0.042149 dollars/share across models, under the declared 0.5 tolerance. Neither the historical result nor the one-row W1 smoke is a realized-profit guarantee.

Luna's integration review recorded one minor operations gap: an interrupted stage may leave a partial output directory that blocks retry with the same JSON. A missing stage manifest prevents false completion, but manual recovery or a fresh output root is currently required.

## Reproducibility and handoff

From WSL2, in `children/index_options`, run `PYTHONPATH=/home/russell/dskit-condor-robustification <venv-python> -m index_options.cdf_study <config> --decision-stage <prepare|evaluate|report>` once per stage; the CLI applies each JSON budget and each config pins its own unique output root. The actual result manifests are under `pipeline_runs/cdf_decision_regions_v5_20260930/{prepare,evaluate,report}` and `pipeline_runs/cdf_decision_regions_w1_smoke_v3_20260930/{prepare,evaluate,report}`. These ignored generated outputs remain local. Next authorized slice: causal radius calibration on prior settled OOF rows, fresh independent final review, then decide whether a separate timestamped/research holdout is warranted. No merge or push.
