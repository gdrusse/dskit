# Skeptic review — final-model replay Phase 1 architecture/integration/governance lens, recovery verification

**Reviewer task:** the second, independent bounded reviewer required to close
out the ADR-0113 Phase-1 recovery — architecture/integration/governance lens
(distinct from the method/API-contract lens already re-reviewed and passed in
`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-method-recovery.md`).
**Model/effort:** Claude Sonnet 5, high.
**Dispatch:** fresh sequential architecture/governance review of the full
Phase-1 diff (`c775ac5..HEAD`) against root `CLAUDE.md` and
`dskit/pipeline/CLAUDE.md`; no implementation edits made.

## Scope and evidence

Read root `CLAUDE.md` and `dskit/pipeline/CLAUDE.md` in full (the review
lens); the complete `git diff c775ac5..HEAD` over `dskit/pipeline/kinds_search.py`,
`dskit/pipeline/README.md`, `dskit/pipeline/AGENTS.md`,
`docs/architecture/decision-log.md`, `tests/pipeline/test_kinds_search.py`,
`dskit/pipeline/__init__.py`, `dskit/pipeline/planner.py`; ADR-0113's full
current text; the complete current `dskit/pipeline/README.md` and
`dskit/pipeline/AGENTS.md`; and the prior architecture-lens FAIL report
(`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-architecture.md`,
GPT-5.6 Terra, 1 Major: no `max_candidates` cap) to check its required
correction actually landed with its own required regression.

```text
$ python3 -m pytest -q tests/pipeline/test_kinds_search.py tests/pipeline/test_planner.py \
    tests/pipeline/test_purity.py tests/pipeline/test_method_lengths.py
187 passed in 7.28s

$ python3 -m ruff check dskit/pipeline/kinds_search.py tests/pipeline/test_kinds_search.py
All checks passed!

$ git diff --check
(clean, exit 0)

$ git diff c775ac5..HEAD --stat -- docs/decisioning/path.csv dskit/journal
(no output — neither touched)
```

Also ran a direct probe of the `max_candidates` cap the prior architecture
round required (`n_trials=1` over a 2**20-combination space,
`max_candidates=100`): refused correctly (`candidate count 128 exceeds
max_candidates 100`) — the underlying mechanism works. Grepped the full test
tree for `max_candidates` to check the prior round's required regression was
added: zero matches anywhere under `tests/`.

## Findings

Four Major findings, no Critical. None revisit the method-lens's three
already-closed findings (SelectionRecord construction, simplicity ordering,
TrialLedger concurrency contract) — this pass independently confirms those
are sound (tiering, encapsulation, no duplication) and instead surfaces four
governance/consistency gaps specific to this lens.

### Finding 1 (Major) — `dskit/pipeline/AGENTS.md` and `dskit/pipeline/CLAUDE.md` no longer match, violating the repo's own enforced sibling-pairing rule

`.cursor/hooks/check-agent-doc-pairs.sh:21-37` encodes a standing repo rule:
"AGENTS.md and CLAUDE.md must change together" — any diff touching one
without the other is flagged. This diff adds three new bullets to
`dskit/pipeline/AGENTS.md:409-428` ("Generic search values",
"TrialLedger's concurrency contract is public, not incidental",
"SelectionRecord has no public constructor") and a two-line Contents-tree
addition at `dskit/pipeline/AGENTS.md:493-494` — all ADR-0113 orientation
content a future agent needs. `git diff c775ac5..HEAD --stat --
dskit/pipeline/CLAUDE.md` is **empty**: `dskit/pipeline/CLAUDE.md` was not
touched at all. Confirmed directly: `dskit/pipeline/CLAUDE.md`'s "Extension
points" section has no "Generic search values" (or equivalent) entry, and its
Contents tree's `kinds_search.py` line (CLAUDE.md:468) still reads only
`hpo-grid + top-trials (ctx.rerun seam)`, unlike AGENTS.md's now-longer
entry. This is exactly the "keep both trees current" rule (root `CLAUDE.md`,
"Every package ships its own docs") failing for the SECOND of the package's
two agent-orientation docs — an agent working from `CLAUDE.md` (e.g. Claude
Code sessions, which read `CLAUDE.md` not `AGENTS.md`) has no discoverable
pointer to `CandidateInventory`/`TrialLedger`/`OneStandardErrorSelector`/
`SelectionRecord` at all.

**Required correction:** port the three new bullets and the two-line
Contents-tree addition from `dskit/pipeline/AGENTS.md:409-428,493-494` into
the equivalent locations in `dskit/pipeline/CLAUDE.md`, verbatim or reworded
to fit CLAUDE.md's voice, so the two files stay siblings per the hook's rule.

### Finding 2 (Major) — the cross-module JSON-integer-scalar pinning test does not cover the new duplicated 4096-digit bound it exists to protect

`dskit/pipeline/CLAUDE.md`'s own gotcha ("A search `space` value's grammar is
SPLIT...") already documents that `_is_json_scalar` is deliberately restated
in two tier-boundary-separated modules and says "the two pinned to agree" —
referring to `tests/pipeline/test_kinds_search.py::TestScalarRuleAgreement`
(`:1016-1047`). This diff adds a new bound to BOTH restatements
independently: `planner.py:67` and `kinds_search.py:70` each define
`_MAX_JSON_INT = 10**4096 - 1` as an unshared literal (planner may not import
a tier-2/tier-3 module per the tiering rule, so the duplication itself is the
established, deliberate pattern — CLAUDE.md, "Duplication that diverges").
But `TestScalarRuleAgreement.CASES` (`:1023-1039`) was not extended: it has
no case anywhere near the 4096-decimal-digit boundary (no valid 4096-digit
int, no refused 4097-digit int), and nothing else in `tests/pipeline/`
exercises this boundary either (confirmed: no other match for
`_MAX_JSON_INT`/`4096` in `tests/pipeline/*.py`). Per root `CLAUDE.md`,
"Duplication that diverges": "If a value MUST appear twice, PIN the
agreement with a test... A pinning test that omits a knob is worse than
none — it claims coverage it lacks." The test class's own docstring states
its exact purpose: "A document that passed the plan-time check and then died
at the kind's construction check (or the reverse) would be this rule having
two meanings; the two refusals must be the same refusal" — and that is
precisely the scenario this gap leaves unproven for the newest rule the two
functions share.

**Required correction:** add at least one case pair to
`TestScalarRuleAgreement.CASES` at the boundary (e.g. an int with exactly
4096 nines → `True`, and one with 4097 digits → `False`), asserted against
both `_planner_is_json_scalar` and `_grid_is_json_scalar`; ideally also
assert `dskit.pipeline.planner._MAX_JSON_INT ==
dskit.pipeline.kinds_search._MAX_JSON_INT` directly so a future edit to
either literal alone fails immediately rather than only at the boundary.

### Finding 3 (Major) — the max_candidates regression explicitly required by the prior architecture FAIL verdict was never added

The first architecture-lens skeptic round (GPT-5.6 Terra,
`docs/memos/2026-09-09-final-model-replay-phase1-skeptic-architecture.md`)
FAILED this same code (0 Critical, 1 Major) for `CandidateInventory`
materializing the full Cartesian grid before `n_trials` was consulted, with
an explicit two-part required correction: "add an explicit positive,
non-boolean caller cap... **Pin the cap with a regression that proves
`n_trials=1` cannot bypass it.**" The cap itself now exists and works
(`kinds_search.py:103-112` `_bounded_candidate_count`, called at `:583`
before `_grid`/`_subsample`; verified directly: a 2**20-combination space
with `max_candidates=100, n_trials=1` still refuses with "candidate count 128
exceeds max_candidates 100"). But `grep -rn "max_candidates"
tests/pipeline/` returns **zero matches** — there is no regression anywhere
in the suite that constructs a `CandidateInventory` with a non-default
`max_candidates`, that proves the cap is enforced, or that proves `n_trials`
cannot bypass it. The first half of the required correction landed; the
second half — the pin the FAIL verdict named explicitly, precisely because
an un-pinned resource bound is "a scheduled bug" per CLAUDE.md's "Never
hardcode what could change" — did not.

**Required correction:** add a regression to `tests/pipeline/test_kinds_search.py`
(e.g. beside `TestPhaseOneEvidence`) that constructs a space whose full
Cartesian product exceeds a small explicit `max_candidates`, passes a small
`n_trials`, and asserts `ValueError` is raised before/without materializing
combinations — matching the exact scenario the original FAIL verdict named.

### Finding 4 (Major) — new Examples docstrings in this diff violate the house docstring standard in the exact way the standard calls out by name

Root `CLAUDE.md`'s docstring standard is explicit and applies to "New code...
No retrofit sweep" — `CandidateInventory`, `TrialLedger`,
`OneStandardErrorSelector`, and `SelectionRecord` are all new in this diff,
so the standard is not grandfathered here even though `kinds_search.py` sits
in ruff's `per-file-ignores` "PRE-STANDARD MODULES" `D`-suppression list
(`pyproject.toml:177`) — a file-granular suppression `CLAUDE.md` itself notes
does not distinguish new from old code within a file. Two concrete
violations:

- `CandidateInventory`'s class docstring (`kinds_search.py:473-477`) and its
  `max_candidates` property docstring (`:607-610`) both use `>>>`:
  ```
  Examples
  --------
  >>> inventory = CandidateInventory({"depth": [1, 2]}, max_candidates=2)
  >>> inventory.combinations[0]["depth"]
  1
  ```
  CLAUDE.md: "Examples are illustrative and must NEVER use `>>>`... `>>>`
  promises a suite verified it, and nothing here collects doctests." (No
  `--doctest-modules` is configured in `pyproject.toml`, confirmed by grep —
  so this is a pure documentation-standard violation, not a silently-broken
  doctest — but the rule itself is unambiguous and violated verbatim.)
- `TrialLedger`'s Examples block (`:705-709`) and `OneStandardErrorSelector`'s
  (`:1184-1189`) both skip the required "sentence ending in `::`, blank line,
  then a MORE-indented code block" shape the standard's own template
  demonstrates — their code lines sit at the same indentation as the
  `Examples`/`--------` heading, with no introducing `::` sentence.
  `SelectionRecord`'s Examples block (`:970-978`) gets this right (intro
  sentence + `::` + indented block + a correctly `# ->`-marked output line),
  showing the correct shape was available in the same file and simply not
  applied to the other three.

**Required correction:** rewrite the four Examples blocks above to the
template in root `CLAUDE.md` (indented `::` block, no `>>>`, expected output
marked with `# ->` or a same-line trailing comment) — `SelectionRecord`'s
existing block is the in-file model to copy.

## Checks that passed

- **Tiering.** `kinds_search.py`'s only imports are `hashlib`, `itertools`,
  `json`, `math`, `re`, `types`, `threading`, `collections.abc.Mapping`, and
  three intra-package tier-1 modules (`base`, `document`, `kinds_stats`,
  `node`) — stdlib-only, no domain/finance content anywhere.
  `tests/pipeline/test_purity.py` (18 tests) passes.
- **OOP pillars / immutability idiom.** `SelectionRecord`'s
  always-raising-`__init__` + `_build`/`_seal` classmethods constructing via
  `object.__new__` is a sound, deliberate "private constructor" idiom: `_build`
  still routes attribute assignment through the SAME guarded `__setattr__`
  every other sealed value in this file uses (`getattr(self, "_sealed",
  False)` check) — it is not a parallel or inconsistent construction path,
  just one entered via `object.__new__` instead of `cls(...)` because the
  public `__init__` is deliberately inert. `CandidateInventory` and
  `TrialLedger` use the same `__slots__` + `_sealed` + guarded `__setattr__`
  shape directly. Reasonable given the file's existing conventions; no
  cleaner alternative stands out.
- **Encapsulation boundary.** `_build`, `_seal`, `_ordering_key`,
  `_candidate_obj`, `_SELECTION_RECORD_FIELDS`, `_DIGEST_RE` are all
  underscore-prefixed, none appear in `__all__` (`kinds_search.py:56-64`),
  and `dskit/pipeline/__init__.py`'s new export line only re-exports the four
  public names (`CandidateInventory`, `TrialLedger`, `OneStandardErrorSelector`,
  `SelectionRecord`) plus the pre-existing `HpoGrid`. Only the test file
  reaches into internals (`_is_json_scalar`, `_ordering_key`), which is the
  established allowance.
- **No accidental duplication elsewhere.** `CandidateInventory` reuses the
  module's own `_grid`/`_subsample` (the same functions `HpoGrid` uses)
  rather than re-deriving grid/subsample logic — one canonical
  freeze-and-hash path, exactly as ADR-0113 claims. `SCHEMA_VERSION = 1` has
  one owner (`SelectionRecord.SCHEMA_VERSION`); `_seal` reads it via
  `cls.SCHEMA_VERSION`, never a second literal.
- **ADR discipline.** ADR-0113 (`decision-log.md:6100-6180`) was added whole
  in this diff (no pre-existing text to amend awkwardly); the "Phase 1
  recovery, 2026-09-09" paragraphs read as an integrated continuation of the
  Decision section (each names which skeptic-review Major it resolves), not
  a bolted-on patch. Checked prose against code clause-by-clause: every
  specific claim (nine-field schema, digests computed from the ledger,
  `max_candidates` recorded in the inventory digest, `n_trials` never
  bypassing it, negative-zero canonicalization, at-most-4096-decimal-digit
  integers) matches the current implementation. The **Scope** paragraph
  explicitly excludes empirical search, HPO, replay, market-data read, and
  child decisioning — confirmed nothing in the diff reaches into
  `children/` or adds domain-specific logic.
- **Config/JSON identity.** No touch to `document.py`,
  `NON_IDENTITY_SECTIONS`, `NULLED_IDENTITY_SECTIONS`, or any pipeline
  document's canonical hash recipe — `kinds_search.py`'s own digests
  (`CandidateInventory.digest`, `TrialLedger.digest`,
  `SelectionRecord.digest`) are a self-contained sha256-over-canonical-JSON
  scheme (sorted keys, compact separators, `allow_nan=False` on the two
  digests computed after values have already passed finite-only validation)
  unrelated to and non-interfering with document identity hashing.
- **Test-file architecture / thread-safety-test risk.** The three new
  regression classes for the already-closed method-lens findings
  (`TestSelectionRecordSchema`, `TestSimplicityOrderingTotalOrder`,
  `TestTrialLedgerConcurrencyContract`) are reasonably grouped and clearly
  documented against the specific Major they pin. `TestPhaseOneEvidence` and
  these three sit ahead of the file's own `# ---` section-banner structure
  (unit → planner → end-to-end → seam contract) without a banner of their
  own — a minor navigability nit, not a misplacement, since they are
  logically the file's most fundamental unit-level tests (the value types
  underneath the node kinds the rest of the file tests) and this pass found
  no case that actually belongs in a different layer.
  `TestTrialLedgerConcurrencyContract::test_a_reader_never_observes_a_pending_row_as_committed`
  uses `time.sleep(0.02)` against an artificially widened `time.sleep(0.1)`
  freeze window (5x margin) — a real but small flakiness surface under heavy
  CI contention; not severe enough to require a fix, worth knowing about.
- **Scope discipline.** Nothing in this diff is equity/finance-specific;
  `HpoGrid`/`TopTrials` (pre-existing) are unchanged in behavior beyond the
  cosmetic error-message trims mirrored in `planner.py`.
- **Journal/RE-ENTRY/decisioning.** `git diff c775ac5..HEAD --stat` touches
  no path under `docs/decisioning/` or `dskit/journal/` — confirmed clean.

## Minor observations (non-blocking, not counted toward the verdict)

- The plain-ASCII `|` (vs. box-drawing `│`) used for the new two-line
  Contents-tree continuation in `dskit/pipeline/README.md:74-75` and
  `AGENTS.md:493-494` was already flagged as a non-blocking cosmetic item by
  the method-lens recovery report; still true, not re-counted here.
  `dskit/pipeline/CLAUDE.md`'s Contents tree has no such line at all
  (Finding 1 covers that).
- `dskit/pipeline/__init__.py`'s new `kinds_search` import line
  (`:149`, 127 chars) is long relative to the rest of the file's import
  block, though the repo enables no `E501`/line-length rule
  (`pyproject.toml` `[tool.ruff.lint]` `select = ["E4", "E7", "E9", "F",
  "D"]`) so this is not a lint violation, just a readability nit.

## Verdict

**FAIL — 0 Critical, 4 Major.** Findings 1 and 4 are documentation-standard
gaps confined to comments/docstrings (mechanically small fixes). Findings 2
and 3 are both instances of the same underlying governance failure mode this
repo's CLAUDE.md names explicitly — an un-pinned duplicated value / an
un-pinned resource bound — and Finding 3 in particular means a FAIL verdict
from an earlier independent architecture review was only partially resolved
against its own stated acceptance criteria, even though the runtime behavior
it protects is currently correct. None of the four findings revisit or
contradict the method-lens recovery's PASS; they are additive
architecture/governance gaps this lens is specifically tasked to catch.
Required corrections are named per-finding above; re-run a fresh independent
architecture/governance pass after they land.
