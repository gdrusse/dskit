## Replaced at 2026-11-27 JuMP native wrap

## Core (dskit itself)

**Now.** The reviewed Python candidate adds a three-line fast path for packing
rank-one built-in lists and tuples. Candidate
`1df99f63e75f49ef0fd95ba37568f59d463e9729`, based on
`9bb26e40eae09d5002305ef4b329b7998106db71`, has completed development review.
The 27 focused contract tests and Ruff passed. Two fresh Luna final reviews
reported zero Critical, Major, Minor and Nit findings. Two runs measured
2.89% and 5.03% faster Python preparation for a 4,096-entry tuple open.
The representative list result was inconclusive. The timings cover Python
preparation only. The landed ADR-0328 suite provides the Julia worker and the
`budgeted_select` and `assignment` reference programs. No new Julia model shipped.

The approved runtime direction uses HiGHS by default and benchmarks SCIP.
The local design keeps typed decision state and the Program/Search loop in
Julia, with independent verification and publication under host control.

**Next.** Recheck admission, then run the native shape baseline and RED steps
in the handoff. Continue with Sol implementation, speed and RAM checks, two
fresh Luna reviews, and Terra fixes for review findings. Preserve the unmerged
worktree `/home/russell/wt/codex-jump-shape-20261126`. Phase 0 cleared, and its
only change is 41 unexecuted test lines. Production is unchanged.

Repair two separate pre-existing Major lifecycle defects before relying on
pooled trading updates. Pooled updates can see stale bounds under caller
fixes, and free or search cleanup can erase a program-owned fixed equality.
Both findings have static witnesses and await native regression tests.

**Blocked / owner decisions.** At 2026-11-27 02:08 UTC, C: had 5.1 GiB free
and Windows had 3.1 GiB available RAM, below the 4 GiB reserve. No native
jobs launched. The owner approved a 5 GiB C: reserve for this task. The shared
10 GiB reserve remains in force. The mathematical contract and actual MIO
port are pending. See the immediately prior Core archive for outstanding
amendment choices, storage actions and the disclosed Minor backlog.

**Read first.** [Vector evidence](</home/russell/data/dskit/runs/jump-vector-pack/v20261126/receipt.md>),
[runtime design](</home/russell/reports/JuMP MIO runtime design.md>) (SHA-256
`d900dd05877a95942a1257ecd41711599c0c2c6f2641749920796cab63ab52f9`),
[shape handoff](</home/russell/data/dskit/runs/jump-shape/v20261126/HANDOFF.md>),
[lifecycle packet](</home/russell/research_notes/JuMP MIO transition experiments/lifecycle-next-packet.md>),
and [lifecycle review](</home/russell/research_notes/JuMP MIO transition experiments/lifecycle-design-review.md>).

**Do not.** Extrapolate Python preparation timings to Julia or solver runtime,
run native jobs before renewed admission, or treat development review as
approval for a real MIO port, replay, or deployment.

## Replaced at 2026-11-26 JuMP vector packing wrap

## Core (dskit itself)

**Now.** 2026-11-26: the JuMP suite landed (ADR-0328 and its amendment 1).
`dskit/pipeline/libs/jump.py` is the Python doorway (`JumpSolve`, `JuliaWorker`,
`LiveModel`, `DataContract`); `julia/DskitJuMP/` is the Julia package it drives
through a long-lived subprocess speaking JSON lines (wire protocol v2, written
out in the amendment). A model is built once and changed in place: open, update,
fix, free, start, solve, search, close, a pooled model per shape, Arrow files
for large arrays. Two reference programs ship, `budgeted_select` and
`assignment`; no real MIO model is ported. Reviewed candidate `42ddafca`: two
clean final skeptic lenses, zero Critical or Major. Julia tests 363 passed;
`tests/pipeline_libs/test_jump.py` 449 passed, 7 skipped, with the cross-worker
conformance and the pyomo parity test run against real Julia 1.13.4. Measured
on the `assignment` program at 20,000 items: in-worker build 0.085 s against
pyomo 1.04 s, an `open` seen from Python 0.31 to 0.37 s, an in-place update
0.002 s, solves equal. The earlier storage and relocation state is unchanged
and is in the archive.

**Next.** Port the first real program (its own approval, a new formulation hash,
never a replacement of a locked pyomo result). Run the 200,000-item size of
`tools/jump/benchmark.py` once C: has headroom. Then the Minor backlog below.

**Blocked / owner decisions.** C: sits at the 10 GiB reserve; the staged
`C:\Users\russe\compact-wsl.cmd` (untested, needs the admin prompt) would
recover about 58 GiB. The WSL upgrade, sparse-reclaim pilot and full backup
remain unperformed. Owner to confirm the choices the builders made where the
amendment was silent (listed in its implementation notes): the `components`
field, `shape_changed` refusing rather than rebuilding, labels counting as
shape, the `internal` error kind, and the pool's byte cap reading Julia-side
memory only. The disclosed Minor backlog (not fixed) closes the amendment.

**Read first.** ADR-0328 and "ADR-0328 amendment 1" in
`docs/architecture/decision-log.md`; `dskit/pipeline/README.md` "What ships";
`tests/pipeline_libs/jump_fake_worker.py` (the executable protocol reference);
`/home/russell/storage-investigation/storage-policy.json`.

**Do not.** Run the Julia half of the suite without `~/.local/bin` on `PATH`
and call the skips a pass. Treat the measured speedups as evidence beyond
20,000 items, or as a solve speedup. Add WSL free space to C capacity, delete
protected results, or treat development review as deployment approval.

## Replaced at 2026-11-26 JuMP suite wrap

## Core (dskit itself)

**Now.** 2026-11-24: non-DSKit relocation completed: 10.64 GB verified on D,
old paths preserved, DSKit/shared inputs retained on C. Gears5 and the verified
empty384 GiB D image were retired by owner request. Windows measured C17.3 GB
and D734.2 GB free. Normal admission passes; the migration exception is retired.
No WSL shutdown, upgrade or compaction occurred. The index_options lane holds
the unfinished mathematical MIO and cadence-study re-entry.

**Next.** Follow that lane note; check storage and host RAM before bulk work.
The shared .venv imports from a preserved older checkout. Bind imports explicitly
to reviewed code; never repoint an active environment.

**Blocked / owner decisions.** The WSL upgrade/sparse-reclaim pilot and full
backup remain unperformed; wrap does not resume them. Preserve other sessions'
unpublished commits, active jobs and frozen pins. Earlier ADR-0284 Mac
path-mapping findings were not addressed here.

**Read first.** `/home/russell/storage-investigation/non-dskit-migration-20261124/completion-receipt.json`;
`/home/russell/pmquant/docs/data-locations.md`;
`/home/russell/storage-investigation/storage-policy.json`;
`/home/russell/storage-investigation/storage-migration-plan.json`.

**Do not.** Add WSL free space to C capacity, claim full backup or automatic
reclaim was enabled, delete protected results, or treat development review as
empirical evidence or deployment approval. Normal reserves are C10/D150 GiB.

