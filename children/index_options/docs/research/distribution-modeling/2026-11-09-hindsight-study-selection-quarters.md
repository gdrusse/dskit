# Hindsight study of the weekly option MIO on its four selection quarters

Research note, 2026-11-09. Study agent for the owner-directed reverse-engineering study ("look at the results in those four
periods, see if there are any patterns, and what an optimal trading strategy would be ... consider an MIO application ... if a
problem is not tractable, consider another way to solve it"). Scripts, CSVs and the long tables are beside this note under
`hindsight-20261109/` (`00-compare-cells.md`, `01-decomposition.md`, `02-risk.md`, `03-exit.md`, `04-oracle.md`, `05-edge.md`).

## Scope and governance

- **Development set only.** Everything below is read off the four selection quarters 2019Q4, 2020Q1, 2021Q1, 2021Q2 (Path
  A0010, owner ruling 2026-11-05). No run, bundle or result named for 2021Q3 or later, 2022-2023, holdout, prov or test was
  opened (ADR-0298 holdout; 2024 onward sealed). The still-running 2021Q1-sized document was skipped.
- **Everything here is in-sample.** The quarters were opened to be looked at; every pattern, every counterfactual and every
  candidate formulation is a hindsight description of 55 decision weeks and 215 distinct books. Nothing in this note is
  evidence that a formulation works. Section 8 pre-registers how each candidate must be read on other periods.
- **Read-only.** Receipts, run logs, quotes sqlites and the pinned bars were read; nothing under `/home/russell/data` or
  `/mnt/d` was written; no solver was run. All analysis is pandas over the closed receipts (one process, well under 2 GB).
- Inputs: the 36 closed cells `compare.py` tabulates (nine locked dollar formulations on 2019Q4, four on 2021Q1, five on 2020Q1,
  eighteen on 2021Q2 including the nine sizer arms), their per-week decision records, filled and unfilled books, daily
  account curves, each document's `artifacts/mio-quotes/<SYM>.sqlite` (the full quoted chain the week saw, every session),
  and the pinned bars (`mio-wsl-market-inputs-20261105`, 166 names, daily close; no SPY, QQQ or VIX exists in the inputs).
  The one-stage `kelly050`, the two `top100` arms (zero trades) and the 2021Q1 sizer cells have no closed receipt and are not
  in the counts.

## 0. The three numbers that matter

1. **The money is three books.** Of +150,346 settled over all 36 cells, the APA 2020-01-27 put book (held by three 2020Q1
   cells, +35,435 each on average), the GME 2021-01-19 book (+41,488) and the GME 2021-01-11 book (+24,175) carry the sign of
   every winning quarter. 10 of the 19 positive cells are negative ex-top1. The median filled book returns -100% of its
   reserve (a long-premium structure expiring worthless) in 16 of 36 cells.
2. **Horizon and structure, not name, separate winners from losers.** Books with 31-60 DTE: 250 rows, 46% win, +166,332;
   books with 30 DTE or less: 160 rows, 5-29% win, -15,985. Single long options: 172 rows, 9% win, -35,989; multi-leg custom
   books: 172 rows, 46% win, +176,160. Long-premium books paid only when the underlying moved more than 15% to expiry (88 rows,
   48% win, +182,184); under 3% they lost 98% of the time (107 rows, -26,947).
3. **The methods' own valuations are not calibrated at the top.** Sorting filled books by decision-time edge (recorded expected
   profit over reserve), the top quintile (mean edge 184x reserve) won 13% and lost -66,001; the third and fourth quintiles
   (edge 0.08x to 1.6x) made +224,076. Spearman(edge, realized) = 0.06 over 410 rows. The total expected profit the methods
   recorded was $14.3M against $150k realized.

## 1. Inventory

36 closed cells, 500 cell-weeks (370 selected, 49 cash, 65 timeout_skipped, 13 shock_held_breach, 3 existing_cap_breach),
885 book rows of which 410 filled (215 distinct (decision date, symbol, expiry) books; 44 chosen by three or more methods).
Tidy files: `cells.csv`, `weeks.csv` (status, books, credit, reserve, settled P&L, cumulative, funding, shock losses, sizer
fields per cell-week), `books.csv` (every book with legs summary), `curve.csv` (daily equity, cash, reserve, drawdown).

| quarter | cells | selected weeks | filled rows | distinct books | sum settled | cells > 0 | cells > 0 ex-top1 |
|---|---|---|---|---|---|---|---|
| 2019Q4 | 9 | 123 | 115 | 66 | -38,599 | 1 | 1 |
| 2020Q1 | 5 | 55 | 51 | 28 | +106,735 | 3 | 1 |
| 2021Q1 | 4 | 38 | 54 | 35 | +66,083 | 3 | 1 |
| 2021Q2 | 18 | 154 | 190 | 86 | +16,128 | 12 | 6 |

The dollar arms traded 10-23 books a quarter; sizer arms 4-12. Timeouts: 44 of 252 2021Q2 cell-weeks and 14 of 52 2021Q1
cell-weeks were `timeout_skipped` (900 s dollar stage); 48 2021Q2 sizer cell-weeks ended `cash` (the sizer declined).

## 2. Where the money came from and where it was lost

Full tables: `01-decomposition.md`. Counts are small; these are descriptions.

**Concentration.** Per cell, the best book is 71-160% of a positive cell's total in 2020Q1-2021Q2 (`top1_share`). The
distinct-book view: APA 2020-03-20 puts decided 2020-01-27 (+35,435 per cell, three cells), GME 2021-03-19 decided 2021-01-19
(+41,488), GME 2021-02-26 decided 2021-01-11 (+24,175), GME 2021-02-19 decided 2021-01-04 (+13,420), RUN 2021-06-18 decided
2021-05-17 (+6,964), AMC 2021-06-18 decided 2021-05-03 (+4,972, four cells). Pooled by symbol: APA +105,981 in 2020Q1,
GME +71,051 in 2021Q1, AMC +45,838 in 2021Q2; the losers are KSS -19,992 in 2019Q4 (one earnings gap, six cells), GME
-21,976 in 2021Q2 (post-squeeze decay), CSCO -6,429.

**By horizon (DTE at decision).**

| DTE | rows | distinct | P&L | win rate | median return/reserve | P&L per reserve |
|---|---|---|---|---|---|---|
| <= 7 | 82 | 40 | -11,262 | 0.05 | -1.00 | -0.69 |
| 8-14 | 50 | 20 | -791 | 0.14 | -1.00 | -0.04 |
| 15-30 | 28 | 24 | -3,933 | 0.29 | -0.66 | -0.17 |
| 31-60 | 250 | 131 | +166,332 | 0.46 | -0.07 | +0.48 |

The short-dated books are almost all cheap single long options (82 rows under 8 DTE, 81 of them long premium, mean recorded
edge -1.2x reserve: the W1-worst value was small but positive after the shock rows, and the lp screen let them through).

**By structure.** Long-premium (net debit) books: 304 rows, 21% win, +119,951 (+0.57 per reserve), median -1.00. Short-premium
books: 106 rows, 66% win, +30,396 (+0.15 per reserve). Within long premium, single options lose (-35,989, 9% win) and
multi-leg convex custom books win (custom 172 rows +176,160; strangle 33 rows +1,197; vertical 16 rows +8,778, 75% win).
Books with 3-5 legs: 119 rows, +159,841; 1-2 legs: 265 rows, -32,377. The `w1_010z` arm (radius in volatility units) is the
one formulation that trades short premium almost exclusively (0-10% long-premium share): win rate 60-90%, P&L per reserve
+0.01 to +0.02, drawdowns -0.03/-0.54/-0.05, the only positive 2019Q4 cell (+435) and essentially flat elsewhere.

**By the underlying's move to expiry.** Long premium: |move| < 3%: 107 rows, 2% win, -26,947; 3-7%: 56 rows, 9%, -21,249;
7-15%: 53 rows, 26%, -14,037; > 15%: 88 rows, 48%, +182,184. Short premium: > 15% moves still won 76% (41 rows, +40,455)
because the sold strikes sat on the other side; the short-premium losses sit at 3-7% moves (-13,309) where the earnings gap
reached the short strike (KSS).

**By week regime.** The equal-weight basket of the 166 names (the documents' own bars) gives Spearman 0.16 between a week's
mean settled P&L and the basket's absolute 10-session move, 0.08 with trailing 20-session vol. The quarter, not the week,
is the regime: 2019Q4 (basket vol 9-17% annualised, dispersion 1.1-2.9%) lost in every long-premium cell; 2020Q1 paid on the
crash (one book), 2021Q1 on the GME squeeze, 2021Q2 on the AMC run. Realized per-reserve returns on distinct books were
+0.38 / +1.74 / +1.41 / -0.28 by quarter with medians -1.00 / +0.03 / -0.59 / -1.00.

**Within-week co-movement.** 98 cell-weeks held 2+ books; 56% ended all the same sign, which is what independent bets at the
pooled 32% win rate would give (0.56 for two). Pairs of distinct books decided the same week: 483, correlation of
per-reserve returns 0.06, same sign 63%. The bets behave as roughly independent lottery tickets; the correlated weeks are
2019Q4 and 2021Q1 (81%, 79% same sign) and those are mostly joint losses of cheap options expiring together.

**Fills.** Slippage from decision to next-session fill averaged +$2 to +$17 per contract in 2019Q4/2020Q1 and -$20 to -$33
in 2021Q1/Q2 (fills cheaper than the decision price); not a driver. Unfilled books: 475 of 885 rows, mostly
`missing_fill_quote` and `insufficient_fill_size` on far-OTM legs.

## 3. Bounded hindsight oracle

`h04_oracle.py`, summary `04-oracle.md`. For every decision week the universe is every long single contract the quote rule
admits (bid > 0, ask > bid, sizes >= 1, relative spread <= .5) in the quoted (symbol, expiry) groups with 1-60 DTE: 72,000 to
89,000 contracts in ~1,100 groups per week in 2021Q2. Each is bought at the next session's ask, held, and settled at the
expiry close over the basis factor exactly as the ledger does. Short structures are not enumerated: with the reserve rule
(max loss plus the 20% sold-notional floor) and the no-naked-call row the ex-post best short book is itself a MILP per
week, and the long-only bound already answers the question.

- **The full oracle is useless as a target and informative as a fact.** The best knapsack under 25% of funding and fill
  depth made 50x to 100x the weekly budget every week (2021Q2: $57k-$260k on $2.5k-$4.9k). The universe always contains a
  10x-100x weekly winner at displayed depth; that is the lottery the long-premium formulations are implicitly buying.
- **Base rates (mean over the quarter's weeks; `04-oracle.md`).** 61,000-78,000 eligible long contracts a week in
  ~1,080-1,110 groups. Share ending positive 34% / 39% / 34% (2019Q4 / 2020Q1 / 2021Q2), share expiring worthless 31-33%,
  median return per premium -0.23 / -0.20 / -0.18 in every quarter. The mean return per premium, equal weight over
  contracts, was -0.03 / +0.78 / -0.16, and an equal-weight long portfolio made +0.006 / +0.051 / 0.000 per dollar per week.
  Long premium at the ask was roughly fair outside the crash quarter, where everything OTM paid (31-60 DTE slice +1.38 per
  premium, > 10% OTM slice +0.80). The 31-60 DTE slice beat the under-8-DTE slice in all three quarters (+0.07 vs -0.10,
  +1.38 vs -0.10, -0.15 vs -0.16): part of the horizon effect in section 2 is a property of what the market offered, not of
  the selection.
- **The selections beat the universe only in 2020Q1.** Filled books' mean return per reserve against the universe's mean
  return per premium: 2019Q4 -0.07 vs -0.03 (win 24% vs 35% positive); 2020Q1 +2.10 vs +0.78 (long-premium rows +3.06; win
  55% vs 39%); 2021Q2 -0.28 on distinct books vs -0.16 (win 27% vs 34%). In two of three quarters with a universe the
  selection was no better than a random eligible long contract at the ask, with a lower hit rate; in the crash quarter it
  was 2.7x better. 2021Q1's universe is in `04-oracle.md` when its run completes (see section 9).
- **Same-names oracle.** Restricting the knapsack to the names the method selected that week, the 2021Q2 dollar arms would
  have made $100k-$620k over the quarter against -$5.7k to +$10.4k realized; same (symbol, expiry) groups: $10k-$440k.
  The names and expiries the methods picked contained large winners (AMC, GME, RUN, NVDA); the structures and strikes chosen
  captured a few percent of them, and the weeks that could not be solved (timeouts, cash) sat on $70k-$245k full-oracle weeks.
  Pooled capture ratio, actual P&L over the same-groups oracle: -0.08 (2019Q4), +0.06 (2020Q1), +0.004 (2021Q2). The
  selection problem the data expose is inside the group (which strikes, which structure, how long-dated), not which name.

**Ex-post growth-optimal fraction** (each book a bet of fraction f of capital on its reserve, f* maximising mean
log(1+f*r) over the cell's realized per-reserve returns): pooled over 215 distinct books f* = 0.054 with growth 0.009 per
book; by quarter 0.023 / 0.46 / 0.16 / 0.00. The dollar arms staked 0.02-0.13 of funding per book, the sizer arms
0.07-0.21. The pooled stake was near the hindsight Kelly fraction; the point is that f* is set by 2020Q1 and 2021Q1 and is
zero in 2021Q2, so no fixed fraction is right across quarters and the .25/.5 Kelly multipliers are not where the problem is.

## 4. Risk and hedging

`02-risk.md`, `03-exit.md`.

**Drawdowns are marked-up lottery tickets decaying, not adverse moves.** The -0.80 to -0.92 drawdowns (2019Q4 nominal and
w1_003; 2021Q1 w1_003, name25, component25) run from a marked peak to the settlement of the same books: 2021Q1 w1_003 was
marked at $372k equity on 2021-01-27 and settled at $50k; component25 peaked at $135k marked and ended at $11k. The daily
unrealized mark (equity minus cash) peaked at 11x the final P&L in 2021Q1 w1_003 and 12.6x in 2021Q2 w1_010.

**Index hedging would not have helped.** Of the 94 book rows losing more than $500, the basket (beta-1) component is 15% of
the underlying's own move; the ten short-premium big losers moved 98% on average against a 6% basket move (GME, AMC, KSS
earnings). The losses are idiosyncratic or pure premium decay; an index put spread or short-delta overlay addresses neither,
and no index option quotes exist in the inputs to price one. Within-name wing protection is already in the solver's action
space (every quoted strike, both rights).

**Exits: the executable-side counterfactual.** From each book's own quotes, the liquidation P&L on every session between
fill and expiry (sell longs at the bid, buy back shorts at the ask, 0.65 per contract):

| rule (per book, freed cash not reinvested) | 2019Q4 | 2020Q1 | 2021Q1 | 2021Q2 | all | cells better / worse |
|---|---|---|---|---|---|---|
| hold to expiry (actual) | -38,599 | 106,735 | 66,083 | 16,128 | 150,346 | |
| take profit at 1x reserve | 11,267 | 7,970 | 111,396 | 2,160 | 132,793 | 10 / 16 |
| take profit at 2x reserve | 12,382 | 33,011 | 133,989 | 17,267 | 196,649 | 10 / 13 |
| take profit at 3x reserve | 9,396 | 69,862 | 129,601 | 19,188 | 228,047 | |
| stop loss at -0.5x reserve | -28,327 | -25,406 | 63,123 | 18,318 | 27,708 | |
| tp 2x + sl -0.75x | 12,821 | 10,946 | 93,698 | 17,976 | 135,441 | |

Three facts. (i) Stop-losses destroy the strategy: the convexity is the whole return and a stop sells it at the bid.
(ii) Wide take-profits (2x-3x reserve) add +46k to +78k over hold-to-expiry, improve 3 of 4 quarters, but 2 of 3 of the
gain is GME 2021Q1 (the 2021-01-04 book was closable at $192k against $13k settled; 2021-02-16 at $103k against -$2.3k).
(iii) 209 rows ended as total losses; 55 of them were closable at a gain at some point, 30 at >= 1x reserve. An
account-level counterfactual on the mid marks (`02-risk.md` section 2) shows +$530k at theta = 1; that number is an artefact
of mid marks on wide-spread OTM options and is reported only to be dismissed. The executable table above is the reference.

**Shock gates.** The shock rows bound in 178 of the 342 selected weeks that carry a shock block (slack under 5% of the limit). Binding weeks returned
+0.18 per reserve, slack weeks +0.16, win rates 20% vs 42%: the gate shaped the books (fewer, cheaper, further OTM) without
separating good weeks from bad. Against realized co-moves the gate was neither loose nor tight; it was irrelevant, because the
losses were premium decay and single-name gaps that no basket shock describes.

**Sizer copula.** The 2021Q2 sizer arms' weeks with reserve_ratio <= .25 (sizer cut stage 1 by 75% or more): 17 weeks, 6%
win, -1,169; ratio .25-.5: 12 weeks, 83% win, +6,883; ratio > 1 (scaled up): 8 weeks, 50% win, +5,354. The sizer's cuts fell
on weeks that lost; scaling each week's P&L back to stage-1 counts gives -7,863 for `shrunk` against +661 sized and -917 for
`comp25` against +4,189. Eight arms, one quarter, 51 sized weeks: suggestive that the sampled-settlement sizer sees something
the W1-worst dollar stage does not (it prices the long tail's thinness), not more.

## 5. Sizing and positions

- **P&L per committed reserve against books per week** (selected cell-weeks): 1 book: +0.11 (100 weeks, 44% win); 2
  books: +0.24; 3 books: +0.25; 4 or more: -0.14 (55 weeks, 16% win). More books per week diluted into weaker groups; the
  winners were never the fourth book.
- **Fewer, larger vs more, smaller.** Within the data the answer is "fewer and more convex": the winning books are 3-5 leg
  long-premium structures at 31-60 DTE on one or two names a week. The counterfactual of concentrating the weekly budget into
  the single highest-edge book is not available because edge did not rank outcomes (section 0.3): the top-edge quintile lost.
- **Group screen.** The `lp` screen drops groups with zero relaxed value; the records do not keep the screened list, so
  "did the kept groups contain the winners" cannot be answered from receipts. The same-names oracle says the selected names
  contained them; the same-groups oracle says the chosen expiries did too; the structure within the group is where the
  capture was lost (section 3). The `top100` arm (keep the 100 best relaxed groups) traded nothing in any week it ran.
- **Account dynamics.** Funding grows by $1,000 every 14 days; the weekly budget is 25% of funding; realized gains raise the
  budget. The 2021Q1 cells' $34k-$36k came from books sized on a $10k-$13k account; after the GME settlements the budget was
  $20k-$90k a week and the later books (March GME, AMC) lost $4.6k-$10k each on it. Position size followed account size into
  the post-squeeze decay; this is the one sequential structure in the data (section 7).

## 6. Candidate formulations, ranked

Each is a change to the MIO objective or its inputs, or a stage-two rule; no hard caps (owner rule: price the cost in the
objective). "Tractability" is against the known envelope: W1-worst dollar MILPs solve in 1-5 min at ~10k variables / ~10k
constraints per week (the 2021Q2 example: 10,745 vars, 10,332 rows, 57 s at 1% gap); the full-universe one-stage Kelly times
out at 2,400 s; the sizer's stage 2 solves in 2-3 s at 2,003 vars.

### C1. Horizon-and-structure carry charge in the dollar objective (stage 1)

- **Hypothesis.** The W1-worst value approves cheap short-dated single options because a few cents of robust value on a
  $0.05 option clears the screen while the forecast's tail mass is the only thing paying it. Charging each long leg's premium
  a cost that rises as DTE falls and charging each uncovered single leg a fixed spread makes the solver prefer the 31-60 DTE
  multi-leg convex books that carried the realized P&L (section 2) and starves the under-15-DTE single options (-15,985 on
  160 rows) without a DTE cap or a leg-count cap.
- **Form.** `value_g = credit_g - W1cost_g - sum_legs c(DTE_g) * premium_leg * x_leg - s * singleness_g`, with
  `c(DTE) = c0 * max(0, (D0 - DTE)/D0)` (c0 ~ 0.3-0.5 of premium at DTE 0, D0 = 30) and `singleness_g` the net long
  premium not offset by a sold or bought leg of the other right within 10% of spot (linear in x with one auxiliary per
  group). All linear in x: the MILP keeps its size; rho, shock rows, caps unchanged.
- **Method keys.** Reuse `risk_charge {"measure", "price"}` with a new measure beside `mad` ("carry", price c0, horizon
  D0) and an optional `single_leg_charge s`; nothing else changes. Works with `sized` arms unchanged (stage 1 only).
- **Tractability.** Same program class and size; the charge is a coefficient change. Solve times unchanged.
- **Pre-registered reading (holdout, like-for-like weeks).** Quantities: share of filled books under 15 DTE, share of
  single-option books, P&L per committed reserve, win rate, ex-top1 P&L. Win = P&L per reserve above the paired baseline cell
  (`w1_010_shock10_lp` and its `_sized` arm) in at least 3 of 4 holdout quarters with the ex-top1 P&L not lower in any, and
  the under-15-DTE share below 15%. Weeks the baseline skipped (timeout, cash) are excluded from the pairing.
- **Artefact risk: moderate-high.** The DTE result is 160 vs 250 rows across four quarters and the 31-60 DTE slice of the
  universe was not better than the rest in 2021Q2 (section 3); the structure result is cleaner (single options lost in every
  quarter). The charge is the cheapest change to test.

### C2. Shrink extreme group values toward the universe (a calibration cost on the valuation itself)

- **Hypothesis.** The recorded edges are not calibrated at the top: books valued above ~2x their reserve won 13% and lost
  the most (section 0.3; $13.3M of recorded expected profit on 54 books in 2021Q1). The solver's objective should pay a cost
  that grows with how implausible a group's value is relative to what the market offers, so that the program spends the budget
  on the 0.1x-1.6x edge band where the money was made rather than on forecast-tail artefacts.
- **Form.** Replace `value_g` by a concave, piecewise-linear transform of the robust value per reserve:
  `u(value_g / reserve_g)` with slope 1 up to k1 (~1.0), slope 0.3 to k2 (~3), slope 0 beyond, multiplied back by
  `reserve_g` (the standard epigraph with two breakpoints per group: two continuous variables and three rows per group,
  ~2,200 extra rows at 1,100 groups). Equivalently the W1 radius could be made to grow with the forecast's tail mass, but the
  value transform needs no forecast change and is already how a bounded-rationality cost is usually priced.
- **Method keys.** New `value_transform {"breaks": [1.0, 3.0], "slopes": [1, .3, 0]}` beside `risk_charge`; reuse
  `TransportActionScore` to report both raw and transformed values. The sizer's `shrink` (0.5 pooled to the reserve-weighted
  mean) is the stage-two cousin and lost in 2021Q2 (-7,863 at stage-1 counts), so this belongs at stage 1.
- **Tractability.** Adds ~2 vars and 3 rows per group; the MILP stays in the 1-5 min envelope. No new integer variables.
- **Pre-registered reading.** Same pairing as C1; quantities: Spearman(edge, realized) per quarter, share of books with
  edge > 2x reserve, P&L per reserve, worst book. Win = Spearman not lower and P&L per reserve higher in 3 of 4 holdout
  quarters, with no quarter's worst book below the baseline's.
- **Artefact risk: moderate.** Miscalibration at the top is visible in all four quarters, but the breakpoints are read off
  these 410 rows; the test must use the stated breaks, not tuned ones.

### C3. Stage-two exit rule: wide take-profit at the executable side, no stop-loss

- **Hypothesis.** Hold-to-expiry leaves the convexity's realized peak on the table when it comes early (GME 2021-01-04 book:
  $192k closable, $13k settled). A take-profit at 2x-3x reserve added +46k/+78k over 410 rows and improved 3 of 4 quarters
  (section 4); any stop-loss destroys the return. This is a ledger rule (the option ledger already has `close_rule exit`), not
  a program change, and it is the only genuinely sequential decision the data expose.
- **Method keys.** `exit {"take_profit_multiple": 2.0 or 3.0, "side": "executable"}` on the study; the ledger's exit path
  prices the close at bid/ask. No solver involvement; the freed reserve re-enters the next week's budget (not modelled here).
- **Tractability.** Trivial.
- **Pre-registered reading.** On holdout quarters: per book, realized P&L under the rule against the hold-to-expiry
  counterfactual from the same quotes; win = the rule's total above hold in 3 of 4 quarters and not lower by more than 10% in
  the fourth, with the share of books closed early between 5% and 25%. Both 2x and 3x are declared now; whichever is
  reported must be the one named before the holdout is opened (3x is this note's pick: it fired on 10% of rows).
- **Artefact risk: high.** Two GME books are most of the gain; outside 2021Q1 the gain is +$7k to +$37k per quarter on
  $10k-$16k contributed, still positive but thin. A candidate to test, not to build the strategy on.

### C4. Volatility-unit radius with the Kelly sizer (`w1_010z` stage 1 + `sized` stage 2)

- **Hypothesis.** The `z` arm (W1 radius in reference-scale units) is the only formulation that never had a large loss
  (worst book -$859; drawdowns -0.03/-0.54/-0.05) and the only one positive in 2019Q4; it trades short premium with 60-90%
  win rates at +0.01 to +0.02 per reserve, i.e. it is under-sized rather than wrong. The sizer stage scales stage-1 counts by
  sampled-settlement Kelly and in 2021Q2 its cuts fell on the losing weeks (section 4). The combination is a low-variance
  arm to pair with a convex arm, and both halves already exist.
- **Method keys.** Existing: `radius_scale "reference_scale"`, rho 1.677, shock10, plus `SIZER` defaults (fraction .25,
  factors market, loadings trailing). Nothing new.
- **Tractability.** Both stages already solve inside their limits.
- **Pre-registered reading.** Holdout quarters: P&L per reserve, worst book, drawdown, against `w1_010z_shock10_lp`
  unsized. Win = higher P&L per reserve in 3 of 4 with drawdown not worse than -0.25 in any.
- **Artefact risk: moderate.** 2020Q1's -0.54 drawdown shows the short-premium arm's crash exposure; the 2021Q2 sizer
  evidence is eight arms on one quarter.

Not proposed: a learned regime component. The lottery paid in 2020Q1, 2021Q1 and the AMC weeks of 2021Q2 and lost in 2019Q4
and the rest; four quarters cannot support a vol- or dispersion-conditioned exposure rule, and the week-level correlations
with basket move and vol are 0.16 and 0.08. The owner's "costs not caps" rule also argues against a switch; C1's charge can be
scaled by trailing vol later if a holdout read shows the DTE effect is regime-dependent.

## 7. What an RL agent could add, and why it cannot here

The weekly decision is 55 draws of a heavy-tailed outcome dominated by three books; a policy learned on them would learn
"buy GME and APA". The one sequential structure in the data is within-quarter: (a) exits, where the mark path of an open
book carries information the hold-to-expiry rule ignores (section 4), and (b) budget following account size into the decay
after a squeeze (section 5). Both are one- or two-parameter threshold rules (a take-profit multiple; a budget that follows
contributions rather than realized gains, which is a `funding` definition change in `FundedOptionAccount`), testable by
pre-registration, and an RL agent would add fitting capacity without adding information. If a later holdout read shows the
exit rule's gain is real and horizon-dependent, a two-state policy (days held x mark multiple) is the most that 55 x ~40
daily observations can support, and it should be fit on the development quarters only and read once on the holdout.

## 8. Tractability and the other way to solve it

- C1, C2 are coefficient or small epigraph changes to the existing W1-worst MILP: same size class, same 900 s limit, no
  decomposition needed. Both are compatible with the `sized` two-stage path.
- The full-universe one-stage Kelly remains intractable at 2,400 s and `top100` declines every week because relax-fix cannot
  certify 2,000 free actions. The data say the two-stage path (dollar MILP selects, sampled Kelly sizes) is the right
  decomposition: stage 1 is where the selection errors are (horizon, structure, calibration: C1, C2), stage 2 already sizes
  well on the one quarter it ran. A restricted-universe Kelly is not needed; if one is wanted, restrict by declared cost (keep
  groups whose transformed value per reserve clears a stated threshold, C2's `k1`) rather than by count.
- C3 needs no solver. C4 needs none new.
- The oracle itself (section 3) is a knapsack, exact in seconds; the short-structure oracle would be a per-week MILP over the
  same ~70k items with the reserve rows and could reuse `TransportPortfolioSolve` with realized payoffs in place of the
  forecast losses (radius 0, one atom): a day's work if wanted, no new solver.

## 9. Limits and what could not be done

- In-sample on four owner-opened quarters, 36 cells sharing 215 distinct books; three books carry the sign. Nothing here is
  out-of-sample evidence.
- No index quotes in the inputs: the hedge question is answered on moves, not priced. No VIX; the basket vol is the regime proxy.
- The lp group screen's dropped list is not in the receipts; "did the kept groups contain the winners" is answered only via
  the same-names / same-groups oracles.
- The ex-post oracle is long-only; short structures (and therefore the `z` arm's universe) are not bounded.
- Exit counterfactuals ignore reinvestment of freed reserve and the next-week budget effect; the mid-mark version is dismissed.
- The `kelly050` one-stage arm, both `top100` arms and the 2021Q1 sizer cells are absent (no closed receipt or still running).
- The methods' recorded `expected` is W1-worst for dollar arms and the nominal target for sizer arms; section 0.3 pools them.
- The 2019Q4 cells are the v2 combined-chain priced documents; 2021Q1's nine come from the 900 s set only.
