# Production development: Stage-0 evidence and unresolved gates

## TL;DR

The requested production-development cycle is **incomplete and training is blocked**.
No new fit was attempted: 0 of the 216 allowed attempts were consumed. This
packet completes a bounded inventory, date/purge rehearsal and historical recovery
exercise; it does not approve the data, freeze a new production panel, select a
champion or calibrate uncertainty.

## Execution contract and identities

The owner authorized implementation, necessary ADRs, pilots and Stages 1–4,
independent reviews and delivery. The controlling
[proposal](../research/advanced-cdf-zoo/2026-10-07-production-proposal.md)
requires H1/C1/S1/R1/U1 before real fitting. No protected 2026 observations,
new providers, environment upgrades, full suite, MIO, strategy backtest,
deployment or trading are authorized.

Started from `80a194d526956b24745afe61024d4041b019d4b6` in an isolated WSL2
worktree. While inventory ran, main advanced to `aa44819c686fc2734f1478b62e1184960f44b28e`;
the checkout was fast-forwarded. Existing index_options and CDF implementation
and workflow files were unchanged. The new ParquetSeries pack was inventoried:
it reads day-named files, not arbitrary mixed-year price archives.

**Bounded acceptance:** preserve accurate Stage-0 evidence and close two
demonstrated reusable software gaps under ADR-0248/0249. Allowed paths are the
Parquet and Yahoo packs and docs, experiment reader, thin child reader/bar
facades, their existing focused tests, this memo/aggregate manifest, RE-ENTRY,
decision log and append-only action journal/generated display. No experiment
config, old report or original artifact changes. This software/evidence packet
does not close the whole H1/S1/R1 gate or complete the requested development cycle.

## Owned history and source-to-panel reconciliation

A metadata-only search of the WSL home, excluding Git, caches and environments,
found 1,261 matching paths, including 771 underlying-price parquet files,
88 catalog files and 320 input-panel files. Copies, synthetic fixtures and
unrelated projects are included in those path counts. Filename matching is
not proof of a complete owned-history census; arbitrary names, excluded
locations, unmounted storage and other owned machines remain unverified.

Owned AMZN and MSFT full-history Yahoo archives were found in the stock_options
long-history source, already onboarded as `stock-long-history-audit`. Their
names indicate 1997 and 2003 starts; those are **not verified eligible dates**.
The existing AMZN registration pins SHA256
982acac92f17e808bdca33d4361069cd98646bfd272b06fb4b735cadef26ff66 and declares
corporate_actions_complete=true; the stock_options plan says it was inspected.
This is a provenance lead, not independently verified matching-vintage evidence.
Metadata inspection of all 771 discovered underlying Parquets found six files
with pre-2016 minima: two copies each of SPY/QQQ/IWM, outside the configured
393-name universe. No metadata errors or unknown date-statistics groups occurred.
Raw mixed-year JSON was not decoded to establish earlier-row counts.
The existing Yahoo archive connector decodes the whole response, so it cannot
be used unchanged for a protected-row inspection. The legacy normalized
underlying observation stores are additional leads, not admitted new inputs.
Do not silently exclude that earlier history or claim its eligibility.

Verified configured source hashes reproduce 833,637 pre-2026 dates across 393
tickers. Date/action projections and panel quote/settlement projections applied
strict `< 2026-01-01` predicates; null temporal metadata refuses. No prices or
outcomes from protected rows were inspected. Whole-file hashing/copying was
used only for identity/retention.

The existing cached panel contains 455,149 rows with both quote and settlement
before 2026; its SHA256 is
`719284f168b25f1dccefe300123451e1e5a9c38145e2934f8412afaa767b9d40`.
No panel quote date lacks a corresponding configured source date.
Of 378,488 source dates absent from that restricted panel, an ordered
date-only diagnostic identifies:

- 362,657 nominal expiries falling on a non-session date. The existing
  `exact_dte=31` rule keeps **actual** settlement DTE equal to 31, so it drops
  those dates instead of keeping the preceding-session shorter horizon.
- 8,646 nominal 31-day targets reaching 2026.
- 13 remaining dates with a missing source session on the outcome path.
- 7,172 dates still requiring feature, corporate-action or reference-scale
  reconciliation.

These categories sum to the difference; their precedence is retained privately.
They are not a newly approved sampling rule or a final exclusion ledger.
No stride was introduced. The historical config's `since` is 2007; the
configured stock acquisition recipes start in 2016. Earlier eligible owned
history must be reconciled before a new panel is frozen.

The configured sources contain 103 declared pre-2026 non-unit split factors;
31 are irregular under the existing integer-or-reciprocal classification.
None of the projected factors is missing, nonfinite or nonpositive.
This does not prove action completeness, correct adjustment direction or
valid treatment of spinoffs/mergers. All three stock-daily-bars snapshots
contain the same 116 raw-response hashes: they are not independent
adjustment-vintage evidence.

The universe is a later research cohort. Its option-activity ranking config
ends on 2026-10-02 and uses 20 sessions. No historical membership/known-at
inventory was established. Existing schemas and acquisition clocks cannot be
silently promoted to a point-in-time universe or publication-time proof.
No universe expansion or fabricated older options data occurred.

## Demonstrated source-path gaps remain open

The partial independent Phase-0 inventory found three unresolved Major source
families, not actual owned-data corruption. That review preceded the bounded
repairs below; it did not approve implementation or actual source data. The synthetic probe could not launch in the
Windows sandbox; its escalated retry remained pending and was aborted. No tests
ran in that review. The actual transcript is /root/stage0_semantics; the private
summary is hashed in the public manifest. Its source findings follow:

```text
Partial Phase-0 correctness inventory of base 80a194d526956b24745afe61024d4041b019d4b6.
Reviewer: /root/stage0_semantics, GPT-6. Not a clean pass; three unresolved Major families.

Major 1 — protected-read boundary absent from original price/feature path.
ParquetRows calls pq.read_table without projection/predicates, converts selected
columns to Python, then applies windowing. PriceCalendarCDFPanel and daily-feature
configurations use it; the later holdout filter does not prevent source materialization.
Reuse a proven protected reader and constrain auxiliary inputs before real panel work.
The newer ParquetSeries was not inspected by this reviewer.

Major 2 — corporate-action evidence states collapse at ingestion.
StockDailyBars treats missing/null events as empty, overwrites same-date events in
a dictionary, drops actions on null-price rows, and does not validate finite positive
ratios. Missing action coverage becomes indistinguishable from no-event rows.
CorporateActionRule accepts absent coefficients and its jump rule needs a nonunit
coefficient: prices [100,50] with no coefficients produce no declared event.
This is a source-backed permitted counterexample, not proof of actual owned corruption.

Major 3 — configured log-volume is not consistent-vintage invariant.
DailyBarFeatures computes log(volume); the existing stock-feature test explicitly
pins the shift. A later pure 2:1 split with consistently rebased prior price /2 and
volume *2 changes the historical feature by log(2). Returns/dollar volume and
within-window relative volume can remain invariant. AsTradedClose is an existing
reverse-product seam, but neither it nor standardization certifies matching ledgers.
Do not use as-traded prices for split-crossing return targets.

Limits: the panel assumes split-adjusted closes and strips coefficients from prices.
Acquisition timestamps do not establish historical known-at or stable membership.
Synthetic invariance does not prove owned action completeness or independent vintages.
No unsupported options mappings or fabricated history are justified.

Tests: none executed. The synthetic-probe command could not launch in the Windows
sandbox; an escalated retry remained pending and was aborted. No probe/report file
was created by the reviewer. This retained summary and the actual agent transcript
must not be represented as executed probe evidence.

Untested: actual sources; end-to-end split families; protected auxiliary readers;
new reader reuse; full PIT universe; option mapping; experiment calibration/fit
isolation; checkpoint invalidation; recovery; uncertainty integration.
Unresolved: Critical 0, Major 3, Minor 0, Nit 0.

```

The author separately inspected the new ParquetSeries: its day-file contract does
not directly replace these mixed-year files. Reuse protected predicate reads or
preprojected onboarded snapshots and existing adjustment/feature seams once the
source/basis contract is frozen. Software cannot manufacture missing historical
publication clocks, stable identities or action coverage. The two bounded repairs below address software refusal/projection; they do not
resolve missing provenance or absolute-volume semantics.

## Minimal correctness repairs: implemented and focused-tested

ADR-0248 adds DateBoundedParquet in the existing pack. ParquetRows.read_bounds
projects behind ANDed source-date predicates; invalid declarations, unsupported
schema, missing/null temporal metadata and malformed admitted dates refuse.
The legacy window keeps its old semantics. The price-calendar facade forwards
explicit bounds; the experiment loader shares the owner and requires its pin
when source pins are supplied. No auxiliary input becomes protected implicitly.
Arrow may decode pages to evaluate predicates; this is an application
materialization boundary, not byte-level isolation.

ADR-0249 adds YahooSplitInventory in the existing onboarding pack. The archive
connector validates supplied factors before yielding; a true completeness
declaration needs a present valid inventory to mark unit_history_verified.
StockDailyBars strict_split_inventory is opt-in and defaults false, preserving
legacy artifacts. Its rows, note and transform refuse missing inventory,
invalid factors, duplicate effective dates and lost/duplicate split-date bars.
No real mixed-year JSON was processed. Dividend semantics, JSON member names
already lost during decoding, source completeness and adjustment basis remain
outside this bounded parsed-inventory repair.

Independent Phase-0 transcripts: /root/stage0_execution_checkpoint approved
the protected-reader direction with explicit conditions; /root/action_inventory_design
approved the shared split-inventory direction conditionally. The original partial
source inventory above remains historical and is not silently relabeled clean.
Final implementation lenses are still required.

RED evidence: the old reader returned two excluded synthetic payloads; the
price facade ignored its bounds; experiment source pins accepted an omitted
shared reader; strict bars accepted unknown split inventory. Each regression
then passed after its corresponding repair. Focused results: Parquet pack
66 passed; experiment predicate/pairing/source-identity subset 37 passed
(25 deselected, no fit tests); child price/window subset 10 passed
(69 deselected); Yahoo/stock-bar tests 75 passed; exact-file/pipeline purity
3 passed; onboarding purity 4 passed. Pandas/NumPy timedelta deprecation
warnings remain; no shared environment changes or full suite ran.

The archived cache's existing rejection ledger also provides a useful lead:
among the 393 configured tickers it records 2,436 action-path and 4,747
missing-reference exclusions. Their sum differs from the 7,172 unresolved
date diagnostic by 11, all in VALE. These are different diagnostic populations;
do not overwrite the unexplained residual or claim per-date causal reconciliation.

## Purge rehearsal: tested dates, not frozen production folds

Reused the existing fold-plan code on date-only rows of the historical panel.
The three score ranges are 2020-02-03–2020-06-30,
2021-07-02–2021-12-31 and 2022-07-01–2022-12-30. Their corresponding
settlement ranges are 2020-03-05–2020-07-31,
2021-08-02–2022-01-31 and 2022-08-01–2023-01-30.

The COVID rehearsal uses fit quotes through 2019-08-26, settled through
2019-09-26; 40 distinct monitor quote dates from 2019-09-27 through
2019-12-31, settled through 2020-01-31; then scores from 2020-02-03.
Fit/monitor/score row counts are respectively
138,577/11,956/17,742; 198,529/13,553/25,621; and
247,694/14,783/26,487 for the three rehearsed folds. Every monitor has
40 distinct quote dates; each preceding band's last settlement is strictly
before the next band's first quote. Both later fit bands retain all 17,742
cached COVID rows.

These checks use settlement dates, **not independently verified publication
timestamps**, and precede per-head feature/capacity admission. Reconcile older
history and availability, then regenerate and review the actual immutable
fold manifest. The rehearsal file must not be used as a production run input.
Crash/rebound quote cohorts remain February–March and April–June; no new
forecasts or scores were produced for them.

## Recovery and retention

Copied the complete historical advanced-study directory and panel cache to
`production-development-retained-20261007` beneath the private data root.
628,439 files were checked source → copy → source by SHA256
(5,054,771,657 logical bytes, excluding code and inventory receipts).
All historical trials, failures, checkpoints, score shards, runtime overlays
and evidence in that directory are retained; no original was removed.

Also retained a source archive at 80a194d5 and 60,130 installed-environment
entries (8,455,025,471 regular-file bytes plus recorded symlinks).
This copies dependencies; it does not modify or upgrade the shared environment.
Owned onboarding inputs already live in durable data roots; this is not a
claim that every historical checkout dependency has been independently
materialized or restored. Earlier CNN/zoo artifacts remain preserved at their
original locations and must not be cleaned up.

A fresh system-Python `-S` process loaded the archived source, archived runtime
overlay and archived dependencies. A Python audit hook denied file opens into
the original training checkout, old panel checkout, task checkout and shared
venv. This is a functional isolation check, not an OS security sandbox.
One historical pooled/base/PatchTST/fold-33 checkpoint reproduced seven AAPL
forecasts dated 2024-02-06–2024-02-23 and their ordinary/tail CRPS at
`rtol=1e-6, atol=1e-7`. Dependency versions matched the frozen configuration.
Unknown heads, missing weights and modified weights refused.

This verifies **one historical inference package**. It is not full artifact
recovery, baseline-distribution replay, optimizer continuation, a new
champion package or disaster recovery. OS Python/shared libraries/GPU driver
remain system dependencies. Existing fitted checkpoints contain no full
interrupted-optimizer state; no such capability was built.

The second-copy destination is still awaiting the owner's preference.
Only WSL and C: were mounted; initial free space was about 702 GiB and
77 GiB, respectively. After materialization it was about 685 GiB and 59 GiB.
WSL's virtual free space is not a reservation of physical host capacity.
A C: copy would not establish independent-host/disaster recovery. No second
copy, storage cleanup or historical branch deletion is approved by this receipt.

## Future MIO handoff: specified and format-checked only

Use the existing U1 coherent discrete distribution, shared before any action
selection. For increasing terminal-price grid values `x[j]`, nominal masses
`p[j]`, alternative masses `q[j]`, cumulative masses `P[j]`, `Q[j]`
and positive entry spot `S`, the set requires nonnegative masses summing to
one and:

```math
\frac{1}{S}\sum_{j=1}^{m-1}(x_{j+1}-x_j)\lvert Q_j-P_j\rvert\leq\rho.
```

Prices and spot must share currency, security/share basis and adjustment
identity; the radius is dimensionless. A Gaussian mixture here models
`z = log(terminal_price / spot) / reference_scale`. Preserve its full
mixture representation and scale, alongside any discretization, so a future
consumer can check the mapping `price = spot * exp(reference_scale * z)`.
An arbitrary finite grid is not an exact representation of unbounded tails.
Require an explicit discretization/tail policy; payoff clipping is permissible
only with the separately demonstrated bounded-payoff support argument.
No payoff-dependent grid or option-contract adjustment is validated here.

The handoff record must carry:

- Format version; forecast ID (stable security identity, quote/origin clock,
  nominal expiry, settlement rule); issue time and label-availability rule.
- Model/checkpoint, feature-order/transform, input/action/universe and panel
  identities; complete nominal distribution; paired baseline identity.
- Ordered price grid, probability vector, spot, currency/share units,
  support/discretization policy and reference scale.
- Distance/version, radius, calibration recipe/evidence identity and cutoff,
  grouping/support counts, selection provenance and evidence role.
- Optional jointly validated lower/upper CDF bands and their separate identity;
  fallback/abstention rule and qualification status.

Nine synthetic exact-arithmetic/format assertions checked normalization,
negative mass, grid order, negative radius, crossed/nonmonotone band data,
nominal zero distance and joint price/spot scaling. The illustrative transport
distance 1/25 is **not a calibrated radius**. These checks establish that the
format can express U1; no reusable runtime validator, optimization model,
solver, worst-case witness or trading simulation was installed or executed.

Actual `radius`, `cdf_bands` and `calibration_id` remain null; execution
eligibility is false. Abstain until a separately qualified fallback exists.
An empirical baseline's research scores alone do not qualify a live fallback.
Early-stop monitors are not independent calibration. Neither seed dispersion
nor Bernoulli-residual quantiles identifies conditional-CDF confidence bands.

**Precise missing calibration requirement:** settled, selection-accounted
chronological forecast/outcome evidence on the corrected panel; a frozen
distance/support/cohort/minimum-support rule and declared calibration criterion;
then later untouched evidence for validation. The proposal's mean robust-loss
criterion also requires replaying a decision policy for each radius. That study
is excluded here and was not run. No radius or confidence guarantee can be
inferred from this packet.

## Separately authorized qualification protocol

After Stage 3/4 eventually freeze the champion, reserve and all transforms,
record one inference checkpoint, universe membership known at that freeze,
the empirical reference, fallback rule and every selection/calibration choice.
Keep protected 2026 data sealed under the present authority.

A concrete prospective alternative is: 126 exchange sessions strictly after
the selection freeze for post-selection calibration, wait until their last
target plus publication delay has matured, then 252 later exchange sessions
for validation, again waiting for all targets. Resolve exact dates and clocks
before any new observations are inspected. These durations are proposed
design choices, not evidence of adequate effective sample size or permission
to execute. Do not refit on either period or use the reserve as a second
chance on the same test.

For forecast qualification, freeze weighted CRPS and the paired empirical
denominator, tail metric and the proposal's 0.5% relative tail-deterioration
margin before opening the test. Use synchronized cross-sectional quote-date
blocks: 60 trading sessions primary, 30 sensitivity, both at least the maximum
outcome horizon in calendar span; record 31-calendar-day targets separately.
Use the proposal's 95% intervals: lower bound of weighted skill above zero and
upper bound of relative tail deterioration below the frozen margin. Insufficient
effective blocks, wide intervals or an unfrozen criterion mean inconclusive.
Report PIT and 50/80/95/99% prediction-interval coverage with prespecified
cohorts/support and no post-result threshold choice.

Radius qualification remains separate until a permissible predeclared
calibration/validation criterion exists. Any payoff-risk calibration needs its
own decision-layer authorization. Forecast qualification alone does not qualify
an MIO, a trading strategy, monthly refitting, adaptive recalibration or deployment.

## Status and next bounded action

Implemented here: the two bounded generic repairs, thin facade wiring, private
diagnostic/retention/recovery scripts and this evidence packet. Tested: protected projections, date reconciliation, cached
purge rehearsal, one historical inference restore, three restore refusals and
nine synthetic format assertions. Empirically validated: none of the new
model-development, uncertainty-calibration or production claims.

PatchTST with the base inputs remains the research starting candidate.
CNN and VanillaTransformer remain challengers. Stage 3 has not selected a
champion or reserve; no unrun result is substituted for that decision.
Pilots and Stages 1–4 are deliberately unrun, with all 216 attempts remaining.

Next: resolve the owned-history/action-vintage/availability gates, finish the
7,172-date reconciliation and historical universe declaration, choose the
second-copy destination, and freeze protected input conversion and actual
fold/calibration roles. Complete final reviews of the bounded repairs; resolve the remaining basis and
input-boundary gaps through the existing seams before any real panel or fit. Do not ask again for
the already authorized development scope. Re-run no historical model job to
manufacture missing provenance.

## Reproducibility and handoff

Private receipts and scripts are in `production-development-audit-20261007`;
the public manifest records their hashes without raw records, forecasts,
weights, secrets or machine-specific paths. The original census artifacts
remain unchanged. Diagnostics are not onboarded downstream production inputs.

To repeat the isolated sample recovery from its retained environment, use WSL:

```bash
cd "$DSKIT_PRIVATE_DATA_ROOT/production-development-retained-20261007"
/usr/bin/python3 -S "$DSKIT_AUDIT_ROOT/restore-isolated-environment.py"
```

The saved script intentionally refuses to overwrite its existing receipt.
For a new verification, preserve the old script and receipt and give the new
receipt a distinct filename before running. Verify the retained manifests and
script hashes first. Do not execute the frozen historical training config:
its old source guard pins intentionally differ from current main.

Independent review and remote-delivery evidence are appended after candidate
lock. This is a Stage-0 handoff, not closure of the full requested project.
