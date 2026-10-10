# TODO.md sections closed before 2026-10-10

Moved verbatim from `TODO.md` at the 2026-10-10 cleanup: every item in these
sections was already checked off (the Code standard section keeps its one open
item in `TODO.md`). Nothing here is current work; it is kept because the items
carry the reasoning behind landed changes.

---

## P12 Gate 3 recovery contract (2026-09-05; execute in order)

- [x] **Record the failure without calling it a result.** A12579 records that
      P12 stopped in Gate 3 after NRG h=1, seed 05, fold 00 finished without
      journal evidence. Preserve every partial artifact and the existing
      append-only journal rows; do not delete, rewrite, or relabel them.
- [x] **Inventory the exact Gate 3 state by asset and seed.** Derive the
      selected Gate 1 assets and horizons only from the persisted gate1.json
      rows where gate1_passes is true. Reconcile each null summary directory,
      its 20-fold report/walkforward record, and its actions.csv evidence.
- [x] **Extract only legitimate completed outcomes.** A stock can receive a
      recovered Gate 3 verdict only if all 19 required null draws, their
      score/test-statistic inputs, and their journal evidence are present.
      Rebuild the existing verdict with the shipped logic; retain provenance.
      Any incomplete, unjournaled, or non-reconstructable family is rerun.
- [x] **Diagnose and fix the missing journal-evidence failure.** Reproduce
      the NRG seed-05 fold failure with a focused red test, identify whether
      it is a journal race or another integrity defect, implement the narrow
      fix, and prove the affected fold/walk contract.
- [x] **Obtain an independent major/critical review of the fix.** Resolve
      every major or critical finding and re-run the focused verification
      before any recovery execution or code commit.
- [x] **Append a recovery journal entry after verification.** It must name
      A12579, the crash, exactly which outcomes were extracted from saved
      evidence, exactly which were rerun, the fix commit, and artifact paths.
      Never fabricate a pass/fail result or edit historical action rows.
      For every legitimately reconstructed final result, append a separate
      per-stock-and-horizon journal entry containing the reconstructed
      pass/fail verdict, the source action/artifact paths, all required-draw
      evidence, and the reconstruction method. Do not bundle individual
      results into an undocumented aggregate entry.
- [x] **Build a Gate-3-only continuation for remaining work.** Its manifest
      must be generated from the original persisted Gate 1 selections and
      retain the original data cut, caches, horizons, and provenance. Exclude
      only families whose final result was legitimately reconstructed; rerun
      every other survivor from Gate 3, including NRG as required.
- [x] **Run, verify, memo, wrap, and release only after recovery succeeds.**
      Write the durable results memo, refresh the journal/TODO/re-entry,
      commit the reviewed fix and verified recovery evidence on main, then
      push main.

      Completed by A18622. The frozen Gate 1 selection has 31 survivors:
      25 pass and 6 fail after 16 evidence-bound reconstructions and 15 full
      reruns. A18619 is the atomic final artifact; A12581-A12596 are the
      separate reconstructed verdict records. The durable memo is
      `children/intraday_equities/docs/memos/p12-gate3-recovery-results.md`.
      Future long runs on this 16-CPU host should set
      `INTRADAY_EQUITIES_FOLD_WORKERS=4`; benchmark before using more.

## Gate 3 / P11 closeout audit (2026-09-05)

- [x] **Gate 3 rebuild and P11 development are closed.** ADR-0092
      fail-fast audit, ADR-0093 bounded fold execution, and ADR-0094's
      asset-local study, their tests, and the P12 smoke work were reviewed,
      committed, merged to main, and pushed. Historical wraps are retained
      in docs/RE-ENTRY.md.
- [x] **Earlier P10/P11 code and evidence are closed.** Their commits are
      ancestors of both main and origin/main; there are no unmerged local
      branches from this work.
- [x] **After the active P12 process exits, repair the smoke evidence paths.**
      Journal rows A2888-A2909 still name the removed
      /home/russell/wt/adr-0094 worktree; the artifacts live under
      /home/russell/dskit/children/intraday_equities/pipeline_runs/.
      A18620 appends the verified prefix correction; old rows are unchanged.
- [x] **After the active P12 process exits, record the first P12 attempt's
      termination and wrap.** Its completed steps are rows A2910-A2918, but
      A18621 records the preserved `/home/russell/p12-run.log` exit 143 and
      keeps A2910-A2918 explicitly partial, not a Gate 3 result.

- [x] **Make per-asset Gate 3 evidence durable after P12 closes.**
      A12581-A12596 are the separate reconstructed verdict records. The rerun
      emitted one main and 20 part journal records for every null draw, and
      A18619/A18622 bind those records to the atomic final result. No interim
      entry was substituted for a final verdict; the final stage remained the
      sole result authority.

- [x] **Diagnose the failed P12 Gate 3 walk before resuming.** The 63-asset
      run exited 1 at 16:09:05Z because
      p12-63-asset-modelability-gate3-seed05-nrg-h01-part-00 finished without
      journal evidence (A12579). Preserve the partial artifacts; do not claim
      final Gate 3 results or commit the failed run as complete.
      Focused reproduction and fix are in `cd4fb1c`; exact replacement
      evidence is A12731. Independent review is clean after all major findings
      were resolved.

## Code standard (2026-08-27)

Everything needed to continue is in THIS file — each item below carries its
own reasoning, because a work list that depends on an unreadable document is
not a work list. (A fuller write-up with the measured baselines exists at
`~/.claude/plans/soft-soaring-tide.md`, but it is outside the repo: no
collaborator, fresh clone, or new agent can read it. Do not treat it as
required.)

**Verification recipe for any change below.** Ruff must be clean, the full
suite green, and — load-bearing — every document's identity hash unmoved:

```bash
python -m ruff check . && python -m pytest -q
for f in examples/pipeline/*.json children/intraday_poc/configs/run-*.json; do
    python -m dskit.pipeline validate "$f"      # prints the identity hash
done
```

Any hash that moves means the document grammar changed — revert and rethink,
UNLESS the change is a declared identity move (see the intentional-move log in
`docs/plans/2026-08-closeout.md` §9).

**Baseline refreshed 2026-08-29 after D1.** The ledger is now
**18 documents** — it grew by `examples/pipeline/optuna-continuous.json` (E4),
`model-sweep.json` (E5), `foreach-fanout.json` (C3) and
`selection-demo.json` (D1). Engine examples did NOT
move: `examples/pipeline/torch-declared.json` is still `4039ddf167fa65db…`.
The child documents moved again (C5): `run-train.json` → `6b1df6177dd8eae0…`,
`run-backtest.json` → `2ba4c25bbfaa7401…` (named the zoo LSTM). Prior
intentional moves are in `docs/plans/2026-08-closeout.md` §9. To
regenerate the whole ledger, run the loop above.

LANDED in the PREVIOUS session (2026-08-27; ruff clean; 2440 passed / 108
skipped; all 14 document identity hashes byte-identical to that baseline):

- [x] The docstring standard, in `CLAUDE.md` → "Docstrings". NumPy sections,
      an `Examples` block that INSTANTIATES each class, types in the docstring
      text and not the signature, one line for `_`-helpers. Applies to new code
      and to any function meaningfully edited — no retrofit sweep.
- [x] Enforcement: ruff `D` (convention numpy) + `ANN2` (RETURN annotations
      only; `ANN001` stays off). Verified the gate fires `D103`/`ANN201` on a
      new public file.
- [x] 3a — the `validate_params` helper family has ONE definition, now PUBLIC
      as `reject_unknown_params` / `check_int_param` in `node.py`, beside the
      protocol they serve. `kinds_report.py`, `libs/sklearn.py`,
      `synthetic_nodes.py` and `kinds_stats.py` alias them.
      (`base.py:144,189` keep same-named privates ON PURPOSE — those serve the
      opposite protocol: raise-immediately, for `from_obj` parsing.)

REMAINING, in the plan's risk order:

- [x] **Children still COPY `_reject_unknown`** —
      `children/intraday_poc/intraday_poc/nodes.py:56` and
      `children/_skeleton/yourproject/nodes.py:43`. The helper is public now,
      so they should `from dskit.pipeline.node import reject_unknown_params`.
      Fix the skeleton first — its copy propagates to every future child.
      **Landed this run (2026-08-28, A2):** child and skeleton both import
      `reject_unknown_params` from `dskit.pipeline.node`; no copy remains.
- [x] **3b — `TorchAdapter` becomes a real ABC.** `libs/torch.py:315`: four
      hooks raise `NotImplementedError` WITHOUT `@abstractmethod`, so an
      incomplete adapter constructs fine and fails later at call time. Two
      subclasses exist; low blast radius. Adapters named by import path are
      checked structurally and unaffected.
      **Landed this run (2026-08-27, B1):** ABC on exactly the four hooks;
      the structural-resolution claim verified against `base.py` AND
      pinned; the abstract refusal is core's one sentence
      (`abstract_class_problem`), asked by the node registry and the
      adapter doorway at plan and run, plan/run wording pinned equal.
- [x] **3c — `TrainableNode` base so nodes stop branching on `mode`. ADR FIRST.**
      Deletes nine `if mode ==` sites across torch/sb3/transformers/sklearn/
      synthetic. **Do NOT split each trainable into train and load classes** —
      that was the first proposal and it is wrong: `mode` is inside the
      identity hash (measured: `4039ddf1…` → `2c9d9925…`), and
      `transformers.py:612` refuses any artifact whose sidecar `node_class`
      does not match exactly, so every existing checkpoint would become
      unloadable — while torch and sklearn stay green, so the tests would not
      catch it. Port order: sklearn → synthetic_nodes → torch → transformers →
      sb3. The ADR covers the seam and the conformance-bar change, since
      `conformance.py:1214`'s bytecode sniff for `mode` must become a
      structural check and child packs subclass against it.
      **Landed this run (2026-08-28, C1) via ADR-0038.** All nine sites
      died — the ONE surviving raw-`mode` read is `node_level_pin`, which
      the ADR sanctions as the design's single one. Every hash unmoved on
      an engine change across five packs. The bar is now structural
      (subclass + both template methods still the base's), proven
      red-first against a plain-`Node` trainable. Two services moved to
      `Node`, not the new base, because the non-trainable `Sb3Eval` needs
      them. `_evidence_bases` keeps the new base out of the conformance
      MRO walks — proven red-first too: without it the base vouches for a
      child's declared-but-unread `artifact` knob. A new pin freezes the
      four recorded class refs and both `build_module` identities, the
      invariant only loading a pre-change `.pt` would otherwise catch.
- [x] **3d — decompose the long methods** (after 3b/3c, so the seams are
      stable): `driver.py:730` `run_document` (399 lines; its six phase
      comments ARE the boundaries), `kinds_report.py:1227` `RunReport.run`
      (252, no docstring), `libs/torch.py:960` `TorchTrain.run` (230, no
      docstring), `nodes.py:196` `WindowRows.run` (50, four jobs).
      `conformance.py:491` is 927 lines but is a pytest-class factory — leave.
      **Scope note: 3d is about long METHODS, not large FILES.** Splitting
      modules is 3e; decomposing `run_document` shrinks `driver.py` only as
      a side effect.
      **Landed this run (2026-08-28, C4).** Longest body per file is now 86 /
      72 / 89 lines. Six bodies were decomposed, not four: `TorchTrain.run`
      had been RENAMED `run_train` by ADR-0038 and was still 216 lines — the
      card wrongly said it was already short, and the agent measured by AST
      rather than trusting it. `run_walk_forward` (219), `_summary` (119) and
      `_family_delta` (106) were over the bar too. `WindowRows.run` is C2's.
      The ceiling is now PINNED (`tests/pipeline/test_method_lengths.py`) on
      an allowlist that grows as modules are decomposed, so a body cannot
      quietly regrow; the pin's own walker is pinned to see defs at any
      nesting depth. `kinds_report.py` converted and its ignore entry
      drained; `driver.py` and `libs/torch.py` keep theirs by orchestrator
      ruling — C3 and C6 still own those files and drain them.
- [x] **3e — split `kinds_flow.py` (1549 lines, SEVEN unrelated kinds).**
      Not covered by 3d, and not tracked anywhere before 2026-08-27. It
      holds two unrelated subjects: the record-flow verbs (`Filter:177`,
      `Concat:712`, `Join:1092`, `Derive:1359`) and the banking/admission
      chain (`EventBank:267` role `accrual`, `Eligibility:422` role `gate`,
      `BankingReport:473` role `report`) — a cohesive three-node story that
      deserves its own module, e.g. `kinds_banking.py`.
      Mechanical and low-risk: kinds register by NAME, so a move changes no
      document and no identity hash. Two things to get right — keep both
      modules' `register()` reachable from `dskit/pipeline/__init__.py` or
      documents naming those kinds stop resolving, and re-export the shared
      helpers for one release, since sibling modules import
      `_reject_unknown` and `is_node_ref` through this file today.
      **The other three big files are NOT the same problem** and need no
      split: `torch.py` (1474) is one cohesive pack, `conformance.py`
      (1440) is one pytest-class factory, `driver.py` (1444) is long
      methods → 3d. Size alone is not the trigger; **doing several
      unrelated jobs is.**
      **Landed this run (2026-08-28, B3):** clean 3-round loop; move
      proven mechanical by AST-identity diff + empty hash gate; both
      registers pinned reachable; re-export kept as a pinned safety net
      (grep found NO live importer through kinds_flow); kinds_flow fully
      converted, its ignore entry drained; banking behaviour tests stay
      beside the driver integration run by design.
- [ ] **Drain the pre-standard ignore list.** 30 modules sit in
      `pyproject.toml` under `per-file-ignores` (57 originally; 27 drained
      so far — see below); delete a module's entry when you convert it.
      That list IS the remaining work, in config form.
      **In progress (2026-09-11):** 15 entries drained — 5 needed real
      docstring fixes (`dskit/__init__.py`,
      `children/intraday_poc/intraday_poc/__init__.py`,
      `dskit/onboarding/__init__.py`, `dskit/pipeline/io.py`,
      `dskit/onboarding/state.py`); 5 were already fully compliant and
      needed only the ignore-list entry removed (`dskit/onboarding
      /certify.py`, `dskit/assets/sync.py`, `dskit/onboarding/publish.py`,
      `dskit/assets/default_model.py`, `dskit/onboarding/default_model.py`);
      and 5 more needed real fixes too — `dskit/onboarding/acquire.py` and
      `dskit/onboarding/snapshot.py` each gained new `Parameters`/`Returns`
      (`acquire.py` also `Raises`), `dskit/pipeline/features.py` gained new
      `Parameters`/`Returns` on its public `apply_stream_steps`, and
      `dskit/assets/ingest.py` / `dskit/assets/lineage.py` each got a
      one-line-docstring reflow (mechanical, no new sections needed).
      **Continued (2026-09-11, same session):** 12 more modules drained —
      the next tier up (3-6 D-rule findings each), several needing a new
      class `Examples` block: `dskit/assets/record.py`,
      `dskit/assets/registry.py`, `dskit/onboarding/base.py`,
      `dskit/onboarding/connector.py`, `dskit/onboarding/libs/localfiles.py`
      (`LocalFilesConnector` upgraded to a full class docstring),
      `dskit/pipeline/resolve.py` (`ResolvedPipeline` gained an `Examples`
      block), `dskit/assets/base.py` (`AssetError` gained an `Examples`
      block mirroring `JournalError`'s), `dskit/onboarding/observations.py`,
      `dskit/pipeline/trainlog.py` (`TrainingCurve` gained an `Examples`
      block), `children/_skeleton/yourproject/connectors.py`
      (`SampleConnector` upgraded, mirroring `LocalFilesConnector` — the
      skeleton propagates to every future child), `dskit/onboarding
      /layout.py`, `dskit/pipeline/metrics.py`. Not every `Examples` block
      was execution-verified before its first commit — two rounds of
      independent skeptic review found and fixed a wrong `resolve()` call
      signature, a wrong list-repr in an expected-output line, and three
      other factually-inaccurate reworded summaries. `AssetError`'s,
      `TrainingCurve`'s, `SampleConnector`'s, and `ResolvedPipeline`'s
      `Examples` blocks are now genuinely execution-verified, byte-exact
      output included. `LocalFilesConnector`'s and `AlpacaBarsConnector`'s
      (the sibling it mirrors, pre-existing) are illustrative only, same
      as every connector pack's example that names a plausible but
      unreal path or symbol — they were never claimed to be runnable
      as-is and are not.
      **27 of the original 57 drained; 30 remain**, each with 8+ D-rule
      findings and generally more classes needing full conversion — left
      for a future pass.

      **Correction (2026-09-11, third skeptic round):** the note above
      about `metrics.py`-style files being ruff-clean without full
      `Parameters`/`Returns` was found MAJOR, not acceptable — this
      repo's own `pyproject.toml` PRE-STANDARD MODULES comment says
      "clearing a large module... means converting the whole file," and
      14 of the 27 drained files were only ruff-flagged-spot fixes,
      missing 47 of 94 public functions' full sections and 14 private
      helpers below the one-line floor. `Parameters`/`Returns`/`Yields`
      completeness is genuinely closed — an AST script (parses each
      function, flags a public one with args but no `Parameters`, or a
      return/yield value with no `Returns`/`Yields`, or a private one
      with no docstring at all) reports zero findings, independently
      reproduced by three later skeptic rounds. **`Raises` completeness
      is NOT mechanically checkable the same way** (an AST script
      cannot see which exceptions a call chain can raise without
      interprocedural analysis) and rounds 5 and 6 both found real
      `Raises` gaps by tracing call chains by hand/execution — this
      class of defect is addressed incrementally as skeptics find it,
      not closed by a script. Also fixed in the same round:
      `Connector` (an ABC) gained an `Examples` note pointing to
      `LocalFilesConnector` since it cannot be instantiated itself;
      `ResolvedPipeline`'s `Examples` block was rewritten to actually
      construct a `ResolvedPipeline` (it previously called the
      module-level `resolve()` function against an undefined variable,
      violating "an Examples block that INSTANTIATES the class"); nine
      docstrings that satisfied D205 by becoming one 150-258 char
      physical line were reflowed into a short summary + blank line +
      wrapped body instead; and `dskit/pipeline/features.py`'s
      `apply_stream_steps` Parameters section no longer claims plain
      dict rows work as input (they crash — proven by running the
      built-in `filter`/`regroup` steps against one).

      **Fourth skeptic round (2026-09-12): clean pass — no blocker or
      major findings.** Re-derived all seven checks from scratch
      (independent AST completeness audit, independent NumPy
      section-formatting audit, byte-exact re-execution of every
      constructing `Examples` block including `ResolvedPipeline`'s) and
      confirmed the round-3 fixes hold. Found and fixed 6 minor/nit
      accuracy issues: 5 docstrings (`assets/ingest.py`,
      `onboarding/__init__.py`, `onboarding/base.py`,
      `onboarding/observations.py`,
      `children/intraday_poc/intraday_poc/__init__.py`) had been
      D205-fixed by joining onto one 90-138 char physical line instead
      of the summary+blank+body split used elsewhere in the same diff
      — reflowed for consistency; `dskit/__init__.py` said "Four
      packages" and omitted `dskit.production` from the list, in a file
      this diff itself declares converted — corrected to five;
      `TrainingCurve.summary()`'s `Returns` overstated that every
      recorded metric gets a `final_<name>` (only a fixed whitelist
      does — proven by recording an extra metric and checking it's
      absent from the summary); `LocalFilesConnector.read()`'s `Yields`
      described all-SCHEMAs-then-all-RECORDs instead of the real
      per-stream interleaving; `ResolvedPipeline`'s `Examples` block
      was missing the `from dskit.pipeline.base import ...` line its
      own constructor call needs (added and re-verified it runs);
      three `Raises`/summary sections understated what they actually
      check (`metrics.py`'s `_check_binary`/`_check_regression` omitted
      the type/finiteness checks that run before the range checks
      named; `snapshot_hash`'s `Raises` omitted the non-string-keys
      case; `LocalFilesConnector.discover`'s `Raises` omitted the
      malformed-JSONL case `_rows` can raise while reading a first
      row). **This item is done for these 27 files** — the remaining
      30 pre-standard modules are unstarted, left for a future pass.

      **Fifth skeptic round (2026-09-12): no blocker or major findings;
      8 minor/nit fixes.** Verified all 6 round-4 fixes independently
      (fresh AST audit, fresh executable-diff, re-executed every
      constructing `Examples` block byte-exact) — 5 held cleanly; the
      6th (`snapshot_hash`'s `Raises`) was still incomplete, and one
      summary line round 4 missed (`apply_stream_steps`, already a
      proper summary+blank+body split, just with a 93-char summary
      line) needed shortening too. Fixed: `snapshot_hash`'s `Raises`
      now also names the canonically-serializable check (a set or
      NaN/Infinity value raises even with all-string keys — proven);
      `dskit/pipeline/features.py:158`'s (`apply_stream_steps`) summary
      line shortened; three more `Raises`
      sections tightened to name every condition their body actually
      raises (`LocalFilesConnector.check`/`.read`'s duplicate-stem and
      parse/malformed-JSONL cases; the skeleton `SampleConnector.read`'s
      `_budget`/`parse_utc` cases; `AssetRecord.from_obj`'s non-dict and
      field-validation cases) — each re-verified by triggering it;
      three more docstrings that were summary-only long lines
      (`assets/base.py`'s `check_str`, `pipeline/resolve.py`'s
      `ResolvedPipeline.__post_init__`, `pipeline/trainlog.py`'s
      `_num`) shortened or split for consistency; this item's own
      header still said "57 modules" after the count had moved to 30 —
      corrected; and a ragged short line left by round 4's reflow of
      `children/intraday_poc/intraday_poc/__init__.py` was re-wrapped.

      **Sixth skeptic round (2026-09-12): no blocker or major findings;
      9 minor/nit fixes — the same "incomplete `Raises`" pattern
      recurring for a third straight round.** Verified all 8 round-5
      fixes independently — all held. Found: `save_state`,
      `save_config`, and `OnboardingRoot.registry` had gained
      `Parameters`/`Returns` in earlier rounds but never a `Raises`
      section despite raising (3 fixed); `Lineage.add`,
      `find_snapshot_dir` had `Raises` sections naming only SOME of
      their conditions (2 fixed, each re-verified by triggering the
      missing condition); `LocalFilesConnector.read` and the skeleton's
      `SampleConnector.read` both still omitted a malformed-cursor case
      in `state` (2 fixed); and this item's own round-5 note
      mischaracterized one of its fixes and overstated that an AST
      script closes `Raises` completeness the way it closes
      `Parameters`/`Returns` — it does not, and cannot: no purely
      syntactic check sees which exceptions a call chain can raise
      (corrected). **Given this pattern survived three straight
      rounds, a proactive heuristic script was then written and run
      before the next round** (flags any public function calling
      `_raise_if`/`_check_*`/other known-raising helpers, or containing
      a bare `raise`, with no `Raises` section at all — not full
      completeness, but catches the "missing entirely" half of the
      pattern) over all 27 files: it found 6 more genuine gaps
      (`ingest_run`, `AssetRecord.version_id`, `sync_published`,
      `run_acquisition`, `publish_version`, `probability_metrics`), each
      fixed and re-verified by triggering it. **Correction (round 7):**
      this ledger's first cut of that sentence miscounted — only
      `sync_published` (`dskit/assets/sync.py`) and `publish_version`
      (`dskit/onboarding/publish.py`) were genuinely 0-change files;
      `run_acquisition` (`dskit/onboarding/acquire.py`) and
      `probability_metrics` (`dskit/pipeline/trainlog.py`, not
      `metrics.py`) both live in files this session HAD already edited
      earlier (for `find_active_source` and for imperative-mood/
      `Examples` fixes respectively) — the point stands (ruff-clean is
      not `Raises`-complete) but the file list was wrong. Left
      deliberately unfixed at the time: five dunder
      `__init__`/`__post_init__` methods whose only raise is a
      constructor type-check already implied by the class docstring's
      `Parameters` types — no already-compliant class in this repo
      documents that as a separate `Raises`, and no skeptic round (six,
      by this point) has flagged it, so adding one now would invent a
      convention rather than follow one.

      **Seventh skeptic round (2026-09-12): escalated to MAJOR — the
      "left deliberately unfixed" dunder claim above was FALSE.** An
      independent scan of every already-compliant (non-ignored) class
      in the whole repo whose `__init__`/`__post_init__` raises found
      **32 counter-examples that DO document it** (e.g.
      `dskit/pipeline/records.py`'s `MarketRecord`,
      `dskit/pipeline/release_rotation.py`'s `HalfOpenInterval`,
      `dskit/production/ledger.py`'s `ServeRoot`), so the "no
      convention exists" reasoning was wrong — all five dunders
      (`Lineage`, `Registry`, `OnboardingRoot`, `AssetRecord`,
      `ResolvedPipeline`) now carry a class-level `Raises`. The same
      round also found the `Raises`-completeness gap had recurred a
      **4th** straight time, escalated to MAJOR this time: 6 more
      functions with `Raises` entirely missing or incomplete
      (`verify_snapshot`, `apply_stream_steps`, `AssetRecord.to_obj`,
      `Registry.register`, `ResolvedPipeline.to_dict`, `pipeline_hash`
      + `write_run_dir` — the last two documented only `ValueError`
      when a `set` value actually escapes as an undocumented
      `TypeError`, since the `except ValueError` never catches it) —
      all fixed and re-verified by triggering each condition. Also
      fixed: `ingest_run`'s `Raises` was placed after `Examples`
      (numpydoc order is Parameters → Returns → Raises → … →
      Examples) — the only out-of-order section among the 27 files;
      `ResolvedPipeline`'s own `instruments` doc claimed direct
      construction "canonicalizes by sorting" when it actually REFUSES
      unsorted input (only `resolve()` sorts before constructing) —
      corrected; three `durable_write_*`/`atomic_write_json` siblings
      gained the `OSError` note `save_config` already had, for
      consistency; the two connector `read()` methods gained a
      `ValueError` note for a per-stream `state` value that isn't a
      dict (an internal message leaks through, undocumented); and
      `resolve()`'s `ValueError` list gained the weighted-correction
      case. **Given a MAJOR-severity recurrence of the exact same
      pattern for a 4th time, this is not yet trusted closed** — an
      eighth round is required before treating `Raises` completeness
      as settled, unlike `Parameters`/`Returns`/`Yields` (AST-provable,
      closed since round 4).

      **Eighth skeptic round (2026-09-12): still MAJOR — a 5th
      straight recurrence, this time including two of round 7's OWN
      fixes.** The two connector `read()` methods' round-7
      `ValueError` note for a bad per-stream `state` value was itself
      wrong for most inputs: only a `str` value raises `ValueError`
      (via `dict(v)`); an `int`/`list`/`None`/`float` raises
      `TypeError` instead — split into separately-scoped `TypeError`/
      `ValueError` entries in both `dskit/onboarding/libs/localfiles.py`
      and `children/_skeleton/yourproject/connectors.py`, each verified
      by executing both branches. `durable_copy_file` was the one
      sibling round 7's `OSError` sweep missed — added, verified via a
      copy into a nonexistent directory. Four more gaps, all fixed and
      verified by triggering the condition: `load_state`
      (`dskit/onboarding/state.py`) had no `Raises` at all despite
      raising the same root/segment/mode errors its sibling
      `save_state` documents 40 lines below; `OnboardingRoot.registry`
      (`dskit/onboarding/layout.py`) omitted the "`model` given but not
      an `AssetModel`" case its own callee (`Registry.__init__`) raises;
      `backoff` (`dskit/onboarding/connector.py`) left `base_s`
      completely unvalidated/undocumented — added `TypeError`/
      `ValueError` entries and softened the `Returns` range claim,
      which a negative or NaN `base_s` violates; and an unhashable
      `kind` (e.g. a list) escapes `Registry.register`/`find`/`list`
      (`dskit/assets/registry.py`) as a raw, undocumented `TypeError`
      — added to all three. Also fixed a `Returns` gap:
      `probability_metrics` (`dskit/pipeline/trainlog.py`) documented
      only two of its three early-return-to-`{}` paths, omitting the
      case where a prediction is not a finite number. **Correction to
      round 7's framing above:** "32 counter-examples… so the 'no
      convention exists' reasoning was wrong" overstated what the scan
      showed — of the ~132 already-compliant classes in the repo whose
      `__init__`/`__post_init__` raises, only ~36 (~27%) document a
      class-level `Raises`. That is precedent enough to justify adding
      one (round 7's actual decision), not a majority or "confirmed"
      convention; no future round should cite it as settled practice.
      **Given a 5th straight MAJOR recurrence, `Raises` completeness is
      still not trusted closed** — the pattern each round finds is
      narrower (round 8's own gaps were mostly in files/methods
      previous rounds had already touched, including two of round 7's
      own fixes), which suggests convergence, but a 9th round is
      required rather than declaring victory. A mechanical gate — a
      test asserting every documented exception type actually fires,
      and that sibling functions sharing a helper share its `Raises`
      clauses — would close this class of defect faster than further
      manual rounds; none has been built yet.

      **Ninth skeptic round (2026-09-12): a 6th straight recurrence, via
      a distinct lens (caller-names-callee and shared-helper wording
      consistency, rather than local per-function completeness).** One
      MAJOR: `run_acquisition`'s (`dskit/onboarding/acquire.py`) `Raises`
      named every sub-call that can raise `AssetError` EXCEPT its own
      `load_state`/`save_state` calls — verified by corrupting a
      checkpoint file and triggering the exact `AssetError` `run_acquisition`
      propagates unlisted; fixed by naming both, matching this file's own
      existing convention of citing propagation sources. One MINOR:
      `LocalFilesConnector.read()`'s (`dskit/onboarding/libs/localfiles.py`)
      `Raises` said `state` "is not a dict," silently dropping the "with
      string keys" qualifier its own `_check_dict` call — and every other
      caller of that shared helper — actually documents; fixed and
      verified by triggering `AssetError: state must be a dict with
      string keys`. One MINOR/nit: the same class's `Examples` block used
      a non-existent literal path (`/data/my_source`) with no "not
      runnable as-is" disclaimer, unlike every other filesystem-touching
      Examples block in the 27 files (which all build a real
      `tempfile.mkdtemp()` directory); rewritten to build and read a real
      CSV file, execution-verified byte-exact. Round 9 also closed two
      things fully: every one of the 27 files' `Examples` blocks was
      extracted and EXECUTED (not just read) with all `# ->` markers
      confirmed byte-exact, and zero `>>>` occurrences confirmed by grep
      — both CLOSED. It also flagged, without treating as a defect, that
      3 of the "27" files (`dskit/assets/default_model.py`,
      `dskit/onboarding/default_model.py`, `dskit/onboarding/certify.py`)
      have zero diff from the merge-base — their ignore entries were
      dropped because they already met the standard, not because they
      were edited; the actual edited-file count is 24, not 27. **Given a
      6th straight round finding a live `Raises` gap — smaller in scope
      each time, but still real — this class of defect remains open;**
      round 9's own recommendation, matching round 8's, is to build the
      mechanical gate rather than run a 10th manual pass.

      **Tenth skeptic round (2026-09-12): built the recommended
      mechanical gate — a 7th straight recurrence, this time
      one systemic pattern found at 4 sites plus a second at 2, both
      new bug shapes.** The gate computed, per function with a `Raises`
      section, the interprocedural closure of exception types reachable
      through this package's own helpers (`_raise_if`, `_check_*`,
      `durable_write_*`, `check_payload`, `check_config`,
      `check_message`, and the stdlib calls each function makes
      directly), and diffed it against the documented types; every
      candidate was then verified by executing the real code (the gate
      has both false positives — an argument already narrowed by an
      earlier `isinstance` check — and false negatives — a
      `self.attr.method()` chain two hops deep — so it is a lead
      generator, not a verdict). Found and fixed, all execution-verified:
      **pattern 1**, an unguarded `os.makedirs(..., exist_ok=True)` racing
      a stray file left at the target path raises a raw `FileExistsError`
      undocumented in `save_state` (`dskit/onboarding/state.py`),
      `write_snapshot` (`dskit/onboarding/snapshot.py`),
      `run_acquisition` (`dskit/onboarding/acquire.py`, two call sites),
      and `publish_version` (`dskit/onboarding/publish.py`); **pattern
      2**, a "shape-only" upstream check does not imply
      JSON-serializability downstream — `run_acquisition` also lets a
      `RECORD.data` value `check_message` only confirms is a dict (not
      that its VALUES serialize) reach a raw, undocumented `TypeError`
      from `json.dumps`; the same shape recurs once each in
      `save_config` (`dskit/pipeline/io.py`, a `model.params` value) and
      `resolve` (`dskit/pipeline/resolve.py`, propagated uncaught from
      `pipeline_hash` when `backend.fingerprint()` returns a
      non-serializable value — the exact defect round 7 already fixed
      once in `pipeline_hash`/`write_run_dir` themselves, never
      propagated to their own caller). Two more, unrelated: both
      `LocalFilesConnector.discover`/`.read`
      (`dskit/onboarding/libs/localfiles.py`) and `ingest_run`
      (`dskit/assets/ingest.py`) list a directory and then open every
      entry without handling a broken symlink, raising a raw
      `FileNotFoundError`; and `TrainingCurve.record`
      (`dskit/pipeline/trainlog.py`) passes `epoch`/`seconds` through a
      bare `int()`/`float()` with no validation, raising undocumented
      `TypeError`/`ValueError` for a non-numeric value. All 9 fixed by
      adding the accurate `Raises` entry (no runtime behavior changed —
      documenting reality stays in scope for a docstring-conversion
      item; whether these should also be WRAPPED into `AssetError` for
      consistency with sibling code, e.g. `OnboardingRoot.create`'s own
      `makedirs` already does, is a separate, larger decision this item
      does not make). **Given a 7th straight round finding genuine,
      previously-undetected gaps — now via a mechanical gate rather than
      manual reading — `Raises` completeness remains open.** The gate
      itself is real progress (it surfaces a class of defect no manual
      round had found: unguarded stdlib calls inside otherwise
      AssetError-only functions) but needed several rounds of its own
      tuning before its output was trustworthy, and still carries known
      blind spots — an 11th round should run the now-hardened gate again
      before this is declared closed.

      **Eleventh skeptic round (2026-09-12): an 8th recurrence, but
      narrowing — two MINOR instances of round 10's own pattern-1
      family (a different unguarded stdlib call, not a new shape) plus
      one docstring-accuracy nit.** Generalizing round 10's
      "unguarded destructive filesystem call" pattern from
      `os.makedirs` to `os.rename` found: `write_snapshot`
      (`dskit/onboarding/snapshot.py`) checks `os.path.exists(final_dir)`
      before its commit `os.rename`, but the check is not atomic with
      the rename — a genuine same-second concurrent duplicate pull can
      pass the check on both sides, and the loser's rename raises a raw
      `OSError` ("Directory not empty") instead of the documented
      `AssetError`; verified by triggering the exact TOCTOU gap
      (monkeypatching the check to miss a real concurrent winner).
      `run_acquisition`'s later `os.rename` of the normalized-rows
      staging dir into `observations/`/`forecasts/` has no pre-check at
      all; verified by execution that an already-occupied destination
      raises the same raw `OSError` (reachability note: unlike
      `write_snapshot`'s race, a plain identical retry cannot reach this
      path — `write_snapshot`'s own WORM check on `raw/` fires first and
      blocks it — so this needs an already-occupied
      `observations/`/`forecasts/` destination from outside this
      function's own retry logic, e.g. a leftover from an unrelated
      write). Both fixed with an `OSError` `Raises` entry, matching round
      10's `FileExistsError` phrasing convention. Also fixed:
      `apply_stream_steps` (`dskit/pipeline/features.py`) described its
      silent-skip surface too narrowly — "backend-owned registered
      kinds (no `apply`) are passed over" implies only recognized
      backend kinds are skipped, but the code passes over **any**
      `kind` that is neither a class-reference nor a registered kind
      with an `apply` hook, so a misspelled `kind` is silently a no-op
      too (verified: `FeatureStepConfig(kind="totally_bogus_kind",
      params={})` constructs cleanly and `apply_stream_steps` returns
      its input unchanged) — corrected to say so explicitly. Round 11
      also did a fresh full read of the 5 least-touched files
      (`observations.py`, `sync.py`, `metrics.py`, `features.py`,
      the skeleton's `connectors.py`) and found nothing else — every
      documented `Raises`/`Returns` re-verified byte-exact by execution.
      **Given an 8th straight round, but the finding narrowing to two
      instances of an already-known pattern shape plus one wording nit
      (no new shape, no BLOCKER/MAJOR) — this is the first round since
      the pattern started recurring that did not surface a genuinely
      new defect class.** A 12th round should specifically re-run
      round 10's gate widened to `os.rename`/`os.replace`/`shutil.*`
      (its own stated blind spot) before `Raises` completeness is
      declared closed.

      **Twelfth skeptic round (2026-09-12): a 9th recurrence, both
      instances the same already-known pattern-1 shape at two sites
      rounds 10-11 hadn't swept — no new bug class.** Widening the
      gate to `shutil.*`/`os.replace` (round 11's own recommendation)
      plus a fresh `os.makedirs`/`os.rename` sweep of all 24 files
      found: `run_acquisition`'s (`dskit/onboarding/acquire.py`)
      `finally`-block cleanup checks `os.path.isdir` before each
      `shutil.rmtree`, but an external actor (a stale-tmp reaper,
      manual cleanup) deleting the directory in between raises a raw,
      undocumented `FileNotFoundError` — verified by simulating exactly
      that TOCTOU gap; and `write_run_dir`
      (`dskit/pipeline/resolve.py`) has the identical unguarded
      `os.makedirs(run_dir, exist_ok=True)` shape round 10 already
      fixed at 4 other sites, in a file neither round 10 nor round 11
      swept — its existing generic `OSError` clause is technically a
      superset but omitted the named type and the race wording every
      sibling site now carries verbatim, verified by triggering a real
      `FileExistsError` via the same TOCTOU technique. Both fixed. Both
      findings need external interference to reach (neither is
      triggerable through the function's own retry/at-least-once
      semantics alone) — consistent with round 11's finding, not a
      regression in severity. Round 12 explicitly deferred its most
      expensive planned check (an independent from-scratch exception
      trace, built without looking at the current docstring first) per
      its own stated precondition, since the mechanical sweep did not
      come back clean. **Given a 9th straight round, but narrowing to
      recurrences of one already-catalogued shape at previously-unswept
      sites (no new class since round 10) — a 13th round should run
      that deferred from-scratch trace on 3-4 multiply-touched
      functions (`run_acquisition`, `write_snapshot`,
      `Registry.register`, `pipeline_hash`) before `Raises`
      completeness is declared closed.**

      **Thirteenth skeptic round (2026-09-12): ran the deferred
      from-scratch trace — a 10th recurrence, and it surfaced a
      genuinely NEW bug shape on its first try, confirming round 12's
      concern that the gate-based method alone would not be enough.**
      Method: derive each function's exception surface independently
      from its code, before ever reading its current docstring, then
      diff. `run_acquisition` came back a clean match (nothing to fix —
      the first function in 10 rounds to survive a full independent
      trace with zero gap). Three genuine defects elsewhere, all
      execution-verified: **(1)** `write_snapshot`
      (`dskit/onboarding/snapshot.py`) reads `manifest["acquired_at"]`
      and `manifest["source"]` unchecked for presence — a manifest that
      passes the declared-keys check but omits a required one raises an
      undocumented `KeyError`; added. **(2)** the same function has a
      THIRD, previously uncatalogued TOCTOU beyond its two documented
      races: `staged_dir` deleted externally between the `payload/`
      check and the manifest write raises a raw `FileNotFoundError`
      from `durable_write_json`, not covered by either existing
      `AssetError`/`OSError` entry; added. **(3)** `Registry.register`
      (`dskit/assets/registry.py`) attributed its canonical-
      serializability `AssetError` to "constructing the `AssetRecord`,"
      but the traceback shows the true raise site is one line later, in
      this method's own call to `record.version_id()` — `AssetRecord`'s
      shape check never looks inside a free-form `"object"` field, so
      construction succeeds and only hashing fails; corrected. **(4)
      MAJOR, a new bug class:** `pipeline_hash`
      (`dskit/pipeline/resolve.py`) falls back to `dict(resolved)` when
      `resolved` has no `to_dict`, and that conversion can itself raise
      — `pipeline_hash(42)` raises `TypeError: 'int' object is not
      iterable`, and `pipeline_hash(['a','b','c'])` /
      `pipeline_hash('hello')` raise `ValueError: dictionary update
      sequence element #0 has length 1; 2 is required` — neither
      documented, and the `ValueError` case is actively misleading: it
      collides with the ALREADY-documented `ValueError` (stated cause:
      "NaN/Infinity in an open dict or fingerprint"), for a completely
      unrelated failure that never reaches the NaN/Infinity code path
      at all; corrected with its own clause. **Given a 10th straight
      round, and a genuinely new bug shape found on the very first
      application of the most rigorous method tried — `Raises`
      completeness remains open.** A 14th round should re-apply the
      same from-scratch method to a fresh set of functions (this round
      only covered 4), since it is now the one method in 13 rounds that
      has found a brand-new defect class rather than a recurrence.

      **Fourteenth skeptic round (2026-09-12): the from-scratch method
      applied to 5 fresh functions/classes — an 11th recurrence, all 5
      instances of already-known shapes (partial-type or
      partial-cause documentation), no new class this time, and one
      target (`OnboardingRoot.create`) came back completely clean.**
      All execution-verified: **(1)** `publish_version`
      (`dskit/onboarding/publish.py`) documented only `FileExistsError`
      for its unguarded `os.makedirs`, omitting the generic `OSError`
      an unwritable outbox directory raises from that same call and
      from the manifest's `durable_write_bytes` — added, matching the
      sibling-documented convention that helper already carries. **(2)**
      `ingest_run` (`dskit/assets/ingest.py`) documented a dangling
      symlink's `FileNotFoundError` under `artifacts/` but not a
      symlink LOOP's `OSError` (errno 40, "too many levels of symbolic
      links") from the identical `open()` call — added. **(3)**
      `ResolvedPipeline`'s class-level `Raises`
      (`dskit/pipeline/resolve.py`) documented the sortedness check on
      `instruments` as always producing `ValueError`, but
      `list(self.instruments) != sorted(self.instruments)` calls
      `sorted()` unguarded — a tuple of mutually-incomparable elements
      (e.g. mixed `str`/`int`) raises a raw `TypeError` instead — added.
      **(4)** `to_dict`'s `Raises` documented only `TypeError` from a
      non-serializable `data_fingerprint` value, but the same
      `json.dumps` call raises `ValueError` instead for a circular
      reference — added. **(5)** `AssetRecord.from_obj`'s `Raises`
      enumerated three `AssetError` causes but missed a fourth: the
      stored-`version_id` comparison itself calls `rec.version_id()`,
      which raises `AssetError` when `payload`/`refs` hold a
      non-canonically-serializable value (the same failure mode round
      13 found misattributed in `Registry.register`) — added. **Given
      an 11th straight round, though this one found only known-shape
      recurrences and one clean function — `Raises` completeness
      remains open but is narrowing under the from-scratch method too,
      not just the gate.** A 15th round should continue the
      from-scratch trace on a further fresh set, since two rounds
      running it (13, 14) have both found real gaps.

      **Fifteenth skeptic round (2026-09-12): a 12th recurrence — 6
      findings (2 MAJOR, 4 MINOR) across `connector.py`/`features.py`/
      `trainlog.py`, while `Lineage` and `load_state` came back
      completely clean.** All execution-verified. **MAJOR (1):**
      `TrainingCurve.__init__` (`dskit/pipeline/trainlog.py`) had NO
      `Raises` section at all, though `total_epochs`/`log_every`/
      `max_lines` each go through a bare `int()` — the exact pattern
      round 10 already fixed on this class's `record()`, never applied
      to `__init__`; added to the class docstring, matching the
      established dunder-`Raises` convention. **MAJOR (2):**
      `resolve_connector` (`dskit/onboarding/connector.py`) caught only
      `except ImportError`, letting any other import-time exception
      (e.g. a dependency's own `RuntimeError`) escape raw — **this one
      was a genuine CODE fix, not just documentation**: the sibling
      `dskit/assets/store.py::_resolve_backend`, explicitly written to
      copy this exact "connector resolve idiom" (ADR-0013), already
      widens the catch to `except Exception` with a comment stating why
      ("a backend module crashing at import ... must not escape the
      seam raw") — `resolve_connector` never received that fix. Widened
      to match, verified the wrapped `AssetError` now fires, and the
      full `tests/onboarding` suite (3124 tests) still passes. Unlike
      every other fix in this loop, this changes runtime behavior
      rather than only documenting it — justified because it closes an
      already-established, ADR-cited idiom this exact function's own
      docstring claims to implement, verified safe by the full suite,
      not a new design decision. **MINOR (3):** `retry_after` documented
      no `Raises` and its own Parameters text overclaimed "a caller
      never has to guard the shape" — a headers-like object whose
      `items()` raises, or yields non-pair entries, is not shielded;
      corrected the claim and added `Raises`. **MINOR (4):**
      `apply_stream_steps` documented only `ValueError` for a class-ref
      that fails to import, omitting `TypeError` when the ref imports
      but resolves to a non-callable — added. **MINOR (5):**
      `TrainingCurve.record` omitted `AttributeError` from a truthy,
      non-dict `metrics` argument (`(metrics or {}).items()`) — added.
      **MINOR (6):** `check_config` omitted `TypeError` propagated from
      `codec.py`'s `storage_problems` when `config["storage"]` has
      mixed-type keys — `storage_problems`'s own one-line docstring
      ("never raises") is itself false, but `codec.py` is outside this
      item's 24-file scope, so only `check_config`'s docstring was
      corrected here; `codec.py`'s own false claim is left for a future
      pass. **Given a 12th straight round — `Raises` completeness
      remains open**, though this round's own closing note flagged a
      promising narrower angle: a "fix landed in one sibling, missing
      in its twin" pattern (exactly what `resolve_connector` turned out
      to be) — a 16th round should grep for other lone
      `except ImportError`/single-purpose resolvers across the 24 files
      and check each against any already-fixed sibling elsewhere in the
      repo.

      **Sixteenth skeptic round (2026-09-12): a 13th recurrence — 1
      MAJOR + 5 MINOR, no BLOCKER, the sibling-divergence hunt came back
      clean (round 15's `resolve_connector` fix holds, verified against
      `store.py::_resolve_backend` and the full test suite; no other
      pair in the repo shares an ADR-cited "must mirror" contract).**
      All fixes below are documentation-only and execution-verified.
      **MAJOR:** `load_config` (`dskit/pipeline/io.py`) — the pipeline's
      primary config-loading entry point — lets a syntactically-valid
      JSON file whose top-level value is `null`, a number, or a bool
      reach `PipelineConfig.from_obj`'s unguarded `set(obj)` call,
      raising a raw `TypeError` instead of the documented
      `ValueError`/`OSError`/`ModuleNotFoundError`; added. **MINOR (5):**
      `build_manifest` (`dskit/onboarding/snapshot.py`) — the
      established "unguarded stdlib call after a check" shape recurs
      again: `os.path.getsize` runs unguarded right after
      `file_digest` succeeds, so a file removed in between raises a raw
      `FileNotFoundError`; added. `sync_published`
      (`dskit/assets/sync.py`) accepts any registry whose model
      "declares `dataset`/`dataset_version`," but when that model omits
      the `dataset` kind's `source` ref, `registry.get(...).refs["source"]`
      raises a raw `KeyError` that escapes the per-file `except
      AssetError` catch and aborts the WHOLE scan rather than failing
      one file (defeating the documented anti-entropy design) — added,
      noting it is unreachable against the shipped default model.
      `find_active_source` (`dskit/onboarding/acquire.py`) omitted the
      `AssetError` `registry.find()` raises when the model does not
      declare a `source_config` kind at all (same type as already
      documented, a missing CAUSE) — added. `scan_stream`
      (`dskit/onboarding/observations.py`) documented only `AssetError`
      though its caller-supplied `admit` callback is invoked unguarded
      and its own exception propagates whatever type it raises — added
      as a generic `Exception` entry (deliberately not folded under
      `AssetError`, since the type is genuinely caller-controlled).
      `AssetError` the class itself (`dskit/assets/base.py`) had NO
      `Raises` section despite `AssetError(42)` raising an undocumented
      `TypeError` from its own bare `list(errors)` — added, matching
      the round 7 dunder-`Raises` precedent. **Given a 13th straight
      round finding real gaps — `Raises` completeness remains open.**
      All 7 Examples blocks spot-checked this round (touched or
      adjacent to rounds 10-15's edits) executed byte-exact; that axis
      stays closed. A 17th round should continue the from-scratch trace
      on the remaining not-yet-covered surface this round flagged but
      did not reach: `read_manifest`/`find_snapshot_dir`/
      `verify_snapshot` in `snapshot.py`, and the `assets/base.py`
      checker family (`_check_str`/`_check_dict`/`_check_unknown`/
      `_raise_if`/`_strip_notes`/`canonical_hash`/`atomic_write_json`).

      **Seventeenth skeptic round (2026-09-12): a 14th recurrence — 2
      MAJOR, both in `snapshot.py`, both execution-verified; the
      `base.py` checker family, `read_manifest`, and a fresh sweep of
      the shortest/simplest-looking functions in the diff
      (`metrics.py`'s functions, `Lineage`'s graph reads,
      `Registry.get`) all came back genuinely clean.** `find_snapshot_dir`
      raises a raw, undocumented `FileNotFoundError` when `root`'s
      `raw/` directory is absent (always present on a root
      `OnboardingRoot.create` built, so only reachable if something
      removed it afterward) — added. `verify_snapshot` had two distinct
      gaps: **(1)** a `manifest.files` entry that isn't a dict, or is a
      dict missing `relpath`, crashes with a raw `TypeError`/`KeyError`
      — `read_manifest` checks only that `files` is a LIST, never each
      entry's shape; **(2)** a payload file present during the initial
      `os.walk` scan but removed by something else before the later
      `lstat`/size/digest check on it raises a raw `FileNotFoundError`
      — this directly **contradicts** the docstring's own explicit
      claim, "a MISSING or DRIFTED payload file is a returned problem,
      never a raise" (that claim is true only for a file already absent
      from the walk, not this TOCTOU case); both added, the existing
      claim narrowed to be accurate. **Given a 14th straight round
      finding real, execution-verified gaps — `Raises` completeness
      remains open**, though the breadth of what came back clean this
      round (an entire checker family, plus three more functions from a
      fresh angle) continues the narrowing trend since round 13.

      **Eighteenth skeptic round (2026-09-12): a 15th recurrence — 2
      MAJOR + 1 MINOR, while `parse_utc`, `dir_digest`, `file_digest`,
      `durable_write_bytes`, `durable_copy_file`, all five of
      `OnboardingRoot`'s path helpers, `features.py`'s validator
      halves, and the `__init__.py` re-export question all came back
      clean.** **MAJOR:** `durable_write_json`
      (`dskit/onboarding/base.py`) documented its `AssetError` as only
      "obj is not JSON-serializable," omitting that it also raises
      `AssetError` when `path` itself is not a non-empty string
      (propagated from `durable_write_bytes`'s own check) — added.
      **MAJOR:** `Registry.find`/`list`/`state` (and `transition`,
      which calls `state` internally) each documented only their own
      local checks, omitting that all four propagate `AssetError` from
      a CORRUPTED STORE (a foreign entry under `records/` or a kind
      directory, or `events.jsonl` replaced with something that is not
      a regular file) — verified by planting each corruption and
      triggering the exact propagated message; this is the established
      "document a propagated exception from a called function"
      convention this same session's own `durable_write_json`/
      `durable_copy_file` docstrings already follow, just not yet
      applied to these four methods — added, each noting the condition
      is an out-of-band mutation, not a normal-use path. **MINOR:**
      `fsync_dir` (`dskit/onboarding/base.py`) claimed "a path that
      cannot be opened is a no-op," but a non-string `directory` (e.g.
      `None`, an int) raises an uncaught `TypeError` from `os.open`,
      not caught by the function's `except OSError` — the claim only
      holds for a well-typed but unusable path; narrowed and a `Raises`
      section added. **Given a 15th straight round finding real
      gaps — `Raises` completeness remains open**, though the breadth
      of clean surface keeps growing (this round's clean list alone
      covers 10 more functions/questions). Not yet from-scratch traced:
      `dskit/onboarding/observations.py`'s `stream_dir`/`stream_digest`/
      `verified_payload_dir` (inspected by reading only this round, not
      execution-verified) — a 19th round should close that out.

      **Nineteenth skeptic round (2026-09-12): a 16th recurrence — 2
      MAJOR + 1 MINOR, closing out the `observations.py` carryover
      (`stream_digest` came back clean; `stream_dir` and
      `verified_payload_dir` did not) plus a comprehensive final sweep:
      10 spot-checked exception TYPES across `metrics.py`/`io.py`/
      `Registry.register`/`load_state`/`check_config`/`publish_version`
      all matched exactly, all ~15 Examples blocks across the 24 files
      re-executed byte-exact, and zero `>>>` occurrences remain.**
      **MAJOR:** `verified_payload_dir`
      (`dskit/onboarding/observations.py`) documented only 4 causes
      (root not initialized, hash malformed, no match, verification
      failure/no files) but omitted 4 more, all verified: `root` not a
      string at all (a different `AssetError` cause than "not
      initialized"); `stream` not filesystem-safe (not mentioned at
      all); a raw `AssetError` propagated from `find_snapshot_dir` when
      its scan encounters a completely UNRELATED snapshot with a
      corrupt manifest before reaching the target (verified — a corrupt
      sibling can mask a legitimate lookup depending on scan order); and
      a raw `TypeError`/`KeyError` propagated from `verify_snapshot`
      when the TARGET's own manifest holds a malformed `files` entry
      (the same shape round 17 already fixed in `verify_snapshot`
      itself, now also documented on its caller). **MAJOR:**
      `find_active_source` (`dskit/onboarding/acquire.py`) never
      type-checks `registry` before calling `registry.find(...)` — a
      malformed value (e.g. `None`, an int, a string) escapes as a raw
      `TypeError`/`AttributeError` instead of the documented
      `AssetError`; documented as the two new entries (kept as a
      documentation fix, not a code change, since `sync_published`'s
      `isinstance(registry, Registry)` check is an ordinary defensive
      pattern here, not an ADR-cited "must mirror" idiom like round 15's
      `resolve_connector` case). **MINOR:** `stream_dir`
      (`dskit/onboarding/observations.py`) had no `Raises` section at
      all, though a non-string `root`/`source` raises a raw `TypeError`
      from `os.path.join` — unlike every path helper on `OnboardingRoot`,
      which validates first; added. **Given a 16th straight round
      finding real gaps — `Raises` completeness remains open** — though
      this round's own closing note states it found no new gaps
      anywhere else across the full 24-file surface beyond these three,
      after the most comprehensive single-round sweep yet run. A 20th
      round should apply these three fixes (now done) and attempt a
      confirmation-only pass with no new targets, to test whether the
      surface has actually reached a fixed point.

      **Twentieth skeptic round (2026-09-12): a confirmation-only pass
      (re-verify round 19's fixes + 5 random spot-checks, no new-target
      hunting) — a 17th recurrence, one MAJOR, everything else clean.**
      All 3 round-19 fixes re-verified accurate and complete by
      execution. 4 of 5 random picks (`metrics.py::pinball`,
      `record.py::check_payload`, `connector.py::check_message`,
      `layout.py::OnboardingRoot.snapshot_dir`) came back clean. The
      5th, `resolve()` itself (`dskit/pipeline/resolve.py`) — the
      top-level orchestrator whose OWN sprawling branch tree had never
      had the full from-scratch treatment even though its callees
      (`pipeline_hash`, `write_run_dir`, `ResolvedPipeline`) all had —
      had a MAJOR gap: its `ValueError` entry named only ~9 of the
      branches it actually raises, missing 6 distinct causes, each
      verified live: an unimportable `pkg.module:Attr` reference
      anywhere (`optimization.kind`, a features step, a tracking sink,
      or a custom stage); a class-reference `optimization.kind`,
      features-step `kind`, or custom stage that imports but is not
      callable; a features step `kind` neither registered nor claimed
      by the backend; a class-reference `model.name` missing a required
      `train`/`load` method; and a class-reference tracking sink
      missing a Tracker seam method. All 6 added (same `ValueError`
      type as already documented — a missing CAUSE list, not a wrong
      type). **Given a 17th straight round finding a real gap — even a
      pure confirmation pass — `Raises` completeness remains open, but
      the finding rate keeps falling** (4 of 5 random picks clean, only
      one true orchestrator function still uncovered). A 21st round
      should retry the confirmation-only pass now that `resolve()` is
      fixed.

      **Twenty-first skeptic round (2026-09-12): another confirmation
      pass — an 18th recurrence, 5 MAJOR + 1 MINOR.** A second
      independent from-scratch trace of `resolve()`'s full body (the
      exact function round 20 just rewrote) found **4 more real gaps in
      the same `ValueError` enumeration**: a class-reference
      `model.name` that fails to import (the "does not import" list
      named every OTHER class-ref site but omitted this one);
      `backend.fingerprint()` returning a NaN/Infinity value, which
      propagates `pipeline_hash`'s `ValueError` (only the sibling
      `TypeError`-for-a-`set` case was documented); a non-class-reference
      tracking sink `kind` no longer in `SINK_KINDS` (reachable via
      registry drift between construction and resolve); and a required
      environment variable named in `config.env.require` that is absent
      (`EnvConfig`'s OWN docstring already says this happens "at resolve
      time," yet `resolve()`'s `Raises` never mentioned it). All 4
      added. Separately, `Registry.get()` and all four `Lineage` read
      methods (`parents`/`children`/`ancestors`/`descendants`, which all
      call `get()` internally) documented "absent or kind undeclared" as
      the only causes, omitting that a MALFORMED (non-64-hex)
      `version_id` raises the same `AssetError` type for a third, no
      less real cause — fixed in all five docstrings from one shared
      phrase. **MINOR:** `LocalFilesConnector.discover()`/`.read()`
      (`dskit/onboarding/libs/localfiles.py`) documented a dangling
      symlink's `FileNotFoundError` but not a symlink LOOP's `OSError`
      — the exact pattern round 14 already fixed once in `ingest_run`,
      never propagated to this sibling (which is, notably, the ORIGINAL
      site round 10 first found the dangling-symlink half of this same
      pattern in) — added to both methods. **Given an 18th straight
      round finding real gaps, even under a confirmation-only mandate —
      `Raises` completeness remains open.** The recurring shape is now
      explicit: gaps cluster in (a) long-branch-tree orchestrators
      (`resolve()`, twice now) and (b) shared helpers whose callers
      inherit an incomplete enumeration (`Registry.get`/`Lineage`,
      `verify_snapshot`/its callers). A 22nd round's random picks should
      keep favoring multi-branch functions and widely-called helpers
      over simple leaf functions, per this round's own recommendation.

      **Twenty-second skeptic round (2026-09-12): targeted the two
      highest-risk shapes directly (orchestrators, shared helpers) — a
      19th recurrence, 1 MAJOR + 1 MINOR + 1 NIT, and both `resolve()`
      and `run_acquisition` (the two orchestrators re-traced a SECOND
      time) came back genuinely clean, confirming those two fixed
      points hold.** **MAJOR:** `load_config`
      (`dskit/pipeline/io.py`) — the adapter-import loop
      (`importlib.import_module(module)`) is unguarded beyond Python's
      own import machinery; the docstring named only
      `ModuleNotFoundError`, but a bad nested import inside an adapter
      surfaces as a plain `ImportError` (verified live), and any other
      top-level failure in the adapter (a crashing `raise RuntimeError`
      at module scope, verified live) propagates untouched — the same
      "caller-controlled/plugin code raises whatever it raises, only
      the narrowest case documented" shape round 16 already fixed once
      in `scan_stream`'s `admit` callback; added the same generic
      `Exception` entry. **MINOR:** `check_config`
      (`dskit/onboarding/connector.py`) calls `connector.spec()`
      unguarded before any shape checking of the return value — a
      connector whose `spec()` implementation raises propagates that
      exception raw (verified live); added, same shape/treatment.
      **NIT:** `write_run_dir`'s (`dskit/pipeline/resolve.py`)
      `FileExistsError` entry framed the cause as strictly a TIMING
      RACE ("between the check above and the commit"), but a plain
      pre-existing FILE (not directory) at `run_dir` hits the identical
      error deterministically on the very first call, no race required
      — the non-empty check tests `os.path.isdir` first, which is
      structurally blind to a non-directory occupant; narrowed to
      describe both the simpler case and the race. **Given a 19th
      straight round finding real gaps — `Raises` completeness remains
      open** — though two multiply-re-traced orchestrators (`resolve()`,
      `run_acquisition`) are now confirmed stable across repeated
      independent passes, and this round's own findings were both
      instances of an already-catalogued shape rather than a new one.
      A 23rd round should grep specifically for other unguarded
      first-calls into caller/implementer-owned code (an abstract
      method, an injected callback, an import loop) across the 24
      files, per this round's own recommendation, rather than another
      full sweep.

      **Twenty-third skeptic round (2026-09-12): the targeted grep for
      "unguarded call into caller/implementer-owned code" — a 20th
      recurrence, 3 MAJOR, surfacing at the three main caller-extensible
      seams in this diff (`Connector`, `Store`, `Backend`).**
      `run_acquisition` (`dskit/onboarding/acquire.py`) calls the
      resolved connector's `check()`/`read()` unguarded — a `Connector`
      subclass raising `RuntimeError` from `check()`, or
      `ConnectionResetError` from `read()`, propagates raw (both
      verified live); the ABC documents the contract but never enforces
      it. `Registry.register`/`transition`
      (`dskit/assets/registry.py`) call `self.store.put_record`/
      `append_event` unguarded on a caller-supplied
      :class:`~dskit.assets.store.Store` implementation — a custom
      `Store` raising `MemoryError` propagates raw (verified live);
      `transition` inherits the same gap via its own `append_event`
      call. `resolve()` (`dskit/pipeline/resolve.py`) — its THIRD
      finding in 3 rounds, but the first on this axis rather than
      `ValueError`-completeness — calls the caller-injectable `backend`
      parameter's `discover_instruments`/`fingerprint`/`supported_*`
      unguarded; a backend raising `PermissionError` from
      `discover_instruments` propagates raw (verified live). All three
      fixed with a generic `Exception` entry, matching the
      already-established treatment for this exact shape
      (`scan_stream`/`load_config`/`check_config`). **Given a 20th
      straight round finding real gaps, though every one of them was an
      instance of an already-catalogued shape (none new) — `Raises`
      completeness remains open.** A 24th round should re-check whether
      any OTHER `Registry`/`Lineage` method beyond `register`/
      `transition` calls `store.*` unguarded in a way not already
      covered by the FileStore-corruption documentation added in round
      18 (`find`/`list`/`state` document specific KNOWN corruption
      modes of the built-in store, not an arbitrary custom `Store`'s
      arbitrary behavior) — the same distinction that made this round's
      `register`/`transition` finding a genuine gap despite those
      methods looking superficially covered.

      **Twenty-fourth skeptic round (2026-09-12): resolved round 23's
      own follow-up and found it was a genuine, wider gap than
      expected — a 21st recurrence, 7 MAJOR, all the same
      caller-owned-`Store`/backend propagation shape at every remaining
      site.** Confirmed the specific-corruption wording in `find`/
      `list`/`state` does NOT generalize: a custom `Store` raising a
      generic `MemoryError`/`ConnectionError` propagates through all of
      `Registry.get` (which had NO store-propagation entry at all, not
      even narrow wording), `find` (two undocumented paths — both
      `list_records` AND `get_record`), `list`, and `state` — all four
      gained a generic `Exception` entry alongside their existing
      specific-corruption `AssetError` wording. `Registry.__init__`
      itself calls `store.model_pin()` unguarded (verified: a raw
      `ConnectionError` propagates) — added to the class docstring.
      `Lineage.add`/`parents`/`children`/`ancestors`/`descendants` all
      inherit the identical gap via their internal
      `Registry.get`/`store.append_event` calls (verified live on all
      five) — added to each. Separately, `OnboardingRoot.registry()`
      calls `open_store(...)` unguarded — verified two ways: a
      `store/store.json` that exists (passing `__init__`'s presence-
      only check) but is corrupted raises `AssetError` undocumented,
      and a `pkg.module:Class` backend reference whose own `__init__`
      raises propagates raw — the identical "unguarded call into
      caller/implementer-owned code" shape round 23 fixed at
      `Connector`/`Store`/`Backend`, now also fixed here. **Given a
      21st straight round, and every finding again an instance of an
      already-catalogued shape — `Raises` completeness remains open,
      but this shape (caller-extensible seam propagation) now appears
      close to exhaustively covered**: `Connector`, `Store` (via
      `Registry`/`Lineage`), `Backend`, and store-backend-by-reference
      have all been checked. A 25th round should do a final broad
      confirmation sweep across fresh, unrelated functions rather than
      continuing to mine this one shape, to test whether a genuinely
      different defect class remains.

      **Twenty-fifth skeptic round (2026-09-13): a deliberately broad
      sweep avoiding the now-exhausted "caller-owned code" shape — a
      22nd recurrence, 2 MAJOR, both genuinely NEW shapes (not
      propagation).** `metrics.py`'s `pinball`, `trainlog.py`'s
      `TrainingCurve.summary`/`.payload`, `record.py`'s `check_payload`/
      `AssetRecord.to_obj`/`.version_id`, and `connector.py`'s
      `check_message` all came back clean under fresh traces checking
      Parameters/Returns accuracy and Raises-type correctness (not just
      completeness). **MAJOR:** `LocalFilesConnector.read()`
      (`dskit/onboarding/libs/localfiles.py`) and its sibling
      `SampleConnector.read()`
      (`children/_skeleton/yourproject/connectors.py`) both build
      `new_state = {k: dict(v) for k, v in state.items()}` to validate
      each per-stream cursor value — but an EMPTY per-stream value
      (``""`` or ``[]``) makes `dict(v)` succeed silently (`dict("") ==
      {}`), slipping past that eager check; a few lines later, the
      cursor is read off the ORIGINAL, unconverted value via
      `state.get(stream, {}).get("cursor", "")`, which crashes with an
      undocumented `AttributeError` — verified live on both files with
      `state={"samples": ""}` and `state={"samples": []}`. Added an
      `AttributeError` entry to both, matching each other's existing
      `TypeError`/`ValueError` phrasing. **MAJOR:** `publish_version`
      (`dskit/onboarding/publish.py`) reads `cert.payload["decision"]`,
      `cert.refs["snapshot"]`, and `snap.payload["mode"/"acquired_at"/
      "manifest_hash"]` by unguarded bracket access — the exact
      "accepts any registry whose model declares X, crashes on a
      non-default model that omits a field" shape round 16 already
      fixed once in `sync_published`/`find_active_source`, never
      checked against this sibling until now; verified live with a
      custom `AssetModel` whose `certification` kind omits `decision`,
      raising a raw `KeyError`; added, matching round 16's phrasing
      ("never reachable against the default model"). All 15 Examples
      blocks across the 24 files re-executed byte-exact, zero `>>>`
      remain, and a full top-to-bottom read of this TODO item's own
      24-round history found no internal contradiction or dropped
      follow-up. **Given a 22nd straight round finding real gaps — even
      in a search that deliberately avoided the most productive known
      shape — `Raises` completeness remains open.** A 26th round should
      re-run this same deliberately-broad, shape-agnostic method on a
      fresh set of functions.

      **Twenty-sixth skeptic round (2026-09-13): generalized round 25's
      two shapes (found no new instances of either) and traced 5 fresh
      functions — a 23rd recurrence, 2 MAJOR, both the "caller-owned
      Store propagation" shape on functions that had never individually
      been swept, not a new shape.** `Registry._spec`, `build_manifest`,
      and `logloss` (a full fresh trace of the whole `metrics.py` file)
      all came back clean. **MAJOR:** `Lineage.edges()`
      (`dskit/assets/lineage.py`) — the shared engine underneath
      `add`/`parents`/`children`/`ancestors`/`descendants`, all five of
      which got their OWN `Store`-propagation fix in round 24 — had no
      `Raises` section at all, and calls `self.registry.store.iter_events()`
      unguarded; verified live with a `Store.iter_events` raising
      `MemoryError`. Fixed, and the four sibling methods' existing
      `Exception` entries (which named only the `Registry.get`
      resolution step) broadened to also cite this method, since
      `parents`/`children` call it directly and a raising
      `iter_events` was previously undocumented from THIS path too.
      **MAJOR:** `sync_published` (`dskit/assets/sync.py`) documented
      only the narrow `KeyError` case (a model missing a required
      ref) for "escapes the per-file catch, aborts the whole scan," but
      `_sync_one` also calls `registry.store.has_record(...)`
      *directly*, bypassing `Registry`'s own already-fixed wrapper —
      verified live that a `Store.has_record` raising `MemoryError`
      propagates the same way. Fixed with a generic `Exception` entry
      alongside the existing `KeyError` one. **Given a 23rd straight
      round, and this round's own closing note that the "direct
      caller-owned-seam bypass" sub-shape (skipping `Registry`'s
      wrapper entirely, as `_sync_one` does) is narrower than what
      rounds 23-24 swept and may have more instances — `Raises`
      completeness remains open.** A 27th round should specifically
      grep for other direct `registry.store.*`/`connector.*`/
      `backend.*` calls that bypass an already-fixed wrapper method,
      rather than another fully generic sweep.

      **Twenty-seventh skeptic round (2026-09-13): the direct-bypass
      hunt came back clean, but surfaced a related, bigger recurrence
      instead — a 24th straight round, 4 MAJOR + 1 MINOR.** No new
      bypass-of-a-wrapper sites exist; every `.store.`/`connector.*`/
      `backend.*` call in the 24 files already routes through an
      already-documented wrapper. But three functions that DO call
      `Registry`'s/`Lineage`'s own already-fixed, properly-wrapped
      methods (`ingest_run` → `registry.register`/`Lineage.add`;
      `publish_version` → `registry.get`/`registry.register`;
      `run_acquisition` → `registry.register`, its evidence-writing
      step) never cited that those calls can themselves propagate the
      store's exception — a caller reading only, say, `ingest_run`'s
      own `Raises` would not learn this without separately opening
      `Registry.register`'s docstring. Verified live on all three: a
      `Store.put_record` raising `MemoryError` escapes each,
      undocumented. Added a cross-referencing `Exception` note to each.
      Separately, round 26's own `sync_published` fix was found
      INCOMPLETE: its new `Exception` entry named only the direct
      `has_record` bypass, but `_sync_one`'s properly-wrapped
      `registry.find`/`registry.get`/`registry.register`/`Lineage.add`
      calls escape the identical per-file catch just as unguarded —
      verified live for all four (`list_records`/`get_record`/
      `put_record`/`append_event` failures all propagate past the
      `except AssetError` and abort the whole scan) — broadened to name
      all of them. **MINOR:** `TrainingCurve.record`
      (`dskit/pipeline/trainlog.py`) had its own round-19 fix
      MISFILED: the `metrics`-is-truthy-but-not-a-dict clause was
      appended under the `TypeError` heading, but the actual exception
      is `AttributeError` (`'list' object has no attribute 'items'`)
      — moved to its own, correctly-typed entry. **Given a 24th
      straight round — `Raises` completeness remains open**, though
      this round's framing corrects round 26's: the residual gap isn't
      really "direct bypass of a wrapper" but "a caller of an
      already-fixed wrapper method not cross-citing that method's own
      propagation note in its own docstring" — a documentation-linking
      gap, not a new code-behavior discovery. A 28th round should check
      whether any OTHER caller of `Registry.register`/`.get`/`Lineage.add`
      in the 24 files has the same missing cross-reference.

      **Twenty-eighth skeptic round (2026-09-13): the exhaustive
      call-site inventory — a 25th straight round, 4 MAJOR, all the
      same cross-reference-gap shape, at sites round 27 hadn't
      reached.** Built a full table of every `Registry`/`Lineage`
      call site across the 24 files; 4 of ~8 distinct callers were
      missing or incomplete. **`find_active_source`**
      (`dskit/onboarding/acquire.py`) had no `Exception` entry at all
      for its `registry.find`/`registry.state` calls — verified live
      with a `Store.list_records` raising `MemoryError`. **`run_acquisition`**'s
      existing `Exception` entry (added round 27) named only the
      evidence-writing `registry.register` calls, omitting its own
      earlier `registry.get(config_vid)` lookup — verified live.
      **`OnboardingRoot.registry()`**'s existing entry (added round
      24) covered only the backend-opening step via `open_store`, not
      the separate `store.model_pin()` call `Registry.__init__` makes
      when constructing the returned `Registry` — verified live with a
      backend whose `__init__` succeeds but `model_pin()` raises.
      **`Registry.transition`**'s existing entry (added round 23) named
      only its own `append_event` call, omitting its own internal
      `self.get()`/`self.state()` calls — notably, `state()`'s own
      docstring already correctly cites its internal `get()` call,
      making `transition()` inconsistent with its own sibling's
      pattern; verified live. All four fixed with the same
      cross-referencing phrasing established in round 27. A private-
      helper sweep (`_spec`, `_closure`, `_sync_one`, others) found no
      further gap — both `Registry`/`Lineage`-touching private helpers
      are already fully covered by their public callers. **Given a
      25th straight round — `Raises` completeness remains open** — but
      the call-site table this round built is now believed exhaustive:
      every `Registry`/`Lineage` method call across the 24 files has
      been individually checked and either already cited correctly or
      just fixed. A 29th round should re-run this exact grep as a pure
      confirmation (expecting zero results) rather than searching
      further, and if that holds, move to unrelated functions per
      round 27's original step-3 fallback (the 3 longest/most-complex
      not-yet-individually-named functions).

      **Twenty-ninth skeptic round (2026-09-13): CLEAN — the loop's
      stopping condition is met.** Re-ran round 28's exact
      `Registry`/`Lineage` call-site inventory as a pure confirmation
      (reading each CURRENT docstring, not trusting the prior round's
      summary): every site holds. Full-effort from-scratch traces of
      the 3 longest not-yet-individually-named functions in the 24
      files (`_place_file` in `acquire.py`, `OnboardingRoot.records_dir`
      and `.state_path` in `layout.py`, with `features.py`'s
      `_filter_validator` spot-checked as a tied-length fourth) all
      matched their docstrings exactly across every axis
      (Parameters/Returns/Raises/Examples) — zero discrepancies. One
      NIT found and fixed: a single line in `run_acquisition`'s round-27
      cross-reference (100 chars, the only line over 99 in the whole
      diff) was reflowed. **Zero MAJOR, zero MINOR — the stopping
      condition the user set is met.** 25 straight rounds (4-28) of
      genuine, execution-verified `Raises`/`Returns`/`Examples` defects
      converged, over the last 9 rounds (21-28), onto one narrowing
      family — "a caller of an already-fixed wrapper method
      (`Registry`/`Lineage`, `Connector`, `Store`, `Backend`) not
      cross-citing that method's own documented propagation" — and
      round 29's exhaustive re-confirmation of that exact family, plus
      fresh unrelated surface, both came back clean on the first pass.
      This is treated as a genuine fixed point for the 24 files this
      effort actually touched, not merely a quiet round: **the skeptic-
      review sub-effort on the CONVERTED 24 files is now closed.** This
      top-level item stays open only because 30 of the original 57
      pre-standard modules remain unconverted (untouched by this whole
      effort) — draining those is separate, un-started work, not part
      of what the 29 rounds here reviewed.
- [x] **Convert the 81 unexecuted `>>>` lines** across 17 docstrings in
      `assets/`/`onboarding/` to `::` blocks. They read as verified doctests
      and nothing collects them. Biggest: `assets/model.py:64`,
      `assets/ingest.py:68`, `onboarding/coverage.py`.
      **Landed this run (2026-08-28, A5):** grep clean; CLAUDE.md gained
      the `# ->` output-marking rule the conversion needed; doctest
      directives disposed of honestly (wildcards not re-marked literal;
      the lineage envelope verified byte-exact by execution; unrunnable
      sketches say so).

## Running experiments — NOT plug-and-play today (2026-08-27)

Asked directly: can we run a tracked experiment on `intraday_poc` right now?
**No.** The engine has the seams; nothing has been wired through them. What
already works, with no sink at all: every run writes a dir named
`{name}-{asof}-{run_hash[:8]}` holding `config.json` (the whole document
verbatim), `plan.json`, `resolved.json`, per-node records, `report.md`, and
per-model artifacts with a `state_hash` sidecar — one full dir per fold under
walk-forward. Reproducibility is strong and content-addressed. **Comparison
across runs is what is missing**, plus the wiring below.

- [x] **`log_params` carries no hyperparameters.** `driver.py:934` sends
      exactly five fields — `name`, `asof`, `document_hash`, `run_hash`,
      `nodes`. Not node params, not the architecture. So even with a sink
      attached you could not filter runs by `hidden_size` or `lr`. Widen it
      to flatten each node's params (`<node>.<path>` keys, the space-key
      grammar `hpo-grid` already uses). **This blocks every other item
      here** — do it first.
      **Landed this run (2026-08-28, E1):** ONE core flattener
      (`document.flatten_param_paths`); one `log_params` per run at run
      start, five fields kept; keys follow the declared post-override
      tree, references log as declared (never resolved); grammar parity
      with space keys pinned BOTH halves, mutation-proven.
- [x] **No tracking sink ships.** The seam is complete and deliberate —
      `Tracker` protocol (`protocols.py:86`), `SINK_KINDS` +
      `register_sink_kind` (`base.py:1232`), a `tracking.sinks` config
      section — with only a test `memory` sink registered.
      `base.py:1227` says outright "real sinks (mlflow) register
      application-side". An MLflow sink is a tier-2 pack
      (`dskit/pipeline/libs/mlflow.py`) + an optional extra, per tiering.
      Gotcha to design around: `_Trackers` SWALLOWS per-sink exceptions
      (`driver.py:139-153`) so tracking can never fail a run — meaning a
      misconfigured sink logs nothing and says nothing.
      **Landed this run (2026-08-28, E3):** the pack ships, defaulting to
      a local sqlite store so it needs no server. The swallowing gotcha is
      answered by validating LOUDLY at plan — an unknown scheme, an
      unwritable path or an unreachable server fails `plan()`, never the
      run — while a genuinely transient failure (sqlite lock contention)
      degrades, because tracking must never kill a good run.
      **This card also closed a latent identity defect:** `tracking` was
      hash-GRADED, so declaring a sink — or merely repointing its store —
      changed a document's identity and would have orphaned every run
      directory keyed to it. `tracking` now joins `env`/`outputs`/
      `schedule` as non-identity, via `NULLED_IDENTITY_SECTIONS`: it is
      rendered UNDECLARED rather than removed, because every hash ever
      written counted a `"tracking": null` key and popping it would have
      moved them all. Absent, store A and store B now hash alike, and all
      16 ledger hashes are byte-identical (that is the proof).
- [x] **No cross-run comparison exists at all.** The CLI has
      `run`/`walkforward`/`plan`/`validate`/`nodemap` and nothing that
      lists runs with their metrics. Today you read `report.md` files by
      hand. Even without MLflow, a `dskit.pipeline runs` verb that scans
      `pipeline_runs/` and tabulates (hash, asof, key metrics) would cover
      most of the need with zero dependencies — arguably do this BEFORE
      the sink. **Landed this run (2026-08-28, E2):** tier-1
      `pipeline/runs.py` + the `runs` verb — structured records only
      (never report.md prose), metrics overlay record+carry, foreign and
      broken entries named loudly-but-nonfatally, restatements pinned
      (metric rule vs driver, run root, layout names), 64 new tests.
- [x] **Neither intraday document declares a search node.** Adding
      `hpo-grid`/`optuna-search` is config-only, but note the shape
      problem: two trainers (`qhat_aapl`, `qhat_msft`) means every tuned
      knob needs duplicate space keys that nothing pins together — the
      `foreach` gap again, surfacing in the search space this time.
      **Landed this run (2026-08-28, D3):** `run-train.json` declares an
      `hpo-grid` search over ONE space key naming the `foreach` template.
      **Correction to the premise above, found by running it:** `foreach`
      pins the DECLARATION, not the tuned VALUE. The single key expands to
      one key PER INSTANCE and `hpo-grid` CROSSES them — 3 widths × 2
      symbols = 9 trials, and a winner may pair 16 with 64. So it removes
      the forgotten-copy failure (a third symbol is one line) but does NOT
      force the two symbols to share a width; no grammar in the engine ties
      two nodes' params to one value. Said plainly in the document notes,
      the child CLAUDE.md and the pin's docstring, because the opposite is
      the natural assumption.
- [x] **HPO inside walk-forward is mechanically supported but UNTESTED and
      semantically undecided.** `run_walk_forward` puts every fold through
      `run_document`, so each fold builds its own `_SearchSeam` and re-tunes
      independently. `tests/pipeline/test_walkforward.py` contains no HPO.
      That is defensible nested CV, but it costs folds x trials runs and
      yields a DIFFERENT winner per fold, so there is no single best config
      to ship. Decide the semantics, then test it.
      **Landed this run (2026-08-28, C7) via ADR-0043 — and the "nested CV"
      label above is WRONG, which the ADR corrects:** a fold's evaluation
      window IS its val split, the planner forces every search objective
      onto a val score node, and folds refuse a cal band, so no outer band
      de-biases the fold's score. Per-fold re-tune therefore STANDS as
      MEASUREMENT of the tuning procedure — valid for comparing procedures
      under one fold plan, never a deployment estimate. Shipping is the
      plain `run`; freezing a winner means EDITING the document, which moves
      its hash by design (no `freeze` knob, no `search_mode`). A run now
      SURFACES its search, node-keyed so K>1 search nodes stay distinct:
      trials executed, and the winner and score when the kind produced them
      — presence, not value, separates "no winner" from "a winner of None",
      and a winner JSON cannot hold is recorded as DROPPED, never coerced.
      The summary carries it per fold and in aggregate, so **per-fold winner
      instability is now a printed diagnostic instead of folklore**. Eight
      HPO tests where there were none; the HPO-free summary is proven
      byte-identical (mutation-proven twice). `driver.py` converted and its
      ignore entry DRAINED. Deferred by the ADR: a fold-internal outer band,
      the only route to an unbiased tuned-pipeline estimate.
- [x] **The intraday store is not on this machine** — `./ob` must be
      re-acquired before any of this runs. Blocker zero for an actual
      experiment.
      **Closed this run (2026-08-28, D2) — and the premise was stale:** the
      store WAS on the machine (1.2 GB, last written Aug 26), so this was a
      catch-up, not a re-acquisition. Brought current through the README
      recipe: acquire → validate → certify → publish, every step exit 0.
      **2,016,587 bars** (AAPL 1,097,321 / MSFT 919,266), 2021-01-01 →
      2026-08-28T19:06Z, zero duplicate `(symbol, ts)` rows, and the seam
      between the old and new snapshots is contiguous — old max 23:13Z, new
      min 23:14Z, no overlap and no gap. `verify` reports 0 problems before
      and after. Peak RSS 138 MB, nowhere near the 18 GB ceiling.
      The card also proved A2's single-source `--mode` rewrite is a
      behavioural NO-OP against a store acquired under the old config: the
      registered source record still carries the pre-A2 payload, and
      resolving both through `resolve_knobs` yields identical effective
      knobs, so no re-registration was needed. NOT yet exercised:
      `--mode live` (this card was scoped to backfill); the backfill cursor
      is now within the free-tier 16-minute SIP clamp of the tape, which is
      the safe moment to turn live on.

Also required before the numbers mean anything, tracked above: `monitor`
unset (you would be comparing last-epoch models, not best-epoch) and
`epochs` unpinned between the two documents — both landed this run, as
did continuous optuna ranges (proven e2e; the plan-refusal claim was
stale).

### DO THIS LAST — wire HPO into `intraday_poc`

- [x] **Add a search node to the intraday documents.** Owner's explicit
      sequencing (2026-08-27): **this is the final item, after everything
      else in this file.** Not because it is hard — it is config-only, and
      the engine side already works — but because it is the CAPSTONE that
      consumes nearly every other fix, and doing it early produces numbers
      that quietly mean nothing.

      **DONE 2026-08-28 (D3) — a real tracked experiment exists on this
      machine.** Run on the live store (2,016,587 bars): 9/9 trials in
      4m28s, peak RSS 8.07 GB, objective `$select.metrics.total_realized`
      over 12,818 realized picks on the embargoed tail. Winner
      `hidden_size` 16/16 at 0.2274; runner-up 32/16 at 0.2265; the
      declared 32/32 base pass scored 0.0302 — i.e. the tuned arm beat the
      value that had simply been typed in. **Reproducible:** the identical
      document re-run gave the same `run_hash` and all nine trial scores
      bit-for-bit equal, checked elementwise rather than on the winner
      alone. Trials are distinguishable by params in the mlflow sink (E1's
      flattened keys) and via the `runs` verb. `run-train.json`'s identity
      moved intentionally, `85fff271…` → `f320458f…`, and is in the ledger.
      The winner was deliberately NOT promoted into the documents: doing so
      edits both documents together (the cross-document pin) and an
      asymmetric winner would have to defeat the symbol-twin regime pin —
      an owner call, left standing as a follow-up.

      To be clear about what does and does not exist: **HPO in dskit is
      built and green.** `hpo-grid` (tier-1, `kinds_search.py`) and
      `optuna-search` (tier-2, `libs/optuna.py`) both work end to end on
      the driver's `ctx.rerun` seam; `examples/pipeline/optuna-search.json`
      plans clean and 199 tests cover them. What is missing is only that
      **neither intraday document declares a search node** — verified, zero
      hits across all six configs. Both run at fixed `epochs: 5`,
      `lr: 0.001`, `hidden_size: 32`.

      Why every prerequisite genuinely bites here:
      - **`monitor` unset** → trials are scored on last-epoch weights, so
        the search optimizes epoch-5 luck rather than the model.
      - **`epochs` unpinned across the two documents** → a tuned backtest
        never reaches the production fit; you would ship an untuned model
        while believing it was tuned.
      - **`log_params` carrying no node params** → trials are
        indistinguishable in any sink; you get scores with no way back to
        the config that produced them.
      - **No `runs` verb / no sink** → nowhere to SEE the comparison.
      - **Continuous ranges plan-refused** → `lr` can only be tuned as a
        categorical list, which is the wrong shape for a learning rate.
        *(Closed this run, E4: range specs plan AND run e2e —
        `examples/pipeline/optuna-continuous.json`.)*
      - **No `foreach`** → `qhat_aapl` and `qhat_msft` need duplicate space
        keys with nothing pinning them, so the two symbols can silently be
        tuned to different architectures.
      - **HPO x walk-forward undecided** → re-tunes per fold, so there is
        no single winner to ship.
      - **`./ob` not on this machine** → nothing runs at all.

      Start when those are closed, and start SMALL: one categorical knob
      (`hidden_size: [16, 32, 64]`) on `run-train.json` only, walk-forward
      left alone, so the first tracked experiment tests the plumbing rather
      than a hypothesis.

## Model selection + feature selection (owner design, 2026-08-27)

Goal in the owner's words: do out of the box what pycaret does — sweep many
models from a LIST, with feature selection as a declared step, and plug the
same selection criteria into a torch model. Needs an ADR before code; fold
the ruling below into that ADR's context.

**RULED OUT, owner-confirmed 2026-08-27: no pycaret (or any AutoML framework
of that shape).** Not a dependency-weight objection — an architectural one.
dskit's premise is that the JSON document declares the process and the engine
reads it; pycaret's premise is that pycaret owns the workflow (preprocessing,
comparison, tuning, plotting). Adopting it would put a second, opaque process
declaration inside a document that is supposed to BE the declaration, and its
internals would sit outside the identity hash — so two runs could report the
same identity and do different things. We get the same capability from the
pieces we already have: the `SklearnFit` doorway for the models, the search
kinds for the sweep, and the new selector seam for features. If this is ever
revisited, the question to answer first is how a framework that owns its own
pipeline can live under a grammar that owns the pipeline.

**First, what already exists — do NOT rebuild it.** `SklearnFit` is already
the doorway ("a DOORWAY, not a model registry: the estimator is named by the
document" — `libs/sklearn.py:17`). `"estimator": "sklearn.ensemble.RandomForestRegressor"`
works today *(spelling corrected this run, E5: the estimator grammar is the
DOTTED path — the colon form is the `uses:` grammar and is refused at
plan)*, and `estimator` is in `_PARAMS` with `train` a searchable role,
so `space: {"model.estimator": [...]}` on an existing search node is already
the model sweep. **Per-model wrapper classes would be ~20 classes each
re-doing what the doorway does, and 20 new places to drift. Do not write
them.**

- [x] **LightGBM as an optional extra.** It ships an sklearn-compatible API,
      so `"lightgbm.LGBMRegressor"` (dotted — see the spelling correction
      above) resolves through `SklearnFit` — no pack, no wrapper. Same for
      xgboost/catboost if wanted later.
      **Landed this run (2026-08-28, E5):** extra ships self-sufficient
      (`lightgbm`, `scikit-learn`, `joblib` — the transformers precedent:
      the path runs THROUGH the sklearn doorway) + in `all`; the doorway
      proven by a fitted LGBMRegressor under importorskip; declared
      contents pinned from pyproject.
- [x] **A documented estimator LIST — as config, not code.** The "common
      models" ask is a cookbook: a worked `examples/pipeline/model-sweep.json`
      whose search space enumerates the usual candidates (linear, ridge, RF,
      gradient boosting, SVM, kNN, LGBM), plus a table in the sklearn pack's
      docs. VERIFY the sweep actually plans and runs before writing the
      cookbook — the reasoning above is from reading the rules, not a run.
      **Landed this run (2026-08-28, E5):** verified BY RUNNING first (6
      candidates e2e, RandomForest wins); shipped as the cookbook example
      (in the hash ledger) + the pack-docstring table, candidate list
      triple-pinned; sweep constraints discovered by running are in the
      example's notes (one params block serves every trial → no
      estimator_params/seed on a mixed sweep; unclipped regressor beliefs
      → squared_error on this binary venue as the documented tier-1
      exception).
- [x] **Feature selection — genuinely absent, real new capability.** Grepped:
      zero hits for any selector anywhere in `dskit/`.

      **A selector is FITTED, so it is not a `transform`.** It learns which
      columns survive from TRAINING data. Every existing transform
      (`Filter`, `Derive`, `Concat`, `Join`, `ArrayMap`, `ArrayFeatures`) is
      stateless and pure, and `_ArrayApply`'s causality guard DEPENDS on that
      purity — it re-runs `apply` on truncated prefixes and refuses if the
      output moved, which a fitted selector trips by design. So it needs its
      own seam, not a slot in an existing one.

      **Leakage is the one hard rule: fit on train ONLY, apply to val/test.**
      A selector that sees validation rows leaks invisibly — the scores just
      come out better. The seam must make "which split did you fit on"
      declared and checkable at plan time, the way the `score` role already
      declares `split`.

      Shape (mirrors `PyomoSolve` / `ArrayFeatures`): a library-agnostic base
      with ONE hook returning the surviving feature names, so
      `libs/sklearn.py` supplies the sklearn selectors (`SelectKBest`, `RFE`,
      `SelectFromModel`, `VarianceThreshold`, mutual-info) BY IMPORT PATH —
      the doorway pattern again, never a registry of wrappers — and
      `libs/torch.py` supplies importance-from-a-fitted-net through the SAME
      interface. Outputs carry the selected feature LIST as an artifact, not
      just projected rows, so serving uses the identical columns.
      **Landed via ADR-0042 / C6 (2026-08-29).** `FeatureSelector` is a
      member of ADR-0040's family (not a second seam); `sklearn-select`
      names any selector by import path; `torch-importance` ranks a wired
      net by input-gradient magnitude. The surviving list is the sidecar.
      `libs/torch.py` drained in the same change (last card to touch it).
- [x] **The governing class.** Owner: "we need some parent … we might use
      this on a deep learning model at some point." Half is already
      approved — **`TrainableNode` (3c above) is the parent for the model
      side**; build it first and make the selector seam its sibling, or the
      two abstractions get designed against each other.
      **Landed via ADR-0042 / C6 (2026-08-29).** The letter was not
      followed (and the ADR says so): the selector subclasses
      `FittedTransform`, which already subclasses `TrainableNode`, so the
      lifecycle is written once. A second mode dispatch would have been
      the duplication both ADRs exist to remove.
- [x] **All three owner flows fall out of ONE modular selector node** — the
      difference is only where the node sits and what the search space
      covers. Build the node right and the flows are document edits:
      1. *One feature set, sweep models*: selector upstream of the model;
         space over `model.estimator`.
      2. *Per model, select then score*: same graph, space over BOTH keys
         (`model.estimator` AND the selector's method/params) — the search
         enumerates the pair, which `hpo-grid` already does across multiple
         space keys.
      3. *Select once by a stated method, then sweep*: selector upstream
         with fixed params; space over `model.estimator` only.
      So the design target is the node's interface, not three code paths.
      Keep the selector's fitted state and its declared fit-split as first
      class, and all three compose.
      **Landed via ADR-0044 (2026-08-29).** C6 had 1 and 3. Flow 2 needed
      the searchability guard narrowed from the role to
      `FittedTransform._PARAMS`; a space over `select.n` now plans, a
      space over `select.fit_split` still refuses.

## A time-series architecture zoo in the torch pack (owner ask, 2026-08-27)

Out-of-the-box architectures for financial / quant / betting-market work, so
a new project starts by NAMING one instead of writing one. The point is that
no project rewrites a 1D CNN or an LSTM regressor ever again.

**The owner is right that the work is straightforward — but ONE constraint
decides the shape, so settle it before writing code.**

- [x] **You cannot subclass `nn.Module` at module level anywhere in
      `dskit/pipeline/`.** **Landed via ADR-0041 / C5 (2026-08-29).** Every
      net is defined inside a builder / `build_module`. `torch.py` stayed
      byte-identical; the catalog is `libs/torch_ts.py`. The child's
      `models.py` no longer imports torch.
- [x] **Decide how the zoo stays SWEEPABLE — the real design call.**
      A search space key must address an existing PARAM; `uses` is the
      node's kind, not a param, so **one node-pair per architecture cannot
      be swept** by the search seam. Two ways out:
      - *Node pair per architecture* (the `LinearRegressor` shape): simple,
        matches precedent — but architecture selection then happens by
        editing the document, not by a search.
      - *One `torch-timeseries` pair with an `arch` PARAM*, resolved through
        a small builder REGISTRY (`_ARCHS = {"lstm": _build_lstm, …}` plus a
        `register_arch`). `arch` is a param, so
        `space: {"model.arch": ["lstm", "gru", "tcn", "dlinear"]}` sweeps
        architectures directly — which is what the model-selection work
        above wants. A registry table is the repo's sanctioned middle ground
        (`_OPS`, `METRICS`, `SPLIT_POLICIES`), NOT the string switch the
        pillars rule forbids.
      **Landed via ADR-0041 / C5 (2026-08-29).** One pair
      (`torch-ts-train` / `torch-ts-predict`) over `register_arch`; `arch`
      is a swept param.
- [x] **The architectures.** Owner-named: MLP, LSTM, GRU, simple
      Transformer. Researched additions worth their place:
      - **DLinear / NLinear** — one-layer linear models from Zeng et al.
        2023 ("Are Transformers Effective for Time Series Forecasting?").
        They beat many transformers on long-horizon benchmarks and are the
        HONEST BASELINE: if an LSTM cannot beat DLinear on your series, that
        is the finding. Ship these first; they are also the cheapest.
      - **TCN** (dilated causal convolutions) — causal by construction,
        parallel, fast; a strong bar-data baseline.
      - **1D CNN** — cheap, and the owner's own example of a thing no child
        should ever write twice.
      - **PatchTST** — patching + channel independence, the transformer
        variant that actually works on TS.
      - **GRU/LSTM + attention** — a rung between the simple and heavy ends.
      - *N-BEATS* only if someone needs it — heaviest, least general.
      **Landed via ADR-0041 / C5 (2026-08-29).** Ships `dlinear`, `nlinear`,
      `mlp`, `lstm`, `gru`, `lstm_attn`, `gru_attn`, `tcn`, `cnn1d`,
      `patchtst`. N-BEATS still excluded.
- [x] **Parameterize the HEAD, do not fork the zoo.** Quant work regresses a
      return; betting/binary markets classify an outcome — and binary
      markets are already first class here (`records.py:176`
      `BinaryAccounting` vs `MarkToMarketAccounting`). One `head` param
      (`"regression"` | `"binary"`) selecting the output layer and the loss
      keeps the zoo at N architectures instead of 2N classes. Note this
      needs the missing `loss` knob from the Configurability section above —
      today `RowVectorAdapter.loss` is hardcoded MSE.
      **Landed via ADR-0041 / C5 (2026-08-29).** `head` selects the default
      `loss` (B2's import-path knob). No `register_head`.
- [x] **The zoo and a child's `models.py` do NOT compete — they are default
      vs bespoke.** `models.py` stays, permanently, as the seam for an
      architecture a project genuinely invents. What the zoo removes is
      RE-WRITING a standard net per child. Concretely for `intraday_poc`:
      `NextBarLSTM` is a plain LSTM regressor over a flat lag vector —
      standard, so once the zoo ships the child should NAME the zoo's
      version and drop its hand-rolled copy, while keeping `models.py` for
      anything bespoke later. That switch is also the proof the zoo is
      actually generic. Do NOT read this as "children stop having models".
      **Landed via ADR-0041 / C5 (2026-08-29).** Documents name
      `torch-ts-train` / `arch: lstm`. `NextBarLSTM` deleted. `models.py`
      is the empty bespoke seam. `live.restore_model` rebuilds via
      `TimeSeriesTrain.build_module`. Child hashes moved as declared.

