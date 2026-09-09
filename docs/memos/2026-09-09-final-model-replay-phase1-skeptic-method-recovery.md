# Skeptic review — final-model replay Phase 1 method/API lens, recovery verification

**Reviewer task:** independent fresh recheck of the ADR-0113 Phase-1 recovery
fix (three prior Major findings).
**Model/effort:** Claude Sonnet 5, high.
**Dispatch:** fresh sequential method/API review of the fix commit against
the baseline final report; no implementation edits made.

## Scope and evidence

Read `CLAUDE.md`, `dskit/pipeline/CLAUDE.md`, the prior baseline report
(`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-method-final.md`,
FAIL, 3 Major), ADR-0113's current text in
`docs/architecture/decision-log.md`, the complete current
`dskit/pipeline/kinds_search.py`, and the complete current
`tests/pipeline/test_kinds_search.py`. The named diff range (`c775ac5..HEAD`)
is a single squashed commit (`2631282`, "fix Phase 1
SelectionRecord/simplicity-order/TrialLedger findings") that introduces the
entire ADR-0113 framework and its fix together, so the pre/post-fix code is
not separable by hunk; verification instead re-derives each finding directly
from the current file, cross-checked line-for-line against the baseline
report's own quoted line numbers and repro, and against ADR-0113's current
prose clause-by-clause.

```text
$ python3 -m pytest -q tests/pipeline/test_kinds_search.py tests/pipeline/test_planner.py \
    tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
187 passed in 7.45s

$ python3 -m ruff check dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
All checks passed!

$ git diff --check
(clean, exit 0)

$ git status --short
(clean; HEAD = 2631282)
```

Also ran `TestTrialLedgerConcurrencyContract` alone 5x back-to-back
(`~0.4s` each) to rule out timing flakiness in the thread-race tests — stable
every time. Confirmed no other module in the tree constructs
`SelectionRecord(...)` directly (`grep -rn "SelectionRecord("` outside
`kinds_search.py`/the test file returns nothing), so the constructor
lockdown breaks no existing caller.

## Findings

None survive independent verification. All three prior Major findings are
resolved by genuine public-design changes (not narrow per-example patches),
and no new Critical/Major defect was found in the fix itself.

### Finding #1 (SelectionRecord unbound construction) — RESOLVED

`SelectionRecord.__init__` (`kinds_search.py:992-998`) unconditionally
raises `TypeError` for every call shape — positional, keyword, and empty —
so `SelectionRecord({})`, the exact original repro, now raises instead of
succeeding. The only path to an instance is
`OneStandardErrorSelector.select()` → `SelectionRecord._build()`
(`:1000-1079`) → `SelectionRecord._seal()` (`:1081-1117`):

- `_build` requires `type(ledger) is not TrialLedger` to fail (exact type,
  no subclass substitution) and `ledger.is_complete()`, then derives BOTH
  `inventory_digest` and `ledger_digest` from the live ledger object itself
  (`ledger.inventory_digest`, `ledger.digest`) — never from a caller-supplied
  string, closing the second half of the original finding.
- `_build` cross-checks `eligible_candidates` and `simplicity_order` against
  `ledger.rows` (canonical inventory order) row-for-row, and requires
  `selected_candidate` to be a member of `eligible_candidates`.
- `_seal` independently re-checks the exact 9-field schema
  (`set(payload) != _SELECTION_RECORD_FIELDS`, `:1090-1094`) and that both
  digest fields match `_DIGEST_RE` (`^[0-9a-f]{64}$`, `:930`,`:1100-1103`),
  refusing a caller-supplied arbitrary digest string even at this inner
  layer.

`tests/pipeline/test_kinds_search.py::TestSelectionRecordSchema` pins the
exact reviewer repro (`test_the_formerly_exploitable_empty_payload_can_no_longer_construct`,
`:131-136`), every call shape (`:138-144`), the unbound-digest repro
(`test_seal_refuses_an_unbound_digest_free_record`, `:152-158`), an
exhaustive missing-field sweep over all 9 fields
(`test_seal_refuses_missing_fields`, `:160-165`), extra-field refusal
(`:167-169`), schema-version drift (`:171-173`), and the positive path bound
to a real ledger (`:175-188`). Not shallow — each test targets the exact
failure mode named in the baseline finding.

### Finding #2 (non-orderable simplicity-key mix) — RESOLVED (by re-design, ADR updated)

The accepted simplicity-key grammar is unchanged (str/int/float/tuple,
`_simplicity_value`, `:339-363`, itself untouched by the fix). Instead of
refusing mixed types, ADR-0113 was updated to retract the "refuse
non-orderable values" framing and replace it with one canonical tagged total
order — `_ordering_key` (`:375-414`): every number sorts below every string,
which sorts below every tuple (elementwise, recursively for tuples); the tag
is compared before the payload so two differently-shaped keys never raise
`TypeError` and never silently tie. This is a legitimate design resolution,
not a workaround — ADR-0113's current text says so explicitly
(`decision-log.md:6150-6158`), and CLAUDE.md's "ADR before code" bar is
satisfied (status: accepted, 2026-09-09).

Verified the order is genuinely total over the accepted domain:
- Numbers: Python's `int`/`float` rich comparison is exact even for huge
  ints vs floats (no precision loss), and `_simplicity_value` already
  refuses non-finite floats and oversized ints before `_ordering_key` ever
  sees them, so the NUMBER branch never raises.
- Strings: plain `str.__lt__`, always defined.
- Tuples: recursive elementwise comparison via the same tagged keys, so a
  length mismatch or nested type-shape mismatch resolves by normal Python
  tuple-comparison semantics (shorter prefix orders first) without ever
  comparing two raw incomparable payloads directly.

`OneStandardErrorSelector.select()` evaluates `_simplicity_value` (raising on
failure, "simplicity_key raised on trial …") for **every** ledger row before
choosing a winner (`all_keyed_rows`, `:1312`), not just eligible ones, then
runs the winner tie-break only over eligible rows through `_ordering_key`
(`:1336`). The exact reviewer scenario — eligible int key `1`/`2` beside an
ineligible str key `"a"`/`"zz"` — is exercised directly in
`TestSimplicityOrderingTotalOrder::test_the_exact_reviewer_repro_selects_deterministically`
(`:221-238`), which selects deterministically and asserts the ineligible
string key is retained in the published `simplicity_order` rather than
refused — matching the ADR's stated design. The total-order property itself
is pinned independently: numbers-below-strings-below-tuples
(`:203-206`), exact int/float cross-comparison (`:208-212`), tuple
elementwise/length ordering including a nested type-shape mismatch
(`:214-219`), and that a poisoned `simplicity_key` on an ineligible row
still fails the whole selection (`:240-257`). This is a real total-order
proof, not a homogeneous-type happy path.

### Finding #3 (undeclared TrialLedger concurrency contract) — RESOLVED

`TrialLedger`'s class docstring (`:644-696`) now states, as an explicit
public, PIN-marked contract: thread-safe/process-local only (one
`threading.RLock`); `reserve → freeze → commit` as the transaction, with
`reserve` and `commit` each atomic and freezing outside the lock; exactly
one admission wins a duplicate-concurrent-candidate race and the loser
raises immediately; a failed preparation releases its reservation so the
candidate may be retried; `BaseException` always propagates unchanged after
that release; and a committed row can never later be observed as pending or
be un-committed. Checked each clause against `record()`/`_reserve()`/
`_commit()`/`_release_if_uncommitted()` (`:798-910`) line by line — the
implementation matches every clause:

- `_reserve` (`:821-832`) and `_commit` (`:834-842`) each hold the lock for
  their whole body only; `_freeze_trial` (called between them in `record()`,
  `:815`) runs with no lock held.
- The duplicate-admission check (`key in seen or key in pending`) is read
  and written atomically under one lock acquisition, so a racing loser fails
  fast rather than after the winner's slow freeze.
- `record()`'s `except BaseException: self._release_if_uncommitted(...); raise`
  (`:817-819`) is unconditional and catches `BaseException`, not `Exception`,
  so `KeyboardInterrupt`/`SystemExit` propagate unchanged after release.
- `_release_if_uncommitted` only removes a reservation this call's own
  `token` still owns (`pending.get(key) is token`) and never touches a
  candidate already `seen` — a losing `_reserve()` call never had a pending
  entry to release, so it correctly no-ops rather than clobbering the
  winner's state.

`TestTrialLedgerConcurrencyContract` (`:286-377`) pins every clause with real
multi-threaded and interruption tests, not prose-adjacent assertions: a
two-thread race on one candidate with an artificially slowed freeze proving
exactly one admission and a fast-failing loser (`:296-318`); a mid-flight
read during the slow freeze proving `rows`/`is_complete()` never observe the
pending state (`:320-337`); a same-ledger retry after a mid-freeze
`ValueError` succeeding (`:339-352`); the same for a mid-freeze
`KeyboardInterrupt`, confirmed to propagate as `KeyboardInterrupt` and not
poison the candidate (`:354-367`); and a committed row refusing a second
`record()` call with the original row unmodified (`:369-377`). Reran this
class 5x standalone — stable, no flakiness; the lock-based invariants are
enforced deterministically regardless of actual thread interleaving, so the
`time.sleep(0.1)` widens the race window rather than making the assertions
depend on timing luck.

## Checks that passed

- `CandidateInventory` ordering, immutability, digest, and cap enforcement
  are unchanged and still correct (not touched by this fix).
- `TrialLedger` admission (membership, evidence-field completeness,
  finite/non-negative `se`, negative-zero canonicalization) is unchanged and
  still correct.
- No other module in the tree constructs `SelectionRecord` directly, so the
  new constructor lockdown introduces no breakage; `dskit/pipeline/__init__.py`
  still re-exports all four public names unchanged.
- The purity gate stays satisfied — `record()`'s new transaction machinery
  uses only `threading`/stdlib at module scope; no heavy import was added.
- Full focused suite (kinds_search, planner, purity, method_lengths), Ruff,
  and `git diff --check` all pass clean.

## Minor observations (non-blocking, not counted toward the verdict)

- `dskit/pipeline/README.md:608` and `dskit/pipeline/AGENTS.md:409-411` add
  the new `kinds_search.py` tree-description lines using a plain ASCII `|`
  continuation instead of the box-drawing `│` used by every other multi-line
  entry in both trees — a cosmetic inconsistency in an otherwise-correct doc
  update, not a contract defect.
- The `TrialLedger` docstring's "the loser is NOT retryable for that same
  candidate" (finding #3's bullet) is accurate for the case the race
  resolves to a committed winner, but read in isolation could seem to
  conflict with the immediately-following "failed preparation... MAY be
  retried" bullet, since a caller who lost a race against an eventually-
  failed-and-released reservation can, in fact, retry successfully — the two
  bullets are complementary (committed-winner vs. failed-winner) rather than
  contradictory, and the actual mechanics are correctly tested, but the
  wording could be tightened to say so explicitly rather than leaving it to
  be inferred from clause order.

## Verdict

**PASS — 0 Critical, 0 Major.** All three baseline findings are resolved at
the public-design level (a genuinely non-constructible `SelectionRecord`
bound only through a ledger-cross-checked internal factory; one documented
and proven total order over the whole simplicity-key domain, formalized by
an ADR update rather than patched around; and a fully public, clause-by-
clause TrialLedger concurrency/interruption contract backed by real
multi-threaded regression tests). No new Critical or Major defect was
introduced by the fix; the two items above are cosmetic/prose polish only.
