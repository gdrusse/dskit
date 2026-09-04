# Gate 3: lower-compute, statistically defensible null design

Date: 2026-09-04. Revised 2026-09-04 after review: score-level
resampling is not Gate 3, and the 100× figure is not a Gate 3 saving.

## Question

ADR-0089: Gate 1 selects; Gate 3 must still ask whether the **identical
fitting path** can manufacture that result after whole-session labels are
exchanged. How do we keep that test valid and spend fewer walks than
every survivor × 19 seeds?

## Finding

Gate 3 stays a session-scramble **refit**. Fail-fast after the first
beating null. Reuse the cached, label-free features. Do not replace the
audit with a stored-score bootstrap.

### What Gate 3 must prove

```text
d_t = (y_t - benchmark_t)^2 - (y_t - model_t)^2
```

Positive `d_t` favours the model. The reported statistic is the same
pooled skill as Gate 1 (`R2oos` / studentised DM).

A stored-score session bootstrap answers a different question: uncertainty
on a **frozen** prediction panel. It cannot see a leak or a fitting
artefact already baked into those forecasts. Giacomini–White keeps
estimation uncertainty by treating the **rule** (including refits) as the
object; resampling stored `d_t` is not that test. P8 Tier 1 remains the
cheap score-level bar. It is not Gate 3.

The scramble unit is one regular-trading session. Training and validation
maps stay independent. One map is shared across stocks. That preserves
within-session autocorrelation, overlapping labels, time-of-day, and the
cross-stock vector, and breaks only the link between features at `t` and
the return over `[t, t+h]`. Call the result a test under that
session-scramble null, not an unconditional exact p-value. Do not permute
rows (Romano–Tirlea).

### Decision rule (unchanged rank test)

An asset **passes** only if the real `R2oos` beats every completed null
**and** the null t-statistics on that full family sit in the frozen
calibration band (centre `< 0.3`, SD in `(0.7, 1.4)`).

With `B` completed nulls and zero exceedances,

```text
p_MC = (1 + #{null R2oos >= observed}) / (1 + B)
```

`B = 19` and zero exceedances is `1/20 = 0.05`, not stronger. Keep the
frozen 19-seed family as that α=0.05 rank test. A strict `p < 0.05` would
need `B >= 20`.

### Fail-fast is the same decision, fewer walks

`Gate3WalksStage` currently runs seeds 0..18 for every Gate-1 survivor,
then `tier2_verdict` asks whether the real walk beat **every** scramble.
One exceedance already fails `beat_all`. Remaining seeds cannot change
that fail.

Run seeds in order. After each seed:

1. If any completed null `R2oos >=` observed, **stop that asset as fail**.
   Calibration is not claimed on a 1- or 2-draw stub; the asset already
   failed the rank test.
2. If all 19 lose, run the existing spread/centre check on those 19.
3. A case that never loses and has no remaining budget is not an optional
   pass.

This is Besag–Clifford stopping at the first exceedance (`h = 1`) with
`B_max = 19`. It is equivalent to today’s pass/fail, not a looser test.
Gandy sequential Monte Carlo is unnecessary here: the rule is a rank
decision, not a bounded-risk p-value versus a moving α. Do not stop a
**pass** early.

P11 asset-local models do not share a fit. One scramble map across names
keeps the joint day-shuffle; it does not divide LightGBM cost by 13.

### Calibration is not “B=19 is too noisy, skip it”

Nineteen draws cannot **precisely** certify a narrow SD (relative SE of
a sample SD is `1/sqrt(2×18) = 16.7%`). They **can** detect a large
break. P10’s Gate 3 spreads were 0.608 and 0.667. Under σ=1,
`(n-1)s² ~ χ²_18`; those give 6.65 and 8.01 against a 5% lower critical
value of 9.39. That is a real studentisation failure, not sampling noise.
Keep the per-asset band on every **completed** 19-seed family. A
pipeline-version size study (100–400 independent outer null panels) is
an extra claim, not a replacement for that check.

Availability and negative controls stay beside the test. A scramble only
breaks the path it remaps.

### Why this is faster

Let `C_fit` be one Gate-3 walk that **mmaps the Gate-1 feature cache**,
scrambles labels, and refits. Features do not read `y`; rebuild is
forbidden. Cache reuse is already in the P10/P11 path; it is a per-seed
cost floor, not a new 100×.

The new saving is **fewer seeds on failures**:

| Path | Walks for 13 Gate-1 names |
| --- | --- |
| Current: all 19 seeds, then judge | 247 |
| Fail-fast: stop at first beating null | 13 × (seeds until first exceedance or 19) |
| Passer | still 19 |
| Score bootstrap only | cheap, **wrong question** |
| Straw man `B=500` vs `K=5` real origins | P8 Tier 1, not Gate 3 |

Expected cost drops when Gate-1 selections are false (a scramble beats
them early). Genuine candidates still pay 19. Elapsed time also depends
on load, fit, and parallelism; this is a walk-count bound, not a promised
wall-clock factor.

### Proposed Gate 3 rule

An asset passes only if (1) Gate 1 selected it, (2) availability and
negative controls pass, (3) the real `R2oos` beats every completed
session-scramble refit, with seeds stopped at the first exceedance, and
(4) a completed 19-seed family is calibrated. Report effect size with
the dependence-aware interval. A small p-value is not a trading claim.

This is a research recommendation, not an ADR or a staged-document change.

## Sources

- Besag, J. and Clifford, P. (1991), *Sequential Monte Carlo p-values*,
  Biometrika 78, 301–304.
- Phipson, B. and Smyth, G. K. (2010), *Permutation P-values Should Never
  Be Zero*. <https://gksmyth.github.io/pubs/PermPValuesPreprint.pdf>
- Romano, J. P. and Tirlea, M. A. (2020), *Permutation Testing for
  Dependence in Time Series*. <https://arxiv.org/abs/2009.03170>
- ADR-0074, ADR-0089; `dskit.pipeline.attempts.tier2_verdict`.

## Related

- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — score-level bar (not this audit).
- `docs/explanations/shuffle-and-retrain-test.md`
- `docs/memos/p10-modelability-pipeline.md` — null SD 0.608 / 0.667.
- `docs/memos/p11-modelability-pipeline.md` — 13 Gate-1 names; Gate 3 unrun.
