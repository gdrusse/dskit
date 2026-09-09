## TL;DR

ADR-0112 generic release-rotation calendar is implemented and ready to use: it
makes a repeatable timetable of training and hold-back windows without training
a model or changing a deployment. Five sequential skeptic cycles left no Critical
or Major finding; 49 focused tests, Ruff, and the whitespace check passed. It is
a time-contract utility only: a caller still owns every model, data, release,
and deployment decision.

## Execution contract

Date: 2026-09-08. Owner-authorized ADR-0112 defines the scope. The deliverable
is a stdlib-only, immutable pipeline value seam; it must have no wall clock,
I/O, market-calendar, dataset, training, promotion, or deployment behavior.
Intervals are UTC-normalized and half-open. This memo covers the generic
framework only; it deliberately does not modify children/intraday_equities or
redo its final-model/replay/monitoring plan.

## Implementation evidence

dskit.pipeline.release_rotation adds HalfOpenInterval,
ReleaseRotationWindow, and ReleaseRotationCalendar. A caller supplies an aware
anchor, cadence, training span, embargo, request range, and finite cap.
materialize() returns ordered windows; manifest() returns canonical JSON with
calendar and manifest SHA-256 digests. Zero embargo is null, not an empty
interval. Invalid/naive times, bad spans, overlap, pre-anchor or inverted
ranges, unbounded requests, malformed joins/digests, and datetime overflow
refuse by name.

The calendar is a value object, not a stage or registry kind. It reuses
stages.is_sha256hex; that shared validator now uses fullmatch, so a valid
64-character digest followed by a newline cannot pass. Public classes are
frozen, slot-based, exported through __all__, and document their constructor
and copyable example. The pipeline README and AGENTS inventory the module.

## Files

- docs/architecture/decision-log.md -- accepted ADR-0112.
- dskit/pipeline/release_rotation.py -- generic calendar values.
- tests/pipeline/test_release_rotation.py -- contract and boundary tests.
- dskit/pipeline/stages.py and tests/pipeline/test_stages.py -- exact shared
  digest validation and its regression.
- dskit/pipeline/README.md and dskit/pipeline/AGENTS.md -- module inventory.
- docs/memos/README.md and this memo -- the required new memo directory layout.

## TDD and verification

Observed red evidence: focused test collection initially failed because
dskit.pipeline.release_rotation did not exist. The first skeptical fix cycle
then produced four expected regression failures (zero-embargo and digest cases);
the newline-validator fix produced two expected failures (shared validator and
consumer). The test-first contract now covers normal materialization,
equivalent aware UTC offsets, zero embargo, interval/window joins, invalid
values, finite request bounds, and digest canonicality.

Final green evidence: focused rotation/stages/purity/method checks reported 49
passed; Ruff and git diff --check were clean. The exact final command output
belongs to the implementation task transcript; this memo intentionally does not
reconstruct a shell command it cannot verify.

## Skeptic trace (sequential)

Each reviewer was fresh and independent; one ran at a time. These are their
verdicts and the corresponding changes, preserved verbatim in substance from
the reviewer reports.

1. rotation_skeptic_1: 0C/2M/1m -- zero embargo formed an invalid empty
   interval; ADR-0112 ended mid-entry; a noncanonical digest was accepted.
   Fixed by emitting embargo: null, completing ADR-0112, and routing window
   validation through the shared digest validator.
2. rotation_skeptic_2: 0C/1M/0m -- the anchored digest regular expression
   accepted 64 hexadecimal characters plus a newline. Fixed with
   is_sha256hex(...fullmatch...) and producer/consumer regressions.
3. rotation_skeptic_3: 0C/0M/1m -- malformed interval and window-join
   refusals lacked direct regressions. Fixed by adding boundary/join tests;
   production behavior did not change.
4. rotation_skeptic_final: 0C/0M/1m -- public NumPy-style docstrings and
   examples were incomplete. Fixed those docs and examples.
5. rotation_skeptic_ship: PASS, 0C/0M/0m; it reported 42 focused tests and
   Ruff clean. The final implementer-focused run expanded the evidence to 49
   passing tests above.

No Major or Critical issue remained after the final independent pass.

## Limitations and safe next use

The utility does not choose calendar policy, know a trading/session calendar,
start a run, bind a manifest to a model or dataset, or authorize release or
deployment. Supply a timezone-aware anchor and explicit durations; store the
returned manifest alongside the higher-level release evidence; have that layer
validate its own model/data/promotion authority. Add any domain mapping in its
own owner layer and ADR if it changes architecture.

## Reproducibility and handoff

The implementation is prepared for the repository /wrap workflow. Its
non-committing preflight is the focused tests, Ruff, and diff check above;
/wrap itself requires refreshing docs/RE-ENTRY.md and then separately authorized
commit, merge, and push. No journal row is appropriate: this is package
implementation work, not a child acquire/research/execute/production action. No
child or intraday plan file was changed by this work.
## Retained skeptic closure

The release gate is closed by the two reviewer-owned artifacts required by
skeptic-review Rules 1 and 7:

- docs/memos/2026-09-08-release-rotation-framework-skeptic-method.md --
  method/calendar lens; PASS, 0 Critical, 0 Major, 0 Minor.
- docs/memos/2026-09-08-release-rotation-framework-skeptic-final.md --
  ship/integration lens; PASS, 0 Critical, 0 Major, 0 Minor.

The ship reviewer explicitly states that, together, these distinct retained
reports satisfy Rules 1 and 7. The earlier unretained review summaries remain
historical context only and are not used as the closure evidence.

Both retained reviewers ran:

    /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_release_rotation.py tests/pipeline/test_stages.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
    /home/russell/dskit/.venv/bin/ruff check dskit/pipeline/release_rotation.py dskit/pipeline/stages.py tests/pipeline/test_release_rotation.py tests/pipeline/test_stages.py tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py

The method report records 49 passed in 2.83s; the ship report records 49 passed
in 2.94s. Both report Ruff "All checks passed!" and clean diff checks. This
review closure authorizes no commit, merge, or push by itself; those remain
separate wrap actions.
