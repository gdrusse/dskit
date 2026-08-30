# TODO

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
- [ ] **Drain the pre-standard ignore list.** 73 modules sit in
      `pyproject.toml` under `per-file-ignores`; delete a module's entry when
      you convert it. That list IS the remaining work, in config form.
- [x] **Convert the 81 unexecuted `>>>` lines** across 17 docstrings in
      `assets/`/`onboarding/` to `::` blocks. They read as verified doctests
      and nothing collects them. Biggest: `assets/model.py:64`,
      `assets/ingest.py:68`, `onboarding/coverage.py`.
      **Landed this run (2026-08-28, A5):** grep clean; CLAUDE.md gained
      the `# ->` output-marking rule the conversion needed; doctest
      directives disposed of honestly (wildcards not re-marked literal;
      the lineage envelope verified byte-exact by execution; unrunnable
      sketches say so).

## Configurability

Knobs the toolkit ALREADY offers that `intraday_poc` leaves unset (2026-08-27,
found by diffing the documents against `DeclaredTrain._BASE_PARAMS` +
`_EXTRA_PARAMS`). All config-only unless marked:

- [x] **`monitor` is never set, though `val_rows` IS wired.** ADR-0035
      shipped val-metric checkpoint selection; `run-backtest.json` computes
      per-epoch val loss into `training_curve.json` and then keeps the LAST
      epoch's weights anyway. Five epochs of validation, computed and
      discarded. Set `"monitor": "val_loss"` on both trainer nodes —
      one line, and it is the difference between the backtest scoring the
      best model and scoring whichever epoch happened to be last.
      **Landed this run (2026-08-27, A1):** set on both `run-backtest.json`
      trainers. `run-train.json` wires no `val_rows`, so the engine refuses
      a validation monitor there — left undeclared, and the resulting
      best-vs-last-epoch skew is declared in both documents + README and
      pinned deliberate by `test_configs.py`.
- [x] **No `loss` knob exists in the torch pack** — NOT config-only, a real
      gap. `RowVectorAdapter.loss` (`libs/torch.py:493`) hardcodes
      `mse_loss`, and neither `_BASE_PARAMS` nor `_EXTRA_PARAMS` carries a
      `loss` entry, so changing to Huber/MAE — the obvious choice for
      fat-tailed return series — requires writing an adapter subclass. The
      pack exposes `optimizer` as a declared import path; `loss` should
      work the same way.
      **Landed this run (2026-08-28, B2):** `loss` + `loss_params` as
      declared import paths, optimizer parity, default
      `torch.nn.functional:mse_loss` byte-identical. Three defects the
      review caught and closed: the node's params are the ONLY read
      (threaded as arguments, so an adapter's own knob can never shadow
      the plan-validated objective); the `applies_loss` promise witnesses
      BOTH hooks of the flow, so replacing either without re-declaring
      ends it; a duck-typed adapter with no doorway keeps its pre-knob
      behaviour and is refused BY NAME when an objective is declared.
- [x] **`device` is never declared** — CPU-only by default. The knob exists
      (`libs/torch.py:873`, validated as a string, availability
      deliberately unchecked at plan). A GPU run is a document edit today
      only because nobody wrote it. **Landed this run (2026-08-27, A1):**
      `"device": "cpu"` declared on all four trainer nodes with notes;
      presence pinned by `DECLARED_TRAINER_KNOBS`.
- [x] **`optimizer_params` unused** — no `weight_decay`, no betas. Adam at
      defaults. **Landed this run (2026-08-27, A1):** declared at Adam
      defaults (`{"weight_decay": 0.0}`) on all four trainers with notes;
      presence pinned.
- [x] **`WindowRows` hardcodes the key field names it reads** —
      `row.get("symbol")` / `row.get("asof_ms")` (`nodes.py:204-206`) while
      `price_field` IS a knob. One of three field names configurable, two
      welded: a second project has different names. Same shape as the
      `MarketRecord` envelope coupling.
      **Landed this run (2026-08-28, C2) via ADR-0040:** the pack lifts DECLARED
      key/order/value fields through public accessors, so a keyed series with
      any vocabulary enters; `WindowRows` keeps its own spellings by
      overriding them. An overridden accessor NARROWS the knob set — a
      refusal, not a convention — so validation can never approve a value
      the run discards.
- [x] **`BarsFromStore` hardcodes the whole scan shape** —
      `key_fields`/`ts_field`/`shared_fields` (`nodes.py:117-119`); only
      `stream` is a knob, though `scan_stream` takes all four as
      parameters.
      **Landed this run (2026-08-30):** all three are knobs on
      `BarsFromStore`, defaulting to `BAR_KEY_FIELDS` /
      `DEFAULT_TS_FIELD` / `DEFAULT_SHARED_FIELDS`. A second vocabulary
      is a document edit.
- [x] **Nothing normalizes or scales features anywhere** — raw log returns
      go straight into the LSTM, and the toolkit ships no standardizer
      node either. Fold into the window-transform ADR: a scaler must be
      fit on TRAIN only and carried to val/test, so it is a stateful
      transform, not a formula.
      **Landed this run (2026-08-28, C2) via ADR-0040:** the fitted-transform family ships
      in tier-1 `fitted.py`, with a standardizing scaler as its first
      member: `fit` learns from the DECLARED split only, `apply_state` is
      pure and row-independent, and a `purity_check` screen catches the
      classic leak of recomputing a statistic over the rows handed in.
      Leakage refuses at plan where the document can be read.

- [x] **Continuous ranges in `OptunaSearch` — teach the planner the spec-dict
      grammar.** Owner-approved 2026-08-27. `{"low": .., "high": ..[,
      "log": true][, "int": true]}` already validates inside
      `libs/optuna.py:_spec_problems`, but `planner._search_errors`
      hardcodes the list-of-JSON-scalars space grammar for EVERY
      search-role node, so a continuous document fails `plan()` before
      `OptunaSearch` is ever consulted. Categorical documents work end to
      end today. The pack flags this itself (`libs/optuna.py:43-50`, the
      INTEGRATION FLAG) and cannot fix it — that file may not edit the
      planner. The gap is already pinned by a test in
      `tests/pipeline_libs/test_optuna.py`, so the fix flips that test.
      Keep the grid's categorical form working unchanged — both forms
      share the `"<node>.<param.path>"` key grammar, and `hpo-grid` must
      keep REFUSING continuous specs (exhaustive enumeration over a real
      interval is meaningless). Pruning stays deliberately absent: the
      `ctx.rerun` seam returns one float per trial, so there is nothing to
      prune against until a per-epoch reporting seam lands.
      **Landed this run (2026-08-28, E4) — with a correction:** the
      planner half was ALREADY in the baseline (the pin was already
      flipped at `93ed7e2`; this item's planner claim was stale). What
      was genuinely missing shipped: a continuous document proven e2e
      through the real driver, the shipped twin example
      `examples/pipeline/optuna-continuous.json` (in the hash ledger),
      pins for `hpo-grid`'s range-spec refusal (it had none and was the
      only guard), the INTEGRATION FLAG retired, and the ref-driven
      deferral truth stated where the docs overclaimed it.

- [x] **Gap-aware vectorized window transform — extend `ArrayFeatures`, do
      NOT build a new seam.** Owner-approved 2026-08-27, needs an ADR first.
      The tier-2 doorway already exists (`libs/numpy.py`: `ArrayMap` /
      `ArrayFeatures` / `TrailingReturns`) with per-instrument grouping and
      the causality guard; `WindowRows` in `intraday_poc` reimplements it
      badly and forgoes that guard. Two blockers to close:
      1. **`_lift` is welded to the `MarketRecord` envelope** (`instrument`,
         `contract`, `mid` — `libs/numpy.py:106,154`). Any keyed time series
         must be able to enter: declare the key/order/value fields instead.
         Same coupling as HIGH finding 2 below — fix once.
      2. **Positional offsets silently bridge time gaps.**
         `TrailingReturns` computes `mid[t]/mid[t-window]` straight across a
         session boundary. Needs gap-aware framing — the ONE thing
         `WindowRows` gets right (`nodes.py:220-228`) and a naive port would
         lose.
      Requirements the owner stated: **vectorized**, and speed is a
      first-class concern (the 2M-bar run is the benchmark — see ADR-0037's
      650 B/row). Ops needed: group, order, gap-split, log/pct return, lag
      N, lead N (the forward label). NOT a row-wise formula DSL — `derive`
      already is one and structurally cannot express a lag. Payoff:
      `WindowRows` collapses to one `apply()`, inherits the lookahead
      screen, and `live.py:latest_feature_row` stops existing (killing HIGH
      finding 4).
      **Landed this run (2026-08-28, C2) via ADR-0040:** gap-aware framing via `max_gap`
      (absent reproduces today exactly), the ops (group/order/gap-split/
      log+pct return/lag N/lead N), and vectorized with measured evidence.
      The child's gap semantics were PINNED FIRST against the old node,
      proven able to fail twice, then the rewrite landed underneath the
      same pin untouched — that is the parity proof. `WindowRows` is now
      one `apply()` and INHERITS the causality screen it never had;
      `live.py:latest_feature_row` is gone, killing the third copy of the
      chain semantics and the train/serve skew with it.

Hardcoding audit (2026-08-27) — every finding is the SAME failure: one value
in two places, nothing pinning them, so changing one and not the other is
silent. "Pinned?" is the column that matters; `lookback` is the benchmark
(`test_lookback_agrees_everywhere` + the pack's `seq_len` floor).

HIGH — silent wrong behavior:

- [x] **`WindowRows`/`BarsFromStore` write every default TWICE** — once in
      `validate_params`, once in `run`/`_scan`: `"bars"`
      (`nodes.py:102`/`:119`), `"close"` (`:177`/`:198`), `5`
      (`:182`/`:199`). Validation then approves a value the run never uses.
      Nothing catches it. Fix: name each default once as a module constant,
      the `libs/torch.py:135-136` idiom (`DEFAULT_EPOCHS`, `LOADER_DEFAULTS`).
      **Landed this run (2026-08-28, A2):** each default named ONCE as a
      module constant read by both `validate_params` and the run; pinned by
      rebinding the constant and watching both follow.
- [x] **The numpy pack re-implements `MarketRecord`'s rules** —
      `libs/numpy.py:113-120` (`_price_ok`, `_lead_ok`) and `:106`/`:110`
      restate `records.py:69-76`/`:150-155`/`:122-132`. Tier-2 duplicating
      tier-1 truth, failing SILENTLY: a value failing `_WRITEBACK` leaves the
      field unchanged, counted and logged, never raised. Loosen a bound in
      `records.py` and `ArrayMap` quietly drops legitimate writebacks.
      **Landed this run (2026-08-28, C2) via ADR-0040:** `_price_ok`/`_lead_ok` are deleted
      and the writeback table holds the tier-1 predicates BY IDENTITY, so
      loosening a bound in `records.py` can no longer silently drop a
      legitimate writeback. The review went further: `price_ok` and
      `lead_frac_ok` were themselves the 3rd and 4th copies of one numeric
      rule, now expressed as narrowings of a single owner,
      `records.number_ok`.
- [x] **The bar primary key is stated twice** — `connectors.py:219`
      (`primary_key`) and `nodes.py:117-118` (`key_fields` to `scan_stream`).
      The bitemporal dedup keys off the NODE's copy; diverge and the store
      dedupes on the wrong tuple. `tests/test_connectors.py:93` freezes only
      the connector side.
      **Landed this run (2026-08-28, A2):** one `BAR_KEY_FIELDS` constant in
      `connectors.py`, imported by `nodes.py` (identity-pinned, plus a
      rebinding pin proving the scan reads it).
- [x] **`price_field` is a knob in training, hardcoded in live** —
      `nodes.py:198` vs `live.py:186` (`float(b.close)`). Set
      `"price_field": "vwap"` and the backtest trains on VWAP while live
      feeds close returns into the same weights. Pure train/serve skew.
      `test_live_window_parity` uses `close` on both sides, so it is blind.
      **Landed this run (2026-08-28, A2):** `live.py` READS `price_field` from
      the run dir's document; `test_live_window_parity` is parameterized over
      `close` AND `vwap` on deliberately different series, so the blindness
      the audit named now fails loudly.
- [x] **`epochs: 5` is the one knob the pinning test omits** —
      `run-train.json:38`/`:54`, `run-backtest.json:166`/`:224`.
      `tests/test_configs.py:64-65` pins seven knobs between the documents;
      `epochs` is not among them, though the test's docstring claims it
      proves the documents share their modelling core. **One-string fix.**
      **Landed this run (2026-08-27, A1):** grew past the one-string fix —
      the pin now compares the trainers' params dicts whole (monitor the
      one declared divergence, its values pinned too), plus presence,
      symbol-twin, and monitor/val_rows-coupling pins; each proven able
      to fail.
- [x] **`adjustment` disagrees three ways** — `all`
      (`source-backfill.json:6`), `raw` (`source-live.json:6`), and the
      vendor default (`live.py:178-183` passes none). Training is
      corporate-action-adjusted; the forward loop is not, though
      `source-backfill.json:2` argues `all` is required to keep the return
      series stationary.
      **Landed this run (2026-08-28, A2):** the live fetch takes `adjustment`
      from the SOURCE config through the connector's own knob gate — the loop
      restates neither the value nor the default.

MEDIUM:

- [x] **`--artifact SYMBOL=PATH` does not exist.** `live.py:48` and
      `run-train.json:3` both document it as the override that makes
      `DEFAULT_ARTIFACTS` "never an edit here" — `main()` has no such
      argparse argument (`live.py:227-240`). Either add the flag or stop
      promising it. The `live.py:258` fallback already reproduces both
      table entries, so `DEFAULT_ARTIFACTS` is pure redundancy.
      **Landed this run (2026-08-28, A2):** the `--artifact SYMBOL=PATH` flag is
      implemented (relative and absolute paths pinned) and `DEFAULT_ARTIFACTS`
      is deleted — the convention fallback already reproduced it.
- [x] **The model class path is a literal in the live loop** —
      `live.py:101` refuses anything but `intraday_poc.models:NextBarLSTM`,
      while the documents declare it (`run-train.json:33`/`:49`,
      `run-backtest.json:126`/`:184`). Swapping the declared module — the
      whole point of the ADR-0025 seam — breaks serving. Loud, but it undoes
      the seam.
      **Landed this run (2026-08-28, A2):** the live loop resolves the class the
      run DECLARED, by path, through `import_library_class` — the ADR-0025
      seam restored; a foreign or unloadable ref is refused by name.
- [x] **`utf-8` written twice in the localfiles pack** —
      `libs/localfiles.py:126` (`discover`) and `:153` (`read`). Diverge and
      the schema is inferred under one encoding while rows decode under
      another: mojibake, no exception. **Landed this run (2026-08-28, A4):**
      one `_DEFAULT_ENCODING`, both call sites, prose pinned to the
      constant with a terminated needle.
- [x] **`source-live.json` is disconnected.** It registers as `alpaca-live`;
      both run documents read `"source": "alpaca"`. Live-acquired bars never
      reach the modelling path — no error, just an unused store.
      **Landed this run (2026-08-28, A2):** resolved by DELETING the second
      config: one source name (`alpaca`) carries both pulls, separated by
      `--mode backfill|live` on the cursor the onboarding seam already keys
      per (source, stream, mode). README, configs and code tell one story,
      pinned by `test_one_source_name_carries_both_pulls`.
- [x] **`APCA_API_BASE_URL` is inert.** `.env.example:7` advertises the paper
      endpoint; `live.py:251-252` hardcodes `paper=True` and never reads it.
      Safe direction today, but the file advertises a control that is not
      wired.
      **Landed this run (2026-08-28, A2):** deleted from `.env.example`;
      `paper=True` stays hardcoded (refusal-by-default).
- [x] **`README.md:105-107` states the opposite of the code.** It claims a
      bad `source` "yields an empty scan, not an error";
      `observations.py:214-218` raises `AssetError`. Correct the doc.
      **Landed this run (2026-08-28, A2):** the README states what
      `observations.py` actually does — a mistyped source raises `AssetError`,
      pinned by `test_a_mistyped_source_refuses_loudly`.

LOW:

- [x] `live.py:51` `_SIP_FIELDS` is dead — no reader anywhere in `children/`.
      **Landed this run (2026-08-28, A2):** deleted.
- [x] Defaults restated in prose AND code: `libs/restapi.py:40`/`:155`/`:275`
      (`30`) and `:42`/`:159`/`:278` (`3`); `_skeleton/yourproject/
      connectors.py:42` `_DEFAULT_START` vs its `spec()` note at `:57` —
      doc-drift only, but the skeleton's copy propagates to every child.
      **restapi half landed this run (2026-08-28, A4):** `_DEFAULT_TIMEOUT`
      / `_DEFAULT_MAX_RETRIES` (+ `_DEFAULT_PAGE_START`, same defect class),
      spec() notes read the constants, module prose pinned by test.
      **Skeleton half landed this run (2026-08-28, A2):** the `spec()` note
      is built from `_DEFAULT_START`, pinned by rebinding the constant and
      watching all three consumers follow. The same treatment went to the
      child connector's own five spec defaults (feed / adjustment /
      key_env / secret_env / lookback).

Confirmed CORRECT — do not "fix": `suite-bars.json:33` restating the symbol
vocabulary (an assertion reading its expectation from the thing it validates
asserts nothing); the skeleton tests restating `factor: 2.0` / `rows: 3`;
`live.py:88-94` re-deriving the `state_hash` recipe (pinned by the e2e, and
verifying with the writer's own function would prove nothing).

Found reading `children/intraday_poc` for flexibility (2026-08-27) — the
child hardcodes what should be config, and the pipeline cannot express
"one model per key" without longhand:

- [x] **A `timeframe` knob on the connector spec, and a serving path that
      READS the configs it already has.** `TimeFrame(1, Minute)` is
      hardcoded at `connectors.py:169` and again at `live.py:180`, though
      it is the same category of knob as `feed`/`adjustment`, which ARE
      config. Deeper: `live.py` re-declares what already exists —
      `DEFAULT_ARTIFACTS` and `max_gap_minutes=5.0` restate the training
      document that the driver already writes to `<run-dir>/config.json`
      (`driver.py:870`), and `fetch_bars` re-implements the connector's
      `_fetch` against its own client. Fix: knob on `spec()`; `live.py`
      reads `<run-dir>/config.json` for the modelling knobs and the
      source config for the vendor knobs. Child-only, no ADR. A THIRD
      config file is the wrong answer — it would duplicate both.
      **HALF LANDED (2026-08-28, A2) — the serving half is DONE, the knob
      is not.** `live.py` now reads the modelling knobs (`price_field`,
      `max_gap_minutes`, `lookback`, the declared module class) from the
      run dir's document through the engine's own `load_document`, and the
      vendor knobs from the source config through the connector's knob
      gate; `DEFAULT_ARTIFACTS` is gone and credentials are ONE shared
      rule (`connectors.resolve_credentials`). STILL OPEN: `timeframe` is
      not yet a `spec()` knob — the two hardcoded `TimeFrame(1, Minute)`
      sites were consolidated into one `connectors.bar_timeframe()`, so
      the agreement is single-sourced and the remaining work is to promote
      it to config.
      **Landed this run (2026-08-30):** `timeframe` is a `spec()` knob
      (default `BAR_INTERVAL`); both `_fetch` and `live.fetch_bars` build
      from the resolved pair. Declared on `source-backfill.json`.
- [x] **A `foreach` section in the document grammar — needs an ADR.**
      "One model per symbol" is written longhand: adding a third symbol
      means four new nodes in `run-train.json` and six in
      `run-backtest.json`, plus extending both `concat` blocks. The
      reference grammar is only `$node.output` and `$prev`
      (`document.py:26-37`) — there is no per-key subgraph expansion.
      Precedent exists: `run_walk_forward` already derives N documents
      from one, suffixing each `-wf-<cutoff>`; this generalizes that to
      a declared key list. Scope the ADR to fan-out over a key list
      ONLY — general templating would make configs a programming
      language and dissolve the identity hash. A `foreach` section IS
      identity: it changes what the run computes.
      **Landed this run (2026-08-28, C3) via ADR-0039.** The document
      STORES what was written and DERIVES what runs: `foreach` is one
      hash-bearing field, while `expanded`/`foreach_groups` never reach
      `to_obj` — and with no `foreach` declared, `expanded` IS the
      `pipeline` object itself, which is what makes every engine site
      that switched to reading it byte-identical. **The flagship proof:**
      a two-key `foreach` document expands to exactly its hand-written
      longhand twin — node for node, port for port, same plan order —
      and both run e2e to byte-equal output, so this is fan-out and not a
      second execution path. Identity pinned BOTH ways (a `foreach` twin
      hashes differently; `foreach.notes` does not), with six mutation
      proofs. `$each` is whole-value only and refused as a params KEY;
      port fan-out is opt-in via `<base>__each`; collisions, empty key
      lists and `$`-prefixed keys refuse by name. Search spaces fan out
      per instance — the N unpinned duplicate space keys the capstone
      needed. New example `examples/pipeline/foreach-fanout.json` is in
      the ledger. `planner.py` converted and its ignore entry drained.

- [x] Create a concise `CLAUDE.md` for this project.
- [x] ADR-0020 integrity-parity pass (2026-08-24) closed the deferred
      loud-not-silent register: FileStore OSError wraps + foreign-entry
      doctrine, `\Z` anchors, purity-gate relative-import levels,
      sqlite URI `mode=rw`, storage-key trust on every backend,
      battery coverage for all of it, ruff baseline pinned in
      `pyproject.toml` (classic defaults; tree clean).

- [x] `append_event` broken-symlink guard (2026-08-25): FileStore
      mirrors the iter_events squat guard, so a dangling events.jsonl
      symlink refuses loudly instead of creating the target.

- [x] Driver-side stderr streaming of TrainingCurve lines (2026-08-25):
      the parent's StreamHandler hunk is ported — during a run, INFO
      lines stream bare to stderr unless the caller already has a live
      stream handler, and the handler is removed on every exit path.
      Closes the ADR-0025 residual.

Proposed by the pmquant child design (2026-08-26) — thirteen generic gaps,
each with a named dskit home and interim child-side handling. The full
statements are §13 of `docs/children_design_proposals/pmquant.md`; none is an
ADR yet, and none blocks the child from starting:

- [x] 1. `stat_test` evidence self-description (`kinds_stats.py`) — the one
      unported pmquant→dskit engine capability. **Landed via ADR-0033 (2026-08-26).**
- [x] 2. Studentized recentered cluster bootstrap-t as a `stat_test` method
      (`stats.py`) — **unblocked the single-document deploy→size path**.
      **Landed via ADR-0033 (2026-08-26).**
- [x] 3. Registrable family corrections / weighted BH (`stats.py`).
      **Landed via ADR-0033 (2026-08-26).**
- [x] 4. A calibration split block (`cal_start_ms` in `base.TimeSplitConfig`).
      **Landed via ADR-0034 (2026-08-26).**
- [ ] 5. Calibration/scoring `libs/` packs (beta, CORP isotonic, cross-fit,
      Efron lfdr, Venn–Abers, proper scoring rules).
- [ ] 6. Acquire-side coverage hook + guarded parallel acquisition
      (`onboarding/acquire.py`).
- [ ] 7. A grouped/cardinality suite rule (`onboarding/validate.py` `_RULES`).
- [x] 8. Compressed snapshot payloads in onboarding (~96× on gz-class
      archives, ~10× on parquet-class). **Landed via ADR-0036 (2026-08-26;
      the ratified Tier-B sunset path — pmquant's Tier-B bypass retires
      onto it when the child builds).**
- [ ] 9. A generic `records-write` kind beside `table-write`
      (`kinds_table.py`).
- [ ] 10. A generic onboarding-observations reader kind — the second child
      to need it. **Half landed via ADR-0037 (2026-08-26): the function
      seam (`observations.scan_stream`/`stream_digest`) exists; the
      pipeline-facing KIND stays open until a second child needs it.**
- [ ] 11. A records → keyed-table verb (`groupby`/`pivot`, `kinds_flow.py`).
- [ ] 12. Search-seam expressiveness for seed-ensemble studies + per-fold
      node-param binding (`kinds_search.py`, `document.py`/`driver.py`).
- [x] 13. Val-metric checkpoint selection in the torch pack (a `monitor` +
      best-state-restore seam; the curve already computes the row).
      **Landed via ADR-0035 (2026-08-26).**

Found by the first real-data run of `children/intraday_poc` (2026-08-26) —
reclassified generic by the owner (generic-first: children are wrappers):

- [x] `intraday_poc` bars-node memory (14.3 GB peak on 2,013,682 bars;
      three walk-forward folds killed at 17.4 GB against an 18 GB cap;
      blocked `run-backtest.json`). **Fixed via ADR-0037 (2026-08-26):**
      the scan graduated into `dskit.onboarding.observations.scan_stream`
      / `stream_digest` — single-copy dedup, canonical-string sharing,
      incremental digest with byte-parity to the frozen dump recipe
      (identity frozen for one-spelling-per-instant stores; see the
      ADR-0037 review amendments) — and `BarsFromStore` shrank to a
      wrapper. Measured 650 B/row peak vs 1547 B/row for the defect
      (~2.4×); peak-pinning tracemalloc tests stand at BOTH layers. The
      same session widened the child's score-split tuples to accept
      `"cal"` (ADR-0034) and made its reader codec-aware (ADR-0036) —
      both RE-ENTRY action items. The backtest re-run still needs the
      store re-acquired (the `ob/` root is not on this machine).

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

## Long-term goal — a generic SERVING LOOP in dskit

**Not now. No ADR yet, no code.** Recorded so the design constraints
discovered on 2026-08-27 are not rediscovered later.

**The goal.** dskit runs documents in batch; it has no seam for running a
fitted model FORWARD on a cadence. `children/intraday_poc/intraday_poc/live.py`
is the only forward loop that exists, it is 309 hand-rolled lines, and it is
where most of that child's HIGH-severity defects live. That is the signal:
the loop is generic capability sitting in tier 3.

**What is generic** (belongs in `dskit/`) — restore a run's artifacts with
the hash verification the packs already do; poll a registered source on a
cadence; re-execute a declared subgraph of the SAME document the backtest
scored; emit one decision record per tick; gate on a supplied calendar.

**What is NOT generic** (stays tier-3, per child) — the venue/broker API,
order types, position semantics, and any calendar's actual contents. The
loop takes an executor OBJECT; it never learns a venue.

Design constraints, each learned the hard way — read before designing:

- **The loop READS configs; it never restates them.** Every serious defect
  in `live.py` is a restatement: `DEFAULT_ARTIFACTS`, `max_gap_minutes=5.0`,
  the bar timeframe, `price_field` hardcoded to `.close` while training
  treats it as a knob. The driver already writes the whole training document
  to `<run-dir>/config.json`; the source config already holds the vendor
  knobs. A serving loop that re-declares either WILL drift from the backtest,
  and a parity test over the mechanism will not catch a differing FIELD.
- **Fetch through the CONNECTOR, not beside it.** `live.py:fetch_bars`
  builds its own vendor client and re-implements `_fetch`; that is why the
  live and backfill pulls disagree on `adjustment` with nothing noticing.
  The onboarding seam already keys cursors per (source, stream, mode) — a
  live pull is `acquire --mode live`, not a second data path.
- **Decide with the SAME object the backtest used.** `build_select_model` is
  the one thing `intraday_poc` got right: a module-level function both sides
  call. Generalize that shape — the loop re-runs a subgraph, it does not
  re-implement the decision.
- **Money needs the capital seam, which is half-built.** `kinds_report.py`
  already expects a flow-aware capital stage publishing `twr`, `mwr`,
  `cumulative_contributions`, `equity_curve` — and NOTHING produces them.
  The only shipped `capital` node (`BudgetedSelect`) takes a static scalar
  budget. Periodic contributions make the problem path-dependent (what you
  can buy at t depends on the balance after t-1), so it is a sequential
  replay, not a one-shot solve. Note `role: capital` is gated: the planner
  refuses un-gated capital without a `stat_test` survivors wire.
- **Side effects need a refusal-by-default posture.** `live.py` only ever
  constructs `TradingClient(..., paper=True)`. Whatever generic executor
  seam lands must make "actually move money" an explicit, loud, declared
  act — never a default and never a config typo away.
- **Cadence is already declared but never executed.** A document's
  `schedule` section parses, is hash-excluded, and the runner ignores it; a
  `clock` section parses and refuses to run. Decide whether the serving loop
  finally gives those meaning or leaves them documentation.

Prerequisites already tracked above: the `foreach` grammar gap, the
gap-aware window transform (kills `latest_feature_row`), and `TrainableNode`
(a serving loop is the load path's biggest consumer).

## Long-term goal — Hugging Face integration in `libs/transformers.py`

**Not now.** Recorded because the blocking decision is architectural, not a
wrapper-writing task, and is worth deciding once rather than rediscovering.

**What already exists — a complete fine-tune/predict doorway.**
`TransformerFit` (role `train`) with a `build_model` hook,
`TransformerPredict` (role `signal`) loading from a pinned checkpoint,
`DeclaredTransformerFit` (`transformers-fit`) letting the DOCUMENT name any
`config_class` / `model_class`, and checkpoints written with a sidecar plus a
`content_digest` covering the file tree AND the sidecar's own fields,
re-verified on load. That half is done; do not rebuild it.

**What is deliberately absent: ALL hub access.** From the pack's own docs —
models are built from a CONFIG OBJECT, *"the one HF path that reads no cache
and opens no socket; there is no `from_pretrained`, and no place to put a hub
name. The pack's no-network property is preserved by construction rather than
by convention."* So today you can fine-tune a fresh architecture and cannot
touch a single pretrained weight. For most HF use that IS the point, so this
is the integration.

- [ ] **Decide how a PRETRAINED model enters without breaking identity.**
      Blocking; everything else waits on it. A bare hub name
      (`"bert-base-uncased"`) is not content-addressed — the weights behind
      it can change while the document hash does not, so two runs would claim
      one identity and compute different things. Exactly what the identity
      hash exists to prevent.

      **Recommended shape: a model download is an ACQUISITION, not a pipeline
      concern.** dskit already has the machinery — Package 2 acquires,
      snapshots WORM, builds a manifest, digests, and `verify` re-hashes. An
      HF snapshot is the same object as a data snapshot. Acquire the model
      once through onboarding, then point the existing nodes at the LOCAL
      verified directory — the mechanism the pack already implements. Keeps
      no-network-at-run, keeps the content digest meaningful, adds no new
      trust surface. If a direct hub path is ever wanted instead, it must
      carry a pinned `revision` commit sha, never a bare name.
- [ ] **Add a feature-extraction / embedding node — the real gap.** The pack
      has train and signal but nothing turning text into FEATURE ROWS for a
      downstream model. For markets work that is the common case: news,
      filings or headlines → embeddings or a sentiment score → columns joined
      onto a bar stream. Shape it like `ArrayFeatures` (role `tensor`,
      outputs `rows`) so it composes with the model- and feature-selection
      work above.
- [ ] **Time-series foundation models are the domain payoff.** Chronos
      (Amazon), TimesFM (Google), Moirai (Salesforce), Lag-Llama are all
      HF-hosted zero-shot forecasters and directly relevant to quant/markets
      work — a zero-shot baseline you never trained is the strongest check
      that a bespoke LSTM is earning its keep. Needs the pretrained-weights
      decision first. Pairs with the DLinear "honest baseline" argument in
      the architecture-zoo item.
- [ ] **Then, and only then, the wrappers.** Once weights can enter safely
      the wrappers are the cheap part, following the pack's existing subclass
      contract: sentiment/classification, embeddings, zero-shot forecast.
      Keep them subclasses supplying `build_model` / `encode` — never a
      registry of per-model classes, same doorway doctrine that makes
      `SklearnFit` and `DeclaredTrain` work.

Deferred:

- [ ] Engine-level multi-writer coordination (Registry/Lineage
      check-then-act) — needs its own ADR if ever wanted (ADR-0018
      amendment scopes concurrency to the store seam). No consumer
      needs it; leave until one does.
- [ ] Move-planted vid appears in the wrong kind's id LISTING (declared
      out of ADR-0020, round-3 residual, loud downstream: every
      dereference refuses; fixing needs O(n) content loads, defeating
      the sqlite index). Stays declared, not fixed.
