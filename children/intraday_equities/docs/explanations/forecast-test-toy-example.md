# Forecast-skill test: a worked toy example

## TL;DR

This test asks whether a forecasting model repeatedly predicts future stock
moves more accurately than simply guessing the training-period average. The
current evidence supports predicting Lilly three minutes ahead, but only at a
one-in-20 luck level from full retraining; the five-stock group's two-minute
result still needs that full-retraining check.

## What concern does this method address?

It checks whether a forecasting model repeatedly beats a simple average guess,
while allowing neighboring forecast errors to be related rather than pretending
that every row is an independent piece of evidence.

## The question

Suppose a model predicts a stock's future price change. We compare it with a
baseline that always predicts the average future change in the training data.

The test asks: **is the model closer to the realized changes often enough, and by
enough, that ordinary variation is an implausible explanation?**

## Step 1: make four predictions

Assume the training-set average future change is `0`. Therefore, the baseline
predicts `0` every time.

For four later observations:

```text
                  Row 1   Row 2   Row 3   Row 4
Actual change       2      -1       1      -2
Model prediction   1.5    -0.5     0.5    -1.5
Baseline prediction 0       0       0       0
```

The numbers are deliberately large and tidy so the arithmetic is visible. Real
stock returns are much smaller.

## Step 2: calculate squared errors

An error is `actual - prediction`. We square it so positive and negative misses
do not cancel each other.

For row 1:

```text
Baseline error         = 2 - 0    = 2
Baseline squared error = 2²       = 4

Model error            = 2 - 1.5  = 0.5
Model squared error    = 0.5²     = 0.25
```

Doing the same for every row gives:

```text
Baseline squared errors: 4,    1,    1,    4
Model squared errors:    0.25, 0.25, 0.25, 0.25
```

## Step 3: measure the model's win on each row

For each row:

```text
row score = baseline squared error - model squared error
```

Therefore:

```text
Row 1: 4 - 0.25 = 3.75
Row 2: 1 - 0.25 = 0.75
Row 3: 1 - 0.25 = 0.75
Row 4: 4 - 0.25 = 3.75
```

Positive means the model won that row. Negative means the average guess was
closer.

## Step 4: put volatile and quiet periods on a comparable scale

Calculate the baseline's average squared error:

```text
q = (4 + 1 + 1 + 4) / 4
  = 10 / 4
  = 2.5
```

Divide every row score by `q`:

```text
3.75 / 2.5 = 1.5
0.75 / 2.5 = 0.3
0.75 / 2.5 = 0.3
3.75 / 2.5 = 1.5
```

The normalized score sequence is `1.5, 0.3, 0.3, 1.5`. Its average is:

```text
(1.5 + 0.3 + 0.3 + 1.5) / 4 = 3.6 / 4 = 0.9
```

In this exaggerated example, the model removed 90% of the baseline's average
squared error.

## Step 5: calculate ordinary variance

Variance describes how widely the normalized scores vary around their average
of `0.9`. Subtract that average from every score:

```text
1.5 - 0.9 =  0.6
0.3 - 0.9 = -0.6
0.3 - 0.9 = -0.6
1.5 - 0.9 =  0.6
```

Square the differences, add them, and divide by four:

```text
ordinary variance = (0.6² + (-0.6)² + (-0.6)² + 0.6²) / 4
                  = (0.36 + 0.36 + 0.36 + 0.36) / 4
                  = 1.44 / 4
                  = 0.36
```

The project divides by `4` here because this is the variance term inside its
time-dependence calculation. The separate across-fold calculation uses the
usual sample-variance divisor, `number of folds - 1`.

## Step 6: measure the neighboring relationship

The centered sequence is `0.6, -0.6, -0.6, 0.6`. Multiply each value by the one
immediately before it:

```text
Pair 1: (-0.6)( 0.6) = -0.36
Pair 2: (-0.6)(-0.6) =  0.36
Pair 3: ( 0.6)(-0.6) = -0.36
```

Add the three products and divide by all four observations:

```text
neighbor relationship = (-0.36 + 0.36 - 0.36) / 4
                      = -0.36 / 4
                      = -0.09
```

It is negative because high and low deviations partly alternate and cancel. If
neighboring values tended to be high together or low together, this number
would be positive and the estimated uncertainty would increase.

## Step 7: adjust variance for neighboring observations

With one neighboring step, the Bartlett weight is:

```text
weight = 1 - 1 / (1 + 1) = 0.5
```

The relationship receives partial weight because evidence about dependence is
less direct as observations become farther apart. The factor `2` counts the
same relationship in the forward and backward time directions.

```text
adjusted variance
    = ordinary variance + 2 × weight × neighbor relationship
    = 0.36 + 2 × 0.5 × (-0.09)
    = 0.36 - 0.09
    = 0.27
```

Here the adjustment reduces variance because the neighboring relationship is
negative. In many financial sequences it is positive, which increases the
variance and makes the test harder to pass.

## Step 8: calculate uncertainty in the average

The variance of an average is the adjusted variance divided by the number of
observations. Uncertainty is the square root of that value:

```text
variance of the average = 0.27 / 4 = 0.0675
uncertainty             = sqrt(0.0675)
                        = 0.2598
                        ≈ 0.260
```

## Step 9: calculate the test statistic

Divide the average normalized score by its uncertainty:

```text
raw statistic = 0.9 / 0.2598 = 3.464
```

The project's small-sample correction for four one-step forecasts is:

```text
correction = sqrt((4 + 1 - 2) / 4)
           = sqrt(3 / 4)
           = 0.866

final statistic = 3.464 × 0.866 = 3.00
```

The ordinary one-sided 5% cutoff is `1.645`: if there were no true advantage,
only about 5% of reference results would exceed it by chance. The toy result of
`3.00` clears that first cutoff.

## Step 10: require consistency across test periods

The real study repeats the exercise over 20 successive test periods, called
folds. Each fold contributes one overall improvement score:

```text
across-fold statistic
    = average fold improvement
      / (sample spread of fold improvements / sqrt(20))
```

It must exceed `1.729`, the one-sided 5% cutoff for 20 fold observations. This
second check prevents one unusually successful period from carrying an
otherwise inconsistent result.

A cell initially passes only if both the pooled statistic and the across-fold
statistic pass and the average improvement is positive.

## Step 11: account for trying many ideas

