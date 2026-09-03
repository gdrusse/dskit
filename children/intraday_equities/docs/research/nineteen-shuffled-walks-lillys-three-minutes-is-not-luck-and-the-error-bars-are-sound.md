# Nineteen shuffled walks: Lilly's three minutes is not luck, and the error bars are sound

Date: 2026-09-03. Plan: `docs/plans/2026-09-horizon-search.md`, P8 and P4.
Builds on ADR-0069 (the many-attempts bar) and ADR-0074 (the expensive
scramble). Results table: `RESULT-P1-grid.md`.

## Question

The cheap luck check left exactly two cells standing: Lilly three
minutes ahead and the five stocks together at two, both on one-minute
rows with the tree model. The cheap check re-weights forecasts that are
already stored, so **it cannot test the fitting** — an answer that leaked
into an input is already baked into every stored forecast. Only a refit
sees that. Does the real result sit outside what shuffled trading days
produce, and do the shuffled statistics land where a correct error
estimate says they should?

And separately: Lilly is the widest-spread of the five names, every
price we use is the last trade of the minute, and that print flips
between the buyer's and the seller's side. At one minute ahead the flip
was shown to inflate Lilly's win. The one cell surviving the bar had
never been checked at its own spacing and look-ahead.

## Finding

### 1. The scramble had to be built; ADR-0069 left the middle out

ADR-0069 shipped both ends of a seam — `tier2_plan` emits the day
reshuffles, `tier2_verdict` reads the finished runs — and deliberately
left the part that re-runs the walk absent, as "~100 walks of compute,
for a WINNER only". ADR-0074 fills it as a **run knob**,
`label_scramble_seed`, honoured by `_DayScramble` in the child's
`nodes.py`. A scrambled walk is then an ordinary walk-forward document,
run by the ordinary command and judged by the ordinary rule, which is
the point: a null draw that travelled a different path is not that
result's null.

Four choices fix what the permutation may move, and each would break the
null if it went the other way.

- **The exchangeable unit is a whole trading session, never a row.** A
  session is self-contained for every horizon tested here, so moving one
  moves every overlapping label with it and nothing is reordered inside
  it. Preserved: within-session autocorrelation, the h-minute label
  overlap, the time-of-day shape, day-level volatility clustering, and
  the cross-stock correlation at each minute. Destroyed: only the link
  between the features at t and the return over [t, t+h].
- **One permutation for every symbol**, drawn from a calendar read once
  off the whole fold. If the names did not move together the cross-stock
  correlation would be destroyed too, and the null would no longer be
  the null we mean.
- **Training and validation windows permuted independently**, keyed by
  their own bounds. One shared shuffle would let a scrambled walk train
  on the sessions it is scored on.
- **The within-session key is milliseconds from that session's first
  row**, not the wall clock, so a summer session and a winter one align
  despite the hour daylight saving moves the New York open in UTC.

Two refusals. A session below 80% of the median row count leaves the
donor pool — permuting a half-day against a full one changes the row
count rather than the labels. And a row whose donor session lacks its
minute is refused, never given an invented label. The measured cost is
1.2% of rows (10,136 against 10,266): the boundary sessions of each
window. Filling them from their own real labels would have kept the
count exact by leaking real signal into the null.

### 2. Nineteen shuffles, and none of them came close

| | skill | statistic |
|---|---|---|
| the real walk | **+0.2947%** | **+3.26** |
| best of 19 shuffles | +0.0875% | +1.38 |
| the shuffles on average | −0.0163% | −0.37 |
| spread of the shuffles | — | 0.98 |

The real result beat all nineteen; the best shuffle reached about a
fifth of its size. Nineteen shuffles buy a one-in-twenty statement
(p = 0.050) and nothing stronger — a hundred were planned, and
ninety-nine would be needed for one-in-a-hundred. The cap is a budget
decision, stated rather than hidden. A larger family could only
strengthen the conclusion, since nothing came close.

### 3. The second check is the one that was worth the compute

The shuffled statistics scatter with a **spread of 0.98 against the 1.00
a correct error estimate gives**. That is the half that tests the
variance estimator, and it passes. Had it failed, every p-value in the
project would have been wrong with it — which is why ADR-0069 called
this check worth six hours on its own.

Their **centre sits 0.37 below zero** rather than on it. This is not a
defect and should not be read as one: a fitted model with nothing to
find is slightly *worse* than the constant it is measured against,
because it adds estimation noise the constant does not. The null of a
squared-error gap against a training mean is therefore centred a little
below zero for any model that fits anything. The consequence is that our
threshold is **conservative** — a cell must clear a bar set for a null
centred at zero while the real null sits below it. A centre *above* zero
would have been the alarming direction.

**Act on this:** `tier2_verdict` flags any centre past 0.3 as a
miscalibration and prints "every p-value in the project is suspect".
That rule was written before this evidence existed and fires in the
conservative direction. It should become a check on the spread plus a
one-sided check on the centre. Until it is changed the tool reports this
family as failing, and the reading above is the correct one.

### 4. The price flip is not what carries the three-minute cell

Four walks, two matched pairs, at the survivor's own spacing and
look-ahead (one-minute rows, three minutes ahead), over the same
sixteen-month quoted window and the same twelve test periods. A minute
with no usable two-sided quote is dropped from both sides first, so the
two prices are judged on exactly the same instants.

| model | last traded price | midpoint |
|---|---|---|
| simple | +0.0959% (t +0.74) | +0.0381% (t +0.17) |
| tree — the survivor's model | −0.5124% (t −2.04) | −0.4697% (t −2.14) |

**This pair can neither confirm nor kill the headline.** Its own
traded-price control fails on this window: the tree, which is the model
that survives on the long window, is firmly negative here on both
prices. Sixteen months and twelve test periods is about half the
evidence of the twenty-fold grid, and a third-of-a-percent edge cannot
be resolved either way with that much. Nothing in the pair may be read
against a long-window number.

What it does establish: **at three minutes the two price definitions
give near-identical answers** — −0.512% against −0.470% for the tree,
and the same ordering for the simple model. At one minute the traded
price was more than twice the midpoint. So whatever the three-minute
cell is, the buyer/seller flip is not what carries it. The standing gap
is unchanged: buy and sell prices exist only for Lilly and Exxon, so
this check still covers two of five names.

### 5. The bar, re-applied over everything

Seventy-nine walks, 438 cells, every attempt and every failure in the
ledger; the nineteen null draws excluded, because a null draw is not an
attempt. Adding the twelve new price-definition cells raised Lilly's
pass mark from 3.000 to 3.017 and the group's to 3.051 — the price the
search pays for having been widened.

| stock | cells clearing | worth this many independent tries | surviving look-ahead |
|---|---|---|---|
| Apple | 0 of 67 | 30 | none |
| JPMorgan | 0 of 67 | 31 | none |
| Lilly | 16 of 79 | 39 | **3 minutes** |
| Walmart | 0 of 67 | 30 | none |
| Exxon | 0 of 79 | 40 | none |
| all five together | 9 of 79 | 44 | **2 minutes** |

Both survivors still clear. Nothing else does.

## What this does not settle

- **The group's two-minute cell has had no shuffle test.** Nineteen
  walks, about two and a half hours; the configs and the code are in
  place.
- **The nineteen-shuffle cap.** One-in-twenty is the ceiling of this
  family, not of the method.
- **The model shortlist is entirely unrun.** Nothing was executed from
  it, so "big models do not work here" remains unproven and no model has
  been shown to reach past three minutes.
- **Three of five names have no buy/sell prices.**
- **Nine five-minute-row cells remain unrun** (the ten-minute arms).

## Sources

- ADR-0067 (the skill rule), ADR-0069 (the many-attempts bar and the
  seam), ADR-0074 (this scramble), `docs/architecture/decision-log.md`.
- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — the design this implements, and its Tier 2 specification.
- `docs/research/p4-arm-c-the-midpoint-keeps-half-of-lillys-edge-so-it-is-not-just-the-flip.md`
  — the one-minute midpoint result this extends to three minutes.
- Shao 2010 (dependent wild bootstrap with block weights); Romano & Wolf
  2005/2016 (stepdown, adjusted p-values); Harvey, Liu & Zhu 2016
  (t > 3.0 for a new claim); Romano & Tirlea 2020 (plain permutation is
  not level under dependence — hence whole sessions).
- Evidence on disk: `children/intraday_equities/docs/decisioning/tier2-scramble.jsonl`
  (one line per null draw), `docs/decisioning/attempts.jsonl` (the
  attempt ledger, 438 cells), `docs/decisioning/actions.csv` (a row per
  walk).
