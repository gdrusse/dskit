# RE-ENTRY - index_options

Updated 2026-11-11 by GPT-6.1 Sol.

## Now

- **Frozen method and main.** ADR-0313 replay-hedge Kelly search is frozen at `c65039fe`. Main `68952329` includes exact G5 timing authority and the prior handoff; 91 post-merge focused tests passed. The nine A0010 formulations and Path remain locked.
- **Live WSL queue.** 2019Q4 and 2020Q1 base packets are attested. Base 2021Q1 PID `265559` is active; the same supervisor runs Q2 after its attested close. An unrelated sized 2021Q1 PID `274373` is temporarily stopped for shared-memory safety. Terra-cleared `adr0313-resume-sized-q1-v4-20261111.service` resumes it only after the whole base queue and resource gates pass. Do not use its paused wall time as performance evidence.
- **G5 timing.** Terra independently published approval SHA-256s: clean March `c492e6cc...`, prospective 2019Q4/2020Q1 `38d39841...`. Terra-cleared `adr0313-g5-timing-dispatch-v4-20261111.service` waits for base and sized work, exact mounts, and resources, then runs the six clean jobs before 48 prospective jobs. No G5 timing job has started. The prior March replay's I/O-contaminated wall is excluded.
- **Other gates.** BookGreeks 2019Q4/2020Q1 is attested; Q1/Q2 pending templates are on D and Terra-cleared as pending only. ADR-0311 Stage5 v5 operator approval `1cbc4d70...` received independent Terra conditional clearance; it runs alone after base, sized and G5, with fresh gates. Stage6 waits actual Stage5 terminal receipt and outputs. Four-quarter assessment R6 pending bundle has two Terra lenses at zero Critical/Major but is not launch-cleared; G1/G5 and final evidence remain outstanding.

## Next

1. Monitor base Q1 then Q2, the v4 resume and G5 services, and fresh Windows/WSL/C:/D: headroom. If a service fails, inspect metadata-only receipts and re-review before retrying; do not duplicate a running job.
2. After Q1/Q2 attested closes, activate and independently review their BookGreeks jobs. Derive 2021Q1/Q2 G5 weeks from attested status-only base+twin evidence, freeze new timing bundles, and obtain Terra exact-hash review/dispatch approval.
3. Complete G5 timing, run reviewed Stage5 then Stage6 with terminal/output hashes, then activate and run the four-quarter Kelly assessment after every declared gate. Update this note with real receipts and post-run checks.

## Blocked / owner decisions

- Shared RAM and serial timing policy prevent another heavy WSL launch during the current base work. C: remains about 16 GiB free; recheck, do not infer future headroom.
- A0006 wording, hedge costs, parity-forward mark, hard-to-borrow unreadable window, origin_two shrinkage, E1b, gross-share hedge cost, stale test_configs manifest, and prior instantiator sign-off remain owner items.

## Read first

- [ADR-0313 and G5 amendments](../../../docs/architecture/decision-log.md)
- [Locked Path](decisioning/path.csv)
- [MIO queue runbook](plans/mio-parallel/RUNBOOK.md)

## Do not

- Edit the nine A0010 formulations or Path; keep DEVELOPMENT_SEAL at `2021-07-01`.
- Open protected economics, forecasts, scores, or run logs; no 2022-2023 MIO economics before the owner's three picks, and no 2024+ data.
- Launch a four-quarter arm before G1/G5 and independent Terra clearance. Respect WSL/Windows memory and C:/D: disk gates.
