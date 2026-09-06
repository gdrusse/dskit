# How the model zoo chooses one model for a forecast path

## TL;DR

For a stock approved at horizon `H`, every zoo candidate should predict every
lead `1..H` and show performance at each lead. We should still select the
deployable **path model as one object**, using a frozen multi-horizon score;
otherwise choosing the best visible model at every lead will overfit the same
validation evidence we are trying to trust.

## What question are we answering?

Suppose a stock is approved at `H = 5`. The final predictor must provide five
forecasts: one minute ahead, two minutes ahead, and so on through five minutes.
The concern is that model A may be strongest at one minute while model B is
strongest at five minutes. Which model should the zoo call the winner?

This explanation uses `k` for forecast lead and reserves `t` for the time at
which a forecast is made. Thus `k = 1, ..., H` means the complete future path
visible from origin `t`.

## Inputs and terms

- `asset`: one Gate-3-approved stock or ETF.
- `H`: that asset's approved terminal horizon.
- `k`: one lead in `1..H`.
- `origin`: the timestamp at which the model receives its information and makes
  all `H` forecasts.
- `candidate`: one complete zoo forecasting system, such as ridge, LightGBM,
  or a compact neural model.
- `actual(k)`: the realized project return label at lead `k`.
- `forecast(k)`: the candidate's prediction for `actual(k)`.
- `benchmark(k)`: the frozen no-information forecast at the same lead.
- `loss(k)`: the error assigned to one forecast. Squared error is appropriate
  when the point forecast targets the conditional mean.
- `weight(k)`: the importance assigned to lead `k`. All weights sum to one.

## What Gate 3 did—and did not—approve

A pass such as `MSTR:10` means MSTR passed Gate 3 at its selected terminal
horizon of ten minutes. It does not mean that Gate 3 separately tested and
approved MSTR at every lead from one through nine.

The zoo should now produce and measure those shorter-lead forecasts because the
MIO may need the full path. Those measurements are **new zoo evidence**. They
must not be relabeled as Gate-3 passes.

## What every candidate must produce

At each origin, a candidate should return one record with an ordered path:

```json
{
  "asset": "MSTR",
  "origin": "one forecast timestamp",
  "max_horizon": 10,
  "path": [
    {"lead": 1, "point": "forecast at k=1", "uncertainty": "..."},
    {"lead": 2, "point": "forecast at k=2", "uncertainty": "..."},
    {"lead": 3, "point": "forecast at k=3", "uncertainty": "..."},
    {"lead": "...", "point": "...", "uncertainty": "..."},
    {"lead": 10, "point": "forecast at k=10", "uncertainty": "..."}
  ]
}
```

The teaching strings above are not project output values. They show the required
shape only.

There are two sound ways to create the path:

1. **Direct heads:** train a distinct target or model for each `k`, then collect
   the `H` predictions.
2. **Joint multi-output:** train one model against the vector of all `H`
   outcomes.

The zoo should compare both where the architecture permits it. Ben Taieb,
Sorjamaa, and Bontempi explain that joint multiple-output models can preserve
relationships among future leads that independently fitted direct models may
miss. That is a reason to test joint heads, not proof that they will win on
intraday returns.

Training one model only at `H` cannot create the missing `1..H-1` targets.
Likewise, training only at `k=1` and reusing that same forecast at later leads
does not create honest direct forecasts for those leads.

## Measure every lead

For every outer walk-forward fold and every common forecast origin, save each
candidate's performance at every `k`. At minimum, the evidence should retain:

- benchmark-relative squared-error skill;
- information coefficient and calibration slope;
- the number of scored observations;
- uncertainty coverage, width, and a proper interval or distribution score;
- fold, origin, asset, candidate, and lead identifiers.

All candidates must use the same origins. An origin belongs in the primary
comparison only when the realized outcomes through `H` are available. This
prevents a short-lead model from receiving an easier or larger evaluation
sample than a long-lead model.

Raw error should not simply be added across leads. Longer-horizon returns
usually have a different scale, so their squared errors can dominate the total
even when every lead is intended to matter equally. Normalize each lead against
its own training-only benchmark scale, then freeze that normalization before
opening outer-fold results.

## A three-lead teaching example

Assume `H = 3`, and suppose benchmark-normalized losses have already been
computed. A value below `1.00` beats the benchmark; a value above `1.00` loses
to it. These numbers are invented for teaching and are not project results.

```text
Candidate A losses by lead:  0.70, 0.90, 1.30
Candidate B losses by lead:  0.90, 0.92, 0.95
```

With equal weights, candidate A's path score is:

```text
0.70 + 0.90 = 1.60
1.60 + 1.30 = 2.90
2.90 / 3 = 0.9667
```

Candidate B's path score is:

```text
0.90 + 0.92 = 1.82
1.82 + 0.95 = 2.77
2.77 / 3 = 0.9233
```

Lower loss is better:

```text
0.9233 < 0.9667
```

Candidate B therefore has the better equal-weight path score, even though
candidate A is better at lead one:

```text
0.70 < 0.90
```

Candidate A also falls below the benchmark at lead three:

```text
1.30 > 1.00
```

This arithmetic ranks the toy paths; it does **not** establish statistical
significance. Repeated outer-fold loss differences and dependence-aware tests
are needed for that.

## The three-part selection rule

### 1. Diagnose each horizon

Use a Horizon Confidence Set to report which models remain statistically
competitive at each lead. This answers whether relative model quality changes
across `1..H` while controlling the extra false discoveries caused by looking
at many horizons.

This output is diagnostic. It should not silently assemble a different winner
at every lead from the outer results.

### 2. Select the path-level superior set

Before viewing results, define one normalized multi-horizon loss:

```text
path loss at one origin
= weight(1) × normalized loss(1)
+ weight(2) × normalized loss(2)
+ ...
+ weight(H) × normalized loss(H)
```

If every horizon is equally important:

```text
weight(k) = 1 / H
```

Equal weighting is a judgmental default, not a research theorem. If the MIO has
a locked economic utility by horizon, its weights may replace equal weights—but
they must be declared before the outer results are opened.

Apply multi-horizon superior-predictive-ability and Model Confidence Set logic
to the series of path losses. The result may contain several statistically
indistinguishable models. That is useful honesty: weak data should not be forced
to name a unique winner.

### 3. Choose one deployable survivor

Among the statistically superior path models, use a frozen tie-break order:

```text
1. Greater stability across outer folds
2. Smaller worst-horizon regret
3. Lower model complexity
```

That exact order is a project judgment and remains not locked. It must be fixed
before the zoo results are inspected.

## Can different models win at different leads?

Yes, but a mixed path must itself become a candidate. The choice of model for
each lead must happen only inside the training and inner-validation data of
each outer fold. The completed mixed path is then evaluated once on untouched
outer data.

The unsafe sequence is:

```text
Open outer results
→ choose the best visible model independently at each lead
→ report the assembled path on those same outer results
```

That path has already seen its test. Its apparent performance includes the luck
of selecting among many model-by-horizon combinations.

The safe sequence is:

```text
Choose or tune each head inside the inner window
→ freeze the complete path
→ score that path on the outer fold
→ repeat through time
```

## How uncertainty changes the comparison

Marginal uncertainty must be measured at every lead. Coverage alone is not
enough because an extremely wide interval can cover nearly everything while
being useless. Use proper interval or distribution scores that reward both
calibration and sharpness.

The MIO also needs dependence across the path and across assets. Two candidates
can have similar one-lead interval scores but very different joint scenarios.
Energy scores evaluate a multivariate predictive distribution; variogram scores
are especially useful for detecting incorrect dependence among components.

The final model bundle should therefore preserve:

```text
Per-lead point forecasts and marginal uncertainty
+ synchronized joint residual or scenario paths
+ calibration metadata by asset, fold, origin, and lead
```

## Bottom line for P13

P13 should not run in its current one-terminal-lead form. Before execution, it
needs to materialize honest `1..H` targets, make a full path the unit of a zoo
candidate, preserve all per-lead evidence, and declare the path-level score and
tie-breaks. The new shorter-lead results expand model evaluation; they do not
rewrite what Gate 3 proved.

This is a **not-locked** design explanation. The underlying research is recorded
in [Full-path zoo selection across horizons](../research/post-gate3-predictor-output/2026-09-05-multi-horizon-selection.md).

## Research basis

- Ben Taieb, Sorjamaa, and Bontempi (2010), [multiple-output multi-step
  forecasting](https://doi.org/10.1016/j.neucom.2009.11.030).
- Capistran (2006), [multi-horizon comparison under a user-aligned multivariate
  loss](https://doi.org/10.1016/j.econlet.2006.04.010).
- Quaedvlieg (2021), [uniform and average multi-horizon superior predictive
  ability](https://doi.org/10.1080/07350015.2019.1620074).
- Fosten and Gutknecht (2021), [Horizon Confidence
  Sets](https://doi.org/10.1007/s00181-020-01891-7).
- Hansen, Lunde, and Nason (2011), [Model Confidence
  Sets](https://doi.org/10.3982/ECTA5771).
- Gneiting and Raftery (2007), [proper scoring
  rules](https://doi.org/10.1198/016214506000001437).
- Scheuerer and Hamill (2015), [variogram scoring for multivariate
  forecasts](https://doi.org/10.1175/MWR-D-14-00269.1).
