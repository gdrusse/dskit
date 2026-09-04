# Shuffle-and-retrain test: a worked toy example

## TL;DR

The test replaces each trading day's true future returns with another whole
day's returns, then retrains and scores the complete model. A real-label model
should clearly beat these deliberately broken versions; Lilly does across 19
shuffles, but that supports only a one-in-20 luck statement, and the five-stock
group has not received this check yet.

## What concern does this test address?

It checks whether the modeling process can manufacture an impressive result
from luck, excessive flexibility, or leaked answers even when the true link
between inputs and future returns has been deliberately removed.

## The question

The ordinary forecast-skill test asks whether a model trained on the correct
labels beats a simple average guess. The shuffle-and-retrain test asks a
different question:

> Would this entire training and scoring process still look successful after
> we deliberately break the relationship it is supposed to learn?

We want the answer to be **no**.

## What is a label?

An input row contains information available at a particular minute. Its label
is the stock's realized return over the future period the model must predict.
For a three-minute forecast:

```text
Input time:  10:00
Label:       price change from 10:00 through 10:03
```

The model sees inputs and labels during training. Later, it receives unseen
inputs and tries to predict their labels.

## What gets changed?

The inputs stay attached to their original days. Only the labels are replaced.
A whole donor day supplies its entire label column to another day.

This is **not** a random reordering of individual rows. Minutes remain in order,
and a day's labels move together.

## A three-day toy example

Suppose each day contains three input rows and three future-return labels:

```text
Monday
  inputs:  M09:30, M09:31, M09:32
  labels:  +0.10, +0.20, -0.05

Tuesday
  inputs:  T09:30, T09:31, T09:32
  labels:  -0.20, -0.10, +0.10

Wednesday
  inputs:  W09:30, W09:31, W09:32
  labels:  +0.05, +0.02, +0.08
```

In the real run, each input is paired with what actually happened after it:

```text
Monday inputs    -> Monday labels
Tuesday inputs   -> Tuesday labels
Wednesday inputs -> Wednesday labels
```

Now draw one shuffled donor map:

```text
Monday receives Wednesday's labels
Tuesday receives Monday's labels
Wednesday receives Tuesday's labels
```

The scrambled training data becomes:

```text
Monday inputs    -> +0.05, +0.02, +0.08
Tuesday inputs   -> +0.10, +0.20, -0.05
Wednesday inputs -> -0.20, -0.10, +0.10
```

Monday's market information is now paired with Wednesday's answers. Any real
Monday input-to-outcome relationship has been broken.

## Why move whole days?

Moving individual labels would create unrealistic data. A three-minute label
overlaps the neighboring three-minute labels, the market behaves differently
near the open and close, and stocks move together at the same minute.

Moving a whole day preserves:

- the order of minutes within that donor day;
- overlap among neighboring future-return labels;
- the donor day's time-of-day pattern;
- the donor day's quiet or volatile character; and
- the relationship among stocks at the same minute.

It destroys the one relationship being tested: whether information from this
day predicts this day's later return.

## Why retrain the model?

The scrambled labels are used during training, not merely during final scoring.
The complete 20-fold walk is rerun from the beginning.

This matters because rescoring an already-trained model cannot reveal a fitting
problem. For example, if an input accidentally contained part of its true
future label, that leaked answer would already be baked into the stored
prediction. Retraining after swapping labels breaks that accidental match and
tests the same fitting machinery that produced the real result.

## Training and validation are shuffled separately

Within every fold, the training window gets one donor map and the later
validation window gets a different donor map. A shared map could let the model
train on the same donor days used to score it.

All stocks share the same map. Giving each stock a different map would destroy
the way stocks move together and create an easier, unrealistic test.

## What happens when a donor day does not match?

Minutes are aligned by elapsed time from the first row of each session. This
keeps summer and winter sessions aligned despite daylight-saving changes in
UTC clock time.

A day shorter than 80% of the typical day is excluded from the donor pool. If a
donor lacks a required minute, that row is dropped rather than filled with an
invented or real answer. In the Lilly run, this reduced scored rows from 10,266
to roughly 10,140, about 1.2%.

## A toy scoring result

The following numbers are invented only to show how the comparison works:

```text
Real-label model statistic:  2.40

Shuffle 1 statistic:        -0.50
Shuffle 2 statistic:         0.20
Shuffle 3 statistic:         0.80
Shuffle 4 statistic:        -0.20
Shuffle 5 statistic:         0.10
```

The real-label model beats every scrambled model. That is encouraging: when the
true input-to-label pairing is removed, the apparent predictive ability mostly
disappears.

But five shuffles provide weak resolution. Even when none beats reality, the
smallest honest probability calculation is:

```text
probability = (number of shuffles at least as strong as reality + 1)
              / (number of shuffles + 1)

            = (0 + 1) / (5 + 1)
            = 1 / 6
            = 0.167
```

The added `1` prevents a finite experiment from claiming that luck is
impossible merely because it did not appear in a small number of tries.

## What should shuffled results look like?

They should generally have little or negative improvement over the simple
average guess. A flexible model fitted to meaningless labels often performs
slightly worse than the average because fitting adds noise.

Their test statistics should have a spread near `1`. If the spread were much
larger or smaller, the project's uncertainty calculation would be
miscalibrated. Thus the experiment tests both the discovered result and the
method used to judge it.

## Why both this and the baseline test?

The two tests catch different failures:

```text
Baseline test:
  Did the real-label model predict better than a sensible simple guess?
