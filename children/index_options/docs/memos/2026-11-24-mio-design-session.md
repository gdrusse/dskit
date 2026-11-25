# MIO design session and cadence experiment handoff

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
