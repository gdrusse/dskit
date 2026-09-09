# Skeptic review — final-model replay Phase 1 method/API lens, v4

Reviewer task: /root/phase1_method_skeptic_v4
Model/effort: GPT-5.6 Terra, high
Dispatch: fresh sequential reviewer; no implementation edits.

## Reviewed state

Read root/pipeline AGENTS.md, skeptic-review Rules 1 and 7, ADR-0113
(docs/architecture/decision-log.md:6100-6135), the Phase-1 plan
(children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md:301-315), the complete uncommitted diff from c775ac5, and v1–v3. I independently attacked inventory/ledger identity, frozen rows and JSON hand-off, retry atomicity, canonical arrival-order ties, selector callbacks/comparisons, constructor refusals, Decimal/int/float/bool/non-finite inputs, planner parity, docs, and focused tests.

## Focused checks

/home/russell/dskit/.venv/bin/python -m pytest -q tests/pipeline/test_kinds_search.py
# 115 passed in 0.36s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py dskit/pipeline/planner.py dskit/pipeline/__init__.py tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean

Read-only WSL Decimal probes produced:

[1.0, 1.0]
{'depth': 1}

stored -0.0 1.0
winner {'depth': 1}

The first recorded depth 1 at Decimal(1.0000000000000000000002) and depth 2 at the strictly better Decimal(1.0000000000000000000001), both with zero SE; min selected the worse depth 1 after both values became 1.0. The second recorded se=Decimal(-1e-10000) and a Decimal diagnostic; it stored -0.0 and 1.0, then selected successfully.

## Findings

### Major — admitted Decimal evidence is silently rounded, including a negative SE accepted as zero

_frozen_number deliberately admits every non-bool object convertible by float() and returns that binary float (dskit/pipeline/kinds_search.py:290-302). record() applies it to both score and se (:610-615); _frozen_json does the identical conversion to score components and diagnostics (:268-278). This is not merely display precision: two distinct Decimal scores can collapse to a tie and choose the canonically earlier, objectively worse candidate; a finite negative Decimal SE can underflow to -0.0, bypass the required negative-SE refusal at :785-791. It also makes different Decimal evidence produce the same canonical ledger JSON/digest, contrary to ADR-0113's immutable complete evidence and deterministic encoding/digest contract (decision-log.md:6115-6125).

The API/tests explicitly advertise Decimal admission (tests/pipeline/test_kinds_search.py:234-251), so silently narrowing it is an accepted-input correctness defect, not an unsupported-type edge case. Preserve an exact numeric representation/ordering and canonical encoding, or refuse Decimal and other lossy numeric protocols before recording; in either case add pre-fix regressions for close-but-distinct Decimal scores, tiny negative Decimal SE, and differing Decimal diagnostics/digests. A rejected row must remain retryable.

### Minor — public class docstring points at a nonexistent plan path

CandidateInventory cites docs/plans/2026-09-08-final-model-replay-and-monitoring.md (dskit/pipeline/kinds_search.py:359-362), but the reviewed plan is under children/intraday_equities/docs/plans/. Correct the reference or remove the child-specific path from this generic public API documentation.

## Test-process evidence
