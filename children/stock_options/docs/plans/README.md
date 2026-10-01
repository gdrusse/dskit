# Plans

## Approved bootstrap

ADR-0212 created this thin stock-specific child. The next bounded study trains
and selects a stock/market-only physical forecast before the option period, then
uses option data only as the mixed-integer optimizer's point-in-time option
action set and economics.

Use split-adjusted prices and scale-free features. Retain correctly adjusted
split rows and genuine shocks; exclude only predeclared data-quality or
contract-identity failures. Apply the earnings, dividend, assignment and
adjusted-deliverable gates documented in ADR-0212.

ADR-0213 separately approves offline archive preparation. First inventory the generic forecast,
walk-forward, chain and optimizer seams. Any missing reusable mechanism belongs
in `dskit` under a separate ADR and focused tests.

## Offline AMZN conversion runbook (ADR-0213)

Owner approved 2026-10-01. This builds canonical options, underlying expiry-close
labels, full option-price CDF proxies and the same prepared-panel vocabulary and
argmax outputs used by the indices. It does not train or execute a strategy.

Run in WSL2, from this child, using the shared project environment:

    cd /home/russell/dskit-torch-decision-cdf/children/stock_options
    export PYTHONPATH="$(realpath ../..)"
    export PATH="/home/russell/dskit/.venv/bin:$PATH"
    python -m dskit.onboarding init --root ./pipeline_runs/option-archive-source-v2
    python -m dskit.onboarding register-source options --catalog-source options --connector dskit.onboarding.libs.alpaca:AlpacaOptionArchiveConnector --config @configs/source-option-archives.json --activate --root ./pipeline_runs/option-archive-source-v2
    python -m dskit.onboarding acquire --source options --stream contracts --mode backfill --root ./pipeline_runs/option-archive-source-v2
    python -m dskit.onboarding acquire --source options --stream bars --mode backfill --root ./pipeline_runs/option-archive-source-v2
    python -m dskit.onboarding acquire --source options --stream snapshots --mode backfill --root ./pipeline_runs/option-archive-source-v2
    python -m dskit.onboarding register-source underlying --catalog-source underlying --connector dskit.onboarding.libs.yahoo:YahooChartArchiveConnector --config @configs/source-underlying-history.json --activate --root ./pipeline_runs/option-archive-source-v2
    python -m dskit.onboarding acquire --source underlying --stream prices --mode backfill --root ./pipeline_runs/option-archive-source-v2
    systemd-run --user --wait --pipe --collect --unit=stock-cdf-coverage \
      -p MemoryMax=6G -p MemorySwapMax=0 -p RuntimeMaxSec=1740 \
      -p WorkingDirectory="$PWD" -E PYTHONPATH="$PYTHONPATH" \
      /usr/bin/time -v /home/russell/dskit/.venv/bin/python \
      -m dskit.pipeline run configs/run-cdf-horizon-coverage.json --asof 2026-10-01

Apply the same systemd resource prefix to each import for hard per-command
caps. All are offline CPU tasks. Already acquired immutable data can be reused;
do not repeat init. For an independent reproduction, use a fresh onboarding root
in the commands and in all four ObservationRows nodes, and set outputs.run_root
to a fresh directory in a copy of the runner JSON. No custom Python runner is
needed. Changed source bytes require fresh roots and updated SHA-256 pins.
A rerun at the same identity refuses to overwrite existing evidence.

The source configs name the three supplied Alpaca files and Yahoo history.
Each path is pinned. archive_observed_at records inspection/import observation,
not a fabricated historical availability time. complete_through conservatively
stops at 2026-09-29: the supplied final daily candle is not independently verified
as complete. Split-adjusted and dividend-adjusted closes remain separate; raw
strike units are admitted only when the source lists no subsequent split.
Original archive files must remain available to repeat onboarding.
The Yahoo source additionally declares corporate_actions_complete=true after
inspection; a missing split inventory still produces unknown units and cannot
qualify prices. Snapshots permit one observation per contract/session;
conflicting same-session snapshots refuse during import.

For another ticker, change foreach.keys, the provider files/pins, completion
boundary, and asof after verifying source coverage. Generic code has no ticker
literal. A ticker and a prepared source suffice for the existing index
selector; a raw source also needs its contracts, underlying closes and explicit
clock/unit policy. Those inputs cannot be inferred from the ticker.

The coverage runner includes preparation. The alternative
configs/run-prepare-option-panel.json ends after preparation, useful when no
horizon qualifies. Both graphs share identical source, label and panel nodes.
Their carry.json contains:

- labels.labels: content-addressed JSON of every underlying expiry-close label.
- panel.options: contract metadata plus every dated trade/quote observation.
- panel.cdfs: all curve/rejection records, canonical probability/log-return
  inverse grids, configured quantiles, raw projected knots and diagnostics.
- panel.panel: prepared coverage rows, including rejected and pending cases.
- panel.summary and labels.summary: observation counts and rejection reasons.
- coverage__amzn.records, listed_counts__amzn.records, winner__amzn.records:
  the existing selector schema. Counts are distinct entry dates. Only populated
  horizons appear; absent DTEs have zero coverage in this supplied source.
- artifacts/coverage_figure__amzn/coverage.png: date-count coverage by DTE.

Artifact references have path, SHA-256, bytes and media_type. Paths are relative
to the run directory; read the referenced JSON, not a truncated ordinary node
preview. Quantiles interpolate the exported canonical inverse grid exactly.
Tail endpoints are finite-support completion, not observed distribution tails.

Initial execution: 65,372 historical bars, 23,254 metadata records, 168 current
snapshots and 7,390 prices. The prepared output contains 1,362 date/expiry/basis
rows, 1,358 settled labels and 915 eligible settled CDF proxies across 549
distinct dates (2024-03-21 through 2026-08-26). Exact-DTE argmax is 42 days:
90 eligible dates from 118 available panels; 30 days has 89 from 124.
All 168 current snapshots lack matching contract metadata and remain visible
but ineligible. Two historical non-session keys contain five option rows.
443 historical surfaces fail wing support; no threshold was relaxed to improve
these counts. The requested historical archive covers only nominal 30–45 DTE,
so this says nothing about uncollected 1–29-day options.

Trade closes are asynchronous American-option price proxies, not contemporaneous
quotes or exact risk-neutral/physical probabilities. Labels are underlying
closes, not actual settlement cashflows or share assignment. Null current-close
completion and missing terms cannot be repaired by copying historical fields.

Next: audit feature availability before feature selection or training. A fixed
42-day sample of 90 dates is small. Pooling DTEs offers 915 observations over
549 dates, but would be a separately declared modeling experiment with DTE as
an input and expiry-aware chronological purging. These observations are
correlated; 915 is not 915 independent trials. Do not silently change the
approved exact-DTE selector or loosen CDF admission.

## Candidate acceptance matrix

Implementation base eb08ed8 (approved contract ADR-0213, base c218de6d).
Allowed paths: shared onboarding readers, predictive_cdf, index proxy delegate,
four stock configs, focused tests and associated package/child documentation.
Inputs are untrusted saved provider data, pins/config are operator declarations.
No provider requests, training, execution or claim of executable prices.

Required invariants and checks: source pin drift/default-deny/duplicate refusal;
OCC versus metadata consistency and orphan retention; separate trade/quote
prices and clocks; exchange holiday labels and pending outcomes; raw-unit
split guards; coherent inverse grids including flat projections; configurable
pair/wing/projection gates; legacy index numerical fixture; schema-compatible
stock JSON graph; actual bounded run and independent runbook reproduction.
Focused checks currently 72 pass: onboarding48, CDF12, index6, stock6.
Changed-code lint has no new findings versus the base; legacy findings remain.
Two independent final code-review lenses are required before merge.


Review candidate b1f2461f received two independent reports:
amzn_correctness_v1 C0/M1/m4/N0; amzn_integration_v1 C0/M1/m6/N0.
The shared Major was unknown corporate-action inventory treated as verified
raw units. The union of minors was quantile-name collisions, calendar-edge
rejections, missing standalone quote clocks, numeric input/output validation,
snapshot identity disagreement, and provider error documents read as empty.
All seven families were corrected together with twelve initially failing
regressions. Full reports are retained under those agent IDs in this task.
Fresh final lenses must assess the corrected candidate before delivery.

Corrected execution: stock-cdf-horizon-coverage-2026-10-01-72b70805,
11.59 seconds, peak RSS 652,608 KiB, zero swaps; counts remain 915 eligible
curves and a 42-day/90-date winner. All imported inputs are freshly acquired in
option-archive-source-v2. Final relevant checks now total 87 passes: 52 archive,
20 CDF/label, six index extraction, six stock config, three QQQ JSON-gap tests.
The final reviewer/operator will revalidate the exact locked candidate.


Final independent operator /root/amzn_runbook_operator reproduced candidate
83dcba51 from this runbook using fresh imports and output roots. Evidence:
pipeline_runs/amzn-independent-final-20261001/verification.json and logs;
run outputs/stock-cdf-horizon-coverage-2026-10-01-a36da995. All four JSON artifact
hashes equal the final primary run; every one of 8,235 configured quantiles
matches interpolation of the exported inverse grid. Counts and winner match.
Execution: 9.52 seconds, peak RSS 652,380 KiB, zero swaps; hard 6-GiB/no-swap/
1,740-second limits independently confirmed. No runbook obstacles.
SHA-256 options dd700fc6a413b6b6e53c435f78474666261882a675f7afe95bdad3c2504a4f4f;
labels a8e3ffdc579c5f9ada8163932dcfbf9ae1de4a6940375792b1c2c7f33167f6f1;
CDFs 538533d73508e935a5db03a53397298c29f6f8f9b2d7faf5ed0a92ba6e71ba81;
panel 36f01b982877fb71a3917653ec0f05a5fa44fba4751221c9291c43af39553d74.

First fresh final lens /root/conversion_gaps_correctness_final: C0/M0/m1/n0,
candidate 83dcba51; 92 focused tests passed independently (52 archive,25 CDF,
six index extraction,six stock configuration,three QQQ audit). One disclosed
Minor remains in standalone OptionCDFPanel malformed-clock handling: omitted
entry_close_at raises KeyError; quote_timestamp NaT can bypass age comparisons.
Canonical archive import rejects NaT and generated labels always carry the
clock key, so neither affects this supplied workflow. Defer public standalone
input hardening; do not treat arbitrary hand-built quote records as validated.
The second independent lens must complete before main integration.


## AMZN feature-gap interface extension (owner request, 2026-10-01)

Run configs/run-amzn-feature-availability.json through the same standard runner
after archive onboarding. It embeds the same preparation and QQQ audit rules:
155 fields, 13 families, identical predicates and feature/family gap schemas.
Fixed cohort: AMZN, 42 actual calendar days, 90 eligible dates selected above.
Family contracts state desired semantics; source_contract.merged.archive
explains the data AMZN actually supplies. No feature computation or training.

    mkdir -p pipeline_runs/amzn-feature-availability
    python -m dskit.pipeline run configs/run-amzn-feature-availability.json --asof 2026-10-01

Apply the same WSL2 systemd limits/interpreter as coverage. Outputs reside in
pipeline_runs/amzn-feature-availability/: rows.jsonl, feature-gaps.jsonl,
family-gaps.jsonl, and runs/<run>/{carry,config,result}.json. After publication,
use fresh writer paths and run_root. Never silently shrink the selected cohort.
Missing prepared fields are not proof raw sources cannot supply them.

The initial execution exposed missing asof_ms: shared OptionCDFPanel now emits
quote-date midnight UTC milliseconds, matching the index grouping coordinate,
not a publication clock. A regression reproduced the failure and checks the
exact epoch. Also closed the previously disclosed standalone malformed-clock
minor with missing-key/NaT/invalid/naive timestamp regressions. A second execution
caught the source-contract literal table shape; corrected and directly tested
that node. Both failed run directories remain as local evidence.

Extension matrix: same audit predicates/schema and stock preparation; exact
90 identities retained; every missing feature/date/reason and family/date
persisted; accurate date windows; bounded standard execution; no imputation,
selection, PCA, model training or provider requests. Changed shared panel
date/clock handling, requested JSON, focused tests and associated docs/evidence
require fresh final lenses on the new immutable candidate.


Successful audit: amzn-feature-availability-2026-10-01-cfe3c76c; 22.01 seconds,
658,940 KiB peak RSS, zero swaps under hard caps. Exactly 90 unique dates
2024-03-22 through 2026-08-14. Nine CDF fields complete throughout; other
146 fields absent in this prepared panel: 13,140 feature/date gaps and
1,080 family/date gaps. These are cells, not additional observations.
Underlying-derived families remain a preparation task; historical quote/IV/OI
features cannot be filled from current snapshots.

Independent author-side verification matched every gap key/reason and date
millisecond, and the exact selected source identities. All 1,362 panel rows
equal the earlier run except added asof_ms; options/CDF/label artifact hashes
are unchanged. verification.json carries the full checks and output hashes.
The same seven audit evidence files as QQQ are retained for agent handoff.
Focused extension checks: seven stock tests and eleven CDF/panel/label tests
pass; previous 87-test run and both 83dcba51 lenses remain historical evidence.
Fresh final reviews assess this changed candidate before main integration.
