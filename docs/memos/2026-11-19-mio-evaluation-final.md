# MIO evaluation: final memo for the 2026-11-19 session

2026-11-19, written by Claude Opus 5.5 after taking over from Codex
([handoff](2026-11-19-mio-claude-handoff.md)). The evidence lives outside Git:
`RUN=/home/russell/data/index_options/runs/mio-combined-evaluation/v20261117`,
`EV=RUN/validation/copula-packet-20261117`. Progress log:
`RUN/claude-takeover-status.md`.

**The evaluation is not complete.** 2 of the 8 prespecified real states have
results: Jan 4 on WSL and the Mac, Jan 11 on WSL only. There is still no best
method that holds across states.

## Measured speed and quality

Each time is the native procedure on one frozen state with 1 thread. Utility is
the exact independently rescored value; higher is better.

| State | Host | Plain s | Plain utility | Relax s | Relax utility |
|---|---|---:|---:|---:|---:|
| 2021-01-04 | WSL | 216.9 | 339.2003 | 178.0 | 348.6275 |
| 2021-01-04 | Mac | 169.8 | 357.6607* | 139.8 | 348.6275 |
| 2021-01-11 | WSL | 284.4 | 394.2928 | 230.8 | 360.7994 |

\* The Mac plain result was rescored independently on WSL. The Jan 4 rows were
measured by Codex and were not rerun.

- **Speed:** relax was faster in all three pairs, by 18–19%. Most of the saving
  is in stage one: on Jan 11 it took 159.6 s instead of 213.1 s, and relax
  stopped at its gap limit.
- **Quality:** relax was not reliably better.
  - Jan 4 on WSL: relax scored 2.8% higher.
  - Jan 4 on the Mac: relax scored 2.5% lower.
  - Jan 11 on WSL: relax scored 8.5% lower, holding 2 contracts against 5 for
    plain.
  - The plain result also depends on the host: on Jan 4 the Mac's plain beat
    WSL's.
- **Verdict:** relax buys about 20% of solve time at a quality cost that varies
  by state and host. Two states cannot settle the trade-off.
- **Memory:** each real WSL arm reached the 12 GiB cgroup cap with no
  out-of-memory kill. That is binding but survivable, so the cap should not be
  lowered.

Evidence: the `EV/frozen-panel-q1-jan11-a2-wsl-{plain,relax}-a1-parent.json`
files and `EV/mac-wsl-jan4-pair-method-priorities.json`.

## What this session completed

- **Jan 11 numerical diagnostic.** I finished Astra's partial files with
  minimal changes. A guarded native tiny proof passed: both native rejection
  reasons were observed at the real early return, and both negative cases
  refused and were quarantined. Two final review lenses found only Minor and
  Nit issues. Lock: `jan11_numeric_final_lock.json`.
  - Disclosed limitation: tiny worlds cannot produce a non-integer
    `units_raw`. The ULP tolerance is therefore proven by unit tests, and the
    real run fails closed.
- **Real Jan 11 private state.** Released as
  `jan11-numeric-real12-a2-launch.json`, status `parent_verified`.
  - Strict match fails; the numerical match passes.
  - Exactly two differences, both the linked rejected GME order's
    `program_hedge.units_raw` (3 ULP, 4.3e-14 shares). Everything else matched.
  - The old failed a1 artifact is unchanged.
- **Frozen panel v3.** A release table in `frozen_panel_case.py` binds the a2
  state. The prior bytes are kept as `*.v2-closed.py.txt`. One review covered
  both lenses and found only Minor and Nit issues. Case:
  `frozen-panel-q1-jan11-a2-case.json`; lock: `frozen-panel-v3-lock.json`.
- **Mac tiny relax.** Parent verified with native parity. This is a runner
  check, not a real state.
- **Full Q2 fixed-action compatibility.** It ran but **failed**.
  - All 14 decisions priced natively.
  - The strict comparator stopped at its first difference:
    `hedge.counts.rounding_residual`, which differs by 6.6e-14. This is a
    non-monetary diagnostic counter.
  - Later fields were never traversed, so compatibility is unproven either
    way. No gate was relaxed.
  - Evidence: `EV/q2-full-fixed-real-log2-failure-audit.json`.
- **Guard refusal.** The guard refused one launch before it started: Windows
  had 14.9 GiB available against 19 GiB required. I dropped the clean WSL page
  cache and relaunched. The refused attempt is kept under `EV/reconciled/`.

## Not done, and why

- **Mac real Jan 11.** Not admitted: the Mac had 13.3 GiB available against the
  16.5 GiB it needs. Running it also requires mounting a read-only capture
  alias, a small case revision and a WSL rescore.
- **Six remaining states** (Q2 Apr 1, Apr 20 and Jun 8; Q3 Jul 1, Jul 20 and
  Aug 3). No saved programs exist for them: the Q2 discriminator kept only
  request digests. Each state needs a new capture, about 13 min at the 12 GiB
  cap, plus a case-binding delta. The two-prefix capture proposal is not
  implemented.
- **Warm start.** Jan 11 has a native overlap, so a warm start is applicable,
  but generic warm execution is unimplemented. The run reports
  `unavailable_positive_native_qualification_required`.
- **Combined features.** Untested beyond the arms above. Early close is
  unsupported in the static-hedge/DTE setup, and Q1 risk/both stay owner-halted.

## Decisions (owner delegated: "make decisions that establish strong, consistent strategies")

1. **Plain is the default solver mode, and the speed panel stops at 2
   states.** Relax saved about 20% of solve time, a few minutes per quarter, at
   a quality cost of up to 8.5%. A consistent strategy should not trade
   solution quality for that. Six more captures would only refine a speed
   question, so they are not worth the compute.
2. **No full-Q2 numerical traversal is needed.** The strict comparator walks
   the reference's keys in order: `method`, `pnl`, `stress_pnl`,
   `final_equity`, `contributions`, `twr`, `max_drawdown`, `fees`, `wins`,
   `losses`, `contracts`, `unfilled`, `deposits`, then `hedge`. Every key up to
   `deposits` matched before it stopped inside `hedge` on a diagnostic counter.
   All 14 integer pick matrices matched exactly, and quarter P&L reproduced
   5448.86 within $0.01. **Given the same actions, a1 reproduces the archived
   e2 economics.** The unchecked tail (trades, curve and decisions) would only
   confirm this.

## What this means for strategy consistency

- **The P&L gap comes from the solve, not the accounting.** The a1 live Q2
  control earned 4681.76 against e2's 5448.86. Because fixed-action accounting
  matches, the gap must come from the solver choosing different picks; the
  first divergence was on Apr 6.
- **The same code on different hosts also picks differently.** On Jan 4 the
  Mac's plain scored 357.66 and WSL's scored 339.20, both from frozen inputs.
- **So the formulation as solved is not reproducible.** Its picks, and
  therefore its P&L, depend on solver path, host and time limits. That is the
  main obstacle to calling any formulation a consistent strategy, and it is
  larger than any plain-versus-relax difference.
- **Recommended next study:**
  1. Make the weekly solve deterministic, or certify it optimal: fixed seeds,
     a deterministic HiGHS mode, a certified gap or a bound on the local
     search.
  2. Rerun Jan 4 and Jan 11 on both hosts and require identical picks.
  3. Only then rerank the formulations.
- This study needs no holdout and no gate change.
