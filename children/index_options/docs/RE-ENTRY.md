# RE-ENTRY - index_options

Updated 2026-11-24 by GPT-6 (MIO design, cadence experiment, storage wrap).

## Now

- Resume our [MIO working draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d). It is distinct from the older implemented Kelly formulation.
- Agreed: buy/sell and call/put superscripts; contract/stock/DTE subscripts; integer trades x, known holdings h and cash B; current bid/ask costs, per-trade fees, expiry payoffs, and calendar-DTE roll.
- Open: cash/portfolio-value equations, joint asset scenarios, tail risk, concentration and dynamic hedging. Explain briefly; restate each agreed addition mathematically.
- Corrected daily/weekly experiment has **0/6 completed arms**. A 5 GiB run was OOM-killed after 12/14 weekly decisions. A guarded 6 GiB retry is prepared, not run; no empirical cadence recommendation exists.
- Reviewed research code: `22128802100b0372d965536ac25447d0b0c18329`. Explicit entry dates and next-fill expiry eligibility preserve legacy defaults.
- Non-DSKit relocation completed: 10.64 GB verified on D; shared DSKit data stayed on C. C measured17.3 GB free; normal10 GiB reserve passes, but each job needs fresh RAM/disk admission.
- Prior speed-panel evidence: relax faster in8/8 frozen states; warm dropped. This establishes neither profitability nor the final formulation for this working draft.

## Next

1. Read the working draft's Resume here section and the session memo. Continue from portfolio state (B,h), then agree cash and risk equations.
2. For the cadence study, restore the exact frozen checkout if purged, verify code/driver/config/analysis hashes, and check storage plus host RAM.
3. Run the first missing job from `jobs-solve-6g.json` serially only when admitted. Stop on refusal/failure; six valid corrected receipts are required before analysis. Reuse existing inputs.
4. Compare net growth, drawdown, tail losses, capital-days, DTE attribution and doubled execution costs. Daily needs stable improvement; weekly or cash remains possible.

## Blocked / owner decisions

- OOM risk remains; 6 GiB fitting every arm is unproven. No study job is launched by this wrap.
- A0008/A0009/A0010 remain locked. This separately authorized direct research runner is not the official sealed packet or a replacement for A0010.
- The experiment holds entries to expiry. It does not answer early-exit/hedging frequency; American assignment and executable liquidation remain unmodeled.
- Preserve other sessions' unpublished local-main changes and worktrees. Exact pins and integration receipts are in the memo and external verification record.

## Read first

- [Working draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d)
- [Session handoff and resume commands](memos/2026-11-24-mio-design-session.md)
- [Locked Path](decisioning/path.csv)
- [Prior speed-panel final memo](../../../docs/memos/2026-11-19-mio-evaluation-final.md)
- `/home/russell/data/index_options/runs/cadence-study/v20261124-fill-v2/verification.json`

## Do not

- Use2024+ data, unseal holdouts, refit the frozen model, change Path/A0010, or rank cadence from the invalid old diagnostics.
- Rerun the full suite, launch unguarded compute, lower reserves, overwrite results, or repoint an active shared environment.
- Purge canonical data/evidence, active services or another session's branches. Restore the pinned checkout before reusing path-bound configs.
