## Skeptic review — release-rotation method/calendar lens

**Reviewer task:** `/root/rotation_retained_method_skeptic`
**Model/effort:** GPT-5.6 Terra, high
**Lens:** value-model ownership, calendar arithmetic, half-open and DST
semantics, deterministic identity/manifest behavior, refusals, shared-validator
compatibility, and focused-test sufficiency.
**Dispatch mode:** sequential; this is an independent reviewer-owned report.

## Scope and reviewed state

Reviewed ADR-0112 in `docs/architecture/decision-log.md:6049-6095`; the full
current worktree diff from parent commit
`1124e492a09f6ccaf0004b038df5c511f43ea0da` (`docs: plan final model replay
and monitoring`); `docs/RE-ENTRY.md`; the implementation memo; root and
`dskit/pipeline` AGENTS; `docs/skills/skeptic-review.md` Rules 1 and 7; and
the intraday final-model plan only to confirm non-overlap. The reviewed change
contains the untracked calendar module and tests, tracked ADR/docs/inventory
updates, and the `stages.is_sha256hex` exact-match correction. No staged
changes were present.

The calendar is correctly a standalone immutable pipeline value model, rather
than a stage, registry entry, or child/domain calendar. It owns only explicit
UTC-normalized duration windows; it deliberately does not claim the
zoneinfo/DST local-recurrence contract reserved by the intraday plan.

## Commands and results

```text
/home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_release_rotation.py tests/pipeline/test_stages.py \
  tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
# 49 passed in 2.83s

/home/russell/dskit/.venv/bin/ruff check \
  dskit/pipeline/release_rotation.py dskit/pipeline/stages.py \
  tests/pipeline/test_release_rotation.py tests/pipeline/test_stages.py \
  tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
# All checks passed!

git diff --check
git diff --no-index --check /dev/null dskit/pipeline/release_rotation.py
git diff --no-index --check /dev/null tests/pipeline/test_release_rotation.py
git diff --no-index --check /dev/null docs/memos/2026-09-08-release-rotation-framework.md
git diff --no-index --check /dev/null docs/memos/README.md
# all clean
```

An additional WSL venv probe independently checked: releases strictly inside,
on, and immediately after half-open request boundaries; recomputation of the
manifest SHA-256 from sorted compact ASCII JSON; and the 2026-03-08 New York
DST jump. Result: `calendar boundary, digest, and DST probes passed`.

## Findings

None. I found **0 Critical, 0 Major, 0 Minor** findings.

Evidence: `release_rotation.py:263-273` rejects bad geometry and first-window
underflow; `:365-382` derives bounded non-negative release indices using
microsecond integer arithmetic and preserves `[start, end_exclusive)`; and
`:384-395` creates only joined non-empty training/positive-embargo intervals
or the explicit zero-embargo `None`. `:283-302`, `:195-212`, and `:354-363`
place normalized public state alone in canonical digest material. The shared
validator’s `fullmatch` at `stages.py:45-47` is compatible with existing
benchmark consumers and closes the trailing-newline acceptance; focused tests
cover its producer and consumer behavior.

The reviewed tests do not duplicate every manually probed boundary/DST/digest
case, but the implementation is UTC-duration based (not local calendar
recurrence), and the focused test suite plus the independent probes adequately
exercise that contract. This is not a correctness finding.

## Disposition

**PASS for this distinct method/calendar lens.** This report does not alone
close the release gate: skeptic-review Rule 1 still requires a separate,
independent retained report using another lens. Rule 7 is satisfied for this
review by this reviewer-authored artifact.

**Verbatim verdict:** `PASS — 0 Critical, 0 Major findings.`
