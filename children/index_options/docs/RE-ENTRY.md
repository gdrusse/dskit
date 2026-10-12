# RE-ENTRY — index_options

Updated: 2026-10-12 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

Question since T2: does the forecast have predictive power or edge, and where, apart from
the trading algorithm? **No edge found anywhere so far.** Ledger A1136–A1152, last commit
`4c5f3817` on main. All compute-only; 2026 untouched.

- **T2** (ADR-0259) failed: beyond-market IC 0.033, t 1.15. Memo `2026-10-10-t2-dispersion-ic-result.md`.
- **Condor replay, 2025**: no cutoff, band or skill split survives; the "high win rate"
  patterns were market drift. The low credit/wing lead is weak (same-date gap +$26 in 2024,
  +$224 in 2025, CIs span 0). 2024 holdout config: `run-equity-condor-2024-holdout.json`.
- **Real option-implied CDF vs forecast** (`OptionPriceCDF`): tie on CRPS (49% coverage).
- **Audit**: the PatchTST (~19k params) adds almost nothing beyond the HAR vol scale; its
  skill sits in illiquid/short-history names; 2024–25 was used for architecture selection.
- **HAR vs implied straddle**: fails on both sets (first t 1.52; second 300 t 1.43; net of
  8% costs −5.7%).
- **75–90% shoulder** (only region lead; first set HAR +4.0): did NOT replicate on the
  second 300 names (+0.66, CI [−1.87, +3.37]); about half was drift. Treat as closed.
- **Second 300 pull** (`option-universe-300b`, ADR-0235 amendment 2026-10-12): 285 usable
  names, 11,056 exact-DTE31 rows, chains thin (18% market-curve coverage). Same 2024–25
  dates as the first set, so the market factor is shared.

## Next

1. Owner decides: drift-removed model check in 75–90% (cheap); ADR adopting HAR as the
   reference forecast; a forward chain recorder (Schwab) or paid history (ORATS etc.).
2. Power is capped (about 91–95 dates); more names do not add dates.
3. A new trading structure (wheel, reverse condor) needs a pre-registered rule first.

## Blocked / owner decisions

- Everything in Next 1; opening 2026 data (irreversible, wants a stronger lead first).
- Model-based tests cannot run on the 300b names: no forecasts exist for them.

## Read first

- `docs/memos/2026-10-10-t2-dispersion-ic-result.md`
- `docs/decisioning/actions.csv`, rows A1136–A1152
- ADR-0235 (amendment 2026-10-12), ADR-0259
- Scratchpad scripts are NOT committed; rebuild from configs if needed.

## Do not

- No 2026 data access, fits, or spending. No cross-name selection in replays.
- Do not rerun T2 or the region/IC tests hoping for a different answer; rules were
  pre-registered and thresholds must not be tuned.
- Run with `PYTHONPATH=<this worktree>`; the venv editable install points at a stale tree.
- Open small defect: `DiscreteCDFGrid.worst_expected_loss` HiGHS tolerance.
