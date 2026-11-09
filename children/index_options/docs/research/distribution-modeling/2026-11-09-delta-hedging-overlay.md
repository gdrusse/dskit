# Hedging as an attachable overlay for any MIO formulation: profit, loss control, interface, evidence

**Status:** research note, 2026-11-09 (Claude Fable 5.1), revising the same day's `2026-11-09-delta-hedging-design.md` after the owner's reframe (14:49 ET): hedging is a means to more profit or fewer losses, not a zero-delta goal, and it must be an independent component attachable to any formulation by one config block, byte-identical when detached. Design only: no code, no solve, no real data opened, no run log read, no Path row. Path rows: A0003, A0008, A0009, A0010 (the nine untouched; a hedged method is a research arm beside them). RE-ENTRY "Do not" list honoured. No profitability claim.

## Question

How can hedging raise MIO's realized profit or cut its losses on the selection quarters, which hedge designs are worth comparing, how is a hedge attached to ANY formulation without touching it, and what evidence, readable without a solve, decides which to build first?

## Finding

### 1. What a hedge can do for profit and loss, and how each is measured

The weekly MIO values a book at `E_F[payoff] - price` (the forecast's terminal law) and holds to expiry. Per item the nominal edge `e_i` splits into a DIRECTION part `e_i^dir = delta_i x mult x mu`, `mu = E_F[S_T] - F_0` the forecast's drift against the market forward, and a SHAPE part `e_i - e_i^dir` (dispersion, skew, tails against the chain's implied law). A hedge sold at ratio `h in [0, 1]` of the market delta removes `h` of the direction part and leaves the shape part, to first order in `mu`.

| Objective | Mechanism | Measured on the selection quarters (4 quarters x 4 WSL formulations, saved results) |
|---|---|---|
| (a) keep the shape edge, drop unwanted direction | the hedged value is `E_{F'}[payoff] - price + O(mu^2)`, `F'` the forecast recentred on the forward | ADR-0310 attribution per book: `stock_pnl` vs `residual`; sum, hit rate and gross share of each by cell |
| (b) loss control at the least expected-profit cost | marked NAV moves with the open books' delta; hedged marks move with gamma and vega only | marked max drawdown, book ES95, worst week, `unsupported` cases, per ratio `h`; the frontier `(delta DD, delta settled P&L)` over `h`; cost per unit of drawdown removed |
| (c) capacity | the reserve is the book's terminal max loss: a hedged CREDIT book (short put plus sold shares) loses less at the bottom, so its reserve falls and the caps admit more of it; a hedged DEBIT book (long call plus sold shares) loses MORE at its strike (`premium + h (K - F)`), so its reserve rises. The W1 charge falls for both (a smaller Lipschitz constant) | contracts and reserve per book, the share of weeks `existing_cap_breach_cash`, in the in-program replay against the unhedged twin |
| (d) direction only where it is calibrated | ratio per item `1 - w_dir,b`, `w_dir,b in [0, 1]` the calibrated slope of REALIZED on predicted DIRECTION edge in the item's bucket (an ADR-0311 variant with two regressors, direction and shape, instead of one) | ADR-0311's forward reliability of the direction edge by bucket; the hedged twin's settled P&L against the full-hedge and no-hedge twins |

**Where more profit can come from, stated plainly.** Hedging cannot raise `E_F[P&L]` under the forecast: it removes a bet the forecast values at `h x e^dir`. It raises REALIZED profit only if (i) the direction bet has been losing (the 2026-10-30 memo: realized on expected slope about 0, within-week rank IC -0.43 on the chosen books; if the losing part is direction, removing it adds realized profit directly), or (ii) the freed reserve on credit books lets the caps or a Kelly sizer take more shape edge. Both are measurable before any solve: (i) from ADR-0310's `sum stock_pnl` against `sum residual` per cell; (ii) only partly, from the hedged reserve of the saved books. Everything else a hedge does is loss control, paid for in expected profit and costs.

### 2. Hedge designs to compare

Common facts: 166 single names, next-session end-of-day fills, hold to expiry, a $10,000 account plus $1,000 every 14 days, no dividend table in the study inputs (the onboarding root has `optionshist-chain` SPY/QQQ/IWM chains and `cboe-index` closes, not wired to the MIO study), the chain's vendor iv and parity forward available at the decision (ADR-0310).

| Design | Expected effect on profit | Expected effect on losses | Cost | Data and leakage | Solve-time impact |
|---|---|---|---|---|---|
| D1 per-name static delta, forward instrument, `h = 1` | removes the direction bet (positive for realized profit only if that bet loses); W1 charge falls; credit books cheaper to reserve, debit books dearer | cuts both market and idiosyncratic direction (2020Q1 was market, 2021Q1 the GME/AMC squeeze was idiosyncratic: an index hedge would miss it) | spread and carry inside the parity forward; margin floor on the stock leg | decision chain only; nothing new | none in-program (coefficients only); replay-only mode is free |
| D2 partial ratio `h in [0, 1]` per method | a dial between D1 and nothing; the frontier is read in replay mode before any solve | proportional | proportional | as D1 | as D1 |
| D3 portfolio hedge: beta-weighted dollar delta in ONE index instrument (SPY forward) | leaves idiosyncratic direction in; expected cost zero under the forward (no index forecast exists, so the index drift is set to zero: a free hedge variable would otherwise bet on it); W1 duals do not see it (they are per-name marginals): it enters the shock rows and Kelly's joint scenarios only, so for `nominal_lp` it is pure post-selection | cuts market direction only; basis risk between the equal-weight 166 basket (the shock gate's definition) and SPY | lowest: one liquid instrument | needs index bars (and for D5 the index chain) as a new study input through onboarding; betas from the run's own bars (known at `t`) | post-selection: none; in the joint rows: one continuous variable per week |
| D4 conditional hedge (only when the shock gate's held loss is near its limit, or the name's vol regime is `high` by ADR-0311's trailing-median rule) | keeps the direction bet in calm regimes | cuts cost of protection; timing error is the risk | lower than D1 | gate status and regime are known at `t`; no new data | none: `h_t` is fixed before the solve |
| D5 tail overlay: buy SPY puts or put spreads to a CVaR budget, post-selection | a drag of the premium every week | caps market tails only; useless for single-name squeezes; the account is small relative to one SPY put | high: index puts carry a correlation risk premium (Driessen, Maenhout and Vilkov 2009), protection stays dear even in calm markets (Israelov and Nielsen 2015) and protective puts cut returns more than they cut risk over most horizons (Israelov 2019) | index chain through onboarding | none (post-selection) |
| D6 hedge ratio chosen by the optimizer against the formulation's own risk terms (W1 charge, shock rows, MAD charge, Kelly dispersion) | with the hedge leg's expectation taken under the FORWARD (zero drift) the free ratio cannot harvest `mu`; it trades risk against cost only; for a formulation with no risk term (`nominal_lp`) it is indifferent and never hedges once a cost exists | the solver hedges where the risk term bites | as D1 plus a per-unit cost | as D1 | one continuous variable and two bound rows per group (about 1,300 a week); the LP screen must then relax it too |

Ranking for a first build: D1/D2 in one component (the ratio is a parameter), D4 as an optional condition on it, D3 as the second kind once index bars are an input, D6 after D1 shows the W1 and shock terms respond, D5 last.

### 3. The attachable architecture

**Attach point.** Every formulation's risk machinery reads one number: the item's terminal loss at a level, `_loss_row(items, spot, mult)` in `ledger_studies.py`, which feeds the grid `losses`, the `reserve_rows`, the shock rows (`_move_terms`) and the Kelly scenarios; the replay reads `structure_payoff` and `structure_max_loss`. A hedge is a linear term in the level, so attaching it there hedges every formulation at once without any of them knowing. The program-dict alternative (rewrite `losses` and `joint_rows` after the child built them) cannot rebuild a shock row without re-deriving the move arithmetic, which the repo rule forbids (one owner). Hence: `_loss_row(items, spot, mult, hedge=None)` adds `h_i (spot - ref_i)` per item when `hedge` is given; `structure_payoff` and `structure_max_loss` gain `hedge=None` (`(units, level)`), the same one owner (ADR-0197). Both default to the bit-identical unhedged arithmetic.

**Component (generic, stdlib, `dskit/pipeline/hedge_overlay.py`).**
- `HedgeOverlay(params)` (abstract): `_BASE_PARAMS = ("kind", "mode", "ratio", "margin_fraction", "round_units", "cost_per_unit", "condition")`; subclasses add `_EXTRA_PARAMS`; one `validate_params` rejects unknown keys, a ratio outside `[0, 1]`, a mode outside `("replay", "program")`, a negative margin or cost. Hooks: `units(items, context)` (abstract: hedge units per item, signed, already multiplied by `ratio` and by the condition's 0/1), `reference(context)` (the level the hedge settles against), `floor_rows(items, units, reference)` (margin floor coefficients, consumed by the one `_floor`), `legs(book, units, fill_context)` (replay legs: units, reference, cost), `settle(legs, level)`, `mark(legs, level)`, `record(...)` (what the decision and the book carry). `NullHedgeOverlay`: every hook returns its input or zero; `units` returns `None` so `_loss_row` takes its unhedged branch.
- `FixedDeltaForwardHedge(HedgeOverlay)`: `_EXTRA_PARAMS = ("delta_source", "iv_source", "rate", "parity_strikes")`; units `= -ratio x sign x mult x delta_i` (shares sold per contract), delta from `LegGreeks` on the context's parity forward (ADR-0310; spot fallback flagged, never refused), reference `F` at the decision for the program and `F_fill` at the fill for the legs; `round_units` rounds the book's total to whole shares at fill; cost `cost_per_unit x |units|` charged at fill.
- Reserved kinds, interfaces only: `IndexForwardHedge` (`_EXTRA_PARAMS = ("index_symbol", "beta_lookback")`, units per BOOK not per item, attaches to joint rows and replay legs), `IndexPutOverlay` (post-selection, a CVaR budget), `CalibratedDirectionHedge` (ratio per item from an ADR-0311 map).
- `condition`: `None`, `{"shock_held_share": x}` (hedge only when the held books' shock loss is at least `x` of the limit at the decision) or `{"vol_regime": "high"}` (ADR-0311's trailing-median rule on the name's reference scale); both known at `t`.

**Where the study calls it (once each, formulation-agnostic).** The method step builds `overlay = HedgeOverlay.from_method(method)` (`NullHedgeOverlay` without a `hedge` block). `configured` (the per-method rewrite that already sets `radius`, `scale` and the floor rows) adds the hedged `losses`, `reserve_rows`, slope row and floor rows from `overlay.units(meta["items"], context)` when `mode == "program"`; `_shock_rows` and `_move_terms` receive the same units; `_fill` appends `overlay.legs`; `_settlements` adds `overlay.settle`; `_finish` marks with `overlay.mark`; `_books` and `_ledger` check parity with the hedged `structure_max_loss` and the shared ledger's `hedge: {rebalance: fixed, instrument: forward}` (a small `option_ledger` extension); the record carries `hedge` per decision and per book. `mode == "replay"` skips the `configured` step: the saved selection is replayed hedged, the counterfactual.

**JSON block (per method, the only place the hedge is named):**
`"hedge": {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0, "delta_source": "black76_chain_forward", "iv_source": "vendor", "rate": 0.0, "parity_strikes": 3, "margin_fraction": 0.15, "round_units": true, "cost_per_unit": 0.0, "condition": null}`. The formulation's own keys are untouched; a `hedge` block naming a formulation key (`rho`, `shock`, `kelly`, ...) refuses `hedge_overrides_formulation`.

**Refusals (stable codes).** `hedge_unknown_kind`, `hedge_unknown_key`, `hedge_ratio_range`, `hedge_mode`, `hedge_overrides_formulation`, `hedge_with_sizer` (the F1 sizer samples payoffs through its own path; wiring it is a second step), `hedge_rebalance_not_supported` (dynamic rules belong to `OptionStructureLedger.hedge`, not here), `hedge_seal` (every hedged config pins its window below ADR-0310's `DEVELOPMENT_SEAL`). Counted, never silent: `forward_spot` fallback, `leg_no_iv` (the item is left unhedged and counted), rounding residual per book.

**Parity guarantees (each a test).** (P1) no `hedge` block: the program dict and every result equal today's byte for byte; the pinned `RADIUS_BASELINES` and `SHOCK_BASELINES` digests and the stress Gate 0 control are unchanged. (P2) `ratio: 0`: coefficients `+0.0`, zero-unit legs; every result equals the unhedged one except the `hedge` record fields. (P3) `mode: replay`: the program dict is identical to the unhedged one. (P4) `fixed` ledger hedge equals the study's settlement to the cent. (P5) `advance` never refuses a hedged settlement (the hedged max loss sits in the reserve rows). (P6) recorded and derived replays re-derive the hedged program from the method block alone.

**Composition.** Radius: `WassersteinDual.attach` takes the hedged expressions unchanged; the charge falls. Shock gate: hedged terminal loss under each move, so the gate binds on convexity. Reserves: hedged max loss at 0 and the strikes plus the margin floor; the slope row `-s_i [call_i] + units_i / mult <= 0` keeps the loss bounded above (a hedged long put is otherwise unbounded). Available-size fills: units scale with the filled quantities, rounded once per book. Kelly (ADR-0299/0301): `_move_terms`, `_centred_scenarios` and the atom dispersion read the hedged losses, so the dispersion charge and the scenario wealths are hedged with no Kelly code touched. Sizer (ADR-0300): refused in the first build; its sampled `payoff_b(S)` would call `structure_payoff(..., hedge=)` in a second step.

**In-program vs post-selection, compared.** Post-selection (D1 in `replay` mode) attaches to anything, including saved results, costs no solve time, and is the right counterfactual; but the books were chosen for a value that included the direction bet, so the hedged portfolio holds books picked for the wrong reason, and the freed reserve is never re-used. In-program (`program` mode) chooses books by their hedged value and lets the caps, radius, shock gate and Kelly see the hedged risk; it costs no new variables at a fixed ratio. Recommendation: one component with both modes; `replay` first for evidence, `program` for the build.

### 4. Evidence plan (no solves), with pre-registered rules

Inputs: ADR-0310's `books.csv` and `cells.csv` on the four selection quarters, four WSL formulations; the stress-replay counterfactual (`replay` mode, a `mio_execution_stress` scenario `hedge_overlay`) on the same saved selections; the run's own bars for the basket return over each book's life. Rules fixed before any number is read:

- **E1, is direction the losing part?** Per cell, `sum stock_pnl` and `sum residual`, their gross shares, and the same for the worst-drawdown book of each quarter. Rule: if `sum stock_pnl < 0` in at least 3 of 4 quarters while `sum residual > 0` in at least 3, hedging is a profit measure (objective (a)/(d)), build `program` mode next; if `stock_pnl` dominates gross P&L but is positive, hedging is a loss-control measure, read E2 first; if the residual dominates both, hedging is not the lever (shape or size is).
- **E1b, market or idiosyncratic?** Split each book's `stock_pnl` into `entry_delta x S_fill x beta x basket_return` and the rest. Rule: basket part at least two thirds of the stock P&L variance in at least 3 quarters means `IndexForwardHedge` (D3) is the second kind to build; otherwise per-name stays.
- **E2, the frontier.** `replay` mode at `h in {0.25, 0.5, 0.75, 1.0}` on the saved selections: settled P&L, marked max drawdown, ES95, worst week, `unsupported` count per cell. Rule: proceed to `program` mode if some `h` cuts the marked max drawdown in at least 3 of 4 quarters by at least 0.10 of NAV each and the four-quarter settled P&L falls by no more than one quarter of its unhedged value (or rises); the `h` with the best drawdown cut per dollar of profit given up is the first program-mode ratio.
- **E3, conditional.** `replay` mode with `condition` `{shock_held_share: 0.5}` and `{vol_regime: high}` against unconditional at the chosen `h`. Rule: a condition is adopted if it keeps at least 80 percent of the drawdown cut at no more than half the hedge's P&L cost.
- **E4, calibrated direction.** Waits for ADR-0311 phase 1 with the two-regressor variant (direction and shape edges): if the direction slope's lower band is at or below zero in every bucket, `h = 1` is right; if positive in some buckets, `CalibratedDirectionHedge` with `1 - w_dir,b` is the third kind.

### 5. Leakage and data rights

Deltas, parity forwards and the condition are read from the decision session's chain, bars, gate status and trailing scales; the hedge fills at the next session's close with that chain's forward; settlement is the expiry bar; no dividend, earnings or index data enters the first build; the rate is a constant or a series with `known_at <= t`. First scope: the four selection quarters and four WSL formulations as research arms beside the nine; ADR-0310's seal (2021-07-01) in every config; no 2022-2023 MIO economics before the picks; nothing 2024+ (A0003). An index kind needs index bars through onboarding (`docs/standards/data-onboarding.md`) before any config names them.

### 6. Placement and staged build

| Tier | File | Change |
|---|---|---|
| core | `dskit/pipeline/hedge_overlay.py` (new) | `HedgeOverlay`, `NullHedgeOverlay`, `FixedDeltaForwardHedge`; `__all__` |
| core | `dskit/pipeline/option_structures.py` | `hedge=None` on `structure_payoff` and `structure_max_loss`; the unbounded rule reads the slope |
| tier 2 | `dskit/pipeline/libs/option_ledger.py` | `REBALANCES` gains `fixed`; `hedge.instrument` `forward` |
| child | `index_options/ledger_studies.py` | `_loss_row(hedge=)`, `configured`, `_shock_rows`/`_move_terms` units, `_fill`, `_settlements`, `_finish` marks, `_books`/`_ledger` parity, validation, record |
| child | `index_options/mio_execution_stress.py` | scenario `hedge_overlay` (replay mode over saved selections) |
| child | `index_options/mio_greeks.py` (ADR-0310) | reads hedge legs when present so `stock_pnl` and the hedge leg reconcile |
| tests | `tests/pipeline/test_hedge_overlay.py` (new), `test_option_structures.py`, `tests/pipeline_libs/test_option_ledger.py`, child `test_ledger_studies.py`, `test_mio_execution_stress.py` | P1-P6, hand-worked hedged max loss, slope row, rounding, floor in four places, refusals, purity, params tuples |
| docs | `dskit/pipeline/README.md`, child README, `mio-glossary.md` | one line each |

Stages (Sonnet; up to two agents at once where stages are independent; Claude orchestrates; skeptic review per stage): S1 core `hedge_overlay.py` with `Null` and `FixedDeltaForwardHedge` on synthetic items, plus the `option_structures` keyword (independent of S2); S2 tier-2 ledger `fixed`/`forward` (independent of S1); S3 child wiring in replay mode, P1-P5, the stress scenario; S4 the E1/E2/E3 reads on the saved cells (no solve); S5 program mode, P6, one 2021Q2 week through the ADR-0297 `--check` route for timing; S6 hedged twin documents on the four quarters, two independent lenses before a real solve. S1 and S2 can run in parallel; S3 waits for both.

## Self skeptic review (author's own, labelled; not an independent lens)

- **Major, folded in:** a program-dict transform cannot rebuild the shock rows without re-deriving the move arithmetic; the hedge enters at the one loss owner instead.
- **Major, folded in:** a free hedge variable (D6) harvests the forecast's drift unless the hedge leg's expectation is taken under the forward; stated, and D6 deferred.
- **Major, folded in:** hedged debit books have a LARGER terminal max loss; the reserve rows carry it, so capacity gains are credit-side only (section 1c), and `advance` cannot refuse.
- **Major, folded in:** an index hedge does not touch the per-name W1 duals; its only in-program effect is through joint rows and Kelly scenarios, said plainly.
- **Minor, open:** the component holds `ratio` and `condition` but the study still owns where `units` are consumed; the hooks list is the contract and P1-P6 pin it.
- **Open, disclosed:** American assignment and dividends are inside the parity forward to first order only; the forward leg is marked at spot; rounding loses up to half a share per book; solve time unmeasured.
- **Open, governance:** every hedged method is a new arm under a locked A0010.

## Decisions for the owner (recommendation marked)

1. First kind: (a) per-name `FixedDeltaForwardHedge` (recommended: hedges both market and idiosyncratic direction, no new data); (b) `IndexForwardHedge` (needs index bars onboarded); (c) both.
2. First mode: (a) `replay` counterfactual and the E1-E3 reads, then `program` (recommended); (b) `program` directly.
3. Ratio: (a) fixed per method, swept in replay, the frontier's best taken forward (recommended); (b) 1 only; (c) calibrated per bucket after ADR-0311.
4. Condition: (a) none in the first build, both measured in replay (recommended); (b) shock-held share; (c) vol regime.
5. Margin floor on the stock leg: (a) 0.15 of gross notional (recommended); (b) 0.5, Reg T; (c) 0.
6. Attach point: (a) a hedge vector into the one `_loss_row` plus four replay hooks (recommended); (b) a program-dict transform (cannot reach the shock rows).
7. Governance: (a) research arms beside the nine, four selection quarters only (recommended); (b) amend A0010.
8. Evidence thresholds: (a) as pre-registered in section 4 (recommended); (b) the owner's own numbers.
9. ADR-0311 amendment: (a) ask for the two-regressor (direction, shape) variant so E4 can be read (recommended); (b) not now.

## Sources

Repo: `children/index_options/index_options/ledger_studies.py` (`_loss_row`, `_prepare`, `_shock_rows`, `_move_terms`, `_centred_scenarios`, `_limits`, `_books`, `_fill`, `_settlements`, `_ledger`, `_finish`, `_method_steps`); `dskit/pipeline/libs/pyomo.py` (`WassersteinDual.attach`, `TransportPortfolioSolve`, `_AtomDeviation`); `dskit/pipeline/libs/option_ledger.py` (`_Hedge`, `FundedOptionAccount.advance`); `dskit/pipeline/option_structures.py`; ADR-0310 and `option_exposure.py` on `origin/claude/mio-greeks-diagnostics-20261109` (`8fcd1b8`); ADR-0311 on `origin/claude/edge-calibration-design-20261109` (`c3347f1`); ADR-0193, 0197, 0278, 0282, 0297, 0299-0301; `docs/research/distribution-modeling/2026-11-07-mio-selection-quarters-and-three-kelly-formulations.md`; `2026-11-09-delta-hedging-design.md` (the earlier note, kept); `mio-glossary.md`; `path.csv`; `docs/RE-ENTRY.md`.

Literature (bibliographic records checked by web search 2026-11-09):
- Bakshi, G. and N. Kapadia (2003), "Delta-Hedged Gains and the Negative Market Volatility Risk Premium", Review of Financial Studies 16(2), 527-566.
- Cao, J. and B. Han (2013), "Cross section of option returns and idiosyncratic stock volatility", Journal of Financial Economics 108(1), 231-249.
- Goyal, A. and A. Saretto (2009), "Cross-section of option returns and volatility", Journal of Financial Economics 94(2), 310-326.
- El Karoui, N., M. Jeanblanc-Picque and S. Shreve (1998), "Robustness of the Black and Scholes formula", Mathematical Finance 8(2), 93-126.
- Whalley, A. E. and P. Wilmott (1997), "An asymptotic analysis of an optimal hedging model for option pricing with transaction costs", Mathematical Finance 7(3), 307-324.
- Bertsimas, D., L. Kogan and A. W. Lo (2000), "When is time continuous?", Journal of Financial Economics 55(2), 173-204.
- Driessen, J., P. Maenhout and G. Vilkov (2009), "The Price of Correlation Risk: Evidence from Equity Options", Journal of Finance 64(3), 1377-1406.
- Israelov, R. and L. N. Nielsen (2015), "Still Not Cheap: Portfolio Protection in Calm Markets", Journal of Portfolio Management 41(4).
- Israelov, R. (2019), "Pathetic Protection: The Elusive Benefits of Protective Puts", Journal of Alternative Investments 21(3) (AQR white paper 2017).
Cited from standard references, not fetched today:
- Carr, P. and L. Wu (2009), "Variance Risk Premiums", Review of Financial Studies 22(3), 1311-1341.
- Corsi, F. (2009), "A Simple Approximate Long-Memory Model of Realized Volatility", Journal of Financial Econometrics 7(2), 174-196.
- Rockafellar, R. T. and S. Uryasev (2000), "Optimization of conditional value-at-risk", Journal of Risk 2(3), 21-41 (the linear CVaR budget a put overlay or D6 would use).
