# The intraday_equities MIO trading policy: what runs today, and what the literature says

**Status:** research, 2026-09-26. Design only; nothing built. Companion to the
PROPOSED ADR-0188 (`docs/architecture/decision-log.md`). Every file:line below
is on `origin/main` at `b87a1a3`. The plan is
`docs/plans/2026-09-intraday-equities-mio.md` ("the plan").

Question 1: what does the MIO decide each minute, as it actually runs in the
ADR-0185/0186 retrain simulation, and where does that depart from the plan?
Question 2: given per-name forecasts at 1..10 minute horizons refreshed every
minute, twelve admitted names, and proportional costs, what buying and selling
policy maximizes long-run growth?

## Part 1. The MIO as it runs today

### 1.1 What a minute decides: buys only, from an empty book

- `MioDecider.decide` (`intraday_equities/simulation.py:1207-1262`) is the
  whole per-minute policy. For each lead group in ascending order (1212) it
  builds one `EquityKellyMIO` (1226), hands it `positions: {}` (1235), runs it
  (1249), and turns `trades` into orders with `side: "buy"` (1260). A `sell`
  in the solver's answer raises (1258-1259). So a minute can only add new
  lots; it can never sell, trim or extend.
- Exits are forced, never decided. `EquityReplay._process_exits`
  (`intraday_equities/replay.py:1865-1879`) closes lots whose `expiry_index`
  is due; `HorizonBook.open_lot` sets `expiry_index = fill_index + lead`
  (934-935). `configs/fill-policy.json:12-14` pins `forced_exit_at:
  "horizon_expiry"`, and the closed vocabulary admits nothing else
  (`replay.py:164`). No order shape closes a position early: a decision needs
  a `lead >= 1` (`_enqueue_decision`, 1354) and at its fill bar it OPENS a
  lot (`_process_entries`, 1881-1942, `open_lot` at 1933). A "sell" decision
  would open a short lot, not close a long one.
- The optimizer's own sell path exists but is never reached. A held name
  absent from the bundle enters as a mandatory exit (`nodes_capital.py:
  1473-1490`), `model.s` is bounded by `held` (`dskit/pipeline/libs/pyomo.py:
  1152-1155`), and the sell-side band floor is `min(band, held)` (1634).
  With `positions: {}` every one of these rows is inert.

### 1.2 Held positions are skipped

- `MioDecider._exclusion` (`simulation.py:1276-1279`) drops any name with an
  open lot (`open_lot_at_decision`). `MinuteMioDecider._exclusion`
  (1401-1411) adds `pending_entry_at_decision` and `exit_after_close`;
  `_context` (1373-1380) subtracts the reserved cost of queued buys
  (`_reserved`, 1382-1395).
- The replay does know the book. `EquityReplay._portfolio` (`replay.py:
  1383-1438`) reports `positions` (1421-1428), `pending` (1436) and NAV
  (1435). The decider reads `positions` only to exclude names (1268) and
  gives the MIO an empty book (1235). ADR-0186 records this as "Open lots,
  the explicit rule" and defers `h_i` to an MIO change.
- Consequence: a lot opened on a 5-minute forecast is held for exactly 5 bars
  whatever the next four minutes' forecasts say, and the name is untradeable
  for those bars.

### 1.3 The per-lead-group split

- `MioDeciderNode.run` emits the ascending lead order (`simulation.py:1501`);
  the decider solves one program per lead (1212-1261) and passes
  `cash_after` to the next (1261). Groups in the stopped run: 2, 5, 9, 10.
- The split is forced by two refusals: `_bundle_problems` refuses mixed
  leads (`nodes_capital.py:567-579`) and mismatched weights (688-696);
  `ForecastBundle` refuses them too (`forecast_bundle.py:589-605`).
- Each group solves with `gross_limit = NAV` (1237) and its own
  `cvar_limit` (`configs/run-retrain-simulation-template.json:88`, $500).
  With four groups the book can carry up to four times the CVaR budget and
  four times NAV in planned gross notional (the shared cash is the only
  joint bound). `docs/explanations/mio-optimizer-formulation.tex:451-455`
  states this.
- Scenarios are drawn per lead: `ForecastPublisher._outcome` (`simulation.py:
  715-739`) calibrates one panel per lead (`_residual_panel`, 682-693) and
  `BlockConformalInterval.scenarios` seeds the draw from that panel's digest
  (`dskit/pipeline/outcome_interval.py:959-961`). Scenario `o` of the 2-minute
  group is not the same state of the world as scenario `o` of the 5-minute
  group, so a joint wealth row across groups cannot be assembled from
  today's releases.
- The forecast term structure is discarded. The walk scores every lead with
  the one fitted model (`nodes.py:5804-5806`); the run dir
  `pipeline_runs/lean-pooled-h10-minute-walk-wf-2024-03-29-2026-02-28-8a51c3e7`
  holds `scan_h01..scan_h10/trade_predictions.parquet` covering every
  admitted name at every lead up to its calibrated horizon. `_MinuteWalk.
  trade_yhat` keeps only `(symbol, lead_map[symbol])` (`simulation.py:
  1000-1003`): one lead per name, the cap.

### 1.4 Objective, CVaR, HFDR, band and costs

- Program (`pyomo.py:1024-1283`): cash after trades charges entry and exit
  costs per share (1197-1204); cash floor (1205); buying power with sale
  credit (1206-1216); gross cap (1217-1220); scenario wealth values every
  target share at `price*(1+r) - exit_cost_per_share` (1222-1232); tangent
  planes of CRRA utility (1234-1241); Rockafellar-Uryasev CVaR (1243-1251);
  objective = weighted tangent utility (1253-1255).
- Domain rows (`nodes_capital.py:1577-1652`): HFDR `sum_i (pi_widened_i - q)
  x_i <= 0` (1628-1631), then the band rows (1641-1652).
- Costs: `SchwabCostModel` (`nodes_capital.py:193-471`); each side pays the
  quoted half-spread x `eq_ratio` x the fill-minute multiplier, sells add
  uncapped TAF and Section 31 (`fill-policy.json:15-41`). Sizing keys the
  rate on `portfolio.fill_ms` (1500-1507); the replay bills the same rate at
  the fill (`replay.py:1920-1924`). The horizon exit is priced at the ENTRY
  fill minute (1519; tex 152-155).
- The band is inert AND hard-coded. `band_shares = lot * ceil(band_bps *
  1e-4 * max(price*held, min_ticket) / (price*lot))` (1527-1529) is 0 with
  `held = 0` and `min_ticket = 0`; the tex says so (457-458). Where it would
  bind, it is the declared knob `band_bps` (template line 92), not a quantity
  derived from the cost model.
- Values in force (template 84-98): gamma 2.0, 32 tangents, 128 scenarios,
  CVaR 0.95 / $500 per solve, cardinality null, min_ticket 0, hfdr_q 0.20,
  band_bps 10, max_position_notional $5,000, staleness 0, coverage floor
  0.53. No `solver_options` are declared, so no solver `time_limit` breaker
  is set (the doorway hands `solver_options` through verbatim, `pyomo.py:
  532-542`).

### 1.5 What "long-only" means here

- `nonneg_q` (`pyomo.py:1179`) and `s <= held` (1152-1155) keep every
  target non-negative; the plan §11 excludes shorting (no locate, borrow or
  Reg SHO in the cost model). The replay itself is side-agnostic (a lot's
  side flips at exit, `replay.py:1870`), so shorts are mechanically possible
  but never emitted. Long-only plus buys-only plus forced exits means the
  strategy's only lever is entry sizing.

### 1.6 Departures from the plan (each to be raised with the owner)

| # | Plan | Today | Where |
|---|---|---|---|
| D1 | §5.1 (354-368): "runs the sell/hold/buy model; mandatory exits enter as constrained sell deltas" | buys only; sells raise | `simulation.py:1257-1260` |
| D2 | §5 (345-349): index over held names plus new candidates, with opening shares `h_i` | held names excluded, `h_i = 0` | `simulation.py:1235, 1276-1279` |
| D3 | §3.3(b) (144-153): signed deltas from current inventory with in-model inaction | inventory withheld; band inert; where active it is the `band_bps` knob | `nodes_capital.py:1527-1529`, template 92 |
| D4 | §7 (570-572): normalize OR separate horizons | separate (per lead group, sequential cash); owner ruled joint on 2026-09-26 | `simulation.py:1212-1261` |
| D5 | §5.3 C6/C12 (412-417): one gross row and one CVaR row per tick | one per lead group per tick (up to 4 x $500, 4 x NAV) | template 88; tex 451-455 |
| D6 | §5 (348-349): a held name needs a scenario row or an explicit forced-exit instruction | exits are the replay's expiry rule; the optimizer never sees them | `replay.py:1865-1879` |
| D7 | §3.3(a) (118-142) `lambda_t_bps`; §3.3(c) (155-161) counterfactual ledger | not built | `nodes_capital.py:31-34` |
| D8 | §5.3 (421-432): TAF per-order cap linearized | uncapped flat rate (conservative, disclosed) | `nodes_capital.py:21-30` |
| D9 | §11 (648-653): measure signal decay; refuse a horizon whose half-life is under 5x decision latency | not measured; every admitted lead trades | no code |
| D10 | §3.2 (93-106): risk-tolerance schedule `f_t` from contributions | fixed gamma 2.0 | template 84 |
| D11 | §4.4 (303-308): `time_limit` 8 s breaker | no `solver_options` declared | template (absent) |
| D12 | §5.1 (365-368): no-solve only when eligible set AND inventory are empty | decider-side skips: pending reservation, exit-after-close (ADR-0186, disclosed) | `simulation.py:1401-1411` |
| D13 | §11 (684-689): "if holding periods routinely overlap, the honest form is a 2-3 step MPC ... a separate ADR" | at 1-minute cadence with 2-10 minute leads, holdings overlap every minute; no MPC | ADR-0186 |

Built as planned: fail-closed bundle and cap pins, integer shares, the
Schwab per-share/bps cost model, HFDR (ADR-0088), scenario recentering by
`pi_hat`, CVaR, the post-solve exact recompute (`pyomo.py:1285-1435`), and
determinism pins.

### 1.7 The in-repo evidence on horizons

`pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/gates.json`
(the stopped run's own gate stage; Bonferroni over 90 cells, alpha 0.05,
60 cells passed). Out-of-sample R2 of the vol-scaled cumulative label by
horizon, `*` = passed after correction:

| unit | cap | h1 | h2 | h3 | h4 | h5 | h6-h10 |
|---|---|---|---|---|---|---|---|
| ADBE | 5 | .0175* | .0085* | .0038* | .0025* | .0018* | |
| CIEN | 2 | .0133* | .0073* | .0025 | .0021* | .0013 | |
| IWM | 2 | .0041* | .0028* | .0012 | .0014* | .0012 | |
| LITE | 5 | .0160* | .0086* | .0034* | .0026* | .0021* | |
| LLY | 2 | .0119* | .0069* | .0029 | | | |
| LRCX | 10 | .0276* | .0133* | .0059* | .0048* | .0036* | .0036* .0031* .0061* .0025* .0020* |
| LULU | 5 | .0176* | .0091* | .0038* | .0034* | .0025* | |
| MSTR | 9 | .0275* | .0166* | .0061* | .0048* | .0031* | .0025* .0022* .0045* .0018* .0015 |
| NOW | 5 | .0355* | .0179* | .0076* | .0059* | .0041* | |
| PANW | 5 | .0117* | .0068* | .0033* | .0032* | .0026* | |
| TER | 5 | .0187* | .0095* | .0035* | .0026* | .0028* | |
| XLK | 5 | .0112* | .0058* | .0032* | .0019* | .0013* | |

If a model predicted only the next minute, the cumulative label's R2 would
fall exactly like `1/h`. It falls faster (ADBE h3 is 22% of h1, `1/3` would
be 33%): most of the predictable move is in the first one to two minutes,
and the increments after minute 2 carry little skill. Two consequences.
First, a lot held for its cap horizon (5 or 10 bars) sits through several
minutes of near-zero expected increment while paying nothing to leave
earlier: the exit time is a real decision. Second, a 1-minute edge is of the
same order as the round trip: a 1-minute conditional mean of roughly
`sqrt(R2) x sigma` is about 1-2 bp for these names, against a round trip of
`2 x half_spread x 0.25` plus sell fees, 0.4-2.9 bp. Whether a signal is
worth trading is decided by the cost model, at the margin, every minute.

## Part 2. What the literature says, and what applies at one-minute cadence

### 2.1 Growth-optimal investing is a multi-period problem once costs exist

Maximizing expected log wealth per period is growth-optimal for sequential
independent opportunities (Kelly 1956, Bell System Technical Journal 35(4);
Breiman 1961, 4th Berkeley Symposium; Algoet and Cover 1988, Annals of
Probability 16(2)); fractional Kelly trades growth for drawdown (MacLean,
Thorp and Ziemba 2010, Quantitative Finance 10(7)). The per-tick log program
in the plan is exactly this. It stops being the full answer under two
conditions the child now meets: transaction costs, and overlapping holdings
with serially correlated signals (plan §11). With costs, the position is a
state variable and the problem is a dynamic program. For log utility under
proportional costs the solution is known in the two-asset diffusion case:
keep the risky fraction inside an interval and trade only at its edges,
with minimal effort (Taksar, Klass and Assaf 1988, Mathematics of Operations
Research 13(2)). Constantinides (1986, Journal of Political Economy 94(4))
showed the no-trade region is wide even for small costs and that the welfare
loss from not rebalancing is second order; Davis and Norman (1990,
Mathematics of Operations Research 15(4)) proved the three-region structure
(buy, no-trade cone, sell) and that trading happens only at the cone's
boundary, to the boundary, never to the frictionless target; Shreve and
Soner (1994, Annals of Applied Probability 4(3)) made it rigorous.

With several risky assets and both fixed and proportional costs, Liu (2004,
Journal of Finance 59(1)) shows that for uncorrelated assets the region is a
box: each asset's dollar position is kept between two levels and trades go
to that asset's own targets; correlation couples the boxes; and costs
"can significantly diminish the importance of stock return predictability".

Small-cost asymptotics give the width. Rogers (2004, Contemporary
Mathematics 351) explains the scaling: a region of half-width `delta`
costs about `epsilon / delta` in trading and `delta^2` in misallocation, so
`delta ~ epsilon^(1/3)` and the welfare loss is `epsilon^(2/3)`. Gerhold,
Guasoni, Muhle-Karbe and Schachermayer (2014, Finance and Stochastics 18(1))
give the constant for CRRA `gamma` with Merton fraction `pi*` and spread
`epsilon`:

    pi_(+/-) = pi* +/- ( 3/(2 gamma) * pi*^2 (1 - pi*)^2 * epsilon )^(1/3)

with `gamma = 1` the growth-optimal case; the liquidity premium is spread
times share turnover times a universal constant. Muhle-Karbe, Reppen and
Soner (2017, Annual Review of Financial Economics 9) extend the asymptotics
to mean-reverting expected returns (the closest continuous-time analogue of
a decaying forecast) and Martin (2014, Risk; arXiv 1204.6488) to a
multifactor target: the band width is proportional to the cube root of the
cost and to the 2/3 power of the target position's volatility, so a FASTER
signal gets a WIDER buffer, and the band's displacement from the frictionless
position scales as cost^(2/3).

What carries to one-minute cadence: our costs are proportional (half-spread
x EQ x time-of-day, plus per-share and per-notional sell fees; no impact at
our sizes, plan §3.4), so the qualitative policy is trade-to-the-boundary
with inaction, and a "rebalance to the target each minute" loop is wrong by
construction. Entry has to clear the ROUND TRIP plus a risk premium; exit
does not (the entry cost is sunk), which is the asymmetry the cone
expresses. What does not carry: the closed-form widths assume an infinite
horizon, a diffusing frictionless target and no integrality. At 1-10 minute
horizons with a finite valuation date, whole shares and a CVaR row, they are
diagnostics of scale, not formulas to hard-code. The band should therefore
be a CONSEQUENCE of putting every cost on every trade inside the objective,
not a `band_bps` constant (plan §3.3(b) already forbids a post-solve band;
the owner ruling of 2026-09-25 removes the standing constant).

### 2.2 Combining forecasts across horizons and signal decay

Garleanu and Pedersen (2013, Journal of Finance 68(6)) solve dynamic trading
with return predictors of different mean-reversion speeds under QUADRATIC
costs in closed form: the optimal position is a weighted average of the
current position and an "aim" portfolio, `x_t = (1 - lambda) x_(t-1) +
lambda * aim_t`; the aim is a Markowitz portfolio in which each signal is
discounted by its decay, so slower-decaying signals get more weight ("aim
in front of the target", "trade partially toward the aim"). Garleanu and
Pedersen (2016, Journal of Economic Theory 165) is the continuous-time
version; Collin-Dufresne, Daniel and Saglam (2020, Journal of Financial
Economics 136(2)) add regime-switching costs and volatility: the aim is a
weighted average of the conditional mean-variance portfolios over future
states and trading is faster in persistent, liquid states. Grinold (2006,
Journal of Investment Management 4(2)) is the practitioner form: a linear
rule moving part-way to a target, with one rate for information flow and
one for trading. Under LINEAR (proportional) costs and a bounded position,
de Lataillade, Deremble, Potters and Bouchaud (2012, Journal of Investment
Strategies 1(3); arXiv 1203.5957) show the optimum is a threshold policy on
the predictor with an explicit equation for the threshold as a function of
cost and predictor persistence, and they connect that threshold to the
no-trade band of the quadratic-risk case. Passerini and Vazquez (2015,
arXiv 1501.03756) treat general alpha predictors with linear costs plus
temporary impact and give the no-trade zone with market orders and
practical recipes. Ma and Smith (2025, arXiv 2502.04284) formulate the
single-asset alpha-decay problem with costs as a Markov decision process and
give the small-cost asymptotics of the policy. On the multi-period
optimization side, Jeet, Sivaramakrishnan and Vandenbussche (Axioma Research
Report 55, 2015; International Journal of Financial Engineering and Risk
Management 2(4), 2018) combine a fast-decaying short-term alpha with a
slow long-term one in a two-stage multi-period program and show it beats
the single-period model; Lehalle and Neuman (2019, Finance and Stochastics
23(2)) solve execution with a Markovian (Ornstein-Uhlenbeck) signal against
transient impact, explicit for the OU case.

What carries: the ranking principle. A forecast that will still be there in
five minutes deserves more position than one that is gone in one, because
the cost of acting is paid once and the gain accrues over the holding time.
The quadratic-cost closed forms do NOT carry: retail costs are proportional,
and quadratic costs would price a 10-share order as almost free and never
produce inaction. Nor does a separately estimated "decay rate": the child
already publishes the whole term structure `yhat(t, h)` for `h = 1..cap`
every minute (§1.3), and the gate certifies which leads carry skill. The
term structure IS the decay curve, name by name and minute by minute, so the
right object is a multi-period program that reads it directly (Boyd et al.
2017, Foundations and Trends in Optimization 3(1), §5: "plan a sequence of
trades ... using estimates of future quantities ... execute only the
first"). Nystrup, Boyd, Lindstrom and Madsen (2019, Annals of Operations
Research 282) note the practical point: when forecasts are refreshed every
period, model predictive control costs nothing extra because the plan is
reconsidered anyway. Busseti, Ryu and Boyd (2016, Journal of Investing 25(3))
show that Kelly with a convex risk constraint is the disciplined form of
"fractional" Kelly, which is what the log program plus CVaR already is.

### 2.3 Re-optimizing held positions every minute

The dynamic-programming results above all have the position as state.
Every minute the question for a held name is not "would I buy this now" but
"is the expected remaining gain, net of risk, worth more than liquidating
now", with the entry cost sunk and the exit cost paid either way. Three
outcomes follow without any rule: hold (expected remaining increment
positive and risk acceptable), trim (risk or a cash call makes a smaller
position better), exit early (the forecast has turned or has decayed to
nothing), and, symmetrically, extend past the original lead (the term
structure still says up). Under the current policy the last three are
impossible and the first is forced. Constantinides' second-order result is
also the reason the change matters less for P&L through rebalancing than
through NOT paying round trips: the gain from re-optimizing comes from the
trades it avoids and from exiting dead positions early, not from chasing
the target.

### 2.4 Normalizing mixed horizons into one solve

Plan §7 requires horizons to be normalized or separated before they share a
scenario row; today they are separated (§1.3). The multi-period program
normalizes them: every name's exposure is valued in ONE terminal wealth
number at ONE instant `t + H` (`H` the longest calibrated horizon on the
tick), through a scenario PATH per name that ends in liquidation value at
that name's own calibrated horizon and cash afterwards. A 2-minute name and
a 10-minute name then sit in the same `W_o`, the same tangent objective, the
same CVaR row and the same cash path. This needs scenario paths that are
coherent across leads and names, which today's per-lead draws are not
(§1.3): one draw of calibration rows must supply every `(name, lead)` cell
of scenario `o`. `dskit.pipeline.outcome_interval.ScenarioSet` already
defines scenario `o` as "the SAME state of the world in every array"
(`outcome_interval.py:623-628`); the panel only has to be widened from
"names at one lead" to "(name, lead) cells".

### 2.5 Latency and decay at minute cadence

Plan §11 asks to refuse any horizon whose half-life is under about five
times decision latency. Today's solve is about 0.4 s per lead group (RE-ENTRY,
2026-09-26); one joint solve is expected near that (ADR-0186 measured a
single 25-name group at 0.39 s median, 0.56 s max). The fill is the next
minute's open regardless of solve time, so the binding latency is the
one-minute bar, and the h1 forecast is partly stale by the fill. The
program does not need a separate decay rule: the h1 increment is what the
model predicts for the first bar after the decision, and the cost model
decides whether that is worth acting on. Realized decay should still be
measured (ADR-0188's test plan) so the plan's §11 refusal can be applied
with a number rather than a guess.

### 2.6 Summary of what the design must do

1. One program per minute over all admitted names, with the held shares as
   inputs and target shares as outputs; sells and trims are ordinary
   decisions.
2. Every trade, on the executed step and on every planned step, pays the
   cost model; no `band_bps`, no cardinality, no minimum ticket. Inaction
   is what the objective returns when the gain does not clear the cost.
3. The forecast term structure drives a path of expected returns per name;
   the terminal wealth at one common instant is the object of the log
   utility, the CVaR row and the HFDR row.
4. Scenario paths are one joint draw across names and leads.
5. Only the first step executes; the rest is a plan, re-solved next minute
   (model predictive control).

## Sources

- Algoet, P. H., and T. M. Cover (1988). Asymptotic optimality and asymptotic equipartition properties of log-optimum investment. Annals of Probability 16(2).
- Boyd, S., E. Busseti, S. Diamond, R. N. Kahn, K. Koh, P. Nystrup and J. Speth (2017). Multi-Period Trading via Convex Optimization. Foundations and Trends in Optimization 3(1), 1-76. https://arxiv.org/abs/1705.00109
- Breiman, L. (1961). Optimal gambling systems for favorable games. 4th Berkeley Symposium.
- Busseti, E., E. K. Ryu and S. Boyd (2016). Risk-constrained Kelly gambling. Journal of Investing 25(3), 118-134. https://arxiv.org/abs/1603.06183
- Collin-Dufresne, P., K. Daniel and M. Saglam (2020). Liquidity regimes and optimal dynamic asset allocation. Journal of Financial Economics 136(2), 379-406.
- Constantinides, G. M. (1986). Capital market equilibrium with transaction costs. Journal of Political Economy 94(4), 842-862.
- Davis, M. H. A., and A. R. Norman (1990). Portfolio selection with transaction costs. Mathematics of Operations Research 15(4), 676-713.
- de Lataillade, J., C. Deremble, M. Potters and J.-P. Bouchaud (2012). Optimal trading with linear costs. Journal of Investment Strategies 1(3). https://arxiv.org/abs/1203.5957
- Garleanu, N., and L. H. Pedersen (2013). Dynamic trading with predictable returns and transaction costs. Journal of Finance 68(6), 2309-2340. https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12080
- Garleanu, N., and L. H. Pedersen (2016). Dynamic portfolio choice with frictions. Journal of Economic Theory 165, 487-516.
- Gerhold, S., P. Guasoni, J. Muhle-Karbe and W. Schachermayer (2014). Transaction costs, trading volume, and the liquidity premium. Finance and Stochastics 18(1), 1-37. https://arxiv.org/abs/1108.1167
- Grinold, R. C. (2006). A dynamic model of portfolio management. Journal of Investment Management 4(2), 5-22.
- Jeet, V., K. K. Sivaramakrishnan and D. Vandenbussche (2015). Multi-period portfolio optimization with alpha decay. Axioma Research Report 55; International Journal of Financial Engineering and Risk Management 2(4), 2018, 283-307. https://optimization-online.org/2015/02/4785/
- Kelly, J. L. (1956). A new interpretation of information rate. Bell System Technical Journal 35(4).
- Lehalle, C.-A., and E. Neuman (2019). Incorporating signals into optimal trading. Finance and Stochastics 23(2), 275-311. https://arxiv.org/abs/1704.00847
- Liu, H. (2004). Optimal consumption and investment with transaction costs and multiple risky assets. Journal of Finance 59(1), 289-338.
- Ma, C., and P. Smith (2025). On the effect of alpha decay and transaction costs on the multi-period optimal trading strategy. arXiv 2502.04284.
- MacLean, L. C., E. O. Thorp and W. T. Ziemba (2010). Long-term capital growth: the good and bad properties of the Kelly and fractional Kelly capital growth criteria. Quantitative Finance 10(7).
- Martin, R. J. (2014). Optimal multifactor trading under proportional transaction costs. Risk. https://arxiv.org/abs/1204.6488
- Muhle-Karbe, J., M. Reppen and H. M. Soner (2017). A primer on portfolio choice with small transaction costs. Annual Review of Financial Economics 9, 301-331. https://arxiv.org/abs/1612.01302
- Nystrup, P., S. Boyd, E. Lindstrom and H. Madsen (2019). Multi-period portfolio selection with drawdown control. Annals of Operations Research 282, 245-271.
- Passerini, F., and S. E. Vazquez (2015). Optimal trading with alpha predictors. arXiv 1501.03756.
- Rogers, L. C. G. (2004). Why is the effect of proportional transaction costs O(delta^(2/3))? Contemporary Mathematics 351, 303-308.
- Shreve, S. E., and H. M. Soner (1994). Optimal investment and consumption with transaction costs. Annals of Applied Probability 4(3), 609-692.
- Taksar, M., M. J. Klass and D. Assaf (1988). A diffusion model for optimal portfolio selection in the presence of brokerage fees. Mathematics of Operations Research 13(2), 277-294.
- In-repo: `docs/plans/2026-09-intraday-equities-mio.md`; ADR-0088, 0111, 0120, 0184, 0185, 0186; `docs/explanations/mio-optimizer-formulation.tex`; `docs/research/max-confident-h-one-score-cs-ic-decay-hac-se.md`; `docs/explanations/multi-horizon-model-selection.md`.
