# Kickoff — resume the `dskit.production` build (paused 2026-09-05)

Paste everything below the rule as the first message of the new session. It
contains (A) the original mandate verbatim, (B) what the first session did,
decided and found, and (C) the resume order. The companion files in this
directory (`BRIEF.md`, `BUILD-LOG.md`, `SEAM-DESIGN.md`) hold the full detail.

---

# Build `dskit.production` end to end — autonomous run, RESUMED

Work on branch `claude/dskit-production-build-3g17vw` (already pushed; off
`main` @ 03d797c). Never commit directly to `main`. Read, in this order, before
doing anything: `docs/RE-ENTRY.md`;
`docs/new_package_proposals/build-notes/BUILD-LOG.md` (the durable memory:
group status, every pinned API decision, rulings R1–R8, plan gaps, the "TO
RESUME" list — binding unless the plan contradicts it, in which case stop and
reconcile); `docs/new_package_proposals/build-notes/BRIEF.md` (the shared
brief every subagent reads — copy it to your scratchpad and point every agent
at it); `docs/new_package_proposals/build-notes/SEAM-DESIGN.md` (the binding
design for the ADR-0091 seam); the plan; `CLAUDE.md` and `AGENTS.md`.

## A. The original mandate (verbatim)

Build the whole package — phases 1, 2, 2b and 3 — to the plan already approved
and merged. Work until it is complete and green, then wrap. Take as long as you
need.

### Where everything is

- Trunk is `main`, and it already carries the approved plan. Work on a branch off
  `main`, never commit directly to it.
- **The plan is `docs/new_package_proposals/production.md`.** It is the contract.
  Read it fully before writing anything. §4–§7 are the contracts a test can be
  written from; §8 is the file-by-file structure; §10 is the TDD order; §5.16 is
  the producer table that says which object fills every record field; §11 is the
  phase table.
- ADR-0090 and ADR-0091 are in `docs/architecture/decision-log.md` as `proposed`.
  Flip both to `accepted` in the final commit.
- `docs/new_package_proposals/check_plan.py` is an authoring tool for the plan,
  not a test. **Run it after every plan edit.**
- Repo law: `CLAUDE.md` and `AGENTS.md` at the repo root. Read both. Tier rules,
  default-deny params, `__all__` as the API contract, NumPy docstrings with no
  type hints in signatures, `ruff select = E4,E7,E9,F,D`.

### Scope — everything

- **Phase 1** — every §8 module not marked `[phase 2]`, the ADR-0091 pipeline
  change in §9.1, the §9.2 skeleton updates, the §9.3 doc updates.
- **Phase 2** — `outcomes.py`, `report.py`, the `replay` / `outcomes` / `report`
  verbs, the Outcome and Parity monitor families, DDM/ADWIN/JensenShannon/LInf,
  alert inhibition / silences / escalation / ack with sqlite state, the systemd
  heartbeat emitter, `libs/sqlite.py`, `libs/parquet.py`, `Signer`, and the
  `approve` verb for `hold`.
- **Phase 2b** — widen the `serving_effect` audit past the ~13 classes phase 1
  classifies, so a serve document can reference more than the audited set.
- **Phase 3** — `exchange_calendars`, `prometheus` / `opentelemetry` sinks, the
  stream seam, and migrating the onboarding packs onto `resilience.py` (that
  migration carries its own ADR — write it).

### Stage 0 — specify phase 2 before building it (mandatory)

**Phase 1 is specified to a buildable standard; phase 2 is not.** Its modules get
roughly one bullet each in §5.13 — no method contracts, no place in §10's order,
no rows in §5.16. Building from that means inventing contracts, which is the
failure mode ten rounds of design review were spent removing.

So before any phase-2 code:

1. Write the missing §5 contracts to the same standard as phase 1 — class,
   constructor, every method signature and return type, refusals, and the closed
   vocabularies they use.
2. Add every new record and value object to §5.16's producer table, and every new
   closed set to `vocab.py`'s list in §8.
3. Extend §10's module order with the new modules and state the dependency reason
   for each placement, as the existing entries do.
4. Run `check_plan.py`. It must be CLEAN.
5. **Skeptic-review the specification itself** before writing a line of phase-2
   code, on the same bar as the code reviews below.

Do the same, proportionally, for phases 2b and 3.

### Method — non-negotiable

**TDD, in §10's module order.** For each module:

1. **Tests first, red.** Write `tests/production/test_<module>.py` from §4–§7
   alone. If the plan does not say something you need, that is a plan gap: handle
   it per "When the plan is wrong" — do not invent a contract silently.
2. **Implement to green.** Smallest thing that passes.
3. **Skeptic review** of the test/code pair before moving on.

Never write implementation before its test exists and fails.

**Model assignment:**

- **Fable** — first code build of each module.
- **Opus** — all test authoring, all specification work (Stage 0), all
  first-round reviews, and any rebuild after a review sends a module back.
- **Sonnet** — reviews after the first round on a given module.

**Concurrency: at most 5 subagents at once.** Parallelise aggressively within
that cap — modules with no dependency on each other are the obvious candidates,
and §10's order tells you which those are. Never exceed 5.

### Skeptic reviews

Adversarial, not a rubber stamp. Every review judges:

- **Contract fidelity** — does the code do what §4–§7 say, *including the
  refusals*? Most of this plan is about what must not happen.
- **The OOP ruling in §5.15** — abstraction, encapsulation, inheritance for is-a
  only, real polymorphism rather than disguised type switches. `Tick` and
  `LegPipeline` are concrete template methods with **final** `run`; the ten phase
  methods and eight leg steps are the hooks.
- **Safety** — money movement, authority and permits, barrier ordering, digest
  definitions and bindings, freshness gates, crash recovery. A defect here
  blocks; bookkeeping does not.
- **Tests that can actually fail.** A test that restates its own literal is worse
  than none. Check the assertions bite.

Loop review → fix → review until clean. Escalate to Opus if two Sonnet reviews in
a row find blockers.

### The invariants that must hold at every commit

- The four existing purity gates stay green
  (`tests/{assets,journal,onboarding,pipeline}/test_purity.py`), plus the new
  `tests/production/test_purity.py`.
- **Every pinned identity hash unmoved** — the 20 sha256 literals under `tests/`.
  ADR-0091 touches `dskit/pipeline/driver.py`; if a pinned hash moves you have
  changed behaviour and must stop and reconsider, not re-pin.
- `tests/pipeline/test_driver.py` and `tests/pipeline/test_kinds_search.py` pass
  untouched — the regression guard for the seam extraction.
- `ruff` clean. The skeleton pin in `tests/children/test_skeleton.py` updated in
  the same commit as any skeleton change.
- Targeted tests while iterating; full suite at phase boundaries and before wrap.

### Highest-risk areas — spend your review budget here

Ten rounds of design review kept finding real defects in these:

1. **ADR-0091's seam extraction** (§9.1). `SubgraphRunner` takes `needed` as a
   constructor argument, mutates a caller-supplied `outputs`, and its
   `prev_bindings` is an OUT parameter. The search-only override validation
   (`unsearchable_space_why`) **stays with `_SearchSeam`** — serving overrides a
   `data`-role node, which search forbids. `rerun` honours `policy.defer(key)`.
2. **The leg pipeline** (§5.13.1). Barrier before every effect. Steps (2)/(3)/(6)
   take a **fresh** `SeriesState.snapshot()` and refuse on drift of the members
   the plan and intent already bound.
3. **The reduction/flatten path.** `ReductionIntent` and `Intent` are different
   objects with different digests, never spelled alike.
   `reduction_intent_digest` is what the single-use right names.
4. **`SeriesState` is the sole ledger fold.** Nothing else folds.
   `Ledger.append` calls `apply`.
5. **Crash cuts.** Cut after every barrier and immediately before and after
   native I/O.
6. **`cash_flow` and `tick.nav`.** Four of the five capital metrics (`twr`,
   `mwr`, `cumulative_contributions`, `equity_curve`) cannot be reconstructed
   after the fact. Every economic measure partitions the fold on `external`:
   `pnl`, `drawdown`, `consecutive_losses` see trading records only;
   `bankroll_fraction` and `exposure` see the capital base including external
   flows. An external flow changes what you have, never what you earned — get
   this wrong and an adopted deposit turns a trading loss into headroom under a
   `halt` guard.

### When the plan is wrong

It will be. When you hit a gap or contradiction:

1. **Bookkeeping** (missing producer row, wrong count, naming slip): fix the
   plan, note it in the commit, keep going.
2. **Safety-critical** (money movement, authority, barriers, digests, freshness,
   crash recovery): stop that module, write the finding up, decide the smallest
   correct fix, apply it to *both* plan and code, and have Opus review that
   decision specifically.
3. **Never merge a plan change without a skeptic review.** A change to this plan
   that skipped review introduced four blockers including a money-safety hole.
   The checker does not catch design defects.
4. Plan and code must agree at every commit.

### Journalling

Each mutating CLI verb journals once via `record_production`. Note
`dskit/journal/record.py` no-ops under pytest, so production calls the journal
through an injected `journal_hook` seam (D22).

### Wrap

1. Full suite, `ruff`, all five purity gates, every pinned hash unmoved.
2. Flip ADR-0090 and ADR-0091 to `accepted`. Add the onboarding-backoff migration
   ADR if you did phase 3's migration.
3. `TODO.md`: check off what landed.
4. `README.md`, `CLAUDE.md`, `AGENTS.md` trees and the exit-code line per §9.3 —
   including `README.md:47-48`, which carries its own third variant.
5. Commit on your branch.
6. **Merge decision:** fully green, every invariant holding, no safety-critical
   finding open → merge to `main` and push. Otherwise push the branch and open a
   PR saying exactly what is not green and why. Never merge a red or partial
   build.
7. Leave a written summary: what landed, what did not, what the plan got wrong,
   and anything a human needs to decide.

Work autonomously. Do not stop for approval on anything the plan already decides.

## B. What the first session did, decided and found

**Environment.** Python 3.11 sandbox running as root. `pip install -e ".[dev]"`
plus `pip install numpy scikit-learn joblib pyarrow optuna pyomo highspy`
reproduces a fully green baseline (3407+ passed). Two pre-existing failures are
root-only artefacts, not ours: `tests/pipeline_libs/test_mlflow.py::
TestBusyLocalStore::test_an_unwritable_parent_still_fails_the_plan` and
`tests/pipeline/test_runs.py::TestLostMeasurements::
test_an_unlistable_nodes_dir_is_named_not_fatal`. A pre-existing ruff D103 in
`check_plan.py` was fixed on the branch. `check_plan.py` is CLEAN.

**Orchestration shape that worked.** One Opus test author per group of 1–3
modules (writes only under `tests/`), then one Fable implementer per group
(reads the tests + plan sections, writes only its modules), then an Opus
skeptic reviewer with fix authority, then Sonnet. Every agent reads
`BRIEF.md` and reports in its format; the orchestrator appends each report's
pinned decisions to `BUILD-LOG.md`, rules on gaps, messages running agents
about new rulings, and commits per group. Conventions fixed for the whole
package are in `BRIEF.md` (ProductionError shape, `Registry`, canonical
bytes/hash, `cls(params)` construction, frozen value objects with Decimal
money and epoch-ms ints, digests as methods, closed sets only in `vocab.py`,
injected clocks).

**Green and committed.** G1 foundations — `dskit/production/{__init__,vocab,
base,redact,records}.py` with `tests/production/{test_vocab,test_base,
test_redact,test_records,test_purity}.py` (510 passed). G4 ledger —
`dskit/production/ledger.py` with `tests/production/test_ledger.py` (154
passed). Neither has had its skeptic review yet.

**Red tests committed (module not yet built).** `tests/production/`:
`test_document`, `test_release`, `test_clock`, `test_sessions`,
`test_cadence`, `test_state`, `test_resilience`, `test_metrics`, `test_ids`,
`test_bundles`, `test_policy` (+ `policy_golden.json`, 17,280 cells, 1.65 MB —
the owner may prefer a submit-only table), `test_monitors`, `test_breaker`,
`test_arming`, `test_coordination`, `test_guards`. `tests/pipeline/`:
`test_subgraph_runner`, `test_execution_policy`, `test_serving_effect`,
`test_serving_contract` (140 red for the seam; the driver/search suites and all
20 hashes verified unchanged by their author).

**Stopped mid-work.** F5 (`state.py`, may be absent or partial), F11 (the
seam — only `dskit/pipeline/policy.py` was started; run
`git diff main -- dskit/pipeline` and `git status` first), R1 (the G1+G4
review — not started).

**Rulings made (all logged in BUILD-LOG; all are plan edits pending the
batched skeptic review, so plan and code must be reconciled before merge).**
- `vocab.MONEY_FIELDS` (qty, notional, limit, price, fee, avg_price,
  filled_qty, remaining_qty, amount, total, available, payout, avg_cost,
  reference_price, exposure, nav, bid, ask, mid): the ledger refuses a float
  only under these names, recursively, a list under a money key inheriting
  the key; ratios (`confidence`, `prediction`, `expected_value`,
  `statistic`) may be float. `Finding.value/bound` and
  `MeasureEvidence.value` are Decimal-only (`Decimal(str(x))` for ratios).
- R1 nested ledger envelope: a record is `{kind, id, payload_digest, seq,
  series_id, process_id, release_hash, recorded_at_ms, schema_version,
  prev_hash, hash, body}`; the caller passes exactly `{"kind","id","body"}`;
  `body` may carry its own `kind`/`release_hash`/`series_id`;
  `payload_digest = canonical_hash({kind, id, body})`. Body field renames to
  stay self-describing: `authority.role`, `guard_state.state_kind`,
  `outcome.outcome_kind`, `cash_flow.flow_kind`,
  `control_approval.verified_payload_digest`.
- R2 `JsonlLedger.snapshot(payload)` takes the JSON payload
  (`state.to_snapshot_obj()`); body `{at_seq, state_digest, state}`.
- R3 `PositionBook` keeps a per-instrument applied-fill log since the last
  flat; `reverse(fill_id)` recomputes exactly from the log; the log is in the
  snapshot payload (restart-safe reversal).
- R4 `economic_seq` advances only on `order_event`, `fill`, `cash_flow` —
  never `intent`, `authority_use`, `outcome`, `adoption`.
- R5 `vocab.AUTHORITY_EVENTS = (issue, disarm, revoke, expire)`; the
  `authority` issue body embeds `arming: ArmingState.to_obj()` (ordinary) or
  `authorization: ReductionAuthorization.to_obj()` (reduction);
  `authorization` body = `{permit, authority_use_id}`.
- R6 the halt `trip` record is appended and barriered BEFORE cancel I/O and
  carries no `cancel_outcome`; a new record kind `cancel_outcome` `{trip_id,
  outcome ∈ CANCEL_OUTCOMES, acks[]}` follows the attempt (written on every
  entry into `halted`, `none` when nothing was cancelled); non-economic.
- R7 `ledger.validate_cache_head(head_seq, head_hash, ledger)` is the one
  owner of the cache-head rule; `Checkpoint`, `Breaker`, `Arming` import it.
- R8 `Accounting.snapshot(state_view, executor, quotes, at_ms, requirements,
  calendar)` re-anchors every requirement at its own `at_ms` (bounds from
  `window_kind` + `window_arg` via `Window.resolve(at_ms, calendar)`), keys
  evidence by the re-anchored digest and returns `AccountState.asof_ms ==
  at_ms`; guards rebuild the digest from `state.account.asof_ms`, so step
  (2)'s later snapshot still finds evidence.
- Other rulings: `vocab.CACHE_STATES = (current, stale)`;
  `vocab.TRANSITION_CAUSES = (reduce, flatten_request, trip, halt, resume)`;
  `ALERT_SUPPRESSIONS`; metric label VALUES for `sink`/`monitor` are the
  core kind names; D10's cells govern below-live reducing (shadow any
  effect, paper reduce only, live model-origin refused while reducing); GO
  required at live rungs in `reducing` as well as `active`; authority axis
  inert below live; `pending_control` blocks submit only; D9 live limits and
  "fsync none only at shadow" validated at document level via a rung-keyed
  table (no `if rung ==`); `Invocation` lives in `bundles.py`; `run_keys`
  added to `SubgraphRunner`'s API and `apply_param_override` made public with
  the private alias kept; only names in `env.require` are registered as
  credentials; the `test_vocab` completeness scan of §5–§7 moved to
  `check_plan.py` (already edited in the plan).

**Plan bookkeeping gaps still to fold into the plan** (see BUILD-LOG for the
full list): §8 lists `CALENDAR_WINDOWS` twice; `EXIT_CODES` is a map;
`Invocation` has no §8 home; `Ledger.close()`; `first_bad_seq` semantics;
`max_bytes` optional in every rotate mode; `durability.fsync` batch grammar;
`Coverage`'s §4.1 example should be `max: 0.5`; §5.14 "eight-field" → nine;
`FittedTransform._sidecar` is the real method name; §9.1 gains `run_keys`.

**Open items needing a human decision.** The golden policy table's size
(full product vs submit-only); whether the phase-1 audited set should include
the four synthetic pure kinds; both are recorded in BUILD-LOG.

## C. Resume order (keep five slots busy)

1. F5 — Fable builds `state.py` to `test_state.py` (R1–R6).
2. F11 — Fable builds the ADR-0091 seam to the four red pipeline test files
   per SEAM-DESIGN.md and the "T11 … DONE" pins.
3. R1 — Opus skeptic review + fix of G1+G4 (also resolve the duplicated
   money walk `records._json_value` vs `ledger._check_money` → one owner in
   `base.py`, and a public `fsync_dir` in `dskit/onboarding/base.py`).
4. Fable builds against committed red tests: F2 document/release, F3
   clock/sessions/cadence, F6 resilience/metrics, F10 ids/bundles/policy,
   F15 monitors; after F5: F8 guards, F9 breaker/arming/coordination; after
   F11: the G12 feed/decider test author (+ `tests/production/conftest.py`
   synthetic run over a temp onboarding root; training documents must declare
   `"since_ms": null` on the entry).
5. Remaining Opus test authors: G7 control/accounting (R8), G13
   reconcile/readiness, G14 verifier/executor, G16 alerts/health, G17 leg,
   G18 compose/loop, G19 `__main__` + e2e, G20 test_oop/test_producers; S0
   phase-2 spec + skeptic review as soon as a slot frees.
6. Opus first-round review of every built group, Sonnet re-reviews, fixes.
7. Batch every plan edit above, skeptic-review them, run `check_plan.py`.
8. Docs/examples/skeleton (§9.2, §9.3), phases 2, 2b, 3, then the wrap list.

Keep `BUILD-LOG.md` updated as the durable memory; commit and push often.
Before any merge to `main`, delete `docs/new_package_proposals/build-notes/`
(branch-only working files) and refresh `docs/RE-ENTRY.md`. Work
autonomously; do not stop for approval on anything the plan already decides.
