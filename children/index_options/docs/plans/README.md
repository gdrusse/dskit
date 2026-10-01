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

Next: use the selected exact seven-day horizon for the feature-family/PCA plan,
then establish chronological training/validation/test admission and paired
out-of-sample decision-region skill. Coverage maximization is not model skill
and this census does not prove which horizon will predict best.

Review lock: candidate `96371511` passed two fresh sequential independent lenses,
`/root/coverage_multi_correctness` and `/root/coverage_multi_integration`, both
C0/M0/m0/n0. Retained conversation reports independently verify all counts,
isolation and persisted outputs; the correctness lens additionally exercised
mixed-ticker fixtures with different winners, duplicates, ties and exclusions.
Reviewed config blob: `383a3a5a0e247580969f3014750200374a13aa46` (unchanged).
Reviewed plan blob: `ced3a145789bd837956ea5fd38ccf74c189ef19b`; subsequent changes
only append this review evidence and the editorial re-entry summary. No pending
in-scope findings. No implementation or behavioral contract changes after lock.
