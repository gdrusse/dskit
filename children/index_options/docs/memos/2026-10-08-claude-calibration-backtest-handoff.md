## TL;DR

Claude: finish from the verified forecasting report, then walk the owner through the remaining decisions needed to calibrate the uncertainty set and run a realistic historical decision backtest. This memo is a next-session plan, not evidence that calibration, MIO execution or a trading backtest has occurred. Existing historical data has already informed development; a production-style replay cannot be presented as independent production qualification.

## Authority and first pickup

The owner requested this handoff on 2026-10-08 and intends the next session to address calibration and the path to an MIO backtest. Current authorization is to finish the forecasting cycle, preserve inference models, write this plan, and merge/push the reviewed report. The earlier prohibition on MIO implementation/execution and trading backtests remains in force for this cycle. Begin the next session with a concrete walkthrough of the decisions below and establish the intended next-stage scope before execution. Do not reopen authorization for already approved forecasting/report work.

Use WSL2 only. Fetch `https://github.com/gdrusse/dskit.git`, inspect current remote state and work in a separate checkout. Read root/child AGENTS, current RE-ENTRY and workflow/skeptic/wrap instructions. Preserve the pinned forecasting checkout `/home/russell/dskit-production-universe-rerun-20261008`, the old85 checkout and all artifacts. No protected2026 access, including scoring or feature lookbacks; no new provider acquisition, cloud spending, shared-environment upgrades, full suite, deployment or live trading without an explicit later change in scope.

Read these in order:

1. [Restored-universe execution memo](2026-10-08-restored-universe-patchtst.md), especially its final results, limitations and recovery instructions. It is currently being completed; its final section and actual receipts supersede this memo's preparation-time status.
2. [Model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) and linked complete ticker/training appendices. Refuse to treat its current partial status as a final release.
3. [Production proposal](../research/advanced-cdf-zoo/2026-10-07-production-proposal.md), ADR0254, and [reviewed robustification formulation](../research/distribution-modeling/simple-formulation-with-robustification.md). Its older model performance claims concern a different study and must not be attributed to the new PatchTST.
4. [Worked notation](../explanations/robust-condor-selection.md). Here lowercase q means alternative probability masses; uppercase Q means their cumulative probabilities.

Private audit: `/home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/`. Read execution-lock, execution-plan, fit-attempt ledger, actual stage results, recovery and review receipts. Verify PID plus process start identity before any dispatch; never launch a duplicate. During preparation, evaluation is running under `evaluation-batch-launch.json`; 39 unique new fits are complete and27 reserved, for a maximum133 cumulative charges out of216. This is not permission to spend the remaining budget on a different experiment.

## Model and evidence contract

The selected model is pooled PatchTST32, all42 base features, learning rate0.0003, fusion dropout0.1. HPO retained the default. Seed11 is nominal; seeds29/47 are stability checks. Every cutoff uses a fresh expanding-history fit, with training-only transformations and purged monitoring. Preserve this model choice unless a separately bounded, reviewed next-stage design explicitly changes it.

At preparation, development weighted skill is +0.878415%; all three confirmation seeds pass their prespecified aggregate gate. The original85 nominal confirmation cohort is slightly negative (-0.043181%); the restored cohort is +1.688151%. Nominal95% interval coverage is92.805%. These are developmental results, not proof of conditional calibration or profitable decisions. Read actual evaluation results when available before making any recommendation.

392names include307 histories restored under explicitly unverified action-inventory assumptions. CORZ is excluded; configured event windows apply to WULF/ONDS. The retrospective universe screen, source vintages and assumed availability clocks are material limitations. A nominal count of392 does not imply every checkpoint has392 supported heads.

The inventory gives immutable onboarded `fit_root`, checkpoint/fit and constituent file hashes, private backup paths, training bands and head/feature counts. A package requires `metadata.json`, `complete.json`, `model/state.json` and `model/weights.pt`. The existing `TorchCDF.load_checkpoint(<fit>/model)` seam loads fitted transformations. Reproduce the archived recovery recipe: order features, apply saved training medians, add the exact saved ticker-head encoding, then call the loaded model so its scaler/mask apply once. Unsupported heads must refuse.

Keep all chronological refits. Seed11/fold9 will be the fixed latest evaluation-cutoff reference once completed and recovered, not a checkpoint selected because it scores best. NEVER apply that later checkpoint to earlier historical decision dates. Saved models permit inference, not full interrupted-optimizer continuation. Private model weights and per-forecast records must not be pushed to GitHub.

## Decisions to settle in the walkthrough

- **Instrument and decision scope:** the reviewed formulation is a single underlying, one-lot intact iron condor held to settlement. Confirm whether that remains the target. Equity options, American exercise/physical delivery, portfolio sizing, early exits and multi-asset dependence are not automatically covered by that index-style formulation. The forecast outputs are marginal distributions, not a joint market distribution.
- **Evidence level and periods:** choose a clearly labeled retrospective research replay, with prior-only calibration and later chronological testing, or separately authorize a truly untouched prospective qualification stage. Neither choice opens2026. If current history is insufficient, report the exact missing interval or dataset; do not manufacture evidence.
- **Calibration objective:** define what robustness should protect, the acceptable empirical error/coverage and conservatism tradeoff, pooling groups, minimum sample rules, a bounded search, and frozen acceptance criteria before examining calibration results. Forecast interval coverage is not automatically the criterion for the robust-loss set.
- **Backtest contract:** freeze entry clock, eligible universe and option contracts, tenor/expiry matching, quote quality, execution assumptions, fees, position/capital limits, no-trade rules and treatment of missing data. Allocate a new explicit bounded compute/fit budget if any refits are needed; do not borrow unused attempts silently.

## Calibrate rho, then optional Q bands

The nominal model describes standardized log return z. Convert to terminal price with S_T = S_t exp(a_t z), using that forecast's entry spot S_t and reference scale a_t. Probabilities are unitless; grid and spot share quote-currency units.

For grid masses p and alternative masses q, require nonnegative masses summing to one. Let P and Q be their cumulative sums. Rho is the spot-normalized Wasserstein budget: sum of grid gap times absolute cumulative-probability difference, divided by spot. It is dimensionless. Optional Q^lo and Q^hi constrain the SAME cumulative Q at chosen grid points; they are not independent strike probabilities and are not derived automatically from rho.

Follow the reviewed formulation's staged approach:

1. Freeze grid/support, numerical integration tolerance and calibration groups from information available at entry. Include all candidate payoff kinks. For bounded condor expiry losses, clipping beyond all relevant strikes can preserve those payoffs; do not extend this argument to uncapped positions or discard the retained full forecast tails.
2. Use settled chronological forecasts whose underlying model and transformations existed before their forecast dates. At each calibration update, include only labels settled and published before that update's decision time. Purge overlapping labels across selection/calibration/test boundaries. Early-stopping observations are not independent calibration observations.
3. Start with the Wasserstein radius alone. Select its bounded rule against a prespecified calibration criterion, then freeze it before the test block. If adapting over time, freeze the update algorithm, schedule and eligible prior-label window; future outcomes may never alter an earlier rho. Log each calibration identity and effective time.
4. Add local CDF bands only if separately supported. Estimate grouped predicted-probability versus realized-event discrepancies using entry-known groupings and predeclared shrinkage/fallbacks. Preserve the nominal CDF inside the bands and verify the joint set is nonempty and coherent. If claiming simultaneous band coverage across prices/groups, account for that multiplicity.
5. One realized settlement does not reveal its date's true conditional CDF. Bernoulli residual quantiles and aggregate PIT plots do not create conditional-CDF confidence bands. Treat a historically estimated set as empirical stress limits unless stronger assumptions and evidence actually justify a stronger claim.
6. Keep all same-date tickers together when resampling date blocks. The existing forecast-qualification proposal uses60 trading-date primary blocks and30-date sensitivity, with calendar span covering the maximum31-calendar-day target. That shorter sensitivity is not the longer-block calibration check required by the reviewed formulation: separately predeclare a calibration sensitivity length longer than its primary block before inspecting results. Check adequacy and effective block count at every chosen length. Audit robust expected-loss bounds against average realized losses on held-out blocks, not against every individual payoff. Report sparse groups, inconclusive estimates and abstentions.

Existing2021–2025 periods were already inspected. Properly nested or prior-only chronological recalibration can support a retrospective research assessment, but cannot erase that reuse. Independent qualification needs untouched evidence after model/rule selection. The current proposed collection targets are126 calibration sessions and252 later validation sessions, with labels/publication lags matured before use; they are not an established sample-size guarantee. Exact dates, coverage target and admissible data remain decisions, with2026 closed.

## Reconstruct what a production process would have known

First inventory existing DS Kit onboarding, forecast/checkpoint, calibration, optimizer/library and backtest seams. Read package READMEs and configurable parameter tuples; an empty registry is not evidence of a missing capability. Search the repository and existing studies before proposing new code. Prefer JSON and existing wrappers; use an ADR and focused tests only for a demonstrated behavioral gap.

Reconcile the actual owned option data with the chosen forecast instruments and expiries. Do not assume392 stocks have eligible options history. The reviewed chain archive has date-only snapshots, so same-day executable fills and historical known_at are not established. A conservative assumed research clock must be labeled as such; changing the clock does not manufacture missing intraday quotes. Quote/settlement timestamps, stock actions, contract multipliers, dividends, exercise/assignment treatment and exact forecast horizon must match the intended instrument. Do not interpolate or extrapolate the31-day model to arbitrary maturities without a separately validated rule.

Build an event timeline that orders feature availability, model availability, calibration update, quote snapshot, decision, fill and settlement. Recover model training cutoffs from saved band dates; do not infer them from file creation time. Existing saved out-of-fold forecasts can avoid extra fits on matching dates, but must pass identity and availability checks. If a proposed retraining cadence differs from the saved cutoffs, document the gap and bound any additional work before dispatch.

Realistic replay must model bid/ask and costs, stale/missing/invalid quotes, action and settlement events, overlapping positions and capital use, plus deterministic no-trade/failure behavior. Choose supported exercise/exit mechanics; do not pass an expiry-only European payoff check off as full American-option or early-exit realism. If the desired backtest requires unavailable evidence, narrow its claim or leave that component blocked rather than inventing data.

## Decision layer and backtest gates

Once next-stage execution is in scope, freeze one shared uncertainty set per decision context before evaluating any candidate. Preserve no trade as a feasible choice and break ties toward it. Worst-case loss, probability-of-loss or CVaR constraints must use the same uncertainty set when those constraints are part of the approved model.

For the reviewed one-lot problem, enumerate feasible candidates and solve each existing-wrapper LP first; use this as a transparent reference. An MIO implementation is needed only where the approved combinatorial scope warrants it. If introduced, compare it with enumeration on small fixtures. Check mass sums, monotone CDFs, band feasibility, rho=0 recovery of the nominal result, units, capped payoff integration, mesh/support sensitivity, solver residuals and no-trade behavior. The current nine synthetic uncertainty checks establish format arithmetic only.

Run a frozen, paired research comparison of nominal, radius-only, band-only and joint-set variants only if those variants and budget are approved. Use identical eligible snapshots, candidates, costs and timing; preserve every no-trade/refusal. Keep calibration criteria and test acceptance separate. Report forecast/calibration diagnostics alongside decision outcomes, drawdowns, exposure/capital usage, turnover/costs, tail losses and sample sizes as applicable to the frozen scope. Model-score gains do not imply strategy gains. Do not choose a variant or threshold retrospectively from test profits.

## Reproducibility, completion and handoff

Before declaring the next stage complete, retain source snapshots, resolved configs, dependencies, model/calibration/decision identities, all failures and chronological event/forecast/decision evidence privately. Verify second-copy recovery with original roots denied. The existing backups share the physical disk and are not off-device disaster recovery.

Publish an aggregate memo that separates proposed, implemented, tested and empirically validated work. Include the exact inference package and calibration version used at each cutoff, explicit assumptions and missing data, all prespecified variants/results, and the strongest claim the evidence supports. Obtain required independent reviews with zero unresolved Critical/Major, then merge/push owner-authorized completed work and verify remote contents. Keep clean reviews for unchanged code; future calibration/MIO behavior needs its own scoped reviews.

Current state: no numerical rho, validated Q bands, MIO run or strategy backtest exists from this forecasting cycle. The immediate next-session action is to read its completed evaluation/recovery report, inspect this handoff with the owner, and freeze a bounded calibration-and-backtest contract using the available evidence. Do not start by building new infrastructure or rerunning the zoo.
