# Delta hedging for the weekly MIO option-portfolio study: design and order of build

**Status:** research note, 2026-11-09 (Claude Fable 5.1). Design only: no code, no solve, no real data opened, no run log read, no Path row changed. Path rows followed: A0003 (2024 onward sealed), A0008/A0009 (forecasts and the 166 names unchanged), A0010 (the nine formulations and their digests untouched; anything here is a research arm beside them). RE-ENTRY "Do not" list honoured: no 2022-2023 MIO economics, nothing 2024+. Nothing here is a profitability claim.

## Question

Realized MIO P&L on the selection quarters is set by direction on one or two books a quarter, with marked drawdowns near -0.9 (the 2026-11-07 note, section 2). The owner outlined three hedging levels: (1) a static hedge at fill, held to expiry, as a replay-only attribution column (ADR-0310); (2) dynamic daily rehedging in the replay; (3) the hedge inside the optimizer with a FIXED delta per leg. Which are worth building, in what order, with what formulation, replay, forecast, leakage and placement, for this codebase?

## Finding

### 1. Recommendation and order

Build **level 3 as one staged build, with level 1 as its evidence gate**, and **defer level 2** until a realized-variance target exists (section 4). Order:

1. **ADR-0310 (level 1)** lands and runs on the four selection quarters, four WSL formulations. Read, per cell: the stock-equivalent share of gross `sum |pnl|` and whether the books behind the -0.9 marks are stock-dominated. Gate for stage A below: stock share of gross P&L at or above one half in at least three of the four quarters, or the worst book of each quarter stock-dominated. Below that the problem is shape, not direction, and hedging will not cut the drawdowns (go to section 4 instead).
2. **Stage A (replay only, ADR-0312):** the static hedge as an opt-in `hedge` method key in `WeeklyOptionPortfolioStudy` and as a `mio_execution_stress` scenario (`static_forward_hedge`) over the SAVED selections: same books, each hedged at fill, held to expiry, with margin, marks and settlement. Answers "would hedging what MIO already chose have cut the drawdowns" without a solve. Gate 0 control and every pinned digest stay byte-identical because the key is absent from the nine.
3. **Stage B (optimizer, same ADR):** the hedged payoffs enter the program (section 2). Changes WHICH books are chosen: direction-only edges vanish, shape edges stay.
4. **Level 2** only if, after stage B, drawdowns are still driven by the residual and a realized-variance forecast is built and scored first (section 4).

Why not level 2 first: its P&L is `integral of 1/2 Gamma S^2 (sigma_realized^2 - sigma_implied^2) dt` along the path (El Karoui, Jeanblanc-Picque and Shreve 1998), which neither the terminal predictive CDF nor the terminal-payoff objective can value; a replay with daily rehedging would realize a bet the optimizer never priced. Why not level 3 alone: stage A is a few hundred lines less than B and gives the counterfactual on existing results.

### 2. Level 3: the exact formulation change

**Inventory (done; nothing to invent).** `WassersteinDual.attach` takes any linear loss expression per grid node; `TransportPortfolioSolve` reads `groups[].losses` (one row per atom), `reserve_rows`, `linear_rows`, `joint_rows`; the child builds all four in `_prepare` and the per-method `configured` list; `_loss_row` is the ONE owner of terminal loss per item and feeds the grid losses, the reserve rows and the shock rows (`_move_terms`); `structure_max_loss` / `structure_payoff` (`dskit/pipeline/option_structures.py`) own the book's max loss and settlement, and say "stock hedges are outside this function's scope"; `OptionStructureLedger` already ships a `hedge` block (`_Hedge`: Black-76 delta from each leg's mid-implied vol, forward `S e^{rT}`, rebalance `daily | every_n | threshold`, `fee_per_share`, ex-date dividends, no financing, no margin); ADR-0310 (branch) ships `ParityForward`, `LegGreeks` (vendor iv, chain-implied forward, `F/S` chain rule) and `StockEquivalent`; ADR-0193 `_benchmark` is the same split welded to the condor backtest.

**Hedge ratio per item (fixed).** For item `i` (right, strike `K_i`, sign `s_i`, quoted at decision day `D`): `h_i = s_i x mult x delta_i`, shares SOLD per contract, with `delta_i = black76_delta(right, F_0, K_i, iv_i, T) x F_0 / S_0` from `LegGreeks` on the decision chain: `F_0` the parity forward of that symbol and expiry (`ParityForward.from_chain`, spot fallback flagged), `iv_i` the quote's vendor iv (`iv_source: vendor`), `T` the DTE in years, rate the config value. `h_i` is a number, never a variable: a free hedge would let the program buy the forecast's mean drift, the part the calibration audit says anti-ranks outcomes.

**Instrument: a forward, not stock.** The hedge leg is cash-settled against the reference `F_ref`: hedge P&L per contract `= -h_i (S_T - F_ref)`. A forward absorbs financing, borrow and dividends to first order (the parity forward already does, ADR-0310), books no cash at entry (short-sale proceeds would otherwise inflate the account's `cash`, which is funding plus open credits), and needs no dividend table (the study inputs carry none; `pair_option_ledger` and the condor backtest do, for other data). The realized difference from a stock hedge is `h (F_fill - S_fill) + h x dividends - h x financing`, zero when parity is fair; `basis_usd` of ADR-0310 reports its size.

**Hedged terminal loss per contract at level `S`:** `l_i^h(S) = l_i(S) + h_i (S - F_ref)`, with `l_i` today's `_loss_row` term. One owner: `_loss_row(items, spot, mult, ref)` reads `item["hedge"]` (absent means 0.0, so every unhedged coefficient is bit-identical). Everything downstream follows from that one change:

- **Nominal objective** `credit . x - sum_k p_k (l^h_k . x)`: the hedged expectation is `E_F[payoff] - price - h (E_F[S_T] - F_0)`. To first order in the forecast's drift `mu = E_F[S_T] - F_0`, `E_F[payoff] = E_{F'}[payoff] + delta x mult x mu` where `F'` is the forecast recentred on the forward, so the hedged value is the forecast's SHAPE value against the market forward plus `O(mu^2)`: direction removed, shape (dispersion, skew, tails against implied) kept. No recentring of the CDF is needed; the fixed market delta does it.
- **W1 worst-case term:** `WassersteinDual.attach` receives the hedged loss expressions unchanged in form. Its cost is bounded by `radius x scale x` the loss's Lipschitz constant in `S`; a hedged long call has slopes `-h` below and `mult - h` above the strike against `0` and `mult` unhedged, so the robustness charge FALLS for hedged books (the adversary can no longer move the mean against a delta the book no longer carries). The `radius_scale` route is untouched.
- **Shock gate rows** (`_shock_rows` through `_move_terms` through `_loss_row`): the terminal loss at the shocked spot now includes the hedge, so the gate stops binding on direction and binds on the book's convexity under the move (short gamma). This is intended: the gate becomes a gamma gate for a hedged formulation. Kelly's `_centred_scenarios` inherit the same terms but are out of scope.
- **Reserves and floors.** The reserve rows evaluate `l^h(S) - credit` at `S in {0} U strikes` and `-credit`; the hedged loss at 0 includes `-h F_ref` (a gain for a sold-share hedge). The settlement P&L is piecewise linear with the same kinks, so its minimum over `S >= 0` is at 0, a strike, or `+inf` when the slope above the top strike is negative: `sum_i x_i (s_i mult [call_i] - h_i) < 0`. Today's `linear_rows` entry (coefficients `-s_i` for calls, upper 0) generalizes to coefficients `-s_i [call_i] + h_i / mult` with upper 0, bit-identical when `h_i = 0.0`. `structure_max_loss` and `structure_payoff` gain a `hedge=(shares, level)` keyword (default `None`, the same one owner, ADR-0197), used by `_books` parity, `_fill`, `_settlements` and `_ledger`. Without this row a hedged naked long put (sold shares against it) would be unbounded above and `FundedOptionAccount.advance` would refuse its settlement ("settlements must cover maturities within reserved losses").
- **Short-stock margin.** The hold-to-expiry account reserves the terminal max loss only; a broker also holds margin on the stock leg path-wise. Add one capital-charge floor row beside `short_notional_fraction`: `hedge.margin_fraction x |h_i| x F_ref` per contract (gross, so linear), applied by the one `_floor` to the solver, the books, the fill and the ledger alike. A fraction of 0.15 is a portfolio-margin-like stress on the stock leg; 0.5 is Reg T initial, which would reserve more than the account holds for most books (a 10-contract 50-delta book is 500 shares); 0 is terminal loss only. Owner question 5.
- **Available-size fills.** The hedge scales with the filled quantities: `H_b = round(sum_i h_i q_i^filled)` shares (integer; at most half a share of delta per book is lost). The fill reprices the options at the next session's quotes as today and sets `F_ref = F_fill` from the fill chain's parity forward (spot fallback flagged); the book's reserve is recomputed with the hedged max loss and the margin floor, and `repriced_shared_cap` applies unchanged. `h_i` is NOT recomputed at fill (owner question 4): the decision's valuation is what the certificate covers.
- **Settlement and marks.** `pnl_b = credit_b + mult x payoff(S_T) - H_b (S_T - F_fill)`; the daily mark adds `-H_b (S_day - F_fill)` to the option mid mark (the forward's remaining carry is ignored, at most tens of days; owner question 8). `_ledger` passes the shared `OptionStructureLedger` a `hedge` block with a new rebalance rule `fixed` (the order carries its shares) and `instrument: forward`, so the existing cent-level parity check covers the hedge leg too.

**Solve size and time.** Zero new variables. Per group: the same loss and reserve rows with different coefficients, one linear row with changed coefficients, one more floor row (margin). The `lp` screen keeps groups by standalone relaxed value; hedged edges are smaller, so the screen will drop more and the MILP will usually be smaller. The direction of the time change is unmeasured: before any quarter, one 2021Q2 week through the ADR-0297 `--check` bundle route (per-week objective, termination word and seconds against the unhedged twin). The Greeks cost nothing (about 230k Black-76 deltas a week, microseconds each; one parity forward per symbol and expiry).

**Where the hedge is applied in code.** NOT in `_prepare`: its cache is one entry per day shared by every method of a document, and the hedge is per method. The `configured = [{**g, ...}]` step of `_method_steps` already rewrites `radius`, `scale` and `reserve_rows` per method; it rewrites `losses`, `reserve_rows` and `linear_rows` from `meta["items"]` plus `h` there. The `recorded` and `derived` replays re-derive the same program, so a worker's actions are priced on hedged coefficients by this host.

### 3. Level 2: replay design (deferred, specified so the gate is concrete)

- **Mechanism exists at tier 2** (`option_ledger._Hedge`): daily, every-n and threshold rules, Black-76 on mid-implied vol, `fee_per_share`, ex-date dividends. Missing for a real replay: financing and borrow on the stock, a stock half-spread, margin, the parity forward and vendor iv (the ledger re-implies from mids and uses `S e^{rT}`), and the chronological account (`_fill`, `_settlements`, `advance`) which today sees options only; the shared ledger is a parity check at `_finish`, not the account.
- **Rebalance rule.** The replay's data is end-of-day, so the finest rule is the close. Literature: with proportional costs the optimal rule is a no-trade band around the delta of width proportional to `(k S Gamma^2 / lambda)^{1/3}` (Whalley and Wilmott 1997), and discrete rehedging leaves an error whose size falls with the root of the rebalance count (Bertsimas, Kogan and Lo 2000). A band rule (`threshold`) is already there; the band is a config value, never a constant.
- **Costs.** Stock: commission per share (config), half-spread (config, basis points of price), borrow fee on short stock (annualized rate times notional times days/365; config or an onboarded series). Rate: the `fred-market-features` onboarding source exists for a daily rate; until wired, a constant `rate` as ADR-0310.
- **Dividends and American short calls.** A stock hedge pays the dividend on a short position at the ex-date (the ledger does this with a `dividends` input, which the study inputs lack and would have to be onboarded for 166 names, `docs/standards/data-onboarding.md`). Hedging in forwards avoids the table but not the exercise problem: an ITM short call on a dividend payer is assigned the day before ex-date when the dividend exceeds the matching put's time value; the replay is cash-settled European and does not model assignment (glossary; ADR-0310 states the same). Level 2 would at least flag `assignment_risk` per book (short call, ITM, ex-date before expiry, dividend above the put mid) rather than price it.
- **Composition and guarantees.** A `hedge.rebalance` other than `fixed` would make the account path-dependent: cash moves daily, reserves must cover the hedge's path loss, and the marked NAV becomes the thing hedged. Gate 0 holds because the key is opt-in and absent from pinned documents; the hedged replay with `rebalance: fixed` must equal stage A exactly (test), and with every share zero must equal the unhedged replay exactly (test).

### 4. Is a realized-volatility target needed?

For levels 1 and 3, **no**: the hedged payoff is a function of `S_T` alone and the predictive CDF values it exactly; the forecast's dispersion and skew against the chain's implied law IS the bet, stated in terminal terms. The static hedge at the forward is therefore the right ceiling for a terminal-distribution forecast.

For level 2, **yes**: the dynamic hedging P&L is the path integral of gamma times the realized-minus-implied variance gap, so valuing it needs a forecast of realized variance over `[fill, expiry]` per name: a new target (log realized variance to the horizon), a proper score (QLIKE or the log score), a HAR-RV baseline (Corsi 2009; the child already runs HAR nodes in the condor grid), and the same A0003 windows and A0005-style promotion rule before any use. The literature says what such a bet earns on average: delta-hedged index option gains are negative, the negative volatility risk premium (Bakshi and Kapadia 2003; variance swap rates exceed realized variance, Carr and Wu 2009), and in the single-name cross-section delta-hedged option returns fall with idiosyncratic volatility (Cao and Han 2013) and rise with the gap between long-run realized and current implied volatility (Goyal and Saretto 2009). Those are signals a hedged MIO would be competing with; a terminal CDF carries a variance view implicitly, but a path-wise bet should be scored on a path-wise target first.

### 5. Leakage and data rights

Every input is dated at its own time: `delta_i`, `F_0` from the decision day's chain and bars; `F_fill` and `S_fill` from the fill session's; settlement from the expiry bar; vendor iv is the end-of-day value of the day it is read; the rate is a constant or a series with `known_at <= day`. No dividend or earnings data enters. Scope of the first run: the four selection quarters, the four WSL formulations' documents plus one hedged twin, as plain WSL documents beside the nine (as ADR-0299's were); the hedged twin is a research arm and not a candidate for the three picks unless the owner amends A0010. ADR-0310's `DEVELOPMENT_SEAL` (2021-07-01) applies to every hedged config; no 2022-2023 MIO economics before the picks; nothing 2024+ (A0003).

### 6. Placement and build plan

Tiering test: "could a project that never heard of MIO use it?" Hedged payoff and max loss, a fixed forward hedge in a ledger, integer hedge rounding: yes. The method key, the parity forward on this chain's columns, the stress scenario: the child.

Files (none written; each needs the owner's manifest approval):

| Tier | File | Change |
|---|---|---|
| core | `dskit/pipeline/option_structures.py` | `structure_payoff` and `structure_max_loss` gain `hedge=None` (`(shares, level)`); the unbounded rule becomes "calls' signed quantities x multiplier minus shares negative" |
| core | `dskit/pipeline/option_exposure.py` (ADR-0310) | `HedgeRatio(_PARAMS=("rounding", "multiplier"))`: items with deltas and quantities to integer shares |
| tier 2 | `dskit/pipeline/libs/option_ledger.py` | `REBALANCES` gains `fixed` (shares from the order, held); `hedge.instrument` `stock | forward`; `hedge.margin_fraction`; forward P&L against the order's reference |
| child | `index_options/ledger_studies.py` | method key `hedge` (`instrument`, `delta`, `iv_source`, `rate`, `parity_strikes`, `margin_fraction`, `round_shares`, `mark`), `_hedge_ratios`, `_loss_row` with `ref`, `configured` rewrite, `_fill`, `_settlements`, `_ledger`, `_books` parity, validation (not with `kelly` or `sizer` in the first build) |
| child | `index_options/mio_execution_stress.py` | scenario `static_forward_hedge` (stage A) |
| tests | `tests/pipeline/test_option_structures.py`, `tests/pipeline_libs/test_option_ledger.py`, `children/index_options/tests/test_ledger_studies.py`, `test_mio_execution_stress.py` | below |
| docs | `dskit/pipeline/README.md` "What ships", the child README line, `mio-glossary.md` (hedge terms) | one line each |

Tests (RED first): hedged max loss by hand (the glossary's 5:1 call book with 300 sold shares); unbounded when the slope row is violated; `hedge=None` bit-identical to today; `RADIUS_BASELINES` and `SHOCK_BASELINES` digests unchanged with no `hedge` key; a `hedge` block whose deltas are forced to zero and margin 0 reproduces the unhedged result byte for byte except the hedge fields (the parity-at-zero test); `fixed` rebalance equals the study's settlement to the cent; `advance` never refuses a hedged settlement; the margin floor applied in the four places; rounding at half a share; recorded and derived replays carry and re-derive the hedge; the stress Gate 0 control unchanged; configs pin `window.end` below the seal.

Staged Sonnet plan (one subagent at a time, Claude orchestrates, skeptic review per stage): S0 ADR-0310 merged and its attribution read (gate 1); S1 core `option_structures` keyword plus tests; S2 tier-2 ledger `fixed` and `forward`; S3 child replay key, parity tests and the stress scenario, then the stage-A read on the saved 4x4 cells; S4 optimizer coefficients, replay routes, one-week timing check on 2021Q2; S5 hedged twin documents on the four quarters, two independent lenses before a real solve.

## Self skeptic review (author's own, labelled; not an independent lens)

- **Major, folded in:** booking short-sale proceeds as credit would inflate `cash` and the open-credit accounting; the hedge is a cash-settled forward.
- **Major, folded in:** a hedged book can be unbounded above where the unhedged one was not; the slope row replaces the call-count row, bit-identical at zero.
- **Major, folded in:** `_prepare` is cached per day across methods; the hedge is applied in the per-method `configured` step.
- **Major, folded in:** `advance` refuses a settlement below the reserve; the reserve rows carry the hedged loss, so the reserve covers the hedge leg by construction.
- **Minor, open:** the forward leg is marked at spot, ignoring remaining carry; owner question 8.
- **Minor, open:** integer shares lose up to half a share of delta per book.
- **Open, disclosed:** American assignment through ex-dividend is unmodelled for hedged and unhedged books alike; the first-order dividend sits inside the parity forward.
- **Open, unmeasured:** solve time after hedging; one-week check before any quarter.
- **Open, governance:** a hedged twin beside the nine is a new arm under a locked A0010; it is research evidence only until the owner says otherwise.

## Decisions for the owner (recommendation marked)

1. Order: (a) level 1 gate, then stage A replay, then stage B optimizer, level 2 deferred (recommended); (b) stage B directly; (c) level 2 first.
2. Instrument: (a) cash-settled forward at the parity forward (recommended); (b) stock with borrow and an onboarded dividend table; (c) both arms.
3. Delta: (a) Black-76 on the decision chain's parity forward, vendor iv, `F/S` chain rule, ADR-0310's code (recommended); (b) forecast-implied delta; (c) both arms.
4. Hedge ratio: (a) fixed at the decision, scaled by the filled quantities (recommended); (b) recomputed at fill.
5. Margin floor on the stock leg: (a) 0.15 of gross notional (recommended); (b) 0.5 (Reg T); (c) 0.
6. First hedged twin: (a) `w1_010_shock10_lp` (recommended: the primary, and its gate becomes a gamma gate); (b) `nominal_lp`; (c) both.
7. Governance: (a) research arm beside the nine, four selection quarters only, not a pick candidate (recommended); (b) amend A0010.
8. Mark of the forward leg: (a) the bars' spot (recommended); (b) the day's parity forward.

## Sources

Repo: `children/index_options/index_options/ledger_studies.py` (`_prepare`, `_loss_row`, `_shock_rows`, `_move_terms`, `_limits`, `_books`, `_fill`, `_settlements`, `_ledger`, `_method_steps`); `dskit/pipeline/libs/pyomo.py` (`WassersteinDual.attach`, `TransportPortfolioSolve` program contract); `dskit/pipeline/libs/option_ledger.py` (`_Hedge`, `OptionStructureLedger.hedge`, `FundedOptionAccount.advance`); `dskit/pipeline/option_structures.py` (`structure_max_loss`, `structure_payoff`); `children/index_options/index_options/nodes.py` (`CondorQuoteBacktest._benchmark`, `_usable_quote`); ADR-0310 and `dskit/pipeline/option_exposure.py` on `origin/claude/mio-greeks-diagnostics-20261109` (`8fcd1b8`); ADR-0193, ADR-0197, ADR-0278, ADR-0282, ADR-0297, ADR-0299 to ADR-0301 in `docs/architecture/decision-log.md`; `docs/research/distribution-modeling/2026-11-07-mio-selection-quarters-and-three-kelly-formulations.md`; `docs/explanations/mio-glossary.md`; `docs/decisioning/path.csv` (A0003, A0008, A0009, A0010); `docs/RE-ENTRY.md`.

Literature (bibliographic records checked by web search 2026-11-09):
- Bakshi, G. and N. Kapadia (2003), "Delta-Hedged Gains and the Negative Market Volatility Risk Premium", Review of Financial Studies 16(2), 527-566.
- Cao, J. and B. Han (2013), "Cross section of option returns and idiosyncratic stock volatility", Journal of Financial Economics 108(1), 231-249.
- Goyal, A. and A. Saretto (2009), "Cross-section of option returns and volatility", Journal of Financial Economics 94(2), 310-326.
- El Karoui, N., M. Jeanblanc-Picque and S. Shreve (1998), "Robustness of the Black and Scholes formula", Mathematical Finance 8(2), 93-126.
- Whalley, A. E. and P. Wilmott (1997), "An asymptotic analysis of an optimal hedging model for option pricing with transaction costs", Mathematical Finance 7(3), 307-324.
- Bertsimas, D., L. Kogan and A. W. Lo (2000), "When is time continuous?", Journal of Financial Economics 55(2), 173-204.
Cited from standard references, not fetched today:
- Carr, P. and L. Wu (2009), "Variance Risk Premiums", Review of Financial Studies 22(3), 1311-1341.
- Corsi, F. (2009), "A Simple Approximate Long-Memory Model of Realized Volatility", Journal of Financial Econometrics 7(2), 174-196.
