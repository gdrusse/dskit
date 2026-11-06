# Parallel MIO method units: launch, monitor, recover, stop (ADR-0297, proposed)

Status 2026-11-05: built and tested synthetically on branch `claude/mio-parallel-on-rerun-20261105`
(uncommitted). `tools/mio_parallel_queue.py` and `index_options/mio_worker.py` are PROPOSED, not
approved or merged. No real quarter has run through them. The first real unit is an owner go.
Path rows it rests on: A0003, A0009 and A0010 (the locked MIO test plan: formulations, accounts, the Mac, the 1% first-unit check; uncommitted on `claude/full-span-rerun-20261105`).

Paths below use `R=/home/russell/wt/mio-parallel-20261105` and `PY=/home/russell/dskit/.venv/bin/python`.
Write them out in full when you run anything: the compute gate matches the literal command text. The
venv's editable installs point at another worktree that has no `mio_worker`, so every command here sets
`PYTHONPATH=$R:$R/children/index_options`.

## What runs where

- WSL (canonical): the coordinator `$R/children/index_options/tools/mio_parallel_queue.py` (outside the
  pinned package, so a coordinator fix never forces a re-freeze), `queue.sqlite`,
  `recorded/<quarter>-<arm>/<method>.json`, the frozen six-node documents, registries, receipts.
  Nothing a registry touches ever leaves WSL.
- Workers (the Mac over ssh, spare WSL slots): `python -m index_options.mio_worker spec.json`, one method
  of one quarter-arm from a staged bundle. A worker returns integer actions and solver certificates; the
  WSL document re-derives every valuation (`TransportPortfolioSolve.derived`) and replays the result exactly.
  The worker solves BEFORE any registry claim exists, so it neither needs nor sees one.
- **Sealed files stay on WSL ext4.** `queue_root` (action files with certificates, held parity files,
  rejected/superseded copies, worker logs, document logs and every document's run root `sealed/runs/<step>`)
  must be on WSL ext4, where mode 0700 is real. `/mnt/d` is a 9p drive where chmod does nothing, so the
  coordinator reads each sealed directory's mode back and REFUSES to start on one that is not 0700 (exit 2).
  It also runs with umask 077, which its workers and documents inherit. This is hygiene that stops an
  accidental `cat`, `tail` or editor open of an objective; it is not protection from another process of
  the same user. D: (`scratch_root`) holds only bundles, worker work directories and the index cache.
  Every document runs from a derived copy of its config whose `outputs.run_root` is the sealed one (the
  run root is not part of a document's identity), so run.log's `MIO_EVENT` lines never land in a
  configured, possibly open, place.
- Registries (owner ruling 2026-11-05 13:15): each quarter-arm has its own registry, so quarter-arms overlap.
  The coordinator still allows ONE active document per registry and runs a registry's documents in plan
  order. Two arms naming the same registry (compared by real path) are chained; different registries run
  together, bounded by the RAM gates.

## Forecasts: pinned, not blinded; through 2023Q4 only (owner rulings 2026-11-05 ~14:15 and ~15:10)

MIO on the new forecasts runs on the Mac too. The owner approved a policy for them:
PLAN.md, owner rulings 2026-11-05 ~15:10: "add a fixed_loc forecast type to MIO; rows quoted in 2023 that settle in 2024 get no forecast; no 2019 forecasts, the pipeline starts at 2020Q1."
The "no 2019 forecasts" part was narrowed later (~17:55: "forecast only 2019Q4 from 2019 (option A)"), and the plan of ~17:15 and ~17:30 (locked as Path row A0010) is
nine formulations on four selection quarters (2019Q4, 2020Q1, 2021Q1, 2021Q2), which the owner reads, then the top 3 on 2022 and 2023 (8
quarters), unopened until the picks are fixed: 9 x 4 + 3 x 8 = 60 accounts. The code validates and runs all nine (below), and the coordinator
carries the selection and sealed-holdout flow (section "Selection and sealed holdout"). The "7 x 16 = 112" plan is superseded.

Each quarter-arm's run document uses `domain.predictive.policy = "owner_selected_fixed_forecasts"`
(`MIOStudyContract._fixed_selection`). Take its pins from the builder's `mio-bindings-<recipe12>/<Q>.json`:

- `domain.sources.forecasts` and `.contexts` are the binding's `forecasts` and `contexts`, unchanged.
- `domain.predictive` is `{"policy", "receipt": <the binding's receipt>, "recipe_sha256": <the recipe's hash>,
  "selection_basis": "owner_approved_fixed_loc_after_score_ranked_zoo_and_hpo"}`.
- `bridge.fold` is the quarter's fold (2 for 2020Q1 through 17 for 2023Q4, and 29 for 2019Q4: the builder numbers 2019 after
  the 2020+ folds, `FOLD_2019Q1` = 26). A 2019Q4 receipt must carry `tuning_disclosure` (the HPO tuned on 2019), no other quarter's may.
  `bridge.z_grid` and `grid_labels` are the
  forecast's 13 points (or the fine arm's 25, below); `symbols` is the locked list. Use `protected_end_before = 2024-01-01`, the only boundary a
  2023Q4 tail is capped at. The packet may be any part of the quarter.

It refuses, before any bundle or claim: another receipt schema or field set; a receipt from a synthetic builder run; receipt digests or a lock-file hash that differ from the pins; `evaluation_computed` not false; a quarter
outside 2019Q4 and 2020Q1-2023Q4; a tuned-year (2019) receipt without `tuning_disclosure`; a fold or window that disagrees with the quarter or the document; a label cutoff other than the
quarter start; a window that misses the packet or passes `protected_end_before`; another grid; a recipe that differs
from the document's `recipe_sha256`, is not `fixed_loc`, or has repeated seeds (several distinct seeds pass); a ticker
list other than the lock; names that do not partition the lock into `expected_names` and `missing_names`; files of
another quarter or a pin or byte that differs; and any row at or past 2024-01-01, quoted outside the window, of another
fold or band, unfitted, or unpaired between the two files. A refused quarter is listed under `skipped`.

What "owner selected" means here: the owner approved this forecast type, and the document names one recipe hash. It won
a score-ranked zoo and HPO picks its values, so `selection_basis` does not say "no score ranking" as the older policies
do. Whether a specific recipe hash needs the owner's own sign-off is the owner's call (see ADR-0297).

Not checked by this policy: CDF values (no CDF column is decoded; the builder's structure checks cover them) and the
receipt's fit, panel and fold-table hashes, which are recorded but not traced to a run. The older policies are unchanged:
`owner_selected_pinned_forecasts` stays bound to the M53 blend, and `owner_selected_blinded_cache` arms get NO worker
units and run as plain WSL documents (ADR-0293).

"No one views" holds for 2022 and 2023 only (the owner reads the four selection quarters, ~17:15). It rests on tooling and hygiene, not on a permit: the pinned path has no active permit and no
verification step that withholds the forecasts; what keeps them unseen is that the coordinator and worker print
only hashes, states and counts, that sealed directories and umask 077 stop accidental display, and that nobody
opens `recorded/`, `sealed/` or a bundle.

Nothing dated 2024 or later is read:

- The coordinator refuses a quarter after `2023q4` (its id) and any arm whose selected packet ends after
  `2024-01-01`; both are listed under `skipped` in `status.json`.
- The 2023Q4 settlement tail is capped at the document's `protected_end_before` when that is exactly
  `2024-01-01` (options expiring before it only; see ADR-0297); under any other boundary a tail that reaches it
  refuses. A capped packet records `tail_capped_at` in its close audit.
- A bundle ships whole pinned files, which can hold rows from 2024 on. The bundle therefore ships CUT COPIES:
  every parquet payload keeps only rows whose date columns are before `2024-01-01`, with its own SHA-256 and a
  rewritten snapshot manifest; the study the worker reads pins those digests and `identity.json` records the
  ORIGINAL pins so the action file still names what the document holds. The pinned source files are untouched.
  The cut FAILS CLOSED. Date, timestamp (zoned or not: cut on both the UTC instant and the zone's own date) and `DATE_COLUMNS`
  string columns are cut, dictionary-encoded and large-string columns included. A JSON, CSV, TSV or text payload naming a date
  from 2024 on cannot be cut; a payload of any other type cannot be checked; a parquet string column that `DATE_COLUMNS` does not
  know, but that names such a date in a kept row, cannot be cut by name; and a parquet column of a type that cannot be checked
  (list, struct, map, binary, union, extension, ...) is refused outright, as is a zone this host cannot resolve: in each case that
  arm gets no worker units and runs as a plain WSL document, shown in `status.json` under `no_worker_units`.
  **Metadata pull stamps.** Real files carry 2026 pull stamps in columns such as `created_at`. The explicit list
  `MIOMethodBundle.METADATA_STAMP_COLUMNS` (`created_at`, `acquired_at`; matched by exact, case-sensitive name) names columns that are NEVER
  treated as market dates: they are neither cut nor scanned, but their TYPE is still checked (a struct or list named `created_at` refuses),
  and each file's exempted columns are written to `identity.json` (`metadata_stamp_columns`), so the exemption is on record.
  **`known_at` is not on the list:** in this repo it can be a point-in-time availability date (`contracts.py`, `cdf_study.py`), so it is cut
  (timestamp or date type) or refuses (text) like any date column. A real file whose `known_at` is a 2026 pull stamp therefore refuses and
  its arm falls back to a plain WSL document, loudly (`no_worker_units`); add the column to the tuple only after reading what it holds.
  A cut that removes EVERY row of a file is allowed only when a RECOGNISED market-date column does it (a column named exactly as in
  `DATE_COLUMNS`: `date`, `quote_date`, `expiry`, `expiration`, `settlement_date`): the file ships as an empty table with its schema (a
  window wholly at or past 2024-01-01 is a legitimate empty cut, and refusing it would cost the whole arm its Mac units), and its path is
  recorded in `identity.json` (`emptied_payload_files`). An emptying that comes from any other column refuses: a case variant such as
  `Created_At` or `Date`, `known_at`, or any other name (names match exactly, never case-insensitively), so a pull stamp cannot silently empty a file.
  Zoned date TEXT (`2023-12-31T20:00:00-05:00`) is cut on its UTC instant as well as its wall date, like a zoned timestamp. Any other unnamed column with a date at or past the cap still refuses. The list
  is code (pinned); a new stamp column is added by editing the tuple, not by config. A stamp column therefore ships with its 2026 values.
  **A named date column must hold ISO text.** A `DATE_COLUMNS` string column with any non-null value that is not
  `YYYY-MM-DD` (optionally `T`/space and a time) refuses: strings compare lexicographically, so `12/31/2019` would otherwise pass.
  **Not checked** by the cut (state it, do not assume it): numeric coded dates (epoch seconds, `20240102`), parquet key-value metadata,
  `store.json` and the manifest, and non-ISO date text such as `12/31/2025` or `Jan 5 2025` inside an unnamed string column (every ISO
  date in a value IS read, not only the first; a non-ISO date in a named date column refuses, in an unnamed column it ships). Not detected
  (unverified on real files): integer-coded dates; a real file with a nested or binary column would lose its worker units.

## The nine formulations (owner rulings, PLAN.md 2026-11-05 ~15:30 to ~17:30; Path row A0010)

Every method shares the base `portfolio_fraction 1`, `name_fraction 1`, `short_notional_fraction 0.2`, `component_fraction null`
(the correlation grouping stays off except in `w1_010_component25_shock10_lp`) and the shock `{"moves": [-0.1, -0.05, 0.05, 0.1], "loss_fraction": 0.1,
"beta_lookback": 60}` (not `nominal_lp`). The synthetic test `FORMULATIONS` in `tests/test_ledger_studies.py` validates these exact specs, and
`tests/test_mio_worker.py` runs the two added at ~17:30 as worker units that the document derives and replays.

| id | differs from `w1_010_shock10_lp` | document |
|---|---|---|
| `w1_010_shock10_lp` | `rho 0.10` | 13-point arm |
| `w1_003_shock10_lp` | `rho 0.03` | 13-point arm |
| `w1_003_shock10_mad050_lp` | `rho 0.03`, `risk_charge {"measure": "mad", "price": 0.5}` | 13-point arm |
| `nominal_lp` | `rho 0`, no `shock` (control) | 13-point arm |
| `w1_010z_shock10_lp` | `rho 1.676983322330843`, `radius_scale "reference_scale"` | 13-point arm |
| `w1_010_shock10_fine_lp` | none; its bridge is the 25-point grid | OWN arm (own document, own registry) |
| `w1_010_component25_shock10_lp` | `component_fraction 0.25` | 13-point arm |
| `w1_010_name25_shock10_lp` | `name_fraction 0.25` (each name's reserve at most a quarter of funding, less its open reserve) | 13-point arm |
| `w1_010_shock05_book_lp` | shock `loss_fraction 0.05` and `scope "filled_account"` (the same four moves, `beta_lookback 60`) | 13-point arm |

- **Config only; `filled_account` runs as a Mac unit.** Both are method specs, no code change. `scope: "filled_account"` is marked
  research-only (ADR-0278 amendment); it was checked against method units and `derived`: the worker and the document both run
  `_method_steps`, so each week's open-book shock (`_account_shock`) is computed from the same bundled bars and the same replayed fills, and the
  worker needs no input the bundle lacks. `test_the_diversification_formulations_run_as_worker_units_the_document_derives_and_replays` runs it
  end to end (including a loose cap that holds books, so later weeks do price open books), with no rejection and no fallback solve. Not
  weakened and not WSL-only. Not verified on real quarters.

- **`radius_scale "reference_scale"`** (optional method key; any other value refuses): a group's W1 `scale` is the spot times that
  row's `reference_scale` instead of the spot, so `rho` is a radius in volatility units (a ball of `rho x scale` strike units). The
  value 1.676983322330843 is 0.10 / 0.05963088521417719, the median `reference_scale` of 791,231 rows, 166 names, 2017-2019 (PLAN.md
  ~15:50; input-only). The solver, the LP screen (`_group_lp_bound`), the certificates and the `derived` and recorded replays all read the
  group's one `scale`. Absent key: every result is byte-identical to the code before (four baseline digests are pinned by test).
- **25-point grid.** The builder publishes `<Q>/forecasts-fine.parquet` (every 0.25 z, -3 to 3) and `mio-bindings-<id>-fine/<Q>.json` with a
  `grid` block. For the fine arm take `domain.sources.forecasts` from that binding, set `bridge.z_grid` and `bridge.grid_labels` to
  its `grid.z_grid` and `grid.grid_labels`, and leave `domain.predictive.receipt` the same `receipt.json`. `_fixed_selection` then
  binds `forecasts-fine.parquet` only when the receipt's `fine_grid` record is exactly `{file, sha256, z_grid, grid_labels,
  seed_average, scored}` (file `forecasts-fine.parquet`, `scored` false, `seed_average` the recipe's, the document's grid and labels,
  the scored span, every scored point present) and its `sha256` equals the pin's; the 13-point file must also match the receipt's
  digest. The fine file under the 13-point grid (or the reverse, or another quarter's) refuses as "belongs to another quarter".
  Nothing is sized by 13: the solver program is sized by the listed strikes, so 13 and 25 points give the same variables and rows
  (tested), the quadrature gets more knots (11 to 23 on the test chain) and nothing else changes.
- **CDF rounding cleanup (owner Option B).** Just before the study uses a forecast (`CDFQuoteCondorSelect._prepare`), a point
  that falls below its predecessor by at most `CDF_NOISE` (1e-9) is raised to the running maximum and the row's projection
  evidence records `cdf_noise_raised`. The rule is the builder's own (`MONOTONE_TOLERANCE`, consecutive differences), so a
  forecast the builder published never fails a whole method; a larger step, NaN or a value outside 0 to 1 still refuses
  ("incoherent method CDF"). It is opt-in per call (`_prepare(cdf_noise=...)`, passed only by the MIO study): the legacy
  `CDFQuoteCondorSelect.run` still skips any fall. A monotone CDF is returned bit for bit, so a
  frozen forecast behaves as before; only a CDF that used to refuse by rounding noise now runs. Saved forecasts and scoring are untouched.
- **Component cap warning (confirmed on synthetic data, rule unchanged).** `_components` is a transitive union over pairs with
  `|corr| >= threshold` (and over any pair with too little history): a chain of neighbours is one component. In the test, 12
  names where only the 11 neighbour pairs correlate (55 of 66 pairs do not) form ONE group, so its 25% cap spans the whole
  universe and acts as a 25%-of-funding portfolio cap; two unlinked chains of six give two groups. In a crisis, correlations above the
  threshold are many, so chaining can do the same on 166 names. The cap binds beside the shock row (tested: week two's room is
  25% of funding less the open reserve). Judge `w1_010_component25_shock10_lp` with this in mind.
- **`derived` prices the duals (a defect fixed here).** A worker returns integer actions, not dual variables; `derived` used to read
  the model's profit with duals at zero, so every method with `rho > 0` or a `risk_charge` and a held book failed the document's
  check and was solved locally (logged as `recorded_rejected`): the parallel gain would have been lost for six of the seven
  formulations. `derived` now fixes the actions and solves the remaining linear program for the duals before `extract` runs (a
  small solve per week, never the integer problem). The certificate objective must still match, so actions priced on another scale
  refuse. Derived results equal a local solve to float noise (the last bit of `model_value` can differ), not byte for byte.

## Pinned, not configurable

`c_mount` (`/mnt/c`, the disk the 3 GB stop reads) and `compute_scripts` (the compute skill directory: its Windows RAM figure, reserves and Mac
certificate) are constants of the coordinator (`C_MOUNT`, `SKILL_SCRIPTS`). A config that names another value refuses to start; absent or
exactly the pin is accepted. Otherwise a config could point the safeguards at a roomy disk or its own script.

## What this does NOT prove (read before trusting a result)

Optimality is NOT independently verified. The certificate (objective, bound, termination) is the worker's
own word: WSL re-derives every reserve, value, cleanup and gap from the integer actions and refuses an
invalid or infeasible action file, then replays it exactly, but it does not re-solve and cannot tell a
self-consistent suboptimal solution from an optimal one. That is the owner's ruling (2026-11-05: "we just
trust the mac's answer"; no warm-start sample, no routine re-solve). The only check is the one first-unit
parity pair below, and it covers that one method. Tamper, truncation, wrong plan or bundle, wrong attempt
and a changed byte are caught; a poor solve is not.

## WSL checks the Mac's solve; a rejection is never silent (owner ruling ~18:35)

The Mac's solve is assumed correct and WSL checks it. When WSL refuses a worker result the document solves that method itself (the
fallback stays recorded), and the refusal is written down, never swallowed:

- **What is recorded:** unit (`quarter/arm/method`), decision week, check name, reason category and the size of the miss as a ratio to its
  tolerance (1.0 is exactly the tolerance; no objective, bound or dollar value anywhere). Categories: `tolerance` (a numeric difference over
  `parity_tolerance`, ratio shown, up to 100 times the tolerance: `RecordedCheckRefusal.TOLERANCE_RATIO_LIMIT`), `numeric` (a miss above that
  limit, or with no tolerance to size it: a garbage or wrong file, NOT a tolerance issue), `invalid_trade` (actions the program forbids: capacity, a cleanup pair, a screened-out group),
  `certificate` (malformed or contradictory solver evidence, model size), `gap` (an uncertified gap, ratio to its allowance), `pricing`,
  `file` (sidecar, schema, plan, study, inputs; also the coordinator's own cheap gates), `replay` and `other`. Checks that decide a numeric miss:
  `certificate_objective`, `certificate_bound`, `incumbent_negative`, `model_over_audit`, `cleanup_gain`, `bound_below_value`. The owner expects
  any rejection to be a tolerance issue: a small ratio (a few times 1) says so, an `invalid_trade`, `numeric` or a large ratio says it is not.
  The coordinator's output line shows the LARGEST open ratio, so a garbage file cannot read as a tolerance issue.
- **Sealed quarters show unit, check and count only.** For a holdout quarter that is still sealed (and for a unit that names no known quarter)
  `status.json`, the console line and `reasons` carry the unit, the check name and a count, nothing else: no ratio, week, `at`, run, category or
  value (`reasons` count them as `sealed:<check>`). The same cut applies to `fallbacks` and the `fallback:` line (the unit only, no attempt
  notes), to a failed sealed arm's `why` (`failed: the owner reads the sealed run log`, no stage or exit code), to a sealed arm's `waiting` reason (the bare word `sealed`, never a failure text or an `N of M units unsettled`), and to the `recorded-from`
  command on a directory under `holdout/` (method, check and count; per-method status, host, attempt and digests are reduced to counts). A sealed unit's and arm's state words (`solved_local`, `replayed`,
  `closed`, `rejected`) would date the quarter's progress, so `status.json` lists no unit of a sealed quarter, gives its arms the status
  `sealed` (or `failed`), and shows `sealed_counts`: units and settled units per arm. `rejections.jsonl` (owner-only, queue root) keeps the
  FULL row privately and is never printed; `unseal` shows it all. The recorded rows themselves are complete: a holdout run's rejections are
  recorded in the ledger and pause the Mac like any other (a bug emptied the list for holdout directories, so they did not).
- **Where:** `<queue_root>/rejections.jsonl` (append-only history), `status.json` key `rejections` (`open`, `cleared`, `reasons` as
  `category:check` counts, `records`, each with a `seq`), and a line in the coordinator's output whenever the count changes. The document hands
  the coordinator a list in its close audit (`recorded_rejections`) and the log's `recorded_rejected` event carries the same fields; the
  coordinator reads only those validated fields (a week is an ISO date, a check a snake-case word, a category from the fixed list, a ratio a number).
- **The pause, and how soon.** While any rejection is open, `place` leases no new Mac unit (`status.json` `waiting.rejections` says so). Running
  work finishes and is accepted; pending units wait (WSL slots, if any, still take them); nothing is quietly rejected over to WSL. The pause is
  host-agnostic: a rejection of a file from any host pauses the Mac. **The coordinator reads each running document's `run.log` every pass** (the
  new complete `recorded_rejected` lines; `logging.FileHandler` flushes per record, so the line is on disk when logged) and records them at
  once, so the Mac stops within one pass (`--poll`, 60 s) of the document REACHING the rejected method, not when the document returns. The
  document's own list at the end is the same fact (same unit, week, check, ratio and run directory) and adds no second row. Chosen over holding
  the Mac while any Mac-solved arm awaits its document, because that rule would idle the Mac for every document's whole run (hours per arm) and
  over a per-unit pre-check on WSL, because the check IS the document's replay (it needs the whole study, program and inputs for that unit).
  **What is left:** a document starts only once every unit of its arm settled, and it reaches the rejecting method part way through, so units
  the Mac leased before that moment still run; they are discarded cost, not wrong results (every file is derived and replayed by its own
  document before it counts, and a rejected one is solved locally). A document that has not logged yet is invisible; a log that cannot be
  read is skipped, and the end-of-document list still arrives. An unreadable LEDGER stops the Mac and says why in `status.json`
  (`rejections.unreadable`), then the pass fails visibly (`waiting.pass_failed`).
- **Clearing:** the owner reads `status.json`, then runs the command below with `--through SEQ`, SEQ being the highest `seq` he read. It closes
  every open row up to that `seq`; a row recorded after he looked has a higher `seq` and stays open, and repeating the same command closes
  nothing new. Without `--through` it closes the rows open at the moment it reads. A zero or negative `--through` is refused (a negative `--seen`
  used to close every open row). It needs no coordinator stop; the next pass places Mac units again. Cleared records stay in the file as
  history. A document that reports the same rejection again after a crash does not re-open a cleared row (same unit, week, check, ratio and
  run); the run name includes the step root, so the rerun of a REOPENED arm (a `.retryN` root) counts as new. An unreadable ledger file refuses
  the command; only a missing file means "no rejections yet".
- **A Mac unit that falls back to WSL is shown, not paused.** A unit whose Mac attempts are spent (stale or exited twice, ssh faults,
  refused files) ends `rejected` and its document solves the method. `status.json` key `fallbacks` gives `unit: attempt 1 on mac: <reason>;
  attempt 2 on mac: <reason>` and the console prints a `fallback:` line when the set changes. No pause: it is an infrastructure outcome.

```bash
cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py clear-rejections /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json --through <highest seq read>
```

## Selection and sealed holdout (Path row A0010)

Config keys `selection_quarters`, `holdout_quarters`, `selection_formulations` (the nine), `selection_picks` (3) and
`selection_formulation_hashes` turn this on (all five or none; the example config has them). Without them nothing is sealed, as before.
`selection_formulation_hashes` pins one spec hash per formulation id (`Phases.spec_hash` of the method spec including its `id`): a document
whose method of that id hashes differently is refused in a selection or a holdout quarter, so an id can never name another formulation than
the one the owner read. Numbers are normalised before hashing (`1` and `1.0` hash alike), so a real config may write either; the earlier
pins hashed floats only and refused an integer `1` at plan time, so the nine in the example were re-pinned (2026-11-05) from the
`FORMULATIONS` specs. A real document that carries an extra key on a method fails loudly with both hash prefixes, and the owner re-pins on
purpose, never by accident.

1. **Selection quarters** (2019q4, 2020q1, 2021q1, 2021q2) run all nine formulations across their arms (the fine one is its own arm). A selection
   quarter whose documents carry another set is skipped with its reason.
2. **The owner reads them** with one explicit command, `report-selection config.json`: per quarter, arm and formulation, the status and
   `pnl`, `twr`, `max_drawdown`, `final_equity`, `contracts`, `unfilled`; listed in id order, no rank or score (the owner chooses). It refuses until
   every selection arm is closed and reads no holdout arm. No such command exists for the holdout quarters.
3. **The owner picks 3:** `record-selection config.json ID ID ID`, once (a second call is refused: the picks are fixed; it also refuses until the
   selection arms are closed, and for any id outside the nine or a wrong count). It writes `<queue_root>/selection.json`.
4. **Holdout quarters** (2022q1 to 2023q4) are planned only after that: a holdout quarter with a READY marker and no recorded selection makes the
   coordinator REFUSE TO START (exit 2), and one whose documents run anything but the picks is skipped. So write the holdout READY markers after the picks.
5. **Sealed:** a holdout quarter's action files, specs, logs and progress files (WSL `out`), aside copies and document run roots live under
   `<queue_root>/holdout/` (mode 0700); `status.json`
   shows its units as state and attempt only and its arms without `run_dir`, with a `sealed` block (quarters, selection, `unsealed`). The owner opens
   it with `unseal config.json` (needs the recorded selection); only then does status show hosts, digests and run directories. This is hygiene, like
   the rest of the sealing: it keeps results out of status, aside copies and reports, not out of reach of a user who opens a file.

All four owner commands are `mio_parallel_queue.py <verb> /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json` with the same
`cd` and `PYTHONPATH` prefix as above; they take no coordinator lock. The economics report is for the owner to run: no agent runs it on real data.

## WSL first, the Mac added later (the Mac off)

All nine formulations on 2019q4, 2020q1, 2021q1 and 2021q2 can run on WSL alone. The code needs no Mac slot, no certificate and no
parity gate for it: with no `mac` block `_build` creates the `wsl` host only (no `SshHost`, no `skill_certificate`, no ssh), `parity.unit`
is not required and `parity_state()` is `None` (a stray `parity` block is ignored without a Mac). Verified on synthetic data
(`test_wsl_only_config_builds_no_mac_no_certificate_and_no_parity`, `test_wsl_first_then_adding_the_mac_later_keeps_what_wsl_finished`);
no real quarter was run.

**The config that sets it:** copy `queue.example.json` and (1) delete the `mac` block (and the `parity` block, optional); (2) set
`"wsl_slots"` to N, with **N >= 1**. Without a Mac, `wsl_slots: 0` is refused at start (`no Mac host and wsl_slots is 0`): no host could run
a unit, and holding the units for a Mac that is not configured would only hide that. (The old single-process behaviour, every document solving
its own methods, is a run with no queue; it is not this route.) `N >= 1` runs up to N local
worker processes (`unit_ram_gb`, 8 GB unmeasured, each; admitted only while WSL RAM, Windows RAM and C: allow, so with other jobs running
expect N of 1 or 2) whose files the document derives and replays. (3) Keep `quarters` as the full list and `selection_quarters` /
`holdout_quarters` as they are; write the `READY-<quarter>.json` markers for the four selection quarters only (the planner stops at the first
quarter without a marker, and a holdout marker before the picks refuses the start). Steps (1) to (3) of Launch, then the plan, then (7).
Skip precondition 3 (RAM disk), 4 (certificate), the check bundle and `stage`. The `jobs.json` needs no `certify`, `worker-timing`
or `mac-units` job; list the WSL workers as a job (`wsl-units`, `index_options.mio_worker`, started by the coordinator) when `wsl_slots` is above 0.

**What is and is not reused.** Reuse holds ONLY for work finished under the same `queue_root`, by this queue. Results of a plain WSL
document (for example the branch `claude/mio-wsl-run-20261105`, documents under `mio-wsl/`, with their own code pins and 4 methods) can NOT
be imported into the queue: their code pins differ from the queue's bundles, and `check_methods` requires all nine formulations in every
selection quarter. No import exists and none is planned; such quarters are solved again by the queue.

**Adding the Mac later, without re-solving what WSL finished.** Let the coordinator stop (`touch STOP`, or wait for the pass), then edit the
same config: add the `mac` block and a `parity` block, and run the Mac preconditions, `check-bundle`, `stage` and the certificate. Restart
on the SAME `queue_root`. Finished work survives: `queue.sqlite` keeps every unit row (`verified`, `replayed`, `solved_local`, `rejected`
are not pending, so no host is given them again), `recorded/` keeps the published files, a closed arm stays closed and its document is not
rerun, a running arm is adopted, and a live WSL lease is adopted by pid. Only units still `pending` can go to the Mac. Two rules:
(a) **`parity.unit` must name a unit that is still `pending` with no lease ever taken** on WSL. One WSL finished, or holds leased, is
refused at start (`parity.unit ... was leased on a host other than the Mac`): the Mac would never be given it, or WSL would be compared with
WSL and the Mac released with no comparison. A pass is also written only when the first result came from the Mac. Pick the first
pending unit of the next arm (read `status.json`); if WSL has leased a unit, let it finish and pick another. (b) **While parity is pending the other units wait** (the Mac runs only the parity unit
and WSL only its twin; the others are released to WSL after `parity_grace_seconds`, 4 h, if no verdict comes): WSL idles for about one
unit's solve, plus the document wait, after the Mac is added. A WSL document for an arm whose units are all settled still starts.

## Preconditions (owner items, not gates)

1. Per-quarter forecasts exist and `READY-<quarter>.json` markers are written (same schema as the 2022
   queue: `{"asof", "runs": [{"name", "initialize", "run"}]}`; the run config's MIOPacketStudy node carries
   `"study"` only; the queue adds `"recorded"`). Every `initialize` and `run` document must be on the locked
   ticker list; the coordinator calls `python -m index_options.ticker_lock DOC` for it and skips a quarter
   that fails or whose marker is malformed. Each quarter-arm's study names its OWN registry path.
   A skipped quarter also skips later quarters that share its registry; other registries are unaffected.
2. C: budget: the new code pins invalidate every cached quote index, so each document rebuilds about
   3.4 GB on WSL. The coordinator projects 3.4 GB per job (`c_job_gb`) against the 3 GB floor, so a job
   starts only if C: stays above 3 GB after it. An unreadable C: stops all new placement and is shown in
   `status.json` under `waiting.disk`.
3. The Mac RAM disk (owner-approved exception, ADR-0297 amends clause 4 of the Mac-worker ADR-0290 (on the unmerged codex branches; not this tree's ADR-0290) and ADR-0293 for this use)
   exists, is mounted at `mac.root`, and carries `<root>/.mio-sentinel` holding the token in the config.
   **What the mount check does NOT prove:** that the volume is memory-backed. It proves a mount point of that name holds a file with the
   token the operator wrote (it keeps a vanished disk from sending writes to the SSD directory of the same name); a disk-backed volume
   with the token would pass. The RAM disk is the owner's hand-made precondition (the `hdiutil ram://` line below), checked once by eye
   (`diskutil info /Volumes/mio-ram` names a RAM disk device), not by the coordinator.
   The mount-identity checks stay: every pass, every spawn, and in `mac_worker.sh`, which now also compares
   the file's CONTENT with the token the coordinator passes (`MIO_SENTINEL`) and refuses a spec whose bundle,
   work directory, output or cache path is outside the root. Create it, not in /tmp or any SSD path:
   `hdiutil attach -nomount ram://<512-byte sectors>` then `diskutil erasevolume HFS+ mio-ram /dev/diskN`,
   then `echo <token> > /Volumes/mio-ram/.mio-sentinel`, then copy `mac_worker.sh` into it. The fit's
   `/Volumes/fullspan-ram` must be finished first and is never touched. The Mac worker's packages must equal
   the study's `dependencies` pins.
4. A valid Mac certificate for job type `mio-method-unit` (command below). The type is fixed in the
   coordinator; a config that names another (or an empty one) refuses to start.

### Mac RAM-disk size (UNMEASURED guidance)

The disk holds, per staged quarter-arm, the cut bundle (`du -sh <scratch_root>/bundles/<quarter>-<arm>`
on WSL is its size); per running unit its work directory (the quote indexes, about 3.4 GB is the document's
rebuild figure and the unit's may differ: measure `du -sh /Volumes/mio-ram/work/*` during the first unit); the
shared index cache `cache/`; and the small `out/` files. Size it as: bundle + slots x work + cache + 1 GB, then
check that the RAM disk plus slots x `unit_ram_gb` plus `mac_reserve_gb` stays under the Mac's RAM (32 GB). With
the first-parity setting (one slot) and one quarter-arm staged, about bundle + 3.4 + 3.4 + 1 GB.
The coordinator prunes: after a result is accepted or refused its work directory is deleted (WSL and Mac), and
when an arm's units have all settled, as its document starts, the Mac copy of its bundle is deleted. NOT
pruned: `cache/` (clear it between quarters while no unit runs) and the small `out/` files (after the quarter closes). Every hand-run
Mac line here first proves that the root is the mounted RAM disk carrying the sentinel (`df` names the root as its own mount point and
`.mio-sentinel` holds the config's token), exactly as `mac_worker.sh` does, so a vanished RAM disk can never make it write or delete on the
Mac's SSD (replace `<token>` with the config's sentinel):

```bash
ssh mac 'R=/Volumes/mio-ram; [ "$(df -P "$R" | awk "NR==2{print \$NF}")" = "$R" ] && [ "$(cat "$R/.mio-sentinel")" = "<token>" ] && rm -rf "$R/cache"'
ssh mac 'R=/Volumes/mio-ram; [ "$(df -P "$R" | awk "NR==2{print \$NF}")" = "$R" ] && [ "$(cat "$R/.mio-sentinel")" = "<token>" ] && rm -f "$R"/out/<quarter>*'
```

## Launch

Order matters. The compute gate runs every worker, `stage` and coordinator command only under a plan that lists it, so the plan comes
FIRST and no line below needs a waiver. The Mac is online only once its certificate is valid, and the certificate needs the bundle on
the Mac: (1) create the RAM disk (precondition 3); (2) write `READY-<quarter>.json` (selection quarters only; the holdout markers come after
the owner's picks); (3) copy and edit the config; (4) write `jobs.json` and make the plan; (5) stage the parity arm's bundle with the `stage`
command (it keeps the mount-identity gate and the whole-or-nothing rename, needs no certificate and places nothing); (6) build and stage the
short check bundle (`check-bundle`, `stage check:...`), write the two cert specs, time the worker, certify; (7) launch. The `jobs.json` below lists the timing and certify commands as well.

```bash
cp /home/russell/wt/mio-parallel-20261105/children/index_options/docs/plans/mio-parallel/queue.example.json /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json   # edit paths, token, parity unit
# compute-plan gate (never waived): write jobs.json (below) with the LITERAL commands, then make the plan BEFORE stage, timing or certify:
python3 /mnt/c/Users/russe/.codex/skills/mac-compute-worker/scripts/compute_run.py plan new mio-parallel --jobs /home/russell/data/index_options/full-span-rerun-20261105/mio/jobs.json
cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py stage /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json 2019q4/rules   # QUARTER/ARM of the parity unit; prints a digest only
```

### Steps 6a to 6c: the Mac certificate (owner ruling: a short check, not an exact match; NOT RUN)

The owner chose a SHORT worker check on a 1-2 week packet bundle, with no exact match of the integer actions: the 1% first-unit parity gate
(below) is the real comparison. The skill's `compute-run certify` runs one command on WSL and one on the Mac and compares the two outputs; it
compares JSON numbers within `--tol` (relative, floor 1: the same form as the parity gate) and everything else exactly. The shared skill is
unchanged. The worker's `--check` flag prints `{"method", "weeks", "objectives", "terminations"}`: per week, EVERY certificate's objective and
termination word (lists of lists), no actions; it exits non-zero when no week was solved (zero weeks, or a week with no certificate), so two optimal books of equal value (different integer actions) pass, and `--tol 0.01` is the 1% rule. The documented command always uses this fixed `--tol 0.01`; never pass another value (the skill's own
`_walk` does not validate `--tol`, and a negative, `nan` or `inf` value would make the comparison pass or fail for the wrong reason).
The earlier `--print` (a digest of the actions) is NOT used for the certificate any more.

1. **Build the short bundle (WSL, no Mac contact):** `mio_parallel_queue.py check-bundle config.json 2019q4/rules --weeks 2` builds
   `<scratch>/bundles/check-2019q4-rules` from the parity arm's own run document with its selected packet cut to at most 2 weeks (the same
   `MIOMethodBundle`, so the same date cut and pins; a selection quarter only, never a plan unit, never read by a document). It refuses a
   holdout quarter, a quarter after 2023Q4 and an arm absent from the READY markers. **Unverified on real data:** the synthetic tests build
   and solve it; a real packet may refuse the shortened window (a pin or receipt that names the full quarter). If it refuses, stop and ask the
   owner: do not certify on the full bundle (a 13-week unit overruns the skill's fixed 900 s limit).
2. **Stage it on the Mac (Mac step, unrun):** `mio_parallel_queue.py stage config.json check:2019q4/rules` (the same whole-or-nothing,
   sentinel-checked stream as the real stage; needs no certificate).
3. **Write the two specs** (absolute paths on each host; the bundle is the check bundle; `<method>` the parity method). The WSL timing and the
   certify below use `--check`:

```bash
mkdir -m 700 -p /home/russell/data/index_options/full-span-rerun-20261105/mio/cert
cat > /home/russell/data/index_options/full-span-rerun-20261105/mio/cert/wsl.spec.json <<'EOF'
{"bundle_root": "/mnt/d/full-span-rerun-20261105/mio-scratch/bundles/check-2019q4-rules", "method": "<method>", "attempt": 1,
 "work_dir": "/mnt/d/full-span-rerun-20261105/mio-scratch/cert-work", "out": "/home/russell/data/index_options/full-span-rerun-20261105/mio/cert/cert.json"}
EOF
ssh mac 'R=/Volumes/mio-ram; [ "$(df -P "$R" | awk "NR==2{print \$NF}")" = "$R" ] && [ "$(cat "$R/.mio-sentinel")" = "<token>" ] && mkdir -p "$R/out" && cat > "$R/out/cert.1.spec.json"' <<'EOF'
{"bundle_root": "/Volumes/mio-ram/bundles/check-2019q4-rules", "method": "<method>", "attempt": 1,
 "work_dir": "/Volumes/mio-ram/work/cert.1", "out": "/Volumes/mio-ram/out/cert.1.json"}
EOF
```

**900 s limit.** The skill's `certify` gives each of its two commands a fixed `timeout=900` (no CLI option); a longer command writes NO
certificate and leaves the Mac offline. A 1-2 week unit is far inside it (a cold quote-index build is minutes, each week's solve at most its
`time_limit`), but time both commands by hand first; both must finish well under 900 s. The WSL timing is a worker run, so the plan lists it as
the job `worker-timing`; the Mac line needs no plan (the sentinel-checked `mac_worker.sh` refuses a root that is not the marked RAM disk):

```bash
time sh -c 'cd /home/russell/data/index_options/full-span-rerun-20261105/mio/cert && umask 077 && PYTHONPATH=/mnt/d/full-span-rerun-20261105/mio-scratch/bundles/check-2019q4-rules/code /home/russell/dskit/.venv/bin/python -B -m index_options.mio_worker wsl.spec.json --check'
time ssh mac 'MIO_SENTINEL=<token> sh /Volumes/mio-ram/mac_worker.sh /Users/gibsonrussell/Compute/software/mio-worker-20261102/bin/python /Volumes/mio-ram/out/cert.1.spec.json --check'
```

Then certify with a 1% tolerance (`cert.json` holds an action file with certificates, so its directory is on WSL ext4 with mode 700; delete
`cert/` and `cert-work/` afterwards, the certify only needs the printed line):

```bash
python3 /mnt/c/Users/russe/.codex/skills/mac-compute-worker/scripts/compute_run.py certify mio-method-unit --mac-host mac --tol 0.01 --wsl-command 'cd /home/russell/data/index_options/full-span-rerun-20261105/mio/cert && umask 077 && PYTHONPATH=/mnt/d/full-span-rerun-20261105/mio-scratch/bundles/check-2019q4-rules/code /home/russell/dskit/.venv/bin/python -B -m index_options.mio_worker wsl.spec.json --check' --mac-command 'MIO_SENTINEL=<token> sh /Volumes/mio-ram/mac_worker.sh /Users/gibsonrussell/Compute/software/mio-worker-20261102/bin/python /Volumes/mio-ram/out/cert.1.spec.json --check'
```

(One line, so the gate sees it whole; replace `<token>` with the config's sentinel. The plan's `certify` job matches its stable first part, up to
`--check'`, because the token differs.) The certificate passes when every week's objective agrees within 1% of the larger objective (taken against a one-dollar scale when both are smaller, so a gap of at most one cent) and the
termination words and the week count are identical. It says nothing about whether the integer actions match: two different optimal books pass.
The certificate expires after the skill's 30 days. The parity gate below, not the certificate, is the similarity check on a real unit.

Then launch (step 7):

```bash
cd /home/russell/wt/mio-parallel-20261105 && nohup env PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py run /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json --poll 60 > /home/russell/data/index_options/full-span-rerun-20261105/mio/coordinator.log 2>&1 &
```

The gate matches a job's literal `command` as a substring of the typed command. The launch adds `nohup env`
and a redirect, which break a substring of the whole command, so the coordinator job also lists under `match`
(the gate's own key for alternative spellings) the part that stays literal: `mio_parallel_queue.py run <config>
--poll 60`. Check the launch first with the throwaway job `coordinator-check` below: a config with
`"quarters": []` and no `mac` at `.../mio/check/config.json`, run with `--once`, writes `status.json` and exits 0
(verified 2026-11-05 on a synthetic config).

`jobs.json` (fields `id, command, match, allowed_hosts, cores, ram_gb, ram_basis, disk_gb, depends_on,
serial_reason`; `ram_gb` is a placeholder until a real unit is measured; each `command` is the literal command
text, no shell variables):

```json
{"run_id": "mio-parallel", "jobs": [
  {"id": "coordinator", "command": "cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py run /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json --poll 60",
   "match": ["mio_parallel_queue.py run /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json --poll 60"],
   "allowed_hosts": ["wsl"], "cores": 1, "ram_gb": 1, "ram_basis": "UNMEASURED: the loop holds only sqlite rows and files",
   "disk_gb": 0, "serial_reason": "single writer of queue.sqlite"},
  {"id": "coordinator-check", "command": "cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py run /home/russell/data/index_options/full-span-rerun-20261105/mio/check/config.json --once",
   "allowed_hosts": ["wsl"], "cores": 1, "ram_gb": 1, "ram_basis": "UNMEASURED: one pass over an empty plan",
   "disk_gb": 0, "serial_reason": "same queue code as the coordinator; run before it, never beside it"},
  {"id": "coordinator-tools", "command": "cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py stage /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json",
   "match": ["mio_parallel_queue.py reopen /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json", "mio_parallel_queue.py recorded-from", "mio_parallel_queue.py check-bundle /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json",
             "mio_parallel_queue.py clear-rejections /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json", "mio_parallel_queue.py record-selection /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json",
             "mio_parallel_queue.py unseal /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json", "mio_parallel_queue.py report-selection /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json"],
   "allowed_hosts": ["wsl"], "cores": 1, "ram_gb": 1, "ram_basis": "UNMEASURED: stage streams a bundle with tar over ssh",
   "disk_gb": 0, "serial_reason": "stage and reopen take the coordinator's lock, so neither runs beside a coordinator; the owner verbs (clear-rejections, record-selection, unseal, report-selection) take no lock and append one line or read"},
  {"id": "worker-timing", "command": "time sh -c 'cd /home/russell/data/index_options/full-span-rerun-20261105/mio/cert && umask 077 && PYTHONPATH=/mnt/d/full-span-rerun-20261105/mio-scratch/bundles/check-2019q4-rules/code /home/russell/dskit/.venv/bin/python -B -m index_options.mio_worker wsl.spec.json --check'",
   "allowed_hosts": ["wsl"], "cores": 1, "ram_gb": 8, "ram_basis": "UNMEASURED placeholder (unit_ram_gb): one worker on a 1-2 week bundle; measure it with the first run",
   "disk_gb": 3.4, "serial_reason": "run alone before certify, so its seconds are the unit's own"},
  {"id": "certify", "command": "python3 /mnt/c/Users/russe/.codex/skills/mac-compute-worker/scripts/compute_run.py certify mio-method-unit --mac-host mac --tol 0.01 --wsl-command 'cd /home/russell/data/index_options/full-span-rerun-20261105/mio/cert && umask 077 && PYTHONPATH=/mnt/d/full-span-rerun-20261105/mio-scratch/bundles/check-2019q4-rules/code /home/russell/dskit/.venv/bin/python -B -m index_options.mio_worker wsl.spec.json --check'",
   "allowed_hosts": ["wsl"], "cores": 1, "ram_gb": 8, "ram_basis": "UNMEASURED placeholder (unit_ram_gb): certify runs one WSL worker beside the Mac's",
   "disk_gb": 3.4, "depends_on": ["worker-timing"], "serial_reason": "certify follows the timing of the same command; the Mac command carries the token, so only this part is matched"},
  {"id": "documents", "command": "dskit.pipeline run <quarter-arm run config>, launched by the coordinator",
   "allowed_hosts": ["wsl"], "cores": 1, "ram_gb": 8, "ram_basis": "UNMEASURED placeholder (document_ram_gb); measure the first real document",
   "disk_gb": 3.4, "depends_on": ["coordinator"], "serial_reason": "one active document per registry"},
  {"id": "mac-units", "command": "index_options.mio_worker on the Mac RAM disk, started by the coordinator over ssh",
   "allowed_hosts": ["mac"], "cores": 1, "ram_gb": 8, "ram_basis": "UNMEASURED placeholder (unit_ram_gb); measure the first real unit with /usr/bin/time -l",
   "disk_gb": 0, "depends_on": ["coordinator"], "serial_reason": "first-parity gate: one Mac unit until parity passes"}]}
```

`--once` runs a single pass. A second coordinator on the same queue root exits with code 4. A coordinator
with a Mac host and no `parity.unit` refuses to start (exit 2), and so does one whose `queue_root` cannot be
sealed or whose Mac certificate type is not `mio-method-unit`. Exit 3 means it stopped because arms failed and
nothing else could move; exit 5 means ten passes in a row failed on a disk or database error (a failed pass is
printed to `coordinator.log` with its error and retried; a full disk is not silent). The coordinator stages
one bundle per arm (`<scratch_root>/bundles/<quarter>-<arm>/`; an existing bundle is re-verified byte for
byte) and streams it to the Mac with tar over ssh on a thread, so a slow transfer never freezes a pass or the
C: stop. The bundle appears on the Mac under its name only when it is whole (unpacked to a temporary directory,
file count and byte total compared, then renamed), so an interrupted transfer is copied again.

## The coordinator's own limits (the compute hook does not see processes it starts)

- WSL RAM: MemAvailable minus the 6 GiB reserve; Windows available RAM minus the 2 GiB reserve, each on its
  own. A unit holds back one document's RAM only while a document is DUE (all its units settled, registry
  chain clear); otherwise it needs only `unit_ram_gb`. The Windows figure is accepted only if at most 120 s
  old (the skill's cache allows 900 s); an older one reads as unknown, which is a stop.
- Floors a config can raise but never lower (it is raised to the floor, an unknown limit refuses, and a limit that
  is not a finite number, such as a `NaN` literal, refuses because `max(NaN, floor)` is `NaN`):
  `c_floor_gb` 3, `c_job_gb` 3.4, `unit_ram_gb` 4, `document_ram_gb` 4, `ramp_seconds` 120, `stale_seconds` 1800,
  `wsl_reserve_gb` 6 (the skill's own constant if larger), `windows_reserve_gb` 2, `mac_reserve_gb` 4.
- `unit_ram_gb` 8 and `document_ram_gb` 8 are UNMEASURED placeholders (no real unit has run). Replace them
  from the first real unit's receipt.
- C: 3 GiB stop: no unit or document starts if C: would fall below the floor after its projected growth;
  WSL WORKERS are killed and requeued when C: falls below it (a finished one is fetched first; at most
  `max_refunds`, 3, such stops are not counted as attempts). Documents are NOT stopped by the coordinator.
- Mac: its own free RAM (`vm_stat`) minus the reserve; unreadable is zero slots. The RAM-disk root must be
  a mount point carrying the sentinel, checked on every pass and before every spawn; otherwise the Mac is offline.
  An ssh that fails mid-run makes a worker's liveness UNKNOWN, not dead: it is neither requeued nor killed on
  that; past the stale limit it is requeued and a kill is remembered for when the Mac answers. A kill checks
  the process start time before it signals a pid.
- Attempts: `limits.max_attempts` per host (default 2 for `mac` and `wsl`; a host not named gets 2, never
  unlimited). A failed stage or spawn is refunded like a resource stop (up to `max_refunds` 3), so a transient
  ssh fault costs no attempt; once attempts are spent a unit is `rejected` and the document solves the method
  itself. A host with 0 slots is not usable.
- First-parity gate (`parity.unit` is REQUIRED with a Mac): until the parity unit has a verdict, the Mac runs
  only that unit and WSL only its twin (the same method, held out of `recorded/`), whatever the slots say. The
  verdict is pass/fail and hashes only: identity digests (method, bundle, plan, study, portable inputs,
  dependencies) and the decision-week set must be identical, and EVERY week's objective must be within 1%
  relative (Path row A0010 records the 1% first-unit check; the constant is `Similarity.REL`). The test is `|a - b| <= 1% * max(|a|, |b|, FLOOR)` with
  `Similarity.FLOOR` = 1 dollar: two objectives both smaller than a dollar may differ by at most one cent (1% of the floor), far below one
  contract's smallest priced step, so the floor lets two cash or near-zero weeks agree and cannot hide a held book's difference. Whether the two hosts' integer actions are identical is recorded
  in `parity.json` as `actions_match` (information only, never part of the verdict: the owner wants similar, not equal). **Only a completed comparison writes a verdict.** A pass is acted on first (the Mac file goes to
  `recorded/`, the twin is verified) and the verdict is written last, so a full disk or a crash in between is
  repaired by the next pass; a failure writes its verdict first and the held units are rejected after, also
  repaired on restart. `parity.json` records the unit, the threshold hash and the bundle hash; a verdict for
  another unit, bundle or threshold is stale and counts as failed, a failed verdict is final for the queue root
  (start a new queue root to try again).
  Infrastructure faults are not a verdict: a parity unit not placed within `parity_grace_seconds` (4 h; the clock
  restarts with the coordinator), or one that ends without a result (attempts spent, ssh down), writes NOTHING and
  kills nothing. `status.json` says so (`waiting.parity`: "no verdict"), the Mac keeps its unit, and the OTHER
  units are released to WSL slots, or, with a Mac and `wsl_slots` 0, rejected so their documents solve locally. The arm
  of the parity unit waits for the comparison. The twin needs only `unit_ram_gb`, not a document's room.

## Monitor (nothing here shows economics)

- `status.json` in the queue root: unit states, attempts, hosts, result digests, arm states with a `why`
  and their `registry`, `moving`, `waiting` (every reason nothing moves: `place`, `parity`, `disk`, `wsl`, per
  arm, `orphans`), `parity`, `parity_reason`, `skipped` quarters, `no_worker_units` (arms that run as plain WSL
  documents and why), `orphans` (units or arms left in `queue.sqlite` by an earlier plan: ignored, listed), `fallbacks` (units that ended without a usable worker result, solved by the document, with the reason of each ended attempt) and `rejections` (open and
  cleared counts, reasons as `category:check`, the open records: unit, week, check, category, ratio; a sealed quarter's record keeps unit, check and `seq` only: the ratio key is dropped, not null) and `sealed` (the holdout
  quarters, the recorded picks, whether unsealed; a sealed quarter's units show state and attempt only).
- Worker progress: `<queue_root>/out/<unit>.<attempt>.json.progress` (WSL) or
  `ssh mac tail -n 1 /Volumes/mio-ram/out/<unit>.<attempt>.json.progress`: a UTC stamp and a week count (`week 3/13`),
  never a status word (a week's status would show a holdout quarter's trading timeline). Progress is judged by CHANGE. A holdout unit's
  file is `<queue_root>/holdout/out/...` on WSL; on the Mac `out/` holds every unit's files until the quarter closes (the Mac is trusted). A cold index build writes an `index building` heartbeat every 60 s, which
  stops after an hour so a hung build goes stale.
- Document output goes to `<queue_root>/sealed/` (mode 0700). Nobody tails or greps it. To see which methods
  a worker supplied use the hash-only helper (literal paths):
  `cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py recorded-from <run_dir>`.

**For the owner to confirm (raised, not decided):** `<queue_root>/rejections.jsonl` keeps the FULL rows (week, ratio, category, run, time)
of every rejection, sealed quarters included, in the 0700 queue root. It is never printed: `status.json`, the console and `recorded-from`
show a sealed quarter's unit, check and count only, and a sealed arm's `waiting` reason is the bare word `sealed`. Whether the owner is
content with the full rows living in that file is an open item.

## Recover

- Coordinator died: restart the same command. Leased units are checked for liveness and adopted; dead ones
  requeue. An arm left `running` is re-attached: the runner waits for the earlier launch of the same step or
  reuses its SUCCESSFUL run directory. A worker left on a Mac that was offline when its lease was requeued
  is killed when the Mac answers again (`pending_kills.json`).
- Worker died or stalled: no progress change for `limits.stale_seconds` (default 75 min) kills and requeues it.
- Rejected return (bad digest or sidecar, wrong attempt/bundle/plan/method, malformed host or numbers): the
  bytes go to `<queue_root>/rejected/`, the unit requeues; nothing enters `recorded/`. A worker file that passes
  these checks but fails the document's re-derivation or exact replay is logged as `recorded_rejected`
  (numbers scrubbed, plus the structured week, check, category and ratio), recorded in `rejections.jsonl`, shown in
  `status.json` and the coordinator's output, and that method is solved locally in the same run; new Mac placement then
  pauses until `clear-rejections` (see the section above).
- A document that fails: the arm is `failed`, its run directory and registry claim are KEPT, and that
  registry's later arms stay `waiting` with `registry chain stopped: <arm> failed (<stage>)`. Other
  registries continue. Never re-run the document by hand. Dispose of the claim with `StudyProtocolControl`
  `retry` (same plan) or `abandon`, then
  `cd /home/russell/wt/mio-parallel-20261105 && PYTHONPATH=/home/russell/wt/mio-parallel-20261105:/home/russell/wt/mio-parallel-20261105/children/index_options /home/russell/dskit/.venv/bin/python -B /home/russell/wt/mio-parallel-20261105/children/index_options/tools/mio_parallel_queue.py reopen /home/russell/data/index_options/full-span-rerun-20261105/mio/config.json QUARTER/ARM`
  and restart the coordinator. The restarted document gets a FRESH run root (`sealed/runs/<step>.retryN`; the
  pipeline refuses to reuse a non-empty run directory): a failed or interrupted run is never adopted and never
  deleted, and an arm that failed with `incomplete_with_worker_files` has that run retired by `reopen`
  (`retired_runs.json`) so it is not adopted again. The coordinator exits (code 3) when only stopped chains remain.
- A document killed by the disk guard: `disk_guard.sh` sends SIGTERM to `dskit.pipeline run` when C: is
  below 3 GB. The coordinator finds the document exited with a signal and fails the arm; status reads
  `killed by signal 15 ... the registry claim may still be active`. If the coordinator was down too, the
  arm is still `running` at restart: the runner finds no process and no finished run, relaunches into a fresh
  run root, and the registry refuses a second active claim, so the arm fails with its stage in status. Either way
  the claim has to be disposed of as in the previous item before `reopen`.
- Place does not lease units of an arm whose document is running.

## Stop

- Graceful: `touch <queue_root>/STOP`; the coordinator finishes the pass, waits for workers being started, and
  exits. Running workers finish on their own and are adopted by the next start.
- Hard: `pkill -f mio_parallel_queue`; then workers if needed: `pkill -f index_options.mio_worker` (WSL) and
  `ssh mac pkill -f index_options.mio_worker`. Do not kill a running document by hand: a killed document
  keeps its registry claim (see Recover). The only automatic document stop is `disk_guard.sh` below 3 GB.
  Detach the Mac RAM disk when finished: `hdiutil detach /dev/diskN`.
- Disk guard coverage: `disk_guard.sh` stops what `heavy_targets.regex` (in the rerun data folder, outside this
  repository) names. On 2026-11-05 that file already lists `index_options\.mio_worker` in its group; confirm
  with `grep -c 'index_options\\.mio_worker' <rerun>/heavy_targets.regex` before launch. If it is ever missing,
  the owner adds `|index_options\.mio_worker` inside the group so WSL workers are stopped too.
  The coordinator's own C: stop already covers its WSL workers; Mac workers are not on C:.

## Parity gate before scaling

1. Synthetic (2026-11-05): `tests/test_mio_worker.py` runs the worker as a subprocess on the bundle's code
   tree and closes a document from its action file; `tests/test_mio_parallel_queue.py` covers the
   coordinator, LocalHost, SshHost (through a local shell), DocumentRunner (stub pipeline, including a failed
   run, `reopen` and a fresh run root) and main, and one optional live test (`MIO_MAC_E2E=1`, not run) creates
   a 256 MB Mac RAM disk. Synthetic data only.
2. First real unit: set `parity.unit` to one method of one quarter-arm; keep `mac.slots` 1 and `wsl_slots` 0.
   The Mac solves it, WSL solves its twin, `parity.json` records pass or fail with hashes. Record the Mac
   RSS (`/usr/bin/time -l`), solve seconds, the `maxTimeLimit` count, the cold index build time and the
   sizes in the RAM-disk section. Only after a pass raise the slots.
3. Stop rule: if the Mac's timeout count over its first three real units exceeds twice WSL's for the same
   methods, set `mac.slots` to 0 and report to the owner.
