# Skeptic review — final-model replay Gate 1 (Phase 0), ADR accuracy/completeness lens

**Reviewer task:** fresh, independent skeptic dispatched to review ADR-0114
against `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` for
accuracy, completeness, and invented content.
**Model/effort:** Claude Sonnet 5 (`claude-sonnet-5`).
**Dispatch:** fresh review; no memory of the implementing session; no edits
made to any reviewed file.

## Scope and evidence

Read in full: the master plan
(`children/intraday_equities/docs/plans/2026-09-08-final-model-replay-and-monitoring.md`),
ADR-0114 and ADR-0113 (`docs/architecture/decision-log.md:6100-6612`), the
Gate 1 memo
(`children/intraday_equities/docs/memos/2026-09-09-final-model-replay-phase0-closeout.md`),
root `CLAUDE.md`, `dskit/pipeline/CLAUDE.md`, `docs/skills/memo.md`, and
`children/intraday_equities/CLAUDE.md`/`AGENTS.md`. Reviewed the exact diff of
commit `4af0df7` (`git show --stat 4af0df7`, `git diff 4af0df7~1 4af0df7`) and
read `dskit/pipeline/kinds_search.py` in full for the ADR-0113 reconciliation
claim. This review does not evaluate code quality, tiering, or governance
process — those are a separate reviewer's lens.

Reproduced the ADR's/memo's baseline evidence myself:

```text
$ cd /home/user/dskit/children/intraday_equities
$ PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m dskit.pipeline validate configs/run-final-hpo.json --adapter intraday_equities
OK — configs/run-final-hpo.json
  name:  final-hpo
  nodes: 8  sections: splits, outputs, tracking, stages
  hash:  ee674709be49f5865d4a77548bb2b963091181ff67a30e62379b2e69b072cc48
# exit 0

$ PYTHONPATH=/home/user/dskit:/home/user/dskit/children/intraday_equities \
    python3 -m dskit.pipeline plan configs/run-final-hpo.json --adapter intraday_equities
{"name": "final-hpo", "document_hash": "ee674709be49f5865d4a77548bb2b963091181ff67a30e62379b2e69b072cc48",
 "order": ["calendar", "select", "memory", "finalist"], ...}
# exit 0
```

Both the hash and the node/edge shape match ADR-0114's and the memo's quoted
output exactly — same node count (8), same order, same stage classes, same
edges.

Confirmed `docs/decisioning/path.csv` and
`children/intraday_equities/docs/decisioning/path.csv` are byte-identical
between `4af0df7~1` and `4af0df7` (`git diff 4af0df7~1 4af0df7 -- ...path.csv`
returns empty). Confirmed the journal action (`A18854`) was appended to
`actions.csv` and the generated `README.md` re-rendered from it, not
hand-edited (both files' diffs are consistent with the CLI's known
append/render behavior — no out-of-band row content). Confirmed
`children/intraday_equities/CLAUDE.md`'s new bullet and tree line are
character-identical to the corresponding lines already present in
`children/intraday_equities/AGENTS.md`.

Confirmed `configs/run-final-hpo.json` itself still contains `"lookback": 20`,
`"momentum_horizons"`, and `"hpo_objective": "ic"` in its LightGBM/torch-MLP
`finalist.templates`, and contains no `lean-pooled`/33-column-drop-mask
content — substantiating the ADR's/memo's "still the wrong recipe" claim
rather than taking it on faith.

Confirmed `dskit/pipeline/kinds_search.py`'s `__all__` and class definitions
contain exactly `CandidateInventory`, `TrialLedger`, `OneStandardErrorSelector`,
`SelectionRecord` (plus the pre-existing `HpoGrid`/`TopTrials`/`register`), and
that `TrialLedger` is fully domain-blind (its docstring: "It never inspects
what `extra` means... only that every row supplies the caller-declared
`required_fields`"; a `grep -ni "lightgbm|lead|symbol|stock|equity|intraday"`
over the file turns up only illustrative docstring mentions of "lead" as a
generic usage example, no domain logic). `tests/pipeline/test_kinds_search.py`
is exactly 1,794 lines, matching ADR-0114's citation. Two "cycle 2" clean
skeptic-review memo files for that Phase 1 work are present in `docs/memos/`,
substantiating the "two clean skeptic rounds" claim in the reconciliation
paragraph.

## Checks performed against the six review items

1. **Completeness against plan §5.** Enumerated all 21 distinct file/doc-update
   bullets in plan §5 (8 generic-mechanism items including the doc-update
   bullet, 6 child-adapter items including `__init__.py` and the
   README/AGENTS-tree update, 7 configuration-document items including the
   real-money refusal). Every one is covered under the correct ADR-0114 phase
   heading (Phase 2: `sklearn.py` bundle + `SklearnFit` fix,
   `final_model.py`, `run-final-hpo.json` modify, `run-final-refit.json`
   add; Phase 3: `cashflows.py`, `compose.py`/`state.py`/`report.py`,
   `capital-policy.json`, conditional production doc-tree update; Phase 4:
   `forecast_bundle.py`, `nodes_capital.py`, `run-mean-confirmation.json`;
   Phase 5: `replay.py`, the `ServeLoop`/`ReplayFeed` conformance-test
   requirement (correctly left unresolved pending its own failing test, per
   plan §6 Phase 5 item 1), `run-development-replay.json`; Phase 6:
   `metrics.py`/`monitors.py`/`vocab.py`, child `metrics.py`, the full §6
   event-schema category list verbatim, `run-full-system-backtest.json`;
   Cross-cutting: `__init__.py` per new module, child doc-tree update,
   real-money refusal). No file from plan §5 is silently dropped. The
   plan's "update the sklearn/search package docs" bullet's *search* half was
   already satisfied by ADR-0113's own implementation (`dskit/pipeline/README.md`
   and `CLAUDE.md` already document `kinds_search.py`'s four new public
   values) — ADR-0114 correctly does not re-litigate that half and covers only
   the still-open `sklearn.py` doc-tree update under Phase 2.

2. **Invented detail.** Spot-checked every Phase 2-6 bullet: each either
   quotes the plan directly or explicitly names what the plan leaves silent
   ("The plan does not name the new class(es)... those are implementation-time
   decisions," "No class/function signature is given beyond this," "No
   further shape is specified," etc.). No parameter name, default value,
   method signature, or schema field appears in ADR-0114 that is not either
   quoted from the plan or explicitly flagged as plan-silent. The one
   borderline case — the "Identity/hash effects" paragraph under Phase 2,
   which reasons that `run-final-hpo.json`'s hash will change once its
   `pipeline`/`finalist.templates` content changes — is a direct application
   of the *already-ratified* identity-hash rule in root `CLAUDE.md`
   ("Configuration standards"), not a new invented rule; it introduces no new
   decision.

3. **§11 fidelity.** Diffed ADR-0114's ten "Open owner decisions" items
   (`decision-log.md:6529-6554`) against plan §11 (`plan.md:520-543`)
   line-by-line: all ten are verbatim, not paraphrased. ADR-0114 answers or
   infers none of them — every phase's "Blocking §11 items" note is phrased
   as a dependency, never a ruling. Cross-checked the per-phase blocking
   claims individually: Phase 2 correctly cites item 1 (the one-SE unit)
   as blocking `final_model.py`'s per-lead selection, consistent with plan §2
   locking the score function itself but leaving the SE unit open in §11.1.
   Phase 4's `forecast_bundle.py` correctly cites item 3 (inverse-label
   reference policy), matching plan §11.3's identical wording and plan §6
   Phase 4 test 1's "owner-approved inverse label transformation" language.
   Phase 5's `replay.py` correctly cites items 5 and 6, matching plan §6
   Phase 5 item 2's explicit "after §11 is ruled" deferral. Phase 6 correctly
   distinguishes that item 7 blocks only WARN/HOLD *behavior*, not metric
   *recording* — this is the plan's own distinction in §6 ("values are
   recorded even before alert thresholds are approved"), not a weakening
   introduced by the ADR.

4. **§12 packet fidelity.** Diffed the appendix (`decision-log.md:6556-6587`)
   against plan §12 (`plan.md:545-571`) line-by-line: all seven Path-row
   proposals (including the A18256 preservation note) are verbatim, with the
   same `LOCKED` values and conditions. The appendix opens with an explicit
   "This ADR is NOT authorized to apply any of it to `docs/decisioning/path.csv`"
   statement, correctly citing both AGENTS.md and CLAUDE.md's owner-only
   restriction, and the **Scope**/**Consequences** sections repeat the
   restriction. Confirmed by git diff (above) that `path.csv` was in fact
   untouched.

5. **ADR-0113 reconciliation.** Confirmed accurate, not overstated or
   understated. The claim that ADR-0113 delivered exactly the plan's
   `kinds_search.py` bullet is true by direct inspection of the file (four
   named classes present, fully domain-blind). The claim that a "domain-side"
   half remains — a real per-lead LightGBM objective feeding the ledger — is
   also true: nothing resembling that exists in `kinds_search.py`, and the
   plan's own Phase 1 test list (§6 Phase 1 items 1-3) does describe such
   domain evidence ("the exact forecast-accuracy objective against a
   hand-calculated tiny example," LightGBM-specific selection). Folding that
   remaining half into Phase 2's `final_model.py` is consistent with plan §5's
   own assignment of "the lead-specific outer-aligned score... and calls into
   the generic search/bundle objects" to that file.

6. **Baseline evidence accuracy.** Reproduced independently (above); hash,
   node/edge shape, and exit codes match exactly what ADR-0114 and the memo
   quote.

## Findings

None. Zero Critical, zero Major, zero Minor findings against the ADR-accuracy/
completeness lens.

## Checks that passed

- Every file plan §5 names for Phases 2-6 (generic and child-side) is covered
  under the correct phase in ADR-0114's Decision section; nothing is dropped.
- No parameter, default, class/function signature, or schema field is
  invented anywhere the plan is silent — every such gap is called out by name
  in the ADR's own text.
- All ten plan §11 items are reproduced verbatim in ADR-0114, none is answered
  or inferred, and the per-phase blocking mapping is accurate on the phases
  spot-checked (2, 3, 4, 5, 6) against the plan's own text.
- The §12 Path-update packet is reproduced verbatim and is explicitly, and
  correctly, marked as not authorizing any `path.csv` edit — confirmed by git
  diff that the file was in fact untouched.
- The ADR-0113 reconciliation claim (kinds_search.py matches the plan's
  generic-mechanism ask; a domain-side gap remains, folded into Phase 2) is
  accurate by direct inspection of the current `kinds_search.py` source.
- The baseline `validate`/`plan` evidence for `configs/run-final-hpo.json`,
  including the identity hash `ee674709…`, is reproducible exactly as quoted
  in both ADR-0114 and the Gate 1 memo.
- ADR-0114's status line reads literally "proposed — awaiting owner approval
  (2026-09-09)" — never "accepted."
- The Gate 1 memo follows `docs/skills/memo.md`'s required shape: opens with
  a three-sentence TL;DR, states the execution contract before any evidence,
  gives deliberately-unrun work its own full section ("What did not happen")
  rather than a buried closing caveat, and ends with a "Reproducibility and
  handoff" section naming every durable artifact and the (none-authorized)
  next action.
- `docs/decisioning/path.csv` (both root and child copies) is confirmed
  byte-identical across the Gate 1 commit; the journal action was recorded
  through the CLI (`actions.csv` append + regenerated `README.md`), not
  hand-edited.

## Verdict

**PASS** — 0 Critical, 0 Major, 0 Minor.

ADR-0114 is an accurate, complete, and faithful Phase 0 artifact against the
plan: it covers every file the plan's §5 names for Phases 2-6, invents no
detail the plan leaves silent (calling out each such silence explicitly),
reproduces all ten §11 items and the §12 packet verbatim without resolving or
inferring any of them, correctly reconciles ADR-0113's delivered scope against
the plan's Phase 1 ask, and its quoted baseline `validate`/`plan` evidence for
`configs/run-final-hpo.json` reproduces exactly under independent execution.
