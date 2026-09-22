# Index-options child: official scaffold proposal

Status: **S0 implementation approved by Russell; build in progress.** Revision S0-v3,
2026-09-22. Owner: Russell. Proposed child/package: `index_options`.
Base and inventoried dependencies: DSKIT
`4ca0d4e47f41cfcdded9679707bdec9a47b07806`.

## Decision requested

Approve **S0: an offline, synthetic-only index-options scaffold** and the exact
file manifest below. This creates a real child using DSKIT's existing ingestion,
observation, pipeline, artifact, and journal interfaces. It does not create a
trading bot or claim an edge. The deliverable is a reproducible demonstration
with independently checked contract identity, quote quality, and expiry payoffs.

The [standalone project explanation](index_options_volatility.md) supplies the
financial tutorial and longer research agenda. This document is the bounded
implementation contract; [ADR-0167](../architecture/decision-log.md#adr-0167--proposed-offline-index-options-child-scaffold)
records the architectural decision. Neither document substitutes for approval.

The intended later research is weekly selection of intact, defined-risk,
same-expiry iron condors, initially 30–60 calendar days to expiry, with daily
risk monitoring and a hold-to-settlement experiment. ML would forecast realized
variance and physical return distributions; a mixed-integer optimizer would
choose integer spread positions subject to risk and capital limits. That is a
separate experiment after data feasibility. Learning value is plausible; net
profitability is unproven.

## 1. Scope, authority, and stopping point

S0 includes three typed observation wrappers, one index-condor diagnostic,
synthetic fixtures, JSON configurations, child documentation, and focused tests.
No change to shared runtime behavior is proposed.

The owner approves scope, future data spending, experiment execution, and any
deployment. The implementer may create only the approved files, run synthetic
tests in temporary directories, and document evidence. Reviewers assess design
and code; they cannot authorize spending, promote an owner Path, or certify
trading readiness.

Explicit non-goals:

- No paid/free vendor acquisition job, credentials, broker connection, real
  historical replay, training, HPO, lockbox access, paper trading, or live orders.
- No strategy selection, dynamic hedging, early exits, rolling, 0DTE, naked
  options, AM-settled positions, or mixing products in one spread.
- No custom storage engine, CSV/Parquet reader, network client, scheduler,
  training loop, solver plumbing, pricing library, or production loop.
- No modifications to `pmquant`, `intraday_equities`, root owner Path,
  existing ledgers, or active backtester work.

Only synthetic demonstration data is accepted by the S0 child entry points.
A real-data adapter requires S1 approval, not a flag that silently removes this
restriction. Output is always labelled `synthetic_ex_post_diagnostic` and
`decision_eligible=false`. These labels are honest provenance, not an
authorization system. No executor or serving configuration is supplied.

## 2. Inventory and reuse decisions

Inventory used package READMEs plus implementations, not registry contents.
Names below are existing seams at the base commit, not promises of a new API.

**Acquisition.** `dskit/onboarding/libs/localfiles.py:LocalFilesConnector`
already reads CSV/JSONL; S0 uses JSONL so numeric typing is explicit.
Its required config is `path`, `effective_field`; optional knobs are
`forecast_streams`, `encoding`. No child connector is needed.
`LocalTablesConnector` already supports Parquet/JSONL for a future larger
corpus; it is not a CSV parser. `RestApiConnector` is a candidate for a
future compatible HTTP source, not a verified Theta/Cboe integration.

**Observations.** Subclass
`dskit/pipeline/libs/observations.py:ObservationRows` and implement its
`project(records)` domain hook. Reuse its memoized scan and fingerprint:
do not open snapshot files or implement deduplication in the child.
Its actual `_PARAMS` are `root, source, stream, key_fields, ts_field,
ts_unit, ts_out, shared_fields, since_ms, as_of_acquisition_ms`.
The three child subclasses fix stream, revision keys, and timestamp semantics;
remove those configurable names using the existing narrowing pattern and
override their accessors. Retain only `root, source, as_of_acquisition_ms`;
no redundant defaults. Override `serving_effect` to the framework's
`forbidden` result and test the public serving analysis. Domain validation
belongs in one contract owner shared by direct and Node entry points.

**Pipeline and storage.** Use `Node`, `PipelineDocument`, `run_document`,
the existing onboarding snapshot store and validation suites, and
`JsonArtifact` for the diagnostic. Public `run_document` receives an
in-memory document; the CLI receives a file. No private driver call.
Existing `AssetModelConfig`/registry are available but S0 does not publish an
asset: adding an unused asset model would imply an unimplemented workflow.
The generic `event-grid` is arithmetic time, not an exchange calendar.

**Later modeling, not S0.** `ArrayFeatures`/`ArrayMap` already lift record
fields into arrays; their domain hook is `apply(arrays, params)`.
`SklearnFit` owns estimator construction and persistence; its knobs are
`estimator, estimator_params, features, label, predict_method, seed`, plus
the inherited train/load contract. Ridge and gradient boosting can be selected
by dotted estimator path. The child must not implement another fit/save loop.
The pack fits exactly the rows supplied; it does not make a research split
causal automatically.

**Later optimization, not S0.** `PyomoSolve` exposes `build_model` and
`extract`, with `solver, solver_options` knobs.
`ScenarioUtilitySolve` additionally has `instruments`, `payoffs`, and
`domain_constraints` hooks, plus utility, scenario, CVaR, cardinality and
minimum-ticket parameters. Its existing long-only integer/cash formulation
is not assumed to represent credit spreads, broker margin, or a common-horizon
options book. An algebra/units audit must precede reuse. Any missing reusable
mechanism goes into core or a library pack under its own approved design;
only option-specific constraints remain in this child.

**Journal and isolation.** Reuse the skeleton's root-independent test bootstrap,
child packaging, and journal layout. Initialize empty decisioning files through
`python -m dskit.journal init --root .`; never hand-edit generated files.
An empty owner Path is not permission to promote an action.
The existing root child test enumerates all children in one test; invoke its
`run_child_suite` helper specifically for this child for bounded integration
verification, rather than running unrelated children's suites.

## 3. Domain contract and exact interfaces

### Instruments and money

S0 supports a synthetic PM-settled, European-exercise, cash-settled index
product. European exercise means exercise at expiry rather than early
exercise; cash settlement transfers intrinsic value rather than shares.
SPXW/XSP are future product candidates, not interchangeable symbols.
XSP uses one-tenth of the SPX index level with a USD 100 multiplier; SPX/SPXW
also quote with a 100 multiplier. Product scale and settlement conventions
must come from product-specific reference data, never a ticker-prefix guess.
See [Cboe XSP specifications](https://cdn.cboe.com/resources/xsp/XSP_Options_Fact_Sheet.pdf)
and [SPX specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications).

A `CashIndexContract` validates immutable domain metadata. A
`DefinedRiskCondor` owns exactly four legs with ordered strikes
`K_long_put < K_short_put < K_short_call < K_long_call`, quantities
`(+1, -1, -1, +1)`, and equal positive integer spread count. All legs must
share underlying identity, expiry, settlement identifier/style, currency,
multiplier, and reference-data version. No inference of missing terms.

Canonical prices/strikes/index levels/fees are finite base-10 strings parsed
as Decimal by the domain owner. The multiplier is a strictly positive integer;
zero, negatives and booleans refuse through every domain/Node/API facade.
Quantities and sizes are integers with booleans rejected; spread count is
strictly positive and quoted sizes must cover every leg. No binary-float
arithmetic or implicit cent rounding.
S0 reports exact decimal-string USD amounts; real settlement rounding rules
remain a vendor/product contract for S1. Currency is explicitly USD.

### Three fixture streams

Every record carries `schema_version, corpus_id, row_version, provenance`
(`synthetic` in S0), `effective_at` (timezone-qualified UTC ISO), and
`known_at, known_at_basis`. `known_at` is nullable; its basis is explicitly
`synthetic`, `observed`, `assumed`, or `unknown`. Unknown requires null;
other bases require a timezone-qualified instant. S0 requires the synthetic
basis. An assumed timestamp is never described as observed evidence.

- `contracts`: contract ID, reference version, underlying ID, product,
  right, strike, multiplier, currency, expiry, last-trade instant,
  exercise/settlement style, and settlement ID. Key:
  `(corpus_id, contract_id, row_version)`.
- `quotes`: contract ID and reference version, quote instant, bid, ask,
  bid/ask size, and explicit quote-condition validity. Key:
  `(corpus_id, contract_id, effective_at, row_version)`.
- `settlements`: settlement ID, underlying ID, expiry, settlement style,
  official-value instant, and value. Key:
  `(corpus_id, settlement_id, expiry, row_version)`.
  These are synthetic analogues of official settlement observations,
  not ordinary closing prices relabelled as settlement.

`effective_at` is the reference effective time, quote time, or settlement
observation time, respectively. Quotes' quote instant must equal
`effective_at`; the settlement observation instant must equal it too.
The child stamps a distinct `observation_ms` using the parent timestamp
accessors, rejecting collisions. Preserve all versioned rows; S0 selects exact
versions by declared IDs in the diagnostic configuration and rejects missing
or ambiguous matches. It does not implement a generic as-of join.

A shipped fixture corpus has unique keys, pinned by tests; mutated fixtures
become a different corpus/source. This is not a claim that the observation
reader rejects every duplicate in acquisition history. It deliberately adopts
the parent's winning-row semantics: latest eligible acquisition wins per key;
identical at-least-once repeats at the winning instant are permitted;
conflicting data tied at the winning acquisition instant refuse. Projection
validates every winning row it receives, not discarded history. Direct
diagnostic inputs must provide one unambiguous row for every selected reference;
the child does not reimplement the parent's history-deduplication algorithm.

The onboarding suite is separate structural evidence. Its `block` result can
prevent certification in workflows that certify, but S0 performs no
certification and observation reads do not consume this result. Skipping or
failing that suite is not represented as a runtime read prohibition. Every
child facade instead enforces its own declared domain rules on the rows it
receives; the public demo test separately requires the suite to report `pass`.
Invalid discarded raw history can therefore appear in suite evidence without
making otherwise valid winning rows unreadable. No extra gating mechanism is
silently introduced.

Each demonstration fixture ingestion uses a fresh temporary onboarding root
and source state. The local connectors' strict
`effective_date > cursor` resume rule is **not** a solution for backdated
revisions or new equal-timestamp rows in a later pull.

Three clocks must remain distinct: market event time, documented availability
(`known_at`), and DSKIT `acquired_at` (actual ingestion commit time).
The parent's `as_of_acquisition_ms` filters the last, not the first two.
A 2026 backfill does not prove a researcher knew its rows in 2018. S0 makes
no point-in-time tradability claim; S1 must specify availability-before-decision,
version selection, lag, and execution-time rules before any feature/backtest
pipeline. Revisions must not be collapsed before that availability selection.

### Diagnostic, not a strategy

`CondorPayoffDiagnostic(Node)` has role `transform`, accepts the three validated
record streams, and returns a `report` output wrapped in `JsonArtifact(value)`.
`JsonArtifact` is an output wrapper persisted by the driver, not another Node.
Parameters declare the corpus, the four exact contract/quote version references,
the settlement version reference, positive spread count, and a nonnegative
all-in fee amount in USD for this toy outcome. Unknown parameters refuse.
All input streams must belong to the declared synthetic corpus.
The four quote instants must match, sizes must cover the declared leg quantity,
and quotes must be nonnegative, uncrossed, and condition-valid.
Require `quote_at <= last_trade_at <= settlement_observation_at` for every leg.
The selected settlement must match the declared settlement ID, expiry and
underlying; the reference metadata must declare the PM settlement observation
instant used by the synthetic example, and the outcome must match it. This is
an explicit fixture convention, not an inferred exchange calendar.
Reject unsupported terms, missing versions, mixed settlement, inverted strikes,
and a nonpositive credit or credit at least the narrower wing width.

Entry credit per spread, in index points, is:
short-put bid + short-call bid - long-put ask - long-call ask.
Net expiry P&L in USD is the multiplier times spread count times
(entry credit + long intrinsic values - short intrinsic values), minus fees.
This is a hypothetical cashflow calculation using conservative quote sides,
not evidence that a four-leg package filled. Unequal wing widths are allowed;
worst terminal loss before fees is
`count * multiplier * (max(put_width, call_width) - credit)`.

Example: strikes 480/485/515/520, quotes giving credits 1.60 and 1.00,
multiplier 100, one spread, fees USD 8.00. Settlement at 500 gives
USD 252.00; settlement at 470 or 530 gives USD -248.00.
The diagnostic reports signed cashflows, gross/net P&L, quoted credit, maximum
terminal loss, identities, and synthetic/non-decision eligibility. It does
not report return on margin, Sharpe, alpha, expected profit, or live safety.

Settlement data is an ex-post outcome input and is deliberately visible to this
diagnostic. There is no forecast/selection node or execution facade to which
it can leak in S0. The diagnostic and observation subclasses are forbidden
for serving; `JsonArtifact` writes only via the existing run machinery.

## 4. Exact file-by-file implementation manifest

All paths below are relative to `children/index_options/`. No copying the
skeleton wholesale; intentionally omit its sample connector, asset publication
config, executor, accounting, approval, coordination, and serve examples.

Package and configuration:

- `pyproject.toml` — independent child metadata; dependency `dskit`,
  Python >=3.11; no heavy library dependencies for S0.
- `AGENTS.md` — child scope, synthetic-only boundary, tier separation.
- `CLAUDE.md` — the same child instructions, kept aligned.
- `README.md` — purpose, exact WSL install/demo/test commands, limits.
- `.gitignore` — local environments, acquired data, runs, credentials excluded.
- `journal.json` — generated journal marker.
- `index_options/__init__.py` — small public surface; no import-time I/O.
- `index_options/contracts.py` — `CashIndexContract` and
  `DefinedRiskCondor`; single owner of identity/leg/payoff rules.
- `index_options/observations.py` — `ContractRows`, `QuoteRows`,
  `SettlementRows` extending `ObservationRows`.
- `index_options/nodes.py` — `CondorPayoffDiagnostic`; no fit or solver code.
- `configs/source-fixture.json` — LocalFilesConnector over synthetic JSONL.
- `configs/suite-fixture.json` — separate shared structural validation evidence;
  neither a substitute for domain validation nor a gate consumed by the reader.
- `configs/run-fixture.json` — observation-to-diagnostic DAG with the diagnostic's
  report explicitly persisted through `JsonArtifact`.
- `fixtures/contracts.jsonl` — original, redistributable synthetic metadata.
- `fixtures/quotes.jsonl` — matching synthetic four-leg quote snapshot.
- `fixtures/settlements.jsonl` — synthetic settlement outcomes.

Documentation and journal infrastructure:

- `docs/decisioning/actions.csv` — CLI-initialized empty action ledger.
- `docs/decisioning/path.csv` — CLI-initialized empty owner Path; no promotion.
- `docs/decisioning/README.md` — CLI-generated journal view.
- `docs/explanations/README.md` — concise standalone financial glossary,
  payoff example, and distinction between demo and research.
- `docs/plans/README.md` — S0 boundary and S1/S2 gates.
- `docs/memos/README.md` — where execution evidence belongs.
- `docs/research/README.md` — source diligence checklist and journal CLI
  instructions; future research entries are CLI-created, never hand-edited.
- `docs/research/.gitkeep` — empty file created by the existing journal initializer.

Tests:

- `tests/conftest.py` — root-independent bootstrap and temporary stores.
- `tests/test_contracts.py` — metadata, numeric and exact payoff cases.
- `tests/test_observations.py` — narrowed knobs, version identity,
  provenance, timestamp, scan/fingerprint and freshness-boundary cases.
- `tests/test_nodes.py` — direct diagnostic validation and output values.
- `tests/test_configs.py` — config parsing, import-path resolution,
  default-deny, declared param/accessor agreement.
- `tests/test_integration.py` — finite offline ingest/validate/run/artifact
  round trip, serving refusal, journal isolation, foreign-cwd behavior.

Additional existing file change after approval: `children/README.md`, add the
child and explicitly label it offline/synthetic. No root dependency changes,
new registry kinds, or core/library edits are expected. If inventory or tests
show a generic gap, stop and propose that gap rather than filling it child-side.

This manifest contains **30 new child files**, plus the one existing-file edit.
This proposal, its review record, and the proposed ADR are pre-implementation
design artifacts, not part of the child package.

## 5. Data-source investigation and recommendation

Public documentation checked 2026-09-22; no provider account queried, sample
downloaded, license accepted, or price quote obtained. These are source leads,
not a claim that we already have a usable historical corpus.

**Cboe DataShop — first diligence candidate for slower research.**
Option EOD Summary offers 15:45 ET and EOD NBBO/size snapshots; optional
calculations include IV/Greeks. Index underlying quotes are not included by
default. The product page says history starts January 2012, while the FAQ says
January 2010: obtain symbol/date-specific confirmation instead of selecting the
more favorable range. Daily files normally arrive the following day and can
arrive after the next market opens. On half days, the fields labelled 1545 use
12:45 ET. A same-snapshot signal and fill would therefore be an invalid default
for this delivered-file workflow.
Sources: [product](https://datashop.cboe.com/option-eod-summary),
[FAQ](https://datashop.cboe.com/faqs).

**Theta Data — compare for a targeted quote-history workflow.**
Its v3 historical quote endpoint exposes OPRA NBBO and interval snapshots
through a running Theta Terminal. This creates a runtime dependency and
timestamp/schema adapter work; it is not a bare public cloud REST endpoint.
Verify SPXW and XSP series coverage, expired contracts, exchange timezone,
entitlements, corrections, request limits, and endpoint-specific availability
on an authorized sample. Use the existing generic HTTP/file seams if compatible;
an SDK/transport gap belongs upstream, not in the child.
Source: [v3 quote endpoint](https://docs.thetadata.us/operations/option_history_quote.html).

**Databento — candidate if timestamp fidelity justifies the data footprint.**
Its OPRA service includes SPX. A May 2025 announcement extends selected schemas,
including minute NBBO snapshots and instrument definitions, to April 1, 2013;
it distinguishes tick/second schemas beginning in March 2023. These are
schema-specific provider claims, not verified symbol-level completeness.
Check current catalog, filtered request size, actual cost, XSP coverage,
reference definitions, licensing, and a separate index/settlement source.
Sources: [service announcement](https://databento.com/blog/opra-data),
[history/schema update](https://databento.com/blog/opra-improvements-coming-soon).

**IBKR — possible later broker/current-data source, not the proposed archive.**
IBKR documents unavailable expired-option history. A broker connection alone
does not solve the research-history requirement.
Source: [IBKR market-data lesson and API references](https://www.interactivebrokers.com/campus/trading-lessons/requesting-market-data/).

Recommendation is an investigation order, not a purchase: compare one identical,
small product/date request from Cboe and Theta first; assess Databento if needed.
Do not download a full OPRA universe. Choose XSP versus PM-settled SPXW only
after examining executable quotes, fees, capital requirements, and coverage.
A smaller multiplier-scaled instrument need not have better liquidity.

S1 must produce a source acceptance record with sample checksums; exact symbol,
root, dates and missing-session counts; historical chain completeness; contract
terms; documented quote/publication clocks and revisions; licensed index
levels and official settlement values; rates/dividend inputs if recomputing IV;
bid/ask sizes and costs; storage estimates; and terms for local retention,
two-machine use, and publishing derived research. Unknowns stay explicit.
Synthetic fixtures, not restricted market data, go into git. No vendor has yet
passed this gate.

## 6. Invariants and verification matrix

Actors/inputs: owner-approved JSON is configuration but still validated;
fixture files and records are untrusted data. Import paths resolve trusted
repo code. Arbitrary hostile Python controlling the interpreter is not a
security boundary promised by this project.

For each row, test the positive case and the named negative family. When a
child domain check or parent read refuses, there must be no completed success
artifact. A separate onboarding-suite `block` is evidence, not that runtime
refusal. Framework failure metadata may still exist; the child does not replace
the framework's crash protocol.

- **I1 Authority:** package import, CLI/config resolution, and direct calls
  perform no network/trading activity. Synthetic fixture succeeds; real/unknown
  provenance, attempts to serve any child node, and unsupported config knobs
  refuse. No executor exists. Invalid input cannot reach a child side effect.
- **I2 Instrument identity:** direct domain construction, all three projection
  hooks and diagnostic agree. Valid four-leg PM cash European fixture passes;
  wrong right/order/count, bool quantities, zero/negative/bool multipliers,
  AM/American/physical terms, mixed
  expiry/underlying/currency/multiplier/version, and absent metadata refuse.
- **I3 Time and revisions:** parent accessors, projection, and diagnostic keep
  exact version references and the three clocks distinct. Test valid offset
  instants and parent's winning-row semantics, including identical-repeat
  acceptance, conflicting winning-tie refusal, and later-acquisition replacement.
  The shipped fixtures' keys must be unique. Naive timestamps, output-field
  collisions, missing/ambiguous selected versions, unknown provenance, quotes
  after last trade, and mismatched settlement identity/instant refuse.
  A later read-vintage
  can include a later ingestion; it never changes a row's known-at claim.
  Fresh-root fixture ingestion is required; resumable revision capture is not
  claimed.
- **I4 Quotes and money:** exact positive and negative payoffs at every strike,
  between strikes and in both tails; unequal widths; quantity scaling; zero
  versus positive fees. Bad decimal strings, NaN/infinity, floats, crossed
  quotes, insufficient sizes, mismatched quote times, invalid conditions and
  invalid credits refuse. Pin strictly positive multipliers through domain
  construction, contract projection, direct diagnostic and pipeline/CLI paths;
  zero/negative/bool values refuse rather than produce a signed 'maximum loss'.
  Reordering valid input rows does not change amounts.
- **I5 Dependency and identity:** direct class invocation and public
  `run_document`/CLI paths resolve the same rules; unknown and fixed-away knobs
  refuse, omitted retained knobs follow only the parent contract. Test a changed
  fixture in a newly identified corpus changes run input identity. Mutation
  after a node's first snapshot cannot change that instance's input silently.
  Test ordinary subclasses against the same public contracts; do not claim
  hostile method replacement is contained.
- **I6 Failure/isolation:** missing required stream, corrupt JSONL, parent read
  refusal and failed domain validation cannot produce a completed diagnostic.
  A suite `block` is not a read gate: test that suite evidence and winning-row
  domain validity are distinct, and that the accepted demo also has a suite
  `pass`. Two independent temporary roots do
  not share cursor/artifact/journal state; an interrupted temporary run can be
  retried only through the generic pipeline protocol or a fresh root, never an
  invented child resume mechanism. Test no writes to either existing child or
  an existing owner Path. For journal-isolation tests, explicitly enable
  recording with `DSKIT_JOURNAL_TESTS=1`, set `DSKIT_JOURNAL_ROOT` to each
  isolated temporary child root, and assert each expected action was actually
  recorded as well as that the other root/owner Paths stayed unchanged.
  A no-op under pytest is not positive isolation evidence. Expiry is evaluated
  as an outcome, not authorization;
  broker revocation/leases are irrelevant because no such authority exists.
- **I7 Integration:** own child suite passes from child root and foreign cwd;
  the root `run_child_suite(child_dir)` facade passes for this child only.
  Exact manifest and AGENTS/CLAUDE parity hold. No runtime import arrow from
  `dskit` into the child. No skipped dependency-heavy tests masquerade as
  completed S0 coverage.

Implementation exit: focused tests and the synthetic public-CLI demonstration
pass, output equals the analytic example, all failures are explained, two fresh
independent implementation lenses have no unresolved Critical/Major findings,
and reviewed code/test/contract identities are retained. No full framework or
other-child suite is required for this docs-only design or child-only slice.
S0 evidence is software correctness evidence, not trading validation.

## 7. Next stages and approvals

- **Now:** owner approved this S0 manifest and ADR in the follow-up request
  to build the child on 2026-09-22. Implementation and code review may proceed.
- **S0 build:** implement with TDD, review, and deliver the synthetic child.
- **S1 data feasibility:** separately approve a provider/sample budget and
  bounded adapter plan. Reject the project or change instrument if adequate
  data, rights, or execution realism cannot be obtained.
- **S2 empirical ML:** separately approve experiment and actual runs. Compare
  persistence/HAR-style realized-variance baselines against regularized/boosted
  models using time-ordered, overlap-purged validation, train-only transforms,
  uncertainty calibration, net costs and untouched final evaluation.
- **S3 allocation:** audit the existing Pyomo money semantics, use a common
  valuation horizon, distinguish current-book value from new cashflows, and
  compare MIO with a simple risk-budget heuristic. No automatic leverage lift.
- **S4 deployment:** new broker, settlement, reconciliation, risk-limit,
  monitoring and authority contracts; paper evidence before any live approval.
  Existing framework production/backtester work is not declared closed here.
  RL hedging/DL are optional later questions, not scaffold dependencies.

A useful resume outcome is a reproducible instrument-aware research system,
an honest negative or positive out-of-sample result, and defensible experiments.
A fabricated return target or an elaborate scaffold without data is not one.

## 8. Review and approval record

The separate [skeptic review record](index_options_scaffold_review.md) binds
the proposal/ADR hashes and retains independent review outputs and dispositions.
This is Phase 0 design review, not the immutable-code candidate/merge gate.
No implementation, empirical validation, owner approval, commit, or push is
implied by a favorable design review.
