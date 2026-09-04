# R2 — Monitoring ML models and automated decisions in production

Research input for the design of `dskit.production`. Domain-neutral: nothing
below names a venue, an asset class, or a project. Where a number is quoted
it is a *tool default* or a *paper result*, offered as a calibration point for
config bounds — never as a value to hardcode.

---

## 1. Findings by theme

### 1.1 The canon: Rules of ML, ML Test Score, Hidden Technical Debt

**Rules of ML (Google).** The production rules reduce to four
mechanisms. *Log what you served* — Rule #29: "save the set of features used
at serving time, and then pipe those features to a log to use them at
training time." *One code path* — Rule #32: re-use code between training and
serving; "this eliminates a source of training-serving skew." *Measure skew
as three discrepancies* — Rule #37: training-vs-holdout ("will always exist,
not always bad"), holdout-vs-next-day ("tune regularization to maximize
next-day performance"), next-day-vs-live ("the same example at serving …
should give exactly the same result … a discrepancy here probably indicates
an engineering error"). *Sanity-check before export* — Rule #9: teams
"check the AUC before exporting"; Rule #10 warns that the commonest failure
is *silent* — a joined table stops updating and quality "decays gradually";
the remedy is tracking data statistics plus periodic manual inspection.
Rule #8 asks how much performance degrades with a model a day / week old,
which sets the staleness alarm; Rule #34 keeps a randomly held-out slice of
traffic un-acted-on so labels stay unbiased.

**The ML Test Score (Breck et al. 2017).** 28 tests in four sections
(Data, Model, Infrastructure, Monitoring), scored 0 / 1 (manual) / 2
(automated) per test, with the overall score the *minimum* across sections.
The seven monitoring tests, verbatim headings: (1) dependency changes
result in notification; (2) data invariants hold in training and serving
inputs — "careful tuning of alerting thresholds is needed"; (3) training and
serving features compute the same values — "log a sample of actual serving
traffic … adding identifiers to each example at serving time will allow
direct comparison; the feature values should be perfectly identical"; the
metrics are "the number of features that exhibit skew, and the number of
examples exhibiting skew for each skewed feature"; (4) models are not too
stale — monitor model age *and* the age of every upstream aggregate; (5)
numerically stable — first NaN/Inf, weight bounds; (6) no dramatic or
slow-leak regression in latency/throughput/RAM, sliced by data and model
version; (7) no regression in prediction quality on served data — "measure
statistical bias in predictions … 90% of predictions of probability 0.9
should in fact be positive", use labels when they arrive soon, otherwise
rater-labelled samples; alert on both "dramatic and slow-leak regressions".
Infrastructure tests add the release half: Infra 4 (an automated system must
"bless the model or veto it" — loose thresholds against a validation set for
slow decay, tighter thresholds against the previous model's predictions for
sudden drops), Infra 6 (canary: load into the serving binary, then "turn up
new models gradually, running old and new concurrently"), Infra 7 (rollback
of the *model*, rehearsed "normally, when not in emergency conditions").

**Hidden Technical Debt (Sculley et al. 2015).** The monitoring section
gives three invariants. *Prediction bias*: "the distribution of predicted
labels is equal to the distribution of observed labels … can be met by a null
model … however a surprisingly useful diagnostic"; slice it and alert on it.
*Action limits*: "in systems that are used to take actions in the real
world … set and enforce action limits as a sanity check … broad enough not to
trigger spuriously"; hitting one fires an alert and manual review. *Upstream
producers* must meet an SLO and propagate alerts "to the control plane of an
ML system". Feedback loops: *direct* (a model selects its own future training
data; mitigate with randomization or by isolating data from the model's
influence) and *hidden* (two systems couple through the world — their example
is two market-prediction models influencing each other's bidding). Both
"are all more difficult to detect … if they occur gradually".

### 1.2 Drift monitoring: statistics, windows, thresholds, streams

**Vocabulary.** Klaise et al. (2020) formalize covariate shift (P(X)
changes, P(Y|X) fixed) and label shift (P(Y) changes, P(X|Y) fixed); concept
drift is a change in P(Y|X). Training/serving *skew* (training vs serving)
is distinguished from *drift* (serving over time) by both TFDV and Vertex AI
(skew comparator vs drift comparator, baseline-from-training vs
window-over-window).

**Two-sample comparisons — what the tools do.** Evidently's default
algorithm switches on reference size: ≤1000 rows → hypothesis tests (KS for
numeric with >5 unique values, chi-square for categorical/≤5 unique, z-test
for binary; p < 0.05); >1000 rows → distances (Wasserstein normed for
numeric, Jensen–Shannon for categorical; threshold 0.1); dataset-level drift
when ≥50% of columns drift. Their benchmark shows why: at 100k rows KS
"detected even 0.5% shifts consistently", so "the test can be 'too
sensitive' … just because you have a lot of data". Vertex AI computes the
baseline from the training set, samples serving requests (e.g. 20%),
evaluates every window (default 24 h, minimum >1 h), uses JS for numeric and
L∞ for categorical with a per-feature threshold (default 0.3). TFDV uses
the same two distances with per-feature `skew_comparator` /
`drift_comparator` thresholds (docs example 0.01). whylogs answers the
"how do I store a reference" question: per-column *mergeable* profiles
(counts, min/max, quantile sketches, frequent items, HLL cardinality), so
profiles of any granularity can be merged and compared to a historical
baseline. Arize's playbook enumerates baseline choices — training set,
validation set, pre-production, a prior period, a moving window — and
recommends cohort/segment analysis because aggregates hide slices.

**PSI has a distribution; the 0.10/0.25 rule does not.** Yurdakul & Naranjo
(2020) prove `(1/n + 1/m)^-1 · PSI` is asymptotically χ² with B−1 degrees of
freedom (n, m = reference/current sizes, B = bins). "When B=10, the
rule-of-thumb PSI > 0.25 seems reasonable for sample sizes n and m between
100 and 200, but it is too conservative for larger sample sizes." They
recommend `PSI > (1/n+1/m)·χ²_{α,B−1}` or the normal approximation
`PSI > (1/n+1/m)·(B−1 + z_α·sqrt(2(B−1)))`. The second form needs only
`statistics.NormalDist().inv_cdf` — stdlib.

**KS.** `D = sup|F_m − G_n|`; for large m, n, `sqrt(mn/(m+n))·D` follows the
Kolmogorov distribution `K(x) = 1 − 2 Σ_{j≥1} (−1)^{j−1} exp(−2 j² x²)`
(scipy's asymptotic mode; convergence is slow and exact tables are preferred
below ~50 per sample). The statistic is a merge-sort pass and the p-value
is a short exponential series — both stdlib.

**Streaming detectors (River defaults).** *DDM* watches a binary error
stream: `p_i`, `s_i = sqrt(p_i(1−p_i)/i)`; warning when
`p_i + s_i ≥ p_min + 2·s_min`, drift at `3·s_min` (≈95%/99%), after 30
warm-up instances; resets after drift. *Page-Hinkley* is a CUSUM:
accumulate `(x_t − x̄_t − δ)`, alarm when the sum exceeds its running
minimum by λ (δ=0.005, λ=50, forgetting α=0.9999, min 30). *ADWIN* keeps a
variable-length window in exponential-histogram buckets and cuts where two
sub-window means differ beyond a Hoeffding-style bound (δ=0.002, check every
32 items, ≥5 items per sub-window). The forecasting literature's equivalents
are Trigg's smoothed-error tracking signal `T_t = |E_t / M_t|` with
`E_t = βe_t + (1−β)E_{t−1}`, `M_t = β|e_t| + (1−β)M_{t−1}` (β=0.1, alarm >
0.51) and the cumulative-error/MAD signal (alarm |TS| > 4); Gardner's
simulations find CUSUM "robust to the choice of forecasting parameter" and
"more responsive to small changes". All are per-observation, constant
memory, stdlib.

**Calibrating the false-alarm rate instead of the threshold.** Alibi
Detect's online detectors are configured by *expected run time* under no
change: thresholds are simulated from the reference set so that the detector
fires, on average, once per ERT ticks with a constant per-step false-positive
rate. Klaise et al. note thresholds "require domain knowledge and can be
difficult to set appropriately to limit the number of false alarms" and that
"change-point detection may prove to be more robust".

### 1.3 Label delay, outcome joining, label-free estimation

Label delay is the norm, not the edge case (fraud 30–90 days, churn 30–60,
default 12–36 months per the Datadog guide; Chapelle 2014 models ad
conversions arriving up to a month late with an exponential delay model).
Arize's playbook splits the world into four regimes — real-time ground
truth (score directly), delayed (proxies now, real metrics on arrival),
*biased* (only accepted decisions get outcomes → keep a holdout that follows
a different decision rule), none (annotate samples; drift as proxy).
Evidently ships a `NoTargetPerformance` preset for exactly the "predict in
batches, labels later" case. Best practice from several guides: two loops
(fast proxy loop, slow label loop), a *label-freshness* view (fraction of
recent decisions that have outcomes, age of newest labelled data), and being
"explicit about the distinction between what's known and what's inferred".

NannyML's label-free estimators are the state of the art. **CBPE** (binary
and multiclass classification): calibrate probabilities on reference data
(isotonic, adopted only if it lowers ECE across stratified folds), then per
prediction treat `1 − |ŷ − p̂|` as expected correctness and sum expected
TP/TN/FP/FN to get expected accuracy, precision, recall, ROC-AUC (sweeping
thresholds). **DLE** (regression): a "nanny" model (LightGBM) predicts the
per-row absolute/squared error from features *and* the child's prediction;
mean of its predictions estimates MAE/RMSE. Both state the same limits:
"CBPE will not work under concept drift, i.e. when P(Y|X) changes", no
extrapolation to unseen regions, and sample size matters. A 2026 arXiv paper
on "evidence sufficiency under delayed ground truth" confirms the
theoretical gap: concept drift without feature change "remains theoretically
undetectable without labels", while covariate/mixed drift is detectable by
proxies. NannyML's windowing rules: minimum chunk ≈300 observations (the
size at which sampling-only std of the metric falls below 0.02), at least 6
reference chunks, thresholds = reference mean ± 3·std, clipped to the
metric's theoretical bounds.

### 1.4 Calibration and skill, online

ECE (Guo et al. 2017): M equal-width confidence bins,
`ECE = Σ_m (|B_m|/n)·|acc(B_m) − conf(B_m)|`; MCE is the max gap;
a reliability diagram is the same table plotted. Murphy's decomposition,
`Brier = Reliability − Resolution + Uncertainty`, separates calibration
(REL) from discrimination (RES) with UNC "the Brier score of the
climatological forecast" — a built-in baseline. Stephenson et al. show the
three-term identity holds only when stratifying on every issued probability;
binned versions need two extra within-bin terms, so a monitor should report
the binned REL/RES *and* the raw Brier. Skill vs a reference is
`BSS = 1 − BS/BS_ref` with the reference being climatology (base rate),
persistence, or — for a decision system — whatever baseline forecast the
config declares (mean, prior tick, a market-implied probability supplied as
an input). Bias in *point* forecasts is the tracking-signal family above;
bias in *probabilities* is the prediction-bias invariant (mean p̂ vs mean y,
by slice). Guides converge on computing all of these per slice, because "a
single slice often signals an upstream feature pipeline issue … that the
global metric averages out".

### 1.5 Release practices and promotion gates

The pattern across CD4ML, Google's MLOps CD guide, MLflow and the canary
guides is a ladder with evidence at each rung:

1. **Offline validation** — compare to "the current model, for example,
   production model, baseline model"; check "the performance of the model is
   consistent on various segments"; verify serving-environment compatibility
   (Google). ML Test Score Infra 4: loose thresholds vs validation for slow
   decay, tight thresholds vs the previous model for sudden drops.
2. **Shadow** — "run the new version in parallel, log its outputs, and
   compare them against the champion before making any switch"; needs
   traffic mirroring, a separate endpoint, a logging pipeline, side-by-side
   dashboards. Compared: agreement rate, prediction-distribution alignment,
   latency.
3. **Canary / online validation** — 1–10% of traffic; "if the canary passes
   all system SLOs, automatically promote … if any metric regresses, trigger
   an immediate rollback." Google: "online model validation—in a canary
   deployment or an A/B testing setup—before it serves prediction for the
   online traffic."
4. **Champion/challenger via registry aliases** — MLflow deprecated stages
   (2.9) for aliases: `champion` names the version serving traffic,
   `challenger` the candidate; promotion is an alias reassignment "decoupled
   from your production code"; rollback is the reverse assignment, the old
   version retained. ML Test Score: rehearse rollback.
5. **Retraining triggers** (Google): on demand, on schedule, on new data, on
   performance degradation, on drift — each a monitor verdict.

The Shankar interview study reports what practitioners actually keep:
old versions for fast rollback, proxies while labels lag, threshold alerts
with on-call; and names the pains — alert fatigue, silent failures, unclear
retraining cadence, version sprawl. The multivocal review (136 papers) lists
the same three unsolved problems: label delay, threshold setting, alert
fatigue.

### 1.6 Training/serving parity verification

Three mechanisms, layered: *structural* (one code path, Rule #32; point-in-
time-correct joins so training only sees feature values whose event — and
optionally *created* — timestamp precedes the decision, Feast), *replay*
(log serving inputs with example ids and re-run them through the offline
path; values "should be perfectly identical", count skewed features and
skewed examples per feature — ML Test Score Monitor 3), and *statistical*
(compare training-baseline vs serving-window distributions per feature —
Vertex/TFDV; catches pipeline divergence and world change but not silent
semantic drift such as `age_days` computed from `event_time` in batch and
`request_time` online). Rule #37's three-tier decomposition tells you which
layer is at fault: next-day-vs-live discrepancy is engineering, the other
two are statistics.

### 1.7 What a decision-monitoring dashboard tracks

Evidently's pyramid: system health (request volume, RPS, error rate, p50/
p75/p90/p99 latency — LinkedIn monitors mean and those four percentiles),
data quality (missing rates, schema, ranges, freshness), drift (inputs and
predictions), model quality (with labels: task metrics by segment; without:
prediction drift, outlier rate), business KPIs. SRE's four golden signals
(latency, traffic, errors, saturation), with the rule to avoid means — "1%
of requests might take 5 seconds" behind a 100 ms average — and to alert
only on what is "urgent, actionable, and actively or imminently
user-visible". For a *decision* system add the selective-prediction pair:
coverage `φ = E[g(x)]` (fraction decided) and selective risk
`R = E[ℓ·g]/φ` (loss among decided), plus the risk–coverage curve/AURC;
abstention rate is `1 − φ`. Hidden-Debt action limits (count of
limit hits) and ML Test Score staleness (model age, upstream data age) round
out the set. Klaise et al. flag the engineering constraint: label-joined
metrics are stateful and need "synchronization across multiple instances,
and appropriate time windows".

---

## 2. Design implications for `dskit.production`

### 2.1 Seams — abstract bases with small hooks

```python
class Monitor(ABC):                    # tier-1, stdlib
    _PARAMS = ("window", "min_n", "threshold", "slices")   # default-deny
    def fit(self, reference):  ...     # derive bins/baselines/thresholds from reference records
    @abstractmethod
    def observe(self, record): ...     # one DecisionRecord or OutcomeRecord; O(1) state update
    @abstractmethod
    def verdict(self): ...             # -> Verdict(status, statistic, threshold, n_ref, n_cur, window, slice)
    def state(self) / restore(state)   # JSON-able, so a loop resumes across ticks
```

`Verdict.status ∈ {ok, warn, alarm, insufficient}` — `insufficient` is a
first-class result (NannyML's NaN-precision lesson): a monitor below
`min_n` never reports `ok`. Concrete families, each a subclass, never a
`kind` branch:

- `DistributionMonitor` — reference vs current; takes a `Distance` strategy
  object (`fit(ref_values)`, `score(cur_values)`): `PSI`, `KS`,
  `JensenShannon`, `ChiSquare`, `LInfCategorical`, `ProportionZ`. Bins come
  from reference quantiles at `fit`, never from current data.
- `StreamMonitor` — sequential, per observation: `DDM` (binary error),
  `PageHinkley`/`CUSUM` (signed residual), `ADWIN` (mean shift),
  `TrackingSignal` (Trigg). These are the cheap, always-on layer.
- `OutcomeMonitor` — needs joined outcomes: `Calibration` (ECE/MCE +
  reliability bins), `BrierDecomposition` (REL/RES/UNC + raw Brier),
  `Skill` (BSS and Diebold–Mariano against a declared baseline — reuse the
  existing pipeline metric registry and DM test), `PredictionBias`
  (mean p̂ − mean y, by slice).
- `LabelFreeMonitor` — `ExpectedMetric` (CBPE arithmetic over calibrated
  probabilities; the calibrator is a strategy object, stdlib isotonic-by-
  bins in core, sklearn isotonic in a pack). DLE is a pack (needs a model).
- `OperationalMonitor` — `DecisionRate`, `Coverage`/`Abstention`,
  `LatencyPercentiles`, `ErrorCount`/`RefusalCount`, `Staleness` (model age
  from the run dir, data age from the newest snapshot), `ActionLimit`.
- `ParityMonitor` — replays logged decision inputs through the *same node
  objects* and counts skewed features / skewed examples per feature, with a
  numeric tolerance.

Companion seams: `Reference` (where the baseline comes from), `Chunker`
(how current is windowed), `Threshold` (how a statistic becomes a status),
`Response` (what a status does: `log | warn | halt | fallback | rollback`),
`Gate` (evidence predicate over MonitorRecords that permits a stage
transition), and `Executor` (already an owner constraint — the loop never
learns a venue). A `Profile` value object (per-field counts, min/max,
sum/sumsq, missing count, quantile bins, top-k categories) with `merge()`
gives mergeable references, whylogs-style, in pure stdlib.

### 2.2 Reference / current window semantics

- `Reference` variants: `RunReference` (the training run's validation fold,
  read from the run dir — never restated), `SnapshotReference` (an
  onboarding snapshot id — immutable, content-addressed via `assets`),
  `LeadingReference` (first N live ticks), `RollingReference` (prior
  period). Always record *both* a fixed anchor and a rolling one: the fixed
  anchor catches slow leaks, the rolling one catches sudden breaks; a
  rolling-only reference silently re-baselines on a degraded model.
- `Chunker` variants: `CountChunk(n)`, `PeriodChunk(iso duration)`,
  `SlidingWindow(n, step)`. Rule: the last incomplete chunk reports
  `insufficient`, never `ok`.
- Verdicts are keyed by `(monitor_id, slice, window_start, window_end)` and
  are re-emitted when late outcomes land — an `OutcomeMonitor` verdict for a
  window is *provisional* until `label_coverage ≥ min_label_coverage`.

### 2.3 Config knobs (types / bounds)

| knob | type | bound / default source |
|---|---|---|
| `window.kind` | enum count/period/sliding | — |
| `window.n` / `window.period` | int ≥ 1 / ISO-8601 | NannyML ≈300 rows minimum for metric chunks |
| `min_n`, `min_ref_chunks` | int | 300 / 6 (NannyML) |
| `threshold.kind` | enum constant/reference_std/alpha/ert | — |
| `threshold.value` | float | PSI 0.1–0.25, JS 0.1, L∞ 0.01–0.3, Wasserstein 0.1 σ (tool defaults) |
| `threshold.k_std` | float > 0 | 3 (NannyML) |
| `threshold.alpha` | 0 < α < 1 | 0.05; PSI benchmark then derived from (n, m, B) |
| `threshold.ert` | int ticks | Alibi-style expected run time under no drift |
| `bins` | int 2–50 | 10 (PSI convention); equal-width for ECE, quantile for PSI |
| `epsilon` | float > 0 | smoothing for empty PSI/JS bins |
| `warn_level`, `drift_level` | float | 2 / 3 (DDM) |
| `delta`, `lambda`, `alpha` | floats | 0.005 / 50 / 0.9999 (Page-Hinkley); 0.002 (ADWIN) |
| `beta`, `limit` | float | 0.1 / 0.51 (Trigg) |
| `label_delay`, `min_label_coverage` | ISO duration / 0–1 | from the project's outcome horizon |
| `baseline` | enum mean/persistence/input:<port> | for BSS/DM skill |
| `slices` | list of field names | Monitor 7 / prediction-bias slicing |
| `action_limits` | {field: [lo, hi]} | Hidden Debt; broad enough not to trigger spuriously |
| `sample_rate` | 0–1 | Vertex-style logging fraction |
| `response` | enum log/warn/halt/fallback/rollback | halt ⇒ exit code 3, "a halt is a result" |
| `gate.require` | list of {monitor, status, min_windows} | evidence to promote shadow→paper→live |

All literals above are *sources for bounds*, to live in one named default
per knob, validated once (the `validate_params`/`run` duplication rule).

### 2.4 Records to persist (all JSON lines in the run dir, hash-sidecar'd)

- `DecisionRecord` (one per tick, already required): decision id, tick
  timestamp, model/run hash, inputs used (or their hash + sampled full copy),
  prediction/probability, decision, abstain flag + reason, baseline forecast,
  latency ms, action-limit hits, stage (`shadow|paper|live`), executor ack.
- `OutcomeRecord`: decision id, outcome value, `observed_at`, source
  snapshot id — joined by id, never by time proximity.
- `MonitorRecord`: monitor id, config hash, reference id, slice, window
  bounds, n_ref, n_cur, label_coverage, statistic, threshold, status,
  provisional flag.
- `ProfileRecord`: the mergeable per-window profile (so a reference can be
  rebuilt or merged later).
- `ReleaseRecord`: stage transition, from/to version, evidence (the
  MonitorRecords that satisfied the gate), actor — written through
  `dskit.journal` so promotions are ledger actions.
- `LabelFreshness`: per window, fraction of decisions with outcomes by age
  bucket.

### 2.5 Tier placement

Tier-1 (stdlib): PSI, KS statistic + asymptotic p, JS/L∞/chi-square
statistics on binned or categorical data, proportion z-test
(`statistics.NormalDist`), 1-D Wasserstein (sorted-sample mean |F−G|), ECE/
MCE, Brier + Murphy terms, BSS, tracking signals, CUSUM/Page-Hinkley, DDM,
ADWIN (two-window simplification acceptable), CBPE expected-confusion
arithmetic, quantile-bin isotonic calibrator, latency percentiles
(`statistics.quantiles`), mergeable profiles, PSI χ²/normal benchmark
(normal form is stdlib; χ² quantile via `math.lgamma` series if wanted).
Tier-2 packs: `libs/scipy.py` (exact KS p, permutation tests, chi-square
quantiles), `libs/sklearn.py` (isotonic/Platt calibrators), `libs/numpy.py`
(MMD / vectorized ERT bootstrap), DLE via the existing model packs; plots
via the matplotlib pack; metric sinks via the mlflow pack. Tier-3: the
project's slices, outcome horizon, baseline port, action limits — config
only.

### 2.6 Tests to write

Purity gate (core imports nothing heavy); default-deny params; identity-hash
stability with `notes` stripped. Statistic pins: PSI = 0 on identical
samples, symmetric under swap, and `(1/n+1/m)^-1·PSI` on same-distribution
draws has mean ≈ B−1 (seeded `random`); KS against a hand-computed 5×5
case; ECE = 0 for a synthetic perfectly-calibrated stream; Murphy terms sum
to Brier exactly when stratified on issued values; tracking signal ≈ 0 on
unbiased noise and > limit on a step bias; DDM warns at 2σ and alarms at 3σ
on a constructed error stream; Page-Hinkley alarms on a mean shift and not on
stationary noise. Window semantics: `insufficient` below `min_n`; last
partial chunk never `ok`; provisional-until-coverage. State round-trip:
`restore(state())` then identical verdicts. Determinism: same record stream
→ byte-identical MonitorRecords. Parity golden test: replaying a run's own
decision records through the same nodes reproduces decisions bit-for-bit
(this *is* Rule #37's third tier). Gate evaluation: promotion refused when
any required monitor is `insufficient`. Exit code 3 on `halt`. A
"lookback-agrees-everywhere"-style pin that the serving loop's knobs equal
the run dir's.

---

## 3. Pitfalls and anti-patterns

- **p-values at scale.** KS/chi-square fire continuously on large windows;
  switch to distances above ~1000 rows or subsample (Evidently).
- **Fixed PSI thresholds.** 0.10/0.25 ignore n, m, B; derive the benchmark
  from sizes and bins (Yurdakul & Naranjo).
- **Rolling-only references** re-baseline onto a degraded model; keep a
  fixed anchor too.
- **Label-free estimators under concept drift** are blind by construction
  (NannyML, evidence-sufficiency paper) — label them `estimated`, never
  `measured`, and expire them when real labels arrive.
- **Prediction bias passes for a null model**; it is a canary, not a
  quality metric.
- **Drift ≠ degradation**: alerting on every drifted feature is the alert
  fatigue every survey reports; page only on symptoms (quality, coverage,
  errors), log causes.
- **Biased outcomes**: only acted-on decisions get labels; keep a held-out
  or randomized slice (Rule #34, Hidden Debt) and record its share.
- **Feedback loops** that build gradually; journal every action so the
  loop is auditable, and never train on the loop's own selected data without
  the holdout.
- **Averages hide tails** — persist percentiles.
- **Coverage collapse looks like low risk**: report coverage beside
  selective risk, never risk alone.
- **Restating training knobs in serving** — read the run dir; pin with a
  test.
- **Unpaired challenger comparisons** — DM/BSS need the same targets on the
  same ticks; and multiple challengers inflate false promotions (the
  many-attempts bar already in dskit applies to release, not only research).
- **Rollback that reverts code but not the model** (ML Test Score Infra 7);
  rehearse the model rollback.
- **Silent upstream staleness** (Rule #10): monitor data age, not just data
  values.
- **Thresholds sourced from the thing they validate** — the reference used
  to fit a threshold must be declared independently of the window it judges.

---

## 4. Sources

- Rules of ML — https://developers.google.com/machine-learning/guides/rules-of-ml
- Production ML systems: monitoring (Crash Course) — https://developers.google.com/machine-learning/crash-course/production-ml-systems/monitoring
- The ML Test Score (Breck et al. 2017) — https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/ (PDF: https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/aad9f93b86b7addfea4c419b9100c6cdd26cacea.pdf)
- Hidden Technical Debt in ML Systems (Sculley et al. 2015) — https://proceedings.neurips.cc/paper/2015/file/86df7dcfd896fcaf2674f757a2463eba-Paper.pdf
- Evidently: data drift algorithm — https://docs-old.evidentlyai.com/reference/data-drift-algorithm ; drift parameters — https://docs-old.evidentlyai.com/user-guide/customization/options-for-statistical-tests ; large-dataset benchmark — https://www.evidentlyai.com/blog/data-drift-detection-large-datasets ; monitoring metrics — https://www.evidentlyai.com/blog/ml-monitoring-metrics ; NoTargetPerformance — https://www.evidentlyai.com/blog/feature-spotlight-notargetperformance-test-preset
- NannyML: performance estimation (CBPE/DLE) — https://nannyml.readthedocs.io/en/v0.13.1/how_it_works/performance_estimation.html ; chunking — https://nannyml.readthedocs.io/en/stable/how_it_works/chunking_data.html ; minimum chunk size — https://nannyml.readthedocs.io/en/v0.3.1/deep_dive/minimum_chunk_size.html ; thresholds — https://nannyml.readthedocs.io/en/stable/how_it_works/thresholds.html
- Arize: monitoring playbook — https://arize.com/blog/monitor-your-model-in-production/ ; PSI — https://arize.com/blog/population-stability-index-psi/
- whylogs — https://github.com/whylabs/whylogs ; merging profiles — https://whylogs.readthedocs.io/en/latest/examples/basic/Merging_Profiles.html
- Vertex AI skew monitoring — https://cloud.google.com/blog/topics/developers-practitioners/monitor-models-training-serving-skew-vertex-ai
- TFDV skew/drift comparators — https://www.tensorflow.org/tfx/data_validation/get_started
- Feast point-in-time joins — https://docs.feast.dev/getting-started/concepts/point-in-time-joins
- Yurdakul & Naranjo, Statistical properties of the PSI (J. Risk Model Validation 2020) — https://files.wmich.edu/s3fs-public/attachments/u730/2022/PSIfinal.pdf
- KS two-sample — https://www.math.ucla.edu/~tom/distributions/KolSmir2.html ; scipy ks_2samp — https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ks_2samp.html
- River: ADWIN — https://riverml.xyz/latest/api/drift/ADWIN/ ; DDM — https://riverml.xyz/latest/api/drift/binary/DDM/ ; Page-Hinkley — https://riverml.xyz/latest/api/drift/PageHinkley/
- Cobb et al., calibrated sequential change detection (Alibi Detect ERT) — https://arxiv.org/abs/2108.00883
- Klaise et al., Monitoring and explainability of models in production — https://arxiv.org/abs/2007.06299
- Monitoring ML Systems: a multivocal literature review — https://arxiv.org/abs/2509.14294
- Shankar et al., Operationalizing ML: an interview study — https://arxiv.org/abs/2209.09125
- Evidence sufficiency under delayed ground truth — https://arxiv.org/abs/2604.15740
- Chapelle, Modeling delayed feedback in display advertising (KDD 2014) — https://dl.acm.org/doi/10.1145/2623330.2623634
- Datadog, ML monitoring best practices — https://www.datadoghq.com/blog/ml-model-monitoring-in-production-best-practices/
- Guo et al., On calibration of modern neural networks (ECE) — https://arxiv.org/abs/1706.04599
- Siegert, Simplifying and generalising Murphy's Brier score decomposition — https://rmets.onlinelibrary.wiley.com/doi/10.1002/qj.2985 ; Stephenson et al., two extra components — https://journals.ametsoc.org/view/journals/wefo/23/4/2007waf2006116_1.xml
- DWD, Brier skill score — https://www.dwd.de/EN/ourservices/seasonals_forecasts/forecast_reliability.html
- Tracking signal (Trigg) — https://en.wikipedia.org/wiki/Tracking_signal ; Gardner, CUSUM vs smoothed-error — https://www.bauer.uh.edu/egardner/3301H%20Operations%20Management/ESG%20Publications/1985%20Cusum%20vs.%20smoothed%20error.pdf
- Geifman & El-Yaniv, Selective classification — https://arxiv.org/abs/1705.08500
- CD4ML (Sato, Wider, Windheuser) — https://martinfowler.com/articles/cd4ml.html
- Google, MLOps continuous delivery and automation — https://docs.cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning
- MLflow model registry (aliases) — https://mlflow.org/docs/latest/ml/model-registry/
- Shadow deployment patterns — https://atlan.com/know/shadow-deployment-for-ml-models/ ; canary/shadow guide — https://resiliotech.com/blog/model-canary-releases-shadow-traffic-guide
- Google SRE, Monitoring distributed systems — https://sre.google/sre-book/monitoring-distributed-systems/
