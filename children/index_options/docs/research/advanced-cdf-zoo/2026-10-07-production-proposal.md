# Production CDF candidate: synthesis and proposed validation plan

## Question

What should power the next production-candidate run, which features should it use, and how should validation and hyperparameter optimization (HPO) proceed after the completed advanced architecture study?

## Finding

**Recommend a pooled, ticker-head PatchTST with the 42 base inputs as the lead production candidate. Keep the empirical distribution as the operational reference/fallback; qualify the learned model before promotion. Retain pooled CNN as the simpler challenger and pooled VanillaTransformer as the strongest advanced challenger. Exclude options from the initial default.**

This is a research proposal, not an accepted execution contract or permission to inspect the protected 2026 holdout. No new model training, HPO, providers, deployment or trading was performed for this synthesis. Publishing this proposal does not approve those actions. The prior reports and closeout memo were verified on remote main at `f98af937a6855e0145f3a4d46342bdc2402b126f`.

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

### Proposed validation and HPO schedule

This schedule is a finite research budget to approve, not a running job. All dates below are proposed quote-date windows. Before execution, generate and independently review a new versioned fold manifest with exact market-session timestamps, feature known-at cutoffs and target settlement times. Do not edit or repin the historical study config to run on main: its frozen source identity intentionally differs from current main.

**Stage 0 — prerequisites, no training.** Verify the point-in-time data/universe contract, onboard cross-run inputs through the existing source catalog, define the actual forecast publication clock, and inventory the fit/search/report seams. Select only rows whose entire target is known by each fit or monitor cutoff. Purge across fit→monitor and monitor→score boundaries by actual label availability, not a guessed fixed number of rows. Retain a 40-quote-date monitoring band where feasible and the existing training-only transforms. Refuse insufficient-history folds rather than silently substituting later dates.

**Stage 1 — features, at most 15 fits.** Five bundles × three pooled PatchTST development folds, seed 11. Proposed development score windows: 2020 H2, 2021 H2 and 2022 H2. Use expanding earlier history, preserving purges and monitor bands. Availability must be audited before accepting these windows. Their existence and capacity sufficiency are not asserted here. Select the feature bundle with the rule above; use that same bundle for all three architectures.

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


## Publication review and disposition

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
