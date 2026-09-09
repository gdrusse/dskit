# Skeptic review - final-model replay Phase 1 method/API lens, v3

Reviewer task: /root/phase1_method_skeptic_v3
Model/effort: GPT-5.6 Terra, high
Dispatch: fresh sequential reviewer; no implementation edits.

## Reviewed state

Read root/pipeline AGENTS, skeptic Rules 1 and 7, ADR-0113
(docs/architecture/decision-log.md:6100-6135), plan Phase 1
(children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md:301-315),
full uncommitted diff from c775ac5, and v1/v2.

## Focused checks

    /home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
    # 110 passed in 0.35s

    /home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py dskit/pipeline/planner.py dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
    # All checks passed!

    git diff --check
    # clean

Read-only WSL probes: mutating a hashable object after recording it inside
diagnostics={item} changes the frozen row; identical evidence recorded in depth
order [1,2] versus [2,1] selects winners (1,) versus (2,); json.dumps(ledger.rows)
raises TypeError: Object of type mappingproxy is not JSON serializable; and a
finite float subclass whose __le__ returns False leaks IndexError: list index out
of range at kinds_search.py:743.

## Findings

### Major - accepted ledger rows can still mutate

ADR-0113 requires one immutable row per candidate (decision-log.md:6115-6117).
record uses non-strict _freeze(score) (kinds_search.py:518-520), retaining an
arbitrary mutable score by reference. The strict extra path turns a set directly
into a frozenset without inspecting members (:241-244); hashable mutable members
are legal in Python. Mapping keys are also neither frozen nor validated
(:235-238). The mappingproxy shell therefore does not prevent historical
score/diagnostics/key mutation. Enforce one recursive admitted evidence grammar
(including keys and set members) or reject unfreezable values transactionally;
test mutable score, set member, mapping key, and retry after refusal.

### Major - deterministic inventory does not produce deterministic ties

record appends arrival order (kinds_search.py:545); both score and simplicity ties
use first-recorded/eligible order (:691-695, :743-757). Different concurrent
completion order produces different winners. This violates ADR-0113's explicit
non-deterministic-winner risk (decision-log.md:6105-6110) and Phase 1's
deterministic simplicity rule. Keep arbitrary transactional ingestion, but select
in canonical inventory rank (or expose canonical rows), with both arrival
permutations tested.

### Major - no canonical ledger JSON/digest boundary exists

ADR-0113 promises deterministic JSON encodings and digests
(decision-log.md:6123-6125); Phase 1 requires the complete ledger and inventory
digest (plan:312-314). Only inventory digest and ledger inventory_digest exist
(kinds_search.py:390-407, :469-482); rows contain mappingproxies (:533-544) and
are not JSON serializable. Callers must reimplement owner serialization/hash
logic to produce the required artifact. Add canonical JSON-safe ledger data and
digest, including inventory identity and canonical complete evidence, or amend
ADR; test stable bytes/digest and changed-evidence sensitivity.

### Major - admitted numeric can leak raw IndexError

The selector admits duck-typed finite numerics (kinds_search.py:592-638) and
catches comparison exceptions (:640-665) but does not assert that its best row
is eligible. A finite float subclass with __le__ returning False makes the list
at :705-708 empty, then leaks IndexError at :743. Normalize/reject inconsistent
numeric comparison behavior and at least raise a candidate-naming ValueError;
add the probe with finite/NaN/inf/bool/overflow cases.

### Minor - score/SE numeric policy is inconsistent

The selector intentionally supports non-builtin finite numerics, but se is a
strict extra: Decimal(1) is accepted as score and rejected as SE (:519-520,
:243-244). Define one stored numeric grammar and test score/SE parity.

## Verdict

FAIL - 0 Critical, 4 Major, 1 Minor. Focused tests/style/diff hygiene pass, but
immutable evidence, deterministic winner selection, JSON/digest hand-off, and
one-SE error handling fail. Fix Majors with focused regressions, then use a new
fresh skeptic; this is the Rule-7 reviewer trace.
