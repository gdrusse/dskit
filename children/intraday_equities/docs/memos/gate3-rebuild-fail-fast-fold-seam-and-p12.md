# Gate 3 rebuild: the fail-fast audit, the fold seam and the forty-name study

## TL;DR

Three things were built overnight, each looped through hostile review until
no CRITICAL or MAJOR finding remained: P11's Gate 3 now stops an asset at the
first scrambled refit that matches or beats it (ADR-0092); fold execution
moved out of the child into dskit as `BoundedFoldRunner` (ADR-0093); and the
forty new names of cohorts D and E got their own staged study, P12, built the
same way P11 is built but with one feature cache per source group (ADR-0094).
No gate was run for real. A one-asset smoke (ORCL) proved the P12 wiring end
to end in 6 minutes 55 seconds at a median 2.10 seconds per fold against the
3.40-second baseline, and measured the group-D cache build at 13.88 GiB under
the 17 GiB cap. The largest caveats: ORCL failed Gate 1 at one minute, so the
staged run never drew a null (one null walk was run separately as evidence);
the group-E cache has never been built; and five of the forty names carry a
reconciliation the selection notes say is owed before they are modelled.

## Execution contract

- Built 2026-09-05 on the WSL2 workstation, in git worktrees off `main`.
  Branches (all merged into `main` and deleted afterwards):
  `feat/adr-0092-fail-fast-gate3`, `feat/adr-0093-bounded-fold-runner`,
  `feat/adr-0094-cohort-study`.
- Governing records: ADR-0092 and ADR-0093, already accepted when the brief
  was issued; ADR-0094, written first, looped clean in three rounds, then
  accepted on the owner's pre-authorization in the brief.
- Document identities (`load_document(...).hash`):
  - P10 `configs/run-p10-modelability.json`: unchanged,
    `b7c8efe93664c65a71407f81cd903e47503976c6d6849b9e7bb67b6089e6d8dd`.
  - P11 `configs/run-p11-modelability.json`: moved from `355b6198…` to
    `b0388de84b47c3b8268001f78c20f4ff6405cd52c581f107256eb94cd1b006c9`,
    because `gate3_walks` gained the `gate1_cells` input and the `alpha`
    param and `gate3` gained the `draws` input. A moved identity is what a
    new stage input requires; the completed P11 artifacts under
    `pipeline_runs/p11-25-asset-modelability-staged-2026-02-28-355b6198/`
    are untouched and not reinterpreted.
  - P12 `configs/run-p12-modelability.json`:
    `1a2d194fa09533d44d47ef47d58840d3c89d7bcb9fb8574036d4109613e251ce`.
    Never run.
  - The smoke document `pipeline_runs/run-p12-smoke-orcl.json` (uncommitted,
    reproduced in the appendix):
    `dc07cc19f58cd241d043d757e2bfc96a0610f9d1fb7d8461b74695c397c2905f`.
- The one command that ran real data, from the child root of the worktree
  with `PYTHONPATH` at the worktree root and
  `INTRADAY_EQUITIES_FOLD_WORKERS=2`:

  ```bash
  python -m dskit.pipeline staged pipeline_runs/run-p12-smoke-orcl.json \
    --asof 2026-02-28 --adapter intraday_equities
  ```

  It completed all four stages (`memory`, `gate1`, `gate3_walks`, `gate3`)
  and exited 0 at 08:37:33Z after starting at 08:30:38Z. Its staged
  artifacts are under
  `pipeline_runs/p12-smoke-orcl-staged-2026-02-28-dc07cc19/stages/`.
- Deliberately unrun: the full P12 gate (about 2 hours expected, 6 hours
  worst case); the revised P11 run under the new identity; the group-E
  cache build, which the first real P12 run will perform and measure.

## Deliverable 1 — ADR-0092, the fail-fast Gate 3

Implementation evidence:

- `dskit/pipeline/attempts.py`: `beat_all(observed_r2, scrambled_r2)` is the
  one owner of the strict rank predicate, exported in `__all__`;
  `tier2_verdict` calls it and keeps its own two-null refusal and the
  calibration band. The existing `tier2_verdict` tests are untouched (the
  test file's diff is additions only, 68 lines).
- `children/intraday_equities/intraday_equities/modelability_p11.py`:
  `Gate3WalksStage` runs seeds 0..18 in order per survivor, scores each walk
  with `_score_one`, stops the asset at the first null with `r2oos >=`
  observed (`beat_all` false for that one draw), never stops a pass, and
  emits `draws = {asset: {stopped, stop_seed, n_draws}}` beside `walks` and
  `survivors`; it reads `gate1_cells` and refuses, before any walk, a
  survivor whose selected cell was never scored. `Gate3ResultStage` takes
  `draws` as a fourth input, refuses a missing or malformed stop record up
  front (including `n_draws` not equal to the stop seed's position plus
  one), emits for a stopped asset `gate3_status` `fail` with top-level
  `null_mean`/`null_sd` null, `calibration` `not_computed_early_stop`,
  `p_bound = 2/(n_draws+1)`, `stopped`, `stop_seed`, `n_draws` and no
  `gate3` block, and calls `tier2_verdict` over all 19 draws for a completed
  asset exactly as before.
- Config wiring, by surgical text edits: `gate3_walks` gained
  `"gate1_cells": "$gate1.cells"` and `"alpha": 0.05`; `gate3` gained
  `"draws": "$gate3_walks.draws"`; the document's stale Gate-2 notes were
  rewritten (notes are outside the identity).
- Mutation checks the brief demanded: flipping `beat_all`'s `>` to `>=`
  fails `TestBeatAll::test_a_tie_is_not_a_win`; making the walks stage
  ignore the stop fails three child tests. Both were re-run by every
  reviewer.
- Commits on `main`: `a28daaa` (round 1), `eead323`, `7f4704c`, `15358d1`,
  `69ec671` (rounds 2–5, all by Opus subagents).

## Deliverable 2 — ADR-0093, `BoundedFoldRunner`

Implementation evidence:

- `dskit/pipeline/folds.py` (new, tier 1, `__all__ = ["BoundedFoldRunner"]`,
  not re-exported from the package): `__init__(memory_limit_bytes,
  workers=None, env_var="DSKIT_FOLD_WORKERS")`; `run` returns
  `CompletedProcess` results in input order at the declared width, width 1
  being the serial path with no pool; `spawn(index, argv, cwd, env)` is the
  hook, raising `RuntimeError` with the output tail on a nonzero exit, which
  drops the unstarted folds; `measure_one` refuses a process whose
  `RUSAGE_CHILDREN.ru_maxrss` is already nonzero and runs its one command
  serially under the instance cap. The cap is validated in the parent
  (refused only when the hard `RLIMIT_AS` is finite and below it, and
  refused in the constructor above `sys.maxsize`), applied by a
  `setrlimit` + `os.execvp` shim in this interpreter — no shell, no
  `preexec_fn` — and never divided. `resource` is imported inside the
  methods that touch it.
- `dskit/pipeline/runs.py`: `single_fold_row(summary_dir, cutoff)` beside
  `walk_fold_dirs`, both over one `_walk_record` reader; `score_bar`,
  `single_fold_row` and `walk_cells` promoted to `__all__`.
- `dskit/pipeline/driver.py`: `FOLD_FIELDS`, `FOLD_OPTIONAL_FIELDS`,
  `aggregate_folds` (refuses a missing required key or any key outside the
  union) and `write_walkforward_summary`, all exported; `_run_folds` builds
  every row from the tuples; a test round-trips a written summary through
  `walk_fold_dirs`, `single_fold_row` and `aggregate_folds`.
- The child: `modelability.py` lost `_capped_command`, `_fold_workers`,
  `_one_bounded_fold` and its thread pool; `_run_bounded_walk` saves each
  single-fold document, hands only the not-yet-journaled folds to the seam
  (`_WalkRunner`, a `spawn` override that names the failing fold's derived
  config), and reads every row back through `single_fold_row`. The P10 and
  P11 preflights measure through `measure_one`, keep only their threshold
  and asset choice, and are named for the staged document's identity so a
  revised study measures afresh; the persisted per-fold `peak_rss_bytes`
  record is gone and a finished preflight walk is refused rather than
  reused.
- `dskit/pipeline/README.md` and `CLAUDE.md` trees carry `folds.py`
  (`CLAUDE.md` also `stages.py`) and an Extension-points bullet for `spawn`.
- Commits on `main`: `dd8b808` (round 1), `adfd483` (round 2, Opus),
  `cabae33` and `5c5a570` (ADR-0093 wording: `execvp`; an unsettable cap is
  a constructor error; `measure_one` takes the serial path).

## Deliverable 3 — ADR-0094, the forty-name study (P12)

Implementation evidence:

- ADR-0094 (`docs/architecture/decision-log.md`, last entry) was written
  first, reviewed three times (one Opus round, two Sonnet rounds; the
  round-one CRITICAL was that per-group `industry_*` one-hot columns would
  have given the two group caches and P11 three different design matrices),
  amended, and accepted before any code.
- `children/intraday_equities/intraday_equities/modelability_study.py`
  (new, 999 lines): the asset-local study over whatever cohort a document
  declares in its scan `fit_symbols`. `MemoryPreflightStage` reuses or
  builds one feature cache per source group at
  `<cache_dir>/<group>-<first 8 hex of the group universe's SHA-256>`, the
  first build measured through `measure_one`, later builds capped but
  unmeasured, an asset-fold measurement when nothing needs building, and
  pins every group universe digest. `Gate1Stage` refuses to run as of any
  date but its graded `data_cut`, places each asset in exactly one group,
  refuses a universe that moved since the memory stage, and runs the
  ordered horizon search with P11's stop rule and ledger key (literals now
  owned by the document). `Gate3WalksStage` and `Gate3ResultStage` are the
  ADR-0092 audit and verdict. The cohort, study name, caches, ledger key,
  walk tags, derived document and scorer are hook methods.
- `modelability_p11.py` is now the pinned special case: its four stages
  subclass the study's and supply the frozen 25, horizons and seeds, the
  P10 cache and its `p11-` tags; its document, identity and derived-document
  names are unchanged (verified by the round-one reviewer against the
  pre-refactor code). `modelability.py` (P10) exposes
  `_verify_cache_once` beside its membership pin.
- `dskit/pipeline/attempts.py`: `early_stop_p_bound(n_draws)` owns
  `2/(n_draws+1)`; every stopped row takes it from there.
- Configs: `run-p12-modelability.json` (study `p12-40-asset-modelability`;
  ORCL … TMUS then MSTR … EOG as `fit_symbols`; SPY the reference only;
  P11's scan, features and walk-forward sections verbatim, pinned by test;
  `data_cut` 2026-02-28 pinned to every source's declared cut),
  `universe-p12.json` and the two group universes (P10 geometry key for
  key, no industry block; both group caches carry the same 81 columns,
  P11's 90 less its nine constant one-hots, pinned by test).
- Tests (`tests/test_modelability_study.py`, 666 lines, plus edits to the
  P11 and config pins): the estimand — every declared asset's Gate-1 and
  Gate-3 walks fit and score `[asset]` alone over its own group cache, the
  group builds fit only the reference, no derived document carries more than
  one fit symbol; offline planning of the shipped document; the memory and
  gate contracts; refusals for the moved universe, the wrong `asof`, the
  double-placed asset, the malformed cache.
- Path (owner-authorized): `path.csv` row A2851 keeps every field with its
  label struck through; A2887 "Gate 3: fail-fast scramble refit" sits
  directly beneath it, LOCKED N, relevant files ADR-0092, ADR-0093, ADR-0094
  and the P12 document; the README was regenerated with
  `python -m dskit.journal render`.
- Commits: `e52776a`, `f3e7aa1`, `d5d5344`, `25871b7`, `86bea2b` (the ADR),
  `637c850` (the build), `3749c55` (the cache-verifier fix the first smoke
  attempt exposed), `cc84ea0`, `c7c968c` (journal and Path), `a5208ac`
  (two nits from the D2 review), `a8540e3` (round 2, Opus), `0bd0cef` and `f864545` (journal), `a8960c3` (round 3, Opus).

## Review rounds

| Deliverable | Round 1 code | Reviews (verdict) | Fix rounds | Final |
|---|---|---|---|---|
| ADR-0092 build | Fable | Opus: NOT CLEAN (MAJOR: per-asset `draws` routing unpinned); Sonnet ×4: NOT CLEAN (up-front refusal ordering unpinned; walks→result seam untested; `not_reached_reason` unpinned) then CLEAN | 4 (Opus) | CLEAN, 5 review rounds |
| ADR-0093 build | Fable | Opus: NOT CLEAN (MAJOR: P10 preflight name identity-blind under the new refusal); Sonnet: CLEAN | 1 (Opus) | CLEAN, 2 review rounds |
| ADR-0094 text | Fable | Opus: NOT CLEAN (CRITICAL: three design matrices; 7 MAJOR); Sonnet: NOT CLEAN (1 MAJOR, wording); Sonnet: CLEAN | 2 (Fable, see decisions) | CLEAN, 3 review rounds |
| ADR-0094 build | Fable | Opus: NOT CLEAN (MAJOR: the tradable refusal unbuilt; P11 preflight not a subclass); Sonnet: NOT CLEAN (MAJOR: preflight row check and one-fold shape unpinned); Sonnet: CLEAN | 2 (Opus) | CLEAN, 3 review rounds |

Every reviewer ran the suites and planted mutants; every mutant on named
behaviour was killed by the end of each loop. In every code round the
production code was found correct; the findings were test-pinning gaps,
standards nits and one design asymmetry (P10's preflight name).

## The smoke, measured

Journal rows A2858–A2908 in `docs/decisioning/actions.csv`; the timing record
is A2886.

- First attempt (A2858, A2859, 08:24:52Z): the group-D cache was built and
  then refused by the study's own verifier, because the universe node
  patches the derived `features` list into the spec it emits and the
  verifier compared the whole spec. Fixed in `3749c55` (raw universe keys
  against the emitted spec); the attempt's cache and run directories were
  removed and rebuilt. The rows stay, as the ledger is append-only.
- Second attempt (A2860–A2885): `memory` measured the group-D cache build
  (21 symbols, 81 columns, 3,976,098-row-order frames) at
  `peak_rss_bytes = 14,898,974,720`, then `gate1` ran ORCL's one-minute
  walk, which failed; `gate3_walks` and `gate3` ran with zero survivors.
- Per-fold time, from each fold run's `run.log` (first to last stamped
  line), 18 parseable of the 20 Gate-1 folds: median 2.10 s, mean 2.16 s,
  min 1.97 s, max 2.51 s, two folds in flight at a time.
- The 20-fold Gate-1 walk took 37.0 s wall from its first fold's journal row
  to the walk's own row.
- One real null draw, run afterwards through the same builder and seam
  (A2888–A2908): `p12-smoke-orcl-gate3-seed00-orcl-h01`, 20 folds, median
  1.97 s per fold; observed ORCL `r2oos` −3.35e-05 (`t_pool` 0.111, fails
  Gate 1), null `r2oos` −2.81e-04 (`t_pool` −0.686), `beat_all` true. This
  proves the asset-local scramble walk runs on the group cache; it is not a
  Gate-3 decision, since ORCL never entered Gate 3.

Math:

- Memory headroom. `peak / cap = 14,898,974,720 / 18,253,611,008 = 0.816`,
  so the build used 81.6% of the cap and left 3,354,636,288 bytes
  (3.12 GiB). ADR-0094's proxy predicted about 0.68 × P10's 15.90 GiB
  = 10.8 GiB; the measured 13.88 GiB is 28% above that, so the proxy
  under-predicts and the group-E build (0.66 × by the same proxy) should
  be expected near 13.5 GiB, still under the cap but with less headroom
  than the ADR implied. The single 41-name build the ADR ruled out would,
  by the same ratio, sit near 1.34 × 15.90 × 1.28 ≈ 27 GiB.
- Per-fold speed against the baseline. `2.10 / 3.40 = 0.62`: an asset-local
  fold over the 21-name group cache ran in 62% of P11's per-fold time on
  the 25-name cache. This is a per-fold duration, not a throughput figure;
  at width 2 the 20-fold walk's 37.0 s is 1.85 s of wall per fold.
- Cost projection for the full P12 gate at the measured rate. Gate 1 worst
  case `40 × 8 × 20 × 2.16 / 2 / 3600 ≈ 1.9 h` wall at width 2; P11's stop
  pattern (66 cells for 25 names, 2.64 per name) gives `40 × 2.64 × 20 ×
  2.16 / 2 / 3600 ≈ 0.6 h`. Gate 3 under ADR-0092: about 2.73 draws per
  failing passer and 19 per survivor. Plus the group-E build, about 6
  minutes.

## Decisions taken without the owner

1. **The cohort is D and E together (forty names), not D alone.** The brief
   said "about 40 new stocks landed in commit bdf8321"; that commit holds
   cohort D's twenty, and the next data commit (`5dd29d6`) holds cohort E's
   twenty on identical terms. Both were on disk and neither had been gated.
   Reviewers judged the reading defensible.
2. **ADR-0094's round-two and round-three text edits were made by me, not
   by an Opus subagent.** The owner's process names Opus for round-2 code.
   The edits were design decisions only I could make coherently, the
   owner had cut subagents to one at a time, and a usage limit was hit
   mid-session. The reviews stayed independent (fresh Sonnet rounds).
3. **`Gate3WalksStage` (P11) gained an `alpha` param** because `_score_one`
   needs a level; the config test pins the three stage alphas equal.
4. **P11's document notes were rewritten** (they still described a Gate-2
   study); notes are outside the identity.
5. **The P11 and P10 preflight walks are named for the staged document's
   identity**, because ADR-0093 drops the persisted per-fold reading and a
   measurement needs a fresh spawn; a finished preflight walk is refused,
   not reused.
6. **The shim uses `os.execvp`** (ADR-0093 said `execv`) so `argv[0]`
   resolves through `PATH` capped and uncapped alike; the ADR text was
   amended to say so.
7. **The child keeps `_WORKERS_ENV`** as the one owner of its knob name,
   passed to the seam as `env_var`; the mechanism behind it is gone.
8. **P10's `_feature_cache_info` was split** to expose
   `_verify_cache_once`; ADR-0094 names this as P10's one edit.
9. **The P12 universes carry no industry block** (ADR-0094 §3); the labels
   stay in the two research notes.
10. **`data_cut` is a graded Gate-1 param** pinned to the sources' cut and
    refused for any other `--asof`; **the stop bound graduated** into
    `attempts.early_stop_p_bound`; **group cache paths carry the universe
    digest** and the gates refuse a universe that moved.
11. **The reference symbol is fit alone inside each group cache build**
    (a one-symbol SPY fit whose result is discarded); it is the walk that
    gives the build a scoreable objective. It is not a cohort fit and not
    pooled; the estimand test pins it.
12. **The five names with unreconciled distributions (MRK, MET, WDC, EOG,
    PANW) stay in the cohort**; the unadjusted `alpaca-sip` tree holds
    only the original six names, so the owed check cannot be done from
    what is on disk. Their future rows are provisional until it is.
13. **The smoke document lives under the ignored `pipeline_runs/`**, not in
    `configs/` (an extra config would have tripped the config pins and the
    owner asked for no unrequested files); it is reproduced in full below.
14. **The smoke ran from the D3 worktree** with `ob`, `pipeline_cache`,
    `pipeline_runs` and `mlruns.db` symlinked to the main checkout, so its
    artifacts live under `~/dskit/children/intraday_equities/` but the
    journal rows record the worktree paths
    (`/home/russell/wt/adr-0094/…`). The worktree is deleted at wrap; the
    artifacts are not.
15. **The first smoke attempt's cache and run directories were deleted**
    (my own failed attempt, minutes old) so the relaunch would measure the
    build; the journal rows A2858/A2859 remain.
16. **One real null draw was run outside the staged path** as evidence
    (decision 11 above and the smoke section), since ORCL never reached
    Gate 3.
17. **`_run_bounded_walk` and `_stopped_row` docstrings were collapsed to
    one line**, and `driver.__all__` re-sorted, on reviewer nits.
18. **Two of the three pre-existing failures the brief named did not
    reproduce**: only the `run-pb-s01-h01-lgbm-cross.json` start pin fails
    here; the `no-information-scan` conformance ImportError and the
    root-only chmod test passed in every run (the machine is not root).
    They were never masked.
19. **One equivalent mutant survived** in my own D3 mutation pass
    (`ledger_key` reading `ctx.asof` instead of the `data_cut` param):
    `check_asof` already forces the two equal before the key is built, so
    no test can tell them apart; it is reported, not hidden.

## Verification

- Final suites on the merged tree: child suite 341 passed, 11 skipped, 1 failed (the pre-existing start pin); tests/pipeline 1919 passed, 25 skipped. The one failure is the
  pre-existing, unrelated start pin named in the brief.
- Ruff clean on every changed file; `tests/pipeline/test_purity.py` and
  `test_method_lengths.py` pass (folds.py is stdlib-only; driver.py has no
  new annotation and no function over 100 lines).
- Not verified: the full P12 gate; the group-E cache build and its peak;
  P11 under its new identity; any Gate-3 decision on real data (the null
  draw above was scored but decided nothing).

## Reproducibility and handoff

- Rerun the smoke: from `children/intraday_equities` with the study on
  `main`, `INTRADAY_EQUITIES_FOLD_WORKERS=2 python -m dskit.pipeline staged
  <the appendix document> --asof 2026-02-28 --adapter intraday_equities`;
  the group-D cache at `pipeline_cache/p12-features-f32-v5/d-c1ed493d` is
  reused after verification, so the memory stage then measures ORCL's
  one-fold walk instead of a build.
- Run the study: `INTRADAY_EQUITIES_FOLD_WORKERS=<width> python -m
  dskit.pipeline staged configs/run-p12-modelability.json --asof
  2026-02-28 --adapter intraday_equities`; the memory stage will build and
  measure the group-E cache first (group D is reused). Expect about 6
  minutes for that build and about 0.6–1.9 hours for Gate 1 at width 2.
- Next owner actions: decide whether to run P12 now or after reconciling
  the five flagged names; decide whether to launch the revised P11 run.

## Appendix — the smoke document, verbatim

```json
{
  "name": "p12-smoke-orcl",
  "notes": "SMOKE (ORCL, group d only): the shipped P12 document with fit_symbols and groups narrowed to prove the wiring end to end and time a fold; it builds and leaves the real group cache the study reuses. The original notes follow. P12: the forty breadth-cohort names (D and E) through the asset-local modelability study on P11's exact geometry (ADR-0094): memory preflight and per-group feature caches, Gate 1's ordered horizon search per name, then ADR-0092's fail-fast whole-session scramble audit on every passer. The cohort is fit_symbols; universe-p12.json bounds the read and each group cache is built through its own group universe. The source-level cut is 2026-02-28. SPY is the residual reference only; META and GROUP are excluded.",
  "pipeline": {
    "universe": {
      "uses": "intraday_equities-universe",
      "params": {"path": "configs/universe-p12.json"}
    },
    "source_reference": {
      "uses": "intraday_equities-bars",
      "params": {
        "root": "./ob",
        "source": "alpaca-sip-split",
        "universe": "configs/universe-p12.json",
        "start_ms": 1514764800000,
        "sessions": ["rth"],
        "shared_fields": ["symbol", "ts"]
      },
      "notes": "The reference tape only: alpaca-sip-split holds the P10 names, and the universe bound keeps SPY alone from it."
    },
    "source_d": {
      "uses": "intraday_equities-bars",
      "params": {
        "root": "./ob",
        "source": "alpaca-sip-split-d",
        "universe": "configs/universe-p12.json",
        "start_ms": 1514764800000,
        "sessions": ["rth"],
        "shared_fields": ["symbol", "ts"]
      }
    },
    "source_e": {
      "uses": "intraday_equities-bars",
      "params": {
        "root": "./ob",
        "source": "alpaca-sip-split-e",
        "universe": "configs/universe-p12.json",
        "start_ms": 1514764800000,
        "sessions": ["rth"],
        "shared_fields": ["symbol", "ts"]
      }
    },
    "pooled": {
      "uses": "concat",
      "inputs": {
        "reference": "$source_reference.records",
        "d": "$source_d.records",
        "e": "$source_e.records"
      },
      "params": {
        "shape": "records",
        "provenance_waiver": "Each immutable MarketRecord already carries its source; the three symbol namespaces are disjoint and checked below.",
        "key": "symbol",
        "consume_inputs": true
      },
      "notes": "The top-level pipeline is the template the stages derive from and is never run whole: the memory stage builds one cache per group from that group's sources alone (ADR-0094)."
    },
    "features": {
      "uses": "intraday_equities-session-features",
      "inputs": {
        "records": "$pooled.merged",
        "spec": "$universe.spec"
      },
      "params": {
        "lookback": 20,
        "layout": "columns",
        "dtype": "float32",
        "cache_dir": "./pipeline_cache/p12-features-f32-v5",
        "momentum_horizons": [
          {"width": 3, "tag": "3m", "cross_session": false},
          {"width": 120, "tag": "2h", "cross_session": true},
          {"width": 180, "tag": "3h", "cross_session": true},
          {"width": 780, "tag": "2s", "cross_session": true},
          {"width": 1950, "tag": "1w", "cross_session": true}
        ]
      }
    },
    "scan": {
      "uses": "intraday_equities-no-information-scan",
      "inputs": {
        "records": "$features.records",
        "bars": "$features.tape",
        "spec": "$universe.spec"
      },
      "params": {
        "split": "val",
        "train_end_ms": "$splits.train_end_ms",
        "train_start_ms": "$splits.train_start_ms",
        "val_start_ms": "$splits.val_start_ms",
        "val_end_ms": "$splits.val_end_ms",
        "label_scale": "vol",
        "label_residual": "SPY",
        "label_residual_self": "raw",
        "fit_symbols": ["ORCL"],
        "lead_start": 1,
        "lead_step": 1,
        "lead_stop": 1,
        "score_period_ms": 1800000,
        "estimator": "lightgbm.LGBMRegressor",
        "estimator_params": {
          "n_estimators": 800,
          "learning_rate": 0.005,
          "num_leaves": 4,
          "max_depth": 2,
          "min_child_samples": 2000,
          "reg_lambda": 100.0,
          "reg_alpha": 0.1,
          "subsample": 0.5,
          "subsample_freq": 1,
          "colsample_bytree": 0.3,
          "n_jobs": 8,
          "random_state": 0,
          "verbosity": -1
        }
      }
    }
  },
  "walkforward": {
    "objective": "$scan.metrics.val_ic",
    "select": "max",
    "first": "2022-05-06",
    "step_days": 63,
    "count": 20,
    "val_days": 63,
    "embargo_days": 5,
    "train_days": 730
  },
  "tracking": {
    "sinks": [
      {
        "kind": "dskit.pipeline.libs.mlflow:MlflowTracker",
        "params": {
          "tracking_uri": "sqlite:///mlruns.db",
          "experiment": "intraday_equities"
        },
        "notes": "Local sqlite beside the invoking cwd."
      }
    ],
    "notes": "Excluded from identity - where metrics land is not what it computes."
  },
  "outputs": {"run_root": "./pipeline_runs"},
  "stages": {
    "memory": {
      "uses": "intraday_equities.modelability_study:MemoryPreflightStage",
      "params": {
        "memory_limit_bytes": 18253611008,
        "groups": {
          "d": {"universe": "configs/universe-p12-d.json", "sources": ["source_reference", "source_d"]}
        }
      },
      "notes": "One feature cache per group under the 17 GiB cap, groups declared largest-first: taking compressed bars on disk as a proxy for records, a single 41-name build would carry about 1.34x the P10 records that peaked at 15.90 GiB, while one group plus SPY carries about 0.68x (D) or 0.66x (E). The first build is measured through the seam's measure_one; a later build is capped but unmeasured (ADR-0093 allows one reading per process). Each group cache path is suffixed with its universe file's digest (ADR-0094)."
    },
    "gate1": {
      "uses": "intraday_equities.modelability_study:Gate1Stage",
      "inputs": {"preflight": "$memory.passed", "caches": "$memory.groups"},
      "params": {
        "horizons": [1, 2, 3, 5, 10, 20, 30, 60],
        "attempt_registry": "docs/decisioning/attempts.jsonl",
        "alpha": 0.05,
        "architecture": "lgbm-tight-asset-local",
        "data_cut": "2026-02-28"
      },
      "notes": "data_cut is the sources' end date, pinned by test to every source this document reads; the stage refuses to run as of any other date so the attempt ledger never records an invocation's --asof as the data's cut (ADR-0094)."
    },
    "gate3_walks": {
      "uses": "intraday_equities.modelability_study:Gate3WalksStage",
      "inputs": {
        "gate1": "$gate1.rows",
        "gate1_cells": "$gate1.cells",
        "caches": "$memory.groups"
      },
      "params": {
        "seeds": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "alpha": 0.05
      }
    },
    "gate3": {
      "uses": "intraday_equities.modelability_study:Gate3ResultStage",
      "inputs": {
        "gate1": "$gate1.rows",
        "gate1_cells": "$gate1.cells",
        "walks": "$gate3_walks.walks",
        "draws": "$gate3_walks.draws"
      },
      "params": {
        "seeds": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "alpha": 0.05
      }
    }
  }
}
```

