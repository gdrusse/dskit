# Starting from scratch: what the research says the per-minute MIO should be

**Status:** research synthesis, 2026-09-26. Three independent research passes
(literature on minute-frequency MIO trading; how practitioners turn
multi-horizon forecasts into positions; a from-scratch comparison of five
formulations) were run after the owner asked whether ADR-0188's formulation
is the right one. Research only; no skeptic loop (owner ruling 2026-09-26).
Companion to ADR-0188 (PROPOSED, locked at `710317d`, not approved) and to
`2026-09-26-evaluation-and-literature.md`.

## The question in the owner's terms

Every minute t: the state is the shares we carry and our cash. The inputs are,
per stock, a forecast of the cumulative return over the next 1, 2, ..., H_i
minutes (H_i is that stock's admitted horizon, 2 to 10), with calibrated
scenario dispersion and a per-horizon false-signal rate. The decision is how
many whole shares of each stock to hold from the next bar on; the orders are
the differences from what we carry. The objective is long-run growth
(fractional Kelly) with a risk cap. At t+1 the same problem is solved again
from the new state. Nothing from the previous minute's solve is binding.

## What the three passes agree on

1. **There is no off-the-shelf answer.** No published work solves a
   whole-share MILP re-optimized every minute for a small book. The nearest
   mechanics come from the MILP portfolio line (Mansini, Ogryczak and
   Speranza 2015; Mansini and Speranza 1999, who prove minimum-lot selection
   is NP-complete; Bertsimas, Darnell and Soucy 1999 at GMO), the CVaR LP of
   Rockafellar and Uryasev 2000, and the risk-constrained Kelly program of
   Busseti, Ryu and Boyd 2016. The combination with per-minute re-solving is
   our own; the existing scenario-utility doorway already implements the
   right building blocks (tangent-linearized CRRA, CVaR block, integer lots,
   exact recompute).
2. **Re-solving a single-period problem every minute, with the state
   carried, loses little against full multi-period planning in THIS
   regime.** The theory (Garleanu and Pedersen 2013; Collin-Dufresne, Daniel
   and Saglam 2020) locates the value of multi-period planning in quadratic,
   persistent costs and in signals that decay at different speeds; the
   empirical MPC edge reported by Li, Uysal and Mulvey 2022 is "in the
   presence of market impact costs". Our costs are proportional and our
   forecasts refresh every minute, which is exactly the case Nystrup, Boyd,
   Lindstrom and Madsen 2019 describe as "the optimal control actions are
   reconsidered anyway". Boyd et al. 2017 present single-period and
   multi-period as one framework; the multi-period version's extra machinery
   buys the ability to plan intermediate trades that a per-minute re-solve
   makes on its own.
3. **Do not pick a horizon offline; use the whole curve.** Every pass rejects
   "one forecast horizon per stock chosen in advance" (today's rule, the
   admitted cap). Two ways to use all horizons: collapse the curve into one
   decay-weighted "aim" return per stock (the Garleanu-Pedersen and
   Grinold 2006 logic, restated by Sneddon 2008 and by Axioma's Jeet,
   Sivaramakrishnan and Vandenbussche 2015), or let the optimizer allocate a
   stock's shares across exit horizons inside the solve. The second is a
   scenario-indexed MILP discretization of the aim-portfolio idea and needs
   no offline weighting.
4. **Inaction must come from the costs, not from a knob.** Under proportional
   costs the optimal policy is a no-trade region (Constantinides 1986; Davis
   and Norman 1990; Liu 2004 for many assets), trade-to-the-boundary and
   bang-bang rather than partial adjustment (de Lataillade, Deremble,
   Potters and Bouchaud 2012; Martin 2014's cube-root band). A program that
   charges every trade its proportional cost and values a holding at its
   liquidation value produces that region on its own; the cube-root law is a
   diagnostic of scale, not something to hard-code (the owner's 2026-09-25
   ruling).
5. **Growth objective plus risk cap is the established form.** Fractional
   Kelly for estimation and regime error (MacLean, Thorp and Ziemba 2010),
   with a convex tail constraint (Busseti, Ryu and Boyd 2016: drawdown
   probability; Rockafellar-Uryasev CVaR as we have). HiGHS cannot solve a
   quadratic objective with integer variables and silently drops the
   quadratic term, so the tangent linearization we already use is mandatory,
   not optional.
6. **Retail realities that belong in the policy.** In a cash account the
   pattern-day-trader rule does not apply; the binding rule is settled
   funds: buying with unsettled sale proceeds and selling again before the
   sale settles risks a good-faith violation (three in twelve months brings a
   90-day settled-cash restriction). Odd lots are excluded from the NBBO and
   receive measurably worse fills; true retail price improvement is about
   1-5 bp after correcting the NBBO benchmark (Schwarz et al. 2025). None of
   the academic policies model these; they are feasibility constraints to
   layer on. One pass reports that FINRA replaced the margin-account PDT rule
   with an intraday-margin standard in June 2026 (Regulatory Notice 26-10);
   the plan's §11 could not locate that notice earlier this month, so treat
   it as unverified until read from FINRA directly.

## The five formulations compared (from the third pass)

| | A. one fixed horizon per stock (today) | B. exit-horizon tranches | C. full multi-period plan (ADR-0188 as locked) | D. aim + band + rounding | E. band only |
|---|---|---|---|---|---|
| uses all horizons | no | yes | yes, and the joint path | via a proxy | no |
| values an early exit | no | yes (distribution over exit times) | yes, most completely | implicitly | no |
| re-optimizes held positions | only if holdings are inputs | yes | yes | partly | yes |
| inaction from costs | yes | yes | yes | NO (a calibrated rule) | NO |
| integer variables (N = 12) | 24 | 24 (+ continuous tranches) | up to ~140 plus binaries | 12-24 | 0 |
| expected solve | tens of ms | tens to hundreds of ms | 0.3-3 s, tail risk at 10 s | fastest | fastest |
| held past its horizon | undefined | valued at the last calibrated lead, cash after | forced flat by construction | undefined | undefined |
| owner knobs | gamma, CVaR, false-signal budget, plus a brittle horizon pick | gamma, CVaR, false-signal budget | the same plus a planning horizon and stage weights | band constants needing recalibration | band constants |

## Recommendation

**Adopt B.** Every minute, one program takes the shares carried and the cash,
plus each stock's forecast curve and scenarios, and chooses:

- `b_i`, `s_i`: whole shares to buy or sell now (fill next bar), `q_i = h_i +
  b_i - s_i >= 0`;
- `e_i(k) >= 0` for `k = 1..K_i`: the shares of `q_i` expected to be sold at
  minute `k` (`sum_k e_i(k) = q_i`), continuous because they are an
  expectation, never an order;
- objective: expected tangent-linearized CRRA of terminal wealth `W_o = c' +
  sum_i sum_k e_i(k) (p_i (1 + r_io(k)) - exit cost)`, where `c'` is cash
  after today's trades at their proportional costs;
- one Rockafellar-Uryasev CVaR row on `W_o`; one false-signal capital row
  `sum_{i,k} pi_i(k) p_i e_i(k) <= q sum_{i,k} p_i e_i(k)` (ADR-0088's rule
  applied per horizon tranche); no band, cardinality or minimum-ticket rows.

Why B and not the others: A discards most of the curve and hard-codes a
horizon; C (the locked ADR-0188) pays for a full path of future trades, with
heavier coupling and real latency tail risk, to gain what the every-minute
re-solve already provides; D and E make inaction a rule rather than a
consequence of costs. B is the exit-schedule simplification proposed to the
owner during question C, and it is what every pass converges on.

What B keeps from ADR-0188 (the integration work survives): holdings as
inputs and target holdings as outputs, sells and trims as decisions, all
horizons in one solve, joint scenario paths coherent across stocks and
horizons, the share book on dskit's `PositionBook`, the path-row bundle
contract, the decider's `positions_solve` rules, the routing classification,
the standalone rerun document, and the test map. What B removes: the plan
variables `b_ik`, `s_ik` for `k >= 1`, the expected-path cash rows, the
`2H` envelope widening, the plan-step question C, and about a third of the
ADR's formulation text.

Owner questions this changes: C disappears; A (flat by close) and B (zero
expected gain past the cap, dispersion kept) are already ruled and carry
over unchanged (`K_i(t)` and the last-decision rule apply to the tranches);
D, E, F, G, H, I, J, K, L stand.

## Minimal test plan for B (from the third pass, merged with the ADR's)

1. Frictionless dominance: with zero costs and slack caps a dominant stock is
   bought to the no-leverage limit.
2. Cost break-even: one more share is bought exactly when its expected path
   gain exceeds its round trip, to whole-share granularity.
3. Horizon-use discriminator: two curves equal at `H_i` but different in
   between produce different tranches and different orders; a fixed-horizon
   policy could not tell them apart.
4. CVaR recomputed independently respects the cap; loosening it never lowers
   the objective.
5. False-signal isolation: identical returns, different `pi_i(k)`, a tight
   budget: exposure shifts beyond what the haircut alone does.
6. Integer, cash and no-leverage stress: adversarial states, including cash
   below one share of anything; every answer whole, non-negative, funded.
7. Statelessness: a cold solve at `t + 1` equals the live decision; nothing
   from the previous minute's tranches persists.
8. Solver trust: a pre-solve lint refuses any quadratic term with integers
   (HiGHS drops it silently); 100+ randomized `N = 12, S = 128` instances
   solve with median under 3 s and a 10 s time limit halts, never trades.

## Sources

- Bertsimas, D., C. Darnell and R. Soucy (1999). Portfolio construction through mixed-integer programming at GMO. Interfaces 29(1).
- Bertsimas, D., and B. Stellato (2021). Online mixed-integer optimization in milliseconds. INFORMS Journal on Computing. https://arxiv.org/abs/1907.02206
- Boyd, S., E. Busseti, S. Diamond, R. N. Kahn, K. Koh, P. Nystrup and J. Speth (2017). Multi-Period Trading via Convex Optimization. Foundations and Trends in Optimization 3(1). https://arxiv.org/abs/1705.00109
- Busseti, E., E. K. Ryu and S. Boyd (2016). Risk-constrained Kelly gambling. Journal of Investing 25(3). https://arxiv.org/abs/1603.06183
- Collin-Dufresne, P., K. Daniel and M. Saglam (2020). Liquidity regimes and optimal dynamic asset allocation. Journal of Financial Economics 136(2).
- Constantinides, G. M. (1986). Capital market equilibrium with transaction costs. Journal of Political Economy 94(4).
- Davis, M. H. A., and A. R. Norman (1990). Portfolio selection with transaction costs. Mathematics of Operations Research 15(4).
- de Lataillade, J., C. Deremble, M. Potters and J.-P. Bouchaud (2012). Optimal trading with linear costs. Journal of Investment Strategies 1(3). https://arxiv.org/abs/1203.5957
- Garleanu, N., and L. H. Pedersen (2013). Dynamic trading with predictable returns and transaction costs. Journal of Finance 68(6).
- Gerhold, S., P. Guasoni, J. Muhle-Karbe and W. Schachermayer (2014). Transaction costs, trading volume, and the liquidity premium. Finance and Stochastics 18(1).
- Grinold, R. C. (2006). A dynamic model of portfolio management. Journal of Investment Management 4(2).
- Huangfu, Q., and J. A. J. Hall (2018). Parallelizing the dual revised simplex method. Mathematical Programming Computation 10(1). HiGHS: https://highs.dev/
- Jeet, V., K. K. Sivaramakrishnan and D. Vandenbussche (2015). Multi-period portfolio optimization with alpha decay. Axioma Research Report 55; IJFERM 2(4), 2018.
- Li, X., A. S. Uysal and J. M. Mulvey (2022). Multi-period portfolio optimization using model predictive control with mean-variance and risk parity frameworks. European Journal of Operational Research 299(3).
- Liu, H. (2004). Optimal consumption and investment with transaction costs and multiple risky assets. Journal of Finance 59(1).
- MacLean, L. C., E. O. Thorp and W. T. Ziemba (2010). Long-term capital growth: the good and bad properties of the Kelly and fractional Kelly capital growth criteria. Quantitative Finance 10(7).
- Mansini, R., and M. G. Speranza (1999). Heuristic algorithms for the portfolio selection problem with minimum transaction lots. European Journal of Operational Research 114.
- Mansini, R., W. Ogryczak and M. G. Speranza (2015). Linear and Mixed Integer Programming for Portfolio Optimization. Springer.
- Martin, R. J. (2014). Optimal trading under proportional transaction costs. Risk, July 2014.
- Muhle-Karbe, J., M. Reppen and H. M. Soner (2017). A primer on portfolio choice with small transaction costs. Annual Review of Financial Economics 9.
- Nystrup, P., S. Boyd, E. Lindstrom and H. Madsen (2019). Multi-period portfolio selection with drawdown control. Annals of Operations Research 282.
- Rockafellar, R. T., and S. Uryasev (2000). Optimization of conditional value-at-risk. Journal of Risk 2.
- Schwarz, C., et al. (2025). The actual retail price of equity trades. Journal of Finance. https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13467
- Stanford GSB, Modernizing odd lot trading (working paper). https://www.gsb.stanford.edu/faculty-research/publications/modernizing-odd-lot-trading
- Unverified in this pass: Sneddon 2008 (title and venue); FINRA Regulatory Notice 26-10 (June 2026 intraday-margin standard); Kolm and Ritter, From strategic policy to tactical trades (SSRN 7384838, abstract only).
