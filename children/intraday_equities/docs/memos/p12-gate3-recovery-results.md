# P12 Gate 3 recovery results

## Outcome

P12 Gate 3 is complete for the 31 families selected by the persisted Gate 1
artifact. Twenty-five pass and six fail. The result combines 16 verdicts
reconstructed from complete original-run evidence with 15 families rerun under
a new continuation identity. No partial artifact was deleted, rewritten,
relabeled, or treated as a final result.

Pass (asset:horizon): `LLY:3`, `QQQ:1`, `XLF:3`, `XLE:1`, `XLK:5`,
`BAC:1`, `SMH:5`, `IWM:5`, `XBI:1`, `FCX:1`, `DAL:1`, `NRG:1`,
`MET:1`, `MSTR:10`, `NOW:5`, `LULU:5`, `PANW:5`, `INTC:1`, `CIEN:5`,
`LRCX:10`, `TER:5`, `BIDU:2`, `LITE:5`, `ADBE:5`, `ANET:3`.

Fail (asset:horizon): `TQQQ:3`, `NVDA:2`, `UPRO:60`, `AVGO:10`,
`NFLX:3`, `BA:1`. Every failure beat all 19 scrambled draws but failed the
shipped null-statistic calibration check; no family stopped early.

## Frozen source and Gate 1 selection

The only selection source was `gate1.json` rows whose `gate1_passes` value is
the boolean `true`. The source contract is:

- document: `configs/run-p12-modelability.json`
- document SHA-256:
  `2d203f5c95c99eeedb69486cd378df6c884ce2a411840881a50a8e0844dc583b`
- data cut / as-of: `2026-02-28`
- source staged run:
  `pipeline_runs/p12-63-asset-modelability-staged-2026-02-28-2d203f5c`
- persisted source stages: `stages/memory.json` (A2920) and
  `stages/gate1.json` (A6155)
- ordered survivors:
  `LLY:3`, `QQQ:1`, `XLF:3`, `XLE:1`, `XLK:5`, `TQQQ:3`, `NVDA:2`,
  `UPRO:60`, `BAC:1`, `AVGO:10`, `NFLX:3`, `SMH:5`, `IWM:5`, `XBI:1`,
  `FCX:1`, `DAL:1`, `NRG:1`, `BA:1`, `MET:1`, `MSTR:10`, `NOW:5`,
  `LULU:5`, `PANW:5`, `INTC:1`, `CIEN:5`, `LRCX:10`, `TER:5`, `BIDU:2`,
  `LITE:5`, `ADBE:5`, `ANET:3`.

The inventory is A12580 at
`pipeline_runs/p12-63-asset-modelability-gate3-recovery-inventory-staged-2026-02-28-5594cbdf/stages/inventory.json`,
SHA-256
`126444fa327002ce63e211ed08cfee9310763ad2c68c1122c0b1c82d093a0f1f`.
It reconciles `actions.csv`, every expected Gate 3 directory, `report.md`, and
`walkforward.json` against the frozen document, caches, horizons, seeds, and
data cut.

## Inventory and reconstruction

Sixteen families were complete end to end in the original run: `LLY:3`,
`QQQ:1`, `XLF:3`, `XLE:1`, `XLK:5`, `TQQQ:3`, `NVDA:2`, `UPRO:60`,
`BAC:1`, `AVGO:10`, `NFLX:3`, `SMH:5`, `IWM:5`, `XBI:1`, `FCX:1`, and
`DAL:1`. Each had exactly 19 null summaries, 20 journal-backed fold parts per
summary, finite observed and null `r2oos`, `t_pool`, and `t_fold` inputs, plus
matching reports and walk-forward records. The recovery recomputed each
verdict with the shipped `_score_one` and `tier2_verdict` functions.

The separate provenance-rich result rows are A12581-A12596, one per
asset/horizon. They retain the observed artifact, all 19 summary paths/action
IDs, all 380 part paths/action IDs, verdict inputs, pass/fail, and method.
Eleven reconstructed families pass; `TQQQ:3`, `NVDA:2`, `UPRO:60`,
`AVGO:10`, and `NFLX:3` fail calibration.

The other 15 families were incomplete or non-reconstructable and therefore
rerun in full: `NRG:1`, `BA:1`, `MET:1`, `MSTR:10`, `NOW:5`, `LULU:5`,
`PANW:5`, `INTC:1`, `CIEN:5`, `LRCX:10`, `TER:5`, `BIDU:2`, `LITE:5`,
`ADBE:5`, and `ANET:3`.

NRG failed at A12579: seed 5 part 0 exited zero but the parent could not find
matching journal evidence. Its seed 0 was complete; seed 1 lacked journal
evidence for its main and parts 18-19; seeds 2-4 had artifacts without a
complete journal chain; seed 5 lacked a main summary and its parts lacked a
complete journal chain; seeds 6-18 were absent. Those partial artifacts remain
in place. The continuation reproduced the exact NRG seed-05 part-00 case and
journaled its successful replacement as A12731.

## Defect fix and review

The focused red test reproduced the zero-exit/no-journal case. The narrow fix
makes the parent validate the freshly completed fold's exact identity, hash,
as-of, state, row schema, finite non-boolean score, and run directory before
the parent appends its journal row. Pre-existing or orphaned fold output is
never silently adopted. Fix commit: `cd4fb1c`.

Independent major/critical review found two major contract gaps: incomplete
identity/state validation and an incomplete row-schema check. Both were fixed;
a subsequent review was clean. Recovery construction commit `d1230ba` also
received independent review; its major finding (non-finite test statistics
could be dropped instead of forcing a rerun) was fixed and the final review was
clean. The focused modelability, recovery, staged-run, and concurrent-journal
verification covered 142 passing tests before execution.

The first continuation identity was too long for the 80-character journal step
limit. A12618 records that safe failure after one NRG seed-00 family; its
artifacts and rows remain untouched. Commit `47bcdaa` shortened only the new
continuation label and added the length pin.

## Continuation evidence

The successful continuation is `configs/run-p12-gate3-continuation.json`,
identity
`a1f293a2508bdcef76656729b3cde045049623d4b257c0b2cc1cfd94607837ea`.
Its stage run is
`pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2`:

- A12619: `stages/inventory.json`, SHA-256
  `7a23f5c5b913bcd96d31d7134aba21c37258421c641a4fdd2308ab0431990473`
- A18619: `stages/gate3_recovery.json`, SHA-256
  `098b21eaef6ee0260753d4f981ca2337bccae406b9efd394284d9b180ba03bd0`

The result contains all 63 original Gate 1 rows, the exact 16/15
reconstructed/rerun partition, 285 rerun main walks (15 x 19), and 5,700
matching one-fold journal records (285 x 20). Every walk has a `ran`
`walkforward.json`, `report.md`, 20 finite scored folds, matching document hash
and as-of, and exactly one matching main plus 20 part journal rows. Every rerun
family completed 19 draws; the final stage emitted no combined result until all
15 were terminal.

The rerun added 14 passes. `BA:1` is the sole rerun failure: it beat all null
draws but its null standard deviation was `0.6518554226`, below the shipped
calibration interval. `NRG:1` passes with 19 draws, best-null R2oos
`0.0008032984`, null mean `-0.6846336469`, and null standard deviation
`1.2357401500`.

## Runtime and future width

The successful continuation inventory journaled at 17:47:56Z and the final
stage at 21:25:31Z: 3 hours 37 minutes 35 seconds at two fold workers, or about
14.5 minutes per rerun family. On this 16-CPU workstation, future long runs
should set `INTRADAY_EQUITIES_FOLD_WORKERS=4`. Widths above four are possible
but should be benchmarked first because each fold's LightGBM fit can use eight
threads and higher widths risk CPU oversubscription.

## Verification

Post-run verification checked the stage state/token/hash and its matching
journal row; the exact 63-row cohort and 31 Gate 1 survivors; the exact 16/15
partition; all 285 main walk directories, reports and walk-forward records;
all 5,700 part records and fold run directories; finite scores and verdict
statistics; reconstructed-row equality; Gate 1 failure handling; and the exact
A12731 NRG replacement evidence. The result is 25 pass, 6 fail.

Append-only closeout records: A18620 corrects the old smoke paths without
editing A2888-A2909; A18621 records the first 64-asset attempt's exit 143;
A18622 is the final recovery summary.
