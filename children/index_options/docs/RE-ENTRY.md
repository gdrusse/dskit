# RE-ENTRY - index_options

Updated: 2026-11-03 by GPT-5. The M53 causal residual-head, PCA, and regime screens are closed: no stable learned correction was found.

## Now

- Direct M53 residual-head forward selection stopped after its first train--validation divergence. Among 46 quote-time Theta/market/lifecycle candidates, rn_median improved 2021 by only 0.0136%; its frozen 2022--2023 score was 0.0851% worse than M53. Evidence: research/distribution-modeling/2026-11-03-m53-causal-forward-selection.md.
- A new causal PCA and three-way-regime screen also found no transferable learned signal. The best-looking 1-PC head improved its historical monitor 0.7597% but was 0.4705% worse in 2021. All 12 regimes selected maximum shrinkage; the null M53 head wins. No F4/F5 candidate evaluation occurred. Evidence: research/distribution-modeling/2026-11-03-m53-causal-pca-segmentation.md.
- Reviewed implementation candidate fb84644836f6f3c37ee1dad17a92838cd8317ee8 has fresh authority and integration passes (C0/M0/Minor0/Nit0). Evidence-only lock: f699f451fb7bdd170fbb9377ed7b39ed4ed963ed; reviewed source/test blobs remain unchanged.
- The RL sizing service is inactive; its plan reports paused_after_first_sizing_scan. No RL fit or evaluation has completed.

## Next

- Do not add more feature-compression, coarse segmentation, or direct M53 location-head variants. A later learning study needs a separately scoped target or correction operator and must show a positive 2021 effect before any later-fold testing.
- On RL resume, inspect its completed scan evidence and then review/close the claim before dispatching remaining sizing configurations serially.
- Queue: /home/russell/data/index_options/rl-full-universe-20261102/logs/dimensions-plan.json and the first assignment. The 2019 training import is already done; do not reimport it.
- After all-date bounds, freeze a meaningful PPO budget, run exact-recipe feasible reset probes, then the nine fits and 27 frozen-policy quarter evaluations.

## Blocked / owner decisions

- No learned M53 correction is accepted from this feature pool. This is exploratory evidence only; A0002--A0005 and A0008 remain binding and no method is promoted.
- User authorized delivery and cleanup of the reviewed implementation while the empirical comparison remains paused/incomplete. Parent will fast-forward and push the accepted candidate and remove task branches with expected-head checks only after verifying their state.
- Full-universe action exploration and storage remain practical risks. The 128-step smoke is infrastructure evidence only; no learning or profit conclusion is supported.
- Preserve all 212 names, three methods, seeds 11/29/47, saved forecasts without rescoring, completed-MIO reuse, Q2-Q4 baseline matches and Q4 eligibility-v2.

## Read first

- [M53 PCA and segmentation screen](research/distribution-modeling/2026-11-03-m53-causal-pca-segmentation.md).
- [M53 residual-head screen](research/distribution-modeling/2026-11-03-m53-causal-forward-selection.md).
- [Dimensions ADR and review lock](../../../docs/architecture/decision-log.md).
- [Reviewed candidate and packet inventory](../configs/rl-full-universe-20261102/inventory.json).
- [Decisioning path](decisioning/path.csv).

## Do not

- Stop another session's active job, extend a stopped M53 screen, or treat a screen-log message as a completed cohort.
- Reimport 2019 data, rescore forecasts, reopen completed MIO results, read protected windows, fit/evaluate before stated gates, or claim profitability.
- Change reviewed source/test blobs, the locked path, or another session's worktree.
