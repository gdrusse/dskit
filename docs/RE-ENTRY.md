# Re-entry

Refreshed 2026-09-03, end of the horizon-search session. On `main`,
pushed to `origin`.

This is written for someone arriving cold. No shorthand. The question we
are answering: **how far ahead can we predict a stock's move and still
beat simply guessing the average?** That distance is the "look-ahead".

---

# ▶ PICK UP HERE

## The answer as it stands

**The best look-ahead that survives a fair bar is three minutes for Lilly
and two minutes for the group taken together.**

Apple, JPMorgan, Walmart and Exxon show nothing at any setting we tried.

So the honest summary is **one answer per stock, not one shared answer**.
Anyone who reports a single look-ahead for the whole study is overstating
it. The plan that produced this is
`docs/plans/2026-09-horizon-search.md`, which now carries a one-line
result for each of its nine problems.

"A fair bar" matters here. We have tried a great many combinations, and
with enough attempts something always looks good by luck. The bar makes a
candidate beat the best that luck alone produces across every attempt
we have ever made. Lilly at three minutes and the group at two are what
is left standing after that.

## What actually moved the wall: dense rows, not features

This is the single most useful thing to carry forward.

For most of the study we formed one row of data every five minutes. At
that spacing **nothing ever passed at three minutes ahead** — there
appeared to be a hard wall. Forming a row every minute instead moved the
wall out. The spacing of the rows, not the choice of inputs, was the
binding constraint all along.

Meanwhile, **every new input we built failed**:

- a time-of-day block (31 inputs) — did not beat its control anywhere,
  even after we fixed a real bug in it where the clock encoding wrapped
  around so the market open and the market close carried the same value;
- a block derived from the price bars themselves (10 inputs) — no;
- a block from the other stocks and the wider market (17 inputs) — no.

All three were leak-tested, so those are real negatives, not broken
tests. The lesson: spend effort on how densely we sample, not on
inventing more inputs.

## The honest gaps

Do not present the answer above without these.

1. **9 of the 50 five-minute-row cells were never run.** They are the
   ten-minute-ahead arms, and the control for that block had already
   failed, so they were deprioritised — but they are unrun, not
   negative.
2. **The 19-cell model shortlist is entirely unrun.** We confirmed the
   earlier model comparison was unfair to the bigger models (they got no
   tuning, no restraint and a single random seed), and we built a fair
   replacement. None of it has been executed. So "big models do not
   work here" is currently **unproven**.
3. **The buy/sell-midpoint check is underpowered.** Every price we use
   is the last trade of the minute, which lands on either the buyer's or
   the seller's price, so it jitters even when nothing changed. Repeating
   the work on the midpoint of the buy and sell prices showed the jitter
   *inflates* Lilly's win but does not *create* it — the edge survives at
   under half size. Exxon fails on both prices. But we only hold buy and
   sell prices for Lilly and Exxon; **JPMorgan, Walmart and Apple are
   missing or partial**, so this test covers half the evidence.
4. **The expensive scramble test has not been run for the surviving
   cells.** The cheap version of the luck check is done. The thorough one
   — re-running everything a hundred times with the trading days
   shuffled — is built and ready but never executed. Until it runs, the
   two survivors are "best available", not confirmed.

## Infrastructure fixed tonight

Two real bugs, both of which had been silently distorting the work.

1. **The journal was losing rows.** When several agents ran at once,
   their writes overwrote each other, so the record of what had been run
   was incomplete. Fixed.
2. **The walk was reading everything before filtering.** Each run built
   all sixteen million records into memory and only then narrowed to the
   handful it needed. This is what made dense rows unaffordable and so
   **is the direct reason the five-minute wall looked permanent all
   night**. Fixed — a run now reads only the bars it declared, once.

If a future session sees runs that are suddenly slow or memory-hungry,
suspect a regression in one of these two first.

## ADR status — nothing is ratified

**ADRs 0059 through 0073 are all PROPOSED. None is ratified.** Their code
is already in the tree, ahead of approval. That is the largest
outstanding decision for the owner: read them and accept or reject.
They are in `docs/architecture/decision-log.md`.

## The environment trap — read before running anything

**Run exactly one command at a time.**

A second command started beside a running walk **wedges the whole Linux
virtual machine**. Stopping the container does not clear it; only a full
restart (`wsl --shutdown`, then start again) does. One walk alone holds
roughly 11.5 GB of the 17 GB available, which is why there is no room for
a second.

Research and writing work can overlap freely. Anything that runs a
pipeline must be strictly one at a time.

Also standing: **no data after 2026-02-28 is ever read**, and everything
is paper only.

## Verification

```bash
python -m ruff check .
python -m pytest tests -q
(cd children/intraday_equities && python -m pytest tests -q)
```

The suite is slow. Prefer running only the tests covering what you
touched unless the owner asks for the full run.

## Next steps, in priority order

1. **Run the expensive scramble test on the two survivors** (Lilly at
   three minutes, the group at two). This either confirms the answer or
   removes it, and nothing else should be built on top of it until then.
2. **Run the 19-cell model shortlist.** It is the largest unrun block and
   the only fair test of bigger models we have.
3. **Get buy and sell prices for JPMorgan, Walmart and Apple**, then
   repeat the midpoint check so it covers all five stocks instead of two.
4. **Push row spacing below one minute**, since spacing is the one lever
   that has actually worked.
5. **Fill the 9 unrun five-minute cells** — low value, but it closes the
   grid honestly.
6. **Get ADRs 0059–0073 ratified or rejected.**
