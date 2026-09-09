# Skeptic review — final-model replay Phase 1 architecture/integration/governance lens, recovery verification, cycle 2 (LAST bounded cycle)

**Reviewer task:** re-verify, on the current HEAD, that the 4 Major findings
from my own cycle-1 report
(`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-architecture-recovery.md`)
were actually and correctly resolved by the intervening fix (commit
`b217e5f`) — not merely fixed-looking. Same lens as cycle 1:
architecture/integration/governance against root `CLAUDE.md` and
`dskit/pipeline/CLAUDE.md`. This is the second and last bounded correction
cycle; a FAIL here stops the process for human adjudication rather than
looping again.
**Model/effort:** Claude Sonnet 5, high.
**Dispatch:** fresh sequential re-read of both CLAUDE.md files, the full
Phase-1 diff, the cycle-1-to-HEAD delta, and direct code/test inspection; no
implementation edits made.

## Scope and evidence

Read root `CLAUDE.md` and `dskit/pipeline/CLAUDE.md` in full. Read my own
prior report in full. Reviewed:

```text
$ git -C /home/user/dskit diff c775ac5..HEAD --stat -- dskit/pipeline/kinds_search.py \
    dskit/pipeline/README.md dskit/pipeline/AGENTS.md dskit/pipeline/CLAUDE.md \
    docs/architecture/decision-log.md tests/pipeline/test_kinds_search.py \
    dskit/pipeline/__init__.py dskit/pipeline/planner.py
 docs/architecture/decision-log.md   |   84 +
 dskit/pipeline/AGENTS.md            |   24 +-
 dskit/pipeline/CLAUDE.md            |   24 +-
 dskit/pipeline/README.md            |   11 +-
 dskit/pipeline/__init__.py          |    6 +-
 dskit/pipeline/kinds_search.py      | 1367 +++++++++++++++++++++++++++++++++--
 dskit/pipeline/planner.py           |   19 +-
 tests/pipeline/test_kinds_search.py |  369 +++++++++-
 8 files changed, 1838 insertions(+), 66 deletions(-)

$ git -C /home/user/dskit diff 2631282..HEAD --stat
 ...phase1-skeptic-architecture-recovery.md   | 282 ++++++++ (new memo, cycle 1's own report)
 ...phase1-skeptic-method-recovery-2.md       | 203 ++++++ (new memo)
 ...phase1-skeptic-method-recovery.md         | 214 ++++++ (new memo)
 dskit/pipeline/AGENTS.md                     |   6 +-
 dskit/pipeline/CLAUDE.md                     |  24 +-
 dskit/pipeline/README.md                     |   6 +-
 dskit/pipeline/kinds_search.py               |  33 +--
 tests/pipeline/test_kinds_search.py          |  45 ++
 8 files changed, 790 insertions(+), 23 deletions(-)
 # note: docs/architecture/decision-log.md, __init__.py, planner.py are
 # UNCHANGED since cycle 1 — confirmed no cycle-2 touch to these three.
```

```text
$ python3 -m pytest -q tests/pipeline/test_kinds_search.py tests/pipeline/test_planner.py \
    tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
190 passed in 7.00s

$ python3 -m ruff check dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
All checks passed!

$ git diff --check
(clean, exit 0)

$ git -C /home/user/dskit diff c775ac5..HEAD --stat -- docs/decisioning/path.csv dskit/journal
(no output — neither touched)
```

190 passed vs. cycle 1's 187 — exactly the 3 new tests the fix commit added
(`test_max_candidates_refuses_before_materializing_even_with_n_trials_1`,
`test_a_grid_within_the_cap_still_honors_n_trials`,
`test_the_two_max_json_int_literals_agree`).

## Findings — each of the 4 required corrections, checked against what was actually asked for

### Finding 1 — RESOLVED

Required: port the "Generic search values" orientation content (and the
Contents-tree line) from `AGENTS.md` into `dskit/pipeline/CLAUDE.md`.

`git diff 2631282..HEAD -- dskit/pipeline/CLAUDE.md` shows both additions
landed: a new "Generic search values" bullet at `CLAUDE.md:167-186`
(Extension points section) and the two-line Contents-tree addition at
`CLAUDE.md:488-490` for `kinds_search.py`. Content check, not just
presence: `AGENTS.md` spreads the same material across three separate
bullets (`Generic search values`, `TrialLedger's concurrency contract is
public...`, `SelectionRecord has no public constructor...`,
`AGENTS.md:409-428`); `CLAUDE.md` consolidates it into one bullet
(`CLAUDE.md:167-186`) — every specific clause is present in both:
`max_candidates` cap ownership, negative-zero canonicalization,
reserve→freeze→commit atomicity, `TestTrialLedgerConcurrencyContract`,
`SelectionRecord`'s no-public-constructor + `_build`/`_seal` factory +
nine-field schema + digest cross-check, and `_ordering_key`'s tagged total
order. This is "reworded to fit CLAUDE.md's voice" exactly as my cycle-1
required correction allowed, not a partial port. The Contents-tree lines in
`CLAUDE.md`, `AGENTS.md`, and `README.md` are now byte-identical in wording
(and the fix additionally corrected the ASCII-pipe cosmetic nit
`AGENTS.md`/`README.md` had, noted non-blocking in my cycle-1 report — a
bonus, not required). All three package docs now agree.

### Finding 2 — RESOLVED

Required: extend `TestScalarRuleAgreement.CASES` with a true 4096/4097-digit
boundary pair against both `_planner_is_json_scalar` and
`_grid_is_json_scalar`, plus a direct equality assertion between the two
`_MAX_JSON_INT` literals.

`tests/pipeline/test_kinds_search.py:1072-1075` adds four cases:
`10**4096 - 1` (True), `-(10**4096 - 1)` (True), `10**4096` (False),
`-(10**4096)` (False). Verified the digit-count arithmetic directly:
`10**4096 - 1` is 4096 nines (4096 decimal digits — the accepted edge);
`10**4096` is `1` followed by 4096 zeros (4097 decimal digits — one over).
These feed the SAME parametrized loop (`test_both_gates_draw_the_same_line`,
`:1078-1085`) that already asserts both `_planner_is_json_scalar` and
`_grid_is_json_scalar` agree on every case, so both gates are exercised at
both edges. Confirmed the bound in the production code (`planner.py:67`,
`kinds_search.py:70`, both `_MAX_JSON_INT = 10**4096 - 1`) is a `<=`
inclusive-both-sides comparison (`planner.py:746`, `kinds_search.py:91`), so
the test cases land exactly on the enforced line, not merely near it. A new
`test_the_two_max_json_int_literals_agree` (`:1086-1092`) directly asserts
`_PLANNER_MAX_JSON_INT == _GRID_MAX_JSON_INT == 10**4096 - 1`, imported at
the top of the test file (`:46-47`) — satisfies the "ideally also assert...
directly" half of my required correction, not just the boundary-case half.

### Finding 3 — RESOLVED

Required: a regression that constructs a grid whose full Cartesian product
exceeds a small explicit `max_candidates`, passes a small `n_trials`, and
proves the refusal happens BEFORE materialization, not just that the final
count is small.

New `TestCandidateInventoryCap` class (`test_kinds_search.py:107-129`).
`test_max_candidates_refuses_before_materializing_even_with_n_trials_1`
builds a 20-knob binary space (`2**20 = 1,048,576` combinations),
`max_candidates=100`, `n_trials=1`, and asserts `ValueError` with message
matching `"candidate count .* exceeds max_candidates 100"`. This is not a
weaker "final count is small" check — it is the refusal itself, which by
construction can only fire if the cap check ran before `n_trials` narrowed
anything down to 1. Verified the code path directly:
`CandidateInventory.__init__` (`kinds_search.py:491-593`) calls
`_bounded_candidate_count(space_snapshot, max_candidates)` at line 585 —
strictly before `_grid(space_snapshot)` (materialization, line 586) and
before the `n_trials`-driven `_subsample` call (line 588). So the ordering
the test's own docstring claims ("the cap itself... called before
`_grid`/`_subsample`") matches the real source ordering, not just the
observed exception. A companion
`test_a_grid_within_the_cap_still_honors_n_trials` confirms the cap does not
also wrongly refuse a within-cap grid when `n_trials` narrows it further.
This is exactly the scenario my cycle-1 Finding 3 (and the original
pre-recovery architecture FAIL before it) named.

### Finding 4 — RESOLVED

Required: rewrite `CandidateInventory`'s class + `max_candidates`-property
Examples (both `>>>`), and `TrialLedger`'s / `OneStandardErrorSelector`'s
Examples (missing the `::`-intro shape), to the CLAUDE.md template.

`grep -n ">>>" dskit/pipeline/kinds_search.py` returns zero matches — all
doctest-style syntax is gone from the file. Direct inspection of all four
sites:
- `CandidateInventory` class docstring (`kinds_search.py:473-479`): now
  `"Build the two-candidate inventory over one knob::"` + blank line +
  indented block + `# -> 1` output marker. Compliant.
- `CandidateInventory.max_candidates` property: the Examples block was
  DELETED entirely (`kinds_search.py:604-606` now just a one-line
  docstring, no Examples section) rather than reformatted — an acceptable
  resolution path since the instruction allowed "rewrite... or removal"
  is implicit in "must never use `>>>`"; a one-line property returning a
  cached field does not need a worked example duplicating the class's own,
  and nothing in CLAUDE.md requires every method to carry an Examples
  block (only classes require one, at the class level, which
  `CandidateInventory` still has).
- `TrialLedger` Examples (`kinds_search.py:701-708`): now
  `"Record the one candidate a single-value inventory declares::"` + blank
  line + indented block + `# -> True` output marker on the final line.
  Compliant.
- `OneStandardErrorSelector` Examples (`kinds_search.py:1183-1189`): now
  `"Prefer the shallowest depth within one standard error of the best::"` +
  blank line + indented block. No output-marker line is needed here — the
  block only instantiates the class (no return value shown), matching
  CLAUDE.md's own template exactly (`WindowRows`'s Examples also only
  instantiates, no `# ->`). Compliant.
- `SelectionRecord`'s block (already compliant in cycle 1) is unchanged.

All four sites now match root `CLAUDE.md`'s docstring standard.

## Checks that passed (re-confirmed, cycle 1's approvals still hold)

- **Scope discipline.** The cycle-2 delta (`2631282..HEAD`) touches only
  `AGENTS.md`, `CLAUDE.md`, `README.md`, `kinds_search.py`,
  `test_kinds_search.py`, plus three new retained memo files — additive and
  corrective only. `docs/architecture/decision-log.md`,
  `dskit/pipeline/__init__.py`, and `dskit/pipeline/planner.py` are
  byte-identical to their cycle-1-reviewed state (no stat entries for them
  in the `2631282..HEAD` diff), so ADR-0113's text accuracy, the tiering
  and export-surface checks, and the `_MAX_JSON_INT` restatement-not-import
  pattern I already approved in cycle 1 are undisturbed.
- **Tiering / purity.** `tests/pipeline/test_purity.py` passes (included in
  the 190); no new imports were introduced by the cycle-2 diff beyond the
  two `_MAX_JSON_INT` re-imports inside the test file, which is
  test-tier and exempt.
- **No new duplication.** The two new production lines
  (`_bounded_candidate_count` call ordering) were already present at cycle
  1's review time — this cycle only added the pinning test the cycle-1
  verdict required; no new runtime code path was introduced.
- **Config/JSON identity.** Untouched — no diff anywhere near
  `document.py`, `NON_IDENTITY_SECTIONS`, or `NULLED_IDENTITY_SECTIONS` in
  either the full Phase-1 diff or the cycle-2 delta.
- **`docs/decisioning/` and `dskit/journal/`.** Confirmed untouched by both
  `git diff c775ac5..HEAD --stat` (see command output above) — no
  human-owner-only path violated.
- **Ruff / whitespace.** `ruff check` and `git diff --check` both clean.

## Minor observations (non-blocking, not counted toward the verdict)

- `CandidateInventory.max_candidates`'s Examples block was removed rather
  than reformatted (see Finding 4 above) — a legitimate resolution, flagged
  only so a future reader knows it was a deliberate deletion, not an
  overlooked case.
- `kinds_search.py:480-482` and `:709-711` each carry a doubled blank line
  after the docstring closes (pre-existing style, `E303` not selected in
  `pyproject.toml`'s ruff config) — cosmetic only, not part of either
  correction cycle's scope.

## Verdict

**PASS — 0 Critical, 0 Major.** All 4 Major findings from cycle 1 are
verified RESOLVED on the current HEAD (`31d8a29`), each checked against the
specific gap it named (not merely "a change exists nearby"): `CLAUDE.md`
now carries equivalent ADR-0113 orientation content and Contents-tree entry
to `AGENTS.md`; the JSON-integer boundary is pinned at the true
4096/4097-digit line against both restatements plus a direct literal-equality
assertion; the `max_candidates` cap is proven to refuse a
2**20-combination grid under `n_trials=1` before materialization, matching
the real call ordering in the source; and all four flagged Examples blocks
are now `>>>`-free and either correctly `::`-shaped or legitimately
removed. The cycle-2 diff is additive/corrective as required — nothing
previously approved was disturbed. No new architecture/governance finding
surfaced. This closes the bounded 2-cycle recovery for the
architecture/integration/governance lens.
