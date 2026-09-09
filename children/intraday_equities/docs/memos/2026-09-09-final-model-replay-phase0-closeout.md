# Final-model replay Gate 1 — Phase 0 closeout

## TL;DR

This is a paperwork gate, not a build. It writes the one ADR the plan requires
before any new code, checks the existing HPO config's shape (still the old,
wrong recipe — as expected), and fixes a small documentation gap. No model
trained, no market data touched, and none of the plan's ten open owner
decisions were answered — they are restated, not resolved, so every later
phase they gate stays blocked until the owner rules on them.

## Execution contract

This is Gate 1 ("Phase 0 closeout") of the seven-gate plan built on top of
`docs/plans/2026-09-08-final-model-replay-and-monitoring.md`. Phase 1 of that
plan (generic, domain-blind search-evidence values in
`dskit/pipeline/kinds_search.py`) was already delivered and reviewed under
ADR-0113 earlier the same day, on the same branch
(`claude/phase1-recovery-seven-gates-ao4zdj`); this session's job was to
reconcile that delivery against the plan and produce the plan's required
Phase 0 artifact — one ADR proposal covering Phases 2 through 6 — plus a
narrow set of read-only companion artifacts. Nothing here is Phases 2–6
implementation.

No implementation `.py` file was created or modified. No new config `.json`
file was created. No pipeline `run` or `walkforward` command was executed. No
market data, HPO run, training run, or replay execution occurred. The only
pipeline commands run were `validate` and `plan` against one already-existing
config file, both read-only.

## What Gate 1 covered

1. Read the master plan in full, root `CLAUDE.md`, `dskit/pipeline/CLAUDE.md`,
   this child's `AGENTS.md`/`CLAUDE.md`, and ADR-0113.
2. Reconciled ADR-0113's delivered `kinds_search.py` objects
   (`CandidateInventory`, `TrialLedger`, `OneStandardErrorSelector`,
   `SelectionRecord`) against what the plan's §5 asked that file to contain.
3. Wrote ADR-0114 in `docs/architecture/decision-log.md`, status
   **"proposed — awaiting owner approval"**, covering every file the plan's
   §5 names for Phases 2 through 6 (generic `dskit` side and child side),
   their proposed classes/functions/parameters as far as the plan itself
   specifies them, their outputs/schemas/identity effects, and which of the
   plan's §11 open items block each phase from being implemented.
4. Captured current, read-only baseline evidence for
   `configs/run-final-hpo.json` (below).
5. Fixed a documentation-pairing gap between this child's `AGENTS.md` and
   `CLAUDE.md`.
6. Recorded this action through `dskit.journal` and regenerated the
   decisioning README from the CSV.

## ADR-0114 summary

**File:** `docs/architecture/decision-log.md`
**Status line:** `proposed — awaiting owner approval (2026-09-09)`

ADR-0114 is the plan's required Phase 0 cross-layer ADR. For each of the
plan's Phases 2–6 it names, from the plan's §5 alone (never invented):

- every new or modified file, generic and child-side;
- the proposed public classes/functions and parameters, exactly as far as
  the plan specifies them — every place the plan is silent on a name,
  signature, or default is called out explicitly rather than guessed;
- outputs, artifact/event schemas, and identity/hash effects the plan
  describes;
- which of the plan's ten §11 open owner decisions block that phase from
  being **implemented** (as opposed to merely designed).

It resolves **zero** of the plan's ten §11 items — they are restated
verbatim as an "Open owner decisions" section, exactly as the plan requires
("No agent may infer these"). It also carries the plan's §12 owner-only Path
packet as an appendix, explicitly labeled that this ADR is **not** authorized
to apply any of it to `docs/decisioning/path.csv`.

One reconciliation finding: ADR-0113's `kinds_search.py` delivery matches the
plan's generic-mechanism ask exactly, but the plan's own Phase 1 test list
also implies domain-side evidence (a real per-lead LightGBM objective feeding
the ledger) that cannot live inside a domain-blind file. That half of Phase 1
is still open and is folded into Phase 2's `final_model.py` in ADR-0114,
since refit follows selection in that same file. This is not a defect in
ADR-0113 — it built exactly what the plan's §5 asked of `kinds_search.py`
and nothing more.

## Baseline evidence: `configs/run-final-hpo.json`

Plan §4 item 1: "`run-final-hpo.json` still describes the pre-P16
full-feature recipes and cannot select/train the exact lean candidate." Ran
read-only from `children/intraday_equities` on 2026-09-09. The child package
is not `pip install`-ed in this environment; the working invocation needed
`PYTHONPATH` pointing at the repo root and the child directory (following the
pattern of the child's documented `--adapter intraday_equities` commands in
`README.md`).

```
$ cd children/intraday_equities
$ PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m dskit.pipeline validate configs/run-final-hpo.json --adapter intraday_equities
OK — configs/run-final-hpo.json
  name:  final-hpo
  nodes: 8  sections: splits, outputs, tracking, stages
  hash:  ee674709be49f5865d4a77548bb2b963091181ff67a30e62379b2e69b072cc48
```

Exit code: `0`.

```
$ PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m dskit.pipeline plan configs/run-final-hpo.json --adapter intraday_equities
{
  "name": "final-hpo",
  "document_hash": "ee674709be49f5865d4a77548bb2b963091181ff67a30e62379b2e69b072cc48",
  "order": ["calendar", "select", "memory", "finalist"],
  "stages": {
    "calendar": {"class": "dskit.pipeline.program_calendar:ProgramCalendar", "inputs": {}},
    "select": {"class": "dskit.pipeline.benchmarks:BenchmarkSelect", "inputs": {}},
    "memory": {"class": "intraday_equities.modelability_study:MemoryPreflightStage", "inputs": {}},
    "finalist": {
      "class": "intraday_equities.model_zoo:FinalistCandidate",
      "inputs": {"preflight": "$memory.passed", "caches": "$memory.groups", "selection": "$select.selection"}
    }
  },
  "edges": [["memory", "finalist"], ["memory", "finalist"], ["select", "finalist"]]
}
```

Exit code: `0`.

**Interpretation.** Both commands succeeded — the document is structurally
valid and its identity hash (`ee674709…`) matches the value already recorded
in this child's `CLAUDE.md`/`AGENTS.md` ("Constructed and validated
... it has NOT been run"). This confirms shape only, not recipe correctness:
the document's `pipeline` section still builds the full pre-P16 feature set
(20-bar lookback, five momentum-horizon windows, `ic`-objective inner search)
and its `finalist.templates` still carry the pre-lean-mask LightGBM/torch-MLP
recipes, not the P16 33-column-drop `lean-pooled-h10` mask or the plan's
locked 24-combination inventory. This is exactly the gap plan §4 item 1
names; no execution was needed to confirm it, and none was run.

## Documentation-pairing fix

`children/intraday_equities/AGENTS.md` carries a "child rules" bullet —
"**Implementation plans live in `docs/plans/`.**" — and a `docs/plans/` line
in its Layout tree that `CLAUDE.md` was missing entirely. Ported both into
`CLAUDE.md`, reworded to match its existing voice, matching the pattern
already used to keep `dskit/pipeline/CLAUDE.md` paired with its README during
the Phase 1 recovery. Files touched:

- `children/intraday_equities/AGENTS.md` — unchanged (source of truth).
- `children/intraday_equities/CLAUDE.md` — added the matching bullet and tree
  line.

## What did not happen

- No Gates 2–6 (the plan's Phases 2–6) implementation code was written.
- No new `.py` file was created anywhere in `dskit/` or
  `children/intraday_equities/`.
- No new config `.json` file was created.
- No market data was read.
- No pipeline command beyond read-only `validate`/`plan` on the one existing
  `configs/run-final-hpo.json` was executed — no `run`, no `walkforward`.
- No §11 item was resolved, answered, or inferred.
- `docs/decisioning/path.csv` was not touched — not read, not edited,
  character-for-character unchanged. The Path packet from plan §12 is
  recorded in ADR-0114 as owner-only reference content only.

## Open owner decisions and what they block

All ten of the plan's §11 items remain open; none is answered here. Per
ADR-0114:

| §11 item | Blocks |
|---|---|
| 1. One-standard-error unit + simplicity order | Phase 2 `final_model.py` per-lead winner selection |
| 2. Biweekly Friday anchor + 09:30 ordering | Phase 3 `configs/capital-policy.json` real values, same-timestamp ordering test |
| 3. Inverse-label reference policy | Phase 4 `forecast_bundle.py` core conversion |
| 4. March-May ordering/minimum evidence | Phase 4 cap confirmation; `configs/run-mean-confirmation.json` stays unreadable |
| 5. First replay horizon policy | Phase 5 `replay.py` exit/expiry/overlap semantics |
| 6. Fill model | Phase 5 `replay.py` fill/latency/slippage policy |
| 7. Performance-monitor thresholds | Phase 6 warn-vs-hold behavior only (metric recording itself is not blocked) |
| 8. Open MIO policies (referenced plan §10) | Phase 4 `nodes_capital.py` cap enforcement interaction with MIO |
| 9. Broker/tax/settlement/PDT/wash-sale/live | Phase 5 real Schwab cost/fill parameters (mechanism may still be built/tested synthetically) |
| 10. Reporting/alert requirements beyond §6 catalogue | Phase 6 reporting/alerting beyond the named catalogue |

## Reproducibility and handoff

- ADR: `docs/architecture/decision-log.md` (ADR-0114, "proposed — awaiting
  owner approval").
- This memo: `children/intraday_equities/docs/memos/2026-09-09-final-model-replay-phase0-closeout.md`.
- Doc fix: `children/intraday_equities/CLAUDE.md`.
- Journal: recorded via `dskit.journal record --category research`, README
  regenerated via `dskit.journal render`.
- Next authorized action: none. Every Phase 2–6 file in ADR-0114 stays
  unimplemented until the owner accepts the ADR and rules on the §11 items
  each phase needs.
