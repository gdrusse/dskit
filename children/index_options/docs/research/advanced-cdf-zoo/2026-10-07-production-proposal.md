# Production CDF candidate: synthesis and proposed validation plan

## Question

What should power the next production-candidate run, which features should it use, and how should validation and hyperparameter optimization (HPO) proceed after the completed advanced architecture study?

## Finding

**Recommend a pooled, ticker-head PatchTST with the 42 base inputs as the lead production candidate. Keep the empirical distribution as the operational reference/fallback; qualify the learned model before promotion. Retain pooled CNN as the simpler challenger and pooled VanillaTransformer as the strongest advanced challenger. Exclude options from the initial default.**

The original synthesis was a research proposal. The owner amendment below now authorizes bounded pre-holdout implementation/execution after its gates; it does not permit inspecting the protected 2026 holdout. No new model training, HPO, providers, deployment or trading was performed for this synthesis. Publication itself is not execution evidence or production qualification. The prior reports and closeout memo were verified on remote main at `f98af937a6855e0145f3a4d46342bdc2402b126f`.

### What the evidence supports

The completed study verified 48,073 successful fits and 21,467 explicit skipped fits; all 318,330 expanded ticker/model/window/arm outcomes were accounted for. All-period skill here means **100 × (1 − summed model loss / summed paired empirical-baseline loss)**. It is not an average of window percentages, equal-ticker skill, or profit.

On the full pooled-base panel, all 12 executable architectures have the same 103,901 forecasts across 393 tickers and nine exploratory windows (2024-02-06 through 2025-12-29). Weighted CRPS skill relative to the empirical reference was:

- PatchTST +0.458%; VanillaTransformer +0.424%; FEDformer +0.378%; TFT +0.330%.
- TSMixer +0.297%; KAN +0.289%; DilatedRNN +0.278%; TiDE +0.250%.
- TCN +0.219%; Autoformer +0.192%; NBEATS +0.160%; PatchTSMixer +0.105%.

PatchTST also improved ordinary CRPS by 0.487% and tail CRPS by 0.406%. It improved weighted CRPS in eight of nine windows and for 204 of 393 tickers. Its leave-one-window-out aggregate skill stayed positive, ranging from +0.327% to +0.561%. These are descriptive robustness checks, not independent tests or confidence intervals.

On exactly matched forecasts, the earlier pooled CNN has +0.252% weighted skill against the same baseline. PatchTST reduces CNN's weighted loss by approximately 0.206% and wins for 207 of 393 tickers. The advantage is small. The old CNN's batch size, stopping protocol and learning-rate selection differed, so this is not a controlled estimate of architecture alone. The older memo's approximately +0.36% CNN headline used a different aggregation; do not splice that number into this ranking.

Coverage changes the ranking. Across the smaller panel shared by all 12 architectures AND both pooling regimes (92,231 forecasts, 373 tickers, 3,082 ticker/windows), pooled VanillaTransformer leads at +0.171%, versus PatchTST +0.143%. PatchTST's advantage on the full panel therefore is not a universal architecture ordering. Both deserve confirmation, and CNN deserves a fair same-protocol rerun.

The four latest windows give PatchTST +0.548% and VanillaTransformer +0.589%; the four earliest give +0.295% and +0.187%, respectively. The middle window is excluded from this particular early/late comparison because it straddles the calendar years. This is a sensitivity description, not a late-period selection rule.

### Pooling is the clearer choice

Here, pooled means a shared encoder and context representation trained across tickers, followed by a small ticker-specific distribution head. It does not mean one identical forecast for every ticker, attention between contemporaneous stocks, or a joint portfolio distribution. Unpooled means a separate fitted network for each ticker.

On the common panel, all 12 pooled architectures beat their unpooled counterparts. The unpooled weighted skills range from approximately −1.71% to −3.13%, while pooled results cluster near zero with several positive. Every advanced local architecture also loses to the earlier local LSTM on its own verified matching intersection. Those intersections differ and cannot establish a single cross-model local ranking.

My interpretation is that shared statistical strength is more useful than giving each ticker a separate complex network. Typical local fits have roughly 1,063–1,302 rows but only about 84–103 disjoint target intervals; overlapping 31-day outcomes do not create that many independent examples. Hundreds of thousands of pooled rows are also correlated. Architecture widths, capacity admission and batches differ across regimes, so the study does not isolate a pure pooling effect. Nevertheless, it gives no practical reason to fund another full local-model zoo now.

Global/local forecasting research supports parameter sharing as a legitimate modeling approach; it does not establish that this market application has reliable alpha. [Montero-Manso and Hyndman](https://arxiv.org/abs/2008.00444).

### What these architectures actually saw

The networks consume a **22-step, one-channel return history plus 20 current context fields**, not 42 historical channels. The return sequence is ordered oldest first. Native encoders were initialized from scratch; these are not pretrained financial foundation models.

PatchTST turns short pieces of the history into tokens and applies attention. The tested leading configuration uses width 64, one layer, patch length 4, stride 3, one attention head, feed-forward width 128, layer normalization and 0.1 dropout. DS Kit adds a small fusion layer and per-ticker three-Gaussian-mixture output heads. Its approximately 65,171–66,305 trainable parameters include the changing admitted head population. Patching is a plausible way to extract short local patterns while sharing parameters, not proof that attention caused the measured gain. [Hugging Face's architecture description](https://huggingface.co/docs/transformers/model_doc/patchtst).

VanillaTransformer applies ordinary attention to the return history; it is competitive enough to retain. CNN/TCN use local convolutional patterns; CNN is particularly valuable as a compact reference. DilatedRNN uses recurrence at different temporal spacings. PatchTSMixer and TSMixer use MLP mixing; with only one historical channel, this experiment does not exercise a rich multivariate-channel advantage.

Autoformer and FEDformer emphasize decomposition/autocorrelation or frequency structure. TFT is designed for richer variable selection and covariate handling, but our wrapper fuses the current context in DS Kit, outside the native sequence encoder. TiDE uses a dense encoder/decoder, NBEATS residual basis blocks, and KAN learned univariate transformations. Their results describe these particular wrappers, short histories and budgets—not every capability of the original models.

The NeuralForecast adapters use the native forward output as eight latent coordinates feeding the distribution head; the internal `h=8` is not eight supervised forecast horizons. DS Kit owns fitting, splits and probabilistic scoring. Informer and TimesNet are explicitly excluded for failed deterministic row independence; BiTCN is excluded because its defining forward branch is absent without future covariates. Keep these three visible as incompatible, not poor-scoring trained models.

### Feature recommendation

Start from the existing 42-field base set, with the same training-only scaling, missing-data policy, constant masks and target normalization. This is the best supported starting bundle, not proof that every field adds value:

- 22 daily return lags.
- Momentum over 5 and 22 sessions.
- Realized volatility over 1, 5, 22 and 66 sessions, plus separate upside/downside volatility over 5 and 22 sessions.
- Log volume, relative volume, dollar volume and Amihud liquidity proxy.
- Calendar DTE, sessions to expiry, their logs, calendar-days-per-session and reference scale.

The present evidence covers the configured approximately 31-calendar-day target only. Several calendar inputs can be constant within a fold and are correctly masked. It does not validate deployment across arbitrary expiries.

Use one small group-ablation experiment before HPO: the full bundle, minus momentum, minus the four liquidity/volume fields, minus directional volatility, and minus long-window volatility (rv22 and rv66). Keep return lags and the horizon/reference inputs fixed. Use the same lead encoder configuration and same admissible heads, not capacity-expanded replacements after a feature removal. Record actual parameter differences; feature removal changes input dimensions even at fixed widths. Compare all five on identical forecasts. Prefer the full bundle unless a removal reduces the pooled summed development loss; exact ties favor fewer optional fields, then the fixed listed order. This is a development choice that subsequent confirmation must challenge.

Do not add options to the initial production bundle. Only the last two folds have adequate genuine options-era history; at most 300 tickers and 17,037 forecasts qualify per architecture before capacity gates. The seven fields are trade proxies (call/put activity, put share, volume, trade count, strike count, moneyness and straddle ratio), each with a missing flag. They are not a full implied-volatility surface.

Only pooled PatchTSMixer (+0.492%) and TCN (+0.045%) improve over their matched-history base control among the seven architectures with paired options results. PatchTST does not. Local comparisons have additional capacity changes: 1,366 of 3,560 pairs have different head widths and 477 different native configurations, potentially overlapping. Five otherwise eligible families lack paired results. This is insufficient evidence for a general options benefit or for declaring options useless.

A later, separate options study should fix the architecture and widths, compare base/options on exactly the same eligible history, preserve observation/missingness indicators, and validate on new post-selection dates. Do not replace unavailable historical option inputs with constants and call that options training.

### Universe and warmup selection

Yes, ticker selection can use only warmup information, provided the rule is frozen before later evaluation. Model-fit loss is not an appropriate ranking signal: use purged out-of-fold warmup forecasts, and include the selector itself in later evaluation. Repeatedly trying ticker screens also overfits the warmup.

For the initial candidate, I recommend **no performance-based ticker selection**. Use availability/admission and operational liquidity criteria fixed before the evaluation period, and keep all eligible heads. Report outcomes for excluded and fallback tickers; never quietly remove weak tickers from the denominator. The existing 393-name research panel is not proof of a historically point-in-time universe: the larger universe was assembled later, including option-activity ranking. A clean future test must document membership as known at its decision time.

The existing warmup is one fold, 2023-08-29 through 2023-11-10. It is too narrow for extensive architecture × feature × ticker screening and has no genuine options training history. New pre-2024 development folds are proposed below; they do not already exist in the reviewed manifest. Also, selecting this proposal after examining 2024–2025 means those years remain exploratory even if later HPO uses earlier data. [Cawley and Talbot on selection overfitting](https://www.jmlr.org/beta/papers/v11/cawley10a.html).

### Owner amendment — longer history, COVID, and split correctness (2026-10-07)

**Current authority and precedence.** The owner requested these changes either now or as an actionable handoff and offered an end-to-end run. This session records and reviews the handoff; it does not start training. The next session may implement the revised Stage-0 contract and execute Stages 1–4 within the existing 216-attempt cap once the source, feasibility, ADR and independent skeptic gates pass. Do not ask again for this bounded pre-holdout scope. This amendment supersedes the original 2020 H2 window and publication-only execution status below; all original review outputs remain historical evidence. Protected 2026 data, providers/new acquisition, cloud spending, environment upgrades, deployment and live trading remain excluded. A material expansion beyond this contract still needs a decision.

**H1 — use all usable owned history, not just the old cached panel.** Inventory existing local source catalogs, snapshots, prepared panels and their generating configurations before deciding the start date. For each ticker in the existing intended universe, report the earliest/latest eligible quote dates, earliest usable feature date, source versions and hashes, number of candidate rows, admitted rows and excluded rows by reason. Investigate start-date filters, truncation, sampling/stride, missing-feature cutoffs, IPO dates, identifier changes and duplicate/overlapping sources. Reconcile each source's eligible inventory to the new panel; every omission needs an explicit data-quality, identity, feature-availability, target-maturity, sampling-contract or split/purge reason. Do not claim all owned history was used from a panel min/max date alone.

Use expanding training history from the earliest reliable observation available for that ticker at each fold cutoff; remove arbitrary 2016 truncation if reliable earlier records already exist. Some tickers legitimately begin later. Feature lookback and missingness gates still apply, and the 22-session input sequence stays unchanged. Do not invent older options observations, backfill pre-IPO rows, count duplicate records twice, expand the universe automatically, or substitute today's surviving members for a point-in-time universe. A future test must use membership known at its decision time.

Unify source identity and price/volume units before combining history. Register/acquire existing local cross-run inputs through the existing onboarding seam; this permits cataloging owned files, not contacting providers. Use metadata for inventory and existing protected readers for row inspection: never open an unrestricted mixed-year file to inspect protected holdout rows. Eligible pre-holdout slices must respect the existing quote-date and settlement-date locks. Frozen original inputs/artifacts remain intact; changed history creates new immutable source/panel/fold/checkpoint identities and invalidates reuse across changed input identities.

All available history means all *eligible* historical examples under the reviewed sampling contract. Stage 0 must disclose whether the old panel sampled dates and how the new sampling rule affects coverage. Freeze that rule before comparisons; use the same admitted forecast identities and row weights for every candidate. No automatic history-window or recency-weight HPO is added. Equal treatment of eligible rows remains the first-pass training policy; any later weighting/window comparison requires a separately bounded design.

**C1 — explicit COVID shock development fold.** Replace the 2020 H2 development window with quote dates from **2020-02-01 through 2020-06-30 inclusive**, mapped to actual available market sessions. The other two development windows remain 2021 H2 and 2022 H2. No extra fold or fit is added: 15 feature fits, 108 HPO cells, 27 confirmation fits and 54 exploratory replay fits still total 204, with the unchanged 216-attempt first-pass cap.

For the COVID fold, freeze training, transformations and the early-stopping monitor before the first score origin. Every fit/monitor target must already be settled and available by that checkpoint cutoff. No parameter updates from the February–June outcomes occur during that fold. Contemporaneously available return/volatility/liquidity features may evolve during scoring; this is not a frozen-feature simulation.

Report the aggregate February–June score and two prespecified quote-date slices, February–March and April–June. These labels describe quote-date cohorts; a 31-day forecast can settle in the following cohort. Print actual quote and settlement ranges, counts, coverage, weighted CRPS, ordinary CRPS, tail CRPS, PIT and interval coverage for each. The slices reuse the same forecasts and add no fits. Do not select a winner from the better slice, optimize a special COVID weight, or call this known historical event an untouched test. Later folds retain COVID observations in their expanding training history. Do not add a hindsight COVID indicator.

**S1 — corporate actions must preserve economic meaning.** Inventory and reuse `StockDailyBars`, `PriceCalendarCDFPanel`, `DailyBarFeatures`, `AsTradedClose` and the existing corporate-action rules/tests before adding code. These seams demonstrate support, not end-to-end correctness for every dataset. Stage 0 must verify the actual source-to-feature-to-target path.

Record each source's raw versus split-adjusted price basis, dividend/total-return treatment, volume basis, adjustment vintage, action ratio/direction/effective date, stable security identity and known-at metadata. Reconcile overlaps without silent price-basis changes. Missing, zero, negative, conflicting or unexplained adjustment factors must refuse or quarantine affected spans with counts; absence of an event and missing event coverage must not be treated as the same fact. Do not infer correctness from a column called `close` or `adjusted_close`.

Return lags, volatility, reference scales and terminal targets must share a consistent split-neutral economic basis across their whole windows. A pure 2-for-1 or reverse split must not create a loss/gain by itself. Preserve the current price-return target definition: do not silently replace it with dividend-reinvested total return. Price-level, share-volume, relative-volume, dollar-volume, liquidity and moneyness features require explicit compatible units and as-of treatment. A valid split is not by itself a reason to discard an entire ticker.

Back-adjusted vendor history and point-in-time features are distinct. Later vendor rescaling may be undone using its matching action ledger to recover the historical share basis; that does not make future action information an allowed predictor. Verify with **internally consistent snapshot vintages** that an action effective after a historical forecast's settlement cannot change that earlier forecast's eligible normalized features, target, membership or liquidity screen merely by rebasing stored prices/volume. Rebase both prices/volume and their corresponding ledger in this test; appending an action ratio to unchanged adjusted prices is not a valid equivalence test. If the required vintage/action evidence is absent, record the limitation and refuse the affected release claim.

Features may only use events/data available at the forecast origin. Target construction may incorporate corporate actions that actually occur within the subsequent outcome interval, only as outcome accounting; they must never enter that forecast's features. Keep raw/as-traded units distinct for later strike comparison. Options crossing an action need the applicable contract identifier, strike, multiplier and deliverable terms; do not assume every action merely divides the strike. Unverified nonstandard contracts/spinoffs/mergers are explicitly excluded from option-based evaluation or abstained on. They do not justify fabricating mappings, a provider fetch, or substituting an unrelated ticker head. Base-only research does not require building an options execution engine.

**Required implementation evidence.** Before real fits, produce:
- A source-to-panel coverage ledger and reconciled exclusion counts, including any newly recovered older history.
- A versioned three-fold development manifest with COVID replacement and proof of fit/monitor/score label availability and purge boundaries.
- Focused forward-split, reverse-split, repeated-split, no-split, invalid/missing-factor, inconsistent-source and post-origin-action tests spanning ingestion, features, labels and applicable contract mapping. Include actual pre-2026 split samples already owned, if available, alongside synthetic accounting cases. Test targets crossing the event and feature windows preceding/following it.
- The consistent-vintage invariance check, unchanged no-event outputs, and explicit refusal/coverage accounting for unsupported actions.
- Proof that all feature variants and model trials receive paired forecast identities and fixed row weights; cache/checkpoint resume must reject changed data/action/feature identities.
- Independent design review before implementation, focused tests after implementation, and two fresh final correctness/integration lenses with zero unresolved Critical/Major before training. Source/corporate-action evidence failures stop affected execution; preserve unrelated verified results and do not call integrity failures ordinary model skips.

**Next-session execution order.** Read this amendment and the current RE-ENTRY first; fetch origin and work in an isolated WSL2 checkout. Audit owned history and corporate-action semantics, implement only demonstrated generic gaps with the required ADR/review workflow, generate and freeze the revised data/fold contract, then run representative counted pilots followed by the bounded Stages 1–4. Deliver trial and coverage ledgers, filterable reports, a frozen champion/reserve decision, uncertainty and calibration diagnostics, and a memo of remaining qualification gates. Wrap/push complete skeptic-reviewed work and verify remote artifacts. Stop before Stage 5 protected-data access; prepare its concrete protocol for a separate decision. Do not reinterpret "end to end" as permission to deploy or trade.

References for adjustment semantics: [OCC/OIC corporate-action adjustments](https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies) and the inspected local stock-bar, feature, panel and corporate-action test seams. Historical-run findings elsewhere in this document remain unchanged.

### Proposed validation and HPO schedule

This schedule is the bounded next-session research budget authorized by the owner amendment, conditional on its prerequisites and review gates; it is not a running job. All dates below are proposed quote-date windows. Before execution, generate and independently review a new versioned fold manifest with exact market-session timestamps, feature known-at cutoffs and target settlement times. Do not edit or repin the historical study config to run on main: its frozen source identity intentionally differs from current main.

**Stage 0 — prerequisites, no training.** Complete H1, C1 and S1 and the implementation-evidence gates in the owner amendment above. Verify the point-in-time data/universe contract, onboard cross-run inputs through the existing source catalog, define the actual forecast publication clock, and inventory the fit/search/report seams. Select only rows whose entire target is known by each fit or monitor cutoff. Purge across fit→monitor and monitor→score boundaries by actual label availability, not a guessed fixed number of rows. Retain a 40-quote-date monitoring band where feasible and the existing training-only transforms. Refuse insufficient-history folds rather than silently substituting later dates.

**Stage 1 — features, at most 15 fits.** Five bundles × three pooled PatchTST development folds, seed 11. Development score windows under the owner amendment: 2020-02-01 through 2020-06-30, 2021 H2 and 2022 H2. Use expanding earlier history, preserving purges and monitor bands. Availability must be audited before accepting these windows. Their existence and capacity sufficiency are not asserted here. Select the feature bundle with the rule above; use that same bundle for all three architectures.

**Stage 2 — HPO, 108 fit cells.** Three architectures × 12 configurations × the same three development folds. Use a transparent small grid: learning rate {0.0001, 0.0003, 0.001} × compact/reference encoder width × dropout {0.0, 0.1}. Proposed compact/reference sizes are PatchTST d_model 32/64, VanillaTransformer hidden_size 32/64, and CNN channels 8/16, subject to the native wrapper's supported parameter names and capacity checks in Stage 0. Keep each family's other architectural settings fixed at its documented reference; enforce the existing parameter budgets and print realized counts. Do not silently resize an over-budget trial into another candidate.

Keep AdamW, weight decay 0.01, clip 5, pooled batch 512, seed 11, patience 20 and ceiling 500 fixed for this first HPO cycle. CNN must adopt the same training/scoring contract as the advanced candidates; its earlier batch-4096/patience-6 scores remain historical comparisons. Reject non-finite objectives. A training ceiling or numerical failure is a declared failed trial, not a missing fold dropped from its average. A complete trial requires all three prespecified folds; no best-fold selection.

Minimize total weighted CRPS divided by the fixed paired baseline total across the three folds. Use the full common admissible forecast panel and the same per-row weights for every trial. Do not average fold skill percentages. Break exact score ties by fewer parameters, then fixed configuration order. Retain all trials, including failures. No tuning of the scoring weights or target normalization.

The generic `hpo-grid` driver and `OptunaSearch` pack already exist. The latter reports only one scalar per trial and has no intermediate-value pruning; do not promise ASHA/Hyperband through that interface. The advanced experiment CLI is a fixed-protocol executor, not a completed multi-fold HPO integration. Before execution, prove the existing search/fold interfaces can implement this objective and checkpoint reuse; any missing generic mechanism needs its own reviewed implementation. Prefer the grid for 12 candidates. Count any driver winner reapplication as real additional work unless verified reuse prevents a fit.

**Stage 3 — chronological confirmation, 27 fits.** Freeze the best configuration from each of the three families, then evaluate each on three later proposed 2023 windows (January–April, May–August, September–December) with seeds 11, 29 and 47: 3 × 3 × 3. No tuning within those windows. Rank configurations by summed paired loss averaged over all three seeds; use the same parameter/order tie break. Do not pick the best seed. Seeds 29 and 47 are proposed in advance, not selected results.

Choose one champion and one reserve before the next stage. Production-candidate inference uses seed 11; the other seeds are stability diagnostics, not an untested ensemble. If the seed-11 candidate loses to the empirical reference in aggregate, or seed variation makes the selected family unreliable, stop and retain the baseline rather than claiming qualification. Returning to development is allowed only as a recorded new cycle; confirmation already inspected in that cycle is no longer independent.

**Stage 4 — existing-period stress replay, 54 fits maximum.** Champion and reserve × three seeds × all nine existing 2024–2025 windows, using the frozen protocol, to characterize seed sensitivity, paired loss, tail behavior, calibration, coverage and failure rates. These dates are already seen. Do not use this replay to select a different champion and then describe it as untouched confirmation. Material failure stops the proposal or creates a disclosed new development cycle.

Stages 1–4 total **204 fit cells** before reuse, failed attempts, pilots and search-driver winner reapplications. Budget up to nine additional winner reapplication cells (three families × three development folds), plus a three-cell representative pilot allowance: **216 fit attempts maximum for the first pass**, no automatic retry or expansion. Reuse only verified identical completed cells; the pilots count against the allowance. Integrity failures halt the affected run and are never ordinary trial skips.

Measured historical pooled PatchTST fit time was roughly 262 seconds median, 2,338 seconds for nine fits. Across architectures retained as advanced challengers, training times differ, and CNN under the new protocol has not been timed. For orientation, 216 fits at 4–10 minutes each would be about 14–36 fit-hours; this is a scenario calculation, not an ETA. Preprocessing, scoring, I/O and failed fits add time. Establish a real estimate from the pilots. Continue one pooled CUDA job at a time under the existing 11 GiB memory slice; do not assume the earlier two CPU lanes are useful for this pooled plan.

**Stage 5 — freeze and seek a separately authorized independent test.** Pin the one champion, feature order, seed, weights, preprocessing, universe/fallback policy, data/version identities, score endpoints and decision thresholds before opening any protected data. The 2026 holdout remains inaccessible under the current instruction. This proposal does not authorize its use.

For a later authorized test, use one prespecified initial fitted checkpoint (no refitting on holdout labels), a prespecified date interval containing only fully matured targets, the empirical reference, and the champion. Report primary paired weighted-CRPS improvement with a 95% confidence interval using synchronized quote-date blocks across the entire cross-section; 60 trading dates as the primary block length, 30 as sensitivity. This respects date dependence better than treating ticker rows as independent; the short available period can still make uncertainty large. Do not calculate such intervals from the nine aggregate window totals.

Proposed promotion gates are: lower 95% bound for weighted skill above zero versus baseline; upper 95% bound for relative tail-loss deterioration below 0.5%; complete, finite, correctly paired admitted forecasts; and no feature-clock, checkpoint-identity or calibration defect. The 0.5% tail margin is a proposed engineering tolerance requiring agreement before the test, not a threshold estimated from the current winners. Report PIT and 50/80/95/99% interval coverage, both overall and by predeclared liquidity/volatility cohorts. Calibrate those diagnostics and any blocking thresholds on development before the lock; do not invent cutoffs after seeing the test. If the intervals are wide or the criteria cannot be frozen, the result is inconclusive, not a pass.

The reserve is not a second chance to win the same holdout. Do not search seeds, tickers, feature bundles or architectures on that test. A failed champion requires a new decision and fresh later evidence. Rolling-origin evaluation is appropriate for development when every forecast uses only information available at its origin. [Hyndman and Athanasopoulos](https://otexts.com/fpp3/tscv.html).

### Proposed operation after qualification

Daily end-of-day inputs should produce forecasts only after their actual publication/processing cutoff; an end-of-day feature is not available for a trade earlier that day. Preserve the existing reference-scale definition and matured-label accounting. Record model identity, feature version, ticker head, forecast/settlement times and fallback reason with every issued distribution.

Use a monthly refit cadence with fixed hyperparameters as a starting operational proposal, not as a result validated by the current frozen-checkpoint holdout design. A later production release must separately validate the exact rolling-refit policy before enabling it. Never let evaluation labels alter the locked static test. Revisit HPO at most quarterly or after a documented regime/quality review; do not automatically search every time recent skill falls.

For an unseen/ineligible ticker, stale inputs or a failed model, use a separately qualified empirical fallback only when its own data requirements pass; otherwise abstain. Never use a different ticker's head. Track learned-model coverage, fallback coverage and abstentions explicitly so a narrower coverage set cannot inflate reported skill.

Before a live release, require source onboarding/availability checks, a supported serving artifact, deterministic batch/row behavior, latency/memory benchmarks, rollback, and a forward shadow period with matured forecasts. Distributional improvements alone do not establish an executable trading advantage: option prices, spreads, fees, risk controls and portfolio dependence require separate decision-layer validation. These models supply marginal terminal-return distributions, not joint portfolio tails.

## Sources

Local evidence takes precedence for what actually ran:

- [Final four-report memo and closure](../../memos/2026-10-07-advanced-architecture-results.md).
- [Pooled base](../../reports/advanced-cdf-zoo-20261006/pooled-base.html), [unpooled base](../../reports/advanced-cdf-zoo-20261006/unpooled-base.html), [pooled options](../../reports/advanced-cdf-zoo-20261006/pooled-base-options.html), [unpooled options](../../reports/advanced-cdf-zoo-20261006/unpooled-base-options.html).
- [Committed final verification](../../reports/advanced-cdf-zoo-20261006/final-verification.json), [architecture and capacity research](2026-10-06-capacity-and-architecture-plan.md), and [prior simple-model memo](../../memos/2026-10-05-pooled-zoo-417.md).
- Local audited aggregate source `final-data.json`, SHA256 `c75a30d8e98eea28ae881b72017d821e8cf75b8c7f48e9beb96dff4249775b27`; paired prior comparison `prior-comparison.json`, SHA256 `f068a65189fc51cb97eb60fd401adc2282733a5a326fbd317f6a7235c1332969`. Preserved under the frozen run's `final-verification/`; no raw records or weights are published here.
- Existing fold manifest SHA256 `944227da6f9a1c0fd015ef2e7e62c73ba9fde921fd559a6345e55c3dde45d492`: warmup fold 31 and scored folds 33–41. New pre-2024 folds above are proposals, not modifications to this manifest.
- Current source inventory: `dskit/pipeline/libs/optuna.py`, `kinds_search.py`, `libs/neuralforecast.py`, `libs/cdf_experiment*`, and the historical advanced study configuration at base commit `f98af937`.
- Primary background sources linked inline were consulted 2026-10-07. Their general claims do not substitute for local implementation or empirical evidence.

### Review scope

This document is a proposed design and synthesis. In scope: numerical accuracy/denominators, architecture and feature interpretation, selection leakage, chronological boundaries, search budget and existing capability claims, operational gates and authorization. Out of scope: executing these proposed stages, reading 2026 data, changing the frozen study, or approving a trading deployment. Independent review must challenge the design before it becomes an execution contract.


## Original publication review and disposition (historical)

The following records apply to the original proposal at ca9b2102, not to the later history/COVID/split amendment. Their original scope and verdicts are preserved; the amendment requires fresh independent review.

Two fresh, sequential independent GPT-6 skeptics reviewed immutable candidate `ca9b2102822b85ce4f0a312e0f51513229beb40f` against base `f98af937`: correctness/authority (`/root/proposal_correctness_r1`) and integration/feasibility/evidence (`/root/proposal_integration_r1`). Both returned zero Critical/Major and the same single Minor. **Publication gate closed; execution remains unapproved.**

Disposition C-R1-01 / INT-01: corrected TiDE's headline from +0.251% to +0.250%; audited value is 0.25046783655113813%. This is a display-only correction, with no change to ordering, recommendation, budget, validation rules or authority. No unresolved Minor/Nit remains. All other reviewed proposal content and the manifest-test blob are unchanged; this section appends evidence only. The reviewed proposal hash and actual reviewer outputs are retained below rather than rewriting their original verdicts.

The reviewers also confirmed a key prerequisite: leaving outcome files unopened does not make an earlier-2026 test independent if ticker membership was chosen from later-2026 information. Point-in-time reconstruction or an eligible later test interval after the appropriate freeze is required. No such reconstruction or new test was performed here.

<details>
<summary>Correctness/authority reviewer output (formatting normalized; wording retained)</summary>

Design correctness/authority review of candidate ca9b2102822b85ce4f0a312e0f51513229beb40f against base f98af937. Reviewed document SHA256: ff0fbcd80b33a1b874340fd49bcdfa9ec95bd5da5972c98103f4ad3827f521fc.

Verdict: zero unresolved Critical/Major; one Minor. This supports publishing the proposal, not executing it or accessing protected data.

Finding C-R1-01 — Minor, numerical rounding:
Location: children/index_options/docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md:20.
Invariant: printed three-decimal skills must correctly round the stated ratio of paired loss sums.
Reproduction: independently summing all pooled/base/TiDE aggregate rows gives 100 × (1 − Σmodel / Σbaseline) = 0.25046783655113813%. The document prints +0.251%; three-decimal rounding is +0.250%.
Consequence: a 0.001-percentage-point display discrepancy. Neither ranking nor model recommendation changes.
Family: numerical transcription/rounding.
Disposition: unresolved Minor; change to +0.250% or retain explicitly in the minor backlog. Other full-panel headline values round correctly.

Checks completed:
- Read root and child AGENTS, canonical implementation workflow, skeptic procedure and RE-ENTRY. Confirmed reviewed workflow/skeptic bytes match available origin/main versions. Worktree remained unchanged.
- Verified both audited aggregate hashes exactly match the proposal: final-data.json c75a30…75b27; prior-comparison.json f068a6…2969.
- Independently recomputed all 12 full pooled-base skills and their 103,901-forecast/393-ticker coverage; recomputed all 24 common-panel pooled/unpooled results.
- Confirmed full-panel PatchTST leadership and common-panel VanillaTransformer leadership are accurately distinguished.
- Recomputed PatchTST ordinary CRPS, tail CRPS, eight positive windows, 204 positive ticker aggregates, leave-one-window-out range, and both architectures’ early/late sensitivities.
- Checked prior CNN denominator, +0.252264538% baseline skill, PatchTST’s +0.205981080% relative improvement and 207 ticker wins. Checked all 12 advanced local comparisons against their paired prior LSTM results; every relative improvement is negative.
- Recomputed pooled/unpooled options/control intersections. Confirmed only pooled PatchTSMixer and TCN improve, at +0.492050239% and +0.045153263%; PatchTST is −0.717598632%. Confirmed 3,560 unique local fit pairs, 1,366 changed head widths and 477 changed native configurations. The proposal acknowledges incomplete coverage and capacity confounding.
- Verified PatchTST configuration, parameter range 65,171–66,305, nine-fit total 2,337.938 seconds and median 262.431 seconds against saved fit metadata. Checked feature inventory and sequence/context split against configuration.
- Checked arithmetic: 15 + 108 + 27 + 54 = 204 fit cells; nine reapplications plus three pilots give 216 maximum attempts; 216 × 4–10 minutes is 14.4–36 hours.
- Challenged chronological fit/monitor/score boundaries, failed/incomplete-trial handling, full paired objectives, feature selection, universe screening, best-seed selection, finalist selection, multiplicity, protected-data authority, static-test versus monthly-refit separation and fallback coverage. No additional proven defect found.

Universe/holdout adjudication:
An unopened 2026 outcomes file alone would not establish independence. Membership selected using later-2026 information cannot support an earlier-2026 independent evaluation without a genuinely point-in-time reconstruction. Lines 77 and 85 already require membership known at decision time and a verified point-in-time data/universe contract; using later membership would violate those prerequisites. Exact reconstruction, or choosing eligible later dates after an appropriate freeze, remains a Stage-0/5 prerequisite. Making that example explicit would improve clarity but is not a proven missing material protection.

Other prerequisite limits remain appropriately unresolved for a proposal: exact session/settlement manifests, historical availability and capacity of proposed pre-2024 folds, supported search integration, realized resource estimates, calibration thresholds, independent-test dates and separate rolling-refit qualification. The 2023 stage selects finalists; it does not replace the independent Stage-5 test. The one-champion test and prohibition on retrying the reserve preserve that separation.

No training, HPO, provider access, protected-2026 data access, code changes or full-suite execution occurred. Calculations used saved aggregate evidence; original row-level fitting/scaling, forecast identity and raw-data lineage were not independently reconstructed. Existing search interfaces were inspected, not executed. Future block-bootstrap coverage, native-wrapper grid feasibility and operational serving behavior remain untested.

Unresolved counts: Critical 0; Major 0; Minor 1; Nit 0.

</details>

<details>
<summary>Integration/feasibility/evidence reviewer output (formatting normalized; wording retained)</summary>

Independent integration/feasibility/evidence DESIGN review completed on candidate ca9b2102822b85ce4f0a312e0f51513229beb40f, base f98af937.

Verdict: zero unresolved Critical/Major. One Minor, independently confirmed and already reported by the correctness lens. No additional findings. This verdict covers publication of the proposal, not execution or production qualification.

Finding INT-01 — Minor, numerical presentation. Proposal line 20 reports TiDE weighted skill as +0.251%. Recalculation from the hash-verified aggregate rows gives 0.25046783655113813%, which rounds to +0.250% at three decimal places. Consequence: a small inaccurate headline; neither model ordering nor recommendation changes. Defect family: displayed rounding. Disposition: correct editorially; unresolved on this immutable candidate.

Assigned invariant coverage:
- Budget: independently recomputed Stage 1–4 counts as 15 + 108 + 27 + 54 = 204; nine winner reapplications plus three pilots yield 216 maximum attempts. Failed attempts, retries and reuse cannot silently expand that allowance. Timing is explicitly a scenario, with pilot-based estimation outstanding.
- Architecture/features/capacity: inspected historical configuration, HFSequenceEncoder, NFSequenceEncoder, TorchCDF, and experiment admission/selection paths. Confirmed 42 base inputs comprise 22 oldest-first, single-channel return steps and 20 current context fields. Native encoders initialize from configuration; NeuralForecast’s eight outputs are latent coordinates. Pooled PatchTST reference settings agree with the prose. Aggregate records confirm nine fits, 65,171–66,305 parameters, median 262.431 seconds and total 2,337.938 seconds. Actual capacity selection uses total and per-head budgets. Proposed fixed-width ablations and refusal to resize HPO candidates are appropriately distinguished from historical adaptive selection.
- HPO integration: inspected HpoGrid and OptunaSearch, including accepted parameters and scalar rerun seam. The no-intermediate-pruning claim is accurate. The proposal expressly defers multi-fold objective wiring, checkpoint reuse and any missing mechanism to reviewed implementation. Existing driver availability is not presented as completed advanced-experiment integration.
- Chronology: proposed windows require a new reviewed manifest, exact session/publication/feature/settlement clocks, matured labels and purges at both boundaries. Insufficient history must refuse. Stage 0’s point-in-time universe prerequisite prevents treating the later assembled universe as historically available. Exact historical reconstruction and final independent-test interval remain unresolved prerequisites, not claimed completed capabilities.
- Completed versus proposed: historical metrics and protocols are separated from proposed pre-2024 folds, CNN harmonization, additional seeds, pilots and qualification. Seen 2024–2025 results remain exploratory. Confirmation reuse after failure requires a disclosed new cycle.
- Identity and historical preservation: inspected the source-pin verification guard and diff inventory. No historical study configuration or executable source changed. The proposal forbids repinning historical evidence to current main and requires new versioned identities before execution.
- Static test versus operation: Stage 5 requires one initial checkpoint, frozen choices and no refitting on holdout labels. Monthly refits explicitly require separate validation before release. Fallback qualification, abstention, head identity and coverage accounting are retained.
- Evidence and authorization: all eight local evidence links resolve; both cited aggregate files match their recorded SHA256 values. Four external background links resolve and are relevant to their limited methodological claims: PatchTST (https://huggingface.co/docs/transformers/model_doc/patchtst), global/local forecasting (https://arxiv.org/abs/2008.00444), selection overfitting (https://www.jmlr.org/beta/papers/v11/cawley10a.html), and rolling-origin evaluation (https://otexts.com/fpp3/tscv.html). The journal appends A1088; the generated latest-ten display preserves ledger history. Proposal text expressly withholds training, provider, holdout and deployment authorization.

Checks run entirely through WSL2:
- /home/russell/dskit/.venv/bin/python -m pytest children/index_options/tests/test_configs.py::test_exact_manifest_and_agent_parity -q — 1 passed in 0.17s.
- Read-only configuration, aggregate-hash, arithmetic, parameter/timing and local-link checks — passed apart from INT-01.
- git diff --check f98af937 ca9b210 — passed.
- Candidate HEAD verified; working tree remained clean.

Reviewed proposal SHA256: ff0fbcd80b33a1b874340fd49bcdfa9ec95bd5da5972c98103f4ad3827f521fc.
Reviewed manifest-test SHA256: da8dba9f2343716fddc538da1e851f2798ba0a7c94b4ddea057e5b4ce4928e80.

Limits: no training, providers, raw forecast regeneration, 2026 data access, deployment, full suite or new HPO/refit execution. Proposed data sufficiency, exact folds/clocks, runtime integration, qualification thresholds and operational feasibility remain future gates.

Unresolved counts: Critical 0; Major 0; Minor 1; Nit 0.

</details>


## History/COVID/split amendment review closure

Two fresh sequential GPT-6 reviewers completed the amendment design review on immutable `e78ec1be45d9b7223ad77664e789f35821064e16`, base `8c379bcb`, proposal SHA256 `760fddcebb6158316887a31e4de87fe8c4e751d62507f70830a50cbcfd7de85c`. Both returned **zero Critical/Major/Minor/Nit**. This closes publication of the actionable handoff; it does not certify the future implementation, historical-data coverage or production qualification.

Parent verification: exact manifest/parity test passed (1 in 0.27 seconds); local links resolve; original reviewer output bytes are preserved; executable sources, configs, tests and all reports are unchanged. The integration reviewer's last status command failed before execution; the parent subsequently completed the same WSL2 diff check, clean-status check and candidate SHA256 verification successfully. No behavioral changes followed either review; only this evidence and handoff/journal closure were appended.

The next session should use the amended H1/C1/S1 contract and its conditional Stage-0–4 authority. It must still perform the required implementation reviews and data checks before real fits. No training or protected holdout inspection has started in this handoff session.

<details>
<summary>Amendment correctness reviewer output (formatting normalized)</summary>

Design review: zero proven findings; zero unresolved Critical/Major/Minor/Nit. This approves the scoped handoff design only.

Candidate: e78ec1be45d9b7223ad77664e789f35821064e16; base 8c379bcbb79b614a96de2f745f1d17ae4106a618.
Reviewer/lens: /root/history_split_correctness_r1, GPT-6, independent correctness/statistical/economic/authority review.
Scope: amendment, surrounding proposal requirements, RE-ENTRY precedence, and A1091 journal/generated README consistency.
Reviewed proposal blob: 5fd914eacb4ebc2d03c3ac6456220b66e9ed1953.

Attempted falsification covered:
1. History and leakage: H1 requires source-to-panel reconciliation, immutable changed identities, eligible earlier observations at each cutoff, protected readers, mature labels, and no automatic universe expansion. The sampling exception does not demonstrably violate the owner’s exact request to expand warmup history: it requires disclosure and review before comparison and cannot justify arbitrary historical truncation. Actual sampling suitability remains a Stage-0 gate.
2. COVID: February–June replaces H2, without another fit. Training, transformations and monitoring freeze before the first score origin; fit/monitor labels must already be available. The two score cohorts reuse forecasts, acknowledge overlapping outcome periods, and cannot become separate winner-selection opportunities.
3. Economic split accounting: requirements cover forward/reverse/repeated splits, incompatible sources, missing factors, price-return versus dividend-return definitions, price/volume units, event-crossing labels and feature windows.
4. Future actions: consistent-vintage testing explicitly rebases both stored observations and their ledger. Recovery of historical share units does not authorize future-event predictors. Existing AsTradedClose append-only-ledger test is not accepted as evidence of this stronger property.
5. Authority: conditional next-session Stages 0–4 authorization retains the 216-attempt cap and excludes protected holdout access, acquisition, spending, upgrades and deployment. Historical reviews are explicitly archived rather than reused.

Checked existing seams: StockDailyBars, PriceCalendarCDFPanel and its inherited label path, CorporateActionRule, DailyBarFeatures, AsTradedClose, and relevant stock-bar, feature and price-calendar test sources. Their limitations are compatible with the amendment’s requirement to audit and repair demonstrated gaps before fits.

Checks: WSL2 read-only source/diff inspection; clean git diff --check; no candidate changes to code/tests; canonical workflow files match origin/main. No tests, training, provider calls, or raw protected-data inspection performed.

Limits: this review does not establish actual history completeness, source adjustment semantics, point-in-time universe validity, configuration feasibility or end-to-end split correctness. Those are explicitly required future implementation evidence, not unresolved defects in this handoff.

</details>

<details>
<summary>Amendment integration reviewer output (formatting normalized)</summary>

Independent integration/feasibility/evidence DESIGN review of candidate e78ec1be45d9b7223ad77664e789f35821064e16 against 8c379bcb, reviewer /root/history_split_integration_r1.

Reviewed proposal SHA256: 760fddcebb6158316887a31e4de87fe8c4e751d62507f70830a50cbcfd7de85c.

Verdict: zero unresolved Critical/Major; no proven Minor/Nit. This closes this lens on the amendment’s design/handoff, not its future implementation or empirical qualification. No findings require disposition.

Assigned invariant coverage:
- H1 source coverage and identity: The amendment requires catalog/snapshot/config inventory, per-ticker source-to-panel reconciliation, exclusion counts and reasons, investigation of truncation/sampling, and earliest reliable expanding history. A cached-panel min/max alone explicitly cannot establish completeness. The disclosed and frozen sampling contract, paired forecast identities and fixed row weights prevent undisclosed candidate-specific sampling. New inputs require new source/panel/fold/checkpoint identities; original artifacts remain frozen.
- Source integration: Inspected StockDailyBars, PriceCalendarCDFPanel, its source-reference/fingerprint path and since/window filtering, DailyBarFeatures, AsTradedClose, and CorporateActionRule. These are actual existing seams. Their presence does not establish the proposed end-to-end guarantees, which the amendment correctly makes Stage-0 obligations.
- C1 chronology and budget: February–June 2020 replaces 2020 H2 in the amendment and Stage 1; it adds no fourth development fold. Independently checked 15 + 108 + 27 + 54 = 204, plus nine possible reapplications and three counted pilots yields 216 attempts. COVID scoring freezes fit/monitor information before its first origin; later outcomes cannot update parameters during that fold. Crash/rebound slices reuse forecasts, distinguish quote from settlement cohorts, and cannot select a favorable slice. Later folds explicitly retain COVID history.
- S1 economic semantics and clocks: The design separately requires price/dividend/volume basis, adjustment vintage, action direction/effective date, stable identity and known-at metadata. Pure splits must preserve returns; raw/as-traded units remain distinct from split-neutral model accounting. Feature availability is restricted to origin-time information; subsequent actions may enter outcome accounting only. Invalid or unknown action evidence requires refusal/quarantine and coverage accounting.
- Existing tests versus missing evidence: Inspected stock-bar tests, stock-feature synthetic construction, price-calendar corporate-action tests and AsTradedClose tests. The existing test_as_traded_close_is_point_in_time appends a later ledger event while retaining earlier adjusted prices; it does not establish consistent-vintage invariance. The amendment explicitly rejects that as an equivalence test and requires rebasing matching prices, volume and action ledger. It also requires forward/reverse/repeated/no-split, invalid/missing-factor, inconsistent-source and post-origin-action coverage across the actual processing path. Those are future gates, not falsely reported completed tests.
- Options boundary: Applicable contract identity, strike, multiplier and deliverables are required; unsupported nonstandard actions must abstain or be excluded. The contract does not demand an options execution engine for base-only research. Its rejection of universal strike-division assumptions agrees with the linked OIC corporate-action reference (https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies), including reverse splits and changed deliverables.
- Authority, provenance and handoff: Proposal, new top RE-ENTRY entry and appended A1091 consistently describe conditional next-session implementation and Stages 1–4. Original publication reviews are explicitly historical. Fresh design review, required ADR approval, focused implementation tests and two fresh final lenses precede training. Protected 2026 access, providers/new acquisition, cloud spending, environment upgrades, deployment and live trading remain excluded. Stage 5 remains a separate decision. The handoff specifies actionable ordering and deliverables without treating historical reviews as approval for changed implementation.

Checks and limits:
- All filesystem/code inspection ran through WSL2. Read root/child instructions and canonical implementation/skeptic procedures; the local workflow/skeptic/wrap files had no diff from available origin/main.
- Verified HEAD, proposal hash, four-file diff inventory, journal append and generated display change. Earlier status checks were clean; no files were edited.
- Read existing synthetic tests; did not execute tests or repeat the parent’s focused manifest check. No training, raw historical/holdout inspection, acquisition or full suite occurred.
- A final read-only diff-check/status command failed before execution because the Windows sandbox could not reopen a writable descendant beneath a read-only carveout. That last check is unverified; preceding inspection completed.
- Actual owned-history completeness, historical action/vintage availability, rebuilt panel correctness, future fold feasibility and runtime behavior remain implementation prerequisites, not results established by this review.

Unresolved counts: Critical 0; Major 0; Minor 0; Nit 0.

</details>
