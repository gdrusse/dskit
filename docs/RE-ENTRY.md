# Re-entry

## Current state: P15 temporal-fusion zoo complete (2026-09-06)

The three-candidate P15 run completed 20/20 paired outer folds per model under
benchmark `5e88726b…`. Mean path scores were Ridge 0.001346, Transformer
0.000835, and TCN 0.000409. No pairwise test rejected equal performance at
the 0.016667 adjusted threshold; all three positive means depended on the
same 2023-05-19 fold and became approximately zero or negative without it.
Ridge is the simplest P15 frontier, but no model was promoted or refit.

The nine-model P13/P14/P15 reference leaves P13 pooled native LightGBM as the
practical development frontier. Cross-zoo values are descriptive, not paired
tests. Memo:
`children/intraday_equities/docs/memos/p15-temporal-fusion-model-zoo-results.md`.

**Next:** treat the temporal zoo as a completed negative complexity ablation.
Only spend on another sequence family after a sharper representation or loss
hypothesis; keep promotion/final refit as a separate owner-approved action.

## Current state: cross-benchmark model selector landed (2026-09-06)

`BenchmarkSelect` (ADR-0106, accepted) joins completed benchmark zoos' pinned
`compare.json` artifacts and names one winner by a config-declared
`decision_metric` + `select` direction — never promotes (`auto_promote` False).
Shipped in `dskit/pipeline/benchmarks.py` with `is_sha256hex` (single owner of
the lowercase-64 SHA-256 rule, `dskit/pipeline/stages.py`); 27 tests, ruff clean,
skeptic loop closed with a clean round-3 pass. Child config
`configs/run-model-select.json` now chains P13+P14+P15, ranks all nine
candidates, and selects `lgbm-pooled-h10`. The completed selector artifact is
`pipeline_runs/model-select-staged-2026-02-28-ef2e8f37/stages/select.json`
(SHA-256 `df474f64…`). This is descriptive ranking only, not a cross-zoo
significance claim, final refit, or promotion.

## Current state: P14 recurrent-fusion model zoo complete (2026-09-06)

The corrected two-candidate P14 run completed 20 paired outer folds per model
under benchmark identity `70b5a399…` and approved inventory `85e1fb8c…`.
Mean path scores were LSTM 0.001174 and GRU -0.000417. GRU minus LSTM was
-0.001591 (`p=0.109186`), so the comparison selected the simpler LSTM frontier
without detecting a reliable difference. LSTM's mean excluding its best fold
was -0.000498; neither model was promoted or refit.

The initial run exposed sparse no-trade minutes: strict 120-minute continuity
left MSTR without fold-four path evidence. ADR-0104 and the config now state a
causal bounded fill—carry the last close into OHLC and zero volume for gaps up
to five minutes, while refusing longer gaps and session boundaries. Corrected
MSTR fold-four coverage is 198 origins and both models finished 20/20 folds.
Scoped verification passed (253 tests, 21 skips), Ruff/config validation and
the diff check are clean, and final Major/Critical review is clear. Memo:
`children/intraday_equities/docs/memos/p14-recurrent-fusion-model-zoo-results.md`.

**Next:** retain pooled native LightGBM from P13 as the practical development
frontier. Do not spend on a joint six-candidate rerun or promote a recurrent
model unless a sharper sequence hypothesis is approved first.

## Current state: P13 pooled/Kronos model zoo complete (2026-09-06)

The four owner-approved candidates completed 20 outer folds each under
benchmark identity `b017ea1b…` and approved inventory `8731619d…`. Mean path
scores ranked pooled LightGBM 0.006401, pooled Torch MLP 0.005322, frozen
Kronos+LightGBM 0.001175, and frozen Kronos+MLP -0.003852. LightGBM and the
tabular MLP were not detectably different (`p=0.382351`); both Kronos variants
were detectably worse than both tabular baselines after all-pairs Bonferroni.
The comparison selected the simpler native LightGBM frontier but made no
automatic promotion.

ADR-0103 and its implementation add the generic verified local Kronos hidden
state node/cache to dskit, expose the optional runtime through pmquant, add
causal OHLCVA cache rows and P13 fusion, and allow Torch MLP width/depth HPO.
The pmquant ladder model itself is unchanged. Final Bugbot review is clear;
the real pinned 128-session GPU smoke passes. P13 D/E cache membership was
narrowed to approved cohort names plus SPY after one WSL OOM on unused P12
breadth names. The final run took about 7h25m; journal evidence is A18704-
A18709. Memo:
`children/intraday_equities/docs/memos/p13-pooled-kronos-model-zoo-results.md`.

**Next:** treat pooled native LightGBM as the current simplest development
frontier. If Kronos receives another exploratory attempt, first test an
approved additive ablation against the full nonduplicative P12 feature set;
do not fine-tune or promote while the cheaper frozen representation shows no
incremental value and pretraining-contamination certainty remains low.

## Current wrap: dskit.production BUILT — phases 1, 2, 2b and 3 (2026-09-06)

Branch: `claude/dskit-production-build-3g17vw`. ADR-0090 and ADR-0091 are
**accepted**; the package is complete against
`docs/new_package_proposals/production.md`, which is the contract.

**What it is.** The serving layer: an immutable release of a finished pipeline
run, driven forward on a cadence — fetch, decide, guard, act, record. Every
tick writes one decision into a hash-chained append-only ledger; every proposal
passes a declared guard chain before anything is sent. The four rungs differ
only by which objects were injected, and reaching a live venue additionally
needs a recorded, expiring, independently authenticated maker-checker arm bound
to the release hash. A child ships the venue executor, its accounting, its
approval verifier and its fenced lease; dskit ships everything else.

**What phase 2 added**: the series can score its own decisions. `outcomes`
records what happened to each leg bitemporally; `report` gives attribution,
calibration and a value curve at an explicit cut; `replay` re-runs the tape and
diffs it. Plus the outcome and parity monitor families, four statistical
monitors, a sqlite chain, a request signer, alert inhibition/silences/
escalation/ack, the systemd heartbeat, readiness evidence drawn from the
outcome fold, and durable guard holds. **Phase 3**: the exchange-calendar pack,
the metric-sink seam with prometheus and opentelemetry exporters, and the
websocket stream seam.

**State.** 7973 passed over production, production_libs and pipeline; the full
suite is 9560 passed with three failures that are all pre-existing on `main`
(two uid-0 environment cases, one child's own config assertion). ruff clean,
five purity gates at 44, the 20 pinned sha256 literals unmoved, `check_plan.py`
CLEAN, and the pinned driver and search suites passing untouched.

**Open, needing the owner.** ADR-0101 (proposed) holds the last phase-3 item:
moving the onboarding connector packs onto `resilience.Retry`. It was NOT
migrated, deliberately — the record names three obstacles the draft did not,
two of which need a ruling, and the onboarding purity gate is a hard stop
rather than something to adjust. Also open: whether `kinds_banking`'s newly
pure classes and `TorchImportance` were the right calls (both flagged), and
`authority.expire` plus `cash_flow.supersedes` remain folded but unproduced.

**How to run it.** `python -m dskit.production validate|plan|ready|serve|
status|verify` for the loop; `outcomes|report|replay` to score it, all
read-only bar `outcomes`; the authenticated verbs are `arm-request|approve-arm|
disarm|halt|reduce|resume|flatten-request|approve-flatten|execute-flatten|
adopt|ack|silence|approve-hold`. See `dskit/production/README.md`.

## Current state: P12 Gate 3 recovery complete (2026-09-05)

Branch: `main`; `571884a` is pushed to `origin/main`. Focused recovery,
staged-run, and concurrent-journal verification: 142 passed. P12 recovered
without changing any original partial artifact.
The only Gate 1 selection source was persisted `gate1.json` rows with boolean
`gate1_passes=true`: 31 survivors. The final Gate 3 result is 25 pass / 6
fail. Pass: LLY3, QQQ1, XLF3, XLE1, XLK5, BAC1, SMH5, IWM5, XBI1, FCX1,
DAL1, NRG1, MET1, MSTR10, NOW5, LULU5, PANW5, INTC1, CIEN5, LRCX10,
TER5, BIDU2, LITE5, ADBE5, ANET3. Fail: TQQQ3, NVDA2, UPRO60, AVGO10,
NFLX3, BA1; all six beat all 19 draws but failed shipped calibration.

A12580 is the immutable source inventory. A12581-A12596 separately record
the 16 legitimate reconstructions with all 19 draw and 380 part action/path
references each. The remaining 15 families were rerun completely. A18619 is
the final `gate3_recovery.json`; its SHA-256 is
`098b21eaef6ee0260753d4f981ca2337bccae406b9efd394284d9b180ba03bd0`.
It contains all 63 Gate 1 rows, the exact 16/15 partition, 285 rerun main
walks and 5,700 matching part journals. NRG seed05 part00 replacement A12731
passes the fixed journal seam. Recovery summary: A18622.

The missing-evidence fix is `cd4fb1c`; continuation construction is
`d1230ba`; the safe journal-label bound fix is `47bcdaa`. Independent review
is clean after all major findings were resolved. A12618 preserves the failed
long-label continuation attempt. A18620 appends corrected locations for smoke
rows A2888-A2909; A18621 records the first 64-asset attempt's exit 143. Memo:
`children/intraday_equities/docs/memos/p12-gate3-recovery-results.md`.

**Next:** use the Gate 3 survivors as the fixed input to the ratified
predictor-output/model-development plan. For future long runs on this 16-CPU
host, set `INTRADAY_EQUITIES_FOLD_WORKERS=4`; benchmark before going wider.

## Current state: predictor-output research + topic folders (2026-09-05)

Branch: `main`. ADR-0096: `journal research` writes
`docs/research/<topic>/<YYYY-MM-DD>-<name>.md` (no root markdown). Skills
copied to Cursor + Claude; OpenCode `/research` updated. Deep-research
finding for post-Gate-3 output:
`children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md`
(A12628–A12635). Journal/skeleton tests 38 passed. P12 Gate-3 recovery
execute rows continued appending in the same ledger.

**Next (owner):** ratify or amend the synthesis (pooled trunk + \(H_i\)
heads, 5-seed ensembles, quantiles+conformal, empirical robust sets;
large transformers as challengers only). P12 recovery still in flight
elsewhere — do not treat this wrap as a Gate-3 result.
## Current state: agent-doc sync + opencode setup (2026-09-05)

Branch: `main`. Synced all nine `AGENTS.md`/`CLAUDE.md` pairs so the Codex-
facing copies carry the latest rulings (the "prefer objects" ruling,
ADR-0077/0079/0093, connector shapes, the intraday "Machine knobs" section),
and added the commit-author standard. Added opencode support: `opencode.json`
(registers `.cursor/skills` + the session-pull plugin), `.opencode/command/
{wrap,research}.md`, and `.opencode/plugin/session-pull.ts`. No source code
changed; no tests run.

Next: restart opencode to load the config, skills, commands, and plugin.

## Current state: P12 Gate 3 failed; closeout blocked (2026-09-05)

Branch: main; the completed P10/P11, Gate 3, bounded-fold, and cohort-study
work is committed, merged, and pushed (main equals origin/main). Their
historical wraps remain below. No local branch from that work is unmerged.

The 63-asset P12 study exited 1 at 16:09:05Z in Gate 3 walks. Memory and
Gate 1 artifacts are present, but Gate 3 produced no final result. Journal
row A12579 records the error: its seed-05 NRG h=1 part-00 fold finished
without journal evidence. Do not infer or publish final Gate 3 verdicts from
the partial artifacts.

Next: diagnose and repair the missing journal-evidence failure, then resume or
rerun P12. After a successful result, repair the smoke journal rows A2888-A2909,
record the prior 64-asset attempt's exit 143, write the result memo, and wrap.
Do not commit or push the failed P12 run as complete.

## P12 recovery handoff — required sequence

This wrap records the failure and recovery plan only; it does not claim a P12
result. The next session must preserve the partial artifacts, build an
asset-and-seed inventory from the persisted Gate 1 selection and Gate 3
reports/actions, and extract a final outcome only for an auditable complete
19-draw family. Every other survivor must be rerun.

Verification for this documentation-and-evidence wrap: `git diff --check`;
no test suite was run because no source code changed.

First reproduce and fix NRG h=1 seed-05 part-00's missing journal evidence
with a focused test. Obtain independent major/critical review and resolve its
findings before execution. Then append a provenance-rich recovery journal
entry plus one separate, source-linked entry for each legitimately
reconstructed stock-and-horizon verdict. Create the continuation, run
the remaining work, write a result memo, wrap, commit, and push main.

## Prior wrap: Gate 3 rebuild built (2026-09-05, overnight, autonomous)

Branch: `main`. Everything committed and pushed; the three feature branches
and their worktrees are purged. `claude/dskit-production-build-3g17vw` and
`claude/gate-3-null-design-docs-9me2mk` are other sessions' remote branches
and were left alone.

Landed, each looped through hostile review until no CRITICAL or MAJOR
remained (rounds: ADR-0092 build 5, ADR-0093 build 2, ADR-0094 text 3,
ADR-0094 build 3):

- **ADR-0092 built.** `attempts.beat_all` owns the strict rank predicate;
  P11's `Gate3WalksStage` stops an asset at the first null that matches or
  beats it and emits `draws`; `Gate3ResultStage` takes `draws`, and a
  stopped asset carries `p_bound = 2/(n_draws+1)` top-level with no
  `gate3` block. P11's identity moved `355b6198 → b0388de8` (new stage
  input/output); its completed artifacts are untouched.
- **ADR-0093 built.** `dskit/pipeline/folds.py::BoundedFoldRunner` (cap in
  the parent, `setrlimit` + `execvp` shim, width from the environment,
  `measure_one` as the one memory reading), `runs.single_fold_row`,
  `driver.FOLD_FIELDS`/`aggregate_folds`/`write_walkforward_summary`; the
  child's own pool, cap wrapper and persisted per-fold peak are gone.
- **ADR-0094 written, looped clean, accepted, built.** `modelability_study.py`
  is the asset-local study over a document-declared cohort with one feature
  cache per source group; P11 is now its pinned subclass; `attempts.
  early_stop_p_bound` owns the stop bound. P12 (`configs/run-p12-
  modelability.json`, identity `1a2d194f…`, forty names of cohorts D and E,
  P11 geometry verbatim) had NOT yet run at the time of this wrap. Path: A2851 struck through, A2887
  "Gate 3: fail-fast scramble refit" beneath it.

Smoke (one asset, ORCL, `INTRADAY_EQUITIES_FOLD_WORKERS=2`): the whole
staged document ran in 6 min 55 s; group-D cache build measured 13.88 GiB
under the 17 GiB cap; median 2.10 s per fold against the 3.40 s baseline
(journal A2886); ORCL failed Gate 1 at h=1, so no null draw ran inside the
staged run — one was run separately as evidence (A2888–A2908). Memo:
`children/intraday_equities/docs/memos/gate3-rebuild-fail-fast-fold-seam-and-p12.md`.

Verification on the merged tree: child suite 341 passed, 11 skipped, 1 failed (the pre-existing start pin); tests/pipeline 1919 passed, 25 skipped. The one failure is the
pre-existing `run-pb-s01-h01-lgbm-cross.json` start pin; the other two
failures the brief named (the no-information-scan conformance ImportError,
the root-only chmod test) did not reproduce on this machine.

**Next step (owner):** decide whether to run P12 now
(`INTRADAY_EQUITIES_FOLD_WORKERS=<w> python -m dskit.pipeline staged
configs/run-p12-modelability.json --asof 2026-02-28 --adapter
intraday_equities`; the group-E cache builds and is measured first, ~6 min;
Gate 1 ~0.6–1.9 h at width 2) or first reconcile the five names the
selection notes flag (MRK, MET, WDC, EOG, PANW); and whether to launch the
revised P11 run under its new identity. Every judgement call taken
overnight is listed in the memo's "Decisions taken without the owner".

## Prior wrap: Gate 3 redesign (2026-09-05)

Branch: `main`. Everything committed and pushed; merged feature branches
purged. `claude/dskit-production-build-3g17vw` is another session's live
work and was left alone.

Landed: the Gate 3 null-design research doc, reviewed four rounds and merged
with an independent revision that was already on `main`. Two ADRs, both
skeptic-cleared and both still **proposed, awaiting owner approval**:

- **ADR-0092** — Gate 3 stops at the first null exceedance (Besag–Clifford,
  `h=1`, `B_max=19`). Same beat-all verdict; `E[draws | fail] = 2.73`, so
  twelve failures and one passer cost ~52 walks against 247. Requires
  extracting `beat_all` into `dskit/pipeline/attempts.py` and a `draws`
  output on `Gate3WalksStage`. Calibration stays per-asset on completed
  families.
- **ADR-0093** — bounded parallel fold execution graduates from the child into
  `dskit/pipeline/folds.py` as `BoundedFoldRunner` (seven rounds). Cap is
  caller-supplied and never divided; `setrlimit`+`execv` shim, cap validated
  in the parent with an `RLIM_INFINITY` guard; width from the environment;
  `measure_one` owns the `RUSAGE_CHILDREN` contamination guard;
  `single_fold_row`, `FOLD_FIELDS`/`FOLD_OPTIONAL_FIELDS`, and the driver
  renames pin the fold-row shape.

Shipped ahead of ADR-0093 (recorded there as the violation it is): the tape
filter and a child-side fold pool in `modelability.py`. Measured on the
recorded run: 3.40 s/fold, so today's gate is ~4.7 h; the tape filter is
3.9% of a fold; concurrency is unmeasured. The pooled-null idea was rejected
— `t_pool` is not location-pivotal (LLY's null centre is −0.37).

Verification: child 278 passed; `tests/pipeline` 1,733 passed. Three
pre-existing unrelated failures: the `run-pb-s01-h01-lgbm-cross.json` start
pin, the `no-information-scan` conformance ImportError, and a root-only
chmod test.

**Next step (owner):** approve or amend ADR-0092/0093, then build under the
TDD + skeptic loop. Nothing has been built. A new cohort (~40 stocks) wired
through Gate 1 → Gate 3 needs its own ADR first: P11 pins `_ASSETS` to
exactly 25.

## Prior wrap: child infrastructure

Branch: `main`.

Landed: the Path schema now records label, purpose, relevant files, `LOCKED`,
and owner-only Current Work. Generated decisioning README shows the full Path
and the latest 10 Actions without deleting CSV history. The skeleton now
initializes decisioning, explanations, memos, and research with skill
reminders. `refresh-child-infra` and paired AGENTS/CLAUDE edit reminders are
available.

Verification: 38 focused journal/skeleton tests passed; final Bugbot found no
bugs. Legacy two-column Path ledgers render read-only and refuse promotion
until the human owner explicitly migrates them.

Next step: apply `refresh-child-infra` to a chosen child when authorized.

**Current policy:** Gate 1 selects provisional modelability candidates. Gate 3
is their mandatory 19-seed whole-session refit audit. There is no Gate-2
filter; HFDR belongs later in MIO (ADR-0089).

Prior P11 wrap: 2026-09-04 on `main`. ADR-0089's direct Gate-1-to-Gate-3
correction is implemented. The revised P11 run has not started.

Verification: 77 targeted P11/config/attempt tests passed; Ruff and diff checks
are clean. One known pre-existing config-pin test still rejects the 2020 start
in `run-pb-s01-h01-lgbm-cross.json` against 2018. The full suite was not
rerun.

The prior Gate-2-only P11 run is historical evidence from a mistaken
configuration. This wrap also includes the reusable `memo` skill and P11
execution memo.

## Historical P11 record (superseded)

ADR-0087 is accepted. P11 trains one model per asset, stops the ordered
`h=1,2,3,5,10,20,30,60` search at the first Gate-1 failure, and confirms only
the selected horizon on untouched 2025-12-02 through 2026-02-28 observations.
The generic fixed-family ledger reserves all 25 Bonferroni slots at alpha
0.05 (0.002 each), valid under arbitrary dependence and arrival order.

Gate 1 selected 13 assets: LLY h3, QQQ h1, XLF h3, XLE h1, XLK h5, TQQQ h3,
NVDA h2, UPRO h60, BAC h1, AVGO h10, NFLX h3, SMH h5, and IWM h5. All 13
failed Gate 2; UPRO was closest (raw p=0.0132419, adjusted p=0.331047). The
other 12 assets failed Gate 1 at h1 and never entered confirmation. Full rows
and decision math are in `children/intraday_equities/docs/memos/` plus the P11
staged artifacts and append-only decision ledgers.

Next step: run revised P11 through memory, Gate 1, Gate-3 walks and Gate-3
result. Do not run Gate 2. Then design the predictive `pi_i` model and HFDR
MIO seam under a separately approved ADR.

## Landed this wrap: ADR-0082…0086

- Hugging Face repositories enter as WORM acquisitions; pretrained encode,
  classify and forecast nodes load only verified, manifest-pinned payloads.
- Validation gained JSON-identity-safe `unique`, `accepted_values` and
  grouped `distinct_count`; record flows gained deterministic `groupby`.
- Record streams can be written through the shared atomic writer discipline.
- Skeptic review corrections preserve JSON type identity, refuse output-key
  collisions, structured-cardinality crashes, non-contiguous classifier labels
  and non-finite group keys, and bind Hub cursors to `repo_id`.
- The corresponding TODO entries are checked and ADR-0082…0086 are accepted.

## Landed this wrap: pmquant child (PR #7)

- `children/pmquant/` — prediction-market ladders (Kalshi, Polymarket) as
  thin tier-3 kinds + JSON over dskit seams. `configs/run-e2e.json` is the
  proof document (22 nodes; `tests/test_e2e.py` runs it on the synthetic
  world). `run-kalshi-ladders.json` is its real-data twin.
- dskit generic, ADR-0075…0080: onboarding packs `kalshi`, `polymarket`,
  `predexon` + `leads.py`; the `localtables` connector; the `observations`
  pipeline kind; the public clause DSL; `acquired_at` is the commit instant;
  one backoff ceiling (`connector.MAX_BACKOFF_S`); Polymarket `closedTime`.
- **Waiting on the owner:** `PREDEXON_API_KEY` in the environment before
  `configs/source-predexon.json` can pull; the twin's real-data run on a
  machine holding `~/pmquant_data`; the rulings listed in `TODO.md` under
  "Found by the pmquant child build".
- Also merged: `chore/quote-pull-budget` — the Alpaca quotes backfill
  `budget_seconds` 3000 → 570, so an interrupted pull loses under ten minutes.
- `fix/hstar-min-split-gain` is the pre-rewrite lineage (no common ancestor
  with `main`); every file it carries is already in `main`. Safe to delete.

## Reference

P10 result:
`pipeline_runs/p10-25-asset-modelability-staged-2026-02-28-b7c8efe9`

P10 memo:
`children/intraday_equities/docs/memos/p10-modelability-pipeline.md`

P10 used pooled 25-asset fits and a study-wide 200-cell max-statistic
correction. Gate 2 retained QQQ at three minutes and NFLX at ten; both later
failed Gate 3's frozen null-spread calibration. P11 changes the estimand and
must not overwrite or reinterpret those artifacts.
