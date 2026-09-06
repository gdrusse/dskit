# Predictive-model strategy audit and implementation report

**Date:** 2026-09-05
**Decision:** use a post-Gate-3, JSON-declared model-zoo benchmark.
**Execution status:** code and configuration only. No candidate, fold, test,
backtest, or simulator was run after the owner's no-execution instruction.

## Verdict

The research logic is sound with one important refinement: the model zoo must
compare the architectures on the actual Gate-3-approved asset/horizon families,
not on the historical five-name P7 shortlist. The implemented P13 design now
does that. It is the best next strategy because it combines individualized
model construction with one frozen evidence contract.

The useful object is not a long registry of estimators. It is a reproducible
benchmark in which every candidate sees the same target definition, data and
cache identity, eligible asset/horizon pair, scored timestamps, outer folds,
embargo, primary score, lockbox, and multiplicity family. Feature selection,
architecture, regularization, search space, and seed policy remain specific to
the model family and are fit only inside training data.

## Research audit

The synthesis correctly recommends:

- strong tree and regularized-linear controls before larger networks;
- compact neural and sequence challengers at this signal-to-noise ratio;
- model-specific feature selection and hyperparameter spaces;
- nested, rolling-origin evaluation with an untouched downstream simulation;
- uncertainty as a separate layer over a promoted conditional-mean model.

The following remain hypotheses rather than design facts:

- pooled or shared heads may outperform asset-local heads, but P13 does not
  assume that result;
- a parameter-count range is a capacity prior, not a promotion rule;
- per-name horizons require explicit MIO holding-period semantics;
- failure to reject equal performance is not proof of equivalence. P13 labels
  its tie-break honestly as “no family-wise detectable deficit.”

## Official P13 population

P13 reads the immutable A18622 Gate-3 artifact and admits exactly its 25 passing
asset/horizon pairs, including the approved 1, 2, 3, 5, and 10-minute horizons.
It maps each asset to exactly one of the five P12 cache groups and refuses a
missing or duplicate assignment. The P12 source-document identity, Gate-3
artifact bytes, and memory/cache artifact bytes are pinned by SHA-256.

Each enabled template expands across all 25 pairs. The 13 enabled families are:

- Gate-3 LightGBM incumbent and a separately tuned LightGBM;
- ExtraTrees, RandomForest, and HistGradientBoosting;
- partial least squares, ridge, and elastic net;
- a small tabular MLP;
- NLinear, a small GRU, compact PatchTST, and compact transformer.

A pretrained time-series foundation model is explicitly outside this attempt
until a local, manifest-pinned snapshot has a disclosed pretraining cutoff and
verified no-overlap policy. It is not mislabeled as an attempted candidate.

## Feature selection and tuning

Every challenger has an empirical mechanism confined to the outer training window:

- tabular models jointly tune a train-fit univariate feature count with their
  family-specific model parameters;
- linear and MLP candidates standardize after selection inside the estimator
  pipeline;
- sequence candidates exclude static columns and tune a contiguous causal
  return-history context length;
- the Gate-3 LightGBM is deliberately unchanged as the incumbent control.

All tuning uses the existing purged inner holdout inside each outer training
fold. Inner scores choose features and hyperparameters only. The 20 outer fold
scores are the only candidate-comparison evidence.

## Comparison and failure controls

`BenchmarkPlan` validates every generated document, freezes its logical hash,
checks the complete shared contract, and emits a deterministic inventory hash.
The first invocation is no-launch by construction: `BenchmarkApproval` returns
an awaiting-review record, `BenchmarkRun` calls no candidate, and the comparison
records `no_launch=true`. Training becomes possible only after the exact
inventory hash and reviewer identity replace the two pending JSON values.

Once approved, `BenchmarkRun` rechecks document hashes and the calendar limit,
validates complete fold evidence, and writes an atomic per-candidate checkpoint.
A resumed run reuses only a successful candidate with the same document hash
and complete summary.

`BenchmarkCompare` requires every family on every approved pair and identical
ordered cutoffs. It uses paired fold differences, Newey-West standard errors,
and a two-sided global Bonferroni correction across all pairwise comparisons.
This replaced a finite bootstrap whose p-value resolution was too coarse for
the full comparison family. Per-pair output includes all fold scores, pairwise
tests, the observed-best comparison, and the predeclared simplest-candidate
tie-break. Family summaries use equal weight over the 25 approved pairs.

The declared compute rank is a pre-registered policy, not measured hardware
cost. P13 says this in provenance. Runtime, peak memory, and inference latency
should be added later only if the walk-forward result owns portable resource
fields.

## Locked calendar

`configs/program-calendar.json` remains the sole active date authority.
Gate 1, Gate 3, and P13 use the same 20 rolling-origin folds:
`first=2022-05-06`, `step_days=63`, `val_days=63`, `train_days=730`, and
`embargo_days=5`. The final development-validation interval is
`[2025-08-15, 2025-10-17)`.

Finalist-only HPO trains through 2025-11-30, embargoes 2025-12-01, and validates
from 2025-12-02 through 2026-02-28. March-May confirms the frozen mean model and
calibrates uncertainty. June-August is the first untouched full-system
simulation. The complete mean/uncertainty/Gate-2/MIO bundle freezes before
2026-06-01; production is ineligible before 2026-09-01 and owner approval.

## Relationship to the final MIO bundle

P13 chooses and documents candidate behavior; it is not the production model
publisher. The already-locked final-model contract must publish conditional
mean, calibrated probability or score-to-probability mapping, uncertainty in
expected alpha, realized-return predictive uncertainty, joint residual
scenarios/dependence, eligibility and horizon metadata, timestamps, units,
feature/model/calibration identities, and the capital-aware Gate-2 fields the
MIO consumes. P13 preserves the per-pair and prediction evidence needed to
design that final publisher without pretending the zoo artifact is the bundle.

## Research basis

Research-backed:

- nested selection prevents feature/HPO search from contaminating reported
  performance (Cawley & Talbot, 2010; Varma & Simon, 2006);
- ordered future-block evaluation is appropriate for dependent forecasting
  data (Racine, 2000; Bergmeir et al., 2018; Cerqueira et al., 2020);
- paired forecast comparison and dependence-robust standard errors follow the
  logic of Diebold-Mariano (1995), Giacomini-White (2006), and Newey-West
  (1987);
- explicit family-wise control addresses data snooping across many attempted
  strategies (White, 2000; Hansen, 2005);
- tree ensembles remain strong tabular baselines and carefully regularized MLPs
  can be competitive (Grinsztajn et al., 2022; Holzmüller et al., 2024).

Judgemental but predeclared:

- the exact 20/63/730/5 fold geometry and calendar boundaries;
- equal weight over the 25 approved asset/horizon pairs;
- the model families, search ranges, trial counts, ordinal compute ranks, and
  one-lag HAC setting;
- choosing the lowest-compute model whose deficit is not family-wise detectable
  is a conservative engineering heuristic, not an equivalence theorem.

Full calendar citations are in
`docs/research/predictive-program-calendar/2026-09-05-synthesis.md`. The source
research synthesis is
`docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md`.

## Independent review

The initial skeptical review issued a no-launch verdict on the earlier P7-based
configuration. Its Critical and Major concerns drove the post-Gate-3 population,
missing-family coverage, empirical selectors, complete contract, plan-review
barrier, multiplicity logic, and checkpoint machinery now implemented.

A Terra Bugbot loop then reviewed the latest code and configuration. It found
and prompted fixes for the combined Gate-3 row schema/filter order, the
Bonferroni p-value resolution/zero edge, and the checkpoint-to-summary crash
window. Its final re-review reported no remaining Critical or Major findings.
It ran no tests or pipelines.

## Readiness

The code, JSON, contracts, approval barrier, checkpointing, and static tests are
present. P13 remains intentionally set to `PENDING-PLAN-REVIEW`. No execution
has been authorized or performed in this implementation pass.
