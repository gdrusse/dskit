# The intraday-equities MIO in plain English

## TL;DR

The MIO is the proposed portfolio decision-maker: at each trading decision, it
would choose how many shares to buy, sell, or leave alone while respecting cash,
cost, and risk limits. The supporting solver framework exists, but this specific
intraday-equities MIO is still a reviewed design—not runnable production code.

## The question it answers

The prediction model says which stocks may rise and how uncertain those forecasts
are. That does not answer the money question: given the positions and cash we
already have, what should we actually own now?

The MIO answers that second question jointly. It does not pick each stock in
isolation, and it does not send orders to a broker.

## What “MIO” means

MIO means **mixed-integer optimization**. “Integer” matters because we trade whole
shares. “Optimization” means the program searches the allowed combinations and
chooses the one with the best projected balance between growth, cost, and risk.

Think of packing a suitcase with a strict weight limit. Every item has a possible
benefit, but some combinations are safer or more useful than others. The MIO packs
the portfolio as one suitcase instead of asking whether each item looks good by
itself.

## What goes in

Before solving, the MIO would receive two separately verified bundles.

The **forecast bundle** describes each eligible stock:

- when the forecast was made and when it expires;
- its holding period;
- a range of plausible joint returns, not just one best guess;
- the estimated chance that the signal is false;
- proof of which model, data, and safety checks produced it; and
- enough cost detail to reproduce any reported net-return number.

The **portfolio state** describes reality now:

- current shares, cash, and account value;
- current quotes and allowed lot sizes;
- available buying power and required cash reserve; and
- exposure limits.

A missing, stale, mismatched, or unverifiable bundle must stop the solve by name.
The program must not quietly guess a value.

## What it does

1. It removes forecasts that did not clear the upstream evidence checks.
2. It rejects trades whose expected benefit cannot cover spread and regulatory
   costs.
3. It reduces an expected return when the signal has a meaningful chance of being
   false.
4. It evaluates stocks together across many plausible market outcomes. A stock
   with modest expected return may still help if it offsets losses elsewhere.
5. It chooses signed share changes: positive means buy, negative means sell, and
   zero means hold.
6. It may deliberately keep cash. The budget is a ceiling, not a command to spend.
7. It returns a target portfolio and proposed share changes. The existing
   production layer remains responsible for permissions, safety breakers, the
   audit ledger, and broker execution.

## The guardrails inside the decision

The proposed program must obey all of these at the same time:

- **Cash and buying power:** it cannot spend money the account cannot use.
- **Exposure limits:** no stock or portfolio total may exceed its approved cap.
- **False-signal capital limit:** the capital-weighted chance of backing false
  signals must stay below an owner-approved ceiling.
- **Tail-loss limit:** loss in the bad simulated outcomes must remain within an
  owner-approved bound.
- **Position-count and minimum-trade rules:** it cannot create an impractical pile
  of tiny positions.
- **No-trade band:** small changes are ignored when trading costs outweigh the
  benefit. This rule belongs inside the solve so it cannot break another limit
  after the fact.
- **Whole shares and exact fees:** share counts are integers, with sell-only fees
  and price-dependent per-share costs applied in the right direction.

The objective is long-run compounded growth, using a conservative fraction of
the full growth-seeking position. It is represented with straight-line pieces so
the HiGHS solver can solve the whole-share problem reliably.

## Small teaching example

These numbers are invented; they are not project settings.

Suppose the account has `$10,000`, and policy allows at most 20% to be newly
deployed:

```text
maximum new deployment = $10,000 × 0.20
                       = $2,000
```

The optimizer considers two `$1,000` positions. Their estimated false-signal
chances are 5% and 15%. The capital-weighted chance is:

```text
weighted false-signal dollars = ($1,000 × 0.05) + ($1,000 × 0.15)
                              = $50 + $150
                              = $200

capital-weighted chance = $200 ÷ $2,000
                        = 0.10
                        = 10%
```

If the approved ceiling were 10%, that pair would sit exactly on the limit. The
real ceiling has not been chosen, so 10% is only an illustration.

The final choice could still be smaller or zero because transaction costs, the
bad-outcome loss limit, current holdings, or a better future use for cash may
make the pair unattractive.

## What already exists

dskit already has the generic `PyomoSolve` doorway and deterministic HiGHS solver
settings. The `pmquant` child already runs a related fractional-Kelly MIO for
prediction-market contracts, including integer lots and exact post-solve checks.
That proves much of the mechanism, but its contract and settlement rules do not
fit equities directly.

The reviewed intraday-equities design is
`docs/plans/2026-09-intraday-equities-mio.md`. It proposes moving the reusable
scenario-and-risk machinery into dskit, then adding an equity-specific
`EquityKellyMIO` node and `run-mio-*.json` documents in this child.

## What does not exist yet

There is currently no runnable `EquityKellyMIO`, no intraday-equities MIO run
configuration, and no completed shadow or paper run. The implementation must not
start until ADR-0111 records and receives approval for the open policy choices,
including the false-signal ceiling, risk limit, cash reserve, position count,
minimum trade, no-trade band, and contribution-aware risk schedule.

After approval, the required order is: build and test the reusable solver base,
refactor `pmquant` onto it without changing behavior, implement the equity node,
produce the calibration artifacts, and then run shadow followed by paper mode.
Live capital remains a separate decision.

## What this protects us from—and what it cannot prove

The MIO is meant to prevent a promising forecast from becoming an unaffordable,
over-concentrated, high-cost, or jointly risky portfolio. It cannot make a weak
forecast true, guarantee profit, remove broker rejection risk, or prove that a
signal will survive the delay between decision and execution.

That is why the honest current status is **designed and reviewed, not implemented
for intraday equities**.
