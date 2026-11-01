# RE-ENTRY - index_options

Updated: 2026-11-01 by GPT-6 Astra; wrapped by Claude Opus 5.5. Q2-Q4 2021 evaluation and execution memo complete.

## Now

- All three accepted methods completed Q2, Q3 and Q4 with attested close receipts and replay parity. Nine standard reports plus combined evaluation and memo are complete; all 63 accounting/report gates passed.
- Final output candidate `6b795948` passed fresh sequential correctness/accounting and integration/provenance reviews, both C0/M0. Source/config candidate `ae2c9f98` and CDF candidate `159ce6aa` retain their prior closed reviews; all 270 runtime pins and 34 prior configs remain unchanged.
- Fully settled gains occurred only for Q2 shock (+$1,853.85) and Q4 nominal (+$19,190.15). All three Q3 methods lost. Q4 nominal won 2 of 25 books; one COST book contributed +$22,287.10. No method was positive in every quarter.
- Accounts reset separately each quarter with $16,000 total deposits. Marked quarter-end and fully settled cohort results are distinct; do not compound quarterly returns. Q2 W1's positive quarter-end mark became a settled loss.
- Corrected fits selected 46/96 and 36/86 epochs with patience 50 / max 150; fresh final fits 46/36/36. Q4 includes mature Q3 labels but Q3/Q4 never choose epochs. M53 is refitted before each quarter and frozen during it. No separate formal M53 HPO; selection among 67 methods on 2020-2023 makes these retrospective development results.
- Q4 alone uses owner-approved public-notice spinoff eligibility, excluding 122 paired IBM/O candidates with existing JSON filters while preserving full quote/basis history. Original Q2/Q3 eligibility differs. All original/failed attempts and exposure history remain retained.
- Registry revisions 16 (original), 6 (tail failure), 6 (eligibility success), none with an active claim. Each completed quarter was published and reviewed once. Failed Q4 attempts were abandoned once. Never repeat dispositions.
- Disk investigation identified repeated quote indexes; verified hardlink dedup recovered about 34 GiB inside WSL. Lossless Windows compression provided headroom. VHDX compaction was not performed; live recorders were preserved.
- Durable delivery: `/home/russell/data/index_options/mio-2021h2-walkforward-20261101/coverage-v2/delivery`. The main push and cleanup receipts are written after this commit to `delivery/wrap-receipt.json` and the parent `continuation.json`. Original absolute source paths can be restored from the reviewed Git candidate.

## Next

- Requested evaluations, reports and memo are complete. No further simulation is scheduled by this task after wrap.
- Future work needs a separately scoped decision: earlier 2019/Q1 coverage remains truncated; arbitrary date-change preparation/reporting is not yet one automatic entrypoint.

## Blocked / owner decisions

- None for this completed retrospective evaluation. No live deployment or fresh holdout approval is implied.
- Storage, outside this task: the WSL VHDX is still uncompacted (about 812 GiB logical; C: free was about 10 GiB on 2026-11-01). Compaction needs administrator approval and a recorder pause.
- Nonblocking backlog: generic report 14 sessions means 14 decision dates (actual quarter coverage 63/64/64); unsettled-label regression fixture enhancement; Q4 selection hash names a wrapper with the actual summary hash nested. Storage timestamp corrected to 15:09 UTC.

## Read first

- [Combined evaluation and all nine reports](reports/mio-2021q2-q4-walkforward-20261101/README.md).
- [Execution and correction memo](memos/2026-11-01-mio-q2-q4-walkforward.md).
- [Final output review lock](reports/mio-2021q2-q4-walkforward-20261101/final-output-review-lock.json).
- [Training and protocol evidence](reports/mio-2021q2-q4-walkforward-20261101/protocol-evidence.json).
- [Approved Q4 source review](reports/mio-2021q2-q4-walkforward-20261101/q4-eligibility-v2-review-lock.json).
- [Decision log](../../../docs/architecture/decision-log.md): ADR-0288 and its implementation evidence.
- [Archived handoffs](../../../docs/archive/re-entry/index_options.md): previous storage/Q1/Q2 lane notes.

## Do not

- Reopen completed claims, overwrite failed evidence, read protected 2024+ price/outcome data, or claim 2021 was a fresh holdout.
- Change Path A0002/A0003/A0008, other sessions' checkouts, raw/model artifacts, exposure registries or live recorders.
- Purge necessary source/caches or interrupt WSL for compaction. Only the finished contained task branch/checkout is eligible for this wrap cleanup.
