## Current execution update — 2026-10-07 evening

The corrected data and chronological JSON configuration are ready; the first
actual training pilot completed:55epochs,47.421seconds,7,247forecasts, bestepoch35. New checkpoint recovery is pending. This is research execution, not production
qualification. The original pilot launch refused a missing deterministic CUDA
environment setting before fitting; its immutable refusal is retained. The
replacement uses CUBLAS_WORKSPACE_CONFIG=:4096:8 with identical model/data
settings and a distinct output identity. The attempt ledger conservatively
charges both launches against216.

The owned-source reconciliation now covers838,326 selected dates:111,011
admitted,632,255 missing-action-inventory quarantine,89,729 non-session expiry,
2,442 action-span,1,870 target-reaching2026,1,006 missing-reference and13
incomplete-path exclusions. Residual:zero. This includes4,689 additional
pre2016 AMZN dates; usable AMZN raw history starts1997-05-15. Other owned
history was inventoried, including older MSFT, which cannot be admitted without
its missing split inventory. The admitted85-symbol retrospective cohort is
not the intended393-symbol universe and is not a point-in-time membership proof.

ADR0251 uses existing relative-volume configuration; ADR0252 fixes one
price-endpoint exclusion.104 focused tests and two independent prerequisite
reviews closed those changes. ADR0253 freezes three eight-month2021-2022
development windows, three four-month2023 confirmation windows and nine existing
2024-2025 evaluations. Retraining starts fresh weights with all eligible settled
earlier history, including COVID. Forty mature monitoring origins remain
separate from scored outcomes. Development outcomes settling during2023 are
purged before tuning; the analogous confirmation/evaluation seam is frozen.
No2026 observations or scores are permitted.

Execution candidate4f9e1084 closed independent correctness and integration
lenses with zeroCritical/Major/Minor/Nit. The former checked75 no-fit cells;
the latter ran13 focused checks including the460-file manifest and independently
tested training-only imputation. The exact capacity grid retains540 no-fit
cells acrossfive feature bundles. Only compactPatchTST32 is feasible throughout
allthree development folds. Other widths/families remain explicit refusals;
a smaller head or different CNN fusion is not silently substituted.

The incremental second copy contains2,055 files (1,347,348,480bytes) and restored
the entire111,011-row,175-column panel exactly in a fresh process with original
paths denied. Its SHA256 is830c68f00c38a4434058665e53efa819fc351414085c427ef2aff7691c29e6fa.
It reuses the previously recovered environment. This is same-disk recovery,
not off-device disaster protection; preserve originals. New fit checkpoints
will receive their own verified second copy.

Private evidence lives under
/home/russell/data/index_options/production-development-audit-20261007/.
The running JSON is production-feature-selection-v2.json; original tracked
JSON remains the retained first-launch identity. The current model inputs are
the onboarded production-model-inputs-20261007-v2 snapshot. Review outputs,
candidate lock, attempt ledger and conformance receipt are retained there.
Earlier sections below are historical evidence; this update supersedes their
pending-source/COVID-tuning descriptions. The final evaluation memo will follow
the completed bounded stages or their explicit statistical stopping gate.

---

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
Final implementation lenses later closed as recorded below.

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

At the initial Stage-0 close the backup location was unresolved. The owner
then delegated its choice near the existing data; the verified second copy
below supersedes that pending preference. Original receipts retain their
historical status. WSL's virtual free space is not reserved physical capacity;
this same-disk backup does not establish disaster recovery or permit cleanup.

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
negative mass, grid order, negative radius, nonmonotone band data,
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
7,172-date reconciliation and historical universe declaration, complete linked-payload
recovery, and freeze protected input conversion and actual
fold/calibration roles. Use the reviewed bounded repairs; resolve the remaining basis and
input-boundary gaps through the existing seams before any real panel or fit. Do not ask again for
the already authorized development scope. Re-run no historical model job to
manufacture missing provenance.

## Reproducibility and handoff

Diagnostic receipts and scripts are in `production-development-audit-20261007`;
retention-receipt.json, environment-inventory.json and source-80a194d5.tar
are in the retained directory. The public manifest records hashes without raw records, forecasts,
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


## Independent review lock and closing memo

Candidate fd8dffb892e41d574a03a35400ed90ac98352ede was reviewed against
aa44819c686fc2734f1478b62e1184960f44b28e by two fresh, sequential independent
GPT-6 reviewers: /root/stage0_correctness_r1 and /root/stage0_integration_r1.
Each completed its bounded lens with Critical 0, Major 0, Minor 1, Nit 0.
The one shared Minor, C-R1-01, is closed editorially: the original nine U1
assertions checked nonmonotone bands, not a separate crossed-band case.
The correctness reviewer separately executed a crossed-band case with lower
[1/2,4/5,1] and upper [2/5,9/10,1]; the diagnostic correctly refused it.
No code, tests or behavioral contract changed for this correction.

The second reviewer independently ran 151 focused integration tests
(132 plus 19); all passed, with no model-fit cases. Both reviewers verified all
24 original private evidence hashes. The first also executed additional
date-parser probes and checked aggregate/fold/recovery identities.
Their full actual report content is retained as plain text, with Markdown
styling normalized, in stage0-correctness-r1.txt and stage0-integration-r1.txt.
Their original transcripts remain under the reviewer IDs. The parent's durable
report copies were written after reviewer-side sandbox write failures; no failed
command is counted as executed verification.

The lock file records all 23 candidate blobs and ten dependencies. Before this
editorial/evidence appendix the author verified every recorded SHA256 unchanged.
Implementation/test blobs and ADR contract/matrix semantics remain unchanged
after this appendix. The aggregate manifest carries the lock/report hashes and
the Minor disposition. Historical source-family Major findings remain open at
whole-project level; scoped software closure does not approve a real panel or fit.

Additional metadata-only evidence: 121 source registrations across the 88
discovered stores, with no parse errors. Four Yahoo registrations include two
with a completeness declaration and two without it; they do not establish an
independent matching vintage. These counts include duplicate/inactive/fixture
registrations and are not a complete eligible-source census. Reproducing scripts
and registered-source-metadata.json are retained privately and hashed.

**Delivery conclusion:** the bounded repairs and audit/recovery/format packet
are complete for reviewed integration. The requested forecasting development
cycle is not complete. There is no new champion, reserve, calibration radius
or production qualification. All 216 fit attempts remain. Required next work
is the older-history protected conversion, exact exclusion ledger (including
the VALE discrepancy), matching action/price/volume and availability evidence,
historical universe declaration, explicit bounds for every active input,
actual immutable folds and evidence roles, and full linked-payload recovery.
No historical checkout, trial, checkpoint or source payload may be cleaned up.
Only this task's safely contained Git branch may be deleted after remote merge
verification; its isolated checkout and all private evidence remain preserved.

## Second-copy follow-up: verified local backup

The owner delegated the destination choice. Stored the retained bundle and
existing audit evidence under
`/home/russell/data/backups/index_options/production-development-20261007/`,
beside the existing data stores. The WSL virtual disk and C: share the physical
disk; this protects a separate copy from accidental directory loss, not disk
failure. Physical host free space was checked before writing (about 58 GiB),
rather than relying on WSL's approximately 685 GiB virtual availability.

`bundle.tar` is 16,049,244,160 bytes, SHA256
`4e732c80253aebc393e9f79d9074f5a1b3eb1521a8aa4b55449b54d4fc9110be`.
Every archived regular file was independently hashed against its primary copy:
690,763 files, 14,196,381,172 logical bytes.
All 4 symbolic-link targets matched.
The existing historical/dependency/source inventories additionally matched
688,570 entries. The complete per-member inventory,
archive verification receipt and operation script are private; their identities
are in the aggregate report. The copy was opaque: it did not inspect protected
observations or run a model fit.

Restored source, runtime, dependencies, panel and the selected checkpoint/score
files from this archive into its new `recovery/` directory. A fresh
`/usr/bin/python3 -B -S` process reproduced the same seven AAPL forecasts
and ordinary/tail CRPS at `rtol=1e-6, atol=1e-7`. All configured dependency
versions matched. Unknown head, missing weights and modified weights refused.
The recovery audit hook additionally denied opens into both primary retained
and primary audit roots, alongside the original worktrees/shared environment.
This is a functional Python file-open check, not an OS security boundary.

This backup covers the retained advanced study, cached panel, frozen source,
installed packages and existing audit evidence. Other historical studies and
owned onboarding source stores are outside this archive. System Python,
OS libraries and drivers remain external dependencies. One historical checkpoint
was restored; complete cross-study recovery, full linked source restoration,
optimizer continuation and production qualification are not claimed. All
originals, the archive and the recovery extraction remain; no cleanup occurred.

For pickup, set `DSKIT_BACKUP_ROOT` to the private directory above. First
compare `sha256sum "$DSKIT_BACKUP_ROOT/bundle.tar"` with the recorded hash and
verify the member/operation/receipt identities in the aggregate report.
`backup-and-verify.py` records the exact tar, per-member comparison and
selective extraction procedure; it deliberately refuses an existing archive
and must not be rerun against this completed destination.
`restore-second-copy.py` records the exact inference/negative checks and
restored paths. To repeat them, preserve that script and its receipt, create a
copy with a distinct receipt filename, then run it using
`/usr/bin/python3 -B -S <copied-script>`. For restoration to a different
directory, change only the restore/output locations in the copied scripts,
extract into an empty directory and retain the primary-path denial checks.
Never run the frozen training configuration to test recovery.

Verified 28 reviewed file/dependency identities; ADR text differs from the
original review only in the two status lines already published at c6f31962,
and is unchanged by this follow-up. All 30 pre-existing private evidence
hashes still match. This is an evidence-only appendix, preserving the two
clean software reviews; no code, tests, configurations or behavioral contracts
changed. Full-suite and fit counts remain zero for this follow-up. The broader
H1/C1/S1 prerequisites and complete R1 package recovery remain open; all 216
fit attempts remain. No new champion/reserve or uncertainty radius exists.


## Continuing prerequisites: bounded JSON projection packet

Base 38d2fba5; the owner requested continuation without repeating authorization.
Independent /root/prerequisite_path_review (GPT-6) clarified that the fixed
393-name retrospective development cohort is permissible when explicitly
disclosed; a historically known universe is required for later independent
qualification. S1 requires internally consistent rebasing plus owned action
evidence, not necessarily two independently captured vendor vintages. Research
publication clocks can be explicit conservative assumptions, never observed
historical timestamps. R1 concerns the candidate dependency closure, and U1
allows an uncalibrated radius with abstention. These distinctions do not waive
protected reads, split/volume accounting or paired coverage before fitting.

The review found three remaining Major families before fits: unprotected JSON
auxiliary reads, absolute log-volume rebasing, and incomplete source coverage.
The starting base bundle includes volume inputs; its accounting must be fixed
before the full authorized five-bundle comparison, not silently dropped.
No new fit or source-policy change is made by this implementation packet.

ADR-0250 adds one reusable JsonSQLProjection helper in the existing localtables
module. Trusted JSON recipes supply SELECT/check queries and scalar bindings;
SQLite parses opaque bytes internally, and Python receives projected fields.
Strict JSON/duplicate-member checks, read-only SQL authorization, reserved
document binding, exact check-result shape and payload-free refusals apply.
The helper itself does not recognize time policy: source-specific recipes need
separate sentinel/schema validation before real conversion and must publish
derived inputs through existing onboarding. Legacy source paths are unchanged.

Phase-0 reviewer /root/prerequisite_path_review (GPT-6): Critical 0, Major 0,
Minor 2, Nit 0; both clarifications adopted (reserved document binding and
one-row/one-column SQLite integer 1 checks). RED on unchanged code reproduced
a forbidden synthetic payload entering Python's JSON decoder before legacy
post-read filtering. The new helper passed 34 focused projection cases; full
localtables compatibility plus onboarding purity total 71 passed. Ruff passed.
An initial GREEN attempt caught six check-error-message mismatches because
AssetError derives from ValueError; preserving AssetError fixed the family.
No real raw archive conversion or training is counted here. Two fresh final
lenses remain required before this helper is declared ready.


**Stopped candidate and correction:** Final correctness review of 59d35c6 proved
SQLite temporary-file spill during its duplicate-key GROUP BY, despite the
in-memory database. Only synthetic data was used. The candidate was stopped.
Independent Phase-0 correction review by /root/stage0_execution_checkpoint
required checking compile-time TEMP_STORE mode as well as setting/reading
temp_store=MEMORY. Both are now mandatory before schema or source SQL; absent,
forced-file or unknown evidence refuses. Caller PRAGMAs remain denied.
The 80,000-object descriptor-observation regression failed on 59d35c6 and then
passed over duplicate/check/projection sort families after correction.
Focused compatibility/projection/purity checks: 77 passed; Ruff passed.
Two complete independent final reviews of the corrected candidate are pending.

**Exact historical coverage now reconciled:** Independent
/root/coverage_reconciliation_review verified all 833,637 source dates and all
455,149 cached forecast identities with zero residual. The 7,172 omissions
are 2,425 action-path and 4,747 missing-reference exclusions. VALE's difference
is 11 pre-2026 origins whose calendar-derived targets reach January 2-29,
2026, within the older January 30 holdout boundary. The current January 1
boundary excludes them before action accounting. No protected outcomes were
inspected. The retained h1-exact-coverage-20261007-r2 receipt and per-date ledger
pin this reproduction; older-source admission and economic correctness remain
separate from historical coverage arithmetic.


## Bounded helper review closed; development continues

Candidate d76dcf1c485842613180ff73c8077401a716c786 closes ADR-0250 with two independent final lenses: /root/coverage_reconciliation_review (correctness/authority) and /root/stage0_execution_checkpoint (tests/integration), both GPT-6, each zero Critical/Major/Minor/Nit. The first independently passed 40 focused cases plus 40 additional probes; the second passed 110 focused cases plus 8 SQL-denial and 11 connection-closure probes. Author checks remain 77 focused tests and Ruff. Complete actual reports are in the private audit directory; the aggregate report pins their SHA256 and reviewed code/test/contract/document identities, rechecked unchanged before this evidence-only append.

Stopped candidate 59d35c6 remains historical failure evidence: /root/stage0_correctness_r1 proved one Major temporary-file-spill defect, corrected in d76dcf1c. Its actual report was delivered in that reviewer conversation; a failed file-write attempt did not create the proposed report file. The correction and fresh reviews are retained locally. No fit was started.

The integration reviewer separately demonstrated one Major in private Yahoo audit recipe v1: original JSON booleans lost type identity and duplicate action timestamps could collapse before validation. This diagnostic recipe defect is not evidence of corrupt owned records or a helper defect. No real audit used v1. It remains preserved; v2 has 17 synthetic negative checks and awaits independent source-policy approval. Full source admission, volume semantics and folds remain open.

Next authorized work: bounded older-history and auxiliary projections, actual split/vintage checks, revised invariant volume configuration, frozen conservative research clocks and purged folds, candidate dependency recovery, then counted pilots/Stages 1-4. All 216 fit attempts remain. No production qualification, calibrated radius, MIO or strategy result is claimed.
