# MIO evaluation: Codex wrap and Claude handoff

2026-11-19. Owner requested: "Let's wrap. Kick off a Claude agent to finish this."
Codex is stopping substantive work and transferring this existing isolated worktree.
The tests are NOT complete. Too much effort went into repeated review/framework revisions.
Do not repeat that pattern; prioritize actual remaining comparisons and a useful final memo.

## Start here

- Worktree: `/home/russell/wt/mio-combined-evaluation-20261117`, branch `codex/mio-memory-ready-20261117`, HEAD `a1a925b3b7e90b7f171667396db9d0724437da31`.
- RUN: `/home/russell/data/index_options/runs/mio-combined-evaluation/v20261117`.
- EV: `RUN/validation/copula-packet-20261117`. Most work/evidence lives there, outside Git.
- Current durable state: `RUN/study-state.json`. This handoff supersedes stale progress fields.
- Python: `/home/russell/dskit/.venv/bin/python`. Scientific source is frozen original e2 (`e2c1cc7ba482414cb550624be062b5280c1744e9`) or explicitly named a1; do not repoint the shared environment.
- Read repo CLAUDE.md, implementation-workflow, skeptic-review, lane note and locked Path. Reuse unchanged qualified components; only affected final review needs renewal. No full test suite.
- Codex agents are idle/finished or usage-limited. No active MIO process was found at handoff. Luna remains stopped; do not restart it.

## What is actually done

Only **Jan 4, 2021** of the eight preselected real states has plain/relax comparisons on both WSL and Mac. Do not rerun it.
WSL plain procedure 216.907s, exact utility 339.2003; relax 177.950s, utility 348.6275.
Mac plain procedure 169.825s, independently WSL-rescored utility 357.6607; relax 139.817s, utility 348.6275.
Relax was faster in this state, but quality ordering changed by host. There is no universal best method yet.
Evidence: `EV/mac-wsl-jan4-pair-method-priorities.json` and `mac-pair-wsl-rescore-real-a1-terminal.json`.

The new shared frozen-state runner is source-closed at `frozen-panel-candidate-v2.json`.
Two final reviews: `frozen-panel-v2-correctness-review.json`, `frozen-panel-final-integration-review-v2.json`.
35 focused tests passed. WSL tiny plain and relax passed against saved reference results.
Mac tiny plain finished successfully at 13:22:40 UTC; its remote owned process tree drained.
Parents: `frozen-panel-saved-tiny-e2-{wsl-plain,wsl-relax,mac-plain}-a1-parent.json`.
Mac plain parent SHA256: `37de8b200d823cc03d4271bc324231dd620ef9c1b05043e70bd325a4f5adc61b`.
These are synthetic runner checks, NOT additional real panel results. Mac tiny relax is still unrun.

Q2 e2-versus-a1 fixed-action two-date discriminator PASSED (Apr 1/Apr 6, postings through Apr 7).
Evidence: `q2-prefix-real12-authfix-terminal-audit.json`; full-quarter compatibility remains unrun.
The original a1 live Q2 control completed 14 decisions but FAILED the outer exact historical-pick gate.
P&L 4681.75662 versus old e2 5448.86198; 29 versus 33 trades. Its registry is still claimed/exposed, unpublished.
Evidence: `q2-control-a2-terminal-audit.json`. Never relabel this as an accepted control.

Branch cleanup and Markdown audit were already completed: 53 local/14 remote branches archived, active Claude branches preserved; 785 Markdown files audited, 28 docs plus PROCESS corrected, docs main commit 233ea53c. Do not redo this cleanup.

## Next bounded work, in order

1. Finish the existing narrow Jan 11 diagnostic; do not build another framework.
   Partial UNFINISHED files in EV: `jan11_numeric_audit.py`, `jan11_numeric_observer.py`, `jan11_numeric_storage.py`, `jan11_numeric_worker.py`, `launch_jan11_numeric.py`, `test_jan11_numeric_audit.py`.
   These were left by an Astra agent at the usage limit: no final candidate, no final reviews, no native qualification. Inspect, finish minimally, qualify the actual native rejection path, and get only required focused review of changed code.
2. While independent work fits, complete Mac tiny relax once through `launch_frozen_panel_v2.py`, binding the successful Mac plain parent. Existing Mac plain jobs JSON is a template; don't overwrite it or fabricate a parent.
3. Run one newly named Jan 11 forward private state capture only after its new checks pass and resource admission succeeds. Preserve the failed strict artifact. Bind the newly eligible case with a small explicit consumer delta; then run plain/relax on WSL and Mac as resources permit. Retain genuine previous-climb warm-start only when applicable and actually qualified; currently generic warm execution is unimplemented, not silently tested.
4. Continue the remaining prespecified six states using existing capture mechanisms. `speed-panel-prespecification.json` names Q2 Apr 1/Apr 20/Jun 8; Q3 Jul 1/Jul 20/Aug 3; Q1 Jan 4/Jan 11. `q2-q3-three-target-prefix-capture-phase0-proposal.json` proposes two quarter-prefix captures instead of six repeated intakes; this is NOT an implemented/qualified launcher.
5. Run the already source-closed full Q2 fixed-action compatibility check when useful and admitted. Command below. This uses archived actions, not another full MIP quarter. If strict comparison fails, retain precise evidence, do not silently relax the economic gate.
6. Complete authorized combined-feature testing when dependencies permit, then write the final memo: measured speed/quality, applicable features, limitations, remaining blockers, and next decision. No invented ETA. Small status updates only when something changes.

## Jan 11: exact issue and authorized narrow fix

Old `q1-jan11-asof-real12-a1` capture ran about 12.6 minutes, peaked at the 12 GiB cgroup ceiling, and failed exact comparison of `0.program_hedge.units_raw` for a Jan 4 GME rejected order. Its provisional D/S/context files are INELIGIBLE, no receipt. Failure audit: `jan11-asof-real12-a1-failure-audit.json`.
A no-solve reconstruction using accepted Jan 4 S proved current raw units 90.51009425886703 vs archived 90.51009425886699: 3 ULP / 4.263256414560601e-14 shares. `jan4-gme-raw-a1-terminal.json` passed. Cause is not proven; do not blame a host or serializer.

Controlling scope: `state-only-numerical-materiality-clarification.json` SHA `9e1da671b9edd9f010640ed5c22e4fec0388e8231b217b4eef78f430929b14cf`.
Phase 0 reviews: `continuous-output-equivalence-diagnostic-phase0-{correctness,integration}-review.json`.
A NEW private state-only artifact is authorized without another user permission request; old failures and official gates stay unchanged.
Only `program_hedge.units_raw` for proven pre-admission rejected/unfilled orders (`insufficient_fill_size` or `missing_fill_quote`) and exactly linked decision-book copies may use the prospective ceiling: finite exact floats, same nonzero sign, <=4 ULP AND <=1e-12 shares, same truncation/nearest integer, >1e-9 away from an integer. Must prove actual native early return before hedge attach/account admit, identity/legs/picks/reason linkage.
All integers/actions/keys/types/provenance/state stay exact; existing money check <=0.01 per field remains. No operative/held/admitted exception. Never round native state. Record strict and numerical matches separately. Traverse all safe bounded mismatches; later bad/held differences, overflow, unknown/incomplete traversal must refuse release. Keep full intake/calendar/fixed pricing and actual guarded drain proof.
Useful old qualified files: `jan11_asof_worker_real12.py`, `launch_jan11_asof_real12.py`, `jan11_prefix_observer_v2.py`, `jan11_prefix_storage.py`. Old native prefix/full-tail fixture already passed; reuse unchanged proof.

## Ready full-Q2 fixed-action command and feature gate

Candidate: `q2-full-fixed-log2-candidate.json`, final correctness/integration reports with `log2` in name.
Native full-tail synthetic fixture passed: `q2-full-fixed-fixture-terminal-audit.json`.
Real lock `q2-full-fixed-real-log2-lock.json` SHA `2c0eefca8f2d40ac6d7f3531877d1bf382c22f9c03c89d0c6cedc71c0aceec21` (61 files).
Prepared jobs: `q2-full-fixed-real-log2-a1-jobs.json`; placement plan not yet created.
Command: project Python `-B EV/launch_q2_full_fixed_v2.py real 2c0eefca8f2d40ac6d7f3531877d1bf382c22f9c03c89d0c6cedc71c0aceec21` (expand EV).
Hard 12 GiB / zero swap / guard 12.5 GiB; .1 derived +3.25 scratch +.04 EV GiB. Runtime ceilings are NOT an ETA. Previous live peak 11.7393 GiB means fit is uncertain.
Shared historical adapter auth bug was fixed in `chronological_capture_adapter_authfix.py`; the old full-Q1 capture closure is quarantined and MUST NOT be dispatched.

Official Q2 primary comparator/publication/joint dependency amendment remains a separate OWNER decision, not authorized by the private numerical extension. Prepared: `q2-control-primary-comparator-owner-proposal.json`, `q2-control-prospective-acceptance-dependency-review.json`. Rule: docs/skills/skeptic-review.md:36, "Owner approval is required for a material contract/threat-model change." Do authorized compatibility work first; ask only for a concrete remaining material change.
Old calibrated/risk rankings are provisional because control change exceeds claimed gains. Q1 risk/both were owner-halted after failed pricing at 7/13: NEVER restart. Early-close is not supported by the static-hedge/DTE combined setup; do not imply all features were tested. No 2024+ data, holdout, refit, paper/live actions.

## Resources and existing worker entry points

Always run WSL `python3 /home/russell/storage-investigation/storage_guard.py status` before dispatch and at completion; read its policy/layout/catalog. Use guarded launchers, declare all peaks, refuse on refusal. No reserve lowering. C: reserve 10 GiB; Windows and WSL share physical RAM. Do not add their headroom.
Read canonical `/mnt/c/Users/russe/.codex/skills/mac-compute-worker/SKILL.md`; plan jobs through its compute_run.py and resource_probe.py. New output containers already registered: `/home/russell/data/index_options/{derived,scratch}/mio-solver-panel/v20261119`. EV is within the registered run.
Strict local 12.5 GiB admission needs about 19 GiB Windows available and 18.5 GiB WSL available; remeasure. At 13:28 UTC guard allowed, 0 reservations, Windows available 19.51 GiB, WSL 21.31 GiB, C: free 62.47 GiB. This is NOT future admission.
Mac SSH alias `mac`; native Python `/Users/gibsonrussell/Compute/software/mio-worker-20261102/bin/python` (3.12.3). Mac real 12 GiB sampled child +0.5 supervisor +4 OS needs >=16.5 GiB exact available; last pre-resume available about 15.99 GiB. Current value unknown. No psutil there; use vm_stat free+inactive+speculative pages and pressure/swap checks.
Existing encrypted readonly SMB inputs: `/Users/gibsonrussell/Compute/wsl-compute-20261102/inputs`. Fixture alias `mio-speed-panel-fixture-20261119`, bundle alias `mio-speed-q1-e2-20261119`. Mount receipt `frozen-panel-fixture-mount-20261119.json`. No Mac research SSD writes. Native supervisor returns bounded evidence to WSL.
Frozen fixture case `frozen-panel-fixture-case.json` SHA `a6328b092d69c12afa85b869b2d05401c75a29cddac1b404fd837877142721b8`; lock `frozen-panel-fixture-v2-lock.json` SHA `ad6d8c31b0112ec8b9be48802ad991605b8b1c319bd663cfaa33ec6a116d1765`.
Mac real outputs need independent pristine-S WSL rescore, no extra MIP. Generic new-case rescore entry is not yet implemented; reuse Jan 4 mechanism with a reviewed case-binding delta.

Claude GUI/VS Code were closed and clean RAM cache dropped with permission. Preserve independent Claude CLI, recorder, current app and all other jobs unless identified and explicitly within authorization. Do not kill/relocate jobs, delete datasets, compact disks, restart Luna, or keep expensive agents polling. No research job was left running at handoff.
