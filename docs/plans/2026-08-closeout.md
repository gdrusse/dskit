# Orchestration plan — 2026-08-27 audit closeout

**You are the orchestrator.** This document is your complete brief. Owner
(Russell) has ratified it; his acceptance approves every new file it names and
pre-authorizes the ADR approval rule in §6. Work autonomously to the end.

**Mission.** Complete every item ADDED to `TODO.md` by commit `93ed7e2`
("docs: a code standard, an OOP-pillars doctrine, and a hardcoding audit"),
EXCEPT the two "Long-term goal" sections (serving loop, Hugging Face) — those
stay untouched. Older open items (pmquant gaps #5/6/7/9/10/11/12, the two
Deferred entries) are OUT of scope. Then: run the selection demo (D1), acquire
`./ob` (D2), run the capstone tracked experiment (D3), mark `TODO.md` (D4),
and wrap + push (D5). **Success = all in-scope boxes checked, merged to
`main`, suite green, hashes accounted for, pushed, RE-ENTRY refreshed.**

**Owner rulings recorded 2026-08-27 (do not re-ask):**
- Ignore-list drain = **touched modules only** (doctrine "no retrofit sweep"
  governs). Convert any module a workstream meaningfully edits; delete its
  `per-file-ignores` entry in the same change. No sweep of the other ~55.
- ADR approval = **pre-authorized**: skeptic loop to zero findings, then the
  ORCHESTRATOR approves holistically (§6). The owner is not pinged mid-run.
- TDD is mandatory for all code (§4). Docs-only work is exempt.
- Worktrees + Workflow-tool fan-outs; orchestrator is the only merger.
- The plan's model/effort assignments (§7) are deliberate — follow them.

---

## 1. Startup ritual (every orchestrator session, and after any compaction)

1. `wsl.exe -e bash -lc 'cd ~/dskit && git pull --ff-only && git log -3 --oneline'`
2. Read, in order: this file; `~/dskit/TODO.md` (the in-scope sections carry
   their own reasoning — they are the spec); `~/dskit/CLAUDE.md` (Working
   agreement, Code standards, Docstrings, Duplication that diverges);
   `~/dskit/children/README.md` (child law); `~/dskit/docs/RE-ENTRY.md`.
   Also your global `~/.claude/CLAUDE.md` and memory `MEMORY.md` (Windows
   side). Re-skim CLAUDE.md + this file at every wave boundary.
3. Verify the baseline (§9): full suite green, ruff clean, all 14 hashes
   match the ledger. If not, STOP and fix the environment first.
4. Recreate/refresh the STATE table (§10) from git + TODO checkboxes.

## 2. Environment law (non-negotiable; distilled from owner memory)

- Repo: `/home/russell/dskit` in WSL2 (Ubuntu). This harness runs on Windows:
  every command goes through `wsl.exe -e bash -lc '...'`. Subagents inherit
  this — their prompts must say so explicitly (template §5).
- Venv: `~/dskit/.venv` (python ≥3.11) has `.[all,dev]` + `stable-baselines3`
  + `matplotlib` + `alpaca-py` — the full 2440/108 baseline env.
- **Worktrees.** One per task card, created by YOU before dispatch:
  `git -C ~/dskit worktree add ~/wt/<id> -b ws/<id> main`
  Each worktree needs its own venv (`python3 -m venv .venv && .venv/bin/pip
  install -e ".[dev]"` + the extras the card names; pip cache makes repeats
  fast). **Editable-import trap:** using `~/dskit/.venv` inside a worktree
  imports MAIN's code, not the worktree's. Every agent must run
  `.venv/bin/python -c "import dskit; print(dskit.__file__)"` and confirm the
  path is inside ITS worktree before trusting any test result.
- Child tests: `.venv/bin/python -m pytest children/intraday_poc/tests -q`
  (conftest bootstraps sys.path; no child install needed). The root suite
  reruns them by subprocess via `tests/children/`.
- WSL `/tmp` is volatile — stage anything durable under `/home/russell`.
  Long jobs: harness-tracked background only, ≤10-min chunks (detached WSL
  jobs die when `wsl.exe` returns). RAM ceiling 18 GB. Disk is ample (~890G).
- Never touch `~/dskit` (main) while agents run except to merge; agents
  never leave their worktree.

## 3. Merge law — the orchestrator is the only merger

Per completed card (skeptic-clean + your verification, §5):
1. In the worktree: rebase onto current main (`git fetch . && git rebase
   main` from the worktree, or rebase after fresh pull), rerun the card's
   targeted tests.
2. Gates, in the worktree or on main after ff-merge — all three, every time
   (the suite costs ~30s; there is no reason to skip):
   - `.venv/bin/python -m ruff check .` → clean
   - `.venv/bin/python -m pytest -q` → green (baseline 2440/108; count grows
     as cards add tests — track the new number in STATE)
   - Hash gate: validate all 14 ledger documents (§9) + any new ones. A hash
     may differ from the ledger ONLY if the card explicitly declares an
     intentional document edit; update the ledger in the same merge and say
     so in the commit message. **An engine-side card that moves any hash is
     an automatic FAIL — bounce it back to the skeptic loop.**
3. `git -C ~/dskit merge --ff-only ws/<id>` (rebase makes ff possible).
   Then `git -C ~/dskit worktree remove ~/wt/<id> && git branch -d ws/<id>`.
4. Mark the TODO.md checkbox(es) the card closes (style: `**Landed via
   ADR-00NN / this run (2026-08-DD).**`) and update STATE. Commit doc edits
   with the merge or immediately after.
5. Commits: descriptive messages in the repo's style, ending
   `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Conflict chains (serialize; merge each link before dispatching the next):**
- `libs/torch.py`: B1 → B2 → C1(torch leg) → C4(TorchTrain.run) → C5(zoo) → C6(torch-importance leg)
- `driver.py`: E1 → C1(driver touches) → C4(run_document) → C3(foreach)
- child (`intraday_poc/*`): A1 → A2 → C2(WindowRows leg) → C5(model switch leg) → D3
- `libs/numpy.py`: C2 owns it exclusively (including audit HIGH-2)
- Everything else is parallel-safe; `pyproject.toml` one-liners (extras) may
  conflict trivially — resolve at rebase.

## 4. TDD doctrine (owner-mandated; the repo already works red-first)

- **Code cards:** write the failing test FIRST, watch it fail for the right
  reason, then implement to green. Commit order inside the branch should
  show it (test commit or test-first diff acceptable). Pure refactors (C4,
  B3): the existing suite is the harness — prove zero behavior change
  (suite green, hashes unmoved) and ADD the missing docstrings/tests the
  card names.
- **Pin tests** (the house specialty): a pin must be shown to FAIL when one
  copy diverges (mutate temporarily, revert) — a pin that cannot fail is
  test theater and skeptics must kill it.
- **Config-only cards** (A1, parts of D3): the pin/validation test is the
  "test-first" — extend it before editing the config.
- **Docs-only cards** (A5, ADR drafts, D4): no TDD; gates still apply.
- Never weaken, skip, or delete an existing test to get green without the
  card explicitly saying so.

## 5. The delegation protocol

**Dispatch.** Use the Workflow tool for wide fan-outs (parallel cards, ADR
drafts, skeptic rounds) — `agent(prompt, {model, effort, schema})`, no
`isolation` option (worktrees are manual, §2). Use direct Agent calls for
serial chain links. Skeptic loops fit Workflow's loop-until-zero pattern.

**Every implementer prompt = this preamble + the task card verbatim:**

> You are implementing one task in the dskit repo (`/home/russell/dskit`,
> WSL2 — run ALL commands via `wsl.exe -e bash -lc '...'`). Work ONLY in
> your worktree `~/wt/<id>` on branch `ws/<id>`. First actions: (1) read
> `~/dskit/CLAUDE.md` in full — the Working agreement, Code standards,
> Docstrings, and "Duplication that diverges" sections are binding law;
> (2) if your card touches `children/`, also read `~/dskit/children/README.md`
> — ADR-0021 child law is binding: children NEVER modify `dskit/`, generic
> capability graduates INTO dskit, domain lives in configs, vendor knobs are
> `spec()` knobs, position-independence; (3) read the TODO.md section your
> card cites — its reasoning is part of the spec; (4) verify your venv
> imports dskit from YOUR worktree (card tells you how). Design with the
> OOP pillars: subclass a hook, never branch; @abstractmethod for abstract;
> `__all__` + `_` prefix at boundaries; one job per method. Reuse before
> building: inventory `dskit/*/libs/` and the `_PARAMS` tuples before
> claiming anything is missing. Never introduce a value in two places
> without a pin. New/meaningfully-edited functions get NumPy docstrings per
> the standard (no `>>>`, no new signature annotations); if you meaningfully
> edit a module listed in `pyproject.toml` per-file-ignores, convert it and
> delete its entry. TDD: failing test first (see card for exemptions). Do
> NOT touch `TODO.md`, `docs/RE-ENTRY.md`, or merge anything — the
> orchestrator owns those. When done: commit (message style of the repo +
> `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`), and report:
> branch, HEAD sha, files touched, tests added/changed with red→green
> evidence, targeted-test + ruff output tails, and a self-check against the
> standards above. Your report is data for review, not a human update.

**Skeptic rounds.** After an implementer (or fixer) reports, launch the
card's skeptic set IN PARALLEL, fresh agents every round (never reuse a
skeptic across rounds — anchoring). Skeptics are read-only reviewers of the
branch diff. Prompt = this preamble + card + lens:

> You are an adversarial skeptic reviewing branch `ws/<id>` of
> `/home/russell/dskit` (WSL2; commands via `wsl.exe -e bash -lc`). Diff:
> `git -C ~/wt/<id> diff main...HEAD`. Read `~/dskit/CLAUDE.md` (all
> standards sections) first; if the card touches `children/`, read
> `~/dskit/children/README.md` (ADR-0021). Your job is to REFUTE the work:
> find real defects, not style taste. Verify every suspicion before
> reporting it (run the test, read the actual line) — an unverified or
> merely-plausible finding is worthless; so is a nitpick the written
> standard does not require. Check specifically: (a) correctness — does the
> change do what the TODO item's reasoning demands, with edge cases; (b)
> test integrity — red-first evidence, pins that actually fail on
> divergence, no weakened/deleted tests, no test theater; (c) the standards
> — OOP pillars (branch-vs-subclass, real ABCs, encapsulation, one job per
> method), docstring standard, tiering (core stdlib-only / packs name their
> library only inside run() / domain stays in the child), default-deny
> params; (d) duplication — any value now in two places unpinned, any
> reimplementation of something `dskit` already ships (inventory before you
> accept a rebuild), tier-2 restating tier-1 truth; (e) identity-hash
> discipline — run the hash gate; engine changes must move nothing; (f) the
> card's own Do-NOTs. Also honor TODO.md's "Confirmed CORRECT — do not fix"
> list — flagging those is a false positive. Output: either the single
> token NO FINDINGS, or a numbered list where each finding has file:line, a
> concrete failure scenario or violated rule (cite the CLAUDE.md/TODO line),
> and severity (BLOCKER/MAJOR/MINOR). Report findings only — no praise, no
> summary of what is fine.

**Loop.** Findings → dispatch a fixer (continue the SAME implementer agent
via SendMessage while its context is healthy; else a fresh agent with card +
findings + diff) → fixer commits → NEW skeptic round (fresh agents, full
lens set — they review the whole branch, not just the fix). Terminate only
when a round returns NO FINDINGS from EVERY skeptic. MINOR-only rounds still
loop. If a round produces only findings that contradict a previous round's
accepted resolution, you arbitrate: rule on the disagreement explicitly in
the dispatch of the next round (your ruling is binding on skeptics).
Escalation valve: after 3 findings-rounds, escalate the fixer one model tier
(sonnet→opus→fable) and raise effort one step; after 5, intervene yourself.

**Orchestrator verification (after zero-findings).** You read the full diff
yourself and check it holistically against the card's Done-when, the OOP
pillars, and the duplication law — you are the last reviewer. Then run the
three gates (§3). If YOU find something: write the finding, send it back
(fixer + fresh skeptic loop), and re-verify. **Context guard:** if your own
context is strained, delegate the diff-read to a fresh verifier agent
[opus, effort high] with the card + §5 checklists, and act on its report —
but the gates you always run yourself.

## 6. ADR batch protocol

Six design documents (ADR-A…F, cards below) are drafted IN PARALLEL during
Wave 1 by fable agents, each through the full skeptic loop (3 fable
skeptics/round). They are appended to `docs/architecture/decision-log.md`
only at approval time, numbered in decision-log order starting at **ADR-0038**
(0038 is free; you assign final numbers when committing).

**Gate G1 (implementation kickoff):** ADR-gated implementation (Wave 2)
starts only when (a) every Wave-1 card is MERGED, and (b) every ADR draft is
skeptic-clean AND you have approved it. Your ADR approval is a real review:
coherence with the pillars and tiering; no duplication with existing seams
(inventory!); identity-hash consequences stated; cross-ADR consistency —
ADR-C(window/scaler) and ADR-E(selector) must share one fitted-transform
story; ADR-E must build on ADR-A's `TrainableNode`; ADR-D(zoo) must consume
B2's loss knob. If something is off: written finding → draft goes back
through the loop. Status line on commit:
`**Status:** accepted (2026-08-DD; owner pre-authorized 2026-08-27, skeptic-loop + orchestrator approval)`.

## 7. Models & effort — the sophistication map

Orchestrator: model/effort set by the owner at launch (recommendation:
**fable, effort high**). Subagents (Workflow `agent()` opts / Agent tool):

**AMENDED 2026-08-28 (owner ruling, mid-run): FABLE IS UNAVAILABLE — out of
credits. Do not dispatch a fable subagent; every fable row below is served by
opus at the same effort.** Opus and sonnet are unaffected. The effort ladder
is what carries the sophistication now, so keep xhigh where the table says
xhigh. Restore the original mapping only when the owner says fable is funded
again. (Mechanically: the card-loop script passes a `model` override ONLY when
the args name one, so omitting it inherits the session model — a safe default
when a tier goes dry mid-run.)

| Tier | Used for | Cards |
|---|---|---|
| **~~fable~~ → opus, xhigh** | hardest design-adjacent implementation | C1, C2, C5, C6, D1 |
| **~~fable~~ → opus, high** | ADR drafting; identity-bearing grammar | ADR-A…F drafts, C3 |
| **opus, high** | hard bounded implementation | A2, E3, E4, C4, D3 |
| **opus, medium** | bounded engine/child work | E1, E2, E5, B1, B2, B3, C7, D2 |
| **sonnet, medium** | mechanical with care | A1, A5 |
| **sonnet, low** | trivia | A4, D4 assist |
| Skeptics (default) | 2×/round | **opus, high** |
| Skeptics (escalated) | 3×/round | **~~fable~~ → opus, high** — for ADR drafts, C1, C2, C5, C6, D1 |
| Verifier delegate | context guard only | opus, high |

Lens split when 2 skeptics: #1 correctness/test-integrity, #2
standards/duplication/tiering (+ADR-0021 when child files are touched).
When 3: add #3 identity-hash/compat/perf.

## 8. Task cards

Wave structure: `W1 ∥ ADR drafts → G1 → W2 → D1 → D2 → D3 → D4 → D5`.
Within W1/W2 everything runs parallel EXCEPT the conflict chains (§3).
Line numbers cite `93ed7e2`; treat as hints, re-locate before editing.

---

### Wave 1 — no ADR required

**A1 — intraday config knobs + epochs pin** · child chain #1
- Agent sonnet/medium · Skeptics 2×opus/high · TDD: pin-first
- Files: `children/intraday_poc/configs/run-train.json`,
  `configs/run-backtest.json`, `tests/test_configs.py`
- Do: (1) Add `epochs` to the seven-knob pin tuple in `test_configs.py:64`
  — prove the pin fails on divergence, then green (docs currently agree at
  5). (2) Set `"monitor": "val_loss"` on ALL trainer nodes in both
  documents (4 sites) — ADR-0035's seam; `val_rows` is already wired.
  (3) Declare `"device": "cpu"` explicitly on the trainer nodes with a
  `notes` explaining it is the declared knob for a GPU box. (4) Declare
  `optimizer_params` explicitly at Adam defaults (`{"weight_decay": 0.0}`)
  with a `notes` naming the knob's purpose — the item is that the knob is
  invisible, not that training must change.
- Do-NOT: touch `epochs` values; change any other training semantics.
- Hashes: BOTH child documents move — intentional; declare in report so the
  orchestrator updates the ledger (TODO's baseline block too, see D4).
- Done when: pin proven; both docs carry monitor/device/optimizer_params
  with notes; child tests + `validate` green.

**A2 — child hardcoding audit (HIGH+MEDIUM+LOW) + live reads configs** · child chain #2
- Agent opus/high · Skeptics 2×opus/high (+ADR-0021 lens) · TDD: yes
- Files: `intraday_poc/nodes.py`, `connectors.py`, `live.py`,
  `testing.py?`, `configs/source-live.json`, `.env.example`, `README.md`,
  `tests/*`; skeleton: `children/_skeleton/yourproject/nodes.py`,
  `connectors.py`
- Do (each is a TODO checkbox; the TODO text is the spec):
  1. Defaults-once: `"bars"`/`"close"`/`5` each become ONE module constant
     used by both `validate_params` and `run`/`_scan` (idiom:
     `libs/torch.py` `DEFAULT_EPOCHS`).
  2. Bar primary key: single source of truth (constant in `connectors.py`
     imported by `nodes.py` — same package, allowed); keep/extend the
     freeze test so BOTH consumers are pinned.
  3. `price_field` train/serve skew: `live.py` READS it from the run dir's
     `config.json` (driver writes the whole document — serving doctrine in
     `children/README.md`); extend `test_live_window_parity` to a
     non-`close` field so the blindness dies.
  4. `adjustment` three-way disagreement: live fetch takes adjustment from
     the SOURCE config (backfill declares `all` and says why); align
     `source-live.json`; document the ruling in notes.
  5. `--artifact SYMBOL=PATH`: implement the promised argparse flag;
     delete `DEFAULT_ARTIFACTS` (the `live.py:258` fallback already
     reproduces it — pure redundancy).
  6. Model class path: `live.py:101` stops refusing anything but the
     literal — read the declared class from run-dir `config.json`
     (ADR-0025 seam restored). Loud error if absent.
  7. `source-live.json` disconnected: today it registers `alpaca-live`
     while documents read `source: "alpaca"`, so live bars reach nothing —
     but `children/README.md` claims the two-source split is deliberate
     with per-(source,stream,mode) cursors. INVESTIGATE the onboarding
     cursor keying and pick the design that makes live-acquired bars
     actually reachable (likely: same source name, `--mode live`); then
     make README and configs tell ONE true story.
  8. `APCA_API_BASE_URL` inert: delete it from `.env.example` (doctrine:
     never document an escape hatch you did not build; `paper=True` stays
     hardcoded — refusal-by-default).
  9. `README.md:105-107` states the opposite of `observations.py:214-218`
     (raises `AssetError`) — correct the doc.
  10. Delete dead `_SIP_FIELDS` (`live.py:51`).
  11. `_reject_unknown` copies: child `nodes.py:56` and skeleton
      `nodes.py:43` import `reject_unknown_params` from
      `dskit.pipeline.node` instead (public since 3a). Skeleton FIRST.
  12. Skeleton `connectors.py:42` `_DEFAULT_START` vs its `spec()` note —
      make the note read the constant's value or restate-with-pin.
- Do-NOT: touch `WindowRows.run` internals beyond the defaults-once fix
  (C2 rewrites it); add a third config file (doctrine); "fix" anything on
  TODO's Confirmed-CORRECT list (`suite-bars.json:33`, skeleton test
  literals, `live.py:88-94` state_hash re-derivation).
- Hashes: engine hashes must not move; child config edits (4,7) may —
  declare them.
- Done when: every numbered point has a test or pin where testable; child
  suite + root `tests/children` green; README/config/code tell one story.

**A4 — engine trivia constants** · parallel-safe
- Agent sonnet/low · Skeptics 2×opus/high · TDD: yes (small pins)
- Files: `dskit/pipeline/libs/localfiles.py`, `libs/restapi.py`
- Do: `utf-8` in localfiles (`:126` discover / `:153` read) → one module
  constant. restapi defaults `30`/`3` stated in prose AND code
  (`:40/:155/:275`, `:42/:159/:278`) → module constants the docstrings
  reference. Convert+drain these modules' ignore entries only if the edit
  is "meaningful" (constant extraction is borderline — converting
  localfiles is cheap; agent judges, skeptics check).
- Done when: one name per default; ruff/suite green; no hash movement.

**A5 — doctest-style `>>>` → `::` conversion** · parallel-safe
- Agent sonnet/medium · Skeptics 2×opus/high · TDD: docs-only
- Files: 17 docstrings across `dskit/assets/`, `dskit/onboarding/`
  (biggest: `assets/model.py:64`, `assets/ingest.py:68`,
  `onboarding/coverage.py`) — 81 lines total.
- Do: mechanical `>>>` → indented `::` blocks per the CLAUDE.md standard.
  Zero content changes beyond the format swap. This does NOT count as
  "converting the module" — leave `per-file-ignores` alone.
- Done when: `grep -rn ">>>" dskit/assets dskit/onboarding` returns
  nothing; suite/ruff green; no hash movement.

**E1 — `log_params` carries hyperparameters** · driver chain #1 · BLOCKS E3
- Agent opus/medium · Skeptics 2×opus/high · TDD: yes
- Files: `dskit/pipeline/driver.py:934` region, tests
- Do: widen the tracker payload to flatten every node's params as
  `<node>.<param.path>` keys — the SAME grammar `hpo-grid` space keys use.
  REUSE: if a flattener exists in `kinds_search.py`/`base.py`, import it;
  if not, put ONE in core where both can share it (duplication law). Keep
  the five existing fields. Test with the memory sink: a run's payload
  contains e.g. `model.hidden_size`.
- Do-NOT: change identity hashing; break the memory sink's tests.
- Done when: params visible in sink payloads; grammar-parity with space
  keys pinned by a test; suite green, hashes unmoved.

**E2 — `runs` CLI verb (cross-run comparison, zero deps)** · parallel-safe
- Agent opus/medium · Skeptics 2×opus/high · TDD: yes
- Files: `dskit/pipeline/__main__.py` (+ a core module if the scan logic
  earns one), tests
- Do: `python -m dskit.pipeline runs [--root DIR]` scans `pipeline_runs/`
  (the run-dir layout: `{name}-{asof}-{run_hash[:8]}/` with `config.json`,
  `resolved.json`, records, `report.md`), tabulating name, asof, hashes,
  and key metrics (read structured records, not report.md prose; params
  come from `config.json`). Tier-1: stdlib only. Sort newest first;
  tolerate foreign/partial dirs loudly-but-nonfatally (say what was
  skipped — no silent truncation).
- Done when: verb documented in `--help` + pipeline README "What ships";
  e2e test over two synthetic runs; suite green.

**E3 — MLflow tracking sink pack** · after E1 merges · new file
- Agent opus/high · Skeptics 2×opus/high · TDD: yes
- Files: NEW `dskit/pipeline/libs/mlflow.py`; `pyproject.toml` (`mlflow`
  extra + `all`); tests NEW `tests/pipeline_libs/test_mlflow.py`; pipeline
  README/CLAUDE trees (docs-currency rule).
- Do: implement the `Tracker` protocol (`protocols.py:86`) as a tier-2
  pack registered via `register_sink_kind` (`base.py:1232`; note
  "real sinks register application-side" — study how the test `memory`
  sink wires in and follow the established seam). mlflow imports ONLY
  inside methods (purity gate). Design around the gotcha the TODO names:
  `_Trackers` swallows per-sink exceptions (`driver.py:139-153`) so a
  misconfigured sink is silent — therefore the SINK validates its config
  loudly at construction/plan time (default-deny params; unreachable
  tracking URI fails the plan, not the run). File/local URI default so
  tests need no server.
- Done when: run with sink configured lands params (E1's) + metrics in a
  local mlflow store in tests (importorskip mlflow); registration seam
  documented; suite/ruff green; hashes unmoved (tracking config must stay
  hash-excluded — verify against the `env`/`outputs`/`schedule` exclusion
  list and pin where it belongs).

**E4 — continuous ranges reach `OptunaSearch`** · parallel-safe
- Agent opus/high · Skeptics 2×opus/high · TDD: flip-the-pin
- Files: `dskit/pipeline/planner.py` (`_search_errors`),
  `libs/optuna.py:43-50` (INTEGRATION FLAG comment), tests
  (`tests/pipeline_libs/test_optuna.py` has the pin that flips;
  `tests/pipeline/` planner tests), possibly NEW
  `examples/pipeline/optuna-continuous.json`.
- Do: teach the planner's space-grammar check the spec-dict form
  `{"low","high"[,"log"][,"int"]}` ALREADY validated by
  `libs/optuna.py:_spec_problems` — planner stays tier-1 (no optuna
  import; the grammar is generic). `hpo-grid` must KEEP refusing
  spec-dicts (exhaustive enumeration over an interval is meaningless) —
  that refusal gets its own pin if it lacks one. Both forms share the
  `"<node>.<param.path>"` key grammar. Remove the integration-flag
  comment; leave pruning absent (per TODO: nothing to prune against until
  a per-epoch reporting seam exists — do not build one).
- Done when: continuous document plans + runs e2e; categorical grid
  behavior byte-identical; existing example hashes unmoved; new example
  (if added) enters the ledger.

**E5 — model-sweep verify + cookbook + lightgbm extra** · parallel-safe
- Agent opus/medium · Skeptics 2×opus/high · TDD: verify-first
- Files: NEW `examples/pipeline/model-sweep.json`; `pyproject.toml`
  (`lightgbm = ["lightgbm>=4.0"]` + `all`); sklearn pack docs (estimator
  table in `libs/sklearn.py` module docstring or pipeline README — follow
  where the pack documents itself); tests.
- Do: FIRST verify by running (TODO's own instruction — the claim is from
  reading, not a run): a document sweeping
  `space: {"model.estimator": [...]}` over `SklearnFit` with
  linear/ridge/RF/GBM/SVR/kNN plans + runs e2e and picks a winner. Then
  ship it as the cookbook example with rich `notes` (including the
  `"lightgbm:LGBMRegressor"` spelling — keep the shipped space
  sklearn-only so the example runs without the extra; an
  importorskip-lightgbm test covers the LGBM path). Do NOT write wrapper
  classes — the doorway is the point ("estimator is named by the
  document").
- Done when: e2e test green (sklearn present), lightgbm path tested under
  importorskip, example in the hash ledger, docs table shipped.

**B1 — 3b: `TorchAdapter` becomes a real ABC** · torch chain #1
- Agent opus/medium · Skeptics 2×opus/high · TDD: yes
- Files: `dskit/pipeline/libs/torch.py:315` region, tests
- Do: the four hooks that raise `NotImplementedError` become
  `@abstractmethod` (ABC/ABCMeta per pillars: "abstract means abstract —
  refuse at construction"). Red-first: a test instantiating an incomplete
  adapter currently succeeds → assert `TypeError` on construction. Two
  in-repo subclasses must still construct; import-path adapters are
  checked structurally by conformance and per TODO are unaffected —
  VERIFY that claim, don't assume it.
- Done when: incomplete adapter refuses at construction; suite green;
  hashes unmoved.

**B2 — `loss` knob in the torch pack** · torch chain #2 (after B1 merges)
- Agent opus/medium · Skeptics 2×opus/high · TDD: yes
- Files: `libs/torch.py` (`RowVectorAdapter.loss:493`, `_BASE_PARAMS` /
  `_EXTRA_PARAMS` — read both + base classes per CLAUDE.md), tests
- Do: expose `loss` as a declared import-path param EXACTLY the way
  `optimizer` is exposed (same validation, same resolution, same docs) —
  default `torch.nn.functional:mse_loss` preserved. Emitted-only-when-
  present: existing documents unchanged → hashes unmoved (verify). Test:
  a document selecting Huber (`smooth_l1_loss`) trains and the selection
  provably reaches the training step.
- Do-NOT: build a loss registry; the import-path doorway is the pattern.
- Done when: knob declared/validated/documented; default behavior
  byte-identical; suite green.

**B3 — 3e: split `kinds_flow.py` → `kinds_banking.py`** · parallel-safe
- Agent opus/medium · Skeptics 2×opus/high · TDD: refactor rules (§4)
- Files: `dskit/pipeline/kinds_flow.py` (1549 lines), NEW
  `dskit/pipeline/kinds_banking.py`, `dskit/pipeline/__init__.py`, tests,
  pipeline README/CLAUDE trees.
- Do: move the banking/admission chain — `EventBank` (:267, role
  `accrual`), `Eligibility` (:422, role `gate`), `BankingReport` (:473,
  role `report`) — into the new module. TWO named traps from the TODO:
  (1) both modules' `register()` must stay reachable from
  `dskit/pipeline/__init__.py` or documents naming those kinds stop
  resolving; (2) re-export the shared helpers (`_reject_unknown`,
  `is_node_ref`) from `kinds_flow` for one release — sibling modules
  import through it today. Kinds register by NAME → no document changes,
  no hash movement (the proof this was mechanical).
- Do-NOT: split `torch.py`/`conformance.py`/`driver.py` (TODO says
  explicitly they are not this problem); rename any kind.
- Done when: suite green with zero test edits beyond imports; hash gate
  clean; both modules' docstrings/trees current.

### ADR drafts (parallel with Wave 1; fable/high; 3×fable/high skeptics)

Each draft is a complete decision-log entry (Context / Decision /
Consequences, house style — read ADR-0033…0037 as models) written to a
scratch file, NOT yet committed to the log. Drafters read the cited code
first; every claim about current behavior must carry a file:line.

**ADR-A — `TrainableNode` (3c)**
- Spec: TODO "3c". A base class so trainable nodes stop branching on
  `mode` — deletes nine `if mode ==` sites across
  torch/sb3/transformers/sklearn/synthetic. HARD CONSTRAINTS the draft
  must honor and restate: `mode` stays a param INSIDE the identity hash
  (splitting train/load classes measured `4039ddf1… → 2c9d9925…` — moves
  every hash); `transformers.py:612` refuses any artifact whose sidecar
  `node_class` mismatches → class names of existing trainables must not
  change; port order sklearn → synthetic_nodes → torch → transformers →
  sb3; `conformance.py:1214`'s bytecode sniff for `mode` becomes a
  STRUCTURAL check (child packs subclass against the bar). Decide the
  hook set (train/load/save seams), how `run()` template-methods the mode
  dispatch, and what the conformance bar asserts.

**ADR-B — `foreach` document-grammar section**
- Spec: TODO "Flexibility" item 2. Fan-out over a DECLARED key list ONLY
  — expanding a subgraph per key (nodes, inputs, space keys), the
  `run_walk_forward` derive-N-documents precedent generalized. Explicitly
  identity-bearing (a `foreach` changes what the run computes — in the
  hash, unlike `notes`). NOT general templating (no expressions, no
  conditionals — "configs must not become a programming language").
  Define: grammar, expansion point (document load? plan?), node-name
  suffixing, how `concat`-style joins reference the expanded set, hash
  semantics, validate errors. Existing documents' hashes must be provably
  unmoved (optional-absent field).

**ADR-C — gap-aware vectorized window transform (+ scaler)**
- Spec: TODO Configurability final item + the normalization item folded
  in. Extend `ArrayFeatures`/`libs/numpy.py` — do NOT build a new seam.
  Blockers to resolve: `_lift` welded to the MarketRecord envelope
  (`instrument`/`contract`/`mid`, `:106,154`) → declared
  key/order/value fields (this also settles audit HIGH-2: the pack must
  IMPORT tier-1 record rules, never restate `_price_ok`/`_lead_ok`);
  positional offsets silently bridge session gaps → gap-aware framing
  (the ONE thing child `WindowRows` got right, `nodes.py:220-228`). Ops:
  group, order, gap-split, log/pct return, lag N, lead N (forward label).
  Vectorized; the 2M-bar run is the benchmark (ADR-0037's 650 B/row is
  the spirit). Plus the SCALER: fit-on-train-only, carried to val/test —
  a stateful fitted transform, which the causality guard's purity
  assumption forbids in the existing transform family → design the
  fitted-transform story ONCE, coordinated with ADR-E (same seam or
  explicit siblings; the two drafts must cite each other and agree).
  End state: child `WindowRows` collapses to one `apply()` and inherits
  the lookahead screen; `live.py:latest_feature_row` stops existing.

**ADR-D — time-series architecture zoo (torch pack)**
- Spec: TODO zoo section. Decisions the draft makes concrete: ONE
  `torch-timeseries` node pair with an `arch` PARAM resolved through a
  builder registry (`_ARCHS` + `register_arch`) — TODO recommends this
  over pair-per-arch because `space: {"model.arch": [...]}` makes
  architecture a swept param (registry table = sanctioned middle ground,
  string-switch-in-run() = forbidden). Purity law: no `nn.Module`
  subclass at module level ANYWHERE in `dskit/pipeline/` (the gate scans
  class bodies too) — the sanctioned pattern is `_LinearModule`
  (`libs/torch.py:1274`): define the net INSIDE `build_module`; sidecar
  compares `build_module` FUNCTION identity, so artifact loading is safe.
  Archs: DLinear + NLinear FIRST (the honest baselines, Zeng et al.
  2023), MLP, LSTM, GRU, TCN, 1D CNN, PatchTST, GRU/LSTM+attention;
  N-BEATS excluded. `head` param (`"regression"`|`"binary"`) selects
  output layer + loss (consumes B2; binary markets are first-class —
  `records.py:176` accounting split). File placement decision (inside
  `torch.py` vs a sibling module) with the purity/registration
  consequences argued. Child follow-up in scope: `intraday_poc` drops
  hand-rolled `NextBarLSTM` and NAMES the zoo LSTM (the genericity
  proof); `models.py` stays as the bespoke seam.

**ADR-E — feature-selection seam**
- Spec: TODO model+feature-selection section. A selector is FITTED →
  cannot be a `transform` (the causality guard re-runs `apply` on
  truncated prefixes and refuses moved output — a fitted selector trips
  it by design) → own seam. Leakage is the one hard rule: fit on train
  ONLY, apply to val/test; the fit-split is DECLARED and checkable at
  plan time (the way `score` declares `split`). Shape: library-agnostic
  base with ONE hook returning surviving feature names; `libs/sklearn.py`
  supplies SelectKBest/RFE/SelectFromModel/VarianceThreshold/mutual-info
  BY IMPORT PATH (doorway, never a wrapper registry); `libs/torch.py`
  supplies importance-from-a-fitted-net through the SAME interface.
  Outputs carry the selected-feature LIST as an artifact (serving uses
  identical columns), plus projected rows. Governing class: builds on
  ADR-A's `TrainableNode` (parent or explicit sibling — decide WITH
  ADR-A/C, cite both). Show how all three owner flows (sweep-models /
  select-per-model / select-once-then-sweep) are document edits over ONE
  node.

**ADR-F — HPO × walk-forward semantics** (short decision entry)
- Spec: TODO experiments item 5. Today each fold re-tunes independently
  (defensible nested CV; costs folds×trials; yields no single shippable
  winner) and is UNTESTED. Decide the semantics (candidates: keep
  per-fold nested CV as the measurement story + a separate
  tune-once-then-freeze path for shipping; or pick one). Whatever is
  chosen: tests pin it (`tests/pipeline/test_walkforward.py` gets HPO
  cases) and the cost/winner story is documented. Implementation lands as
  C7.

### Gate G1 → Wave 2 — ADR implementations

**C1 — implement ADR-A: `TrainableNode` port** · torch+driver chains
- Agent fable/xhigh · Skeptics 3×fable/high · TDD: yes
- Files: per ADR-A — `base.py`/`node.py` seam, `conformance.py:1214`,
  `libs/sklearn.py`, `synthetic_nodes.py`, `libs/torch.py`,
  `libs/transformers.py`, `libs/sb3.py`, tests throughout.
- Do: port in the ADR's order (sklearn → synthetic → torch → transformers
  → sb3), each pack green before the next. The conformance bar's bytecode
  sniff becomes the structural check the ADR defines. ALL nine
  `if mode ==` sites die.
- Hard invariants: every identity hash unmoved (engine change!);
  transformers sidecar still loads existing-style checkpoints (test it);
  no trainable class renamed.
- Done when: zero `if mode ==` in trainables (grep-proof), conformance
  structural check red-first-proven, full suite green, hashes unmoved.

**C2 — implement ADR-C: window transform + scaler; child collapses** · owns `libs/numpy.py`; child chain #3
- Agent fable/xhigh · Skeptics 3×fable/high (+perf lens) · TDD: yes
- Files: `dskit/pipeline/libs/numpy.py`, child `nodes.py` (`WindowRows`),
  child `live.py` (`latest_feature_row` deletion), tests both sides.
- Do: per ADR-C. Includes audit HIGH-2 (numpy pack imports tier-1 record
  rules — the silent `_WRITEBACK` drop dies loudly or by import).
  Vectorization evidence: a benchmark-style test or measured note against
  the 2M-bar scale (tracemalloc/vector-shape pin in the ADR's terms —
  peak-pinning tests are the house precedent, `test_observations.py`).
  Child `WindowRows` becomes one `apply()` call and INHERITS the
  causality screen; gap framing preserved (`nodes.py:220-228` semantics
  pinned before the rewrite — write the pin against current behavior
  FIRST, then port).
- Done when: ops (group/order/gap-split/returns/lag/lead) tested
  including gap-boundary cases; scaler fit-on-train-only refuses leakage
  by test; child parity pinned (same windows before/after); hashes: child
  doc moves only if the ADR says params change (declare); engine
  examples unmoved.

**C3 — implement ADR-B: `foreach`** · driver chain #3 (after C4)
- Agent fable/high · Skeptics 3×fable/high · TDD: yes
- Files: `dskit/pipeline/document.py`, `planner.py`, `driver.py`,
  `validate` path, tests; maybe an example doc (ledger).
- Do: per ADR-B. Expansion covered by plan/validate tests (duplicate-name
  collisions, empty key list, hash semantics). Existing documents:
  hashes PROVABLY unmoved.
- Done when: a two-key foreach document runs e2e; longhand equivalence
  test (foreach expansion ≡ hand-written expansion, node for node);
  suite green.

**C4 — 3d: decompose the long methods** · torch chain #4 + driver chain #2 + `kinds_report.py`
- Agent opus/high · Skeptics 2×opus/high · TDD: refactor rules (§4)
- Files: `driver.py:730` `run_document` (399 lines — its six phase
  comments ARE the boundaries), `kinds_report.py:1227` `RunReport.run`
  (252, no docstring), `libs/torch.py:960` `TorchTrain.run` (230, no
  docstring). May land as 2–3 sub-branches in chain order.
- Do: one job per method; the extracted methods get NumPy docstrings;
  meaningful edits → convert those modules + drain their ignore entries.
  `WindowRows.run` is C2's (note only). `conformance.py` explicitly
  exempt (pytest-class factory).
- Done when: zero behavior change (suite green, hashes unmoved,
  no test edited except additions), section comments replaced by
  method names.

**C5 — implement ADR-D: the zoo (+ child model switch)** · torch chain #5; child chain #4
- Agent fable/xhigh · Skeptics 3×fable/high · TDD: yes
- Files: per ADR-D (torch pack ± sibling module), child `models.py`,
  `nodes.py?`, both run documents, pyproject only if the ADR says so,
  pipeline docs trees, tests.
- Do: registry + `register_arch`; DLinear/NLinear first, then the list;
  `head` param wiring through B2's loss; conformance over the new pair;
  determinism/seed tests per arch (small shapes — CPU, keep it fast);
  sweepability e2e: a small document sweeping `model.arch` picks a
  winner. THEN the child: documents name the zoo LSTM, hand-rolled
  `NextBarLSTM` deleted, `models.py` kept as the bespoke seam with its
  docstring saying so. Child hash moves — intentional, declare.
- Done when: all archs construct + train on toy shapes; sweep e2e green;
  child suite green on the zoo model; purity gate untouched and passing.

**C6 — implement ADR-E: selector seam** · torch chain #6 (importance leg)
- Agent fable/xhigh · Skeptics 3×fable/high · TDD: yes
- Files: per ADR-E — core seam module, `libs/sklearn.py`, `libs/torch.py`
  (importance leg LAST in the torch chain), conformance, tests, docs
  trees.
- Do: per ADR-E. Leakage refusal is the flagship test: a document whose
  selector declares a non-train fit split is refused AT PLAN; a selector
  that would see val rows refuses at run. Feature-list artifact round-
  trips (serving reads identical columns). Doorway: sklearn selectors by
  import path; torch importance through the same hook.
- Done when: three owner flows demonstrated in tests as document edits
  over one node; suite green; hashes of existing docs unmoved.

**C7 — implement ADR-F: HPO×WF semantics** · after C1/C4 land
- Agent opus/medium · Skeptics 2×opus/high · TDD: yes
- Files: `driver.py`/`walkforward` path per ADR-F,
  `tests/pipeline/test_walkforward.py`.
- Do: whatever ADR-F ruled, tested: HPO-in-fold behavior pinned
  (winner-per-fold surfaced, or freeze path), cost documented.
- Done when: `test_walkforward.py` has HPO cases; suite green.

### Wave 3

**D1 — selection demo: select features, sweep 2 models, use the best** (owner-added task)
- Agent fable/xhigh · Skeptics 3×fable/high · TDD: yes — full loop, no shortcuts
- Files: NEW `examples/pipeline/selection-demo.json`, an e2e test in the
  repo's example-test idiom (see `TestExampleDocument` in
  `tests/pipeline_libs/test_optuna.py`), docs mention.
- Do: the very SIMPLE pipeline, on synthetic/simple data (no `./ob`
  dependency): feature-selection node (C6) fitted on train → search over
  TWO estimators (E5's doorway, e.g. ridge vs random forest) → the
  winning model scored/used downstream — demonstrating owner flow #1-2
  end to end in one small document. Rich `notes` (this doubles as the
  cookbook's worked selector example). The demo must prove: selected
  features are the ones the winner consumed (artifact check), and the
  winner actually beat the loser on the declared metric.
- Done when: document plans/runs e2e in tests; hash in ledger; every
  skeptic round clean; orchestrator verified.

### Wave 4 — capstone (sequential; orchestrator-supervised)

**D2 — re-acquire `./ob`** · child chain #5
- Agent opus/medium (operational; skeptics not required — verification is
  mechanical) · TDD: n/a
- Do: from `children/intraday_poc/`, follow the README recipe exactly:
  env from `.env` (present; NEVER print values), `onboarding init --root
  ./ob` → `register-source alpaca … --config @configs/source-backfill.json
  --activate` → `acquire --mode backfill` → `validate` (suite-bars) →
  `certify` → `publish`. ~2M bars (AAPL+MSFT 1-min, 2021→now) through the
  REST pack — run as harness-tracked background in ≤10-min chunks
  (cursors resume per source/stream/mode; re-invoke until caught up).
  Durable path only (`~/dskit/children/intraday_poc/ob`). RAM well under
  18 GB (ADR-0037's 650 B/row makes the read side ~1.3 GB).
  If keys are invalid/expired: STOP the wave and surface to the owner —
  credentials are his; do NOT hunt for alternatives.
- Done when: `verify` passes on the published store; bar count reported.

**D3 — capstone: wire HPO into `intraday_poc` and run it for real** · child chain #6 · LAST
- Agent opus/high · Skeptics 2×opus/high (config diff + wiring review) · TDD: config-pin rules
- Preconditions (assert every one before dispatch — they are the reason
  this is last): monitor set (A1); epochs pinned (A1); log_params carries
  node params (E1); sink ships (E3); runs verb (E2); continuous ranges
  possible (E4); foreach available (C3); HPO×WF ruled (C7/F); `./ob`
  present (D2).
- Do: per the TODO capstone card: start SMALL — `hpo-grid` (or
  optuna-search if continuous lr is wanted; start categorical
  `hidden_size: [16, 32, 64]`) on `run-train.json` ONLY; walk-forward
  untouched. Use `foreach` for the two symbols if it makes the space
  keys single-sourced (its first real consumer); otherwise duplicate keys
  WITH a pin. Attach the mlflow sink (local file store) + confirm the
  runs verb shows the trials. RUN IT on the real store. Verify: trials
  distinguishable by params in the sink; winner reproducible (same seed →
  same winner); report the numbers.
- Then (bonus, closes the ADR-0037 residual note): re-run
  `run-backtest.json` walk-forward end to end on the re-acquired store —
  the run that was blocked when the store left the machine. Report RSS
  peak vs the 2.6 GB precedent.
- Done when: a real tracked experiment exists on this machine, comparable
  in the sink and the runs verb; backtest re-run green; child hashes
  updated in ledger.

### Wave 5 — closeout

**D4 — mark `TODO.md` + refresh its baseline block**
- Orchestrator directly (sonnet/low assist allowed) · docs-only
- Every in-scope box → `[x]` with a landed note in the house style
  (`**Landed via ADR-00NN (2026-08-DD).**` / `**Done this run …**`).
  The drain item gets the owner's ruling verbatim (touched-only,
  ongoing-by-rule). The TODO verification-recipe baseline hashes are
  updated to the post-run values (they moved intentionally: A1/A2/C5/D3
  child edits, any new examples). Long-term sections and out-of-scope
  items untouched.

**D5 — wrap** (the very end)
- Orchestrator directly. `.claude/commands/wrap.md` is a repo slash
  command your session may not auto-load — read it and execute manually:
  1. Refresh `docs/RE-ENTRY.md` — brief: what landed (by ADR/card), test
     count, hash-ledger note, capstone numbers, what remains (out-of-scope
     items, long-term sections).
  2. Commit everything outstanding; merge any straggler branch only if
     coherent + green (else say why not).
  3. `git push` `main` (and `-u origin <branch>` for any kept branch).
  4. Report ≤300 chars.

---

## 9. Baseline ledger (captured 2026-08-27, commit `93ed7e2`)

Suite: **2440 passed / 108 skipped, ~29s** (venv `.[all,dev]` +
stable-baselines3 + matplotlib + alpaca-py). ruff: clean (verify at
startup). Identity hashes (`python -m dskit.pipeline validate <f>`):

| Document | sha256 |
|---|---|
| examples/pipeline/mpl-figure.json | `e9d5f60c3676ca77…` |
| examples/pipeline/nodemap-minimal.json | `5ba8f4d62b2032f0…` |
| examples/pipeline/numpy-features.json | `7df9b26e55fc3b8c…` |
| examples/pipeline/optuna-search.json | `687f9292d7c908c2…` |
| examples/pipeline/pyomo-solve.json | `f74da200f293f7d4…` |
| examples/pipeline/sb3-train.json | `149bd150b5691ef9…` |
| examples/pipeline/sklearn-fit.json | `7355bfce12bf2128…` |
| examples/pipeline/synthetic.json | `4351c116ab2271e2…` |
| examples/pipeline/torch-declared.json | `4039ddf167fa65db…` |
| examples/pipeline/torch-train.json | `0b14798b3146d98a…` |
| examples/pipeline/transformers-fit.json | `523072fa7103fae2…` |
| examples/pipeline/walk-forward.json | `54197909aecaee05…` |
| children/…/configs/run-backtest.json | `4db5b7904d19b73c…` |
| children/…/configs/run-train.json | `187658f8b58b91a1…` |

(Full 64-char values: recompute at startup; the validate loop in §1.3
prints them. Prefixes above are for eyeballing drift.)

Intentional-move log (append `document → old-prefix → new-prefix → card`):
- children/…/configs/run-train.json → `187658f8b58b91a1…` → `85fff271bfdd05ec…` → A1
- children/…/configs/run-backtest.json → `4db5b7904d19b73c…` → `5e1c24b0fad3ae1b…` → A1
- examples/pipeline/optuna-continuous.json → NEW → `5560a479eacc071e…` → E4 (ledger now 15 documents)
- examples/pipeline/model-sweep.json → NEW → `c01ae84ec899e1d8…` → E5 (ledger now 16 documents)
- examples/pipeline/foreach-fanout.json → NEW → `242120e437f7adc6…` → C3 (ledger now 17 documents)

## 10. STATE — orchestrator updates as it goes

| Card | Status | Branch/SHA | Notes |
|---|---|---|---|
| A1 | MERGED | 3d8ccf6 | 5-round loop + orchestrator round-5 intervention (README skew twin, note brevity); monitor scoped to run-backtest only (run-train wires no val_rows — engine refuses; skew declared+pinned); child hashes moved as declared; suite 2440/108, child 74/11 |
| A2 | MERGED | c12aa0f | child chain #2 · 3 loop rounds + 6 orchestrator rulings; all 12 card points landed; credential rule REUSED not restated (`connectors.resolve_credentials`, one function both paths call — closed a regression that would have authenticated on blank keys); run doc read through the engine's `load_document`; `source-live.json` DELETED (one source, two modes); child run-*.json hashes unmoved; suite 2648/109. NOTE for owner: adds `import_library_class` to `base.__all__` (one line, pinned) |
| A4 | MERGED | 97ac471 | 5-round loop (10→6→5→1→1) + orchestrator needle-termination fix (mutation-proven); +_DEFAULT_PAGE_START bonus; ignores restored per touched-only ruling; suite 2483/108, hash gate empty |
| A5 | MERGED | 86816c0 | 5-round loop + orchestrator directive-disposal fixes (lineage envelope verified byte-exact); NOTE for owner: CLAUDE.md Docstrings gained an output-marking rule (# ->) ratified by the loop; suite 2483/108, hash gate empty |
| E1 | MERGED | 2e6fda9 | 4 loop rounds + limit-killed round, orchestrator arbitration (declared-tree keys / run values / references-as-declared; single log_params call) + 1 confirming round (4 findings) + directed fixes; suite 2479/108, hash gate empty. E3 UNBLOCKED |
| E2 | MERGED | fc316b0 | 5-round loop + orchestrator rulings (markdown.py renderer retrofit REVERTED — driver/kinds_report byte-identical to main; tolerance MAJORs fixed; refusal path prints notes; pins made structural) + confirming round + 4 directed fixes; suite 2547/108, hash gate empty |
| E3 | MERGED | c26018e | 3 loop rounds + 7 orchestrator rulings. Ruling 1 (make `tracking` hash-excluded as the card demanded) was implemented by a BETTER mechanism than the one ruled: popping the key would have moved all 11 example hashes, because every hash ever written counted a `"tracking": null`; `NULLED_IDENTITY_SECTIONS` renders it undeclared instead — equally excluded, canonical JSON byte-identical, ledger provably unmoved. Rogue ADR-0038 entry reverted. Suite 2717/109 with mlflow installed (67 pack tests run, not skipped) |
| E4 | MERGED | d24a088 | planner half was ALREADY in baseline (stale TODO claim); shipped the proof layer: continuous e2e, optuna-continuous.json example (ledger), hpo-grid refusal pins, flag retired; orchestrator round-5 fixes (ref-driven deferral truth ×4 sites, _strip_notes import); suite 2561/108 |
| E5 | MERGED | d33385a | verify-first RAN the sweep (RF wins 6-candidate e2e); TWO TODO corrections proven by running (colon spelling refused — estimator grammar is dotted; mixed sweep cannot carry estimator_params/seed); lightgbm extra self-sufficient (transformers precedent); model-sweep.json in ledger c01ae84e…; suite 2598/109 |
| B1 | MERGED | b3237a7 | torch #1 · 5-round loop + orchestrator round-5 rulings (plan-time guard kept; sentence-equality pin + _ADAPTER_SUBJECT; docstrings to standard); new core helper abstract_class_problem; suite 2456/108, hash gate empty |
| B2 | MERGED | ec0a3c7 | torch #2 · 5 loop rounds + orchestrator rulings (rogue ADR-0038 entry REVERTED — numbering is G1's; objective threaded from the node's one read so adapter_params cannot shadow it; promise re-keyed on both hooks; duck-typed adapters keep pre-knob behaviour); suite 2647/109, hash gate empty |
| B3 | MERGED | d157848 | CLEAN loop (3 rounds, no intervention — first); AST-identity proof of the mechanical move; kinds_flow converted + ignore drained; suite 2577/108, hash gate empty |
| ADR-A…F | ACCEPTED | a1aa3e7 | ADR-0038…0043 in the decision log. Drafted in parallel, 5 adversarial rounds each, then CONDENSED by the orchestrator: 2164 → 583 lines, because review had driven them far past house style (25–62) into specifying mechanism the log should not carry — and the over-specification was generating its own contradictions. Substantive rulings folded in, not sent round again (see the commit message) |
| G1 | PASSED | a1aa3e7 | All 12 W1 cards merged + all six ADRs approved. Cross-checks done: 0042 is a MEMBER of 0040's fitted-transform family (not a second seam), both build on 0038's TrainableNode, 0041's head consumes B2's loss knob, every record states its identity-hash consequences. **Wave 2 is open.** |
| C1 | MERGED | 5548780 | ADR-0038 ported across five packs. FIRST card under the new convergence floor: clean in 2 rounds (7→5 findings, no BLOCKER/MAJOR surviving) using 8 agents against wave 1's ~15 — and the first run with skeptics SERIAL per the owner's no-parallel ruling. Nine `if mode ==` sites dead; hash gate empty; suite 2718→2746 |
| C2 | pending | | |
| C3 | MERGED | aaf6932 | ADR-0039. Clean in 3 rounds; 44 new tests, 6 mutation proofs. Flagship holds: a two-key foreach expands to its longhand twin node-for-node and both run e2e byte-equal. New example foreach-fanout.json in the ledger (17 docs). planner.py ignore entry drained; driver.py left for C7 by ruling. Test scope run: tests/pipeline (1410 passed) + ruff whole-tree + hash gate. NOTE for a future card: the `synthetic_nodes` demo classes accept ANY unknown param (they predate default-deny and are private/demo-registry only) — surfaced while pinning that a non-search kind carrying `space` is refused at plan; not a foreach defect, would be its own card across 11 classes |
| C4 | MERGED | 59922ba | clean in 2 rounds / 6 agents. CORRECTED THE CARD: I had told it torch.py held no long method left; it measured by AST and found ADR-0038 had RENAMED `TorchTrain.run` to `run_train`, still 216 lines. Six bodies decomposed, not four. Ceiling now PINNED (test_method_lengths.py, allowlist grows as modules are decomposed); kinds_report.py ignore entry drained, driver.py/torch.py deferred to C3/C6 by ruling. Suite 2753, hash gate empty |
| C5 | pending | | |
| C6 | pending | | |
| C7 | pending | | |
| D1 | pending | | |
| D2 | DONE | (no code; gitignored store) | Store was ALREADY present (1.2 GB, Aug 26) — the plan's "not on this machine" was stale, so this was catch-up not re-acquisition. 2,016,587 bars, 2021-01-01 → 2026-08-28T19:06Z, 0 duplicates, contiguous seam, verify clean before AND after, all CLI steps exit 0, peak RSS 138 MB. Also PROVED A2's single-source `--mode` rewrite is a behavioural no-op vs the pre-A2 store (identical resolved knobs; no re-registration). `--mode live` NOT exercised — D3 is the first consumer |
| D3 | pending | | capstone |
| D4 | pending | | |
| D5 | pending | | wrap |
