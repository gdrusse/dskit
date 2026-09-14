# Q2 — F2 deferred lint nits closeout (2026-09-14)

## Outcome

Packet Q2 re-inventoried the F2 deferred lint findings and fixed the two still
present, closed with two clean final lenses, and merged to main. No behavior
changed; no test was invented for the lint-only change; the accepted F2 slice
contract (ReviewExit `docs/review-evidence/F2/0011-review-exit.v1.json`) was not
reopened.

## Candidate

- Base: `390dc851e3704e8db4d2a6a4d4e7aa0412a69899` (`origin/main`).
- Candidate (code): `25f3f2ca449d49e0859b3c6e64bd0b5d5d56c1ec`.

## Changes

1. **D202 (editorial)** — `dskit/pipeline/__main__.py:249`: removed the blank
   line after the `_legacy_validate` function docstring.
2. **F401 (semantic import removal)** — `dskit/pipeline/stages.py:16`: removed
   the unused `PipelineDocument` name from
   `from dskit.pipeline.document import PipelineDocument, parse_node_ref`.

Import/behavior compatibility proof for the F401 removal:

- `PipelineDocument` is unreferenced anywhere else in `stages.py` (only the
  import line).
- It is absent from `stages.__all__`, and no module repo-wide imports
  `PipelineDocument` from `dskit.pipeline.stages`.
- `parse_node_ref` remains imported from `dskit.pipeline.document` and is used
  at `stages.py` lines 269 and 384, so `document.py` is still imported at module
  load — no import side effect is dropped.
- The star-import boundary `from dskit.pipeline.stages import *` is unchanged:
  `set(__all__) <= namespace` holds and `PipelineDocument` is not leaked.

## Model / reviewer attribution

- Primary implementer: **deepseek-v4-pro** (`opencode-go/deepseek-v4-pro`),
  default reasoning.
- Reviewers: **gpt-5.6-luna** (`opencode-go/gpt-5.6-luna`), reasoning high,
  two fresh independent instances, sequential.

**Blocker recorded (owner-approved substitution):** the required reviewer model
`gpt-5.6-terra` is unavailable in this environment — `opencode models` lists
only `opencode-go/gpt-5.6-luna` in the gpt-5.6 family, and a
`--model opencode-go/gpt-5.6-terra` invocation returns a server error while
`gpt-5.6-luna` resolves. The owner authorized substituting `gpt-5.6-luna`
(reasoning high) for both final lenses; this substitution is recorded here as
required by the "do not substitute another reviewer model without recording a
blocker" policy.

## Final review outcomes

Both lenses ran sequentially on candidate `25f3f2c`, each a fresh gpt-5.6-luna
instance, reasoning high.

- **Lens 1 — correctness/authority:** PASS — 0 Critical / 0 Major / 0 Minor /
  0 Nit. Verified the two changes touch only the named files; `git diff --check`
  clean; no `stages.PipelineDocument` consumer exists; import/AST probes confirm
  `parse_node_ref` remains imported and used; focused pytest 34 passed; Ruff
  clean.
- **Lens 2 — test-quality/integration:** PASS — 0 Critical / 0 Major / 0 Minor /
  0 Nit. Verified no unnecessary test was added (scope/`tests/` audit clean),
  Ruff clean, 61 focused stages/CLI tests passed, star-import boundary holds,
  and a full-suite run is correctly unnecessary per plan Q2.

Zero unresolved in-scope Critical/Major findings.

## Commands / results (implementer, WSL2)

```bash
# interpreter
/home/russell/dskit/.venv/bin/python   # Python 3.12.3
/home/russell/dskit/.venv/bin/ruff     # ruff 0.16.5
cd /home/russell/wt/q2-lint-20260914   # PYTHONPATH=$PWD

PYTHONPATH=$PWD /home/russell/dskit/.venv/bin/ruff check \
  dskit/pipeline/__main__.py dskit/pipeline/stages.py        # All checks passed!
PYTHONPATH=$PWD /home/russell/dskit/.venv/bin/python -m py_compile \
  dskit/pipeline/__main__.py dskit/pipeline/stages.py        # OK
git diff --check                                              # clean
```

## Limits

- Lint-only maintenance packet; no TDD/behavior test (correct per plan Q2).
- No full suite run (correct; touched code is two lint lines).
- Does not reopen the accepted F2 contract; no whole-slice ReviewExit emitted.
- Reviewer-model substitution recorded as a blocker (gpt-5.6-terra unavailable).

## Next

Packet 2 D0 — replay ADR/plan reconciliation from
`origin/codex/r5-replay-ops-20260911` @ `97900efd66bfed44cc403c567c04c4e383c05c24`.
