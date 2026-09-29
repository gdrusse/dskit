## Question

Do the predictive price-CDF models assume the wrong distribution, and can
bounded architecture/training HPO plus pooling improve them? Owner requested
this investigation and separate index heads on September 28, 2026. This note
records research and pre-experiment assumptions, not completed HPO results.

## Finding

The terminal **raw-price log return to a listed expiry** remains the right
target for an intact terminal condor payoff. A distribution of realized
volatility alone does not identify signed terminal returns. The physical
distribution must not be confused with the option-implied risk-neutral one.
Early exits, assignment and dividend handling need additional path/execution
models; a terminal marginal CDF does not settle those questions.

Gaussian mixture components do NOT mean the final curve must be one symmetric
bell: distinct means, variances and weights permit skew and multiple modes.
But a finite Gaussian mixture still has Gaussian-decaying extreme log-return
tails. Finite-knot empirical/quantile curves impose endpoint assumptions too.
The prior comparison's tail misses establish inadequate predictive calibration,
not that any one family is mathematically disproved.

Financial-return evidence supports testing heavy-tailed conditional errors,
changing volatility and asymmetric losses. Cont's empirical review discusses
both unconditional and conditional heavy tails; these are qualitative patterns,
not an identification of a uniquely correct Student-t law for our ETFs or all
expiries. The arch documentation demonstrates Student-t conditional errors in
a financial volatility model, but its example is not evidence of our model's
out-of-sample advantage.
[Cont](https://rama.cont.perso.math.cnrs.fr/pdf/empirical.pdf),
[arch documentation](https://arch.readthedocs.io/en/latest/univariate/univariate_volatility_modeling.html#student-s-t-errors).

### Our data: descriptive audit, without future evaluation outcomes

Using only labels settled before 2019 from the prior pinned input panel,
standardized return = terminal log return / (entry rv22 × sqrt(planned sessions)),
with the existing 0.001 volatility floor. Keep one nominal expiry per index/date
at each actual horizon, then separately take nonoverlapping windows greedily
(next quote strictly after prior settlement). These are descriptive moments,
not iid normality-test p-values. A Gaussian has excess kurtosis zero.

| Index / actual calendar days | Rows | Nonoverlapping rows | Skew | Excess kurtosis | Nonoverlapping excess kurtosis |
|---|---:|---:|---:|---:|---:|
| SPY / 1 | 601 | 594 | −1.684 | 9.442 | 9.456 |
| SPY / 30 | 474 | 101 | −1.078 | 3.032 | 1.198 |
| QQQ / 1 | 404 | 400 | −0.779 | 7.936 | 7.913 |
| QQQ / 30 | 326 | 76 | −0.557 | 0.850 | 0.127 |
| IWM / 1 | 483 | 478 | −0.947 | 3.313 | 3.269 |
| IWM / 30 | 372 | 101 | −1.168 | 3.497 | 2.906 |

The evidence supports a heavy-tail/asymmetry sensitivity experiment, not an
assumption that all conditional forecasts must have those pooled moments.
Conditioning on richer information can alter the shape. The small effective
long-horizon sample is also a warning against highly flexible tail estimation.
Pooling SPY, QQQ and IWM adds related tasks, not three independent market
histories. Contemporary common shocks and overlapping expiry windows remain.

### Distribution controls worth testing now

1. Keep Gaussian mixtures and empirical/direct-CDF/quantile controls.
2. Add Student-t mixtures as a targeted tail-family sensitivity, with fixed
   degrees of freedom greater than two. A single t is symmetric; mixtures can
   be asymmetric. Its scale is not its standard deviation: the latter is
   scale × sqrt(df/(df−2)). Saved artifacts must retain df.
3. Do not fit an unconstrained extreme-value tail merely to a handful of
   long-horizon extremes. Monotone spline flows are a credible later flexible
   family, but their base/tail choices also impose assumptions and do not
   remove the need for calibration and fresh evaluation.
[Student-t definition](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html),
[neural spline flows](https://arxiv.org/abs/1906.04032).

A crucial mathematical restriction: a literal Student-t **log return** has
no finite positive exponential moment. Its density decays polynomially,
so integrating exp(return) against its positive tail diverges. Unbounded
expected stock/call values are therefore unsuitable under that literal law.
An iron condor's terminal payoff is bounded; its expectation still exists and
must be computed by finite-interval CDF integrals, not subtraction of infinite
standalone call expectations. This conclusion is a direct derivation from the
linked density, not a trading claim. Gaussian mixtures remain a useful
finite-price-moment control.

The sqrt(session-count) divisor is currently a **reference normalization**,
not a hard assertion that future volatility follows square-root-time scaling:
the network also sees the exact horizon and can change scale/shape with it.
Nevertheless, standardized CRPS gives different observation weights than
raw-return or dollar-loss scoring. Report all three; do not change the winner's
metric after inspecting outcomes. Expanding training also assumes older data
remain useful; rolling-history and event features are plausible later studies,
not changes smuggled into this architecture comparison.

### Pooling and bounded tuning

Compare separate index models, a shared pooled model with index indicators,
and a pooled shared trunk with independently routed index output heads. Hard
parameter sharing can transfer useful structure but can also force different
tasks into incompatible representations. No pooling scheme is presumed better.
[Multi-task overview](https://arxiv.org/abs/1706.05098),
[learning sharing structure](https://arxiv.org/abs/1705.08142).

Vary network size/depth, component count, learning rate, regularization,
dropout, batch size, epochs and tail family in a declared small candidate set.
Use a fixed screening seed; refit finalists with both prior ensemble seeds,
never pick the luckiest seed. Fewer deliberate trials keep the experiment
tractable; random search is a standard alternative for a later wider space.
[HPO reference](https://jmlr.org/papers/v13/bergstra12a.html).

Screen only 2016–2018 forecasts whose outcomes settle before 2019. After this
extra cutoff purge there are 18,616 development rows: SPY 8,905, QQQ 4,852,
IWM 4,859. Freeze model settings before the already-inspected 2019–2025 research
evaluation. Do not call reused historical years an untouched test, or infer
skill from the maximum among many tuning scores. Evaluate paired dates,
index/exact-horizon cells, 30–45-day secondary results and tail calibration.
Report configuration counts, training/calibration/evaluation rows and dependent
date blocks. A new-data evaluation is still required after research selection.
[Selection bias](https://jmlr.org/papers/v11/cawley10a.html).

Use full CRPS as the unchanged primary objective. Threshold-weighted CRPS can
focus on predeclared relevant regions while retaining proper-score properties;
weights must not depend on the realized outcome or be chosen after seeing a
winner. Keep bounded-condor loss error as a separate economic diagnostic.
[Weighted scoring rules](https://www.jstatsoft.org/article/view/v110i08).

## Sources

Primary author publications and official implementation references are linked
beside claims. Local audit source: prior reviewed `input_panel.parquet`, SHA-256
`837e5c7696ea33004d1f606491b9ae4f5699cdeabd2a8f9237e271a3f5f5d368`, under
`/home/russell/dskit-feature-research-20260927/children/index_options/pipeline_runs/predictive_cdf_reviewed_20260928`.
Cont's PDF search index was accessible but direct retrieval returned 502; no
claim is made to have inspected its full text in this turn. HPO results belong
in the subsequent execution memo, not this research finding.
