# Re-entry

## Current wrap: Gate 3 redesign (2026-09-05)

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
