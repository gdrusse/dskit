# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

---

# ▶ PICK UP HERE

**State: C6 is on `ws/c6`, gates green, not yet pushed.** The 2026-08 closeout
run is **22 of 24 cards done**. Two remain: **C5, D1**. D1 is unblocked.

## The two remaining cards

| Card | What it delivers | Spec (already ACCEPTED) | Start now? |
|---|---|---|---|
| **C5** | Time-series architecture zoo — 10 archs behind a registry, `arch` becomes a swept param | **ADR-0041** | ✅ yes |
| **D1** | Selection demo — select features, sweep two models, use the winner | plan §8, card D1 | ✅ yes (needs C6 merged) |

C5 must not touch `libs/torch.py` (C6 drained it; ADR-0041 says the zoo is a
NEW sibling `libs/torch_ts.py`). D1 adds `examples/pipeline/selection-demo.json`
and moves the ledger to 18 documents. **ADR-0044** (proposed) is the owner's
call: flow 2 of ADR-0042 cannot run until `fitted_transform` searchability is
narrowed from per-role to per-knob.

## How to run one, exactly

1. **Read the spec first.** The ADR is the decision — implement it, do not
   re-decide it. Each ADR names its hooks, params, registration points, error
   behaviour and test obligations. Then read the card in
   `docs/plans/2026-08-closeout.md` §8, and the TODO section the card cites.
2. **Make a worktree with its own venv** (an editable install from the MAIN
   checkout would import main's code, not the branch's — always verify):
   ```bash
   git -C ~/dskit worktree add ~/wt/c6 -b ws/c6 main
   cd ~/wt/c6 && python3 -m venv .venv
   .venv/bin/pip install -e ".[all,dev]" && .venv/bin/pip install stable-baselines3
   .venv/bin/python -c "import dskit; print(dskit.__file__)"   # MUST print a path inside ~/wt/c6
   ```
3. **Work TDD.** Failing test first, watched failing for the right reason, then
   green. A pin must be PROVEN able to fail (mutate the thing it guards, watch
   it fail, revert) — a pin that cannot fail is test theatre.
4. **Gate before merging** (see "Verification recipe" below).
5. **Merge law — the orchestrator is the only merger.** Rebase the branch onto
   `main`, re-run the card's targeted tests, `git merge --ff-only`, run the gates
   on main, then remove the worktree and delete the branch.
6. **Then mark `TODO.md`, update the STATE table in the plan, commit AND push.**

## Rulings that are NOT in the cards (carry them forward)

- **Do not dispatch a `fable` subagent** — that tier ran out of credits mid-run.
  Plan §7's model map is amended in the plan itself: every `fable` row is served
  by **opus at the same effort**, keeping `xhigh` where it says `xhigh`.
- **`libs/torch.py` is drained (C6).** C5 must leave it byte-identical; the zoo
  goes in a NEW sibling `libs/torch_ts.py`. The purity gate forbids an `nn.Module`
  subclass at module level anywhere in `dskit/pipeline/`, including inside a class
  body, so every net is defined INSIDE `build_module`.
- **ADR-0044 is PROPOSED, not accepted.** C6 landed ADR-0042 flows 1 and 3. Flow 2
  (a space over the selector's own knobs) is refused by ADR-0040's per-role
  `_UNSEARCHABLE_ROLES` entry. Implement 0044 only after the owner accepts it.
- **Targeted tests, not the whole suite** (owner ruling). Run the suites covering
  what the card touched. Ruff over the whole tree and the hash gate run every time
  regardless — they are seconds and catch what a scoped run cannot.
- **At most two cards in flight, each internally serial.** Never pair cards that
  share files.
- **Review converges on a severity floor:** once a round produces no
  BLOCKER/MAJOR, stop and let the orchestrator rule on the MINORs. Measured over
  this run, 65% of findings targeted prose and 56% were MINOR, so the tail rounds
  were mostly churn.

## Verification recipe

```bash
cd ~/dskit
.venv/bin/python -m ruff check .                       # whole tree, must be clean
.venv/bin/python -m pytest tests/pipeline -q           # + the suites your card touched
for f in examples/pipeline/*.json children/intraday_poc/configs/run-*.json; do
    echo -n "$f "; .venv/bin/python -m dskit.pipeline validate "$f" \
      | grep -oE "[0-9a-f]{64}" | head -1
done
```
The ledger is **17 documents**. Every identity hash must be unmoved unless the card
DECLARES the move, in which case record it in the intentional-move log
(`docs/plans/2026-08-closeout.md` §9). **An engine card that moves any hash is an
automatic fail.** Current expected values live in the §9 log; regenerate with the
loop above.

---

## What landed in this run (2026-08-27 → 28)

**Wave 1 — 12 cards.** The docstring/doctest conversion (A5); the child hardcoding
audit, all 12 points (A2); engine constants (A4); config knobs and the epochs pin
(A1); `TorchAdapter` as a real ABC (B1); the `loss` knob (B2); the
`kinds_flow`/`kinds_banking` split (B3); hyperparameters in `log_params` (E1); the
`runs` CLI verb (E2); the MLflow sink pack (E3); continuous optuna ranges proven
end to end (E4); the model-sweep cookbook and the lightgbm extra (E5).

**G1 — six ADRs accepted: 0038–0043.** Drafted in parallel, each through five
adversarial review rounds, then condensed at the gate from 2,164 lines to 583
because review had driven them far past house style into specifying mechanism the
decision log should not carry — and that over-specification was manufacturing its
own contradictions.

**Wave 2 — 6 of 7.** `TrainableNode` ported across five packs, all nine
`if mode ==` branches gone (C1); gap-aware windows, the fitted-transform family and
the child collapse (C2); the `foreach` fan-out grammar (C3); the long-method
decomposition, with the ceiling now pinned (C4); HPO × walk-forward semantics, so
per-fold winner instability is a printed diagnostic (C7); the feature-selection
seam — `FeatureSelector` + `sklearn-select` + `torch-importance`, `torch.py`
drained (C6). C5 (the architecture zoo) is the last Wave-2 card.

**Waves 4–5.** The store brought current (D2), the capstone run (D3), TODO marked
(D4), this wrap (D5).

## The capstone (D3) — real numbers

Run on the live store, **2,016,587 bars**:

- **9/9 trials in 4m28s**, peak RSS 8.07 GB. Objective
  `$select.metrics.total_realized` over 12,818 realized picks on the embargoed tail.
- **Winner `hidden_size` 16/16 at 0.2274**; runner-up 32/16 at 0.2265. The declared
  32/32 base pass — the value that had simply been typed in — scored **0.0302**.
- **Reproducible:** an identical re-run produced the same `run_hash` and all nine
  trial scores bit-for-bit equal, checked elementwise rather than on the winner.
- Trials are distinguishable by params in the MLflow sink and via the `runs` verb.
- `run-train.json`'s identity moved intentionally: `85fff271…` → `f320458f…`.

**A premise the run corrected.** `foreach` pins the DECLARATION, not the tuned
VALUE: one space key naming the template expands to one key PER INSTANCE and
`hpo-grid` CROSSES them — 3 widths × 2 symbols = 9 trials, and a winner may pair 16
with 64. It removes the forgotten-copy failure (a third symbol is one line) but does
NOT force the two symbols to share a width. The TODO assumed otherwise; the
documents, the child CLAUDE.md and the pin's docstring now say so plainly.

## Known gaps and follow-ups

1. **A memory twin of the ADR-0037 defect, found by running.** The walk-forward
   backtest re-run is GREEN (3/3 folds, exit 0) but peaked at **17.59 GB** — 6.8×
   the 2.6 GB precedent — and swapped (110,850 major page faults). Attribution was
   measured, not guessed: the observations READ is fine (1.54 GB, 764 B/row, in line
   with the ADR's 650 B/row), and the cost is the torch pack's **unbatched
   final-loss pass** (~11.9 GB for the last fold's 906,394 rows). ADR-0037 fixed
   this shape in the read; it has a twin in the pack. **This deserves its own card.**
2. **D3's fit window is bounded** to 2026-01-01 as a deliberate ceiling so the grid
   fits in 8.1 GB — labelled as such in the node's notes. Remove the bound once the
   final-loss pass is batched.
3. **The winner (16/16) was NOT promoted** into the documents. Promoting it edits
   both documents together (the cross-document pin) and an asymmetric winner would
   have to defeat the symbol-twin regime pin. That is an owner call; both pins were
   left standing.
4. **D3 merged on a proof-of-concept bar** with 2 MAJOR + 2 MINOR review findings
   outstanding — the MAJORs were a stale hash figure in its own report (corrected
   here from the merged tree) and its twin. Worth a read of the D3 STATE row.
5. **`synthetic_nodes`' 11 demo classes accept any unknown param** — they predate
   default-deny and are private/demo-registry only. Surfaced while pinning a
   `foreach` rule; not a defect of that card. Would be its own card across 11 classes.
6. **Still open in `TODO.md` and untouched by this run:** the `BarsFromStore` scan
   shape, the `timeframe` spec knob (half done — the serving half landed, the knob
   did not), the ignore-list drain (ongoing by rule — 6 modules drained this run),
   the pmquant §13 gaps 5/6/7/9/10/11/12, and the two long-term sections (serving
   loop, Hugging Face), which were explicitly out of scope.

## Where the state lives

- `docs/plans/2026-08-closeout.md` — the orchestrator brief. §3 merge law, §7 model
  map (amended), §8 the task cards, §9 the hash ledger and intentional-move log,
  §10 the STATE table with a row per card and what it actually did.
- `docs/architecture/decision-log.md` — ADR-0038…0043 accepted; ADR-0044 proposed.
- `TODO.md` — every item carries its own reasoning; landed items carry what landed
  and any correction the work forced. 54 checked, 22 open after C6 (feature
  selection + governing class landed; the three-flows item stays open until
  ADR-0044, because flow 2 cannot run).
