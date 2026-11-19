# RE-ENTRY - index_options

Updated 2026-11-19 by Claude Opus 5.5 (MIO speed panel complete; wrapped).

## Now

- MIO speed panel COMPLETE: all 8 prespecified frozen states (Q1 Jan 4/Jan 11, Q2 Apr 1/Apr 20/Jun 8, Q3 Jul 1/Jul 20/Aug 3) ran plain and relax on WSL; warm on the 5 states with a prior book.
- Relax: faster in 8/8 states (1416 s vs 2461 s total, -42%), higher utility in 5/8, summed utility +9%. Losses on Apr 20 (-13%), Jan 11 (-8.5%) and Jul 20 (-0.6%). Decision: relax is the default solve mode.
- Warm (previous-climb MIP start): same picks as plain in 4/5, slower each time, and worse on Apr 20. Dropped.
- Plain's stage 1 often stops at its gap/time limit (it ended in cash on Q2 Apr 1), so plain is not a certified baseline.
- Q2 fixed-action accounting reproduces archived e2 economics (picks and P&L exact). The a1 live Q2 P&L gap comes from the solve, not the accounting.
- Q1 risk/both were re-run and accepted on 2026-11-18 (+17,641 vs control +26,782); the old "owner-halted" note was stale.
- Evidence lives outside Git under the run dir (paths in the final memo). The frozen-panel runner (v9 lock), the Q2/Q3 prefix capture with the disclosed noise class, the Q3 quote cache and the warm arm are all reviewed (no Critical/Major).

## Next

1. Owner decision: adopt relax as the production default. If adopted, rerun the affected quarter controls with relax under the official harness.
2. Combined calibration + risk + joint-history arm: never run on real data; needs its Q2 lock and reviews (EV/combined-feature-inventory-20261119.json).
3. Reproducibility (cross-host identical picks) is deferred by the owner; revisit before any production claim.

## Blocked / owner decisions

- The official Q2 primary comparator/publication change still needs a concrete owner decision (skeptic-review.md:36).
- Close candidates are unsupported beside the static hedge/DTE stack. Prior hedge-overlay, stock funding, A0006, hedge-cost, parity-forward, hard-to-borrow, origin_two, E1b and config-manifest issues remain unresolved.

## Read first

- [Final memo](../../../docs/memos/2026-11-19-mio-evaluation-final.md)
- [Codex handoff](../../../docs/memos/2026-11-19-mio-claude-handoff.md)
- [Takeover status log](/home/russell/data/index_options/runs/mio-combined-evaluation/v20261117/claude-takeover-status.md)
- [Locked Path](decisioning/path.csv)

## Do not

- Treat the owner-accepted hedge-noise states as strict replays, relabel the failed strict a1 Q2 capture, or present tiny fixtures as real results.
- Use 2024+ data, unseal holdouts, edit locked Path or A0010, or claim profit from solver-speed states.
- Run unguarded compute, add Windows and WSL RAM, or lower reserves.
