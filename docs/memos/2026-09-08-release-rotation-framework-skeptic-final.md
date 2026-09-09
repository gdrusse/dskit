## Skeptic review - release-rotation retained ship lens

**Reviewer task:** `/root/rotation_retained_ship_skeptic`
**Model/effort:** GPT-5.6 Terra, high
**Lens:** architecture and integration: OOP/tiering/public API compatibility,
shared-validator blast radius, identity/refusal behavior, docs/tree and memo
evidence, RE-ENTRY/wrap readiness, review-artifact compliance, and strict
non-overlap with the intraday plan.
**Dispatch mode:** sequential. This is an independent reviewer-owned report.

## Reviewed state

Reviewed `main` at `1124e492a09f6ccaf0004b038df5c511f43ea0da`, with no staged
files. Unstaged tracked changes: `docs/RE-ENTRY.md`,
`docs/architecture/decision-log.md`, `dskit/pipeline/AGENTS.md`,
`dskit/pipeline/README.md`, `dskit/pipeline/stages.py`, and
`tests/pipeline/test_stages.py`. Untracked changes: the `docs/memos/` index,
implementation memo, retained method review, `release_rotation.py`, and its
tests. This report is the only file created by this reviewer.

I read the root and pipeline `AGENTS.md`, `wrap.md`, `skeptic-review.md`, full
current tracked/untracked diff, ADR-0112, RE-ENTRY, the implementation memo,
the retained method review, affected code/tests/docs, and the intraday MIO
plan. ADR-0112 adds a Tier-1, stdlib-only immutable value seam; it neither
imports a child nor takes a child/local-calendar policy. It therefore does not
overlap the still-separate intraday plan or `ProgramCalendar` stage contract.

## Commands and results

```text
/home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_release_rotation.py tests/pipeline/test_stages.py \
  tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
# 49 passed in 2.94s

/home/russell/dskit/.venv/bin/ruff check \
  dskit/pipeline/release_rotation.py dskit/pipeline/stages.py \
  tests/pipeline/test_release_rotation.py tests/pipeline/test_stages.py \
  tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
# All checks passed!

# WSL probe: half-open microsecond boundary selection and recomputed compact,
# sorted-ASCII manifest SHA-256.
# boundary and manifest probe passed

git diff --check
git diff --no-index --check /dev/null dskit/pipeline/release_rotation.py
git diff --no-index --check /dev/null tests/pipeline/test_release_rotation.py
git diff --no-index --check /dev/null docs/memos/2026-09-08-release-rotation-framework.md
git diff --no-index --check /dev/null docs/memos/2026-09-08-release-rotation-framework-skeptic-method.md
git diff --no-index --check /dev/null docs/memos/README.md
# clean
```

## Findings

None: **0 Critical, 0 Major, 0 Minor** findings.

The public value objects are frozen, slot-based, module-exported, and validate
at construction (`release_rotation.py:57-212`, `:216-395`); no new registry,
stage, child import, or root-package import side effect is required. The shared
digest change is deliberately narrower in acceptance: `is_sha256hex` now uses
`fullmatch` (`stages.py:45-47`), and its benchmark consumers remain compatible
because they require actual lowercase digest equality before accepting pinned
bytes. `ReleaseRotationWindow` uses the same validator (`release_rotation.py:166-180`).

Identity is canonical and fail-closed: normalized UTC public objects alone feed
calendar/window/manifest digests (`release_rotation.py:48-53`, `:195-212`,
`:283-302`, `:354-363`); malformed joins, intervals, digest strings, request
ranges, and boolean/unbounded caps refuse (`:90-103`, `:157-181`, `:365-381`).
README/AGENTS trees both list the module, and ADR-0112's stated files match the
diff. The memo and RE-ENTRY explicitly supersede their earlier unretained review
claims and keep the gate open until this report and the separate method-lens
report exist; neither claims a commit, merge, or push has happened.

## Disposition

**PASS for this distinct retained ship/integration lens.** Together with the
already retained, distinct method/calendar report, this satisfies the two
independent-review artifacts required by skeptic-review Rules 1 and 7. This
review authorizes no commit, merge, or push by itself; those remain the
authorized `/wrap` steps.

**Verbatim verdict:** `PASS - 0 Critical, 0 Major findings.`
