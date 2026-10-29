---
name: mio-report
description: Produce standardized DSKit MIO reports for one or more MIO methods and date ranges, reusing compatible completed runs and executing only uncovered intervals when account continuity is supported. Infer the predictive model from prior accepted reports unless overridden.
---

# MIO reports from existing coverage and missing intervals

Accept MIO method(s) plus date range(s); resolve coverage, run legitimate gaps,
and render one standard report per independent account/method. Work in WSL2.
This skill uses existing DSKit JSON pipelines, not a new runner.

## Resolve the request

- Inputs: `mios` (one ID/config or a list), `ranges` (start/end or a list),
  optionally `predictive_model`, `cohort`/universe and explicit account settings.
  Treat a natural-language ending date as inclusive; normalize to start-inclusive,
  end-exclusive dates and record both. Use the configured trading calendar.
- MIO methods are portfolio policies; they are distinct from the predictive model
  that supplies forecasts. Resolve aliases against accepted config method IDs.
- Explicit choices win. Otherwise inherit the accepted matching report's universe,
  execution/sizing rules, fees, cash/deposit settings and predictive recipe/fold
  schedule. If different accepted cohorts remain plausible, ask one concise
  clarification; do not silently substitute limited36 for broad212 or vice versa.
- Default predictive identity comes from the prior accepted MIO config's
  `domain.predictive` and forecast/producer pins. State the inferred choice.
  "Best" means the owner's previously accepted predictive choice, not the MIO
  with highest realized profit, newest timestamp, or a fresh model competition.
  Retain each fold's producer/config/artifact hashes and use only the fold valid
  for the requested dates. An explicit model override invalidates incompatible
  coverage; never fall back to the old predictor silently.

Read current root/child AGENTS.md, re-entry notes and owner-locked Path first.
A skill invocation requesting runs authorizes those bounded runs within existing
permissions; a report-only/dry-run request does not. Skill creation or testing
itself does not authorize market runs. Preserve sealed dates, originals and TDD.
Do not refit, tune, pull providers or open a protected window implicitly.

## Find verified coverage

Use repository configs/results-pins, run roots, the shared exposure registry and
receipts. Do not decide coverage from report filenames or the registry alone.

For each requested method, record the compatible run's identity:

- method parameters, universe/cell eligibility and decision calendar;
- predictive fold/forecast payload and source/quote identities;
- fill/sizing, fee, reserve/cap, settlement and exit rules;
- initial cash, deposit clock and account lineage/starting state;
- executable economic implementation and dependency identity.

Require `RunAttestation(run_dir).attested_output(node, port, document_hash)` and
`resolve_json_artifact` for receipt-backed results. Verify the content hash,
complete flag, requested method status, recorded replay where promised, actual
`audit.calendar`, decision dates and settled coverage. A changed report renderer
alone does not invalidate an otherwise compatible saved MIO result. A legacy
all-or-none execution result cannot cover an available-size request.

Build a per-method coverage inventory with requested sessions, reusable sessions,
missing contiguous trading-session intervals, report status, source receipts and
account lineage. Count sessions with no trade when their account rows exist.
Never infer coverage from `asof`, source lookback, expiry tail padding, requested
config bounds, a completed sibling method, exit code alone or an incomplete run.

Inspect matching services/processes and their exact config identities before any
launch. Follow a matching active job; do not launch a duplicate. Partial results
are provisional, not complete coverage. Failed outputs stay intact and visible.

## Execute only legitimate gaps

- Fully covered: launch zero MIO jobs. Reuse a compatible standard report or render
  a new report from the saved result if its report is missing/stale.
- Covered subrange: preserve its original starting cash/holdings and label it as a
  view of that account. A full-run P&L is not a subrange P&L. If the current reporter
  lacks bounded views, provide the full source report with its actual coverage and
  identify the requested rows; do not claim the requested window was rendered.
- Partly covered continuous account: execute only missing intervals if an existing
  supported checkpoint restores cash, holdings, deposits, pending orders and
  settlement obligations at the boundary. Verify boundary continuity and any later
  reused suffix; an earlier gap can change later decisions and cash, so a suffix
  is reusable only when its restored state agrees. Never join independently reset
  accounts into a continuous run or silently rerun already covered dates.
- Wholly uncovered range: use the existing MIO JSON pipeline with the requested
  dates and inherited/explicit account settings; label the new account's start.
  Multiple independent ranges/accounts remain separate reports, not summed returns.
- **Current limitation:** at the ADR-0283 baseline, `WeeklyOptionPortfolioStudy`
  constructs a fresh `FundedOptionAccount` for each method, including recorded
  replay. There is no documented cross-interval state-resume input. Recheck the
  current owner before relying on this. If still unsupported, report the exact
  uncovered dates and continuity issue; ask whether the user wants a separately
  labelled fresh account or an explicitly authorized wider replay. Do not execute
  either as an assumed substitute. Reuse/report the verified portions meanwhile.

For an executable gap, use a distinct immutable JSON/output root, original data
onboarding bindings, the appropriate forecast fold and existing protocol nodes.
Refresh imported-code pins for the executing checkout in the new document;
never rewrite old configs/results to fit newer code. Infer/score missing forecasts
only through an existing approved inference workflow using the frozen predictor
and valid fold; unavailable model artifacts or an unauthorized fit are blockers,
not permission to retrain. Preserve source lookback/tail metadata while bounding
actual decisions. Run from the data output root to avoid repository journal writes.
Keep the shared exposure registry serial; use existing freeze/open/intake/study/
close, StudyPacketResults/MIOPacketCompare and StudyProtocolControl review nodes.
Never reset/create a replacement registry or repeat a completed disposition.

After completion verify attested receipt, exact config/code/source identity,
requested methods and dates, actual fills/refusals, fees, chronological cash,
holdings, reserve, expiry settlement and final profit. Save direct field extracts
and new pins. An execution failure remains a visible missing interval.

## Render the standard report

Inventory the merged ADR-0283 report contract and current example JSONs first.
The existing chain is `MioRunRows` -> `RowsToEvents` -> `EventTotalsGate` ->
`EvaluationReport`, followed by `ArtifactLinks`. Keep one method/account per
report. Map only observed decision statuses through the existing action parameter;
unknown statuses refuse. Expected totals come from the pinned result, never typed
in. Preserve the gate on P&L, final equity, contributions, fees and exact event
counts. The money tolerance is the approved $0.005, not a substitute for the stress
replay's exact zero-shock controls.

Use a fresh report-only output root and pin the report JSON, renderer commit,
source result/receipt, inferred or explicit predictive identity and coverage
inventory. Include inherited forecast/fold hashes and reuse/new/gap provenance in
a configured identity table. The pipeline replaces `run_start.data` with its
resolved fingerprint; do not rely on that field for custom metadata. No runtime
edits are needed.

Inspect actual generated HTML, Markdown and CSVs, not just tests or exit status:

- day-by-day marked cash, holdings/reserve, deposits, buys/sells, selected/executed
  quantities, fees, refusals, expiries and realized profit/loss;
- opportunities versus selections and candidate/execute refusal reasons, with
  book/leg/contract meanings and denominators clear;
- authoritative marked P&L/equity/TWR/drawdown, trade metrics, ticker x DTE,
  optimizer certificates/timeouts and the glossary;
- account-basis labels: generic event charts/statistics value open books at cost,
  while saved marked figures are authoritative. Report timestamp slots do not
  establish observed intraday execution times. An expiry closing event is not
  an early-sale policy. The totals gate verifies restatement, not a new payoff test.

Bind existing stress outputs only when their source methods/results match. Inspect
ArtifactLinks hashes and every grid cell; pending, mismatch, unverified or
unsupported stays visible. Link detailed daybooks for each executed leg if the
standard ledger is aggregate. At ADR-0283, the ledger groups selections by
attempted fill date; refused rows may carry reduced quantities. Disclose this
next to the ledger and link the exact saved orders-and-fills.csv selected_quantity
and filled_quantity fields for original demand versus fills. Do not launch stress
scenarios merely to fill links;
run them only when requested under an approved scenario contract. Preserve v4's
all-eight-exact-controls gate before48 stressed cases.

Deliver readable report links and downloadable CSVs with exact source/report pins.
If remote GitHub access is needed, publish a separately named mirror of generated
outputs and a small index linking each method and its detailed daybook. Preserve
original bytes and distinguish generated evidence from a navigation wrapper.
Wrap/merge/push only when authorized; follow repo review rules for config/code
changes and disclose incomplete coverage. Never call the whole request complete
while a requested interval or report remains missing.

## Local discovery anchors (verify, do not blindly execute)

- Repository currently available at `/home/russell/dskit-index-options-stress-adr`;
  use a suitable isolated checkout containing current accepted main. Interpreter:
  `/home/russell/dskit/.venv/bin/python`; PYTHONPATH is checkout plus
  `children/index_options`. Preserve other sessions' worktrees.
- New report examples: `children/index_options/configs/mio-report-{limited36,broad212}-available-size-20261029-v2.json`.
- Completed2019 receipts: `children/index_options/configs/mio-2019-available-size-results-pins-20261029.json`.
  Local state/data: `/home/russell/data/index_options/mio-2019-available-size-20261029`.
- Prior predictive provenance: `/home/russell/data/index_options/mio-broad-2019-20261027/upstream-model-pins.json`.
  Accepted2019 inputs use saved `selected_mlp64_s29` H1/H2 outputs and pinned
  payloads. This is a provenance example, not an eternal default or new fit claim.
- Report contract: `children/index_options/docs/plans/mio-json/study.json`;
  owners: `index_options/mio_run_events.py`, `dskit/evaluation/README.md`,
  `index_options/ledger_studies.py`, `index_options/mio_contract.py`,
  `dskit/pipeline/study_protocol.py`, `dskit/pipeline/driver.py`.

Example: `$mio-report mios=[nominal_lp,w1_003_lp] cohort=broad212 ranges=[2019-01-02..2019-12-31]`.
With the verified available-size2019 lineage this reuses252 sessions per method
and renders/reuses reports; it launches no MIO. Add `predictive_model=<pinned ID>`
to override the inferred forecast source and reassess coverage.
