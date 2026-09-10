## Skeptic review — Gate 5a replay conformance/hook discovery (architecture lens)

**Reviewer task:** Gate 5a replay conformance / hook discovery, reviewer 2 of 2
**Model/effort:** Claude Sonnet 5
**Lens:** architecture / governance / tiering ONLY. Whether each of the six
items has a test that can fail is reviewer 1's lens (conformance/coverage;
already PASSed at cycle 4). This review does not re-litigate that; it hunts
for process, design, and boundary violations — scope creep beyond what Gate
5a's contract authorized, forbidden-file edits, tiering violations, §11
policy inference, and journal/path.csv governance.
**Dispatch mode:** sequential — reviewer 1's cycle-4 PASS (`f2dfe19`) landed
first; this is an independent reviewer-owned report over the same span, not a
paraphrase of it.

## Scope and commits reviewed

Base `95fe75d` (ADR-0114 accepted) through `HEAD` (`f2dfe19`, reviewer 1's
cycle-4 memo). Full span:

```text
git log 95fe75d..HEAD --format="%H %an <%ae> %s"
f2dfe19 Claude Sonnet 5 <noreply@anthropic.com>   docs(memos): Gate 5a cycle-4 conformance skeptic (PASS — 0 Major)
973387d Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai> test(production): Gate 5a cycle 3 — pin working/pending at intent
a5644ca Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai> docs(journal): Gate 5a replay conformance/hook discovery (A18856)
9ebdb97 Claude Sonnet 5 <noreply@anthropic.com>   docs(memos): Gate 5a cycle-3 conformance skeptic (FAIL — 1 Major)
b25159f Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai> test(production): Gate 5a cycle 2 — pin CURRENT Recovery metrics at intent
eb61aa0 Claude Sonnet 5 <noreply@anthropic.com>   docs(memos): Gate 5a cycle-2 conformance skeptic (FAIL — 1 Major)
c3ff034 Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai> test(production): Gate 5a cycle 1 — crash/restart, forced exit, ReplayFeed
9584bae Claude Sonnet 5 <Claude Sonnet 5@opencode.ai> docs(memos): Gate 5a replay conformance skeptic review (FAIL — 2 Critical, 2 Major)
fbefa5d Claude Sonnet 5 <noreply@anthropic.com>   test(production): Gate 5a replay conformance / hook discovery
```

**Governance positive, checked not assumed:** the implementer identity
(`Cursor Grok 4.6`) and the conformance reviewer identity (`Claude Sonnet 5`)
are distinct across every cycle — real separation of duties, not a
self-review loop. Two commits (`9584bae` cycle-1 FAIL, and this memo) show a
malformed author email (`Claude Sonnet 5@opencode.ai` has a literal space);
later cycles corrected to `noreply@anthropic.com`. Cosmetic — not a Critical
or Major, noted as a Nit below.

## Commands run and results

**1–4. Diff surface, path.csv, replay.py, no production hook:**

```text
$ git diff 95fe75d...HEAD --stat
 children/intraday_equities/docs/decisioning/README.md    |   2 +-
 children/intraday_equities/docs/decisioning/actions.csv   |   1 +
 docs/memos/...conformance-cycle2.md                       | 203 +++++
 docs/memos/...conformance-cycle3.md                       | 187 +++++
 docs/memos/...conformance-cycle4.md                       | 207 +++++
 docs/memos/...conformance.md                               | 216 +++++
 tests/production/test_clock.py                            |  24 +
 tests/production/test_executor.py                         | 175 ++++
 tests/production/test_feed.py                              |  33 +-
 tests/production/test_loop.py                              | 607 ++++++
 10 files changed, 1651 insertions(+), 4 deletions(-)

$ git diff 95fe75d...HEAD --name-status | grep '^A'
A  docs/memos/...conformance-cycle2.md
A  docs/memos/...conformance-cycle3.md
A  docs/memos/...conformance-cycle4.md
A  docs/memos/...conformance.md
# (only the four allowed conformance memos; no other new files)

$ git diff 95fe75d...HEAD -- '**/path.csv'
# (empty — path.csv untouched, both repo-root and child)

$ git show HEAD:dskit/production/replay.py
fatal: path 'dskit/production/replay.py' does not exist in 'HEAD'
$ find . -iname "replay.py" -o -iname "run-development-replay*"
# (no results anywhere in the tree)

$ git diff 95fe75d...HEAD --stat -- 'dskit/production/*.py' 'dskit/production/libs/*.py'
# (empty)
$ git diff 95fe75d...HEAD -- dskit/ children/intraday_equities/intraday_equities/ configs/ | wc -l
0
# every module named in the "smallest generic hook" clause individually:
$ for f in loop feed clock executor ledger accounting state records vocab; do \
    git diff 95fe75d...HEAD -- "dskit/production/$f.py" | wc -l; done
0 0 0 0 0 0 0 0 0
```

Zero bytes changed across the ENTIRE `dskit/` tree, the entire child domain
package, and `configs/`. Only 4 test files, 4 memos, and the journal
README/actions.csv were touched.

**8. Journal governance:**

```text
$ git diff 95fe75d...HEAD -- children/intraday_equities/docs/decisioning/
```

`actions.csv` gained exactly one appended row, `A18856` — `next_id = max + 1`
(`A18855` was the prior max), no id collision, append-only preserved.
`README.md`'s Actions table lost `A18846` from its head and gained `A18856`
at its tail: this is the documented "display only latest 10" behavior
(`AGENTS.md` §"Child decisioning"), not a deletion of history — `A18846`
remains present, unedited, in `actions.csv`. `journal.json` itself has a
zero-byte diff (it is a static `decisioning_dir`/`notes` pointer, not the
ledger). The commit message (`a5644ca`) and the row's own `Notes` field are
consistent word-for-word with what happened: "PASS-EXISTING on items 1-5 and
crash/restart...No production hook...Reviewer 2 not run. path.csv untouched;
replay.py not written." Nothing here reads as hand-edited outside the
journal's normal append shape.

**5. Domain-blindness of the new test code:**

```text
$ git diff 95fe75d...HEAD -- tests/production/test_{loop,executor,feed,clock}.py \
  | grep -iE "AAPL|MSFT|GOOG|schwab|PDT|wash.?sale|intraday_equities|ticker|nyse|nasdaq"
(no ticker/venue/broker/tax-rule hits)

$ ...| grep -iE "^\+" | grep -iE "import (children|intraday)"
(no child imports added)

$ ...| grep -iE "equit"
+    Which holding limit an equity adapter would choose is §11 policy
+    an equity adapter SHOULD refuse an overlapping signal is §11 policy
+# fixture below is an injected caller value, never an inferred equity
+    an equity adapter would then pick from what is visible is policy
+    the MECHANISM only — which bar an equity adapter would then choose
```

The five "equity" hits are all in docstring/comment prose explicitly
disclaiming policy scope ("this fixture does not decide", "§11 policy this
test does not decide") — not domain logic, thresholds, or tickers in
executable code. `test_purity.py`'s static "no venue names" AST check (the
same helper `dskit.pipeline.conformance` already uses) also ran and passed
inside the full suite below, which would have caught a literal broker/venue
string.

**11. Test/lint/whitespace:**

```text
$ python3 -m pytest -q tests/production/test_loop.py tests/production/test_feed.py \
  tests/production/test_clock.py tests/production/test_executor.py \
  tests/production/test_ledger.py tests/production/test_purity.py tests/production/test_oop.py
775 passed, 12 skipped in 15.89s

$ python3 -m ruff check tests/production/test_loop.py tests/production/test_executor.py \
  tests/production/test_feed.py tests/production/test_clock.py
All checks passed!

$ git diff --check 95fe75d...HEAD
(exit 0, no output)
```

**12. ADR-0114 Phase 5 and plan §6 Phase 5 items 1–3 versus what was built:**

`docs/architecture/decision-log.md:6420-6457` (Phase 5 entry) states
verbatim: *"This ADR does not name that hook — the plan requires the
conformance test to run FIRST (§6 Phase 5 item 1) to discover whether one is
even needed, and doing so is implementation work this ADR does not authorize
yet"* and names `replay.py` and `configs/run-development-replay.json` as
**future** work blocked on §11 items 5/6, not this lane's job. Plan
§6 Phase 5 (`docs/plans/2026-09-08-final-model-replay-and-monitoring.md:363-377`)
lists five items; this lane's own test-file comments map onto items 1
(no-lookahead mechanism), 2 (order/fill fidelity, rejection/partial-fill,
holding clock, forced exit, overlapping signals — each *pinned as CURRENT
mechanism only*, never as a new required policy), and 3 (crash/restart at
every named boundary). Plan items 4 (continuous solvency/caps assertions)
and 5 (rejected/unfunded observability) are NOT touched by this diff — the
journal row says so honestly ("items 1-5 and crash/restart"), and leaving
them for a later gate is a scope decision, not a defect under this lens
(coverage completeness is reviewer 1's call, already made). I checked for
Phase 6 (event/metric schema — `metrics.py`/`monitors.py`/`vocab.py`) and
Phase 7 (integration configs, `replay.py`, finalist HPO) leakage
specifically: zero diff to any of those files or configs, confirmed above.

## Findings

None that reach Major or Critical. One process Nit:

**Nit — malformed git author email on two commits.** `9584bae` (cycle-1 FAIL
memo) and this memo's own soon-to-be-committed metadata risk carry
`Claude Sonnet 5@opencode.ai` (a literal space in the local-part) versus the
corrected `noreply@anthropic.com` used from `fbefa5d`/`eb61aa0` onward. Not a
tiering, scope, or governance violation — cosmetic identity hygiene only,
does not affect attribution intent (the display name is still the correct
specific model) and does not touch the "every commit names the SPECIFIC
model" rule's substance.

## Items not found defective (checked, not assumed)

- **Forbidden-file boundary:** zero-byte diff, individually confirmed, across
  every module the gate named as off-limits or as the only place a hook
  could go (`loop.py`, `feed.py`, `clock.py`, `executor.py`, `ledger.py`,
  `accounting.py`, `state.py`, `records.py`, `vocab.py`, `monitors.py`,
  `metrics.py`, `cashflows.py`, `compose.py`, `sklearn.py`,
  `final_model.py`, `run-final-hpo.json`, `run-final-refit.json`) and across
  the entire `dskit/` tree, child domain package, and `configs/` directory.
- **No parallel backtest engine:** `replay.py` does not exist anywhere in
  the tree (base or HEAD); the only new integration test
  (`test_serve_loop_with_replay_feed_clock_paper_executor_and_ledger_is_deterministic`)
  constructs the REAL `ServeLoop`/`ReplayFeed`/`ReplayClock`/`PaperExecutor`/
  `JsonlLedger`/`LegPipeline`/`SimulatedAuthority` — no shadow engine, no
  duplicated tick/fold logic.
- **§11 items 5/6 not inferred:** every executor-item docstring
  (`test_executor.py:1731-1868`) explicitly disclaims policy ("GAP
  documented, not built"; "this task does not decide"; "§11 policy this test
  does not decide") rather than asserting a new required behavior. Item 4's
  `Position.opened_ms` gap is named as a gap, not silently worked around —
  the test derives holding duration from existing `Fill.ts_ms`/
  `OrderState.created_ms` instead of proposing a `records.py` field.
- **Tiering — test-local helpers stay test-local:** `AsOfBarDecider`,
  `_PassThroughGuards`, `_ReplayArming`, `_SimulatedTable`,
  `_ReplayAccounting`, `_OneFillProposer`, `_EmptyHistory`, `_SilentExecutor`
  and the `_a_*`/`_an_*` builder functions are all private (`_`-prefixed or
  file-local), subclass existing test fakes (`FakeProposer`, `FakeArming`,
  `FakeAccounting`) rather than reinventing them, and are never imported
  outside `test_loop.py`. No new `dskit` public API, no `__all__` addition,
  confirmed by the zero-diff on every production module above.
  `monkeypatch.setattr(loop_module, "LegPipeline", LegPipeline)` reuses the
  file's own pre-existing seam (`FakeLeg` monkeypatch at line 916, predating
  this gate) symmetrically to restore the real class for one integration
  test — not a new pattern, not a production edit (monkeypatch is
  test-process-local and reverts automatically).
- **No ADR-worthy design smuggled in:** nothing in the diff asserts a new
  required production behavior; every pin is framed as "CURRENT" behavior of
  code that already exists, falsifiable against a live import
  (independently re-verified by reviewer 1's cycle-4 memo via direct
  reproduction, not just re-cited here).
- **Journal/path.csv:** `path.csv` (repo-root and child) has a byte-identical
  diff of nothing; `actions.csv` gained one append-only row with a correctly
  incremented id; `README.md`'s change is the documented latest-10 display
  window sliding, not history deletion.
- **Reviewer dispatch sequencing:** implementer and conformance reviewer are
  distinct identities on every cycle (checked via `git log --format`, not
  assumed from commit message tone); this is reviewer 2, running after
  reviewer 1's cycle-4 PASS, per the stated sequential dispatch.
- **Suite health:** 775 passed / 12 skipped across
  `test_loop`/`test_feed`/`test_clock`/`test_executor`/`test_ledger`/
  `test_purity`/`test_oop`; ruff clean on all four touched test files;
  `git diff --check` clean (no whitespace/conflict-marker damage) over the
  full `95fe75d...HEAD` span.

## Disposition

Zero Critical, zero Major, one cosmetic Nit (a malformed author email on one
early commit, unrelated to scope or tiering). Every boundary this lane was
told to respect — no production `.py` edit anywhere in `dskit/`, no
`replay.py`, no `run-development-replay.json`, no `path.csv` edit, no §11
policy inference, no parallel backtest engine, no child domain leakage into
synthetic tests, no forbidden-file edit, journal append-only intact,
test-local helper tiering intact — was independently confirmed by direct
command output, not inferred from the commit messages or reviewer 1's memo.
This lens's bar (zero Critical, zero Major) is met.

**Verbatim verdict:** `PASS — 0 Critical, 0 Major, 1 Nit.`
