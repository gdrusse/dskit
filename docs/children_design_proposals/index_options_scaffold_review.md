# Index-options scaffold — Phase 0 skeptic review

Status: **Phase 0 design review complete; awaiting owner approval**.
Two fresh v3 lenses report zero Critical, Major, Minor or Nit findings.
No implementation authorization is implied.
Date: 2026-09-22. Scope: the proposed offline, synthetic-only `index_options`
child. This is a design record, not an implementation/merge/deployment exit.

## Identities and method

- Base/framework inventory: `4ca0d4e47f41cfcdded9679707bdec9a47b07806`.
- Proposal: [index_options_scaffold.md](index_options_scaffold.md).
- ADR: [ADR-0167](../architecture/decision-log.md#adr-0167--proposed-offline-index-options-child-scaffold).
- Prior explanatory document is context, not implementation authority:
  [index_options_volatility.md](index_options_volatility.md),
  SHA256 `e000eb806fae503bfc89cfde582697388cfdb0ade04d8b456d88258cbb7741df`.
- Docs are frozen by whole-file SHA256 during each review. No code candidate
  commit exists. Repository candidate-round commit/two-lens gates still apply
  to the future implementation. A design pass cannot be carried over to code.
- Models are inherited without override; reviewer task identities below are
  the retrievable independent transcripts. Exact model/version was not exposed
  to this author, so no model/version or commit attribution is guessed.

## Candidate v1 — stopped

Proposal SHA256:
`ce6a83e8dccaaa0a82d83543371a91e8d2ddf2c0e6db553546dedde848c9b7cf`.

Whole decision-log SHA256:
`2d99107e289cd7d939c9d07d1c25825da51875e32afa1b48285b2080139011af`.

Reviewer: `/root/index_scaffold_design_correctness`.
Lens: correctness, authority and framework reuse. Reviewed I1–I7, relevant
implementations, and the complete proposal. Original unresolved counts:
**0 Critical, 1 Major, 2 Minor, 0 Nit**.

The reviewer explicitly required stopping v1 before the second lens because its
input-validation boundary was inconsistent with the actual framework. That
verdict is retained, not rewritten as a pass.

### F1 — Major: pre-dedup checks and validation authority

`ObservationRows._scan()` calls `scan_stream()` before `project()`.
Identical winning-acquisition records are deduplicated and older acquisitions
can be superseded. The proposed projection could not reject every duplicate
in the original input. Separately, `run_suite()` returns evidence but
`ObservationRows` does not require a passing suite result.

Counterexample: an identical repeated fixture row disappears before child
validation; a direct `run_document` call can complete although v1 required
duplicate refusal. Skipping or blocking the suite also has no specified binding
to direct Node/API reads. An implementation following the manifest would either
break its promised guarantees or invent unapproved generic gating machinery.

Correction in v2: adopt the parent's winning-row semantics explicitly;
identical at-least-once repeats are allowed, conflicting winning-acquisition
ties refuse, and the child validates winning/selected domain rows. The suite
is separate evidence, not runtime read authority. The demo acceptance test
additionally requires a suite pass. Shipped fixture uniqueness is pinned by
tests without pretending it is a pre-dedup security boundary.
This is a substantive contract/matrix correction requiring fresh review.

### F2 — Minor: impossible quote chronology

V1 permitted synchronized valid quotes after the declared last-trade instant
and settlement, producing a hypothetical entry credit for an impossible entry.

Correction in v2: require quote time <= last-trade time <= declared settlement
observation, with matching settlement identity, expiry, underlying and instant.
No exchange-calendar engine is introduced.

### F3 — Minor: generated file missing from manifest

`init_journal()` also creates `docs/research/.gitkeep`. V1's exact 29-file
manifest omitted it.

Correction in v2: list that generated file and authorize **30** child files
in both the proposal and ADR. The coordinator independently found this same
omission.

V1 review limits: bounded static review; no code/runtime/strategy tests,
acquisition, training or source-license verification. Full original report and
stop-before-second-lens ruling are retained in the reviewer transcript above.

## Candidate v2 — reviewed, not clean

Proposal SHA256:
`ef96ec79bbb38d5d0fe8ed5f0e9bf3dbe93d8d8665f6164deb9c87ad91108fa0`.

Whole decision-log SHA256:
`0d01597d025c7943c481f01db7d1f4046257763377f65f37c01d199745beb9f1`.

- Fresh correctness/authority reviewer:
  `/root/index_scaffold_v2_correctness` — confirmed F1–F3 corrections; found F4
  below. Counts: 0 Critical, 1 Major, 0 Minor, 0 Nit unresolved.
- Independent tests/integration/data-semantics reviewer:
  `/root/index_scaffold_v2_integration` — confirmed F4; found T1 below.
  Counts: 0 Critical, 1 Major, 1 Minor, 0 Nit unresolved.
- Owner approval, child implementation, implementation review, market-data
  acceptance, empirical validation and trading approval: **not granted**.

### F4 — Major: multiplier positivity missing

The contract requires integer, equal multipliers but does not require them to
be positive. Its maximum-loss formula assumes positivity. With the documented
strikes/credit and all four multipliers set to -100, gross P&L is -260 at
settlement 500 and +240 in either tail; the specified maximum-loss formula
instead reports -240, although the actual maximum gross loss is 260.

The reviewer requires a strictly positive integer multiplier, with booleans,
zero and negatives refused by the domain owner through all public facades and
explicit regression cases in I2/I4. This is a localized numeric-domain defect,
not another invalid framework boundary. V2's second lens therefore completes
before batch correction. Finding remains open in v2; full reviewer report is
retained under the transcript identity above.

Correction in v3: strictly positive, non-boolean integer multipliers are required
in the domain contract, ADR and I2/I4 tests through construction, projection,
direct diagnostic and pipeline/CLI. Zero and negatives refuse. Coordinator
independently reproduced the counterexample with exact Decimal arithmetic.

### T1 — Minor: vacuous journal-isolation evidence

Ordinary pytest makes journal `append_action` a no-op through
`PYTEST_CURRENT_TEST` unless `DSKIT_JOURNAL_TESTS=1`. Merely checking unchanged
or separate journals could pass without testing any recording.

Correction in v3: explicitly enable recording, bind `DSKIT_JOURNAL_ROOT` to
each isolated temporary child, assert the expected actions actually exist,
and also assert the other root and owner Paths are unchanged.

The v2 integration reviewer additionally ran 108 Decimal payoff comparisons
and 12 maximum-loss checks for positive multipliers, reproduced F4, probed the
journal guard read-only, and checked manifest/hashes. No implementation tests
or market execution occurred. Its primary-source check supplied the newer
Databento May 2025 history/schema announcement; v3 includes that qualified
update without treating it as sample verification.

V2 is retained as not clean. V3 does not inherit either v2 pass: there was none.

## Candidate v3 — design review complete

Proposal SHA256:
`3390136c177fae9e74c32596eeeccf248453d722bb9ca4262785a0d32f267c8d`.

Whole decision-log SHA256:
`785c5e0ea5711b216076ac1d599f5ed1e6a52ae9b8c9024b1205d4dd114e2279`.

- Fresh correctness/authority reviewer:
  `/root/index_scaffold_v3_correctness` — complete, 0 Critical / 0 Major /
  0 Minor / 0 Nit. Completed I1–I7, verified F1–F4 and T1 design corrections,
  confirmed the framework interfaces and unchanged hashes. Static WSL checks
  only; no implementation, provider or empirical certification.
- Fresh tests/integration reviewer:
  `/root/index_scaffold_v3_integration` — complete, 0 Critical / 0 Major /
  0 Minor / 0 Nit. Completed I1–I7, confirmed the manifest, domain/arithmetic
  contract, framework seams and F1–F4/T1 corrections. Both hashes unchanged.
- **Final disposition:** all F1–F4 and T1 findings addressed at the design level;
  no unresolved in-scope Critical/Major or deferred Minor/Nit backlog.
  The two completed independent v3 reports close this Phase 0 design review.
- Owner approval and all implementation/data/trading gates remain ungranted.

The final integration lens checked Cboe, Databento and IBKR source qualifications;
Theta documentation could not be independently retrieved through its redirect.
Its optional Python probe failed to launch because of the sandbox, so its
verdict claims static/shell/hash checks only, not executed arithmetic. Earlier
v2 reviewer arithmetic checks and the coordinator's successful Decimal checks
remain separately attributed evidence; they are not recast as v3 reviewer runs.

No further contract or ADR edits were made after these matching-hash reviews.
This record adds evidence only. Next authority transition: the owner explicitly
approves S0 and ADR-0167, then implementation starts under TDD with its own
immutable code candidate and fresh independent implementation reviews.

## Coordinator checks and limits

Read-only WSL checks verified the original manifest count, distinct paths,
absence of a child directory, unique proposed ADR number, local links (the
review-record link was pending creation), and three exact Decimal payoff
examples: USD -248, +252, -248 for settlement 470, 500, 530 respectively.
These are arithmetic checks, not backtests.

Revised checks passed: 30 unique manifest paths including the generated
`.gitkeep`, child directory absent, unique proposed ADR, all local document
links present, three payoff examples exact, and both reviewed hashes unchanged.
Tracked
`git diff --check` was clean; the untracked proposal's
`git diff --no-index --check /dev/null <file>` produced no whitespace errors
(exit 1 denotes the new-file difference). No full test suite ran.

Primary-source investigation is documented with links in proposal §5. No
provider account, licensed dataset, actual sample, current paid quote, broker,
or empirical profitability was verified. All work remains local and uncommitted;
no scaffold was created and no push is claimed.

## S0 implementation — authorized, candidate review pending

Russell explicitly approved building the child after S0-v3. The approval was
recorded in the proposal and ADR before implementation. On resumption he
identified this session's model as **GPT-6 Astra**, resolving the commit
attribution gate. Earlier design verdicts/statuses above remain historical;
this section supersedes their then-current lack of implementation authority.

Bounded result: the approved 30-file synthetic-only child plus the existing
children list, proposal/ADR/evidence and delivery re-entry documentation.
Base/dependencies remain `4ca0d4e47f41cfcdded9679707bdec9a47b07806`.
No shared implementation, existing child, Path content, model, broker or vendor
workflow changed. The educational proposal is retained unchanged as context.
I1–I7 in S0-v3 remain the behavioral matrix; no threat-model change.

### TDD and corrections

- RED: the initial DefinedRiskCondor interface returned an empty report.
  The three center/down/up payoff tests failed at their expected numeric
  assertions; after exact Decimal domain implementation, all three passed.
- RED: initial thin wrapper/Node interfaces lacked narrow params and report
  behavior. Five intended assertions failed (three allowed-knob tuples,
  direct report and default-deny params). Implementation made all eight
  then-existing tests pass. These were new interfaces, not removed code.
- RED: the shipped suite originally required all three streams in every
  snapshot. The three per-snapshot CLI-compatible suite tests each observed
  block instead of pass. The existing acquisition API commits one stream per
  snapshot. Correction: shared field rules apply to present rows; explicit
  demo/test checks independently require stream counts 4, 4 and 3. No generic
  framework change, weakening of runtime domain validation, hidden read gate,
  or claim that field-rule pass proves completeness.
- Expanded existing behavior checks passed without manufactured RED:
  unsupported terms/numbers, all payoff regions, unequal wings, fees/count,
  exactness under low ambient Decimal precision, versions/duplicates/vintages,
  output-field collisions, ordinary subclasses and direct facade parity.

Interpreter for all tests:
`/home/russell/dskit/.venv/bin/python` (Python 3.12; pytest 9.1.1).
It imports the existing framework at the base revision; the new child is
bootstrapped from its own path. The shared environment was not modified.

### Pre-review checks

- From child root: `python -m pytest tests -q` — **204 passed**, 8.72 s.
- From foreign cwd /tmp: `python -m pytest <absolute-child>/tests -q` —
  **204 passed**, 14.28 s.
- Public root helper loaded from tests/children/test_children.py and invoked as
  `run_child_suite(<index_options>)` — passed for this child only.
- A copied child outside the repository, with only installed DSKIT:
  `python -m pip wheel --no-deps --no-build-isolation . --wheel-dir <temp>`
  built index_options-0.0.1; `python -m pytest tests -q` — **204 passed**,
  5.68 s. No network dependency resolution or shared installation.
- `python -m ruff check children/index_options` — passed.
- `git diff --check` — passed for tracked changes; staged/new-file check is
  required when freezing the candidate.
- Exact 30-file manifest and AGENTS/CLAUDE parity are test-pinned.
- `git grep -n index_options -- dskit` returned no runtime reverse import.
- Actual CLI init/register/acquire/validate/run round trip occurs in two
  independent temporary roots. Tests explicitly set DSKIT_JOURNAL_TESTS=1 and
  DSKIT_JOURNAL_ROOT and prove recorded acquire/execute actions, unchanged
  other-root ledger/state and unchanged checked-in child Path/actions.
- The checked-in journal marker, empty ledgers, generated README and research
  .gitkeep were initialized by `python -m dskit.journal init --root .`.
  No Path rows or action history were added, edited or promoted.
- Negative public API/CLI paths cover missing streams, corrupt JSONL, tied
  winning duplicates, provenance, chronology and zero/negative/bool
  multipliers; no completed diagnostic artifact is permitted.
- ServingExecutionPolicy plus public classify_plan reports forbidden for all
  four child nodes without constructing or reading them. This is serving
  classification evidence, not a release/deployment qualification.

Software outcomes only: center USD +252 and either tail USD -248 including
USD 8 whole-outcome fees. Full root/other-child suites, market-data access,
real replay, training, MIO, paper/live trading and profitability tests were
intentionally not run. Next: immutable candidate and two fresh sequential
independent implementation lenses; no readiness or push claim yet.
