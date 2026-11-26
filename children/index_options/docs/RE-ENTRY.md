# RE-ENTRY - index_options

Updated 2026-11-25 by GPT-6 (MIO formulation and calibration handoff).

## Now

- [MIO working draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d): saved sequence 22, 292 blocks. Formulation definitions are agreed; calibration fitting and validation have not run.
- Defined daily returns, conditional mean/variance and estimates; forecast normalization and CDFs; raw/corrected PITs; expanding stock-by-original-expiry-rank counters; stabilized monotone recalibration and chronological parameter selection. Appendices explain the derivations and pseudocode.
- Notation: d is the daily observation/cutoff clock, a a historical origin, t calendar DTE, eta sessions to expiry, g a future session offset, rho original expiry rank. Tau was retired; v is already occupied. Preserve all existing contract subscripts/superscripts.
- User prefers brief steps, each new mathematical definition restated for confirmation. Chat uses LaTeX display delimiters, never dollar-dollar; the Page uses native math fences.
- Cash/portfolio value, copula, forecasts at intermediate risk checkpoints, IV dynamics, CVaR and dynamic hedging remain unfinished. The strategy continues indefinitely; eligible contracts up to 60 DTE do not impose a strategy end date.
- Daily/weekly cadence is a separate study. Its 24 November status in the older memo is historical, not verified current job status. Preserve its checkout, jobs and receipts; no cadence conclusion is made here.

## Next

1. Read the draft and the 25 November checkpoint at the top of the session memo.
2. Define how settled PITs align across assets and original expiry ranks: origin, target date, information cutoff and incomplete observations. Do not infer joint dependence from marginal counters.
3. Propose the copula estimator and its compact data/state requirements, one mathematical addition at a time, for confirmation.
4. Before implementation, choose numerical recalibration settings, evidence/uncertainty rules, monitoring actions and acceptance versus identity. Then connect joint scenarios to risk checkpoints, scenario wealth, CVaR and feasible hedges.

## Blocked / owner decisions

- Strength candidates, actual warmup/tuning/evaluation dates, score weights, update policy and minimum-evidence/tolerance rules remain open. No empirical calibration or copula result exists from this session.
- A0002/A0003/A0006/A0008/A0009/A0010 remain binding; no Path or run configuration changed. Used development years are not clean holdout evidence.
- Cadence work holds positions to expiry; early-exit/hedge frequency and American assignment need their own treatment. Consult its latest receipts before resuming any run.
- Main contains unrelated unpublished history. Publish only this session's bounded handoff; preserve other worktrees, environments and active jobs.

## Read first

- [Working draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d)
- [Session checkpoint and earlier cadence handoff](memos/2026-11-24-mio-design-session.md)
- [Locked Path](decisioning/path.csv)
- `/home/russell/reports/MIO recalibration fitting methods.md`
- `/home/russell/data/index_options/runs/cadence-study/v20261124-fill-v2/verification.json`

## Do not

- Treat agreed definitions as fitted/validated results, recalibration as a copula, or marginal counts as sufficient joint data. Retain pending forecast/map identities and define joint retention separately.
- Use 2024+ data, unseal holdouts, refit the frozen model, alter Path/A0010 or launch model/replay jobs through this documentation wrap.
- Rerun the full suite, lower storage reserves, overwrite canonical evidence, repoint shared environments, interrupt jobs or purge another session's branches/worktrees.
