# RE-ENTRY - index_options

Updated 2026-11-11 by GPT-6.1 Sol.

## Now

- **Kelly method.** ADR-0313 replay-hedge Kelly search is frozen at `c65039fe`. Main `d753e2f7` includes the exact clean-v2/prospective-v3 G5 timing authority; two independent Terra lenses cleared the code, and 91 post-merge focused tests passed. The nine A0010 formulations and Path remain locked.
- **Real-data timing.** Terra independently inspected scope-specific operator-transcribed user approvals and published exact dispatch hashes (clean `c492e6cc...`, prospective `38d39841...`). WSL unit `adr0313-g5-timing-dispatch-v2-20261111.service` is active, waiting for the older 2020Q1 job, the base queue, and fresh resource headroom. No G5 timing study has started. A prior March replay completed with I/O-contaminated wall time and is excluded from G5 speed.
- **Base/G1/walk.** Attested 2019Q4 and 2020Q1 base closes and BookGreeks exist. The 2021Q1 base process is paused for memory safety; `adr0313-resume-base-q1-after-sized-20261111.service` will resume it after the older job exits and headroom passes. The base supervisor then runs Q2. Q1/Q2 Greeks, ADR-0311 Stage5/6 walk, and four-quarter G5 status/timing evidence remain pending.
- **Four-quarter bundle.** R4 is pending, not launch-cleared. Terra found a Major in the Stage5/6 completion-to-scheduler binding; Sol is preparing R5 with a failing test first. Actual four-quarter dispatch still requires independent final review and every declared gate.

## Next

1. Monitor the older 2020Q1 process, Q1 resume watchdog, base supervisor, C:/D: headroom, and timing dispatcher; do not launch competing heavy jobs. After base closes, finish Q1/Q2 BookGreeks and the reviewed ADR-0311 Stage5/6 walk.
2. Review R5's Stage5/6 provenance fix independently in Terra. Complete clean March timing trio before prospective weeks, project all four quarters from attested statuses, then run the approved four-quarter assessment only after G1/G5 and resource gates pass.
3. Update this note with actual receipts and post-run verification; report unresolved owner items without deciding them.

## Blocked / owner decisions

- Current WSL/Windows shared RAM is committed to the older 2020Q1 job and paused Q1 base. Timing dispatcher is queued but no real timing role is running.
- A0006 wording, hedge costs, parity-forward mark, hard-to-borrow unreadable window, origin_two shrinkage, E1b, gross-share hedge cost, stale test_configs manifest, and prior instantiator sign-off remain owner items.

## Read first

- [ADR-0313 and G5 amendments](../../../docs/architecture/decision-log.md)
- [Locked Path](decisioning/path.csv)
- [MIO queue runbook](plans/mio-parallel/RUNBOOK.md)

## Do not

- Edit the nine A0010 formulations or Path; keep DEVELOPMENT_SEAL at `2021-07-01`.
- Open protected economics, forecasts, scores, or run logs; no 2022-2023 MIO economics before the owner's three picks, and no 2024+ data.
- Launch a four-quarter arm before G1/G5 and independent Terra clearance. Respect WSL/Windows memory and C:/D: disk gates.
