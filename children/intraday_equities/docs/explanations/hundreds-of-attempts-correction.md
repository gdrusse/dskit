# Hundreds-of-attempts correction: a worked toy example

**No longer used.** Gate 2 is retired as a stock-selection filter under
ADR-0088. HFDR is instead controlled inside the MIO.

## TL;DR

Trying hundreds of model variations makes an accidental winner likely, so this
correction compares each real result with the **best** result produced across
all attempts in 10,000 luck-only worlds. Lilly's three-minute statistic is
`3.22`, above its corrected bar of `3.017`; this correction does not retrain the
model, which is why the separate shuffle-and-retrain test is also needed.

## What concern does this method address?

It prevents us from presenting the luckiest result from a large search as if it
were the only idea we ever tested.

## The problem

For one pre-chosen test, the ordinary one-sided 5% cutoff is `1.645`. A useless
idea has about a 5% chance of crossing that cutoff by random variation.

Now imagine testing 100 unrelated useless ideas. The expected number crossing
the ordinary cutoff is:

```text
100 attempts × 0.05 chance per attempt = 5 accidental passes
```

The best of 100 results will usually look much better than an ordinary random
result. Asking only whether the winner exceeds `1.645` is therefore unfair: we
chose the winner after seeing all 100.

This project has tested 438 model cells across 79 complete walks. A cell is one
combination of stock or group, model, look-ahead, row spacing, feature block,
and price definition.

## A lottery analogy

One lottery ticket winning is surprising. At least one ticket winning after we
buy hundreds is much less surprising.

The correction does not judge our winning ticket against one random ticket. It
judges it against the best ticket in many equally large bundles of random
tickets.

## What information is saved for each attempt?

Every tested cell is written to an append-only attempt registry. Repeating the
same cell does not count as a new idea, but an old failed attempt never
disappears simply because it is inconvenient.

This matters because the correction can only charge us for attempts it knows
about. An incomplete registry would make the corrected bar too easy.

## Creating one luck-only world

For each cell, the project reduces its forecast performance to one score for
each trading session. It first recenters those daily scores so their average is
exactly zero. That creates the no-advantage assumption being tested.

It then gives each trading session a random `+1` or `-1` coin:

```text
Original centered daily scores:  +0.4, -0.2, +0.1, -0.3
Random session coins:               -1,   +1,   -1,   +1
Luck-only daily scores:           -0.4, -0.2, -0.1, -0.3
```

The same session coin is used for every related cell and every stock. This
preserves the fact that nearby look-aheads and models often rise and fall
together. Treating them as independent would charge too harshly for several
nearly identical ideas.

The project recomputes every cell's test statistic in this luck-only world and
records only the largest statistic:

```text
Cell A statistic:  0.7
Cell B statistic:  1.9
Cell C statistic: -0.2
Cell D statistic:  1.3

Best luck-only statistic in this world = 1.9
```

## Repeat the entire fake search

The project creates 10,000 luck-only worlds. Each world produces one number:
the best statistic found anywhere in that world's complete search.

A small teaching example might create only 20 worlds. Suppose their sorted best
statistics were:

```text
0.9, 1.0, 1.1, 1.2, 1.3,
1.4, 1.5, 1.6, 1.7, 1.8,
1.9, 2.0, 2.1, 2.2, 2.3,
2.4, 2.5, 2.6, 2.7, 2.8
```

The 95th-percentile position is:

```text
0.95 × 20 = 19
```

The 19th sorted value is `2.7`, so 95% of these best-of-search lucky results are
at or below `2.7`. In a real 10,000-world run, the position is:

```text
0.95 × 10,000 = 9,500
```

The value at that edge is the search-aware cutoff, called `c*`.

## The project's extra floor

The final pass mark is the larger of the simulated cutoff and `3.0`:

```text
final bar = max(c*, 3.0)
```

In the 20-world toy example:

```text
final bar = max(2.7, 3.0) = 3.0
```

A real statistic of `2.1` would pass the ordinary `1.645` test but fail the
search-aware `3.0` bar. A statistic of `3.2` would clear both.

The `3.0` floor is a deliberate conservative rule: even when the simulated
search happens to produce a lower cutoff, the project refuses to call a
selected discovery convincing below `3.0`.

## Why not treat all 438 attempts as independent?

The cells are strongly related. A two-minute and three-minute forecast from the
same model are not two independent lottery tickets. Neither are two models
trained on almost the same inputs.

Using the same random session coins across all cells preserves those
relationships. The simulation then learns how many meaningfully different
tries the search was worth. In the latest report, Lilly's 79 recorded cells
behaved like about 39 independent tries; the group's 79 behaved like about 44.

## The adjusted probability

The procedure also asks how often each real statistic is beaten after accounting
for the other attempted cells. This produces a search-adjusted probability.
The project uses a step-by-step calculation that allows more than one genuine
result to survive while still protecting against accidental selections.

A cell passes the full correction only when:

```text
1. It already passed the ordinary pooled and across-fold tests.
2. Its statistic exceeds max(c*, 3.0).
3. Its search-adjusted probability is at most 0.05.
4. Its estimated improvement is positive.
5. Its one-sided lower uncertainty bound is positive.
```

## Where Lilly currently stands

These are actual project results, not teaching numbers. Lilly's surviving cell
uses one-minute rows, predicts three minutes ahead, and uses the tree model with
the market-and-sector input block:

```text
Recorded Lilly cells:             79
Equivalent independent tries:     39
Real statistic:                  3.22
Corrected pass mark:             3.017
Search-adjusted probability:     0.026
Estimated improvement:          +0.3039%
One-sided lower bound:          +0.1489%
```

The comparisons are:

```text
3.22 > 3.017        statistic clears the corrected bar
0.026 < 0.05        adjusted probability clears the rule
0.1489% > 0         even the lower bound remains positive
```

Lilly therefore survives the hundreds-of-attempts correction at three minutes
ahead. The five-stock group survives at two minutes ahead. Apple, JPMorgan,
Walmart, and Exxon have no surviving individual look-ahead.

## How this differs from shuffle and retrain

This correction is the **cheap** luck check. It rearranges saved daily scores
and can repeat the whole 438-cell search 10,000 times without fitting the models
again.

The shuffle-and-retrain check instead swaps whole days' labels and reruns all
model fitting. That is expensive, but it can catch leakage or fitting behavior
already baked into stored forecasts.

```text
Hundreds-of-attempts correction:
  Did selection from the entire search produce a lucky winner?

Shuffle and retrain:
  Can the complete fitting pipeline look successful after the real
  input-to-label relationship is deliberately broken?
```

Lilly has passed both checks, although only 19 full retrainings were completed,
which limits that second check to a one-in-20 statement. The five-stock group's
two-minute result has passed the hundreds-of-attempts correction but still
needs the full shuffle-and-retrain check.

Neither test proves the historical relationship will continue in future
markets.
