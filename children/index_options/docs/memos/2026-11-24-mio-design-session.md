# MIO design session and cadence experiment handoff

## TL;DR

25 November: the working formulation now includes marginal calibration and its
chronological selection procedure. Resume with PIT alignment across assets and
expiry ranks, then define the copula. No calibration fitting, validation or joint
scenario experiment was run in this session.

## 25 November formulation checkpoint

Owner: Russell; author: GPT-6. The owner requested “wrap, merge, and purge”.
This is an editorial handoff of previously agreed definitions, not a new model,
execution approval, or change to locked Path rows. Base:
`0d90f01edd659ed32b6b88aa08a3682fbbb4f6fc` (remote main).
Allowed repository changes: this memo, the child re-entry note, its archive, and
the index_options row of the root re-entry index. No code, tests, configs, Path,
ledgers, protected outcomes, or other lanes are changed.

Canonical document: [DSKit MIO Formulation Working Draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d),
saved sequence 22, 292 blocks. Sequence 21 contains the agreed definitions;
sequence 22 changes only the restart pointer and labels the old cadence status
as historical. Both replacement paragraphs were read back exactly. Rendered UI
was not rechecked. Attached reading copies predate the latest definitions.

Agreed components in the main body and appendices:

- Daily observed log returns, conditional mean, mean-zero innovations and
  conditional variance. A zero expected innovation does not mean zero variance;
  the appendix derives this and separates true quantities from estimates.
- Daily observation/cutoff d, historical origin a, calendar DTE t, session count
  eta, future risk-session offset g and original expiry rank rho. Tau is retired;
  v remains occupied. Weekly solving does not redefine the daily return input.
- Current frozen forecast preprocessing and the smooth mixture CDF, then one
  raw PIT per stock-expiry-origin, shared by contracts using that forecast.
  Only mature outcomes actually available by cutoff enter the update.
- Expanding per-stock/per-original-rank total and threshold counts. The threshold
  grid is a configuration input; its agreed default spans 0.01 to 0.99 with
  11 thresholds. Store counters incrementally, not an unlimited PIT vector.
- A corrected CDF obtained by composing the base CDF with a monotone endpoint-
  anchored map. Historical corrected PITs use the map issued with the forecast,
  never today's map. Keep raw and corrected assessment streams separate.
- A working map with knots (A + zeta * beta)/(M + zeta), linearly interpolated
  between thresholds and endpoints. Zeta is one configurable positive strength
  shared across groups; it is not an independent-observation count.
- Chronological warmup, tuning and later evaluation. Compare strength candidates
  on the same eligible mature cohort and A0002's primary proper score; freeze
  the chosen strength and update policy before later evaluation. Counter updates
  may continue causally. Selection is not proof of improvement over identity.

Open before implementation: numerical strengths/dates, aggregation weights,
minimum evidence, uncertainty under overlapping outcomes, recent monitoring,
pass/fail tolerances, promotion/fallback actions and corrected-stream counter
notation. EWMA initialization uses prior dates; its numerical configuration and
mean estimator remain open. No parameters were selected by data in this session.
The working calibration family is not an empirically validated winner.

Next: define a joint observation's origin, target-date and cutoff alignment,
missing/maturing dimensions and original expiry-rank identity. Then propose the
copula and compact joint-state retention. Marginal threshold counts cannot
recover joint dependence. Risk-checkpoint forecasts, IV evolution, scenario
wealth/cash, CVaR constraints and feasible dynamic hedging remain downstream.
Each new definition must be shown mathematically for owner confirmation.

The strategy is ongoing; daily/weekly solving and individual contracts up to
60 calendar DTE are different clocks. The cadence study remains separately
owned. The earlier memo below records its 24 November checkpoint only; inspect
its latest receipts before using those counts or launching a retry.

Research completed, preserved outside Git:

- `/home/russell/reports/MIO recalibration fitting methods.md` (synthesis).
- `/home/russell/research_notes/MIO recalibration fitting methods/`:
  `methods.md`, `validation.md`, `streaming.md` (source-backed assessments).
- `/home/russell/mio-formulation-view-20261125/` (earlier reading copies).

The report compares empirical monotone recalibration, stabilization, alternatives
and streaming limits. It distinguishes marginal calibration from copula fitting.
The frozen fixed_loc linear pool does not already apply this PIT correction;
the existing market-calibration helper concerns the market baseline. No helper
was changed or wired into the predictive/MIO process here.

Path A0002 controls scoring; A0003 controls windows; A0006 controls asset/expiry
rank; A0008/A0009 freeze forecast recipe/universe; A0010 remains unchanged.
2019 and 2020–2023 have prior model-selection exposure, so warmup/development
validation there is not a newly clean test. Protected 2024+ outcomes were not
read, and this handoff does not authorize opening them.

## 25 November verification and closure

Acceptance: retain the agreed draft and research, record an unambiguous restart,
archive the replaced lane note verbatim, preserve unrelated history and remove
only this task's clean, merged worktree/branch. This is pure editorial scope;
under skeptic-review.md it receives accuracy/link and documentation checks,
not production-code review. No behavioral contracts or formulas changed at wrap.
Verification: `PYTHONPATH=. /home/russell/dskit/.venv/bin/python -m pytest
 tests/docs/test_budgets.py -q` passed all 4 tests (0.09 s); `git diff --check`
passed. Exact archive preservation, historical memo preservation, local Markdown
links and the four-file allowed scope were checked. The child note is 40 lines.
Page sequence 22 readback matched both edits and retained 292 blocks. No full
suite, model fit, replay, protected-data read or UI-render check was performed.
Final publication and task-only worktree removal are verified in the Git/tool
receipts for this wrap; no empirical result is implied by these checks.

Local main began at `aa1d73fd94d799c3fececd54a047eb8331a83869`, 21 commits ahead of
remote main. Publish only this isolated handoff, then integrate locally without
publishing unrelated commits. Preserve the cadence checkout and other sessions.
Durable research and reading copies remain; purge targets only the temporary
wrap worktree and its contained local branch after successful publication.

## Historical checkpoint: 24 November 2026

The remainder preserves the earlier handoff verbatim except its top heading.
Its tau notation and current-status wording are superseded by the checkpoint
above; its frozen cadence identities and receipts remain useful references.

## TL;DR

Continue the mathematical MIO from cash and holdings, then define portfolio risk
and hedging. The corrected daily/weekly experiment is unfinished: zero of six
arms completed. Disk headroom recovered; the prepared retry still needs RAM admission.

## Working document and agreed notation

Our [MIO working draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d)
has a Resume here section. It is distinct from the older implemented Kelly memo.
Keep replies brief and restate each agreed addition mathematically.

At a fixed decision date, the agreed deterministic foundation is

$$
i\in\{b,s\},\quad j\in\{c,p\},\quad n\in N,\quad t\in T,\quad k\in K^j_{nt},
\qquad x^{ij}_{knt}\in\mathbb Z_{\ge0},\qquad \mathcal P=(B,\{h^{ij}_{knt}\}).
$$

x denotes current trades; h and B are known holdings and cash. The research
baseline uses m=100, F=$0.65 per executed contract, C for buys=100 times ask,
and C for sells=100 times bid. Per-contract expiry profits are

$$
\pi^{bj}_{knt}=V^{bj}_{knt}-C^{bj}_{knt}-F^{bj}_{knt},\qquad
\pi^{sj}_{knt}=C^{sj}_{knt}-V^{sj}_{knt}-F^{sj}_{knt}.
$$

For one immutable expiry E and decision dates separated by calendar days,

$$
t=E-\tau,\qquad \Delta=\tau'-\tau,\qquad t'=t-\Delta.
$$

Net actual filled trades against previous holdings, then carry surviving contracts
to t'. Keep entry premiums/fees as history; refresh current quotes. Expiry payoff
V differs from resale premium C. A contract's payoff formula stays fixed, but its
forecast expectation can change. Deterministic V stays fixed only with the same
expiry price and terms. Exercise/assignment needs its own cash/stock bookkeeping.

Still open: cash-flow and portfolio-value equations; joint asset scenarios,
VaR/CVaR or other tail-loss control; concentration/correlation; hedge actions and
frequency; the final growth objective. No production risk architecture or daily
cadence was accepted in this session. Resume these choices step by step.

## Empirical contract and status

Owner authorized daily-versus-weekly evidence on 2026-11-24. The frozen comparison
uses A0008 saved fixed_loc forecasts (five seeds), A0009's 166 names, 1–60 DTE,
$10,000 initial cash, no deposits, next-session EOD bid/ask fills and the recorded
filled-account risk policy. It holds positions to expiry, with no early exits or
hedge trades. This direct research runner is separate from A0010 and is not an
official sealed MIOStudyContract packet execution.

Entries: full 2020Q1 and 2021Q1; the 2021Q2-labelled episode enters in April only.
Settlement tails end before 2020-06-01, 2021-06-01 and 2021-07-01 respectively.
These are development windows, not clean holdout proof. No 2024+ read, refit,
holdout opening or live trading is authorized by this wrap.

The original diagnostic admitted contracts expiring on the next fill session
into optimization. It was invalidated; never use its economics to select cadence.
Corrected candidate 22128802 excludes expiry on/before the next actual fill date.
Corrected 2020Q1 weekly attempt 2 hit its 5 GiB hard cap after 12/14 decisions
(exit 137, oom-kill, 2505 seconds). **0/6 valid corrected receipts exist.**
The prepared serial retry reserves 6.2 GiB RAM, caps the process at 6 GiB and disables
swap. It has not run; fitting every arm is unproven. No study job starts at wrap.

## Frozen identities and evidence

- Code: `22128802100b0372d965536ac25447d0b0c18329`, parent `5b7440ec63b36ec32467f325c91563fa21ab8552`.
- Driver SHA256: `3be553b73270cf8afb1c3726e9c67bdaa03266f0afaf299a9c62966648abca2b`.
- Analysis SHA256: `04cb1bd07f4b8c6f9e8562d46423059e23b85e0faa27cc059a444142dbc748fc`.
- Corrected root: `/home/russell/data/index_options/runs/cadence-study/v20261124-fill-v2`.
- Reused intake: sibling `v20261124`; retain its configs, receipts and bounded input/index blobs.
- Config/code hashes and reviews: corrected `verification.json`, per-episode `config.json`, `protocol.json`, `review-reuse-tests.json`.
- Exact serial retry commands/budgets: corrected `jobs-solve-6g.json`; these intentionally retain the frozen checkout path.

Analysis requires all six corrected receipts and checks provenance/accounting.
It reports net growth, drawdown, tail loss, reserve capital-days, fees/fills,
DTE attribution and doubled execution-cost sensitivity. Overlapping 2021 episodes
stay together in time-block resampling. Relative daily dominance is not positive
edge over cash; inspect absolute results too. Cost stress holds actions fixed and
is not a feasible reoptimization. American assignment and executable liquidation
marks remain unmodeled. The experiment cannot decide early-exit/hedging frequency.

## Resume exactly

In WSL, restore the frozen checkout if purge removed it. Do not run worktree-add
over an existing path or modify the path-bound frozen configs:

```bash
git -C /home/russell/dskit worktree add --detach /home/russell/wt/codex-cadence-fill-20261124 22128802100b0372d965536ac25447d0b0c18329
python3 /home/russell/storage-investigation/storage_guard.py status
```

Compare driver/analysis/config/code hashes with verification.json. The driver
sets candidate import paths explicitly; never repoint the shared editable venv.
Inspect receipts first, then select the first missing job's exact command from
jobs-solve-6g.json. It already wraps storage_guard and systemd-run. Run serially;
preserve failed logs and stop on hash mismatch, refusal, OOM or failure. Reuse
intake. Only after six valid corrected receipts run frozen analysis.py against
the corrected root, then discuss the recommendation with the owner.

## Storage and continuity

10,643,554,118 bytes across 58,542 files were moved and verified on D; DSKit/shared
PMQuant inputs stayed on C. Old paths work. Gears 5 and the empty D image were
retired by explicit request. Windows measured C 17.3 GB/D 734.2 GB free; normal
reserves C 10/D 150 GiB remain. The task exception is retired. No WSL shutdown or
compaction occurred; do not attribute C recovery solely to WSL file moves.
Recorder and Claude remote session remained active.

Receipts: `/home/russell/storage-investigation/non-dskit-migration-20261124/completion-receipt.json`
and `/home/russell/storage-investigation/2026-11-24-d-cleanup.json`.
Locations: `/home/russell/pmquant/docs/data-locations.md`.

## Review and integration

Two fresh wrap lenses reviewed the complete five-file diff at 22128802 against
696931b0: correctness passed 27 focused tests plus 8 independent probes;
tests/integration passed 87 focused tests plus 8 independent probes. Both found
Critical 0 / Major 0 / Minor 0 / Nit 0 in the code diff. The prior analysis reviews also
passed both lenses; their exact records remain in verification.json.

Deferred external-driver Minor: reuse binding equates Python true with numeric 1;
strict parameter validation rejects before solving and every frozen config uses
literal true. No driver change is made during wrap. No real results, sealed data
or full suite were read/run for these reviews. Runtime, cash-relative edge and
production readiness remain unproven.

Initial local main held 20 unrelated unpublished commits at 33093080; origin/main
was 696931b0. Preserve the unrelated local work. This session publishes only its
reviewed code and handoff. Final commits and purge receipts are appended to the
existing external verification.json. Remove only contained clean session branches
and worktrees. Preserve canonical data, evidence and services; the frozen code
remains reachable through the merged history.
