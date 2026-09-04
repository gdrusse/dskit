# Re-entry

**pmquant child rebuild, 2026-09-04 (branch `claude/pmquant-dskit-rebuild-367zis`, both repos).**
pmquant now lives as `children/pmquant/` — thin tier-3 kinds + JSON over
dskit seams. The success document `children/pmquant/configs/run-e2e.json`
runs the stat test, trains the transformer ensemble and builds the Kelly
MIO in ONE pipeline (22 nodes; `tests/test_e2e.py` proves it on the
synthetic world: GO on both series, 3,014 lots sized). Its real-data twin
is `run-kalshi-ladders.json` (pinned to differ only in data location, fee
book, eligibility bar, training budget). Generic capability graduated into
dskit under ADR-0075…0078: onboarding packs `kalshi`, `polymarket`,
`predexon` + `leads.py` (LeadGrid), the `localtables` connector (the
parent's on-disk parquet/ndjson stores import with no code), the
`observations` pipeline kind, the public clause DSL. Every data pull is a
dskit connector; the on-disk Kalshi + Polymarket stores are ported via
`configs/source-pit-ladders.json` / `source-settled-markets.json`.
**Waiting on the owner:** a `PREDEXON_API_KEY` in the environment before
`source-predexon.json` can pull (names only in configs); the real-data run
of the twin on a machine holding `~/pmquant_data`. Two gotchas cost a run
tonight and are written into the child's CLAUDE.md: declare
`torch.optim.AdamW` (the pack's SGD default never learns this recipe) and
list every sized series in `fee_rate_by_series` (fail-closed). Journal:
`children/pmquant/docs/decisioning/actions.csv` holds every execution of
the build. Follow-ups: `TODO.md` "Found by the pmquant child build".

---

Recovery wrap, 2026-09-03: second-cohort Alpaca SIP bars (TSLA, TQQQ, NVDA, AMD) were researched, registered, and backfilled as `alpaca-sip-split-b`. Focused configuration tests: 28 passed; one known pre-existing start-date assertion still fails in `run-pb-s01-h01-lgbm-cross.json`.
Codex migration wrap: project-local AGENTS.md guides, session-start fast-forward pull hook, and portable skills were added under .cursor/.

Refreshed 2026-09-03, end of the horizon-search session. On `main`,
pushed to `origin`.

This is written for someone arriving cold. No shorthand. The question we
are answering: **how far ahead can we predict a stock's move and still
beat simply guessing the average?** That distance is the "look-ahead".

---

# ▶ PICK UP HERE

## The answer as it stands

**Three minutes for Lilly. Two minutes for the five stocks taken
together. Nothing for Apple, JPMorgan, Walmart or Exxon at any setting
we have tried.**

The rest of the session in five lines:

- **Which cells clear the many-attempts bar.** 25 of the 438 cells ever
  tried: sixteen for Lilly, nine for the five stocks together, none at
  all for Apple, JPMorgan, Walmart or Exxon. Twenty-two of the 25 sit at
  one minute ahead. The two that reach furthest are the headline —
  Lilly at three minutes and the group at two, both on one-minute row
  spacing, the tree model and the market-and-sector input block.
- **What moved the wall.** Forming a row of data every minute instead of
  every fifth. Dense rows, not feature blocks: every new input block we
  built failed against its own control.
- **The luck check.** Lilly's three-minute cell was re-run nineteen
  times with whole trading days shuffled, and the real result beat every
  one of them. Nineteen shuffles is a one-in-twenty statement and no
  more.
- **The midpoint check is inconclusive, not supporting.** On the short
  window where buy and sell prices exist, the cell's own traded-price
  control fails too, so that pair can neither confirm nor kill the
  headline.
- **What is unrun.** The group's two-minute cell has had no luck check;
  the 19-cell model shortlist is entirely unrun; buy and sell prices
  exist for only two of the five names; 9 of the 50 five-minute-row
  cells were never run; and 20 decision records are still unratified.
  The full list, with what each one blocks, is under "The honest gaps"
  below.

So the honest summary is **one answer per stock, not one shared
answer**. Anyone who reports a single look-ahead for the whole study is
overstating it.

Lilly's three minutes is the only cell that has now been tested by
BOTH bars and by the expensive luck check. That makes it the one number
in this project that is properly defended. The group's two minutes has
cleared the bars but not the luck check.

"A bar" matters here, and there are two. The first asks whether a cell
beats a flat average guess on its own (ADR-0067). The second is the
many-attempts bar (ADR-0069): we have tried hundreds of combinations,
and with enough attempts something always looks good by luck, so a
candidate must beat the best that luck alone produces across every
attempt ever made. Lilly at three minutes and the group at two are what
is left after both.

The many-attempts bar was re-applied over **438 cells from 79 walks** at
the end of this session — the whole horizon search, every failure
included, plus the twelve new price-definition cells. It moved Lilly's
pass mark from 3.000 to 3.017 and the group's to 3.051, and both
survivors still clear it. Nothing else does, for any stock, at any
spacing, look-ahead, model, feature block or price definition.

The full result tables are `RESULT-P1-grid.md`, which now opens with a
plain-language summary written for someone reading it on GitHub with no
other context. The plan that produced it is
`docs/plans/2026-09-horizon-search.md`.

## What was settled this session

### 1. The luck check confirms Lilly, and it validates the whole project's error bars

The cheap luck check re-weights forecasts that are already stored. It
cannot see whether the FITTING itself was the problem, because an answer
that leaked into an input is already baked into every stored forecast.
The expensive one can: it shuffles which trading day donates the answers
and **re-runs the whole walk**, model fitting and all. That was built
this session (ADR-0074) — ADR-0069 had deliberately left the middle of
it out, as about a hundred walks of compute, for a winner only.

Whole trading days move, never single rows, so the shape of a day, the
overlap between neighbouring answers, the time-of-day pattern, the
day-to-day swings in how much prices move, and the way the five stocks
move together at any minute all survive. Only the link being tested is
destroyed.

**Nineteen shuffled re-runs of Lilly's three-minute cell. The real
result beat every one of them, and the best shuffle reached about a
third of its size** (+0.0875% against the real +0.2947%). Nineteen
shuffles buy a one-in-twenty statement (p = 0.050) and no more; the plan
called for a hundred, and ninety-nine would be needed to say
one-in-a-hundred. A larger family could only strengthen this, since
nothing came close.

**The second answer is worth more than the first.** The shuffled
statistics landed with a spread of 0.98 against the 1.00 a correct error
estimate gives. **The spread is the half that tests the error estimate,
and it is right** — had it not been, every p-value in this project would
have been wrong with it. Their centre sits 0.37 below zero rather than
on it. That is expected rather than a defect, and it is the cost of
fitting showing up: a model with nothing to find is slightly WORSE than
the flat average it is measured against, because it adds estimation
noise the average does not. It makes our threshold conservative, not
generous. A centre ABOVE zero would have been the alarming direction.

One consequence to act on: `tier2_verdict` flags any centre past 0.3 as
a miscalibration and prints "every p-value in the project is suspect".
On this evidence that rule is too blunt — it fires in the CONSERVATIVE
direction. It should become a check on the spread plus a one-sided check
on the centre. Until it is changed, the tool reports this family as
failing; the reading above is the correct one.

### 2. The price-definition worry does not explain the three-minute cell

Every price we use is the last trade of the minute, which lands on
either the buyer's or the seller's side, so it jitters when nothing has
changed. Lilly is the widest-spread of the five names, so the one
surviving cell is also the one most exposed to that artefact. Four
walks, two matched pairs, at the survivor's own spacing and look-ahead.

**At three minutes the two price definitions give near-identical
answers.** At one minute the traded price was more than twice the
midpoint; at three minutes they agree. So the buyer/seller flip is not
what carries this cell.

**But the pair can neither confirm nor kill the headline**, and the file
says so plainly: its own traded-price control fails on the sixteen-month
quoted window. Half the evidence cannot resolve a third-of-a-percent
edge either way. Nothing in that pair may be read against a long-window
number.

## What actually moved the wall: dense rows, not features

The single most useful thing to carry forward.

For most of the study we formed one row of data every five minutes. At
that spacing **nothing ever passed at three minutes ahead** — there
appeared to be a hard wall. Forming a row every minute instead moved the
wall out. The spacing of the rows, not the choice of inputs, was the
binding constraint all along.

Meanwhile, **every new input we built failed**: a time-of-day block (31
inputs, and a real bug fixed in it where the clock encoding wrapped so
the open and the close carried the same value), a block derived from the
price bars (10 inputs), and a block from the other stocks and the wider
market (17 inputs). All three were leak-tested, so those are real
negatives. The lesson: spend effort on how densely we sample, not on
inventing more inputs.

## The honest gaps — do not present the answer without these

1. **The group's two-minute cell has had no luck check.** Only Lilly's
   three-minute cell was shuffled. The configs and the code are in
   place; it is nineteen walks, about two and a half hours, one at a
   time. Until then the group's two minutes is "best available", not
   confirmed.
2. **The luck check ran nineteen times, not the hundred that was
   planned.** That caps the claim at one-in-twenty. Running to
   ninety-nine would buy one-in-a-hundred and nothing else — no shuffle
   came close, so the direction of the answer will not change. Each
   shuffled walk is about 7 minutes 25 seconds, so the remaining eighty
   are roughly ten hours.
3. **The 19-cell model shortlist is entirely unrun.** Nothing from it
   was executed this session. So "big models do not work here" is
   **unproven**, and no model has been shown to extend the look-ahead
   past three minutes. The configs are ready in
   `children/intraday_equities/configs/run-p7-*.json` — in the author's
   recommended order: the held-back tree, extra trees, projection-based
   linear (PLS), scaled linear, the tiny net, the five-seed net, random
   forest last. Run them at one-minute rows at three minutes ahead
   first: that is the current edge and the only informative
   look-ahead. Note the shipped configs are at five-minute rows and
   need the same one-line spacing override the scramble configs use.
4. **Buy and sell prices exist only for Lilly and Exxon.** JPMorgan,
   Walmart and Apple are missing or partial, so every price-definition
   check covers two of the five names. Pulling the other three is the
   cheapest way to double that evidence.
5. **9 of the 50 five-minute-row cells were never run** — the
   ten-minute-ahead arms, whose control had already failed. Skipped
   deliberately again this session: every block arm at five minutes was
   worse than its control, so the expected value is very low. They are
   unrun, not negative.
6. **A config-hygiene failure is live in the test suite.** The
   one-minute-row family (`configs/run-pb-*.json`) declares a tape start
   of 2020-01-01 while the study start is 2018-01-01, and
   `tests/test_configs.py::test_every_run_reads_the_split_adjusted_store_from_the_study_start`
   fails on it. It does NOT invalidate any result — the earliest fold
   trains from 2020-05, so no fold is truncated — but it must be fixed
   or the assertion relaxed with a reason. It was left alone this
   session because changing a winner's config would break the identity
   of the cell the luck check was run against.

## Infrastructure — two bugs fixed earlier, one thing added

1. **The journal was losing rows** when several agents ran at once.
   Fixed.
2. **The walk was reading everything before filtering** — each run built
   all sixteen million records into memory and only then narrowed. This
   is the direct reason the five-minute wall looked permanent. Fixed; a
   run now reads only the bars it declared, once.
3. **New this session:** `label_scramble_seed`, a run knob on the scan
   node, plus `_DayScramble` in the child's `nodes.py` (ADR-0074). A
   scrambled walk is an ordinary walk-forward document run by the
   ordinary command and judged by the ordinary rule — which is the
   point: a null draw must travel the same path as the real result or it
   is not that result's null. Seven tests in
   `children/intraday_equities/tests/test_scramble.py`.

If a future session sees runs that are suddenly slow or memory-hungry,
suspect a regression in the first two.

## Decision records — nothing is ratified

**20 decision records are PROPOSED and none is ratified: ADR-0002,
ADR-0004, ADR-0005, ADR-0006, and ADR-0059 through ADR-0074.** Counted
from the `**Status:**` line of every record in
`docs/architecture/decision-log.md`, which holds 74 records numbered
ADR-0001 to ADR-0074 with no gaps; the other 54 are accepted. The code
for the proposed ones is already in the tree, ahead of approval. That is
the largest outstanding decision for the owner: read them and accept or
reject.

## The environment trap — read before running anything

**Run exactly one command at a time.**

A second command started beside a running walk **wedges the whole Linux
virtual machine**. Stopping the container does not clear it; only a full
restart (`wsl --shutdown`, then start again) does. A one-minute-row walk
holds about 13.7 GB of the 17 GB available, which is why there is no
room for a second.

Timings measured this session, one at a time:

- a one-minute-row twenty-fold walk over five names: **about 7 minutes
  25 seconds**, peak 13.7 GB;
- the same on the shortened sixteen-month quoted window with two names:
  **about 100 seconds**, peak 2.5 GB.

Research and writing work can overlap freely. Anything that runs a
pipeline must be strictly one at a time.

Also standing: **no data at or after 2026-03-01 is ever read**, and
everything is paper only.

## How to run things

```bash
cd children/intraday_equities
../../.venv/bin/python -m dskit.pipeline walkforward <config.json> \
    --asof 2025-11-30 --adapter intraday_equities.nodes
```

The `--adapter` flag is required: without it none of the child's node
kinds are registered and the document is refused. Then:

```bash
../../.venv/bin/python -m dskit.pipeline skill  <summary-dir>   # ADR-0067
../../.venv/bin/python -m dskit.pipeline bar    <summary-dir>...\
    --registry docs/decisioning/attempts.jsonl                  # ADR-0069
```

## Verification

```bash
python -m ruff check .
python -m pytest tests -q
(cd children/intraday_equities && python -m pytest tests -q)
```

The suite is slow. Prefer running only the tests covering what you
touched unless the owner asks for the full run. One known failure is
listed under gap 6 above.

## Next steps, in priority order

1. **Shuffle-test the group's two-minute cell** — the one survivor with
   no luck check. Nineteen walks, about two and a half hours.
2. **Run the model shortlist at one-minute rows, three minutes ahead.**
   It is the largest unrun block and the only fair test of bigger models
   we have, and the only remaining candidate for pushing past three
   minutes.
3. **Get buy and sell prices for JPMorgan, Walmart and Apple**, then
   repeat the price-definition check across all five names.
4. **Push row spacing below one minute**, since spacing is the one lever
   that has actually worked.
5. **Extend Lilly's luck check from nineteen shuffles toward ninety-nine**
   if a stronger statement is wanted. It will not change the direction.
6. **Fill the 9 unrun five-minute cells** — low value, but it closes the
   grid honestly.
7. **Fix the config-hygiene failure** in gap 6.
8. **Get the 20 proposed decision records ratified or rejected** —
   ADR-0002, ADR-0004, ADR-0005, ADR-0006 and ADR-0059 through
   ADR-0074.
