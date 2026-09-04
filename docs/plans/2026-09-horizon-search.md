# Horizon search — the working document

**Goal.** Find the look-ahead at which we beat a simple average guess —
per stock and for the group. Nothing is settled: how often we form a row,
how far ahead we predict, which price we use, and which inputs we build
are all open. Past negative results narrow nothing on their own.

**Rules for every agent.**
- Every response under 500 characters. Every research summary, every
  result, every note. No jargon in this document.
- Before any action, answer: does this help decide the look-ahead?
  If no, drop it.
- Every pipeline run writes a journal row. Every finding writes a
  research doc through `dskit.journal`.
- Result lines land here, one line per item, under **Result**.

---

## P1 — How often we predict and how far ahead are tangled

**Problem.** Two separate choices have been fixed together: we form a row
every five minutes and we look ahead by a fixed amount. We have a price
every minute, so both are free. Any statement about the right look-ahead
is meaningless until both are searched together.

**Response.** Research agent first: what do practitioners use for row
spacing versus look-ahead, and how do they choose? Then build agents:
rows every 1, 2, 3, 5 and 10 minutes crossed with look-aheads from 1
minute to an hour, same stocks and dates. One agent per row spacing.
Output is a grid, not a number.

**Result.** Row spacing was the binding constraint, not the look-ahead.
Forming a row every minute instead of every fifth moved the wall; at
five-minute rows nothing ever passed at three minutes.

## P2 — Time of day is barely encoded

**Problem.** The market behaves differently at the open, over lunch, into
the close, and across the overnight gap. The model gets only crude clock
inputs — nothing marking lunch, the last half hour, or the switch to a
new day. If behaviour differs by time of day, the right look-ahead
probably does too.

**Response.** Research agent first: how is time of day encoded in
short-horizon equity work, and what is known about lunch, open and close
effects? Then build agents: minutes since open and to close, lunch
window, first and last thirty minutes, day-of-week, day-boundary markers,
training-only time-of-day averages. Test each block alone, then check
whether the best look-ahead differs by time of day.

**Result.** The block was built — 31 inputs — and a real bug fixed: the
clock encoding wrapped, so the open and the close carried the same value.
Even fixed, the block did not beat its control at any look-ahead.

## P3 — Feature engineering has barely started

**Problem.** Only recent returns carry any history. Volume, the gap
between buy and sell prices, trading intensity, and how the stocks move
against each other are snapshots or missing. A lot of ground is
unexplored before anything about the look-ahead is settled.

**Response.** Research agents first, several in parallel and searching
online: what inputs work for short-horizon equity prediction, what is
known to fail, and what needs data we do not have. Then build agents take
the strongest candidates, add history to each, test alone, then combined.
Judged on one thing: does it extend the look-ahead at which we beat the
average?

**Result.** Two blocks built and leak-tested: one from the price bars
themselves (10 inputs) and one from the other stocks and the wider market
(17 inputs). Neither beat its control at any look-ahead.

## P4 — The price we use flips between two values

**Problem.** Every price is the last trade of the minute, and trades
happen at either the buyer's or the seller's price, so the number jumps
even when nothing changed. That could hide a real short-term pattern or
create a fake one. We cannot tell which.

**Response.** Research agent first: how is this handled, and what is the
accepted fix? Then run agents: repeat identically with the average price
of all trades in the minute, and with the midpoint of buy and sell prices
where available. Compare all three at every look-ahead. Build on whichever
gives the clearest, most stable answer.

**Result.** The flip inflates the win but does not create it. On the
midpoint, over an identical window, Lilly's edge survives at under half
the size; Exxon fails on both prices. Carried to the survivor's own
setting — one-minute rows, three minutes ahead — the two price
definitions give near-identical answers, so the flip is not what carries
that cell; but the pair's own traded-price control fails on the
sixteen-month quoted window, so it can neither confirm nor kill the
headline. Buy and sell prices exist only for Lilly and Exxon, so this
test carries half the evidence.

## P5 — What we count as success may pick the wrong look-ahead

**Problem.** Our test is deliberately forgiving about the noise a model
adds when it fits, so it can declare a win where predictions are actually
worse than guessing the average. Choosing the look-ahead by counting wins
uses the wrong evidence.

**Response.** Research agent first: how should forecast skill be judged
when the benchmark is a constant, and what do people use instead of
counting wins? Then one agent writes the rule as numbers, and applies it
to everything already run and everything new, so every look-ahead is
judged the same way.

**Result.** The honest rule is built (ADR-0067) and is now the verdict
every run is judged by. Re-scoring the runs already finished failed 113
of their 120 cells.

## P6 — Ordering and size may have different answers

**Problem.** So far the models rank the stocks sensibly much further
ahead than they predict how big a move will be. We only score size. If
ordering holds longer, the answer to "how far ahead" may be two numbers
for two uses.

**Response.** Research agent first: when is ranking the right target
rather than size, and how is it scored? Then build agents add two
measurements — how close predicted size is to actual size, and how good
the ordering is at each moment across stocks — and re-run every
look-ahead. Report both horizons if they differ.

**Result.** Both measures are built (ADR-0068). The old ordering number
pooled time and names together; the new one refuses to report below five
stocks. Lilly's three-minute win predicts moves about 1.4 times too
small, so its size is usable, not only its ordering.

## P7 — The middle ground between simple and large is untested

**Problem.** Large models have not worked yet, but only the two extremes
were tried, and they were fed inputs with almost no history. That rules
nothing out about model choice or about the look-ahead.

**Response.** Research agent first: what model sizes suit this much data
at this noise level? Then build agents: stronger restraint on large
models, fewer inputs, averaging several small models, and a re-test of
the large ones once P2 and P3 supply real history. Everything else held
identical.

**Result.** 19 model setups are ready, and the earlier comparison was
confirmed unfair to the bigger models — they got no tuning, no restraint
and a single seed. **None have been run**, in this session either: the
budget went to the luck check and the price-definition pair. So no model
has been shown to reach past three minutes, and "big models do not work
here" is still unproven.

## P8 — Many attempts need a fair bar

**Problem.** We have run many combinations, each scored many times. With
enough attempts some look good by luck. Without a bar for that, a real
look-ahead and a lucky one look the same.

**Response.** Research agent first: what is the standard adjustment when
many models and horizons are tried on one dataset? Then one agent
implements it plus a scramble test — shuffle the answers, re-run, see
what luck alone produces. Every candidate look-ahead must clear that bar.

**Result.** The bar is built (ADR-0069) and applied over 438 cells from
79 walks. Surviving it: Lilly at three minutes, and the group at two.
The expensive scramble was then built (ADR-0074) and run nineteen times
on Lilly's cell: the real result beat every shuffle, the best of which
reached about a third of its size, and the shuffled statistics scattered by
the amount a correct error estimate predicts. Nineteen shuffles buy
one-in-twenty and no more. The group's cell is untested.

## P9 — Three stocks, and two were dropped

**Problem.** We are deciding on three stocks, having excluded two whose
price history has uncorrected jumps from stock splits. Too few to tell
whether each stock has its own answer or they share one.

**Response.** Research agent first: how are split jumps corrected, and
what does it cost to redo the history? Then agents correct them, restore
all five stocks, and test one shared look-ahead against per-stock ones.
More stocks also makes P6's ordering measurement meaningful.

**Result.** Both splits sat inside our history, which starts 2016. The
history was re-pulled already corrected; all five stocks and seven funds
were verified clean. The study now starts 2018, to step past a fund's
uncorrected spin-off.

---

## P10 — Twenty-five-asset modelability funnel

**Goal.** Find the furthest horizon predictable by one frozen pooled
architecture for each of 25 stocks and ETFs, or record none. This does not
claim that an asset is intrinsically predictable by every possible model.

**Selection.** Admit assets only by criteria fixed before reading model scores:
coverage, liquidity, clean corporate history, and diversification. If historical
predictive results influence admission, every screened candidate enters the
attempt ledger, including rejected ones.

**One pipeline document.** One declarative, resumable pipeline JSON declares
the 25-asset training universe, eight horizon walks, the three gates, and their
saved filters. The existing journal remains the authoritative run record, and
the pipeline suppresses every synthetic `GROUP` verdict.

**Gate 1.** Fit eight separate pooled models, one for each frozen horizon
`{1, 2, 3, 5, 10, 20, 30, 60}`, and register all 25 × 8 individual cells before
filtering. For each asset, failure at `h=1` means none; otherwise `h*` is its
furthest consecutive pass of both pooled and across-fold statistics.

**Gate 2.** Use one study-wide max-statistic family over all 200 asset-horizon
cells, with the same session resample shared across every cell. A selected `h*`
must clear the corrected statistic, adjusted probability, and positive lower
bound. Failure means no survivor; it never falls back to a shorter horizon.

**Gate 3.** Shuffle whole-session labels and refit the identical 25-asset pooled
model at each unique surviving horizon. Survivors are only scored outputs: the
training universe never shrinks. Use seeds 0–18, so no shuffled run beating the
real result gives the smallest attainable probability, 1/20 = 0.05.

**Output.** One result row per asset records `gate1_h`, each gate's status,
evidence counts, first failed horizon, and an explicit `not_reached` reason.
Filters are saved artifacts, never hand-edited symbol lists.

**Feasibility gate.** Before the study, measure one 25-asset fold against the
17 GB WSL limit. If it does not fit, stop: stream or use float32/LightGBM
datasets, or score bounded target batches after one identical pooled fit; never
reduce the training universe to make a batch fit.

**Freeze.** Before the first run, pin the 2026-02-28 data cut, split adjustment,
folds, embargo, features, model settings, thresholds, horizon order, 19 shuffle
seeds, probability rule, and null-calibration tolerances.

**Implementation.** The current pipeline engine cannot yet resume across the
completed walk, bar, and shuffle stages. Inventory its seams, then write an ADR
for generic staged pipeline orchestration, study-wide correction, GROUP
suppression, and memory strategy; wait for approval before code. Every child
walk and stage remains journaled normally—there is no sidecar manifest.

**Result.** UPRO, BAC, AMZN, AVGO, NFLX, MSFT, GOOGL, SMH, and IWM landed as
12,213,670 verified bars with no cutoff violations, bringing the disk inventory
to 25 assets. The skeptic gave a conditional GO; memory preflight, ADR approval,
implementation, and execution remain.

---

## The orchestrator

Holds the goal and the queue. Runs nothing itself — every research,
build, run and check goes to a subagent, as many in parallel as the work
allows.

- **Order.** P5 and P4 first: they set how everything is judged and what
  the data is. Then P1, P2, P3 together — that is the search. Then P6 and
  P7. Then P8 and P9 as the bar and the breadth.
- **Parallel.** Research and build agents run freely in parallel.
  Pipeline runs serialise: one walk holds about 11.5 GB of 17 GB.
- **Every agent returns under 500 characters.** An agent that returns
  more is asked again, shorter.
- **Recording.** Every run a journal row; every finding a research doc;
  every item a one-line **Result** here.
- **Stop.** When each stock and the group have a defensible look-ahead
  under P5's rule and P8's bar — or when the bar rules them all out, which
  is also an answer.
- **Finish.** Refresh `docs/RE-ENTRY.md`, commit, push.

## Standing constraints

- No data after 2026-02-28 is read. Paper only.
- ADRs 0059–0062 are proposed; their code is in the tree. An ADR before
  significant new code.
- Stocks: all five — AAPL, JPM, LLY, WMT, XOM — since P9 corrected the
  splits. History starts 2018.
