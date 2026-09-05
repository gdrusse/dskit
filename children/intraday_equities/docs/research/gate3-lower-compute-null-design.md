# Gate 3: lower-compute, statistically defensible null design

Date: 2026-09-04. Revised 2026-09-05: merges the fail-fast finding with the
measured cost of the recorded run and the engineering work now shipped.

## Question

ADR-0089: Gate 1 selects; Gate 3 must still ask whether the **identical
fitting path** can manufacture that result after whole-session labels are
exchanged. How do we keep that test valid and spend fewer walks than every
survivor × 19 seeds?

## Finding

Gate 3 stays a session-scramble **refit**. Fail-fast after the first beating
null. Reuse the cached, label-free features. Do not replace the audit with a
stored-score bootstrap, and do not pool null draws across assets.

### What Gate 3 must prove

```text
d_t = (y_t - benchmark_t)^2 - (y_t - model_t)^2
```

Positive `d_t` favours the model. The reported statistic is the same pooled
skill as Gate 1 (`R2oos` / studentised DM).

A stored-score session bootstrap answers a different question: uncertainty on a
**frozen** prediction panel. It cannot see a leak or a fitting artefact already
baked into those forecasts. Giacomini–White keeps estimation uncertainty by
treating the **rule** (including refits) as the object; resampling stored `d_t`
is not that test. P8 Tier 1 remains the cheap score-level bar. It is not Gate 3.

The scramble unit is one regular-trading session. Training and validation maps
stay independent. One map is shared across stocks. That preserves within-session
autocorrelation, overlapping labels, time-of-day, and the cross-stock vector,
and breaks only the link between features at `t` and the return over `[t, t+h]`.
Call the result a test under that session-scramble null, not an unconditional
exact p-value. Do not permute rows (Romano–Tirlea).

### Decision rule (unchanged rank test)

An asset **passes** only if the real `R2oos` beats every completed null **and**
the null t-statistics on that full family sit in the frozen calibration band
(centre `< 0.3`, SD in `(0.7, 1.4)`).

With `B` completed nulls and zero exceedances,

```text
p_MC = (1 + #{null R2oos >= observed}) / (1 + B)
```

`B = 19` and zero exceedances is `1/20 = 0.05`, not stronger. Keep the frozen
19-seed family as that α=0.05 rank test. A strict `p < 0.05` would need
`B >= 20`.

### Fail-fast is the same decision, fewer walks

`Gate3WalksStage` runs seeds 0..18 for every Gate-1 survivor, then
`tier2_verdict` asks whether the real walk beat **every** scramble. One
exceedance already fails `beat_all`; the remaining seeds cannot change it.

Run seeds in order. After each seed:

1. If any completed null `R2oos >=` observed, **stop that asset as fail**.
   Calibration is not claimed on a 1- or 2-draw stub; the asset already failed
   the rank test. This also sidesteps `tier2_verdict`'s refusal of fewer than
   two nulls — a failed asset never reaches it.
2. If all 19 lose, run the existing spread/centre check on those 19.
3. A case that never loses and has no remaining budget is not an optional pass.

This is Besag–Clifford stopping at the first exceedance (`h = 1`) with
`B_max = 19`. It is equivalent to today's pass/fail, not a looser test. Gandy
sequential Monte Carlo is unnecessary: the rule is a rank decision, not a
bounded-risk p-value against a moving α. Do not stop a **pass** early.

P11 asset-local models do not share a fit. One scramble map across names keeps
the joint day-shuffle; it does not divide LightGBM cost by 13.

### Why pooling null draws across assets fails

Spending `B` draws across the survivors and ranking every asset against one
pooled null needs `t_pool` to be asset-invariant. Studentising removes scale,
not location: under the null a fitted model is worse than its benchmark because
it pays estimation noise, so the centre sits near `-c * sqrt(n_eff) / sigma`,
asset-specific in both terms. Measured: LLY's null centre is −0.37, spread 0.98
(`nineteen-shuffled-walks-…`). No second asset's centre has been measured.
Pooling centres that differ shifts the critical value — simulated, up to about
2× nominal size for the asset whose centre is nearest zero, which by that
formula is the noisiest name, not the cleanest. The `mean < 0.3` guard is
one-sided and cannot see it.

### Calibration is not "B=19 is too noisy, skip it"

Nineteen draws cannot **precisely** certify a narrow SD (relative SE of a sample
SD is `1/sqrt(2×18) = 16.7%`). They **can** detect a large break. P10's Gate 3
spreads were 0.608 and 0.667. Under σ=1, `(n-1)s² ~ χ²_18`; those give 6.65 and
8.01 against a 5% lower critical value of 9.39 — p = 0.007 and 0.022. That is a
real studentisation failure, not sampling noise. Keep the per-asset band on
every **completed** 19-seed family. A pipeline-version size study (100–400
independent outer null panels; at α=0.05, 100 runs give ~5 rejections with an
exact 95% interval of 1.6%–11.3%, 400 give ~20 with 3.1%–7.6%) is an extra
claim, not a replacement for that check.

Availability and negative controls stay beside the test. A scramble only breaks
the path it remaps, and neither a scramble nor a stored-score bootstrap catches
a feature that already contains the future.

### Why this is faster, measured

`docs/decisioning/actions.csv` holds 1,320 P11 Gate-1 fold processes (plus 13
Gate-2) run serially on 2026-09-04 over 1.26 hours — **3.40 s per fold** end to
end. A Gate-3 fold is a Gate-1 fold with a permuted label; P10's own campaigns
corroborate that at their larger pooled size, 19.27 s/fold for Gate 1 against
19.20 for Gate 3. So `13 × 19 × 20 = 4,940` fold processes are about
**4.7 hours**.

| Path | Walks for 13 Gate-1 names | Measured cost |
| --- | --- | --- |
| Current: all 19 seeds, then judge | 247 | ~4.7 h |
| Fail-fast: stop at first beating null | `E[draws \| fail] = 2.73` | ~52 walks, ~1.0 h if 12 of 13 fail |
| Passer | still 19 | unchanged |
| Score bootstrap only | cheap | **wrong question** |

Expected cost drops when Gate-1 selections are false. Genuine candidates still
pay 19, so a survivor-heavy cohort saves almost nothing. This is a walk-count
bound, not a promised wall-clock factor.

### The engineering work (shipped), and what it owes

Two changes are already in the tree and neither touches the test.

**The tape is filtered.** An asset-local walk fits and scores one symbol but was
handed all 25 tapes, so `_tapes_from_bars` masked and copied every symbol's full
1-minute history from 2018 on every fold. Only the asset and the tape its label
declares as the residual are read. Benched: 142 ms for 25 symbols against 11 ms
for two — 13× on its own stage, **3.9% of a fold** at the recorded rate, so
nearly irrelevant to the total. It also **moves the derived walk's identity
hash**, and `_summary_dir` names the run directory by it, so the 1,333 recorded
folds can no longer be resumed: about 1.26 hours to rebuild before it pays.
ADR-0089's stage change did not move them, because `_derived_document` drops
`stages`; this does.

**A walk's folds run concurrently.** `_run_bounded_walk` drove its 20 folds
through a blocking `subprocess.run`. The address-space cap is **not** divided
between them: `RLIMIT_AS` bounds address space rather than RSS, the feature
cache maps all 25 symbols whatever a walk scores, and a divided cap both refuses
mappings measured RSS never sees and breaks resume, since a finished fold is
accepted back only under the limit it ran at. The cap is applied by `ulimit -v`
through `/bin/sh` and read back before `exec`, not by `preexec_fn`, which bars
`posix_spawn` and would fork a pool-running parent into Python before `exec` —
unsafe with threads — while dash's `ulimit` exits 0 even when `setrlimit` fails.
The width comes from `INTRADAY_EQUITIES_FOLD_WORKERS`, never a document, because
a graded knob would orphan prior runs on every tuning change.

This is **unmeasured and bounded**: the pool sits inside one walk's 20 folds
while `Gate3WalksStage` loops the 247 walks serially, so width above 20 buys
nothing, and four folds at `n_jobs=8` request 32 LightGBM threads — `n_jobs`
being itself graded, so it cannot be lowered without the orphaning above.

**It owes an ADR, and the pool is in the wrong tier.** The `ThreadPoolExecutor`
sits in the child; dskit has no parallel execution at all. Capped, resumable,
parallel fold processes is generic pipeline capability, so CLAUDE.md puts it in
`dskit/pipeline` with the cohort left here. The ADR must rule on graduating it,
not ratify it where it is.

## Proposed Gate 3 rule

An asset passes only if (1) Gate 1 selected it, (2) availability and negative
controls pass, (3) the real `R2oos` beats every completed session-scramble
refit, with seeds stopped at the first exceedance, and (4) a completed 19-seed
family is calibrated. Report effect size with the dependence-aware interval. A
small p-value is not a trading claim.

This is a research recommendation. The fail-fast change is a staged-document and
`Gate3WalksStage` change and needs its own ADR; so does the shipped concurrency
work.

## Standing caveat

The 13-survivor count comes from the P11 memo, which marks itself a superseded
record from a mistaken configuration; ADR-0089 says no new Gate-3 run has been
started *by that correction*, though P10 ran a Gate-3-shaped campaign whose walk
rows are in the decisioning README. 247 is arithmetic on a number the active
document has not produced.

## Sources

- Besag, J. and Clifford, P. (1991), *Sequential Monte Carlo p-values*,
  Biometrika 78, 301–304.
- Phipson, B. and Smyth, G. K. (2010), *Permutation P-values Should Never Be
  Zero*. <https://gksmyth.github.io/pubs/PermPValuesPreprint.pdf>
- Romano, J. P. and Tirlea, M. A. (2020), *Permutation Testing for Dependence in
  Time Series*. <https://arxiv.org/abs/2009.03170>
- ADR-0074, ADR-0089; `dskit.pipeline.attempts.tier2_verdict`.

## Related

- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — score-level bar (not this audit).
- `docs/research/nineteen-shuffled-walks-lillys-three-minutes-is-not-luck-and-the-error-bars-are-sound.md`
  — the only measured null centre.
- `docs/explanations/shuffle-and-retrain-test.md`
- `docs/memos/p10-modelability-pipeline.md` — null SD 0.608 / 0.667.
- `docs/memos/p11-modelability-pipeline.md` — 13 Gate-1 names; Gate 3 unrun.
