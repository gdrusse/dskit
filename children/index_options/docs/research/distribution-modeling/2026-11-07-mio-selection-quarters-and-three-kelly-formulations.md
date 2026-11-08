# MIO selection quarters: what the nine formulations and half-Kelly did, and three formulations to add

**Status:** research note, 2026-11-07 (Claude Fable 5.1). Read-only: no solve was run, no holdout quarter (2021Q3 onward) or 2022-2023 file was opened, no Path row changed. Numbers are selection-quarter development evidence under Path A0010 (the owner's 2026-11-05 ruling that the four selection quarters may be read). Nothing here is a profitability claim.

## Question

The locked plan (A0010) runs nine dollar formulations on 2019Q4, 2020Q1, 2021Q1 and 2021Q2, and the owner picks three for 2021Q3-2023Q4. ADR-0299 added two fractional-Kelly candidates (`kelly025_lp`, `kelly050_lp`) beside them. What do the cells that exist say, what did the first real Kelly quarter show, and which three further formulations would improve on both families, testable on the same four quarters?

## 1. Where the four-quarter test stands (2026-11-07 morning)

| Quarter | Priced on WSL (readable) | Solved on the Mac, not yet priced | Not solved |
|---|---|---|---|
| 2019Q4 | none. The v1 WSL documents failed every method (`unsettleable selected book`: the one-file-per-symbol chain ended 2019-12-31, before January expiries). v2 documents with the combined chain exist (`mio-parallel/docs-2019q4`, built 2026-11-06 13:25) | all 9 (`mac-results/hand3/2019q4-*`); pricing jobs queued in `priced/jobs-rest-ready.json` | — |
| 2020Q1 | 5 of 9 closed (`2020Q1-rest`: w1_010z, component25, name25, shock05_book; `2020Q1-fine`). The four-method document (`2020Q1`) ran every method to its metrics but did not close (no `carry.json`); its four results below are **provisional** | — | — |
| 2021Q1 | 4 of 9 (`priced-runs/2021q1-rules-900`: w1_003, mad050, component25, name25) | 5 (w1_010_shock10, nominal, w1_010z, shock05_book, fine); the pricing run `2021q1-rules-t25` died mid-document at 2026-11-07 00:29 (no process alive now); `2021q1-fine-t25` queued | — |
| 2021Q2 | all 9 (`2021q2-rules-t25`, `2021q2-fine-t25`) | — | — |
| Kelly (ADR-0299) | none priced | `kelly050_lp` 2021Q1 finished on the Mac 2026-11-07 06:25 (27,514 s); 2021Q2 was killed at week 1 (exit 143) | `kelly025_lp` never ran (its LP did not finish in 1,500 s) |

Nothing is running on WSL or the Mac as of this note (no `dskit.pipeline` or `mio_worker` process; the Mac RAM disk holds the Kelly result and the two holdout bundles). So the "wide test" is 18 readable cells of 36, plus 4 provisional.

## 2. Results on the readable cells

Each cell is one fresh account: $10,000 plus $1,000 every 14 days (about $16,000 a quarter), 166 locked names, the tuned `fixed_loc` forecasts (5 seeds, linear pool), next-session fills at displayed depth, hold to expiry, solver 900 s / gap 1% / $50. `Settled` is net P&L of every book opened in the quarter, including later expiries; `DD` is the marked (daily-mid) maximum drawdown of NAV; `ex-top1` is settled P&L without the single best book; `tmo` counts `timeout_skipped` decision weeks of 13-14.

### 2020Q1 (the COVID crash quarter)

| Formulation | Settled | DD | Books | Wins | ex-top1 | tmo | Note |
|---|---|---|---|---|---|---|---|
| w1_010_shock10_lp | **+51,154** (prov.) | -0.27 | — | — | — | 1 (03-30) | document unclosed |
| w1_003_shock10_lp | -6,860 (prov.) | -0.52 | — | — | — | 4 (03-09..03-30) | |
| w1_003_shock10_mad050_lp | +29,978 (prov.) | -0.26 | — | — | — | 2 | |
| nominal_lp | -9,061 (prov.) | -0.87 | — | — | — | 0 | 552 contracts |
| w1_010z_shock10_lp | -616 | -0.54 | 11 | 7 | -786 | 0 | 11 credit books: effectively a small short-premium book |
| w1_010_component25_shock10_lp | +29,786 | -0.25 | 12 | 5 | -2,457 | 2 | 1 `existing_cap_breach_cash` week |
| w1_010_name25_shock10_lp | +30,080 | -0.25 | 12 | 6 | -2,164 | 1 | |
| w1_010_shock05_book_lp | -16 | -0.12 | 4 | 1 | -830 | 3 | 7 of 14 weeks `shock_held_breach`: blocked, not deciding |
| w1_010_shock10_fine_lp | +47,501 | -0.28 | 12 | 9 | +5,684 | 1 | the only cell with a positive ex-top1 and a majority of winners |

### 2021Q1 (the squeeze quarter: GME/AMC in January)

| Formulation | Settled | DD | Books | Wins | ex-top1 | Worst | tmo |
|---|---|---|---|---|---|---|---|
| w1_003_shock10_lp | +34,166 | **-0.91** | 14 | 5 | +9,991 | -4,642 | **7 of 13** |
| w1_003_shock10_mad050_lp | +417 | -0.37 | 8 | 3 | -1,039 | -927 | 2 |
| w1_010_component25_shock10_lp | -4,808 | -0.92 | 13 | **0** | -4,803 | -2,200 | 3 |
| w1_010_name25_shock10_lp | +36,309 | -0.89 | 19 | 3 | -5,179 | -2,416 | 2 |
| kelly050_lp (Mac, unpriced) | — | — | 1 book in 6 weeks | — | — | — | **12 of 13** (see §3) |

### 2021Q2

| Formulation | Settled | DD | Books | Wins | ex-top1 | Worst | tmo |
|---|---|---|---|---|---|---|---|
| w1_010_shock10_lp | +1,230 | -0.39 | 15 | 4 | -6,058 | -3,962 | 3 |
| w1_003_shock10_lp | -4,504 | -0.68 | 23 | 3 | -11,468 | -2,945 | 2 |
| w1_003_shock10_mad050_lp | -5,723 | -0.44 | 12 | 4 | -5,883 | -2,601 | 3 |
| nominal_lp | -3,736 | -0.45 | 14 | 1 | -5,997 | -1,501 | 0 |
| w1_010z_shock10_lp | +170 | -0.05 | 10 | 6 | +7 | -162 | 0 |
| w1_010_component25_shock10_lp | **+10,424** | **-0.14** | 13 | 6 | +1,142 | -991 | 3 (+2 cap-breach weeks) |
| w1_010_name25_shock10_lp | -3,233 | -0.39 | 20 | 6 | -7,943 | -2,856 | 0 |
| w1_010_shock05_book_lp | -982 | -0.34 | 12 | 3 | -7,534 | -3,365 | 1 (+4 `shock_held_breach`) |
| w1_010_shock10_fine_lp | +3,885 | -0.41 | 17 | 5 | -2,137 | -1,416 | 2 |

### What the cells say

1. **The sign of a quarter is set by one or two books.** In 15 of 18 readable cells, `ex-top1` has the opposite sign to the total or is a small fraction of it. 2020Q1's +30k to +51k is long premium held into March (the fine grid's 9 of 12 winners is the one exception with breadth). 2021Q1's two positive totals came with -0.89 and -0.91 marked drawdowns: the account was nearly gone at mark before one book paid. That is the pattern the calibration gate reported (the objective's `expected` anti-ranks realized P&L), on new forecasts and a new list.
2. **Hit rates are 20-50%** (median 5 wins of 13 books). The dollar objective sizes to its caps, so a book's size says nothing about the solver's confidence in it.
3. **Timeouts are concentrated in high-volatility weeks** (March 2020, January and March 2021, June 2021) and in the radius-0.03 shock formulation (7 of 13 weeks in 2021Q1, 4 of 14 in 2020Q1). A skipped week is cash, so the loser-by-timeout can look safer than it is.
4. **Two formulations hardly decide.** `w1_010z_shock10_lp` (vol-scaled radius) is within +/-$620 of cash in both quarters with a -0.05 drawdown in 2021Q2: the radius 1.68 in reference-scale units is too wide. `w1_010_shock05_book_lp` is blocked by its own held-book shock in 7 of 14 and 4 of 14 weeks.
5. **The 25% group and name caps are the only consistent drawdown reducers where they ran** (-0.25 / -0.25 in 2020Q1, -0.14 in 2021Q2 for component25) but component25 was 0 for 13 in 2021Q1 at -0.92. The 25-point grid beat the 13-point grid in both quarters where both exist (2021Q2 +3,885 vs +1,230; 2020Q1 +47,501 vs +51,154 provisional, with far more winners).

None of this ranks the nine: 2019Q4 and half of 2021Q1 are missing, and the 2020Q1 four are from an unclosed document.

## 3. Fractional Kelly as built (ADR-0299): the first real quarter

`kelly050_lp` on 2021Q1, the Mac, gap 10% / 2,400 s, the 20% short-notional floor, `relax_fix`, from the action file (`/Volumes/mio-ram/out/hand3/2021q1-kelly-t40-kelly050_lp.json`; counts, times and certificate outcomes only):

| Week | Outcome | Books / contracts | Seconds |
|---|---|---|---|
| 01-04, 01-11, 01-25, 02-08, 02-16, 03-01, 03-22 | `relaxation_failed`: the LP relaxation itself did not finish | 0 | 2,438-2,449 each |
| 01-19, 02-01, 03-08, 03-15, 03-29 | `declined`: a support incumbent was found but too many actions stayed free to certify; above the gap, so `timeout_skipped` | 1 book of 147 / 25 / 113 / 144 / 176 contracts | 1,192-1,736 |
| 02-22 | `relaxation_certified` at 4% | 1 book, 196 contracts | 2,275 |

So 12 of 13 weeks traded nothing, 7.6 Mac-hours bought one traded week, and every week that had an incumbent put the whole stake into one book of 100-200 contracts. The program had 300k-430k rows and 290k-360k integer variables (every group's every atom carries payoff and dispersion rows). This confirms the three defects ADR-0299 itself measured and left open:

- **Scale.** The joint program is sized by candidates (1,100 groups x 140 items) and the LP alone takes 1,150-2,400 s; the floor's extra reserve rows pushed it past the limit.
- **Concentration.** The idiosyncratic risk enters only as a second-order charge on each name's own dispersion, assumed independent across names; a growth objective then puts everything into the highest edge-to-dispersion book. The ADR measured the stake at 1.4-2x the exact CRRA stake (the labels 1/4 and 1/2 are nominal).
- **Mean fragility.** Kelly sizes on the forecast mean, and the one objective-versus-outcome test the project has says that mean anti-ranks outcomes. A fixed fraction shrinks every stake equally; it does not distinguish a reliable edge from noise.

## 4. Three formulations to add (all at f = 1/4; one optional f = 1/2 arm)

The common design move: **separate book selection from size.** Selection stays with the primary dollar formulation (`w1_010_shock10_lp`), whose radius and shock gate are the project's calibrated robustness tools and whose solve finishes. Size becomes an exact growth objective on sampled joint outcomes of the few selected books. Reuse found by `tools/sweep/sweep` (70 refs, 134 worktrees): `ScenarioUtilitySolve` (`dskit/pipeline/libs/pyomo.py:1722`, the tangent-envelope Kelly MILP over scenario returns with held inventory, cash, buying power and an optional CVaR block; worked subclass `EquityKellyMIO` in `children/intraday_equities/.../nodes_capital.py:979`), `WassersteinDual` (`pyomo.py:3289`), `black76_implied_vol` (`dskit/pipeline/option_pricing.py:203`), the study's own `_prepare` grid and masses, `_betas`/`_returns`, `_units` (the available-size primitive unit) and `_floor`. No copula, sample-average or select-then-size seam exists anywhere in the repo.

### F1 `kelly025_sized_lp`: select with the primary, size by sample-average Kelly

**Stage 1.** The locked `w1_010_shock10_lp` program, unchanged (rho 0.10, the four-move 10% shock gate, the 25% weekly budget, the floors, the `lp` screen). Its selection is read for its **books and leg ratios only**: each selected book `b` becomes a primitive unit `u_b` (legs divided by their gcd, the same unit the available-size fill uses) with a multiplier `k_b` in `0..K_b`, `K_b` the depth-limited count (quote capacity per leg over the unit). The stage-1 quantities are discarded.

**Stage 2 (new, a `ScenarioUtilitySolve` subclass).** Instruments: the selected units (new) and every open book (held, not saleable). Price per unit: its floored reserve `R_b` (the 20% short-notional floor applies). Payoffs: `N` joint scenarios of terminal profit per unit, `pi_b(s) = credit_b + payoff_b(S_a(s))`, with
- market factor `Z_s ~ t_4` scaled to the week's horizon exactly as ADR-0299 estimates it (trailing 60-session basket volatility, horizon = candidate-weighted mean DTE);
- name outcome through a one-factor Gaussian copula: `u_a,s = Phi(r_a Z~_s + sqrt(1 - r_a^2) e_a,s)`, `r_a = clip(beta_a sigma_m / sigma_a, 0, 0.95)` from the same 60-session window, `e` standard normal, `Z~` standardised;
- settlement `S_a(s) = F_a^-1(u_a,s)`: the inverse of the book's own forecast CDF on its prepared strike grid (the masses the dollar program already uses; piecewise-linear between atoms), so every scenario respects the forecast marginal and the sampled law's mean equals the forecast's (checked by test, as ADR-0299 checks its centring);
- `N` = 1,000, seed = a hash of the decision date (deterministic; a second seed must move no stake by more than one unit, by test).

Wealth per scenario: `W_s = funding + sum_o pi_o(s) + sum_b k_b pi_b(s)` (open books at their own sampled outcomes, so a new book that hedges an open one is credited). Objective: `max (1/N) sum_s u(W_s)`, `u` CRRA with `gamma = 1/f`, `f = 0.25`, through the existing tangent envelope (32 knots, core 2, `wealth_lo` 1% as ADR-0299). Rows: the cash rule `sum_b k_b R_b <= (1 - 0.05)(funding - open reserves)`, the wealth floor, nothing else: no budget, name or component cap (the owner's "remove the hard caps" holds; the risk is priced by the utility over real joint tails, not by a charge). Size: about 20-60 integer variables and `N x 32` tangent rows: seconds.

**What it fixes.** Exact utility on sampled joint outcomes, so no second-order charge and none of the measured 1.4-2x overshoot; idiosyncratic and market tails are in the same law (no independence or perfect-correlation assumption; a 150-contract single book is penalised by its own tail); open books enter; `f = 1/4` is feasible; the week costs the primary's 1-5 minutes plus seconds, so the only timeouts are stage 1's.

**Limits, stated.** Stage 1 is a candidate restriction: Kelly cannot pick a book the dollar program did not. The copula is one factor with a Student-t market and normal idiosyncratic shocks; the forecast marginal already contains part of the market move (ADR-0299's stated limit), so market variance is partly double-counted (conservative for size). The SAA certificate certifies the sampled program, not the continuous law.

**Code.** `ledger_studies.py`: a method key `sizer` (`{"base": <the w1_010_shock10_lp spec>, "fraction", "scenarios", "n_tangents", "core", "wealth_floor", "cash_buffer", "beta_lookback", "mean": "nominal"}`; a `sizer` method may declare no other objective key; the document runs it alone like a Kelly method), the weekly step (stage-1 solve, unit extraction, sampler, stage-2 solve, books), and the `recorded`/`derived` replay routes carrying both certificates. New module `index_options/kelly_sizer.py`: the sampler (about 150 lines) and `BookKellySizer(ScenarioUtilitySolve)` (about 200 lines). Tests: synthetic week, seed stability, open-book hedge credit, k-invariance under a name split, replay parity, the dollar digests untouched. Documents: plain WSL documents beside the nine, as ADR-0299's are (the parallel queue pins A0010's nine ids).

### F2 `kelly025_sized_w1_lp`: F1 sized on the robust mean

Identical to F1, except each selected unit's scenario profits are recentred so their sample mean equals the unit's **W1-worst expected profit at rho 0.10** (`WassersteinDual` on the unit's fixed loss vector: a scalar per unit, computed once a week). Because stage 1 selects under the same radius, every selected book has a positive robust value, so F2 never sizes on a negative mean; it sizes every book smaller than F1, most for books whose edge is near the ball's edge. This is distributionally robust Kelly built from the project's own estimation-risk tool, and it is the direct answer to the measured anti-calibration of the forecast mean. Code: F1 plus about 40 lines (the dual value per unit and a `"mean": "w1_worst"` setting). Comparing F1 and F2 on the same four quarters isolates what robustness of the mean buys inside a growth objective.

### F3 `kelly025_top100_lp`: the one-stage joint Kelly, restricted to the 100 best groups

ADR-0299's program as it stands (f = 1/4, the all-atom dispersion charge, the 16 moves, the floor, `relax_fix`, gap 10%, 2,400 s), with one added rule: after the `lp` screen, keep only the `K = 100` groups with the largest standalone relaxed value (`kelly.max_groups`). Evidence that this loses little: the ADR's real 250-group measurement had 33 active groups in the relaxation at f 1/4, and every certified or declined 2021Q1 week chose one book. Size falls from about 300k-430k rows to roughly 30k-40k (100 groups x about 142 items), the scale at which the ADR's synthetic 40-group week solved in 40 s under `relax_fix`. F3 keeps joint selection under a growth objective and so answers the question F1 cannot: does choosing books under growth beat choosing them under the dollar objective and sizing afterwards? It keeps the ADR's second-order charge and its overshoot, which the comparison with F1 will show. Code: about 30 lines in the screen plus validation and tests; the Kelly documents already built (`docs-kelly2`) need only the new key.

### Optional arm

F1 at `f = 0.5` (`kelly050_sized_lp`), a config-only twin, settles the fraction question the one-stage build could not; it costs minutes per quarter.

### What was considered and set aside

- One-stage robust Kelly (the W1 dual inside the wealth): the same 300k-row program as the failed build; F3 is the cheaper route to a one-stage comparison and F2 the cheaper route to robustness.
- Kelly on a forecast shrunk toward the market-implied law (the chain carries `implied_volatility` and `black76_implied_vol` exists): a forecast-side blend, which the formulation review ruled needs its own design; F2 covers mean shrinkage with the project's calibrated tool.
- An implied-volatility market factor for the scenario weights (trailing realized volatility lags in March 2020 and overstates afterwards): a one-knob follow-up to F1, not a third formulation.
- A within-quarter adaptive fraction (the owner's 2026-10-08 radius precedent, seeded then updated as books settle): the first settlements arrive in week 6-8 of a quarter and accounts reset quarterly, so there is too little time for it to act in a four-quarter test.
- A CVaR cap on the sampled scenarios: a hard cap, against the owner's instruction; the utility prices the tail already, and the cap is one line in `ScenarioUtilitySolve` if the owner ever wants it as a sensitivity.

## 5. Test plan on the four selection quarters

- Formulations F1, F2, F3 (and the optional f 1/2 arm) on 2019Q4, 2020Q1, 2021Q1, 2021Q2: 12 (16) accounts, each a plain WSL document, its own registry, the same forecasts, inputs, fills, fees and deposits as the nine.
- Pre-registered reading, before any cell is opened: settled net P&L and marked maximum drawdown per cell; the count of quarters above zero; `ex-top1`; books and hit rate; `timeout_skipped` weeks and (for F1/F2) stage-1 timeouts separately; the stake per book against the stage-1 quantity (how much the sizer moved sizes); and, for F3 against F1, the share of weeks where the joint program chose a book the dollar program did not.
- Compute: F1/F2 about one worker-hour per account (the primary's stage-1 time: the 2021Q2 eight-method rules document took 6.4 h, the 2021Q1 four-method 3.2 h, so 45-50 min a method, plus seconds), 8 accounts about 8 h; F3 at most 13 x 2,400 s = 8.7 h an account, 4 accounts at most 35 h, likely far less at 100 groups. All fit WSL (10 GB measured for the one-stage Kelly; F1/F2 near the primary's 7 GB) or Mac units.
- Order: F1 first on 2021Q2 (the one fully priced quarter, so F1 is read beside all nine), then F2 and F3, then the remaining quarters.
- Governance: A0010 is locked at nine; these are candidates the owner may put beside them, as ADR-0299's two were. Each needs an ADR (proposed) and the usual two skeptic lenses before a real solve; the four quarters are development evidence (prior exposure); 2021Q3 onward stays unopened.

## 6. Before any new formulation: finish the cells that exist

Eighteen of 36 cells are unreadable for reasons that are housekeeping, not design: price the nine 2019Q4 Mac results against the v2 combined-chain documents, resume the 2021Q1 pricing document that died at 00:29 (four methods) and the 2021Q1 fine arm, close or re-run the 2020Q1 four-method document, and price the Kelly 2021Q1 result (which will show 1 traded week). All are queued in `priced/jobs-rest-ready.json` and `mio-wsl/jobs-kelly.json`; none needs code. Without them the owner's pick of three rests on 2021Q2 alone.

## Decisions for the owner

- A. Approve F1, F2 and F3 as proposed ADRs for the four selection quarters, built in that order, run as plain WSL documents beside the nine.
- B. Approve F1 only (the fastest to build and the one that fixes the most), then decide on F2/F3 after its 2021Q2 cell.
- C. Finish pricing the 18 pending cells first (no code), then decide.
- D. Different fractions, radius, scenario count or group cut than proposed (name them).

## Sources

- Path A0010 and the nine specs: `children/index_options/docs/decisioning/path.csv`; `docs/plans/mio-parallel/RUNBOOK.md` (the nine formulations, the selection flow).
- ADR-0299 (fractional Kelly, speed, floor, gap amendments): `docs/architecture/decision-log.md` on `claude/mio-kelly-20261106` (`69ac7d1e`).
- Formulation review (2026-11-05): `/home/russell/data/index_options/full-span-rerun-20261105/mio-review/formulation-review.md`.
- Results read: `/mnt/d/full-span-rerun-20261105/mio-wsl/runs/{2020Q1-rest,2020Q1-fine}/mio/*/result.json`; `/mnt/d/full-span-rerun-20261105/mio-parallel/priced-runs/{2021q1-rules-900,2021q2-rules-t25,2021q2-fine-t25}/mio/*/result.json`; the 2020Q1 four-method `method_metrics` events of the unclosed run `mio-wsl/runs/2020Q1/mio/...-c577a9ee` (provisional); the Kelly action file on the Mac RAM disk (counts and certificates).
- Queue and pricing state: `mio-parallel/priced/{drive_priced.log,sets.json,jobs-rest-ready.json}`, `mio-wsl/kelly2-check/kelly_driver.log`, `mio-parallel/check/run_status.out`.
- Reuse sweep (2026-11-07): `tools/sweep/sweep ScenarioUtilitySolve copula sample_average saa select_then_size two_stage implied_vol kelly_sizer` from `~/wt/mio-kelly-20261106`; its `origin/main` text search timed out after the name search, so text mentions are unchecked.
