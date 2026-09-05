# Gate 3: lower-compute, statistically defensible null design

Date: 2026-09-04. Revised: 2026-09-05 after two adversarial reviews.

## Conclusion

Gate 3 refits every Gate-1 survivor under 19 whole-session label permutations
(ADR-0089). On the recorded 13-survivor cohort that is `13 x 19 = 247`
asset-local 20-fold walks — 4,940 fold processes — and it grows with a cohort
that is not final.

A pooled null reference was proposed and FAILED review. What survives is one
exact statistical saving and two engineering ones.

| Change | Effect | Statistical cost | State |
| --- | --- | --- | --- |
| Fold concurrency | ~width x, unmeasured | none | shipped; ADR owed |
| Exact early stop on beat-all | 247 -> ~62 walks | the calibration record on failed assets | needs an ADR, and a `tier2_verdict` change |
| Tape filtered to the asset + residual | 3.9% of a fold | none | shipped; costs a Gate-1 re-run; ADR owed |

**Timing comes from the recorded run, not a bench.** `docs/decisioning/actions.csv`
holds 1,320 P11 Gate-1 fold processes (plus 13 Gate-2) executed serially on
2026-09-04 over 1.26 hours — **3.40 s per fold** end to end. P10's own
campaigns corroborate the assumption below at their larger pooled size: Gate 1
at 19.27 s/fold and Gate 3 at 19.20 s/fold. A Gate-3 fold is a Gate-1 fold with a
permuted label: same rows, features and estimator. So 4,940 fold processes are
about **4.7 hours**. The tape saving is the one benched number here — 142 ms
for 25 symbols against 11 ms for two, so 13x on its own stage and **3.9% of a
fold** at the recorded rate: nearly irrelevant to the total.

Concurrency has NOT been measured, and it is bounded: the pool is inside one
walk's 20 folds while `Gate3WalksStage` still loops the 247 walks serially, so
width above 20 buys nothing. Four folds at `n_jobs=8` also request 32 LightGBM
threads and 4 x 17 GiB of address space, and `n_jobs` is itself graded, so it
cannot be lowered without the orphaning described below. Any width figure is
linear extrapolation until one instrumented walk confirms it.

**The tape filter costs a Gate-1 re-run before it pays.** Adding the
`reference_tape` node moves the derived walk's identity hash, and
`_summary_dir` names the run directory by it, so those 1,333 recorded folds
can no longer be resumed — about 1.26 hours to rebuild at the measured rate.
ADR-0089's stage change did not move them, because `_derived_document` drops
`stages`; this does.

**The shipped work owes an ADR, and the pool is in the wrong tier.** The
`ThreadPoolExecutor` sits in the child (`modelability.py`); dskit has no
parallel execution at all. Capped, resumable, parallel fold processes is
generic pipeline capability, so CLAUDE.md puts it in `dskit/pipeline` with the
cohort left here. The ADR must rule on graduating it, not ratify it where it
is. The `ulimit -v` capped exec is likewise mechanism, and only its reasoning
is unrecorded outside this note — the worker knob and the identity-hash ruling
are already in the child README and CLAUDE.md.

## What Gate 3 asks

*Can the same fitting procedure produce this result when the features carry no
information about the labels?*

| Failure mode | Stored-score resample | Scramble refit |
| --- | --- | --- |
| Luck across the 66 cells run | yes — shared session resample | no |
| Luck across horizons | free — fixed ordered stop spends no alpha | free |
| Variance estimator wrong | no | **yes** |
| Fold geometry or embargo broken | no | **yes** |
| Feature leakage | no | **no** |
| Tuning overfit | not applicable to THIS config | not applicable to THIS config |

**The refit's unique value is a pipeline property, not an asset property.** It
alone catches a wrong variance estimator or a broken fold geometry, and both
belong to the pipeline version. Running them once per survivor tests the same
thing thirteen times. That is what makes an early stop affordable.

**Neither test catches feature leakage.** A sign flip never sees a leak already
baked into a stored forecast; a label scramble breaks the feature-label link
but leaves an illegal future field in the features. Availability and lineage
checks are the only defence.

**Gate 3 carries no multiple-testing correction.** ADR-0088 removed Gate 2 from
selection and placed false-signal control in the MIO as a capital constraint,
`sum_i(x_i * pi_i) <= q * sum_i(x_i)` — explicitly not weighted
Benjamini-Hochberg, with no BH prefilter. A per-asset 0.05 across 13 survivors
leaves `1 - 0.95^13 = 48.7%` odds of at least one false pass. A four-hour cost
is not the binding problem with this gate.

"Tuning overfit" is inapplicable because the P11 scan declares no `hpo_*`
params; the node's optional HPO is an inner split of fold TRAIN, never fold val
(`nodes.py`), and `_score_one` reads the pooled walk, so `objective`/`select`
selects no model (`driver.py`). With a rolling 730-day window and a five-day
embargo the stored `d_t` are genuinely out of sample — the case Giacomini and
White's finite-rolling-window framework covers.

## Why the pooled null fails

The proposal was to spend `B` draws across the survivors, pool them into one
null reference, and rank each asset against it. It needs `t_pool` to be
asset-invariant under the null. It is not.

**Studentising removes scale, not location.** Under the null a fitted model is
worse than the constant it is scored against, because it pays estimation noise
the constant does not, so the null centre sits near `-c * sqrt(n_eff) / sigma`
— asset-specific in both `n` and `sigma`. Measured: `nineteen-shuffled-walks-lillys-three-minutes-is-not-luck-and-the-error-bars-are-sound.md`
records LLY's null centre at **-0.37**, spread 0.98. No second asset's centre
has ever been measured.

Pooling assets whose centres differ shifts the pooled critical value. Simulated
over 13 assets with centres spanning -0.15 to -1.00 at B = 19, the pooled
test runs at up to **2x nominal size for the asset whose centre is closest to
zero** (the figure swings with how draws are allocated and whether `sigma`
tracks the centre, so treat it as a direction, not a constant) — by the formula
above, the asset with the largest `sigma` or the fewest rows, i.e. the noisiest
name in the cohort rather than the cleanest.

**The guard cannot see it.** `tier2_verdict` tests `mean < 0.3 and 0.7 < sd <
1.4` — one-sided on the mean, so a pooled null centred at -0.5 passes. Pooling
also adds between-centre variance rather than averaging it away, so the widest
contributor is hidden.

**And the per-asset guard is unexecutable at the budget that made pooling
attractive.** With `B = 19` over 13 survivors most assets contribute one draw;
`statistics.stdev` needs two. Justifying the pooling needs ~10 draws per asset
— 130+ walks — at which point the per-asset test is already paid for.

## The exact early stop

`tier2_verdict` passes an asset only when `observed_r2 > max(nulls)`. The
beat-all limb is decided the instant one draw reaches the observed value — the
predicate is `null >= observed`, since the rule is strict and a tie decides it
too. Stopping there is exact, not an approximation.

Under exchangeability the expected draws to first exceedance is the harmonic
number `H_19 = 3.548` (simulated 3.549). Twelve null assets and one true
survivor cost about `12 * 3.548 + 19 = 62` walks against 247. A passing asset
by construction draws all 19; only failures stop early, and the frozen seed
order 0..18 makes the stop index reproducible.

**Two prerequisites, neither optional.**

`tier2_verdict` refuses fewer than two nulls (`attempts.py`, "scrambled_r2
needs at least 2 finite values"). P(the first draw decides) = 1/2 exactly, so
**about half of all early-stopped assets produce a record the shipped reader
cannot parse at all** — not a different verdict, no verdict. And `passes` is
not purely beat-all: it is `not reasons`, and `reasons` absorbs the calibration
branch, so an asset with `beat_all` true and `calibrated` false still fails.
ADR-0089 states the rule as beat-all AND calibration. Both must change together
in `dskit/pipeline/attempts.py`, which the child may not edit and which needs
its own ADR.

An early-stopped asset must emit `null_mean`/`null_sd` as null with an explicit
reason and the id of the calibration campaign record covering the run — the
full-depth run on representative assets that certifies the variance estimator
for this pipeline version — never zero, never
silently absent, and not the two-draw values the current code would compute.
Its p-value is the bound `p >= 2/(B+1)`, not a point value.

The campaign's own depth matters: at `B = 19` the sample sd's relative standard
error is `1/sqrt(2*18) = 16.7%`, so the `0.7 < sd < 1.4` band is certified to
about one significant figure. A claim about the gate's SIZE needs independent
outer null panels — at `alpha = 0.05`, 100 runs give ~5 rejections with an
exact 95% interval of 1.6%-11.3%, 400 give ~20 with 3.1%-7.6%.

**Two decisions this document does not take.** Whether to bank the saving at
`B = 19` or spend it on a deeper null — `E[draws]` is logarithmic, so `H_99 =
5.177` and B = 99 costs ~161 walks for a cohort that mostly fails, still under
today's 247, buying `p = 0.01` against the `p = 0.05` floor Gate 3 has now,
though a survivor-heavy cohort costs the full B each and is worse than today.
And whether a stale or missing calibration campaign record halts the gate or
only annotates its rows.

## The two engineering savings (shipped)

**The tape was never filtered.** An asset-local walk fits and scores one
symbol, but `_derived_document` handed the scan all 25 tapes, so
`_tapes_from_bars` masked and copied every symbol's full 1-minute history from
2018 on every fold. Only the asset and the tape its label declares as the
residual reference are read; a `reference_tape` filter node keeps those two,
with the residual read from the document rather than restated.

**Nothing ran in parallel.** `_run_bounded_walk` drove its 20 folds through a
blocking `subprocess.run`, and dskit has no parallel execution anywhere. Folds
are independent and now go through a bounded pool. The address-space cap is
**not** divided between them: `RLIMIT_AS` bounds address space rather than RSS,
the feature cache maps all 25 symbols whatever a walk scores, and a divided cap
both refuses mappings that measured RSS never sees and breaks resume, because a
finished fold is only accepted back under the limit it ran at. Total memory
therefore scales with the width. The cap is applied by `ulimit -v` through
`/bin/sh` and read back before `exec`, not by `preexec_fn`: `preexec_fn` bars
`posix_spawn`, so the child would fork from a parent running a fold pool and
then run Python before `exec` — the pattern CPython documents as unsafe with
threads — and dash's `ulimit` exits 0 even when `setrlimit` fails. The width
comes from `INTRADAY_EQUITIES_FOLD_WORKERS`, never a document: a graded knob
would move the identity hash and orphan prior runs on every tuning change.

## The companion test already exists

The multiplicity question needs no refit, and the machinery is not merely
built but already run: `score_bar` assembles the per-cell session totals and
drives `max_bar` (`runs.py` and `attempts.py`), and P10's Gate 2 called it over a
declared 200-cell family (`modelability.py`) — the machinery ADR-0088 removed
from selection. Reinstating it for P11 is a wiring and family-count decision,
not new capability.

Two details are load-bearing and `max_bar` already gets both right: recentre
each column, which is what imposes the null, and keep the session-cluster
standard error, which is invariant under the sign flip. This CORRECTS
`p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`, which
prescribes recomputing each cell "with the same Newey-West
lag"; a Newey-West denominator is not flip-invariant.

## Standing caveats

The 13-survivor count comes from the P11 memo, which marks itself a superseded
record from a mistaken configuration; ADR-0089 says no new Gate-3 run has been
started *by that correction*, though P10 ran a Gate-3-shaped campaign whose
walk rows are in the decisioning README. 247 is arithmetic on a number the
active document has not produced.

Session scrambling is not an exact permutation test: ordinary permutation need
not even be level under dependence (Romano and Tirlea), and this needs sessions
exchangeable under the stated null, which daily dependence and regime shifts
can violate. Report a refit result as a test under its documented
session-scramble null. ADR-0089's asset-local fits also mean no draw preserves
cross-stock correlation, so P8's "share one map across stocks" no longer
applies.

## Sources

- Giacomini, R. and White, H. (2006), *Tests of Conditional Predictive
  Ability*, Econometrica 74, 1545–1578.
  <https://www.eco.uc3m.es/~jgonzalo/teaching/PhdTimeSeries/GiacominiWhite.pdf>
- Romano, J. P. and Tirlea, M. A. (2020), *Permutation Testing for Dependence
  in Time Series*. <https://arxiv.org/abs/2009.03170>

## Related documents

- Repo-root `docs/architecture/decision-log.md` ADR-0089 (the gate this changes),
  ADR-0088 (HFDR in MIO).
- `docs/research/nineteen-shuffled-walks-lillys-three-minutes-is-not-luck-and-the-error-bars-are-sound.md`
  — the only measured null centre.
- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — the companion test's design.
- `docs/memos/p11-modelability-pipeline.md` — the 13-survivor count.
