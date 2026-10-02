# Research stages

S0: finite synthetic ingestion, exact contract/quote identity, condor expiry
cashflows, persisted artifacts, isolation and independent software review.
No profitability experiment is authorized by completing S0.

S1 needs separate owner approval for a bounded provider/sample budget and
adapter manifest. Verify PM series/expired-chain coverage, settlement source,
publication clocks, revision policy, quote quality, license/two-machine rights,
cost and storage. No provider has passed acceptance.

S2 needs an approved experiment and run authorization: forecast realized
variance or return distributions, compare simple baselines with ML, use
time-ordered overlap-purged validation and train-only transforms, calibrate
uncertainty, model costs and preserve untouched final evaluation.

S3: audit the existing Pyomo pack's credit-spread cash/margin semantics before
MIO allocation. Compare integer spread choices with a simple risk-budget
heuristic. Do not implement solver plumbing inside this child.

S4: independently approved broker, accounting, settlement/reconciliation,
risk limits, monitoring and authority contracts, with paper evidence before
live approval. RL hedging and deep models are optional later questions.
Neither model sophistication nor backtest profit guarantees an edge.

## Three-step data selection (owner rulings, 2026-10-01)

Current direction; supersedes conflicting cohort/split wording below. Each step
is a ticker/source-neutral JSON pipeline whose outputs feed the next; nothing
derivable from a prior step is typed in.

| Step | Inputs | Outputs |
|---|---|---|
| 1. Tradable dates | ticker; option-chain source; one configurable underlying-close source | dates per exact DTE 1-45 (table + chart); argmax DTE, ties to shorter |
| 1b. Feature engineering | step-1 selected DTE (`selected.jsonl`); the prepared panel step 1 read; the ADR-0203 tail-data sources | step-1 cohort rows plus engineered families and expiry density, explicit nulls with reasons, `fe_<family>_status`; per-family summaries and clock notes (`carry.json`) |
| 2. Feature availability | ticker; family set (implied CDF is one family); step-1 dates at the selected DTE | dates per family; dates for every non-empty family combination; per-date feature/family gaps |
| 3. Holdout and folds | step-1 DTE and dates; step-2 availability; holdout share H (0.2), tau (0.9), train/validation sizes and retrain interval, all in dates | locked holdout dates; admitted families; training dates; fold table (train/val dates, boundaries, purge counts) |

- Step 1 has no CDF filter: "available" means any option with a valid bid/ask,
  since the MIO may select any. A missing implied CDF is a step-2 gap.
- The holdout is locked first (last H of step-1 dates, purging earlier labels
  that reach it) and is the final frozen MIO test.
- A family is admitted when available on at least tau of pre-holdout dates;
  training uses complete-case dates. Gaps are assumed random for now, though
  surface gaps cluster in 2011-2014. Rows are weighted equally.
- Folds keep intraday_equities' rolling-origin shape (warmup fold, retrain per
  fold) without its extra late periods. Windows and the retrain interval count
  dates, so retraining follows expiry frequency; embargo = selected DTE.
- Step 1b (ADR-0217) materializes variance_gap, ohlc_shape, positioning_changes,
  expanded_volatility_context and expiry density; weekday one-hot (ADR-0214) is
  pending its merge. Pre-2021 entries are mostly Fridays, so weekday is partly
  confounded with era. Step 2 reads step 1b's output; its only change is
  `pipeline.source.params.root`.
- Status: steps 1, 1b and 2 built (QQQ/SPY/IWM; AMZN is out of scope until
  ADR-0216's close-bar reader lands); real runs pending in WSL; step 3 awaits
  ADR-0222 (data-driven holdout and count-sized folds).

### Steps 1-2 runbook

Two ticker/source-neutral files: `configs/run-step1-expiry-coverage.json` and
`configs/run-step2-feature-availability.json`. Tickers are `foreach.keys` in
both (edit both for a subset); another panel changes only `pipeline.source.params`.
From `children/index_options`, with PYTHONPATH, the venv and the same
`systemd-run` 6G/no-swap/1740s prefix as the runbook below:

    # reuse ./pipeline_runs/cdf-horizon-source if acquired, else run its 3 onboarding commands
    mkdir -p pipeline_runs/step1-expiry-coverage/selection pipeline_runs/step2-feature-availability
    python -m dskit.pipeline run configs/run-step1-expiry-coverage.json --asof 2026-10-01
    python -m dskit.onboarding init --root ./pipeline_runs/step1-selection-source
    python -m dskit.onboarding register-source step1-selection --catalog-source step1-selection --connector localtables --config @configs/source-step1-selection.json --activate --root ./pipeline_runs/step1-selection-source
    python -m dskit.onboarding acquire --source step1-selection --stream selected --mode backfill --root ./pipeline_runs/step1-selection-source
    python -m dskit.pipeline run configs/run-step2-feature-availability.json --asof 2026-10-01

Read: step 1 `runs/<run>/carry.json` `coverage__<t>.records` (dates per DTE 1-45,
full) and `winner__<t>.records`, `artifacts/coverage_figure__<t>/coverage.png`,
`selection/selected.jsonl`. Step 2 `carry.json` `cohort_summary__<t>` and
`summary_<f>__<t>`, `combinations.jsonl` (8,192 rows per ticker: `with_<f>=1`
puts a family in the combination, `dates` counts dates where all of them are
available, the all-zero row is the cohort total) and `dates.jsonl` (per-date
family flags). Repeat: the writers refuse overwrite, so move
`step1-expiry-coverage`, `step1-selection-source` and `step2-feature-availability`
aside first. Cross-checks against run `cdf-horizon-coverage-2026-10-01-6ae71aa4`:

- step-1 `listed_dates`/`listed_forecasts` per DTE equal its `listed_counts__<t>`;
  any difference is unsettled rows;
- step-2 `implied_cdf` "yes" dates equal its `coverage__<t>` `eligible_dates` at the
  selected DTE (DTE 7: QQQ 1,497, SPY 1,879, IWM 1,377);
- the all-zero combination row equals `cohort_dates`; each singleton row equals
  its `summary_<f>` "yes" dates.

The earlier CDF-based selector and QQQ gap runbooks below are superseded.

### Step 1b runbook: feature engineering (ADR-0217)

`configs/run-step1b-feature-engineering.json` reads the prepared panel step 1 read
(`pipeline.source`), step 1's onboarded `selected.jsonl` (`step1_selection`, as in step 2)
and the ADR-0203 tail-data sources (`pipeline.panel`, values unchanged). It writes
`pipeline_runs/feature-engineering/panel/input_panel.jsonl`: one row per
ticker/quote_date/expiry of step 1's cohort (selected DTE read from `selected.jsonl`;
row count pinned to its `listed_forecasts`), every prepared field unchanged plus the
four families and `expiry_density`. A field that cannot be computed, or whose entry-time
availability cannot be established, is an explicit null with its reason in
`fe_<entry>_reasons`; `fe_<family>_status` says whether the row's identity was found.
All 18 open-interest-derived fields are withheld (`oi_publication_clock_unverified`)
until the OI publication clock is audited; lifting that is a JSON edit. Weekday one-hot
is declared `pending_merge` (`pending_inputs`). Prerequisites: the prepared panel
onboarded as in the horizon runbook; step 1 run and `selected.jsonl` onboarded as in the
steps 1-2 runbook; `ls /home/russell/dskit-cdf-tail-data/children/index_options/pipeline_runs/predictive_cdf_tail_data_20260930/raw_chain_features.parquet{,.sources.json}`
(rebuild if absent: `python -m index_options.cdf_study configs/run-predictive-cdf-tail-data.json --stage prepare`).

    mkdir -p pipeline_runs/feature-engineering/panel
    systemd-run --user --wait --pipe --collect --unit=feature-engineering -p MemoryMax=6G -p MemorySwapMax=0 -p RuntimeMaxSec=1740 -p WorkingDirectory="$PWD" -E PYTHONPATH="$(realpath ../..)" /usr/bin/time -v /home/russell/dskit/.venv/bin/python -m dskit.pipeline run configs/run-step1b-feature-engineering.json --asof 2026-10-01
    python -m dskit.onboarding init --root ./pipeline_runs/feature-panel-source
    python -m dskit.onboarding register-source cdf-horizon-panel --catalog-source cdf-horizon-panel --connector localtables --config @configs/source-feature-panel.json --activate --root ./pipeline_runs/feature-panel-source
    python -m dskit.onboarding acquire --source cdf-horizon-panel --stream input_panel --mode backfill --root ./pipeline_runs/feature-panel-source

QQQ, SPY and IWM run together (`foreach.keys`; keep them equal to step 1's). For one
ticker set `foreach.keys` to `["QQQ"]`; the row pin follows. Repeats need fresh writer and
onboarding paths (writers refuse overwrite). A refusal names its cause: absent identities
(the reader lacks cohort rows; `max_absent_fraction` is 0), an agree field that differs
(prepared panel and reader built from different sources), or a carried column the prepared
panel lacks. Post-run check: `runs/<run>/carry.json` holds `panel.provenance` and, per
ticker, `features__<t>.summary` (every family and carried entry, every field, withheld ones
included) and `.provenance`; it is their only persisted copy. Step 2 then changes one
line in `run-step2-feature-availability.json`: `pipeline.source.params.root` from
`./pipeline_runs/cdf-horizon-source` to `./pipeline_runs/feature-panel-source` (the
source node's note that both children onboard under the `cdf-horizon-source` names goes
stale). Families holding a withheld field (`positioning_changes`, `liquidity`,
`chain_nodes`) read `no` on every date until the audit (owner question 2, ADR-0217).
AMZN is out of scope until ADR-0216's close source lands.

## Draft: Torch CDF feature families (2026-10-01)

Planning only; feature choices and experiment design remain open.

- Core candidates: lagged underlying returns, realized volatility, and forecast
  horizon. Audit the availability of each extra predictor separately.
- Initial option-chain feature family: same-expiry implied-return quantiles at
  1/5/10/25/50/75/90/95/99 percent; compare against proposed 21- and 41-level
  grids extracted from original quotes, not interpolated from nine inputs.
  Record quote quality, strike coverage, missingness and empirical tail splices.
  Denser levels and tail allocation remain to be specified before testing.
- These are entry-known pricing-distribution features, not outcome labels.
  Torch still predicts realized returns and learns with configurable losses.

### Verified QQQ coverage constraint

Read-only census of local
`/home/russell/data/options_archives/philippdubach_full/qqq/` on 2026-10-01:
`underlying_prices.parquet` has 6,571 distinct price dates, 1999-11-01 through
2025-12-15. The 15 annual `options_2011.parquet` through `options_2025.parquet`
files contain 15,345,882 contract rows on 3,700 distinct dates, 2011-03-23
through 2025-12-15. All option dates have underlying prices.

Requiring chains retains 56.31% of available price dates: 2,865 earlier dates
and six missing dates within the option window are unavailable (43.69% total).
Through 2017, the raw comparison is 4,571 price dates versus 1,704 option dates
(62.72% fewer). These are source-date counts, not usable training examples:
lookbacks, matured targets, expiry availability, purging and quote quality
further constrain training. Contract rows are not independent observations.

The completed CDF studies already used chain-derived panels beginning in 2011.
Adding chain features therefore does not newly discard the earlier history
from those panels; it does constrain a proposed longer-history price model.
The prior option-proxy study had 32,355 eligible QQQ rows out of 32,639 (99.13%),
which is existing-panel coverage, not coverage of the longer price history.
See the 2026-09-29 risk-neutral architecture comparison memo.

### Proposed comparison

Start with the option-era QQQ sample (2011-2025 source coverage) and a compact
Torch model using underlying/horizon features plus implied-return quantiles.
The owner accepts the shorter history as the initial research tradeoff.
Earlier option-transport results motivate this starting point; they do not
establish a higher performance ceiling or superiority at selected strikes.

Split chronologically with overlap purging; the 3,700 source dates are not all
training dates. Keep empirical and corrected option-implied CDF benchmarks,
plus a price-only Torch control on identical eligible dates. Begin with the
existing nine-quantile representation and test denser grids on development data.
Evaluate paired decision-region skill with losses, counts and coverage reported.
Previously inspected years remain reused historical evidence, not a fresh
holdout; freeze the final partition and selection rules before execution.

Defer longer-history price-only pretraining to a later experiment. Earlier
dates can support return losses, but cannot supply actual listed-strike losses
or evaluation without their chains. Do not invent those regions.

### Feature-family inventory and proposed ablations

Inventory checked against the shipped configs, predictor selectors and panel
builders; a column present in a panel is not necessarily consumed by a model.
The current QQQ MLP has 40 inputs: 22 return lags, four realized-volatility
windows (1/5/22/66 sessions), VXN, eleven horizon/lifecycle fields, the reference
scale and constant `is_QQQ`. The earlier single-index LightGBM CDF/quantile and
scaled-empirical studies used the same 39 non-indicator inputs. Pooled QRF,
NGBoost and MLP studies used 42, adding three index indicators. Earlier MLPs
already used Torch; the new Torch interface changed configurability.

Keep entry-known horizon and target-scaling context common to comparisons.
Audit the existing lifecycle terms separately: observed archive age is not a
verified exchange listing age. Omit constant index indicators in QQQ-only
experiments. Actual realized settlement DTE, outcomes and future dividends are
not predictors. Proposed independently switchable families:

1. **Implied CDF shape:** 9/21/41 quantiles from original entry chains; exact
   denser probability levels remain a preregistration decision. Median, widths,
   skewness and other proxy summaries are derived variants of this family,
   not independent sources of information. Accompany with proxy coverage and
   quality diagnostics.
2. **Return history and trend:** 22 daily log-return lags; prior rich studies
   also used 5/22-session summed returns. Shared by MLP and non-neural methods.
3. **Realized volatility and price-bar shape:** 1/5/22/66-session volatility;
   5/22-session upside/downside measures; overnight and intraday returns,
   high-low range, Parkinson variance, jump proxy, and backward range/jump/
   overnight/intraday variance averages. Test the OHLC additions separately
   from the established realized-volatility inputs.
4. **Volatility-index context:** QQQ's VXN is already in the MLP. Rich studies
   added VIX9D/3M/6M, VVIX, SKEW and GVZ; tail-data work added VIX1Y,
   VXN/RVX/VXD/OVX/VXEEM/VXSLV/VXTLT. Avoid duplicating VXN mechanically;
   audit differing observation clocks before merging equivalent fields.
5. **Option surface and its changes:** ATM IV, 25-delta put-call skew and
   curvature; 1/5/22-observation changes within the same expiry series and
   neighboring-expiry IV slopes. Missing observations mean these change lags
   are not necessarily exactly 1/5/22 exchange sessions.
6. **Implied versus realized variance:** same requested-horizon implied and
   trailing variance, their difference and ratio. Keep this small derived
   family separate to test whether it adds to quantiles and realized volatility.
7. **Liquidity and positioning proxies:** spreads, quote depth, contract count,
   put/call OI ratio, volume imbalance, lagged OI changes and unsigned
   Greek-times-OI summaries. These are snapshot proxies, not signed dealer
   positions or observed trade direction. Volume/OI usability needs its own audit.
8. **Rates and broader market context:** policy/3-month/10-year rates, credit
   and commercial-paper spreads, broad dollar, WTI and financial conditions.
   Preserve observation age/missingness; audit release clocks and revision
   history before treating retrospectively acquired series as point-in-time.
9. **Detailed chain representation (alternative):** the prior rich study
   used nine nodes with moneyness, IV, spread, OI, depth and a mask (54 fields).
   CatBoost and QRF consumed these as flattened predictors; neural set encoders
   also tested them. This is an alternative representation to compare with the
   implied-CDF summary, not an automatic addition to the first compact model.

Missingness/age/quality flags accompany their source families. Fit imputers and
scalers on training rows only. Macro-event counts remain deferred: the prior
study found no local historical calendar with event and known-at dates.

### What earlier evidence establishes

The older HAR/HAR-IV scale studies used realized-volatility windows and an
implied-volatility index; their forward-volatility target differs from the
current terminal-return CDF target. Reuse feature ideas, not score rankings.
The 2016-2018 tail-data ablation tested OHLC, requested-horizon variance gap,
liquidity/positioning and expanded Cboe inputs. The variance-gap and flow groups
improved average CRPS versus that study's incumbent by 0.270% and 0.146%; all
candidates failed its historical generic-tail guards, so later evaluation was
not run. Those guards do not define this new decision-region experiment.
The earlier surface study also showed no consistent global-score benefit.
These are candidate families, not demonstrated selected-region improvements.

The systematic selection procedure below supersedes the earlier informal
family-addition order. Feature comparisons hold cohort, model and loss fixed.

Sources: [base CDF comparison](../memos/2026-09-28-predictive-cdf-comparison.md),
[QRF/NGBoost inputs](../memos/2026-09-28-predictive-cdf-methods.md),
[surface study](../memos/2026-09-29-predictive-cdf-option-surface.md),
[rich architecture study](../memos/2026-09-29-risk-neutral-cdf-architecture-comparison.md),
[feature ablation](../memos/2026-09-30-tail-data-feature-ablation.md), and
[current Torch config](../../configs/run-predictive-cdf-qqq-torch-zoo.json).

### Initial horizon proposal: QQQ, 30 calendar days (see coverage revision below)

Use QQQ only and exactly 30 calendar days to entry-known scheduled settlement,
not 30 trading sessions or an undeclared maturity band. This connects to the
existing 30-day strike analysis. The prior actual-settlement census contained
766 dates in 2011-2025, before feature/target/decision-region filtering; it is
not a certified count for this scheduled-horizon cohort. Verify that cohort
and per-fold counts before locking dates. Do not use realized later calendar
changes to choose entry rows. One-day and seven-day studies are deferred.

Treat this as a small, dependent sample: many 30-day outcomes overlap, and
many strikes on a date do not create independent return observations.

### Systematic feature selection and dimension reduction

1. **Freeze the experiment contract.** Pin cohort identities, entry-known
   decision regions, chronological folds, one compact MLP/CDF head, one
   composite loss, fixed seeds and candidate budget. Keep empirical and
   corrected option-implied benchmarks and the current core-feature control.
   Remove constant QQQ/horizon columns and exact duplicate predictors using
   training data only. Preserve needed scaling/context outside predictor PCA.
2. **Select groups inside training history.** Screen all nine listed families
   as additions to a minimal shared context. Include the 9/21/41 implied-CDF
   representations as alternatives within one family. Then perform bounded
   forward group selection, adding at most three families, with a final
   remove-one-group check. This tests incremental value without enumerating
   every subset; useful interactions can still be missed. Keep the unchanged
   core-feature control so the procedure must justify its extra complexity.
3. **Compare raw and compressed representations.** Within high-dimensional
   numeric families, compare no PCA against 2/4/8 retained components where
   training rank and dimension permit. Impute and standardize before PCA;
   fit every transform anew on each inner training fold. Components replace
   the selected family's numeric inputs; flags and required context pass
   through. Do not append components while retaining every original input.
   Select raw/PCA and component count jointly with family choices using
   validation decision-region skill, not explained variance alone. The old
   PCAAugmentedCDF appends components; audit its downstream selectors and
   existing generic transform seams before implementing true replacement.
4. **Separate selection from evaluation.** Use chronological inner folds for
   family/PCA/quantile selection, then refit using only the outer training
   history and score the next outer period. Purge training labels that overlap
   each validation/test start. Never fit PCA or select features once using the
   entire panel. Predeclare a bounded candidate menu and stopping/tie rules
   before running; prefer fewer inputs when scores are within that tolerance.
5. **Report predictive value and stability.** Primary metric remains paired
   out-of-sample Brier skill at selected listed-strike regions versus empirical.
   With one horizon, there is no across-DTE averaging. Keep threshold/row
   weights fixed, apply guards only at decision regions, and retain generic
   tail/global metrics as diagnostics. Report train/validation losses and skill,
   feature dimensions, selected groups across folds/seeds, row/date/expiry/
   threshold counts, coverage and date-block uncertainty accounting for overlap.

All compared variants use the same cohort and observation clocks. Missingness
policies are predeclared; a sparse feature family must not silently change its
scored dates. Nested evaluation measures the selection procedure, not a feature
set chosen after seeing every outer result. Reused 2011-2025 research history
must still be labeled reused, even with correct nested chronology.

PCA is an optional compression test, not a guarantee of better prediction: it
preserves input variance, which need not be predictive at selected strikes.
Defer nonlinear compression/autoencoders and a wider model/loss search until
this small comparison is informative. Run through standard JSON-configured
pipelines in WSL2, under 30 minutes/6 GiB per run; no experiment is launched
while this plan is still being drafted.

Method references: [PCA](https://scikit-learn.org/stable/modules/decomposition.html#pca)
and [nested selection/evaluation](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html).

### Horizon coverage audit and proposed revision (2026-10-01)

Read only the distinct date/expiration pairs in all 15 local QQQ annual option
files. Use the panel's regular-holiday planned settlement convention for entry
horizons; map actual settlement to XNYS and require an available underlying
close there for the following counts. These are pre-feature/pre-quote-quality
counts, not finalized eligible training rows:

- Exact 1 calendar day: 1,291 rows/dates with settlement close (1,292 raw).
- Exact 7 calendar days: 1,505 rows/dates (1,510 raw).
- Exact 30 calendar days: 761 rows/dates (766 raw).
- One planned trading session: 1,564 rows/dates (1,565 raw), spanning
  1-4 calendar days. Do not conflate this with one calendar day.
- All listed horizons of 1-45 calendar days: 32,641 rows across 3,699 dates
  with settlement close (32,804 rows across 3,700 dates raw).
- Nearest expiry to 7 days among 1-45-day listings: 3,695 rows/dates
  (3,700 raw); observed selected horizons span 1-10 days.
- Nearest expiry to 7 days restricted to 3-10 days: 3,635 rows/dates
  (3,640 raw). This keeps a recognizable weekly maturity range.
- Nearest expiry to 30 days restricted to 21-45 days: 3,650 rows/dates
  (3,670 raw), if preserving a monthly decision problem is preferred.

For nearest-expiry rules, select using entry metadata first: minimize absolute
calendar-day distance to target, then shorter horizon, then nominal expiry.
Only then check label availability; never select a different expiry because its
future outcome happens to be available. End-of-archive missing outcomes account
for the raw/matured differences. Earlier years have fewer near-term expiries:
2012 had 51 exact-one-day dates and 52 exact-seven-day dates, versus 195 and
243 respectively in 2024. A shorter exact horizon is not daily historical data.

The exact-30-day proposal above is now under reconsideration for coverage.
Simpler single-expiry-per-date alternative: one nearest-to-seven-day expiry per entry,
within the declared 3-10-day band, conditioning on its actual entry-known
planned calendar/session horizon. This is a maturity range, not a fixed-seven-
day target. Recount eligibility under its own listed-strike/wing rules;
the earlier fixed-$5 monthly-region rule is not assumed suitable for a week.

Superseded pooling alternative (owner requires an exact horizon): pool all 1-45-day
QQQ expiries, include
planned horizon as a predictor, and report skill separately for a prespecified
weekly or monthly decision cohort. Balance training contributions by entry date
so recent dense listings do not dominate merely through row multiplicity.
Multiple expiries share market paths; 32,641 rows are not independent events.
Keep folds grouped by date, purge overlapping labels, and use date-block
uncertainty. No horizon revision or run has been implemented; these counts
inform the draft choice.

### Exact-seven-day clarification: supersedes maturity-band proposals

Owner requires an exact horizon; do not substitute the proposed 3-10-day band
or pooled horizons. Read-only join on symbol/quote_date/nominal_expiry verified
all 1,510 raw scheduled-seven-day identities against the completed risk-neutral
run's raw_chain_features.parquet. Of those, 1,505 have a settlement price and
are in its final evaluate/late/input_panel.parquet. Seven have rn_proxy_eligible=0.
All 1,498 eligible rows have nine finite, nondecreasing implied-return quantiles.

One otherwise eligible row, entry 2025-01-02 / nominal expiry 2025-01-09,
is seven days under the panel's regular-holiday schedule but maps to settlement
close 2025-01-08 (six days) under its actual XNYS convention. Thus the verified
intersection is **1,497 distinct dates** with a seven-calendar-day historical
settlement-close target and eligible implied-CDF proxy, spanning 2011-03-24
through 2025-12-08. The nine probability levels are 1/5/10/25/50/75/90/95/99%.
This verifies the proxy representation, not a calibrated physical distribution
or the future study's complete feature/decision-region eligibility.

Sources: the canonical completed run at
`/home/russell/dskit-cdf-option-surface/children/index_options/pipeline_runs/predictive_cdf_risk_neutral_20260929/`,
raw QQQ annual date/expiry identities, and underlying settlement-close dates.
The seven proxy failures are entries 2013-09-23, 2013-10-11, 2014-03-14,
2014-12-12, 2015-01-09, 2019-10-18 and 2022-06-10.
Before execution, reconcile the entry-known holiday schedule and contract
settlement convention explicitly; do not silently filter prospective entries
using calendar changes unavailable at entry. The 1,497 figure is historical
coverage under the current settlement-close convention, not execution evidence.

## Stock-options reuse audit (ADR-0212, accepted)

**Stock-specific:** the approved work now has a separate thin
`children/stock_options` child. Stock options, earnings windows, contract
adjustments and
share assignment need separate checks. The existing ETF mechanics are reusable,
but the SPY/QQQ/IWM grid, VIX proxy and split refusal cannot be copied silently.

The accessible pilot source is Alpaca indicative. On 2026-09-30 its AMZN chain
returned 972 snapshots across 10 expiries (2026-10-02 through 2026-11-06);
21 raw AMZN IEX daily bars were also pulled. The two raw JSON files are outside
the checkout at /home/russell/data/stock_options/amzn/alpaca_indicative.
Their SHA-256 values are
8bb801d7efa5f036ef17dff44f1bc156df8470fce1263525122e61ff6aeb8d0f
(chain) and
a59c6b9d8991f5c2fdaf349103bb5bd999f549cdb9b4bce319d208c2b90bf72e
(stock bars). They carry no credential values.

A second free pull contains 7,390 field-complete AMZN daily price records from 1997-05-15
through 2026-09-30 (SHA-256
982acac92f17e808bdca33d4361069cd98646bfd272b06fb4b735cadef26ff66).
It is a Yahoo chart export stored outside the checkout at
/home/russell/data/stock_options/long_history_audit. It has complete OHLCV,
ordered unique dates and four split events. The values are split-adjusted, so
pre-2022 prices cannot be paired with raw historical option strikes. This is
price history, not option history, and its last record may be provisional.

The matching MSFT pull contains 5,934 field-complete daily records from
2003-03-03 through 2026-09-30, SHA-256
bcbcc4ccc491a1db21580a0a4cfc20b765b6c67c32ccd34a5a07fd94b74bfc8f.
It is stored beside AMZN. Both files contain all 5,030 expected XNYS sessions
from 2006-10-02 through 2026-09-30 and valid OHLCV. Across 1,553 overlapping
IEX sessions, adjusted closes were within 1%; this is a single-exchange sanity
check, not official-close reconciliation.

The free Alpaca tier enumerated 23,254 inactive AMZN contracts from February
2024 onward. Requiring the OCC `AMZN` root and source multiplier 100 retained
65,372 unique, valid daily trade bars across 8,892 symbols and 126 expiries in
the 30-45 DTE window, SHA-256
49d7b4f5da463b8b551c1673fc667e0a8bb3baebb4313e3b6b1ef0e1c52ff9c8.
These bars have no bid/ask. Historical executable-price work still requires an
entitled quote source. The first predictive model uses adjusted stock and market
history only. Training and development end before the first scored option date,
with every label settled and a 45-calendar-day embargo. Option-period data define
the mixed-integer optimizer's contemporaneous action set; they do not tune the
forecast. Split rows stay after correct adjustment, genuine market shocks stay,
and only predeclared data/contract-identity failures are excluded. Proposed first
cadence:
daily end-of-day chain and stock closes, weekly entry review, 30–45-day
same-expiry condors with nonoverlapping positions. Minute bars and faster
sampling wait for an execution question and suitable data access. ADR-0212
authorizes only the thin child bootstrap; no strategy run is authorized.

Use AMZN first and MSFT as the immediate comparator. All accessible AMZN option
history is post-split and Amazon has paid no dividends, simplifying the first
assignment study. In the two available 30/37-DTE expiries, MSFT had tighter
median spreads and greater volume. The 44-DTE request returned no free snapshot
for either symbol; retained artifacts do not establish active-contract coverage.
AMZN is the operationally simpler baseline; MSFT is the liquidity benchmark. This is not evidence of a profitable strategy.

### Historical QQQ-only coverage selector (superseded runbook below)

The owner requested an argmax over exact horizons 1-45, implemented entirely by
existing nodes in `configs/run-cdf-horizon-coverage.json`. In that original version, input ticker was
`cohort.params.where[0].value`; source settings were `source.params`. `groupby`
counts distinct quote dates, `max` plus `filter` selects the largest count, and
`min` breaks ties toward the shorter horizon. No Python implementation changed.

The source is the existing, validated options-derived CDF panel, imported with
`configs/source-cdf-horizon-panel.json` through localtables. It is not a raw-chain
CDF reconstruction pipeline. Its source contract requires an upstream verified
eligibility flag and finite, ordered nine-quantile proxy; missing quantiles and
missing targets are excluded. Fully observed distribution tails are not claimed.
Historical actual settlement-close DTE supplies the exact horizon; this census
does not establish a prospective holiday/calendar or execution policy.

QQQ result: **7 days, 1,497 distinct dates**. Runner-up: 14 days, 1,400.
Other anchors: 1 day, 1,281; 30 days, 754. These are CDF-eligible prepared-panel
counts, not the larger raw-chain counts previously listed. All 45 output counts
match an independent finite/monotonic-quantile census of the source parquet.

Run: `pipeline_runs/qqq-cdf-horizon-coverage-2026-10-01-21153f83`.
Counts: `pipeline_runs/cdf-horizon-counts.jsonl`; winner:
`pipeline_runs/cdf-horizon-winner.jsonl`. Document identity:
`16ea55b693f3867c6c2d521175439a676ae0c197c4ca0f1db137551cbe0f40c6`.
Source panel SHA-256: b9a11111285f64ec1883c05ef69bdb3d3a5e44706ecf5d55f6da9e84c4a36afc.
Acquisition snapshot: `8815723bdfc9a93e925f82bcb7f027307888ace6b9770d37878c9f17cb4b82ec`.

WSL2 execution under hard 29-minute/6-GiB/no-swap caps: acquisition 37.64s,
2,350,400 KiB peak RSS; pipeline 16.20s, 1,373,936 KiB. CPU only.
Validation and planning passed. Focused existing Filter/Derive/GroupBy/KeyBy
tests: 78 passed, 91 unrelated tests deselected. Three configuration graph
probes passed: distinct-date/tie/exclusion behavior, ticker substitution and
empty-cohort refusal. No unrelated suite or model fitting ran.

Reproduce from `children/index_options`, with the worktree on PYTHONPATH:
```bash
python -m dskit.onboarding init --root ./pipeline_runs/cdf-horizon-source
python -m dskit.onboarding register-source cdf-horizon-panel --catalog-source cdf-horizon-panel --connector localtables --config @configs/source-cdf-horizon-panel.json --activate --root ./pipeline_runs/cdf-horizon-source
python -m dskit.onboarding acquire --source cdf-horizon-panel --stream input_panel --mode backfill --root ./pipeline_runs/cdf-horizon-source
python -m dskit.pipeline run configs/run-cdf-horizon-coverage.json --asof 2026-10-01
```
Use a fresh onboarding root for changed source data and new report paths for a
rerun; writers intentionally refuse to overwrite the previous evidence.
Only prepared-data coverage is selected here; further feature/region admission
can reduce the training sample.

Coverage candidate `af9ad4f4` closed after two sequential independent reviews:
`/root/horizon_json_correctness` and `/root/horizon_json_integration`, both
C0/M0/m0/n0. Their retained conversation reports cover correctness/source
semantics and integration/test quality respectively. The second lens separately
verified all 45 counts and source SHA-256 and confirmed all three report writers
refuse overwrite without changing bytes. No implementation/config changes after
review; this evidence append and re-entry update are editorial only.

### Current runbook: all-index horizon coverage (2026-10-01)

Scope: extend the existing JSON selector from base `7db30fa0` for the owner's
three-ticker coverage request. Allowed edits: that config, this plan, re-entry
and automatic journal evidence. Reuse foreach and DeclaredFigure; no Python
implementation, training, HPO, raw-chain reconstruction or strategy execution.
Acceptance: independent per-ticker argmax, every populated horizon retained,
run-local charts, source-matched counts, bounded WSL2 run and focused checks.

**Inputs are `foreach.keys` and the prepared data source.** In
`configs/run-cdf-horizon-coverage.json`, set `foreach.keys` to `["QQQ"]` for one
ticker, or `["QQQ", "SPY", "IWM"]` for the shipped combined run. No other ticker
edits, custom scripts or per-symbol config copies are needed. The source is
`pipeline.source.params`: onboarding root, source name and stream. Existing
acquired data can be reused. For a new panel, change `path` in
`configs/source-cdf-horizon-panel.json`, import into a fresh onboarding root
using the init/register-source/acquire commands above, and point the runner
at that root. Do not reacquire changed data into an old evidence root.

The source must supply `symbol`, `quote_date`, `expiry`, `actual_calendar_dte`,
`terminal_return`, `rn_proxy_eligible` and the nine finite, ordered `rn_q_*`
fields listed in the config. Eligibility and historical settlement conventions
must already have been validated upstream. This selects coverage from an
options-derived panel; a ticker plus arbitrary raw option quotes is insufficient.
Further feature selection or decision-region screening can reduce these counts.

Run inside WSL2 from `children/index_options`, using the project venv and
repository on PYTHONPATH (matplotlib is required). Standard command:

```bash
python -m dskit.pipeline run configs/run-cdf-horizon-coverage.json --asof 2026-10-01
```

The tested bounded equivalent, with paths changed only for your checkout:

```bash
systemd-run --user --wait --pipe --collect --unit=cdf-horizon-coverage \
  -p MemoryMax=6G -p MemorySwapMax=0 -p RuntimeMaxSec=1740 \
  -p WorkingDirectory="$PWD" -E PYTHONPATH="$(realpath ../..)" \
  /usr/bin/time -v /home/russell/dskit/.venv/bin/python \
  -m dskit.pipeline run configs/run-cdf-horizon-coverage.json --asof 2026-10-01
```

Each standard run directory contains `carry.json`: complete arrays at
`coverage__qqq.records`, `listed_counts__qqq.records`, and `winner__qqq.records`
(and `__spy`, `__iwm`). Coverage rows contain ticker, exact DTE, distinct eligible
entry dates, eligible forecast rows and distinct expiries. These small arrays
are retained in full, not summarized. `listed_counts` means prepared-panel
coverage before CDF eligibility, not raw-chain coverage. Zero-eligible horizons
are absent; absence means zero within this source. An entirely empty ticker
refuses selection. Equal maxima choose the shorter DTE.

Charts are `artifacts/coverage_figure__<ticker>/coverage.png` in the same run;
`carry.json` also records their paths. They show date-count coverage over DTE,
not the underlying return CDF. `report.md` records node completion. All reports
are run-local; the old shared `cdf-horizon-*.jsonl` writers are retired. Identical
config/source/asof resolves to the same run identity; preserve existing evidence
and choose another `outputs.run_root` for a deliberate repeat.

Final execution: `pipeline_runs/cdf-horizon-coverage-2026-10-01-6ae71aa4`,
document hash `c90a04828d2103f1a4a3f27fd78a7746f8309375bb72baf2b0e3ac04c99f0402`.
Same acquisition snapshot and source SHA-256 as the earlier QQQ run above.
All three argmaxes are **7 calendar days**: **QQQ 1,497**, **SPY 1,879**,
**IWM 1,377** distinct eligible dates. Each ticker has 45 populated horizons.
All 135 eligible counts, 135 prepared-panel counts and three winners matched
an independent pandas census requiring finite, ordered quantiles and targets.
Each chart reports 45 points and zero skipped. A first bar-chart run was
replaced by this line-chart run after visual inspection found crowded labels;
all counts and winners match exactly across both runs.

All 37 nodes completed in 16.55 seconds, peak RSS 1,374,220 KiB (1.31 GiB),
CPU only, under hard 29-minute/6-GiB/no-swap limits. Focused foreach/matplotlib
checks: 102 passed, 10 inapplicable conformance cases skipped. Three JSON-graph
probes passed distinct-date/tie/exclusion behavior, singleton ticker substitution
and empty-cohort refusal. Prior filter/derive/groupby/keyby checks remain 78
passed; those implementations are unchanged. No full suite ran.

Next (owner direction): audit feature availability on the selected exact
seven-day CDF-eligible observations before feature selection/PCA or training.
Use the same ticker/source inputs and preserve the baseline identities and
counts: QQQ 1,497, SPY 1,879, IWM 1,377. Inventory the proposed feature families
against the existing Torch MLP/GRU and earlier non-Torch studies. For each
feature and family, report present/finite/missing counts, date windows, required
lookback and entry-time availability; report complete-case intersections and
the exact observations lost. Distinguish genuinely missing source data from
features that can be derived causally from existing history. Do not silently
impute, remove observations or change the selected horizon to improve coverage.
Then choose feature-family experiments and chronological split admission;
fit selection/PCA only on training data. Coverage maximization is not model
skill and this census does not prove which horizon will predict best.

Review lock: candidate `96371511` passed two fresh sequential independent lenses,
`/root/coverage_multi_correctness` and `/root/coverage_multi_integration`, both
C0/M0/m0/n0. Retained conversation reports independently verify all counts,
isolation and persisted outputs; the correctness lens additionally exercised
mixed-ticker fixtures with different winners, duplicates, ties and exclusions.
Reviewed config blob: `383a3a5a0e247580969f3014750200374a13aa46` (unchanged).
Reviewed plan blob: `ced3a145789bd837956ea5fd38ccf74c189ef19b`; subsequent changes
only append this review evidence and the editorial re-entry summary. No pending
in-scope findings. No implementation or behavioral contract changes after lock.

### Supplied AMZN source: process precondition (2026-10-01)

The owner confirmed the historical stock-option bar input is
`/home/russell/data/stock_options/amzn/alpaca_free_history/daily-bars-standard-dte30-45-2024-02-01_2026-09-30.jsonl.gz`.
SHA-256: `49d7b4f5da463b8b551c1673fc667e0a8bb3baebb4313e3b6b1ef0e1c52ff9c8`.
It contains one metadata record and 343 nested response pages, totaling 65,372
AMZN option OHLCV trade bars. The per-bar fields are `t,o,h,l,c,v,n,vw`, keyed
by option symbol. The acquisition covers only 30-45 DTE, not a census of 1-45.

This file is not the prepared input consumed by the horizon-selection JSON:
it has no `rn_proxy_eligible`, nine `rn_q_*` values or underlying terminal-return
targets. Changing the ticker/source path alone cannot produce valid implied-CDF
coverage JSON from it. Flattening the pages would fix only the container shape.
Trade-bar closes also do not establish contemporaneous two-sided option quotes.
Do not substitute raw trade-bar counts for eligible-CDF date counts, fill missing
quantiles, or interpret a missing prepared panel as zero available raw options.

Before applying the selector to AMZN, supply an upstream validated CDF panel
with the documented schema, point-in-time source/settlement conventions and
matched underlying outcomes. Assess whether the trade-only source supports an
explicit proxy study before implementing preparation; this runbook does not
claim it already reconstructs such a panel. No new provider pull, CDF builder,
model fit or stock-CDF coverage result was produced in this process audit.

The associated `contracts-inactive-2024-02-01_2026-09-30.json.gz` contains
23,254 contracts with strikes, expiries, type and multiplier metadata. The
`alpaca_indicative/2026-10-01-chain-dte30-45-asof-2026-09-30.json` file contains
168 unique snapshots, including 150 positive finite IV values and bid/ask quote
timestamps all on 2026-09-30. These are useful contract and current-chain inputs,
but do not supply historical daily IV/quote surfaces or settled outcomes for
those future expiries. Their presence does not make the historical trade-bar
file a prepared CDF panel. The parent inspected these additional files after
the independent operator completed the historical-bar schema check.

### Independent runbook reproduction (2026-10-01)

Subagent `/root/runbook_operator` independently followed this runbook and ran
all three ETF tickers through the standard CLI. Durable evidence is under
`pipeline_runs/runbook-reproduction-20261001/`: config, `execution.log`, and
`runs/cdf-horizon-coverage-2026-10-01-6ae71aa4/carry.json`. Only
`outputs.run_root` changed. All 135 coverage rows, 135 listed-count rows and
three winners matched the original run; all charts contain 45 points with no
skips. Final execution: 37 nodes, 15.82s, peak RSS 1,373,928 KiB, zero swaps,
under hard 29-minute/6-GiB/no-swap caps. Document hash stayed
`c90a04828d2103f1a4a3f27fd78a7746f8309375bb72baf2b0e3ac04c99f0402`;
carry SHA-256 `fe51e29782b1db400c58cdf70c3a1f75648fac26ec550aa1f5e37ff61a987032`.
The initial subagent shell hit a Windows sandbox setup error before execution;
the existing approved WSL Python entrypoint resolved it. No application change
was needed. The AMZN source incompatibility above was inspected, not an
executed stock-CLI refusal. The owner subsequently requested the conversion
layer; proposed ADR-0213 covers that separate shared preparation slice.

### QQQ seven-day feature-gap pipeline (2026-10-01)

Owner requested a JSON pipeline to expose feature-family gaps before selection.
The existing nodes suffice: run-qqq-feature-availability.json reads the same
prepared source as the horizon selector, fixes QQQ and actual DTE=7, and retains
all 1,497 eligible identities. No new Python capability, imputation, training,
PCA or feature ranking. It checks 155 candidate numeric fields in 13 subgroups
of the nine planned families, with explicit source masks and age limits.

From this child in WSL2, with the project venv and repository PYTHONPATH, create
the declared output directory then run the standard command under the same
6-GiB/no-swap/1740-second systemd prefix used above:

    mkdir -p pipeline_runs/qqq-feature-availability
    python -m dskit.pipeline run configs/run-qqq-feature-availability.json --asof 2026-10-01

The source must already be acquired by the existing horizon runbook. For another
prepared source, edit pipeline.source.params. Ticker and fixed DTE are explicit
in pipeline.cohort.params.where; the baseline row writer expect=1497 deliberately
refuses a changed cohort count. If changing cohort intentionally, re-audit
identity membership and update that expectation. A deliberate repeat uses a
fresh outputs.run_root and fresh paths on the three records-write nodes.

Outputs tell the gaps directly:

- carry.json feature_profile__<feature>.records contains present/finite flags,
  explicit gap reason, counts and date windows. Reasons are field_absent,
  null_value, nonnumeric_or_nonfinite, or none. An absent column is a gap in the
  prepared materialization, not proof raw data cannot supply it.
- feature-gaps.jsonl names every affected symbol/date/expiry, feature and reason.
- family-gaps.jsonl names every date/expiry failing each family's required-value,
  age or mask checks. family_contracts.merged contains the exact field lists,
  quality predicates, lookbacks and observation-clock caveats.
- rows.jsonl retains all 1,497 source rows plus family/intersection flags so no
  rejected date disappears. summary_<family>.records reports complete/gap counts.
- deferred_representations.merged records why 21/41-quantile variants are not
  materialized and why a historical known-at macro calendar is unavailable.

Date-window outputs are UTC epoch milliseconds for the source quote date, not
publication/decision timestamps. Finite coverage does not certify historical
release time or vintage: macro fields need a vintage audit; OI needs publication
semantics; cross-expiry slopes need reconstruction using entry-scheduled DTE
before prospective use. These clock limitations are emitted in family_contracts,
not just described in this document.

Executed results: core implied-CDF/returns/realized-volatility/current volatility
context each complete on 1,497 dates. Surface levels: 1,394; basic liquidity:
1,495; full surface changes: 280; all nine detailed chain nodes: 200; complete
macro family: 292. Core plus surface and liquidity: 1,394. Four newer subgroups
(OHLC shape, expanded volatility context, matched variance gap, positioning
changes) are absent from this particular materialization: 32 absent fields.
Their shorter existing tail-data panel covers 403 of these identities, so do
not inner-join it and silently lose 1,094 dates. Rebuild those feature families
for this fixed cohort as the next preparation step.

The standard run emitted 71,880 feature/date gap records: 47,904 field_absent
and 23,976 null_value; 9,812 family/date gaps. These are gap cells, not distinct
training observations. There are still exactly 1,497 dates. All-family complete
intersection is zero because some families have not been materialized.
The gap run used 30.11 seconds, peak RSS 2,104,308 KiB, zero swaps, hard caps.
Earlier count-only validation independently matched every identity and all
155 finite counts to the pinned source. Focused JSON tests cover fixed-cohort
admission, masks/age rules, null/absent/invalid distinctions and exact gap keys.

Next: materialize the deferred derivable families, verify release/vintage
requirements, rerun the same gap pipeline, then freeze missingness/cohort rules
before the planned nested group selection/PCA comparisons. No selected feature
set or predictive-skill claim follows from this availability census.

QQQ review scope: the new pipeline JSON, its three focused tests and this
runbook. Fixed source and 1,497 unique baseline identities, hash-bound family
definitions/quality predicates, finite versus entry-time distinction, complete
gap persistence and refusal to overwrite are the acceptance invariants.
Changing ticker/horizon/source is an explicit new cohort, not a silent fallback.
No generic implementation change is needed for this audit.


Published output handoff (owner-requested): commit aeea072a includes the actual
run files under children/index_options/pipeline_runs/qqq-feature-availability/.
Start with feature-gaps.jsonl for exact feature/date/expiry/reason records;
family-gaps.jsonl identifies failed family/date pairs, and rows.jsonl retains
every baseline observation plus flags. The run subdirectory
runs/qqq-feature-availability-2026-10-01-44bbebfb/ contains carry.json, config.json
and result.json. In carry, family_contracts.merged explains quality predicates
and clock limits; feature_profile__<feature>.records and summary_<family>.records
provide counts and windows. These outputs are a committed evidence exception
to the normally ignored pipeline_runs tree; do not overwrite them.
Use fresh writer paths and run_root for a new execution.

verification.json records independent equality of the exact source identities,
all 71,880 feature-gap reason/identity tuples, and all 9,812 family-gap tuples.
SHA-256: feature-gaps 8bafb07bb64c1aa29fa22b72fb93c8fb8974113560741ee5c99f1d6ae179bd01;
family-gaps 0e09df69dffb505e66d041154dc7ceef2e2b6aa3c3f9e3eb91f187abe61fbf41;
full rows e12fa2ca0f5f5e4c11b26e6101aa8cc8040e279548ad2a35a005d17e1413aac9.


Delivery lock: QQQ runtime/config/tests/output bytes remain unchanged from the
reviewed 83dcba51/aeea072a evidence. Both original lenses verified all 1,497
identities and gap sets; final a0128bf3 integration reran its three audit tests.
AMZN now uses the same audit definitions and output schema on its 42-day/90-date
cohort; see the stock plan runbook. Both evidence folders are tracked for the
owner-requested agent handoff. Next: prepare missing families without dropping
dates, audit source clocks, rerun gaps, then plan selection/PCA on training only.
